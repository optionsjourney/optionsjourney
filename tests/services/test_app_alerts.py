"""The app's own alerts: unread until read, never doubled (#186)."""

from datetime import UTC, datetime

from optionsjourney.services import app_alerts, notify

NOW = datetime(2026, 10, 19, 20, 20, tzinfo=UTC)


def _add(factory, key="k1", title="Settled"):
    return app_alerts.add(
        factory, dedupe_key=key, kind="settled", title=title, detail="d", link="/journal", now=NOW
    )


def test_add_is_unread_until_marked_read_and_never_doubles(factory, monkeypatch):
    monkeypatch.setattr(notify, "send_soon", lambda *a, **k: None)
    assert _add(factory) is True
    assert _add(factory) is False
    assert app_alerts.unread_count(factory) == 1
    assert app_alerts.mark_read(factory, [a.id for a in app_alerts.recent(factory)], NOW) == 1
    assert app_alerts.unread_count(factory) == 0
    assert [a.title for a in app_alerts.recent(factory)] == ["Settled"]


def test_add_sends_through_the_notification_channel(factory, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_soon", lambda sf, event: sent.append(event))
    _add(factory, title="Closed at 4:15")
    assert [e.title for e in sent] == ["Closed at 4:15"]


def test_on_change_runs_after_a_new_alert_and_a_mark_read_and_never_fails_the_caller(
    factory, monkeypatch
):
    monkeypatch.setattr(notify, "send_soon", lambda *a, **k: None)
    calls = []
    app_alerts.set_on_change(lambda: calls.append(1))
    try:
        _add(factory)
        _add(factory)  # a repeat doesn't call it
        ids = [a.id for a in app_alerts.recent(factory)]
        app_alerts.mark_read(factory, ids, NOW)
        app_alerts.mark_read(factory, ids, NOW)  # nothing to mark
        assert len(calls) == 2

        def boom():
            raise RuntimeError("x")

        app_alerts.set_on_change(boom)
        assert _add(factory, key="k2") is True
    finally:
        app_alerts.set_on_change(None)


def test_a_notify_failure_never_fails_add(factory, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(notify, "send_soon", boom)
    assert _add(factory) is True
    assert app_alerts.unread_count(factory) == 1


def test_mark_read_marks_only_the_alerts_given(factory, monkeypatch):
    monkeypatch.setattr(notify, "send_soon", lambda *a, **k: None)
    _add(factory, key="k1")
    _add(factory, key="k2")
    first = app_alerts.recent(factory, limit=1)[0].id
    assert app_alerts.mark_read(factory, [first], NOW) == 1
    assert app_alerts.unread_count(factory) == 1
    assert app_alerts.mark_read(factory, [first], NOW) == 0  # already read
    assert app_alerts.mark_read(factory, [], NOW) == 0
