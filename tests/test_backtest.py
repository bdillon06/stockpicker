"""Tests for the backtest's integrity.

A backtest that cheats produces beautiful numbers and no warning, so the things
worth testing are the ones that would silently invalidate every result: seeing
data from the future, and exits that resolve optimistically.
"""
import numpy as np
import pytest

import backtest


def _bars(close, high=None, low=None, opens=None):
    n = len(close)
    close = np.asarray(close, dtype="float64")
    return {
        "dates": ["2026-01-%02d" % (i + 1) for i in range(n)],
        "open": list(opens if opens is not None else close),
        "high": list(high if high is not None else close * 1.01),
        "low": list(low if low is not None else close * 0.99),
        "close": list(close),
        "volume": [1e6] * n,
    }


# --- the cardinal sin: lookahead -------------------------------------------
def test_indicators_never_see_bars_after_the_decision_bar():
    """Appending future bars must not change the indicators computed at `upto`.

    If it does, the strategy is trading on information it could not have had and
    every downstream number is fiction.
    """
    rng = np.random.default_rng(3)
    close = np.cumsum(rng.normal(0.1, 1.0, 400)) + 200
    o = _bars(close)
    upto = 300

    before = backtest._record_for("T", o, upto, None)["indicators"]

    # A violent future: if any of it leaks backwards, the values will move.
    future = np.concatenate([close[:upto + 1], close[upto + 1:] * 3.0])
    o2 = _bars(future)
    after = backtest._record_for("T", o2, upto, None)["indicators"]

    for k, v in before.items():
        if isinstance(v, float) and not np.isnan(v):
            assert after[k] == pytest.approx(v), f"{k} leaked future data"


def test_record_refuses_to_score_before_the_ema200_warmup():
    o = _bars(np.linspace(100, 120, 400))
    assert backtest._record_for("T", o, backtest.WARMUP_BARS - 2, None) is None
    assert backtest._record_for("T", o, backtest.WARMUP_BARS + 5, None) is not None


def test_forward_return_buys_at_the_next_open_not_todays_close():
    """Entry must be a price you could actually have paid, i.e. tomorrow's open."""
    o = _bars(np.array([10.0, 20.0, 40.0]), opens=[10.0, 20.0, 40.0])
    # Decide on bar 0 -> enter at open of bar 1 (20) -> exit at close of bar 2 (40).
    assert backtest._forward_return(o, 0, hold=1) == pytest.approx(1.0)


# --- exits ------------------------------------------------------------------
def test_target_hit_exits_at_target():
    o = _bars(np.array([100.0, 100.0, 100.0]), high=[100.0, 100.0, 120.0],
              low=[99.0, 99.0, 99.0])
    i, price, why = backtest.simulate_exit(o, 0, stop=90.0, target=110.0, max_hold=5)
    assert (i, price, why) == (2, 110.0, "target")


def test_stop_hit_exits_at_stop():
    o = _bars(np.array([100.0, 100.0]), high=[101.0, 101.0], low=[99.0, 80.0])
    i, price, why = backtest.simulate_exit(o, 0, stop=90.0, target=110.0, max_hold=5)
    assert (i, price, why) == (1, 90.0, "stop")


def test_bar_spanning_both_resolves_to_the_stop():
    """Daily bars hide intraday order, so the pessimistic branch is the honest one.

    Resolving such a bar to the target would invent profit that may never have
    existed — the classic way a backtest flatters itself.
    """
    o = _bars(np.array([100.0]), high=[130.0], low=[80.0])
    i, price, why = backtest.simulate_exit(o, 0, stop=90.0, target=110.0, max_hold=5)
    assert why == "stop" and price == 90.0


def test_untouched_trade_exits_at_the_time_stop():
    o = _bars(np.array([100.0, 101.0, 102.0, 103.0]))
    i, price, why = backtest.simulate_exit(o, 0, stop=50.0, target=200.0, max_hold=2)
    assert why == "time" and i == 2


def test_exit_cannot_run_past_the_available_history():
    o = _bars(np.array([100.0, 101.0]))
    i, _price, _why = backtest.simulate_exit(o, 0, stop=50.0, target=200.0,
                                             max_hold=99)
    assert i == 1


# --- reporting --------------------------------------------------------------
def test_summarise_handles_no_trades():
    assert backtest.summarise([], [], {}, [], [], 10) == {"n_trades": 0}


def test_max_drawdown_is_the_worst_peak_to_trough():
    assert backtest._max_drawdown([1.0, 1.5, 0.75, 1.2]) == pytest.approx(-0.5)
