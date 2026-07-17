"""The displayed cut must be fully enriched — no mixed-basis rows.

Catalysts move a score by up to ~14 points, so enriching the leading top_n and
then re-sorting lets un-enriched names drift up into the visible list as
penalised names fall past them. Those rows showed a bare technical score beside
neighbours showing an adjusted one, and clicking one enriched it for the first
time and changed the number under the user. Regression cover for that.
"""
import json

import app


def _fake_record(ticker, score):
    return {"ticker": ticker, "name": "", "sector": "",
            "indicators": {"price": 100.0, "ema13": 99.0, "ema90": 95.0,
                           "ema200": 90.0, "atr": 2.0, "rsi": 55.0,
                           "dollar_volume": 5e8, "ext_atr_from_ema13": 0.5,
                           "macd_hist": 0.5, "ema200_slope": 0.01,
                           "breakout": 1.01, "volume_surge": 1.6},
            "score": score, "base_score": score, "factors": {},
            "factor_scores": {}}


def test_every_displayed_row_is_enriched(monkeypatch):
    """A name promoted into the cut by re-sorting must still get enriched."""
    top_n = 3
    # 6 candidates. The leaders carry earnings penalties that will drop them
    # below names that started outside the cut.
    ranked = [_fake_record(t, s) for t, s in
              [("AAA", 90.0), ("BBB", 89.0), ("CCC", 88.0),
               ("DDD", 87.0), ("EEE", 86.0), ("FFF", 85.0)]]
    penalised = {"AAA", "BBB", "CCC"}

    def fake_enrich(ticker, force=False):
        # Imminent earnings on the leaders -> -8 each, sinking them.
        return {"ticker": ticker,
                "days_to_earnings": 0 if ticker in penalised else None}

    monkeypatch.setattr(app.data, "enrich", fake_enrich)
    monkeypatch.setattr(app, "_ranked_universe",
                        lambda w=None, refresh=False: (
                            ranked, {r["ticker"]: r for r in ranked},
                            None, "2026-07-16"))
    monkeypatch.setattr(app.db, "prev_rank_snapshot", lambda d: (None, {}))
    monkeypatch.setattr(app.db, "save_rank_snapshot", lambda d, r: None)

    client = app.app.test_client()
    res = client.post("/api/scan", json={"top_n": top_n, "enrich": True,
                                         "hide_avoid": False}).get_json()

    shown = res["results"][:top_n]
    assert [r["ticker"] for r in shown] == ["DDD", "EEE", "FFF"], \
        "penalised leaders should have sunk below the promoted names"
    for r in shown:
        assert "enrich" in r, f"{r['ticker']} is displayed but was never enriched"
    assert res["unenriched"] == []


def test_hiding_avoid_rows_cannot_promote_an_unenriched_name(monkeypatch):
    """Dropping AVOID rows shifts the cut too — the promoted name needs enriching.

    A second route to the same defect: enrichment converges on the visible cut,
    so it must account for rows removed by hide_avoid, not just rows re-sorted
    by catalysts.
    """
    top_n = 2
    ranked = [_fake_record(t, s) for t, s in
              [("AAA", 95.0), ("BBB", 94.0), ("CCC", 80.0), ("DDD", 79.0)]]
    # Sink the two leaders below the AVOID cutoff so they are hidden entirely.
    for r in ranked[:2]:
        r["indicators"] = dict(r["indicators"], ema200=200.0)  # price < 200-EMA

    monkeypatch.setattr(app.data, "enrich",
                        lambda t, force=False: {"ticker": t})
    monkeypatch.setattr(app, "_ranked_universe",
                        lambda w=None, refresh=False: (
                            ranked, {r["ticker"]: r for r in ranked},
                            None, "2026-07-16"))
    monkeypatch.setattr(app.db, "prev_rank_snapshot", lambda d: (None, {}))
    monkeypatch.setattr(app.db, "save_rank_snapshot", lambda d, r: None)

    client = app.app.test_client()
    res = client.post("/api/scan", json={"top_n": top_n, "enrich": True,
                                         "hide_avoid": True}).get_json()

    assert [r["ticker"] for r in res["results"]] == ["CCC", "DDD"], \
        "the two sub-200-EMA leaders should have been hidden as AVOID"
    for r in res["results"]:
        assert "enrich" in r, f"{r['ticker']} promoted into view but not enriched"
    assert res["unenriched"] == []


def test_catalysts_off_reports_no_missing_enrichment(monkeypatch):
    """With catalysts disabled every row is technical-only by design, not by error."""
    ranked = [_fake_record("AAA", 90.0)]
    monkeypatch.setattr(app, "_ranked_universe",
                        lambda w=None, refresh=False: (
                            ranked, {r["ticker"]: r for r in ranked},
                            None, "2026-07-16"))
    monkeypatch.setattr(app.db, "prev_rank_snapshot", lambda d: (None, {}))
    monkeypatch.setattr(app.db, "save_rank_snapshot", lambda d, r: None)
    client = app.app.test_client()
    res = client.post("/api/scan", json={"enrich": False}).get_json()
    assert res["unenriched"] == []


def test_unenriched_rows_are_reported_not_swallowed(monkeypatch):
    """A throttled per-ticker call must surface, not silently skew a row."""
    ranked = [_fake_record(t, s) for t, s in [("AAA", 90.0), ("BBB", 89.0)]]

    def boom(ticker, force=False):
        raise RuntimeError("Yahoo says 429")

    monkeypatch.setattr(app.data, "enrich", boom)
    monkeypatch.setattr(app, "_ranked_universe",
                        lambda w=None, refresh=False: (
                            ranked, {r["ticker"]: r for r in ranked},
                            None, "2026-07-16"))
    monkeypatch.setattr(app.db, "prev_rank_snapshot", lambda d: (None, {}))
    monkeypatch.setattr(app.db, "save_rank_snapshot", lambda d, r: None)

    client = app.app.test_client()
    res = client.post("/api/scan", json={"top_n": 2, "enrich": True,
                                         "hide_avoid": False}).get_json()
    # The scan still returns technical scores rather than failing outright...
    assert len(res["results"]) == 2
    assert res["enriched"] == 0
    # ...but says so, instead of leaving the rows quietly inconsistent.
    assert set(res["unenriched"]) == {"AAA", "BBB"}
