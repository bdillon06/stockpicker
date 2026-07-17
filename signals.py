"""Actionable signals — pure, no network.

Converts a stock's indicators (and optional composite score) into a BUY / WATCH /
AVOID badge with the reasons that fired and ATR-based suggested entry, stop-loss
and target. This is what makes a pick actionable rather than just a number.
"""
from __future__ import annotations

import math


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def levels(ind: dict, stop_mult: float = 1.5, target_mult: float = 2.5) -> dict:
    """ATR-based entry / stop / target for a long swing trade.

    Entry is the current price; stop sits ``stop_mult`` ATR below; target
    ``target_mult`` ATR above. Returns NaNs if price or ATR is unavailable.
    """
    price = ind.get("price")
    a = ind.get("atr")
    if _nan(price) or _nan(a) or a <= 0:
        return {"entry": price, "stop": float("nan"), "target": float("nan"),
                "risk_reward": float("nan")}
    stop = price - stop_mult * a
    target = price + target_mult * a
    rr = (target - price) / (price - stop) if price > stop else float("nan")
    return {"entry": round(price, 2), "stop": round(stop, 2),
            "target": round(target, 2), "risk_reward": round(rr, 2)}


def evaluate(ind: dict, score: float | None = None) -> dict:
    """Produce a signal for one stock.

    Returns dict with ``badge`` (BUY/WATCH/AVOID), ``reasons`` (list of strings),
    and ``levels`` (entry/stop/target). The badge blends a few decisive
    technical conditions with the composite score when provided.
    """
    reasons = []
    bullish = 0
    bearish = 0

    price = ind.get("price")
    e13, e90, e200 = ind.get("ema13"), ind.get("ema90"), ind.get("ema200")
    # Trend — built on the 13/90/200 EMA stack.
    if not _nan(e13) and not _nan(e90):
        if e13 > e90:
            reasons.append("EMA13 above EMA90 (short-term trend up)")
            bullish += 1
        else:
            reasons.append("EMA13 below EMA90 (short-term trend down)")
            bearish += 1
    if not _nan(price) and not _nan(e200):
        if price > e200:
            reasons.append("Price above 200-EMA (long-term uptrend)")
            bullish += 1
        else:
            reasons.append("Price below 200-EMA (long-term downtrend)")
            bearish += 1
    if not any(_nan(x) for x in (e13, e90, e200)) and e13 > e90 > e200:
        reasons.append("Full bullish EMA stack (13 > 90 > 200)")
        bullish += 1
    if not _nan(ind.get("ema200_slope")) and ind["ema200_slope"] > 0:
        reasons.append("200-EMA rising")

    # Momentum
    if ind.get("macd_bullish_cross"):
        reasons.append("Fresh MACD bullish crossover")
        bullish += 1
    elif not _nan(ind.get("macd_hist")):
        if ind["macd_hist"] > 0:
            reasons.append("MACD histogram positive")
            bullish += 1
        else:
            reasons.append("MACD histogram negative")
            bearish += 1

    rsi = ind.get("rsi")
    if not _nan(rsi):
        if rsi > 72:
            reasons.append(f"RSI {rsi:.0f} overbought (chase risk)")
            bearish += 1
        elif rsi < 30:
            reasons.append(f"RSI {rsi:.0f} oversold")
        elif 45 <= rsi <= 68:
            reasons.append(f"RSI {rsi:.0f} in bullish sweet spot")
            bullish += 1

    # Breakout / volume
    if not _nan(ind.get("breakout")) and ind["breakout"] >= 1.0:
        reasons.append("Broke above 20-day high")
        bullish += 1
    if ind.get("bb_squeeze"):
        reasons.append("Bollinger squeeze — breakout pending")
    if not _nan(ind.get("volume_surge")) and ind["volume_surge"] >= 1.5:
        reasons.append(f"Volume surge {ind['volume_surge']:.1f}x average")
        bullish += 1

    # Decide badge. The absolute technical picture leads; the composite score
    # only confirms. Score is a *percentile* against the scanned universe, so on
    # a weak tape the best-ranked name still scores ~100 — gating on score alone
    # promoted "least bad" stocks to BUY. A setup must stand on its own chart.
    stack_ok = (not any(_nan(x) for x in (price, e13, e90, e200))
                and price > e200 and e13 > e90)
    if bearish == 0 and bullish >= 4 and stack_ok and (
            score is None or score >= 60):
        badge = "BUY"
    elif stack_ok and bullish > bearish and (score is None or score >= 40):
        badge = "WATCH"
    else:
        badge = "AVOID"

    return {"badge": badge, "reasons": reasons, "levels": levels(ind)}
