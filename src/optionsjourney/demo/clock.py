"""The demo clock: moves the whole process to the demo day.

Uses time-machine, so every ``datetime.now()`` / ``time.time()`` in the
process sees demo time, which keeps ticking at normal speed. One clock is
active per process.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import time_machine

NY = ZoneInfo("America/New_York")
DEMO_DAY = date(2026, 10, 21)
DEMO_START = time(9, 40)
CLOSE = time(16, 0)

_travel: Any = None  # the time_machine.travel object (has .stop)
_traveller: Any = None  # time_machine's Traveller (has .shift / .stop)


class DemoClock:
    def __init__(self) -> None:
        self.listeners: list[Callable[[datetime, datetime], None]] = []

    @classmethod
    def start(cls, at: datetime | None = None) -> DemoClock:
        """Travel the process to ``at`` (default: the demo morning)."""
        global _traveller, _travel
        cls.stop()
        if at is None:
            at = datetime.combine(DEMO_DAY, DEMO_START, tzinfo=NY)
        _travel = time_machine.travel(at, tick=True)
        _traveller = _travel.start()  # has .shift; stopping goes via the travel
        return cls()

    @classmethod
    def stop(cls) -> None:
        """Back to the real clock. Only tests need this."""
        global _traveller, _travel
        if _travel is not None:
            _travel.stop()
        _travel = _traveller = None

    def now(self) -> datetime:
        return datetime.now(UTC)

    def _close_at(self) -> datetime:
        return datetime.combine(DEMO_DAY, CLOSE, tzinfo=NY).astimezone(UTC)

    def is_over(self) -> bool:
        return self.now() >= self._close_at()

    def _jump(self, delta: timedelta) -> datetime:
        before = self.now()
        delta = min(delta, self._close_at() - before)
        if delta <= timedelta(0):
            # Already at/after the close (the clock keeps ticking): nothing moves.
            return self._close_at()
        _traveller.shift(delta)
        after = self.now()
        for listener in list(self.listeners):
            listener(before, after)
        return after

    def skip(self, minutes: int) -> datetime:
        return self._jump(timedelta(minutes=minutes))

    def to_close(self) -> datetime:
        return self._jump(self._close_at() - self.now())
