"""Pure technical-indicator math for swing-trade analysis.

Every function here takes plain numpy arrays / pandas Series of OHLCV data and
returns numbers or small dicts. There is NO network access and NO global state,
so the whole module is unit-testable offline. ``data.py`` is the only place that
touches yfinance; this module only consumes the price history it produces.

Conventions
-----------
- ``close``/``high``/``low``/``volume`` are 1-D numpy arrays, oldest first.
- Functions return ``float('nan')`` (not an exception) when there is not enough
  history to compute a value, so callers can degrade gracefully.
"""
from __future__ import annotations

import numpy as np

NAN = float("nan")


def _as_array(x) -> np.ndarray:
    return np.asarray(x, dtype="float64")


def sma(values, period: int) -> float:
    """Simple moving average of the last ``period`` values."""
    v = _as_array(values)
    if len(v) < period or period <= 0:
        return NAN
    return float(np.mean(v[-period:]))


def ema_series(values, period: int) -> np.ndarray:
    """Full exponential-moving-average series (same length as input).

    Seeded with an SMA of the first ``period`` points, which is the standard
    convention and keeps the series stable for short inputs.
    """
    v = _as_array(values)
    n = len(v)
    out = np.full(n, NAN)
    if n < period or period <= 0:
        return out
    k = 2.0 / (period + 1.0)
    seed = float(np.mean(v[:period]))
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = v[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def ema(values, period: int) -> float:
    """Latest EMA value."""
    s = ema_series(values, period)
    return float(s[-1]) if len(s) and not np.isnan(s[-1]) else NAN


def ema_slope(values, period: int, lookback: int = 10) -> float:
    """Normalised slope of an EMA over ``lookback`` bars.

    Positive means the EMA is rising (trend strengthening). Used to confirm the
    13/90/200 stack is pointing up, not just stacked from an old move.
    """
    s = ema_series(values, period)
    if len(s) <= lookback:
        return NAN
    a, b = s[-1], s[-1 - lookback]
    if np.isnan(a) or np.isnan(b) or b == 0:
        return NAN
    return float((a - b) / abs(b))


def rsi(values, period: int = 14) -> float:
    """Wilder's RSI (0-100)."""
    v = _as_array(values)
    if len(v) < period + 1:
        return NAN
    deltas = np.diff(v)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    # Wilder's smoothing: seed with simple average, then recursively smooth.
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def macd(values, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD line, signal line and histogram (latest values).

    Returns dict with ``macd``, ``signal``, ``hist`` and ``bullish_cross``
    (True when the MACD line crossed above its signal line on the last bar).
    """
    v = _as_array(values)
    if len(v) < slow + signal:
        return {"macd": NAN, "signal": NAN, "hist": NAN, "bullish_cross": False}
    fast_e = ema_series(v, fast)
    slow_e = ema_series(v, slow)
    macd_line = fast_e - slow_e
    valid = macd_line[~np.isnan(macd_line)]
    sig_series = ema_series(valid, signal)
    macd_now = float(valid[-1])
    sig_now = float(sig_series[-1])
    hist_now = macd_now - sig_now
    # Detect a fresh bullish cross: prev hist <= 0, current hist > 0.
    bullish = False
    if len(valid) >= 2 and not np.isnan(sig_series[-2]):
        hist_prev = float(valid[-2]) - float(sig_series[-2])
        bullish = hist_prev <= 0 < hist_now
    return {"macd": macd_now, "signal": sig_now, "hist": hist_now,
            "bullish_cross": bullish}


def bollinger(values, period: int = 20, num_std: float = 2.0) -> dict:
    """Bollinger Bands plus bandwidth and a squeeze flag.

    ``squeeze`` is True when current bandwidth is in the lowest quartile of its
    own recent history — a classic "coiled spring" pre-breakout condition.
    """
    v = _as_array(values)
    if len(v) < period:
        return {"upper": NAN, "lower": NAN, "mid": NAN, "bandwidth": NAN,
                "pct_b": NAN, "squeeze": False}
    window = v[-period:]
    mid = float(np.mean(window))
    sd = float(np.std(window))
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    bandwidth = (upper - lower) / mid if mid else NAN
    last = float(v[-1])
    pct_b = (last - lower) / (upper - lower) if upper != lower else NAN
    # Squeeze: compare current bandwidth to a trailing distribution.
    squeeze = False
    if len(v) >= period * 3:
        bws = []
        for i in range(period, len(v) + 1):
            w = v[i - period:i]
            m = np.mean(w)
            s = np.std(w)
            if m:
                bws.append((2 * num_std * s) / m)
        if bws:
            squeeze = bandwidth <= np.percentile(bws, 25)
    return {"upper": upper, "lower": lower, "mid": mid, "bandwidth": bandwidth,
            "pct_b": pct_b, "squeeze": squeeze}


def atr(high, low, close, period: int = 14) -> float:
    """Average True Range (Wilder)."""
    h, l, c = _as_array(high), _as_array(low), _as_array(close)
    n = len(c)
    if n < period + 1:
        return NAN
    tr = np.empty(n - 1)
    for i in range(1, n):
        tr[i - 1] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    # Wilder smoothing of TR.
    a = np.mean(tr[:period])
    for i in range(period, len(tr)):
        a = (a * (period - 1) + tr[i]) / period
    return float(a)


def obv_trend(close, volume, lookback: int = 20) -> float:
    """On-Balance-Volume slope over ``lookback`` bars, normalised.

    Returns a value in roughly [-1, 1]: positive means OBV is rising
    (accumulation), negative means distribution.
    """
    c, vol = _as_array(close), _as_array(volume)
    if len(c) < lookback + 1:
        return NAN
    obv = np.zeros(len(c))
    for i in range(1, len(c)):
        if c[i] > c[i - 1]:
            obv[i] = obv[i - 1] + vol[i]
        elif c[i] < c[i - 1]:
            obv[i] = obv[i - 1] - vol[i]
        else:
            obv[i] = obv[i - 1]
    seg = obv[-lookback:]
    rng = seg.max() - seg.min()
    if rng == 0:
        return 0.0
    # Slope of a simple linear fit, scaled by the segment range.
    x = np.arange(lookback)
    slope = np.polyfit(x, seg, 1)[0]
    return float(np.clip(slope * lookback / rng, -1.0, 1.0))


def volume_surge(volume, period: int = 20) -> float:
    """Ratio of latest volume to its trailing ``period``-day average."""
    v = _as_array(volume)
    if len(v) < period + 1:
        return NAN
    avg = np.mean(v[-period - 1:-1])
    if avg == 0:
        return NAN
    return float(v[-1] / avg)


def dollar_volume(close, volume, period: int = 20) -> float:
    """Average daily traded dollars over ``period`` bars (price x shares).

    The liquidity floor for whether a setup is actually tradable — share volume
    alone is misleading across very different price levels.
    """
    c, v = _as_array(close), _as_array(volume)
    n = min(len(c), len(v))
    if n < period:
        return NAN
    return float(np.mean(c[-period:] * v[-period:]))


def breakout_strength(high, close, lookback: int = 20) -> float:
    """How far the latest close sits relative to the prior N-day high.

    >1.0 means a fresh breakout above the prior range high; ~1.0 means right at
    resistance; <1.0 means still inside the range.
    """
    h, c = _as_array(high), _as_array(close)
    if len(c) < lookback + 1:
        return NAN
    prior_high = np.max(h[-lookback - 1:-1])
    if prior_high == 0:
        return NAN
    return float(c[-1] / prior_high)


def relative_strength(close, bench_close, lookback: int = 63) -> float:
    """Return of the stock minus return of a benchmark over ``lookback`` bars.

    Default 63 trading days ≈ 3 months. Positive = outperforming the market.
    """
    c, b = _as_array(close), _as_array(bench_close)
    if len(c) < lookback + 1 or len(b) < lookback + 1:
        return NAN
    stock_ret = c[-1] / c[-lookback - 1] - 1.0
    bench_ret = b[-1] / b[-lookback - 1] - 1.0
    return float(stock_ret - bench_ret)


def pct_return(close, lookback: int) -> float:
    """Simple percentage return over ``lookback`` bars."""
    c = _as_array(close)
    if len(c) < lookback + 1:
        return NAN
    return float(c[-1] / c[-lookback - 1] - 1.0)


def compute_all(ohlcv: dict, bench_close=None) -> dict:
    """Compute the full indicator set for one stock.

    ``ohlcv`` is a dict of arrays with keys ``open, high, low, close, volume``
    (oldest first). ``bench_close`` is the benchmark (e.g. SPY) close array for
    relative strength; may be None.

    Returns a flat dict of indicator values used by ``scoring`` and ``signals``.
    """
    close = _as_array(ohlcv["close"])
    high = _as_array(ohlcv["high"])
    low = _as_array(ohlcv["low"])
    volume = _as_array(ohlcv["volume"])
    last = float(close[-1]) if len(close) else NAN

    macd_d = macd(close)
    boll = bollinger(close)
    e13 = ema(close, 13)
    atr_v = atr(high, low, close, 14)
    # How far price has run above the 13-EMA, in ATRs — the "am I chasing?"
    # measure. NaN (rather than a divide-by-zero) when ATR is unavailable.
    ext = (last - e13) / atr_v if not (
        np.isnan(e13) or np.isnan(atr_v) or atr_v <= 0) else NAN
    return {
        "price": last,
        # --- Core moving-average framework: 13 / 90 / 200 EMA stack ---
        "ema13": e13,
        "ema90": ema(close, 90),
        "ema200": ema(close, 200),
        "ema13_slope": ema_slope(close, 13, 10),
        "ema90_slope": ema_slope(close, 90, 10),
        "ema200_slope": ema_slope(close, 200, 20),
        "rsi": rsi(close, 14),
        "macd": macd_d["macd"],
        "macd_signal": macd_d["signal"],
        "macd_hist": macd_d["hist"],
        "macd_bullish_cross": macd_d["bullish_cross"],
        "bb_upper": boll["upper"],
        "bb_lower": boll["lower"],
        "bb_pct_b": boll["pct_b"],
        "bb_squeeze": boll["squeeze"],
        "atr": atr_v,
        "obv_trend": obv_trend(close, volume, 20),
        "volume_surge": volume_surge(volume, 20),
        "dollar_volume": dollar_volume(close, volume, 20),
        "ext_atr_from_ema13": ext,
        "breakout": breakout_strength(high, close, 20),
        "rel_strength": relative_strength(close, bench_close, 63)
        if bench_close is not None else NAN,
        "ret_5d": pct_return(close, 5),
        "ret_20d": pct_return(close, 20),
    }
