"""Short-Term Upside Score — pure ranking logic, no network.

Turns the indicator dicts from ``indicators.compute_all`` into a 0-100 score per
stock, tuned for swing trades (days to ~2 weeks). Each factor group is reduced to
a single raw scalar, then **percentile-ranked across the scanned universe** so the
groups are comparable before the weighted blend. Catalyst adjustments (earnings,
analyst, news, options, short interest) are applied separately in stage 2 via
``apply_catalysts`` so this core stays network-free and testable.
"""
from __future__ import annotations

import math

# Heavy focus on the 13/90/200 EMA trend stack; momentum/breakout/volume confirm.
DEFAULT_WEIGHTS = {
    "trend": 0.45,
    "momentum": 0.20,
    "breakout": 0.15,
    "volume": 0.10,
    "rel_strength": 0.10,
}

FACTORS = list(DEFAULT_WEIGHTS.keys())


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def raw_factors(ind: dict) -> dict:
    """Reduce an indicator dict to one raw scalar per factor group.

    Returns floats (or NaN where history is insufficient). Higher is more
    bullish for the short term. These raw values are percentile-ranked later.
    """
    price = ind.get("price")

    # --- Trend: the 13/90/200 EMA stack (heavily weighted) ---
    # Rewards full bullish alignment (price > EMA13 > EMA90 > EMA200) and upward
    # slope on each EMA. Being below the 200-EMA is penalised outright.
    trend = float("nan")
    e13, e90, e200 = ind.get("ema13"), ind.get("ema90"), ind.get("ema200")
    if not _nan(e13) and not _nan(e90) and not _nan(price):
        trend = 0.0
        trend += 1.0 if price > e13 else 0.0          # above short-term EMA
        trend += 1.5 if e13 > e90 else 0.0            # short above intermediate
        if not _nan(e200):
            trend += 1.0 if e90 > e200 else 0.0       # intermediate above long
            trend += 1.5 if price > e200 else -1.0    # the key 200-EMA regime
        # Slope confirmation — the stack should be pointing up, not just stacked.
        if not _nan(ind.get("ema13_slope")) and ind["ema13_slope"] > 0:
            trend += 0.6
        if not _nan(ind.get("ema90_slope")) and ind["ema90_slope"] > 0:
            trend += 0.5
        if not _nan(ind.get("ema200_slope")) and ind["ema200_slope"] > 0:
            trend += 0.4

    # --- Momentum: RSI sweet spot + MACD + 20d return, each ~[0,1] ---
    momentum = float("nan")
    rsi = ind.get("rsi")
    if not _nan(rsi):
        rsi_comp = _clamp(1.0 - abs(rsi - 60.0) / 30.0)
        if rsi > 72:  # overbought -> chase risk, damp the contribution
            rsi_comp *= 0.3
        macd_comp = 0.0
        if not _nan(ind.get("macd_hist")):
            macd_comp = (0.6 if ind["macd_hist"] > 0 else 0.0)
            if ind.get("macd_bullish_cross"):
                macd_comp += 0.4
        ret_comp = 0.0
        if not _nan(ind.get("ret_20d")):
            ret_comp = _clamp((ind["ret_20d"] + 0.10) / 0.30)  # -10%->0, +20%->1
        momentum = rsi_comp + macd_comp + ret_comp  # ~[0,3]

    # --- Breakout / volatility expansion ---
    breakout = float("nan")
    if not _nan(ind.get("breakout")):
        breakout = _clamp((ind["breakout"] - 0.95) / 0.10)  # 0.95->0, 1.05->1
        if ind.get("bb_squeeze"):
            breakout += 0.3
        if not _nan(ind.get("bb_pct_b")) and ind["bb_pct_b"] > 0.8:
            breakout += 0.2  # riding the upper band

    # --- Volume confirmation ---
    volume = float("nan")
    if not _nan(ind.get("volume_surge")):
        vs = _clamp((ind["volume_surge"] - 1.0) / 1.5) * 0.6  # 1x->0, 2.5x->0.6
        obv = max(0.0, ind.get("obv_trend") or 0.0) * 0.4
        volume = vs + obv

    rel = ind.get("rel_strength")
    rel_strength = float("nan") if _nan(rel) else float(rel)

    return {
        "trend": trend,
        "momentum": momentum,
        "breakout": breakout,
        "volume": volume,
        "rel_strength": rel_strength,
    }


def _percentile_rank(values: list) -> list:
    """Map a list of raw values to 0-100 percentile ranks.

    NaNs are assigned the median rank (50) so missing data neither helps nor
    dominates. Ties share the average rank.
    """
    n = len(values)
    idx_valid = [i for i, v in enumerate(values) if not _nan(v)]
    out = [50.0] * n
    if not idx_valid:
        return out
    if len(idx_valid) == 1:
        out[idx_valid[0]] = 50.0
        return out
    ordered = sorted(idx_valid, key=lambda i: values[i])
    m = len(ordered)
    # Assign rank fractions; handle ties by averaging positions.
    pos = 0
    while pos < m:
        j = pos
        while j + 1 < m and values[ordered[j + 1]] == values[ordered[pos]]:
            j += 1
        avg_pos = (pos + j) / 2.0
        pct = (avg_pos / (m - 1)) * 100.0 if m > 1 else 50.0
        for k in range(pos, j + 1):
            out[ordered[k]] = pct
        pos = j + 1
    return out


def rank_universe(records: list, weights: dict | None = None) -> list:
    """Score and rank a universe.

    ``records``: list of dicts each with at least ``ticker`` and ``indicators``
    (the dict from ``indicators.compute_all``). Returns the same records sorted
    by ``score`` descending, each annotated with ``score`` (0-100), ``factors``
    (raw values) and ``factor_scores`` (percentile ranks).
    """
    if weights is None:
        weights = DEFAULT_WEIGHTS
    wsum = sum(weights.get(f, 0.0) for f in FACTORS) or 1.0

    raws = [raw_factors(r["indicators"]) for r in records]
    # Percentile-rank each factor column across the universe.
    ranked_cols = {
        f: _percentile_rank([raws[i][f] for i in range(len(records))])
        for f in FACTORS
    }

    out = []
    for i, rec in enumerate(records):
        fscores = {f: ranked_cols[f][i] for f in FACTORS}
        composite = sum(fscores[f] * weights.get(f, 0.0) for f in FACTORS) / wsum
        enriched = dict(rec)
        enriched["factors"] = raws[i]
        enriched["factor_scores"] = fscores
        enriched["score"] = round(composite, 1)
        out.append(enriched)

    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def apply_catalysts(base_score: float, enrich: dict) -> tuple:
    """Adjust a base technical score with stage-2 public-information catalysts.

    ``enrich`` is the dict from ``data.enrich`` (may be partial). Returns
    ``(adjusted_score, notes)`` where notes is a list of human-readable strings
    explaining each adjustment. Score stays clamped to 0-100.
    """
    score = base_score
    notes = []
    if not enrich:
        return round(score, 1), notes

    # Upcoming earnings within 3 days -> gap risk for a short hold.
    days = enrich.get("days_to_earnings")
    if days is not None and 0 <= days <= 3:
        score -= 8
        notes.append(f"Earnings in {days}d — gap risk (−8)")

    # Recent net analyst upgrades.
    up = enrich.get("recent_upgrades", 0)
    down = enrich.get("recent_downgrades", 0)
    if up - down >= 1:
        boost = min(6, (up - down) * 3)
        score += boost
        notes.append(f"{up} recent upgrade(s) (+{boost})")
    elif down - up >= 1:
        score -= min(6, (down - up) * 3)
        notes.append(f"{down} recent downgrade(s) (−{min(6, (down - up) * 3)})")

    # Positive news flow.
    news = enrich.get("recent_news_count", 0)
    if news >= 3:
        score += 3
        notes.append(f"{news} fresh headlines (+3)")

    # Short-squeeze setup: high short interest while already trending up.
    short_pct = enrich.get("short_pct_float")
    if short_pct is not None and short_pct >= 0.15:
        score += 5
        notes.append(f"High short interest {short_pct:.0%} — squeeze potential (+5)")

    # Bullish options skew (low put/call).
    pcr = enrich.get("put_call_ratio")
    if pcr is not None and pcr < 0.7:
        score += 3
        notes.append(f"Bullish options skew (P/C {pcr:.2f}) (+3)")

    return round(max(0.0, min(100.0, score)), 1), notes
