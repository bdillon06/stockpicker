"""SQLite schema and helpers for caching and the watchlist.

One small database at ``data/stockpicker.db`` with three tables:
- ``price_cache``  : JSON OHLCV per ticker (stage-1 bulk download), with fetched_at.
- ``enrich_cache`` : JSON of stage-2 public info per ticker, with fetched_at.
- ``watchlist``    : tickers the user is tracking.

Caching is the central defence against Yahoo rate-limiting, so freshness checks
live here via ``is_fresh``.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

# Overridable so a hosted instance can point at a persistent disk
# (e.g. STOCKPICKER_DB=/var/data/stockpicker.db on Render).
DB_PATH = os.environ.get(
    "STOCKPICKER_DB",
    os.path.join(os.path.dirname(__file__), "data", "stockpicker.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_cache (
    ticker     TEXT PRIMARY KEY,
    ohlcv_json TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS enrich_cache (
    ticker      TEXT PRIMARY KEY,
    enrich_json TEXT NOT NULL,
    fetched_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    ticker  TEXT PRIMARY KEY,
    added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS rank_history (
    date    TEXT NOT NULL,   -- YYYY-MM-DD of the snapshot
    ticker  TEXT NOT NULL,
    rank    INTEGER NOT NULL,
    PRIMARY KEY (date, ticker)
);
"""

# Keep roughly a month of daily snapshots so day-to-day movement always has a
# prior day to compare against without the table growing unbounded.
RANK_HISTORY_KEEP_DAYS = 35


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def is_fresh(fetched_at: float, ttl_seconds: float) -> bool:
    return (time.time() - fetched_at) < ttl_seconds


# --- price cache -----------------------------------------------------------
def save_prices(ticker: str, ohlcv: dict, fetched_at: float | None = None) -> None:
    """Cache OHLCV for ``ticker``. ``fetched_at`` defaults to now; seeding passes
    an old timestamp so the bundled snapshot is always superseded by a live fetch."""
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO price_cache(ticker, ohlcv_json, fetched_at)"
            " VALUES (?,?,?)",
            (ticker, json.dumps(ohlcv),
             time.time() if fetched_at is None else fetched_at),
        )


def get_prices(ticker: str):
    with connect() as conn:
        row = conn.execute(
            "SELECT ohlcv_json, fetched_at FROM price_cache WHERE ticker=?",
            (ticker,),
        ).fetchone()
    if not row:
        return None, 0.0
    return json.loads(row["ohlcv_json"]), row["fetched_at"]


def price_cache_signature() -> tuple:
    """A cheap fingerprint of the price cache: (row count, newest fetch).

    Lets callers reuse computed indicators while the underlying bars are
    unchanged, without paying to load and parse every OHLCV blob to find out.
    Any save_prices call moves one of the two components.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(MAX(fetched_at), 0) AS mx"
            " FROM price_cache").fetchone()
    return (row["n"], row["mx"])


def all_cached_prices() -> dict:
    """Return {ticker: (ohlcv, fetched_at)} for everything in the price cache."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker, ohlcv_json, fetched_at FROM price_cache").fetchall()
    return {r["ticker"]: (json.loads(r["ohlcv_json"]), r["fetched_at"])
            for r in rows}


# --- enrich cache ----------------------------------------------------------
def save_enrich(ticker: str, enrich: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO enrich_cache(ticker, enrich_json, fetched_at)"
            " VALUES (?,?,?)",
            (ticker, json.dumps(enrich), time.time()),
        )


def get_enrich(ticker: str):
    with connect() as conn:
        row = conn.execute(
            "SELECT enrich_json, fetched_at FROM enrich_cache WHERE ticker=?",
            (ticker,),
        ).fetchone()
    if not row:
        return None, 0.0
    return json.loads(row["enrich_json"]), row["fetched_at"]


# --- watchlist -------------------------------------------------------------
def add_watch(ticker: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist(ticker, added_at) VALUES (?,?)",
            (ticker.upper(), time.time()),
        )


def remove_watch(ticker: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM watchlist WHERE ticker=?", (ticker.upper(),))


def list_watch() -> list:
    with connect() as conn:
        rows = conn.execute(
            "SELECT ticker FROM watchlist ORDER BY added_at").fetchall()
    return [r["ticker"] for r in rows]


# --- rank history (day-to-day movement) ------------------------------------
def save_rank_snapshot(date: str, ranks: dict) -> None:
    """Store today's {ticker: rank} snapshot, replacing any earlier scan today.

    Re-running a scan the same day overwrites the day's snapshot, so the
    day-to-day delta always compares against the *previous* calendar day rather
    than against an earlier scan from today.
    """
    with connect() as conn:
        conn.execute("DELETE FROM rank_history WHERE date=?", (date,))
        conn.executemany(
            "INSERT INTO rank_history(date, ticker, rank) VALUES (?,?,?)",
            [(date, t, int(r)) for t, r in ranks.items()])
        # Prune snapshots older than the retention window.
        conn.execute(
            "DELETE FROM rank_history WHERE date < ("
            "  SELECT MIN(date) FROM ("
            "    SELECT DISTINCT date FROM rank_history"
            "    ORDER BY date DESC LIMIT ?))",
            (RANK_HISTORY_KEEP_DAYS,))


def prev_rank_snapshot(before_date: str):
    """Return (date, {ticker: rank}) for the most recent day before ``before_date``.

    Returns (None, {}) when there's no earlier snapshot yet (first-ever scan,
    or the first scan on a new day with no prior history).
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM rank_history WHERE date < ?",
            (before_date,)).fetchone()
        day = row["d"] if row else None
        if not day:
            return None, {}
        rows = conn.execute(
            "SELECT ticker, rank FROM rank_history WHERE date=?", (day,)).fetchall()
    return day, {r["ticker"]: r["rank"] for r in rows}
