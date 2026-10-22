"""The menu bar app: serves OptionsJourney in the background and shows a
status icon with its alerts (docs/decisions/0008)."""

import logging
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from werkzeug.serving import make_server

from optionsjourney import logs
from optionsjourney.config import HOST, ConfigError, load_settings
from optionsjourney.launcher import places
from optionsjourney.launcher.awake import Awake, in_market_hours
from optionsjourney.launcher.login_item import launch_command, login_item
from optionsjourney.launcher.menu import (
    check_once,
    start_download,
    status_lines,
    update_item,
)

logger = logging.getLogger(__name__)
REFRESH_EVERY = 60  # seconds between status checks
CHECK_EVERY = 24 * 60 * 60  # seconds between looks for a newer release


def _on_main_thread(fn) -> None:
    """Run a change to the icon or menu on the UI thread. macOS kills an app
    (SIGTRAP) that touches the menu bar from any other thread."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper  # noqa: PLC0415 -- macOS only

        AppHelper.callAfter(fn)
    else:
        fn()


ICON = Path(__file__).parent.parent / "web" / "static" / "images" / "tray-icon.png"


def _icon_image() -> Image.Image:
    """The logo on a dark tile cut close around it (decision 0096): the mark fills the
    icon, and the tile keeps it visible on light and dark menu bars."""
    return Image.open(ICON).convert("RGBA")


def main() -> None:
    logs.start_console()
    from optionsjourney.services.economic_events import quiet_http_logs  # noqa: PLC0415

    quiet_http_logs()  # httpx logs request URLs, FRED's key included
    try:
        settings = load_settings()
    except ConfigError as e:
        sys.exit(f"Settings problem: {e}")
    logs.start_file(settings.data_dir)

    try:
        import pystray  # noqa: PLC0415 -- only the menu bar app needs it

        from optionsjourney.launcher import startup_problem  # noqa: PLC0415
    except Exception:  # noqa: BLE001 -- without these there is no icon to show
        logger.exception("OptionsJourney couldn't start, and couldn't show its icon")
        sys.exit("OptionsJourney couldn't start: see the log.")

    server = None
    web = None
    try:
        import httpx  # noqa: PLC0415

        from optionsjourney import __version__  # noqa: PLC0415
        from optionsjourney.app import create_app  # noqa: PLC0415
        from optionsjourney.services import background, updates  # noqa: PLC0415
        from optionsjourney.services import settings as saved  # noqa: PLC0415
        from optionsjourney.services.alerts import system_alerts  # noqa: PLC0415

        app = create_app(settings)
        app.config["MENU_APP"] = True  # only the menu bar app can run the installer
        services = app.extensions["optionsjourney"]
        url = f"http://{HOST}:{settings.port}"
        server = make_server(settings.bind_host, settings.port, app, threaded=True)
        web = threading.Thread(target=server.serve_forever, name="web", daemon=True)
        web.start()

        login = login_item()
        awake = Awake()
        updates_dir = settings.data_dir / "updates"
        updates.remove_installed(updates_dir, __version__)
        http = httpx.Client(timeout=updates.TIMEOUT_S)
        state = {
            "alerts": [],
            "running": __version__,
            "latest": None,
            "downloading": False,
            "ready": updates.ready(updates_dir),
            "enabled": True,
        }
        # Last: the bots start only once everything else has
        background.start_all(settings.database_path, services)
    except Exception as e:  # noqa: BLE001 -- not BaseException: Ctrl-C still stops it
        # Keep an icon and say what went wrong (NewerDataError, UpgradeBackupError, a
        # port in use or anything else) instead of dying silently
        logger.exception("OptionsJourney couldn't start")
        if web is not None:
            server.shutdown()
        if server is not None:
            server.server_close()  # the problem page needs the port
        startup_problem.run_problem_icon(settings, startup_problem.kind_of(e), str(e))
        return
    logger.info("OptionsJourney is running at %s", url)

    def keep_awake_on() -> bool:
        return saved.get(services.session_factory, "keep_awake") == "on"

    def toggle_keep_awake(icon, item):
        value = "off" if keep_awake_on() else "on"
        saved.save_section(services.session_factory, "computer", {"keep_awake": value})
        refresh()

    def toggle_login(icon, item):
        login.disable() if login.is_enabled() else login.enable()

    def checking() -> bool:
        return saved.get(services.session_factory, "check_updates") != "off"

    def download_update(icon, item):
        start_download(state, updates_dir, http, refresh)
        refresh()

    def quit_app(icon, item):
        from optionsjourney.services import notify  # noqa: PLC0415

        awake.hold(False)
        http.close()
        icon.stop()
        notify.wait_idle(5)  # alerts still queued get a few seconds to go out
        server.shutdown()

    def restart(icon, item):
        """Quits, then starts a fresh copy once the port is free (decision 0094)."""
        logger.info("Restarting from the menu")
        quit_app(icon, item)
        server.server_close()
        places.start_again(launch_command(), places.app_folder())

    def items():
        for line in status_lines(state["alerts"], __version__):
            yield pystray.MenuItem(line, None, enabled=False)
        offer = update_item(state)
        if offer:
            label, clickable = offer
            if label.startswith("Install "):
                yield pystray.MenuItem(label, lambda: webbrowser.open(f"{url}/update"))
            else:
                yield pystray.MenuItem(label, download_update, enabled=clickable)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Open OptionsJourney", lambda: webbrowser.open(url), default=True)
        if state["alerts"]:
            yield pystray.MenuItem("Open Alerts", lambda: webbrowser.open(f"{url}/alerts"))
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem(
            "Start at login", toggle_login, checked=lambda item: login.is_enabled()
        )
        yield pystray.MenuItem(
            "Keep awake during market hours",
            toggle_keep_awake,
            checked=lambda item: keep_awake_on(),
        )
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem(
            f"Show app in {'Finder' if sys.platform == 'darwin' else 'Explorer'}",
            lambda: places.open_folder(places.app_folder()),
        )
        yield pystray.MenuItem(
            "Open logs", lambda: places.open_folder(logs.log_dir(settings.data_dir))
        )
        yield pystray.MenuItem("Restart OptionsJourney", restart)
        yield pystray.MenuItem("Quit OptionsJourney", quit_app)

    icon = pystray.Icon("OptionsJourney", _icon_image(), "OptionsJourney", pystray.Menu(items))

    def refresh():
        state["enabled"] = checking()
        try:
            state["alerts"] = system_alerts(
                services.session_factory, credentials=services.credentials
            )
        except Exception:  # noqa: BLE001 -- the menu must keep working
            logger.exception("couldn't read alerts")
        awake.hold(keep_awake_on() and in_market_hours(datetime.now(UTC)))
        count = len(state["alerts"])
        title = (
            f"OptionsJourney · {count} alert{'s' if count != 1 else ''}"
            if count
            else "OptionsJourney"
        )

        def show():
            icon.title = title
            icon.update_menu()

        _on_main_thread(show)

    def watch():
        while True:
            refresh()
            time.sleep(REFRESH_EVERY)

    def check_daily():
        while True:
            try:
                check_once(state, http, checking())
                state["ready"] = updates.ready(updates_dir)
                refresh()
            except Exception:  # noqa: BLE001 -- the menu must keep working
                logger.exception("update check failed")
            time.sleep(CHECK_EVERY)

    def setup(icon):
        icon.visible = True
        threading.Thread(target=check_daily, name="update-check", daemon=True).start()
        threading.Thread(target=watch, name="status", daemon=True).start()
        webbrowser.open(url)

    icon.run(setup=setup)


if __name__ == "__main__":
    main()
