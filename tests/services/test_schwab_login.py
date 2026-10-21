"""Schwab's refresh-login reminders (bots slice 11; Tracker2's schwab_token_alerts)."""

from datetime import UTC, datetime, timedelta

from optionsjourney.services import schwab_login

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


class Login:
    def __init__(self, expires_at):
        self.expires_at = expires_at

    def schwab_login_expires_at(self):
        return self.expires_at


def run(factory, login, now, seen, bots=True):
    return schwab_login.check(factory, login, now, notify=seen.append, bots=bots)


def test_each_reminder_goes_out_once_per_login(factory):
    seen = []
    login = Login(NOW + timedelta(hours=60))
    assert run(factory, login, NOW, seen) is None and seen == []
    at_47 = NOW + timedelta(hours=13)
    assert run(factory, login, at_47, seen) == "48h"
    assert run(factory, login, at_47 + timedelta(minutes=10), seen) is None
    assert [e.title for e in seen] == ["Schwab login needs you: it runs out in 47 hours"]
    assert seen[0].link == "/settings#brokers" and "Reconnect Schwab" in seen[0].detail
    assert "watched by this app" in seen[0].detail
    assert run(factory, login, NOW + timedelta(hours=49), seen) == "12h"
    assert run(factory, login, NOW + timedelta(hours=60), seen) == "expired"
    assert run(factory, login, NOW + timedelta(hours=70), seen) is None
    assert [e.title for e in seen][1:] == [
        "Schwab login needs you: it runs out in 11 hours",
        "Schwab login needs you: it has run out",
    ]


def test_starting_late_says_only_where_it_is_now(factory):
    seen = []
    login = Login(NOW + timedelta(hours=5))
    assert run(factory, login, NOW, seen) == "12h"
    assert run(factory, login, NOW + timedelta(hours=1), seen) is None
    assert len(seen) == 1


def test_a_new_login_starts_the_reminders_again(factory):
    seen = []
    run(factory, Login(NOW + timedelta(hours=10)), NOW, seen)
    renewed = Login(NOW + timedelta(days=7))
    assert run(factory, renewed, NOW, seen) is None
    assert run(factory, renewed, NOW + timedelta(days=6), seen) == "48h"
    assert len(seen) == 2


def test_no_login_and_errors_say_nothing(factory):
    seen = []
    assert run(factory, Login(None), NOW, seen) is None
    assert run(factory, object(), NOW, seen) is None  # gateway mode has no login to time

    class Broken:
        def schwab_login_expires_at(self):
            raise RuntimeError("store unreadable")

    assert run(factory, Broken(), NOW, seen) is None and seen == []


def test_bots_are_mentioned_only_where_there_are_schwab_bots(factory):
    """Review M-1: a Schwab user with no Schwab bots hears only about syncing."""
    seen = []
    run(factory, Login(NOW + timedelta(hours=5)), NOW, seen, bots=False)
    assert "bot" not in seen[0].detail and "syncing" in seen[0].detail
