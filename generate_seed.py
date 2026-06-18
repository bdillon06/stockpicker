"""One-off: warm the live price cache and write seed_snapshot.csv.

Run occasionally to refresh the bundled offline fallback. It fetches OHLCV for
the universe via the bulk endpoint (chunked + polite) into the SQLite cache, then
exports a trimmed snapshot (last ~130 bars) for a subset of tickers so the app
can produce a ranking on first run even when Yahoo is throttling.
"""
import csv
import sys

import data
import db

SEED_TICKERS = 160      # how many tickers to write into the committed snapshot
SEED_BARS = 320         # bars per ticker (enough for a 200-EMA to compute offline)


def main():
    db.init_db()
    tickers = data.universe_tickers()
    print(f"Fetching benchmark + {len(tickers)} tickers (2y)...")
    data.get_benchmark(period="2y")
    fetched = data.bulk_history(
        tickers, period="2y", force=True,
        progress_cb=lambda d, t: print(f"  {d}/{t}", end="\r", flush=True))
    print(f"\nCached {len(fetched)} tickers.")

    # Export a trimmed snapshot for the first N successfully-fetched tickers.
    cached = db.all_cached_prices()
    seed_syms = [t for t in tickers if t in cached][:SEED_TICKERS]
    rows = 0
    with open(data.SEED_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "date", "open", "high", "low", "close", "volume"])
        for t in seed_syms:
            ohlcv = cached[t][0]
            n = len(ohlcv["close"])
            dates = ohlcv.get("dates") or [""] * n
            start = max(0, n - SEED_BARS)
            for i in range(start, n):
                w.writerow([t, dates[i],
                            round(ohlcv["open"][i], 4),
                            round(ohlcv["high"][i], 4),
                            round(ohlcv["low"][i], 4),
                            round(ohlcv["close"][i], 4),
                            int(ohlcv["volume"][i])])
                rows += 1
    print(f"Wrote {data.SEED_CSV}: {len(seed_syms)} tickers, {rows} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
