"""Network-free tests for indicator math using synthetic OHLCV."""
import math

import numpy as np
import pytest

import indicators as ind


def make_ohlcv(close, vol=None):
    close = np.asarray(close, dtype="float64")
    if vol is None:
        vol = np.full(len(close), 1_000_000.0)
    return {
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": np.asarray(vol, dtype="float64"),
    }


def test_sma_and_ema_basic():
    vals = list(range(1, 21))  # 1..20
    assert ind.sma(vals, 5) == pytest.approx(18.0)  # mean of 16..20
    # EMA of a steadily rising series should sit below the latest value.
    e = ind.ema(vals, 5)
    assert 16 < e < 20


def test_insufficient_history_returns_nan():
    assert math.isnan(ind.sma([1, 2], 5))
    assert math.isnan(ind.rsi([1, 2, 3], 14))
    assert math.isnan(ind.atr([1], [1], [1], 14))


def test_rsi_strong_uptrend_high():
    close = np.linspace(100, 200, 60)  # monotonically rising
    r = ind.rsi(close, 14)
    assert r > 80  # nearly all gains -> very high RSI


def test_rsi_downtrend_low():
    close = np.linspace(200, 100, 60)
    r = ind.rsi(close, 14)
    assert r < 20


def test_ema_stack_bullish_in_uptrend():
    close = np.linspace(100, 150, 300)
    o = ind.compute_all(make_ohlcv(close))
    # Full bullish 13/90/200 EMA stack in a steady uptrend.
    assert o["ema13"] > o["ema90"] > o["ema200"]
    assert o["price"] > o["ema200"]
    assert o["ema200_slope"] > 0  # long-term EMA pointing up


def test_breakout_detects_new_high():
    # Flat then a jump on the last bar -> breakout ratio > 1.
    close = np.concatenate([np.full(30, 100.0), [110.0]])
    b = ind.breakout_strength(close * 1.01, close, 20)
    assert b > 1.0


def test_volume_surge_ratio():
    close = np.linspace(100, 110, 40)
    vol = np.concatenate([np.full(39, 1_000_000.0), [3_000_000.0]])
    vs = ind.volume_surge(vol, 20)
    assert vs == pytest.approx(3.0, rel=1e-6)


def test_relative_strength_sign():
    stock = np.linspace(100, 130, 100)   # +30%
    bench = np.linspace(100, 110, 100)   # +10%
    rs = ind.relative_strength(stock, bench, 63)
    assert rs > 0


def test_atr_positive_and_reasonable():
    close = np.linspace(100, 120, 60)
    a = ind.atr(close * 1.02, close * 0.98, close, 14)
    assert a > 0


def test_compute_all_keys_present():
    o = ind.compute_all(make_ohlcv(np.linspace(100, 150, 300)))
    for k in ("price", "ema13", "ema90", "ema200", "ema200_slope", "rsi",
              "macd_hist", "atr", "breakout", "volume_surge", "ret_20d"):
        assert k in o
