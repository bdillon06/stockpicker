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
import time

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


def _build_records(price_map: dict, bench_close):
    """Compute indicators for every cached ticker -> records for ranking."""
    meta = {u["symbol"]: u for u in data.load_universe()}
    records = []
    for ticker, (ohlcv, _fetched_at) in price_map.items():
        if ticker == data.BENCHMARK:
            continue
        if len(ohlcv.get("close", [])) < 30:
            continue
        trimmed = {k: v[-COMPUTE_BARS:] for k, v in ohlcv.items()}
        ind = indicators.compute_all(trimmed, bench_close=bench_close)
        info = meta.get(ticker, {})
        records.append({"ticker": ticker, "name": info.get("name", ""),
                        "sector": info.get("sector", ""), "indicators": ind})
    return records


def _ranked_universe(weights=None):
    """Build + rank the whole cached universe so scores are comparable.

    Returns (ranked_list, {ticker: record}, bench_close). Used by scan, detail
    and signals alike so a stock's score is always its rank within the universe,
    never computed in isolation.
    """
    price_map = db.all_cached_prices()
    if not price_map:
        data.seed_from_snapshot()
        price_map = db.all_cached_prices()
    bench = price_map.get(data.BENCHMARK, (None, 0))[0]
    bench_close = bench["close"][-COMPUTE_BARS:] if bench else None
    ranked = scoring.rank_universe(_build_records(price_map, bench_close), weights)
    return ranked, {r["ticker"]: r for r in ranked}, bench_close


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

    ranked, _by_ticker, _bench = _ranked_universe(weights)
    if not ranked:
        return jsonify({"results": [], "enriched": 0,
                        "message": "No cached data yet — hit Refresh."})

    enriched = 0
    for rank, rec in enumerate(ranked):
        base = rec["score"]
        rec["base_score"] = base
        rec["catalyst_notes"] = []
        if do_enrich and rank < top_n:
            try:
                en = data.enrich(rec["ticker"])
                adj, notes = scoring.apply_catalysts(base, en)
                rec["score"] = adj
                rec["catalyst_notes"] = notes
                rec["enrich"] = {k: en.get(k) for k in (
                    "days_to_earnings", "recent_upgrades", "recent_downgrades",
                    "recent_news_count", "put_call_ratio", "short_pct_float",
                    "headlines")}
                enriched += 1
            except Exception:
                pass
        rec["signal"] = signals.evaluate(rec["indicators"], rec["score"])

    ranked.sort(key=lambda r: r["score"], reverse=True)

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

    return jsonify(_clean({"results": ranked, "enriched": enriched,
                           "scanned": len(ranked), "prev_date": prev_date}))


@app.route("/api/stock/<ticker>")
def stock_detail(ticker):
    ticker = ticker.upper()
    # Pull fresh bars for this ticker (and the benchmark) on demand when the
    # cache is stale, so the detail view and chart aren't stuck on the bundled
    # seed snapshot. bulk_history/get_benchmark skip the network when fresh.
    try:
        data.get_benchmark()
        data.bulk_history([ticker])
    except Exception:
        pass
    ranked, by_ticker, bench = _ranked_universe()

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
    return jsonify(_clean({
        "ticker": ticker, "indicators": ind, "enrich": en,
        "score": adj, "base_score": base, "catalyst_notes": notes,
        "signal": sig, "chart": _ema_chart(ticker),
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
    ranked, by_ticker, bench = _ranked_universe()

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
