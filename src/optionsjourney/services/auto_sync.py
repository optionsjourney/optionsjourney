"""The journal syncs on its own (#171): every so often, as set on the Settings page
(sync_market_hours, sync_other_times; core/auto_sync.py says when one is due), the same
sync as Sync now. Runs in the app process (__main__), never in tests."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from optionsjourney.core import auto_sync
from optionsjourney.services import settings
from optionsjourney.services.sync import sync_all

logger = logging.getLogger(__name__)

CHECK_EVERY = 15  # seconds between looks at whether a sync is due


def run_if_due(services, last: datetime | None, now: datetime) -> datetime | None:
    """Sync if one is due; when the last one ran (now, if this one did)."""
    every = auto_sync.interval(
        now,
        settings.get(services.session_factory, "sync_market_hours"),
        settings.get(services.session_factory, "sync_other_times"),
    )
    if not auto_sync.due(last, now, every):
        return last
    for result in sync_all(services.broker_clients(), services.session_factory):
        if result.ok:
            if result.new or result.updated:
                logger.info(
                    "auto-sync %s: %s new, %s updated", result.broker, result.new, result.updated
                )
        else:
            logger.info("auto-sync %s: %s", result.broker, "; ".join(result.problems or []))
    return now


def start(services, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> threading.Thread:
    def loop():
        last = None
        while True:
            try:
                last = run_if_due(services, last, clock())
            except Exception:  # noqa: BLE001 -- never let the loop die
                logger.exception("auto-sync crashed")
                last = clock()  # wait a full interval before trying again
            time.sleep(CHECK_EVERY)

    thread = threading.Thread(target=loop, name="auto-sync", daemon=True)
    thread.start()
    return thread
