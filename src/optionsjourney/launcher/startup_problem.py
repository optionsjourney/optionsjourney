"""When the menu bar app can't start: an OJ icon that still offers Open logs, Restart
and Quit, and a small page in the browser saying what went wrong. When this version
finds data a newer version upgraded, the page offers to restore the backup made just
before that upgrade, then starts the app again."""

import logging
import threading
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import jinja2
from werkzeug.exceptions import Forbidden, NotFound
from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response

from optionsjourney import __version__, logs
from optionsjourney.config import HOST, Settings
from optionsjourney.db import make_engine, make_session_factory
from optionsjourney.launcher import places
from optionsjourney.launcher.app import _icon_image, _on_main_thread
from optionsjourney.launcher.login_item import launch_command
from optionsjourney.services import backups
from optionsjourney.web.formatting import market_time
from optionsjourney.web.security import same_origin

logger = logging.getLogger(__name__)

Kind = Literal["newer", "backup", "other"]
_TEMPLATES = Path(__file__).parent.parent / "web" / "templates"
_pages = jinja2.Environment(loader=jinja2.FileSystemLoader(_TEMPLATES), autoescape=True)


def kind_of(error: Exception) -> Kind:
    try:
        # Here, not at the top: this page must still show when the app itself
        # can't be imported
        from optionsjourney.app import NewerDataError, UpgradeBackupError  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return "other"
    if isinstance(error, NewerDataError):
        return "newer"
    if isinstance(error, UpgradeBackupError):
        return "backup"
    return "other"


def _page(kind: Kind, detail: str, restore: backups.Backup | None, **extra) -> str:
    return _pages.get_template("startup_problem.html").render(
        kind=kind,
        detail=detail.rstrip("."),  # the page ends the sentence itself
        restore=restore,
        restore_time=market_time(restore.made_at) if restore else "",
        log_name=logs.LOG_NAME,
        **extra,
    )


def problem_app(
    kind: Kind,
    detail: str,
    restore: backups.Backup | None,
    on_restore: Callable[[], None],
):
    """The problem page at every address, and POST /restore when there is
    something to restore. Restores at most once."""
    lock = threading.Lock()
    done = []

    def html(body: str, status: int = 200) -> Response:
        return Response(body, status=status, content_type="text/html; charset=utf-8")

    @Request.application
    def app(request: Request) -> Response:
        if request.path == "/restore":
            if request.method != "POST" or kind != "newer" or restore is None:
                return NotFound()
            if not same_origin(request.headers, request.host):
                return Forbidden()  # another website can't press the button
            with lock:
                if not done:
                    try:
                        on_restore()
                    except Exception as e:  # noqa: BLE001 -- shown on the page
                        logger.exception("couldn't restore the backup from before the upgrade")
                        return html(_page(kind, detail, restore, restore_error=str(e)), 500)
                    done.append(True)
            return html(_page(kind, detail, restore, restoring=True))
        return html(_page(kind, detail, restore))

    return app


def _bare_factory(settings: Settings):
    """A session factory on the data as it is: no upgrade, no version recorded."""
    engine = make_engine(settings.database_path)
    return engine, make_session_factory(engine)


def find_restore_point(settings: Settings) -> backups.Backup | None:
    """None if there isn't one, or if the data or backup folder can't be read: the
    page must still show."""
    try:
        engine, factory = _bare_factory(settings)
    except Exception:  # noqa: BLE001 -- never die without an icon
        logger.exception("couldn't open the data to look for a restore point")
        return None
    try:
        return backups.restore_point(factory, __version__)
    except Exception:  # noqa: BLE001 -- never die without an icon
        logger.exception("couldn't look for the backup from before the upgrade")
        return None
    finally:
        engine.dispose()


def restart(stop: Callable[[], None]) -> None:
    """Quit (freeing the port), then start a fresh copy (decision 0094)."""
    stop()
    places.start_again(launch_command(), places.app_folder())


def restore_and_restart(
    settings: Settings,
    stop: Callable[[], None],
    later: Callable[[Callable[[], None]], None] | None = None,
) -> Callable[[], None]:
    """What the restore button does: restore the backup from before the upgrade
    (raises BackupError), then restart once the page has answered."""

    def in_a_moment(fn: Callable[[], None]) -> None:
        threading.Timer(1.0, fn).start()  # lets the "Restoring…" page reach the browser

    def on_restore() -> None:
        engine, factory = _bare_factory(settings)
        try:
            backups.restore_before_upgrade(
                settings.database_path, factory, __version__, datetime.now(UTC)
            )
        finally:
            engine.dispose()
        logger.info("Restored the backup from before the upgrade; restarting")
        (later or in_a_moment)(lambda: restart(stop))

    return on_restore


def run_problem_icon(settings: Settings, kind: Kind, detail: str) -> None:
    """The OJ icon with Open logs, Restart and Quit only, and the problem page
    served on the app's own port and opened in the browser."""
    import pystray  # noqa: PLC0415 -- only the menu bar app needs it

    url = f"http://{HOST}:{settings.port}"
    icon = None
    server = None

    def stop() -> None:
        """Called from the menu, or from a timer after a restore."""
        if icon is not None:
            _on_main_thread(icon.stop)
        if server is not None:
            server.shutdown()
            server.server_close()

    restore = find_restore_point(settings) if kind == "newer" else None
    page = problem_app(kind, detail, restore, restore_and_restart(settings, stop))
    try:
        server = make_server(settings.bind_host, settings.port, page, threaded=True)
    except OSError as e:
        logger.error(  # noqa: TRY400 -- the reason is enough
            "Showing the icon without the start-up problem page: port %s is in use "
            "(is OptionsJourney already running?): %s",
            settings.port,
            e,
        )
    else:
        threading.Thread(target=server.serve_forever, name="web", daemon=True).start()

    def restart_item(icon, item):
        logger.info("Restarting from the menu")
        restart(stop)

    icon = pystray.Icon(
        "OptionsJourney",
        _icon_image(),
        "OptionsJourney · couldn't start",
        pystray.Menu(
            pystray.MenuItem(
                "Open logs", lambda: places.open_folder(logs.log_dir(settings.data_dir))
            ),
            pystray.MenuItem("Restart OptionsJourney", restart_item),
            pystray.MenuItem("Quit OptionsJourney", lambda icon, item: stop()),
        ),
    )

    def setup(icon):
        icon.visible = True
        if server is not None:
            webbrowser.open(url)

    icon.run(setup=setup)
