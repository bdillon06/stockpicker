"""yfinance data access — the ONLY module that touches the network.

Two paths, matching the two-stage pipeline:
- ``bulk_history``  : one chunked ``yf.download`` for many tickers (cheap, the
  friendly endpoint) -> OHLCV used by stage-1 technical scoring.
- ``enrich``        : per-ticker public info (earnings/analyst/news/options/short
  interest), the throttle-prone calls, run only for the top finalists.

Everything is cached in SQLite (see ``db.py``). On first run, when Yahoo throttles
this datacenter IP, the price cache is seeded from the bundled ``seed_snapshot.csv``
so the app is still usable. All network calls degrade to ``None``/empty rather than
raising, so a throttled fetch never breaks a scan.
"""
from __future__ import annotations

import csv
import os
import random
import sys
import time

import pandas as pd
import yfinance as yf

import db

# When frozen by PyInstaller the bundled CSVs are extracted under sys._MEIPASS;
# otherwise they live next to this module. Identical for normal dev runs.
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
UNIVERSE_CSV = os.path.join(HERE, "universe.csv")
SEED_CSV = os.path.join(HERE, "seed_snapshot.csv")
BENCHMARK = "SPY"

PRICE_TTL = 6 * 3600       # 6 hours
ENRICH_TTL = 12 * 3600     # 12 hours


# --- universe --------------------------------------------------------------
def load_universe() -> list:
    """Return list of {ticker, name, sector} from the bundled CSV."""
    if not os.path.exists(UNIVERSE_CSV):
        return []
    with open(UNIVERSE_CSV, newline="") as f:
        return [dict(symbol=r["symbol"].strip().upper(),
                     name=r.get("name", "").strip(),
                     sector=r.get("sector", "").strip())
                for r in csv.DictReader(f) if r.get("symbol")]


def universe_tickers() -> list:
    return [u["symbol"] for u in load_universe()]


# --- OHLCV parsing ---------------------------------------------------------
def _df_to_ohlcv(df: pd.DataFrame) -> dict | None:
    """Convert a single-ticker OHLCV DataFrame to a dict of lists (oldest first)."""
    if df is None or df.empty:
        return None
    cols = {c.lower(): c for c in df.columns}
    needed = ("open", "high", "low", "close", "volume")
    if not all(n in cols for n in needed):
        return None
    sub = df[[cols[n] for n in needed]].dropna()
    if sub.empty:
        return None
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in sub.index],
        "open": sub[cols["open"]].astype(float).tolist(),
        "high": sub[cols["high"]].astype(float).tolist(),
        "low": sub[cols["low"]].astype(float).tolist(),
        "close": sub[cols["close"]].astype(float).tolist(),
        "volume": sub[cols["volume"]].astype(float).tolist(),
    }


# --- stage 1: bulk price download -----------------------------------------
def _download_chunk(tickers: list, period: str) -> dict:
    """Download one chunk with a couple of retries; return {ticker: ohlcv}."""
    out = {}
    for attempt in range(3):
        try:
            data = yf.download(tickers, period=period, interval="1d",
                               group_by="ticker", auto_adjust=True,
                               progress=False, threads=True)
            break
        except Exception:
            time.sleep(2 ** attempt + random.random())
    else:
        return out

    if data is None or len(data) == 0:
        return out
    multi = isinstance(data.columns, pd.MultiIndex)
    for t in tickers:
        try:
            sub = data[t] if multi else data
            o = _df_to_ohlcv(sub)
            if o:
                out[t] = o
        except Exception:
            continue
    return out


def bulk_history(tickers: list, period: str = "2y", chunk: int = 50,
                 force: bool = False, progress_cb=None) -> dict:
    """Fetch/refresh OHLCV for ``tickers``, using cache when fresh.

    Returns {ticker: ohlcv}. ``progress_cb(done, total)`` is called as chunks
    complete. Fresh cached tickers are skipped to spare the network.
    """
    result = {}
    stale = []
    for t in tickers:
        cached, fetched_at = db.get_prices(t)
        if cached and not force and db.is_fresh(fetched_at, PRICE_TTL):
            result[t] = cached
        else:
            stale.append(t)

    total = len(stale)
    done = 0
    for i in range(0, total, chunk):
        batch = stale[i:i + chunk]
        fetched = _download_chunk(batch, period)
        for t, o in fetched.items():
            db.save_prices(t, o)
            result[t] = o
        done += len(batch)
        if progress_cb:
            progress_cb(done, total)
        if i + chunk < total:
            time.sleep(1.0 + random.random())  # be polite between chunks
    return result


def get_benchmark(period: str = "2y") -> list | None:
    """Return SPY close list for relative-strength, cached like any ticker."""
    cached, fetched_at = db.get_prices(BENCHMARK)
    if cached and db.is_fresh(fetched_at, PRICE_TTL):
        return cached["close"]
    fetched = _download_chunk([BENCHMARK], period)
    if BENCHMARK in fetched:
        db.save_prices(BENCHMARK, fetched[BENCHMARK])
        return fetched[BENCHMARK]["close"]
    return cached["close"] if cached else None


# --- stage 2: per-ticker enrichment ---------------------------------------
def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def enrich(ticker: str, force: bool = False) -> dict:
    """Fetch throttle-prone public info for one ticker, cached.

    Returns a dict with normalised fields consumed by ``scoring.apply_catalysts``:
    days_to_earnings, recent_upgrades/downgrades, recent_news_count,
    put_call_ratio, short_pct_float, plus a few display extras (headlines).
    """
    cached, fetched_at = db.get_enrich(ticker)
    if cached and not force and db.is_fresh(fetched_at, ENRICH_TTL):
        return cached

    tk = yf.Ticker(ticker)
    info = _safe(lambda: tk.info, {}) or {}
    out = {"ticker": ticker}

    # Days to next earnings.
    out["days_to_earnings"] = _safe(lambda: _days_to_earnings(tk))

    # Analyst upgrades/downgrades in the last 30 days.
    up, down = _safe(lambda: _analyst_actions(tk), (0, 0)) or (0, 0)
    out["recent_upgrades"] = up
    out["recent_downgrades"] = down

    # Recent news (last 7 days) + a few headlines for display.
    count, headlines = _safe(lambda: _recent_news(tk), (0, [])) or (0, [])
    out["recent_news_count"] = count
    out["headlines"] = headlines

    # Options skew (nearest expiry put/call volume ratio).
    out["put_call_ratio"] = _safe(lambda: _put_call_ratio(tk))

    # Short interest.
    sp = info.get("shortPercentOfFloat")
    out["short_pct_float"] = float(sp) if sp is not None else None

    db.save_enrich(ticker, out)
    return out


def _days_to_earnings(tk) -> int | None:
    cal = tk.calendar
    dt = None
    if isinstance(cal, dict):
        ed = cal.get("Earnings Date")
        if isinstance(ed, (list, tuple)) and ed:
            dt = ed[0]
        elif ed is not None:
            dt = ed
    elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
        dt = cal.loc["Earnings Date"].iloc[0]
    if dt is None:
        return None
    ts = pd.Timestamp(dt)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return int((ts.normalize() - pd.Timestamp.now().normalize()).days)


def _analyst_actions(tk):
    ud = tk.upgrades_downgrades
    if ud is None or len(ud) == 0:
        return (0, 0)
    df = ud.reset_index()
    date_col = "GradeDate" if "GradeDate" in df.columns else df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce", utc=True)
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
    recent = df[df[date_col] >= cutoff]
    actions = recent.get("Action")
    if actions is None:
        return (0, 0)
    up = int((actions == "up").sum())
    down = int((actions == "down").sum())
    return (up, down)


def _recent_news(tk):
    news = tk.news or []
    cutoff = time.time() - 7 * 86400
    count = 0
    headlines = []
    for n in news:
        content = n.get("content", n)  # newer yfinance nests under "content"
        title = content.get("title") if isinstance(content, dict) else None
        pub = n.get("providerPublishTime")
        if pub and pub >= cutoff:
            count += 1
        if title and len(headlines) < 5:
            headlines.append(title)
    # Fall back to total count if timestamps are absent in this yfinance version.
    if count == 0 and news:
        count = min(len(news), 10)
    return (count, headlines)


def _put_call_ratio(tk):
    exps = tk.options
    if not exps:
        return None
    chain = tk.option_chain(exps[0])
    call_vol = float(chain.calls["volume"].fillna(0).sum())
    put_vol = float(chain.puts["volume"].fillna(0).sum())
    if call_vol == 0:
        return None
    return round(put_vol / call_vol, 3)


# --- seeding ---------------------------------------------------------------
def seed_from_snapshot() -> int:
    """Populate the price cache from the bundled snapshot if the cache is empty.

    Returns the number of tickers seeded. Lets the app produce a ranking on the
    very first run even when Yahoo is throttling. Snapshot rows are dated old so
    a later live refresh supersedes them.
    """
    if not os.path.exists(SEED_CSV):
        return 0
    existing = db.all_cached_prices()
    if existing:
        return 0
    seeded = 0
    by_ticker: dict = {}
    with open(SEED_CSV, newline="") as f:
        reader = csv.DictReader(f)
        has_date = "date" in (reader.fieldnames or [])
        for row in reader:
            t = row["ticker"].strip().upper()
            by_ticker.setdefault(t, {"dates": [], "open": [], "high": [],
                                     "low": [], "close": [], "volume": []})
            if has_date:
                by_ticker[t]["dates"].append(row["date"])
            for k in ("open", "high", "low", "close", "volume"):
                by_ticker[t][k].append(float(row[k]))
    for t, o in by_ticker.items():
        if len(o["close"]) >= 60:  # enough for the indicators to compute
            # Stamp the seed as stale (fetched_at=0) so the first live refresh
            # always supersedes it rather than being skipped as "fresh".
            db.save_prices(t, o, fetched_at=0.0)
            seeded += 1
    return seeded
