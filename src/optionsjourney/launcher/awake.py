"""Keeping the computer awake during market hours, so syncs (and later, bots)
keep running: `caffeinate` on Mac, SetThreadExecutionState on Windows."""

import subprocess
import sys
from datetime import datetime, time

from optionsjourney.core.market import MARKET_TZ

# A little before the open and after the close, New York time
AWAKE_FROM, AWAKE_UNTIL = time(9, 15), time(16, 15)


def in_market_hours(now: datetime) -> bool:
    local = now.astimezone(MARKET_TZ)
    return local.weekday() < 5 and AWAKE_FROM <= local.time() <= AWAKE_UNTIL


class Awake:
    """Holds the computer awake while `hold(True)`; lets it sleep after."""

    def __init__(self):
        self._caffeinate: subprocess.Popen | None = None

    def hold(self, awake: bool) -> None:
        if sys.platform == "win32":
            import ctypes  # noqa: PLC0415 -- Windows only

            es_continuous, es_system_required = 0x80000000, 0x00000001
            flags = es_continuous | (es_system_required if awake else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
            return
        if awake and self._caffeinate is None:
            # -i: no idle sleep; ends by itself if this app quits (-w)
            import os  # noqa: PLC0415

            self._caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        elif not awake and self._caffeinate is not None:
            self._caffeinate.terminate()
            self._caffeinate = None
