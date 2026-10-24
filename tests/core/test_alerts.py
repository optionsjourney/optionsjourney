from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from optionsjourney.core.alerts import backup_alert, order_alerts, sync_alerts

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)


class State:
    def __init__(self, broker, number, synced_through, last_error=None):
        self.broker, self.account_number = broker, number
        self.synced_through, self.last_error = synced_through, last_error
        self.last_attempt_at = synced_through or NOW


def test_a_failed_sync_is_an_alert():
    [alert] = sync_alerts([State("tastytrade", "5WX01234", NOW, "TastyTrade answered 502.")], NOW)
    assert alert.level == "problem"
    assert "••1234" in alert.title and "502" in alert.detail
    assert alert.link == "/transactions"


def test_a_sync_more_than_3_days_old_is_an_alert():
    states = [State("schwab", "12349999", NOW - timedelta(days=4))]
    [alert] = sync_alerts(states, NOW)
    assert alert.level == "warning" and "4 days" in alert.title


def test_recent_syncs_are_fine():
    assert sync_alerts([State("schwab", "12349999", NOW - timedelta(days=2))], NOW) == []


def test_one_stale_alert_per_broker_not_per_account():
    old = NOW - timedelta(days=10)
    states = [State("tastytrade", "5WX01234", old), State("tastytrade", "5WY05678", old)]
    assert len(sync_alerts(states, NOW)) == 1


def test_a_broker_that_isnt_connected_raises_nothing():
    """#273: a broker the user never connected, or disconnected, isn't stale or failing."""
    old = NOW - timedelta(days=4)
    states = [
        State("schwab", "12349999", old),
        State("schwab", "12348888", old, "Schwab needs you to log in again."),
    ]
    assert sync_alerts(states, NOW, connected=lambda broker: broker != "schwab") == []
    assert len(sync_alerts(states, NOW, connected=lambda broker: True)) == 2


def test_an_account_the_broker_stopped_returning_raises_nothing():
    """#273: a closed account drops out of the broker's syncs while the others go on."""
    closed = State("tastytrade", "5WX01234", NOW - timedelta(days=30), "Account not found.")
    closed.last_attempt_at = NOW - timedelta(days=30)
    assert sync_alerts([closed, State("tastytrade", "5WY05678", NOW)], NOW) == []


def test_a_broker_that_stopped_syncing_altogether_is_still_a_note():
    old = NOW - timedelta(days=5)
    states = [State("tastytrade", "5WX01234", old), State("tastytrade", "5WY05678", old)]
    [alert] = sync_alerts(states, NOW, connected=lambda broker: True)
    assert alert.level == "warning" and "5 days" in alert.title


def test_backup_problem():
    alert = backup_alert((NOW, "The backup folder /Volumes/USB can't be used: not found"))
    assert alert.level == "problem" and "USB" in alert.detail and alert.link == "/settings#backups"
    assert backup_alert(None) is None


def test_schwab_login_reminder_a_day_before():
    from optionsjourney.core.alerts import schwab_login_alert

    assert schwab_login_alert(None, NOW) is None
    assert schwab_login_alert(NOW + timedelta(days=2), NOW) is None
    soon = schwab_login_alert(NOW + timedelta(hours=5), NOW)
    assert soon.level == "warning" and "5 hours" in soon.title
    gone = schwab_login_alert(NOW - timedelta(minutes=1), NOW)
    assert gone.level == "problem" and gone.action == "Reconnect Schwab"


def test_schwab_reminder_levels_48_and_12_hours_then_run_out():
    """Tracker2's token alerts at 48 h and 12 h left (bots slice 11), and once it's out."""
    from optionsjourney.core.alerts import schwab_reminder

    assert schwab_reminder(None, NOW) is None
    assert schwab_reminder(NOW + timedelta(hours=49), NOW) is None
    assert schwab_reminder(NOW + timedelta(hours=48), NOW) == "48h"
    assert schwab_reminder(NOW + timedelta(hours=13), NOW) == "48h"
    assert schwab_reminder(NOW + timedelta(hours=12), NOW) == "12h"
    assert schwab_reminder(NOW + timedelta(minutes=1), NOW) == "12h"
    assert schwab_reminder(NOW, NOW) == "expired"
    assert schwab_reminder(NOW - timedelta(days=3), NOW) == "expired"


def test_order_alerts():
    unclear = SimpleNamespace(broker="tastytrade", account_number="5WX01234",
                              result="Unclear: Lost the connection")  # fmt: skip
    refused = SimpleNamespace(broker="schwab", account_number="12345678",
                              result="Couldn't check your open positions …")  # fmt: skip
    alerts = order_alerts([unclear], [refused, refused])
    assert [a.level for a in alerts] == ["problem", "problem"]
    assert "unclear" in alerts[0].title and "••1234" in alerts[0].title
    assert "wasn't sent" in alerts[1].title and alerts[1].link == "/orders"
