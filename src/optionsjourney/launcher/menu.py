"""What the menu shows about the app's state."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from optionsjourney.core.alerts import Alert
from optionsjourney.core.versions import is_newer
from optionsjourney.services import updates

logger = logging.getLogger(__name__)
MAX_ALERTS = 3


def status_lines(alerts: list[Alert], version: str) -> list[str]:
    """The menu's top lines: running and the version (for "which version?"), then alerts."""
    lines = [f"● Running · v{version}"]
    lines += [f"⚠ {a.title}" for a in alerts[:MAX_ALERTS]]
    if len(alerts) > MAX_ALERTS:
        lines.append(f"⚠ …and {len(alerts) - MAX_ALERTS} more (open Alerts)")
    return lines


def _name(tag: str) -> str:
    return "v" + tag.removeprefix("v")


def update_item(state: dict) -> tuple[str, bool] | None:
    """The menu entry for an update: (label, clickable), or None for no entry.
    A finished download is shown while it is still newer than the running
    version, even with the daily check off (the user chose to download it)."""
    running = state["running"]
    ready = state.get("ready")
    if ready is not None and is_newer(ready[0].tag, running):
        return f"Install {_name(ready[0].tag)}…", True
    release = state.get("latest")
    if release is None or not state.get("enabled", True) or not is_newer(release.tag, running):
        return None
    if state.get("downloading"):
        return f"Downloading {_name(release.tag)}…", False
    return f"Download {_name(release.tag)}", True


def check_once(state: dict, http, enabled: bool) -> None:
    """Ask GitHub for a newer release, unless the check is switched off."""
    if enabled:
        state["latest"] = updates.latest(http, state["running"])


def start_download(
    state: dict, updates_dir: Path, http, done: Callable[[], None]
) -> threading.Thread | None:
    """Download the known release on a thread, then call `done`. None if there is
    nothing to download or one is already running."""
    release = state.get("latest")
    if release is None or state.get("downloading"):
        return None
    state["downloading"] = True

    def work():
        try:
            updates.download(release, updates_dir, http)
        except updates.UpdateError:
            pass  # the service has logged why; the item goes back to Download
        except Exception:  # noqa: BLE001 -- never take the menu down
            logger.exception("update download failed")
        finally:
            state["downloading"] = False
            state["ready"] = updates.ready(updates_dir)
            done()

    thread = threading.Thread(target=work, name="update-download", daemon=True)
    thread.start()
    return thread
