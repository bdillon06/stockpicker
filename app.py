"""Flask app: serves the frontend and the JSON API for the swing-trade finder.

Wires the two-stage pipeline:
  /api/scan  -> stage 1 (technical scoring from cached OHLCV) + optional stage 2
                (catalyst enrichment of the top finalists).
  /api/refresh warms the caches (the only routes that hit the network heavily).

Run with ``bash run.sh`` or ``python app.py``.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from collections import Counter

import numpy as np
from flask import Flask, jsonify, request, send_from_directory

import data
import db
import indicators
import scoring
import signals


def _bundle_dir() -> str:
    """Directory holding bundled read-only assets (static/, CSVs).

    When frozen by PyInstaller these are extracted under ``sys._MEIPASS``;
    in a normal checkout they sit next to this file. Falls back identically
    when not frozen, so dev runs are unaffected.
    """
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


app = Flask(__name__, static_folder=os.path.join(_bundle_dir(), "static"))

DEFAULT_TOP_N = 30
# Enrichment passes allowed to stabilise the displayed cut (see scan()). Each
# pass shrinks the frontier, so this is a safety stop, not a tuning knob.
_MAX_ENRICH_PASSES = 5

# Guards the universe refresh so concurrent requests share one fetch.
_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH = 0.0
# Window in which a repeat refresh attempt is skipped outright. Deliberately far
# shorter than PRICE_TTL — this collapses stampedes, it does not define freshness.
_REFRESH_COOLDOWN = 60.0
# Bars fed to the indicators. Enough for a well-converged 200-EMA (needs >200)
# while keeping the per-scan compute bounded even with 2 years cached.
COMPUTE_BARS = 400

# Initialise the DB and seed the cache at import time so this runs under a WSGI
# server (gunicorn imports `app` and never executes the __main__ block below).
db.init_db()
try:
    data.seed_from_snapshot()
except Exception:
    pass


def _clean(obj):
    """Recursively make a structure JSON-safe.

    Coerces numpy scalars to native Python types and replaces NaN/inf with None
    (numpy bool in particular is not JSON-serializable by the stdlib encoder).
    """
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def _last_bar_date(ohlcv: dict) -> str:
    dates = ohlcv.get("dates") or []
    return dates[-1] if dates else ""


def _build_records(price_map: dict, bench_close, as_of: str = ""):
    """Compute indicators for every cached ticker -> records for ranking.

    ``as_of`` drops tickers whose most recent bar predates the universe's
    trading day. Ranking is relative, so mixing a stock priced today against
    stocks priced a month ago silently corrupts every percentile — a stale name
    is scored on a move that already happened.
    """
    meta = {u["symbol"]: u for u in data.load_universe()}
    records = []
    for ticker, (ohlcv, _fetched_at) in price_map.items():
        if ticker == data.BENCHMARK:
            continue
        if len(ohlcv.get("close", [])) < 30:
            continue
        # Undated rows are excluded too: freshness that cannot be verified is
        # treated as stale, since a silently month-old price poisons every
        # percentile it takes part in.
        if as_of and _last_bar_date(ohlcv) != as_of:
            continue
        trimmed = {k: v[-COMPUTE_BARS:] for k, v in ohlcv.items()}
        ind = indicators.compute_all(trimmed, bench_close=bench_close)
        info = meta.get(ticker, {})
        records.append({"ticker": ticker, "name": info.get("name", ""),
                        "sector": info.get("sector", ""), "indicators": ind})
    return records


def _universe_date(price_map: dict) -> str:
    """The trading day the ranking is 'as of' — the session most names share.

    The *mode*, deliberately not the max: one ticker carrying a stray newer bar
    would make max() the universe's date and disqualify every other name,
    collapsing the scan. The market is what most of the universe agrees on.
    Returns "" when nothing is dated, which disables alignment rather than
    rejecting everything.
    """
    dates = [d for t, (o, _) in price_map.items()
             if t != data.BENCHMARK and (d := _last_bar_date(o))]
    return Counter(dates).most_common(1)[0][0] if dates else ""


def _ensure_fresh(force: bool = False) -> None:
    """Refresh the whole universe's prices when the cache has aged out.

    This is what makes a scan a *scan* rather than a replay of whatever was
    cached: ``bulk_history`` skips tickers still inside PRICE_TTL, so a warm
    cache costs nothing and a cold one costs one chunked download. Network
    failures are swallowed — a throttled fetch degrades to the existing cache
    instead of breaking the scan.

    Serialised: the server runs multi-threaded (gunicorn ``--threads 4``), and
    concurrent scans would otherwise each start their own 500-ticker download —
    doubling the load on the very endpoint that throttles us. Whoever waits on
    the lock then finds the attempt recent and returns without re-fetching.

    The cooldown only collapses near-simultaneous attempts; ``PRICE_TTL`` inside
    ``bulk_history`` remains the real freshness authority. Keeping this window
    short matters: if Yahoo is down, the next scan a minute later retries rather
    than being locked out for the whole TTL by a failure we recorded as done.
    """
    global _LAST_REFRESH
    if not force and (time.time() - _LAST_REFRESH) < _REFRESH_COOLDOWN:
        return
    with _REFRESH_LOCK:
        # Re-check: another thread may have refreshed while we waited here.
        if not force and (time.time() - _LAST_REFRESH) < _REFRESH_COOLDOWN:
            return
        try:
            data.get_benchmark()
            data.bulk_history(data.universe_tickers(), force=force)
        except Exception:
            pass
        _LAST_REFRESH = time.time()


# Indicator computation for the whole universe is the expensive half of a scan
# and does not depend on weights, so it is memoised against the price cache's
# fingerprint. Correctness rests on the signature moving whenever bars change:
# a stale read is impossible, only a wasted recompute. Opening the detail drawer
# re-ranks the universe (to keep percentiles honest) and without this paid the
# full cost every click.
_RECORDS_CACHE: dict = {}


def _universe_records():
    """(records, bench_close, as_of) for the cached universe, memoised."""
    sig = db.price_cache_signature()
    hit = _RECORDS_CACHE.get("entry")
    if hit and hit[0] == sig:
        return hit[1]

    price_map = db.all_cached_prices()
    if not price_map:
        data.seed_from_snapshot()
        price_map = db.all_cached_prices()
        sig = db.price_cache_signature()
    bench = price_map.get(data.BENCHMARK, (None, 0))[0]
    bench_close = bench["close"][-COMPUTE_BARS:] if bench else None
    as_of = _universe_date(price_map)
    payload = (_build_records(price_map, bench_close, as_of), bench_close, as_of)
    _RECORDS_CACHE["entry"] = (sig, payload)
    return payload


def _ranked_universe(weights=None, refresh: bool = False):
    """Build + rank the whole cached universe so scores are comparable.

    Returns (ranked_list, {ticker: record}, bench_close, as_of). Used by scan,
    detail and signals alike so a stock's score is always its rank within the
    universe, never computed in isolation.
    """
    if refresh:
        _ensure_fresh()
    records, bench_close, as_of = _universe_records()
    # rank_universe copies each record, so callers may annotate the ranked
    # results (score, signal, catalysts) without mutating the memoised set.
    ranked = scoring.rank_universe(records, weights)
    return ranked, {r["ticker"]: r for r in ranked}, bench_close, as_of


def _weights_from_args(args) -> dict | None:
    """Parse ``?w_trend=0.45&w_momentum=0.2…`` into a weights dict.

    The detail view must score a stock with the same weights the scan used, or
    the drawer contradicts the row the user just clicked. Returns None (meaning
    DEFAULT_WEIGHTS) when the caller passes nothing.
    """
    w = {}
    for f in scoring.FACTORS:
        v = args.get("w_" + f)
        if v is not None:
            try:
                w[f] = float(v)
            except ValueError:
                pass
    return w or None


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def status():
    cached = db.all_cached_prices()
    now = time.time()
    fresh = sum(1 for _, fa in cached.values() if db.is_fresh(fa, data.PRICE_TTL))
    newest = max((fa for _, fa in cached.values()), default=0)
    return jsonify({
        "universe_size": len(data.universe_tickers()),
        "cached": len([t for t in cached if t != data.BENCHMARK]),
        "fresh": fresh,
        "last_fetch_age_min": round((now - newest) / 60, 1) if newest else None,
        "watchlist": len(db.list_watch()),
    })


@app.route("/api/scan", methods=["POST"])
def scan():
    body = request.get_json(silent=True) or {}
    weights = body.get("weights") or scoring.DEFAULT_WEIGHTS
    top_n = int(body.get("top_n", DEFAULT_TOP_N))
    do_enrich = bool(body.get("enrich", True))
    filters = scoring.filter_params(body.get("filters"))
    # Only surface names the signal logic would actually stand behind. Off by
    # request for anyone who wants to see the raw ranking including rejects.
    hide_avoid = bool(body.get("hide_avoid", True))

    ranked, _by_ticker, _bench, as_of = _ranked_universe(
        weights, refresh=bool(body.get("auto_refresh", True)))
    if not ranked:
        return jsonify({"results": [], "enriched": 0,
                        "message": "No cached data yet — hit Refresh."})

    scanned = len(ranked)
    # Apply the EMA/liquidity gate before enrichment so the expensive per-ticker
    # network calls are spent only on names that already qualify.
    ranked = [r for r in ranked
              if scoring.passes_filters(r["indicators"], filters)]
    qualified = len(ranked)

    for rec in ranked:
        rec["base_score"] = rec["score"]
        rec["catalyst_notes"] = []

    def _visible(rows: list) -> list:
        """The rows a user would actually see, given the scores set so far.

        Re-badges first: a badge depends on the score, which catalysts move.
        """
        for r in rows:
            r["signal"] = signals.evaluate(r["indicators"], r["score"])
        vis = [r for r in rows
               if not hide_avoid or r["signal"]["badge"] != "AVOID"]
        return vis[:top_n]

    # Enrich until the *visible* cut stops changing, rather than enriching the
    # names that happened to lead beforehand. Two effects reshuffle that cut:
    # a catalyst moves a score by up to ~14 points, and hiding AVOID drops rows
    # out of it. Either one pulls an un-enriched name into view, leaving that
    # row on a bare technical score beside neighbours carrying adjusted ones —
    # and clicking it enriched it for the first time, changing the number under
    # the user. Each pass only covers names that newly entered the cut, so this
    # settles in two or three rounds.
    enriched = 0
    attempted: set = set()
    if do_enrich:
        for _ in range(_MAX_ENRICH_PASSES):
            todo = [r for r in _visible(ranked) if r["ticker"] not in attempted]
            if not todo:
                break
            for rec in todo:
                attempted.add(rec["ticker"])
                try:
                    en = data.enrich(rec["ticker"])
                except Exception:
                    continue  # throttled: leave it on its technical score
                adj, notes = scoring.apply_catalysts(rec["base_score"], en)
                rec["score"] = adj
                rec["catalyst_notes"] = notes
                rec["enrich"] = {k: en.get(k) for k in (
                    "days_to_earnings", "recent_upgrades", "recent_downgrades",
                    "recent_news_count", "put_call_ratio", "short_pct_float",
                    "headlines")}
                enriched += 1
            ranked.sort(key=lambda r: r["score"], reverse=True)

    # Final badges, assigned once every catalyst adjustment has landed.
    for rec in ranked:
        rec["signal"] = signals.evaluate(rec["indicators"], rec["score"])
    if hide_avoid:
        ranked = [r for r in ranked if r["signal"]["badge"] != "AVOID"]

    # Any row the user can see must carry the same basis as its neighbours. With
    # catalysts switched off that is every row by design, so report nothing.
    unenriched = [r["ticker"] for r in ranked[:top_n]
                  if "enrich" not in r] if do_enrich else []

    # Day-to-day rank movement: tag each stock with how many spots it moved
    # since the previous day's snapshot, then record today's ranking.
    today = time.strftime("%Y-%m-%d")
    prev_date, prev = db.prev_rank_snapshot(today)
    current_ranks = {}
    for i, rec in enumerate(ranked):
        rank = i + 1
        current_ranks[rec["ticker"]] = rank
        rec["rank"] = rank
        pr = prev.get(rec["ticker"])
        rec["prev_rank"] = pr
        # Positive = climbed (lower rank number is better); None = no prior data.
        rec["rank_change"] = (pr - rank) if pr is not None else None
    db.save_rank_snapshot(today, current_ranks)

    return jsonify(_clean({
        "results": ranked, "enriched": enriched,
        "scanned": scanned, "qualified": qualified, "shown": len(ranked),
        # Non-empty only when Yahoo throttled a per-ticker call: those rows show
        # a technical-only score. Reported rather than swallowed, so a row that
        # disagrees with its drawer has a visible cause.
        "unenriched": unenriched,
        "prev_date": prev_date, "as_of": as_of, "filters": filters,
        "message": None if ranked else
        "No setups passed the EMA filters today — the market is not offering "
        "any. Loosen the filters or wait rather than forcing a trade.",
    }))


@app.route("/api/stock/<ticker>")
def stock_detail(ticker):
    ticker = ticker.upper()
    # Refresh the WHOLE universe, never just this ticker. Pulling fresh bars for
    # one name and ranking it against a stale universe was scoring it on a
    # different trading day than its peers, which is what made a stock badged
    # BUY in the scan come back AVOID the moment it was clicked.
    weights = _weights_from_args(request.args)
    ranked, by_ticker, bench, as_of = _ranked_universe(weights, refresh=True)

    if ticker in by_ticker:
        rec = by_ticker[ticker]
        ind, base = rec["indicators"], rec["score"]
    else:
        # Off-universe ticker: fetch it and score relative to the universe by
        # appending it before ranking.
        ohlcv, _ = db.get_prices(ticker)
        if not ohlcv:
            ohlcv = data.bulk_history([ticker], force=True).get(ticker)
        if not ohlcv:
            return jsonify({"error": f"No data for {ticker}"}), 404
        ind = indicators.compute_all(ohlcv, bench_close=bench)
        extra = [{"ticker": r["ticker"], "indicators": r["indicators"]}
                 for r in ranked] + [{"ticker": ticker, "indicators": ind}]
        base = next(r["score"] for r in scoring.rank_universe(extra)
                    if r["ticker"] == ticker)

    en = {}
    try:
        en = data.enrich(ticker)
    except Exception:
        pass
    adj, notes = scoring.apply_catalysts(base, en)
    sig = signals.evaluate(ind, adj)
    fails = scoring.check_filters(ind)
    return jsonify(_clean({
        "ticker": ticker, "indicators": ind, "enrich": en,
        "score": adj, "base_score": base, "catalyst_notes": notes,
        "signal": sig, "chart": _ema_chart(ticker), "as_of": as_of,
        "filter_fails": fails, "qualifies": not fails,
    }))


def _ema_chart(ticker: str, display_bars: int = 180) -> dict | None:
    """Price + 13/90/200 EMA series for the detail chart.

    EMAs are computed on the full compute window (so the 200-EMA is populated)
    then sliced to the last ``display_bars`` sessions for a readable plot.
    """
    ohlcv, _ = db.get_prices(ticker)
    if not ohlcv:
        return None
    closes = ohlcv["close"][-COMPUTE_BARS:]
    if len(closes) < 30:
        return None
    dates = ohlcv.get("dates")
    dates = dates[-COMPUTE_BARS:][-display_bars:] if dates else None
    return {
        "dates": dates,
        "close": closes[-display_bars:],
        "ema13": indicators.ema_series(closes, 13).tolist()[-display_bars:],
        "ema90": indicators.ema_series(closes, 90).tolist()[-display_bars:],
        "ema200": indicators.ema_series(closes, 200).tolist()[-display_bars:],
    }


@app.route("/api/watchlist", methods=["GET", "POST"])
def watchlist():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        t = (body.get("ticker") or "").upper().strip()
        if not t:
            return jsonify({"error": "ticker required"}), 400
        db.add_watch(t)
    return jsonify({"watchlist": db.list_watch()})


@app.route("/api/watchlist/<ticker>", methods=["DELETE"])
def watchlist_remove(ticker):
    db.remove_watch(ticker)
    return jsonify({"watchlist": db.list_watch()})


@app.route("/api/signals")
def watch_signals():
    watch = db.list_watch()
    if not watch:
        return jsonify({"signals": []})
    ranked, by_ticker, bench, _as_of = _ranked_universe(refresh=True)

    # Watched tickers outside the S&P universe (added via lookup) are scored by
    # appending them to the ranking set so their percentiles stay comparable.
    missing = [t for t in watch if t not in by_ticker]
    if missing:
        extra = [{"ticker": r["ticker"], "indicators": r["indicators"]}
                 for r in ranked]
        for t in missing:
            ohlcv, _ = db.get_prices(t)
            if not ohlcv:
                ohlcv = data.bulk_history([t], force=True).get(t)
            if ohlcv:
                trimmed = {k: v[-COMPUTE_BARS:] for k, v in ohlcv.items()}
                extra.append({"ticker": t, "indicators":
                              indicators.compute_all(trimmed, bench_close=bench)})
        by_ticker = {r["ticker"]: r for r in scoring.rank_universe(extra)}

    out = []
    for t in watch:
        rec = by_ticker.get(t)
        if not rec:
            out.append({"ticker": t, "error": "no data"})
            continue
        ind, base = rec["indicators"], rec["score"]
        en = db.get_enrich(t)[0] or {}
        adj, notes = scoring.apply_catalysts(base, en)
        sig = signals.evaluate(ind, adj)
        out.append({"ticker": t, "score": adj, "signal": sig,
                    "catalyst_notes": notes, "indicators": ind})
    return jsonify(_clean({"signals": out}))


@app.route("/api/refresh", methods=["POST"])
def refresh():
    body = request.get_json(silent=True) or {}
    tickers = body.get("tickers") or data.universe_tickers()
    limit = body.get("limit")
    if limit:
        tickers = tickers[:int(limit)]
    data.get_benchmark(period="6mo")
    fetched = data.bulk_history(tickers, force=bool(body.get("force")))
    return jsonify({"refreshed": len(fetched),
                    "requested": len(tickers)})


if __name__ == "__main__":
    # Local run. On a host (e.g. Render) gunicorn binds $PORT instead.
    port = int(os.environ.get("PORT", 5057))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
