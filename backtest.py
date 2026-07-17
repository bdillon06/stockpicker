"""Walk-forward backtest of the scan — does the score actually pick winners?

Answers the one question the app cannot: BUY means "matches the pattern", but
does the pattern pay? This replays history bar by bar, taking the picks the
scanner would genuinely have made on each date and measuring what happened next.

It calls the *live* code (``indicators.compute_all``, ``scoring.rank_universe``,
``scoring.passes_filters``, ``signals.evaluate``) rather than re-implementing the
strategy, so what is measured here is what the app actually does.

Reading the results honestly — the biases below inflate returns and CANNOT be
fixed with the data on hand. They are printed with every run for a reason:

* **Survivorship bias (inflates).** ``universe.csv`` is *today's* S&P 500. Names
  that were in the index during the test window and later dropped out (the worst
  performers, typically) are absent, and today's members are there precisely
  because they did well enough to stay. This is the single largest distortion.
* **No catalysts.** Earnings dates, analyst actions and news are only available
  as a *current* snapshot, never point-in-time, so ``apply_catalysts`` is out of
  scope here. This measures the technical score only.
* **Short window.** ~2 years of cached bars, minus 200 for the EMA200 warm-up,
  leaves ~1.2 years — one market regime, and a bull one at that. A strategy that
  is long-only and demands price > 200-EMA cannot help but look good in it.
* **Idealised fills.** Entry at the next open, exits at exactly the stop/target.
  Real fills gap through both. ``--slippage-bps`` models this crudely.

Usage:
    python backtest.py                     # defaults: top 5, 10-day hold
    python backtest.py --top 10 --hold 5 --slippage-bps 10
"""
from __future__ import annotations

import argparse
import statistics
import sys

import db
import indicators
import scoring
import signals

BENCHMARK = "SPY"
COMPUTE_BARS = 400      # must match app.COMPUTE_BARS: same window, same scores
WARMUP_BARS = 210       # EMA200 needs >200 bars before it is anything but NaN


# --- data ------------------------------------------------------------------
def load_bars() -> tuple:
    """{ticker: ohlcv} for dated tickers, plus the benchmark's (dates, closes).

    Undated rows are dropped: without a date axis a bar cannot be placed in time,
    and a backtest that misplaces a bar is measuring nothing.
    """
    cached = db.all_cached_prices()
    bench = cached.get(BENCHMARK, (None, 0))[0]
    if not bench or not bench.get("dates"):
        raise SystemExit("No benchmark bars cached — run a scan first.")
    bars = {t: o for t, (o, _) in cached.items()
            if t != BENCHMARK and o.get("dates") and len(o["close"]) > WARMUP_BARS}
    if not bars:
        raise SystemExit("No dated price history cached — run a scan first.")
    return bars, bench["dates"], bench["close"]


def _index_by_date(ohlcv: dict) -> dict:
    return {d: i for i, d in enumerate(ohlcv["dates"])}


# --- trade simulation ------------------------------------------------------
def simulate_exit(ohlcv: dict, entry_i: int, stop: float, target: float,
                  max_hold: int) -> tuple:
    """Walk forward from ``entry_i`` and return (exit_index, price, reason).

    Checks the stop *before* the target on any bar that spans both: intraday
    order is unknowable from daily bars, so the pessimistic branch is the honest
    one — assuming the target filled first would manufacture profit that may
    never have existed.
    """
    n = len(ohlcv["close"])
    last = min(entry_i + max_hold, n - 1)
    for i in range(entry_i, last + 1):
        if ohlcv["low"][i] <= stop:
            return i, stop, "stop"
        if ohlcv["high"][i] >= target:
            return i, target, "target"
    return last, ohlcv["close"][last], "time"


def _record_for(ticker: str, ohlcv: dict, upto: int, bench_close) -> dict | None:
    """Indicators from bars up to and including ``upto`` — never beyond.

    The whole backtest hinges on this slice. One bar too many and the strategy
    is trading on information it could not have had.
    """
    if upto + 1 < WARMUP_BARS:
        return None
    lo = max(0, upto + 1 - COMPUTE_BARS)
    window = {k: ohlcv[k][lo:upto + 1]
              for k in ("open", "high", "low", "close", "volume")}
    ind = indicators.compute_all(window, bench_close=bench_close)
    return {"ticker": ticker, "indicators": ind}


# --- the walk-forward loop -------------------------------------------------
def run(top_n: int = 5, hold: int = 10, slippage_bps: float = 5.0,
        weights: dict | None = None, filters: dict | None = None,
        stop_mult: float = 1.5, target_mult: float = 2.5,
        progress=lambda *a: None) -> dict:
    """Replay the scanner over history. Returns trades, cohorts and stats.

    Rebalances every ``hold`` bars so cohorts never overlap: each period's
    capital is fully resolved before the next is committed, which keeps the
    equity curve a real chain of returns rather than an average of overlapping
    positions.
    """
    bars, cal, bench_closes = load_bars()
    idx = {t: _index_by_date(o) for t, o in bars.items()}
    slip = slippage_bps / 10_000.0

    # Decide on bar i, enter at the open of i+1, allow `hold` bars to resolve.
    start = WARMUP_BARS
    end = len(cal) - hold - 2
    dates = list(range(start, end, hold))
    trades, cohorts = [], []

    for n_done, di in enumerate(dates):
        date = cal[di]
        progress(n_done + 1, len(dates), date)
        bench_win = bench_closes[max(0, di + 1 - COMPUTE_BARS):di + 1]

        records = []
        for t, o in bars.items():
            i = idx[t].get(date)
            if i is None:          # ticker did not trade that session
                continue
            rec = _record_for(t, o, i, bench_win)
            if rec:
                rec["_i"] = i
                records.append(rec)
        if len(records) < 20:
            continue

        ranked = scoring.rank_universe(records, weights)
        gated = [r for r in ranked if scoring.passes_filters(r["indicators"], filters)]
        picks = [r for r in gated
                 if signals.evaluate(r["indicators"], r["score"])["badge"] == "BUY"]

        # Two controls, because the strategy differs from "buy everything that
        # passed the gate" in TWO ways at once — the ranking AND the ATR
        # stop/target. Comparing against only the first conflates them and
        # blames whichever you happened to name.
        #   control      : every gated name, plain hold  -> the gate alone
        #   picks_hold   : the same top_n picks, plain hold -> isolates RANKING
        #   cohort (ret) : the top_n picks with stop/target -> isolates EXITS
        control = [_forward_return(bars[r["ticker"]], r["_i"], hold)
                   for r in gated]
        control = [c for c in control if c is not None]
        picks_hold = [_forward_return(bars[r["ticker"]], r["_i"], hold)
                      for r in picks[:top_n]]
        picks_hold = [c for c in picks_hold if c is not None]

        cohort = []
        for r in picks[:top_n]:
            o = bars[r["ticker"]]
            ei = r["_i"] + 1
            if ei >= len(o["close"]):
                continue
            entry = o["open"][ei] * (1 + slip)      # pay the spread on entry
            atr = r["indicators"].get("atr")
            if not atr or atr <= 0:
                continue
            stop = entry - stop_mult * atr
            target = entry + target_mult * atr
            xi, xp, why = simulate_exit(o, ei, stop, target, hold)
            xp *= (1 - slip)                         # and again on exit
            ret = xp / entry - 1.0
            trade = {"date": date, "ticker": r["ticker"], "score": r["score"],
                     "entry": entry, "exit": xp, "ret": ret, "reason": why,
                     "bars_held": xi - ei + 1}
            trades.append(trade)
            cohort.append(ret)

        cohorts.append({
            "date": date,
            "picks": len(cohort),
            "ret": statistics.fmean(cohort) if cohort else 0.0,
            "control": statistics.fmean(control) if control else 0.0,
            "picks_hold": statistics.fmean(picks_hold) if picks_hold else 0.0,
            "qualified": len(gated),
        })

    return {"trades": trades, "cohorts": cohorts,
            "stats": summarise(trades, cohorts, bars, cal, bench_closes, hold)}


def _forward_return(ohlcv: dict, i: int, hold: int) -> float | None:
    """Plain buy-at-next-open / sell-`hold`-bars-later return, no stop or target."""
    ei = i + 1
    xi = min(ei + hold, len(ohlcv["close"]) - 1)
    if ei >= len(ohlcv["close"]) or ohlcv["open"][ei] <= 0:
        return None
    return ohlcv["close"][xi] / ohlcv["open"][ei] - 1.0


# --- statistics ------------------------------------------------------------
def _max_drawdown(equity: list) -> float:
    peak, mdd = equity[0] if equity else 1.0, 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return mdd


def summarise(trades: list, cohorts: list, bars, cal, bench_closes,
              hold: int) -> dict:
    if not trades:
        return {"n_trades": 0}
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]

    # Non-overlapping cohorts chain into a real equity curve.
    equity, v = [1.0], 1.0
    for c in cohorts:
        v *= (1 + c["ret"])
        equity.append(v)
    ctrl, cv = [1.0], 1.0
    for c in cohorts:
        cv *= (1 + c["control"])
        ctrl.append(cv)
    ph, pv = [1.0], 1.0
    for c in cohorts:
        pv *= (1 + c.get("picks_hold", 0.0))
        ph.append(pv)

    # Benchmark over the identical span, bought at the first entry.
    first, last = cohorts[0], cohorts[-1]
    bi = cal.index(first["date"])
    bx = min(cal.index(last["date"]) + hold + 1, len(bench_closes) - 1)
    bench_ret = bench_closes[bx] / bench_closes[bi] - 1.0

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "n_trades": len(trades),
        "n_cohorts": len(cohorts),
        "win_rate": len(wins) / len(rets),
        "avg_ret": statistics.fmean(rets),
        "median_ret": statistics.median(rets),
        "avg_win": statistics.fmean(wins) if wins else 0.0,
        "avg_loss": statistics.fmean(losses) if losses else 0.0,
        "expectancy": statistics.fmean(rets),
        "profit_factor": (gross_win / gross_loss) if gross_loss else float("inf"),
        "avg_bars_held": statistics.fmean([t["bars_held"] for t in trades]),
        "exits": {r: sum(1 for t in trades if t["reason"] == r)
                  for r in ("target", "stop", "time")},
        "total_return": equity[-1] - 1.0,
        "control_return": ctrl[-1] - 1.0,
        "picks_hold_return": ph[-1] - 1.0,
        "benchmark_return": bench_ret,
        "max_drawdown": _max_drawdown(equity),
        "start": first["date"], "end": last["date"],
        "avg_qualified": statistics.fmean([c["qualified"] for c in cohorts]),
    }


# --- reporting -------------------------------------------------------------
def report(s: dict, top_n: int, hold: int, slippage_bps: float) -> None:
    if not s.get("n_trades"):
        print("No trades — the filters admitted nothing over this window.")
        return
    p = lambda x: f"{x*100:+.2f}%"
    print("")
    print("=" * 62)
    print(f"  BACKTEST  top {top_n} BUY signals, {hold}-bar hold, "
          f"{slippage_bps:g}bps slippage/side")
    print(f"  {s['start']} -> {s['end']}   ({s['n_cohorts']} non-overlapping periods)")
    print("=" * 62)
    print(f"  trades              {s['n_trades']}  "
          f"(avg {s['avg_qualified']:.0f} names passed the gate per period)")
    print(f"  win rate            {s['win_rate']*100:.1f}%")
    print(f"  avg / median trade  {p(s['avg_ret'])} / {p(s['median_ret'])}")
    print(f"  avg win / avg loss  {p(s['avg_win'])} / {p(s['avg_loss'])}")
    print(f"  profit factor       {s['profit_factor']:.2f}")
    print(f"  avg bars held       {s['avg_bars_held']:.1f}")
    print(f"  exits               {s['exits']['target']} target / "
          f"{s['exits']['stop']} stop / {s['exits']['time']} time")
    print(f"  max drawdown        {s['max_drawdown']*100:.1f}%")
    print("-" * 62)
    print("  THE COMPARISONS THAT MATTER")
    print(f"  1. strategy (rank + ATR stop/target)   {p(s['total_return'])}")
    print(f"  2. same picks, plain {hold}-bar hold      "
          f"{p(s['picks_hold_return'])}")
    print(f"  3. every gated name, plain hold        {p(s['control_return'])}")
    print(f"  4. SPY buy & hold                      {p(s['benchmark_return'])}")
    print("-" * 62)
    # 2 vs 3 isolates the ranking (identical exits); 1 vs 2 isolates the exits
    # (identical picks). Comparing only 1 vs 3 conflates the two and would blame
    # whichever component you happened to name first.
    rank_edge = s["picks_hold_return"] - s["control_return"]
    exit_edge = s["total_return"] - s["picks_hold_return"]
    mkt_edge = s["total_return"] - s["benchmark_return"]
    print(f"  ranking  (2 vs 3, same exits)   {p(rank_edge)}")
    print(f"  exits    (1 vs 2, same picks)   {p(exit_edge)}")
    print(f"  vs market(1 vs 4)               {p(mkt_edge)}")
    print("")
    print("  " + ("the SCORE picks better than the gate alone."
                  if rank_edge > 0 else
                  "the SCORE picks WORSE than the gate alone — ranking is"
                  " subtracting value."))
    print("  " + ("the ATR stop/target adds value."
                  if exit_edge > 0 else
                  "the ATR stop/target DESTROYS value — the stop is cutting"
                  " winners early."))
    print("  " + ("beat SPY over this span." if mkt_edge > 0 else
                  "did NOT beat simply holding SPY over this span."))
    print("=" * 62)
    print("  Read with care — these biases inflate the numbers and cannot be")
    print("  removed with the cached data:")
    print("   * survivorship: universe.csv is TODAY's S&P 500; names that fell")
    print("     out of the index during the window are missing entirely")
    print("   * ~1.2y window, one regime — a long-only, above-200-EMA strategy")
    print("     flatters itself in a bull market")
    print("   * catalysts excluded (no point-in-time earnings/news available)")
    print("   * fills idealised at stop/target; real gaps are worse")
    print("  A positive result here is NOT evidence this makes money.")
    print("=" * 62)


def main() -> int:
    ap = argparse.ArgumentParser(description="Walk-forward backtest of the scan.")
    ap.add_argument("--top", type=int, default=5, help="picks per period")
    ap.add_argument("--hold", type=int, default=10, help="max bars held")
    ap.add_argument("--slippage-bps", type=float, default=5.0,
                    help="cost per side, basis points")
    ap.add_argument("--stop-mult", type=float, default=1.5, help="ATR stop")
    ap.add_argument("--target-mult", type=float, default=2.5, help="ATR target")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    def prog(i, n, d):
        if not a.quiet:
            print(f"  period {i}/{n}  {d}", end="\r", flush=True)

    res = run(top_n=a.top, hold=a.hold, slippage_bps=a.slippage_bps,
              stop_mult=a.stop_mult, target_mult=a.target_mult, progress=prog)
    if not a.quiet:
        print(" " * 40, end="\r")
    report(res["stats"], a.top, a.hold, a.slippage_bps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
