"""The menu bar app: serves OptionsJourney in the background and shows a
status icon with its alerts (docs/decisions/0008)."""

import logging
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime

from PIL import Image, ImageDraw
from werkzeug.serving import make_server

from optionsjourney.config import HOST, ConfigError, load_settings
from optionsjourney.launcher.awake import Awake, in_market_hours
from optionsjourney.launcher.login_item import login_item
from optionsjourney.launcher.menu import status_lines

logger = logging.getLogger(__name__)
REFRESH_EVERY = 60  # seconds between status checks


def _on_main_thread(fn) -> None:
    """Run a change to the icon or menu on the UI thread. macOS kills an app
    (SIGTRAP) that touches the menu bar from any other thread."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper  # noqa: PLC0415 -- macOS only

        AppHelper.callAfter(fn)
    else:
        fn()


def _icon_image() -> Image.Image:
    """A simple round badge; menu bars show it small."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 60, 60), fill=(37, 99, 235, 255))
    draw.text((18, 22), "OJ", fill=(255, 255, 255, 255))
    return image


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from optionsjourney.services.economic_events import quiet_http_logs  # noqa: PLC0415

    quiet_http_logs()  # httpx logs request URLs, FRED's key included
    try:
        settings = load_settings()
    except ConfigError as e:
        sys.exit(f"Settings problem: {e}")

    import pystray  # noqa: PLC0415 -- only the menu bar app needs it

    from optionsjourney.app import create_app  # noqa: PLC0415
    from optionsjourney.services import (  # noqa: PLC0415
        backups,
        bot_loops,
        expiry_settle,
        pl_day,
        stock_history,
    )
    from optionsjourney.services import settings as saved  # noqa: PLC0415
    from optionsjourney.services.alerts import system_alerts  # noqa: PLC0415
    from optionsjourney.services.user_alerts import start_alert_checker  # noqa: PLC0415

    app = create_app(settings)
    services = app.extensions["optionsjourney"]
    url = f"http://{HOST}:{settings.port}"
    server = make_server(settings.bind_host, settings.port, app, threaded=True)
    threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
    backups.start_scheduler(settings.database_path, services.session_factory)
    start_alert_checker(services)
    pl_day.start(services)
    stock_history.start(services)
    expiry_settle.start(services)
    bot_loops.start(services)
    logger.info("OptionsJourney is running at %s", url)

    login = login_item()
    awake = Awake()
    state = {"alerts": []}

    def keep_awake_on() -> bool:
        return saved.get(services.session_factory, "keep_awake") == "on"

    def toggle_keep_awake(icon, item):
        value = "off" if keep_awake_on() else "on"
        saved.save_section(services.session_factory, "computer", {"keep_awake": value})
        refresh()

    def toggle_login(icon, item):
        login.disable() if login.is_enabled() else login.enable()

    def quit_app(icon, item):
        from optionsjourney.services import notify  # noqa: PLC0415

        awake.hold(False)
        icon.stop()
        notify.wait_idle(5)  # alerts still queued get a few seconds to go out
        server.shutdown()

    def items():
        for line in status_lines(state["alerts"]):
            yield pystray.MenuItem(line, None, enabled=False)
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
        yield pystray.MenuItem("Quit OptionsJourney", quit_app)

    icon = pystray.Icon("OptionsJourney", _icon_image(), "OptionsJourney", pystray.Menu(items))

    def refresh():
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

    def setup(icon):
        icon.visible = True
        threading.Thread(target=watch, name="status", daemon=True).start()
        webbrowser.open(url)

    icon.run(setup=setup)


if __name__ == "__main__":
    main()
