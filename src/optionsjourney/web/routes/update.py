"""Install a downloaded update from the menu bar app's "Install vX..." item.

The page lists what is live right now, makes you tick a box for each, and only
then starts the installer. Only the menu bar app in local mode serves it.
"""

import logging
import threading
from datetime import UTC, datetime

from flask import Blueprint, abort, current_app, render_template, request

from optionsjourney import logs
from optionsjourney.brokers.credentials.local import LocalProvider
from optionsjourney.services import update_risks, updates
from optionsjourney.web import services
from optionsjourney.web.routes.demo import not_in_demo

logger = logging.getLogger(__name__)
bp = Blueprint("update", __name__)

_start_lock = threading.Lock()
_started = False  # one installer per run of the app: a double submit must not start two


def _ready():
    s = services()
    if not current_app.config.get("MENU_APP") or not isinstance(s.credentials, LocalProvider):
        abort(404)
    found = updates.ready(s.settings.data_dir / "updates")
    if found is None:
        abort(404)
    return found


def _page(release, risks, *, ticked=(), confirmed=False, started=False, error=None, status=200):
    s = services()
    return render_template(
        "update.html",
        release=release,
        version=release.tag.removeprefix("v"),
        risks=risks,
        ticked=set(ticked),
        confirmed=confirmed,
        started=started,
        error=error,
        log_path=logs.log_dir(s.settings.data_dir) / updates.UPDATE_LOG,
    ), status


@bp.get("/update")
def page():
    release, _ = _ready()
    return _page(release, update_risks.live_risks(services().session_factory, datetime.now(UTC)))


@bp.post("/update/install")
@not_in_demo
def install():
    release, source = _ready()
    s = services()
    risks = update_risks.live_risks(s.session_factory, datetime.now(UTC))
    ticked = {r.key for r in risks if request.form.get(f"risk_{r.key}") == str(r.count)}
    if request.form.get("confirm") != "1" or len(ticked) != len(risks):
        return _page(release, risks, error="Tick every box first.", status=400)
    global _started  # noqa: PLW0603
    with _start_lock:
        if not _started:
            log_path = logs.log_dir(s.settings.data_dir) / updates.UPDATE_LOG
            try:
                updates.start_installer(source, log_path)
            except OSError as e:
                logger.warning("Couldn't start the installer: %s", e)
                return _page(
                    release, risks, ticked=ticked, confirmed=True, status=500,
                    error=f"Couldn't start the installer: {e}. Your app is still running.",
                )  # fmt: skip
            _started = True
    return _page(release, risks, ticked=ticked, confirmed=True, started=True)
