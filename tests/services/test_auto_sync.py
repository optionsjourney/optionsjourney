from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from optionsjourney.services import auto_sync

OPEN = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)  # Monday 10:00 New York


def _services(monkeypatch, chosen):
    calls = []
    monkeypatch.setattr(auto_sync.settings, "get", lambda _sf, key: chosen[key])
    monkeypatch.setattr(auto_sync, "sync_all", lambda clients, sf: calls.append(clients) or [])
    return SimpleNamespace(session_factory=None, broker_clients=lambda: ["broker"]), calls


def test_syncs_when_due_and_waits_otherwise(monkeypatch):
    services, calls = _services(monkeypatch, {"sync_market_hours": "1", "sync_other_times": "60"})
    last = auto_sync.run_if_due(services, None, OPEN)
    assert last == OPEN and calls == [["broker"]]
    assert auto_sync.run_if_due(services, last, OPEN + timedelta(seconds=30)) == OPEN
    assert len(calls) == 1
    assert auto_sync.run_if_due(services, last, OPEN + timedelta(minutes=1)) > OPEN
    assert len(calls) == 2


def test_off_never_syncs(monkeypatch):
    services, calls = _services(
        monkeypatch, {"sync_market_hours": "off", "sync_other_times": "off"}
    )
    assert auto_sync.run_if_due(services, None, OPEN) is None
    assert calls == []
