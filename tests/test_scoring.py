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


def test_uptrend_signal_is_buy_with_valid_levels():
    rec = uptrend_record()
    ranked = scoring.rank_universe([rec, downtrend_record(), flat_record()])
    top = ranked[0]
    sig = signals.evaluate(top["indicators"], top["score"])
    assert sig["badge"] == "BUY"
    lv = sig["levels"]
    assert lv["stop"] < lv["entry"] < lv["target"]
    assert lv["risk_reward"] > 0


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
