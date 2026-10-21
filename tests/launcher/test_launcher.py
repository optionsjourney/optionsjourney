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


def test_windows_starts_the_app_without_a_console_window():
    from optionsjourney.launcher.login_item import launch_command

    win = launch_command(r"C:\OJ\.venv\Scripts\python.exe", platform="win32")
    assert win[0].endswith("pythonw.exe") and win[1:] == ["-m", "optionsjourney.launcher"]
    mac = launch_command("/oj/.venv/bin/python3", platform="darwin")
    assert mac[0] == "/oj/.venv/bin/python3"


def test_the_app_folder_is_the_one_holding_the_venv(tmp_path, monkeypatch):
    from optionsjourney.launcher.places import app_folder

    (tmp_path / "pyproject.toml").write_text("")
    assert app_folder(str(tmp_path / ".venv")) == tmp_path
    monkeypatch.chdir(tmp_path)
    assert app_folder("/somewhere/else/.venv") == tmp_path  # not installed: where it runs


def test_both_ways_of_starting_run_the_same_background_jobs():
    """Decision 0094: the menu bar app had been missing auto-sync and fill sync."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "optionsjourney"
    for entry in ("__main__.py", "launcher/app.py"):
        assert "background.start_all(" in (src / entry).read_text(encoding="utf-8"), entry
