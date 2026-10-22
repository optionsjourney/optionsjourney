import plistlib
from datetime import UTC, datetime

import pytest

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
    assert status_lines([], "0.1.2") == ["● Running · v0.1.2"]
    alerts = [
        Alert("problem", "Schwab needs you to log in again", "", "/settings#brokers", "Reconnect")
    ]
    assert status_lines(alerts, "0.1.2") == [
        "● Running · v0.1.2",
        "⚠ Schwab needs you to log in again",
    ]
    many = [Alert("warning", f"Alert {n}", "", "/alerts", "Look") for n in range(5)]
    assert status_lines(many, "0.1.2")[-1] == "⚠ …and 2 more (open Alerts)"


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


def test_menu_bar_icon_is_the_logo_filling_the_icon():
    from optionsjourney.launcher.app import _icon_image  # noqa: PLC0415

    image = _icon_image()
    assert image.size == (64, 64) and image.mode == "RGBA"
    # The tile is cut close, so the green ring nearly spans the icon (on the app icon's
    # wide tile it spans about two thirds)
    green = [x for x in range(64) for y in range(64) if image.getpixel((x, y))[:3] == (89, 184, 44)]
    assert min(green) <= 5 and max(green) >= 58


def _rel(tag="v0.2.1"):
    from optionsjourney.services.updates import Release

    return Release(tag=tag, page_url="https://github.com/x", zip_url="https://github.com/z")


def test_update_item_four_states(tmp_path):
    from optionsjourney.launcher.menu import update_item

    r = _rel()
    base = {"running": "0.2.0", "latest": None, "downloading": False, "ready": None}
    assert update_item(base) is None
    assert update_item({**base, "latest": r}) == ("Download v0.2.1", True)
    assert update_item({**base, "latest": r, "downloading": True}) == ("Downloading v0.2.1…", False)
    assert update_item({**base, "latest": r, "ready": (r, tmp_path)}) == ("Install v0.2.1…", True)


def test_update_item_ignores_a_stale_ready_download(tmp_path):
    from optionsjourney.launcher.menu import update_item

    old = _rel("v0.2.0")
    state = {"running": "0.2.0", "latest": None, "downloading": False, "ready": (old, tmp_path)}
    assert update_item(state) is None
    assert update_item({**state, "latest": _rel()}) == ("Download v0.2.1", True)


def test_update_item_check_off_hides_download_but_keeps_install(tmp_path):
    from optionsjourney.launcher.menu import update_item

    r = _rel()
    base = {"running": "0.2.0", "latest": r, "downloading": False, "ready": None, "enabled": False}
    assert update_item(base) is None
    assert update_item({**base, "ready": (r, tmp_path)}) == ("Install v0.2.1…", True)


def test_check_once_makes_no_request_when_off(monkeypatch):
    from optionsjourney.launcher import menu

    monkeypatch.setattr(menu.updates, "latest", lambda *a: pytest.fail("asked GitHub"))
    state = {"running": "0.2.0", "latest": None}
    menu.check_once(state, object(), enabled=False)
    assert state["latest"] is None


def test_check_once_records_the_release(monkeypatch):
    from optionsjourney.launcher import menu

    r = _rel()
    monkeypatch.setattr(menu.updates, "latest", lambda http, running: r)
    state = {"running": "0.2.0", "latest": None}
    menu.check_once(state, object(), enabled=True)
    assert state["latest"] == r


def test_download_runs_once_and_reports(monkeypatch, tmp_path):
    import threading

    from optionsjourney.launcher import menu
    from optionsjourney.services.updates import UpdateError

    r = _rel()
    calls = []
    gate = threading.Event()

    def fake(release, updates_dir, http):
        calls.append(release)
        gate.wait(5)
        raise UpdateError("nope")

    monkeypatch.setattr(menu.updates, "download", fake)
    monkeypatch.setattr(menu.updates, "ready", lambda d: None)
    state = {"running": "0.2.0", "latest": r, "downloading": False, "ready": None}
    done = threading.Event()
    t = menu.start_download(state, tmp_path, object(), done.set)
    assert state["downloading"] is True
    assert menu.start_download(state, tmp_path, object(), done.set) is None  # second click
    gate.set()
    t.join(5)
    assert done.is_set() and state["downloading"] is False and len(calls) == 1
    assert state["ready"] is None
