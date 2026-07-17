"""The universe refresh must not stampede.

The server is multi-threaded (gunicorn ``--threads 4``) and Yahoo throttles the
very endpoint a scan depends on, so concurrent scans must share one fetch rather
than each starting a 500-ticker download.
"""
import threading
import time

import app


def _stub(monkeypatch):
    calls = []

    def fake_bulk(tickers, force=False, **kw):
        calls.append(time.time())
        time.sleep(0.2)
        return {}

    monkeypatch.setattr(app.data, "bulk_history", fake_bulk)
    monkeypatch.setattr(app.data, "get_benchmark", lambda *a, **k: None)
    monkeypatch.setattr(app, "_LAST_REFRESH", 0.0)
    return calls


def test_concurrent_scans_share_one_fetch(monkeypatch):
    calls = _stub(monkeypatch)
    threads = [threading.Thread(target=app._ensure_fresh) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1, "concurrent scans each started their own download"


def test_repeat_attempt_within_cooldown_is_skipped(monkeypatch):
    calls = _stub(monkeypatch)
    app._ensure_fresh()
    app._ensure_fresh()
    assert len(calls) == 1


def test_retry_is_possible_once_the_cooldown_lapses(monkeypatch):
    """A failed fetch must not lock refreshing out for the whole PRICE_TTL."""
    calls = _stub(monkeypatch)
    app._ensure_fresh()
    monkeypatch.setattr(app, "_LAST_REFRESH", 0.0)  # cooldown elapsed
    app._ensure_fresh()
    assert len(calls) == 2


def test_force_always_fetches(monkeypatch):
    calls = _stub(monkeypatch)
    app._ensure_fresh()
    app._ensure_fresh(force=True)
    assert len(calls) == 2


def test_cooldown_is_far_shorter_than_the_price_ttl():
    """It collapses stampedes; PRICE_TTL alone decides what counts as fresh."""
    assert app._REFRESH_COOLDOWN < app.data.PRICE_TTL / 10
