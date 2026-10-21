import plistlib
from datetime import UTC, datetime

from optionsjourney.core.alerts import Alert
from optionsjourney.launcher.awake import in_market_hours
from optionsjourney.launcher.login_item import MacLoginItem
from optionsjourney.launcher.menu import status_lines


def test_mac_login_item_writes_and_removes_a_launch_agent(tmp_path):
    item = MacLoginItem(
        home=tmp_path,
        command=["/path/python", "-m", "optionsjourney.launcher"],
        working_dir=tmp_path,
    )
    assert not item.is_enabled()
    item.enable()
    plist = tmp_path / "Library" / "LaunchAgents" / "com.optionsjourney.app.plist"
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == "com.optionsjourney.app"
    assert data["ProgramArguments"] == ["/path/python", "-m", "optionsjourney.launcher"]
    assert data["RunAtLoad"] is True
    assert data["WorkingDirectory"] == str(tmp_path)
    assert item.is_enabled()
    item.disable()
    assert not plist.exists() and not item.is_enabled()


def test_market_hours_are_weekdays_new_york_time_with_a_margin():
    assert in_market_hours(datetime(2026, 10, 9, 13, 20, tzinfo=UTC))  # Fri 09:20 ET
    assert in_market_hours(datetime(2026, 10, 9, 20, 10, tzinfo=UTC))  # Fri 16:10 ET
    assert not in_market_hours(datetime(2026, 10, 9, 13, 0, tzinfo=UTC))  # 09:00 ET
    assert not in_market_hours(datetime(2026, 10, 9, 20, 30, tzinfo=UTC))  # 16:30 ET
    assert not in_market_hours(datetime(2026, 10, 10, 15, 0, tzinfo=UTC))  # Saturday


def test_status_lines():
    assert status_lines([]) == ["● Running"]
    alerts = [
        Alert("problem", "Schwab needs you to log in again", "", "/settings#brokers", "Reconnect")
    ]
    assert status_lines(alerts) == ["● Running", "⚠ Schwab needs you to log in again"]
    many = [Alert("warning", f"Alert {n}", "", "/alerts", "Look") for n in range(5)]
    assert status_lines(many)[-1] == "⚠ …and 2 more (open Alerts)"
