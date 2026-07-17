"""Tests for the universe date-alignment that keeps rankings comparable.

Ranking is percentile-based, so every stock in a scan must be priced on the
same session. Mixing a stock quoted today with peers quoted a month ago is the
bug that made the scanner replay a frozen snapshot and contradict itself when a
row was clicked.
"""
import app


def _entry(dates):
    n = max(len(dates), 30)
    close = [100.0] * n
    return ({"dates": dates, "open": close, "high": close, "low": close,
             "close": close, "volume": [1e6] * n}, 0.0)


def test_universe_date_is_the_session_most_names_share():
    price_map = {
        "AAA": _entry(["2026-07-16"] * 30),
        "BBB": _entry(["2026-07-16"] * 30),
        "CCC": _entry(["2026-06-17"] * 30),   # stale straggler
    }
    assert app._universe_date(price_map) == "2026-07-16"


def test_one_stray_future_bar_cannot_hijack_the_universe_date():
    """A single odd ticker must not disqualify every other name.

    max() would pick the stray date and drop the whole universe; the mode keeps
    the session the market actually agrees on.
    """
    price_map = {f"T{i}": _entry(["2026-07-16"] * 30) for i in range(20)}
    price_map["ODD"] = _entry(["2026-12-25"] * 30)
    assert app._universe_date(price_map) == "2026-07-16"


def test_benchmark_excluded_from_the_date_vote():
    price_map = {
        app.data.BENCHMARK: _entry(["2026-12-25"] * 30),
        "AAA": _entry(["2026-07-16"] * 30),
    }
    assert app._universe_date(price_map) == "2026-07-16"


def test_undated_cache_disables_alignment_rather_than_dropping_everything():
    assert app._universe_date({"AAA": _entry([])}) == ""


def test_stale_and_undated_names_are_left_out_of_the_ranking():
    price_map = {
        "FRESH": _entry(["2026-07-16"] * 30),
        "STALE": _entry(["2026-06-17"] * 30),
        "UNDATED": _entry([]),
    }
    recs = app._build_records(price_map, None, as_of="2026-07-16")
    assert [r["ticker"] for r in recs] == ["FRESH"]


def test_no_as_of_keeps_every_name():
    price_map = {"AAA": _entry([]), "BBB": _entry([])}
    recs = app._build_records(price_map, None, as_of="")
    assert len(recs) == 2
