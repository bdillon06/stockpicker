"""Network-free tests for scoring, ranking and signals."""
import numpy as np

import indicators as ind
import scoring
import signals


def ohlcv(close, vol=None):
    close = np.asarray(close, dtype="float64")
    if vol is None:
        vol = np.full(len(close), 1_000_000.0)
    return {"open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": np.asarray(vol, dtype="float64")}


def uptrend_record(ticker="UP"):
    # Steady climb that accelerates, with a closing volume surge.
    close = np.concatenate([np.linspace(80, 120, 230), np.linspace(120, 140, 20)])
    vol = np.concatenate([np.full(249, 1_000_000.0), [3_000_000.0]])
    bench = np.linspace(100, 105, len(close))  # market barely up -> strong RS
    return {"ticker": ticker,
            "indicators": ind.compute_all(ohlcv(close, vol), bench_close=bench)}


def pullback_record(ticker="PB"):
    """A realistic swing entry: established uptrend, mild pullback, resumption.

    Deliberately not a straight ramp — a perfect line pins RSI at 100, which is
    a chase, not a setup. Here RSI lands mid-band with the 13/90/200 stack
    intact, which is the condition the scanner is meant to reward.
    """
    rng = np.random.default_rng(7)
    base = np.linspace(80, 128, 235) + rng.normal(0, 0.7, 235)
    pull = np.linspace(128, 121, 10)     # orderly dip toward the 13-EMA
    resume = np.linspace(121, 129, 8)    # buyers step back in
    close = np.concatenate([base, pull, resume])
    vol = np.concatenate([np.full(len(close) - 1, 1_000_000.0), [2_400_000.0]])
    bench = np.linspace(100, 105, len(close))
    return {"ticker": ticker,
            "indicators": ind.compute_all(ohlcv(close, vol), bench_close=bench)}


def downtrend_record(ticker="DN"):
    close = np.linspace(140, 90, 250)
    bench = np.linspace(100, 105, len(close))
    return {"ticker": ticker,
            "indicators": ind.compute_all(ohlcv(close), bench_close=bench)}


def flat_record(ticker="FLAT"):
    close = 100 + np.sin(np.linspace(0, 12, 250)) * 2  # choppy sideways
    bench = np.linspace(100, 105, len(close))
    return {"ticker": ticker,
            "indicators": ind.compute_all(ohlcv(close), bench_close=bench)}


def test_uptrend_outranks_downtrend():
    ranked = scoring.rank_universe(
        [downtrend_record(), uptrend_record(), flat_record()])
    assert ranked[0]["ticker"] == "UP"
    assert ranked[-1]["ticker"] == "DN"
    assert ranked[0]["score"] > ranked[-1]["score"]


def test_scores_in_range():
    ranked = scoring.rank_universe([uptrend_record(), downtrend_record()])
    for r in ranked:
        assert 0 <= r["score"] <= 100
        assert set(r["factor_scores"]) == set(scoring.FACTORS)


def test_pullback_setup_signals_buy_with_valid_levels():
    rec = pullback_record()
    ranked = scoring.rank_universe([rec, downtrend_record(), flat_record()])
    top = ranked[0]
    assert top["ticker"] == "PB"
    sig = signals.evaluate(top["indicators"], top["score"])
    assert sig["badge"] == "BUY"
    lv = sig["levels"]
    assert lv["stop"] < lv["entry"] < lv["target"]
    assert lv["risk_reward"] > 0


def test_parabolic_uptrend_is_not_a_buy():
    """A vertical ramp (RSI ~100) must not be badged BUY however well it ranks.

    Guards the regression this scanner had: score is a percentile, so the best
    name in a universe scores ~100 and used to earn BUY on that alone.
    """
    rec = uptrend_record()
    sig = signals.evaluate(rec["indicators"], 100.0)
    assert sig["badge"] != "BUY"
    assert any("overbought" in r for r in sig["reasons"])


def test_downtrend_signal_avoids():
    rec = downtrend_record()
    sig = signals.evaluate(rec["indicators"], 20.0)
    assert sig["badge"] == "AVOID"


def test_catalyst_adjustments():
    base = 60.0
    # Earnings imminent -> penalty.
    s, notes = scoring.apply_catalysts(base, {"days_to_earnings": 1})
    assert s < base and any("Earnings" in n for n in notes)
    # Upgrades + squeeze -> boost.
    s2, notes2 = scoring.apply_catalysts(
        base, {"recent_upgrades": 2, "recent_downgrades": 0,
               "short_pct_float": 0.20})
    assert s2 > base
    assert any("upgrade" in n for n in notes2)
    assert any("squeeze" in n.lower() for n in notes2)


def test_catalyst_score_clamped():
    s, _ = scoring.apply_catalysts(99.0, {"recent_upgrades": 5,
                                          "short_pct_float": 0.5,
                                          "put_call_ratio": 0.3,
                                          "recent_news_count": 10})
    assert s <= 100.0


# --- EMA / liquidity gate --------------------------------------------------
def test_downtrend_fails_ema_gate():
    """A stock under its 200-EMA must never reach the results, however it ranks."""
    fails = scoring.check_filters(downtrend_record()["indicators"])
    assert fails
    assert any("200-EMA" in f for f in fails)


def test_pullback_passes_ema_gate():
    assert scoring.passes_filters(pullback_record()["indicators"])


def test_illiquid_name_is_filtered_out():
    rec = pullback_record()
    rec["indicators"]["dollar_volume"] = 50_000.0  # ~$50k/day: untradable
    fails = scoring.check_filters(rec["indicators"])
    assert any("liquidity" in f for f in fails)


def test_overextended_name_is_filtered_out():
    rec = pullback_record()
    rec["indicators"]["ext_atr_from_ema13"] = 9.0  # 9 ATR above the 13-EMA
    fails = scoring.check_filters(rec["indicators"])
    assert any("overextended" in f for f in fails)


def test_filters_are_overridable():
    ind_d = pullback_record()["indicators"]
    # Demanding a rising 200-EMA and a full stack is stricter, never looser.
    strict = scoring.check_filters(ind_d, {"require_full_stack": True,
                                           "require_slow_rising": True})
    assert len(strict) >= len(scoring.check_filters(ind_d))
    # A malformed override falls back to the default rather than nuking results.
    assert scoring.filter_params({"min_price": "abc"})["min_price"] == \
        scoring.DEFAULT_FILTERS["min_price"]


def test_missing_history_fails_rather_than_passes():
    """An unverifiable rule must fail closed, not wave the stock through."""
    fails = scoring.check_filters({"price": 50.0})  # no EMAs at all
    assert any("insufficient history" in f for f in fails)
