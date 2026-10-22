"""Private daily visitor counts for the public demo, read by the author at /stats.

Numbers only: visits, who had to wait and for how long, time spent, and the furthest
tour step reached. No names, IP addresses, fingerprints or third-party scripts. The
file is small JSON, written atomically after each change; losing it (a redeploy, a
broken file) just starts the counts again.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from optionsjourney.demo.tour import FINISHED  # the step after the last: finished the tour

log = logging.getLogger(__name__)

KEEP_DAYS = 90


@dataclass
class DayCounts:
    day: date
    visits: int = 0
    waited: int = 0
    wait_seconds: float = 0.0
    visit_seconds: float = 0.0
    furthest: dict[int, int] = field(default_factory=dict)  # step -> visitors; FINISHED

    @property
    def ended(self) -> int:
        return sum(self.furthest.values())

    @property
    def average_wait(self) -> float:
        return self.wait_seconds / self.waited if self.waited else 0.0

    @property
    def average_visit(self) -> float:
        return self.visit_seconds / self.ended if self.ended else 0.0

    def reached(self, step: int) -> float:
        """Share of ended visits whose furthest step was at least ``step``."""
        if not self.ended:
            return 0.0
        return sum(n for s, n in self.furthest.items() if s >= step) / self.ended


class Counts:
    """Thread-safe. Midnight rule: a visit counts on the day it starts; waits and
    visit endings count on the day they happen. Saving never raises: counts are
    disposable, so a failed write is logged and the in-memory counts carry on."""

    def __init__(self, path: Path, today: Callable[[], date]):
        self._path = Path(path)
        self._today = today
        self._lock = threading.Lock()
        self._rows: dict[str, dict] = self._load()

    def visit(self) -> None:
        with self._lock:
            self._row()["visits"] += 1
            self._save()

    def waited(self, seconds: float) -> None:
        with self._lock:
            row = self._row()
            row["waited"] += 1
            row["wait_seconds"] += max(0.0, float(seconds))
            self._save()

    def visit_ended(self, seconds: float, tour_furthest: int) -> None:
        step = str(min(max(int(tour_furthest), 1), FINISHED))
        with self._lock:
            row = self._row()
            row["visit_seconds"] += max(0.0, float(seconds))
            row["furthest"][step] = row["furthest"].get(step, 0) + 1
            self._save()

    def days(self, n: int = 14) -> list[DayCounts]:
        with self._lock:
            keys = sorted(self._rows)[-n:] if n > 0 else []
            return [
                DayCounts(
                    day=date.fromisoformat(k),
                    visits=r["visits"],
                    waited=r["waited"],
                    wait_seconds=r["wait_seconds"],
                    visit_seconds=r["visit_seconds"],
                    furthest={int(s): c for s, c in r["furthest"].items()},
                )
                for k, r in ((k, self._rows[k]) for k in keys)
            ]

    def _row(self) -> dict:
        key = self._today().isoformat()
        if key not in self._rows:
            self._rows[key] = {
                "visits": 0, "waited": 0, "wait_seconds": 0.0,
                "visit_seconds": 0.0, "furthest": {},
            }
        return self._rows[key]

    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self._path.read_text())
            rows = {}
            for k, r in data.items():
                date.fromisoformat(k)
                rows[k] = {
                    "visits": int(r["visits"]),
                    "waited": int(r["waited"]),
                    "wait_seconds": float(r["wait_seconds"]),
                    "visit_seconds": float(r["visit_seconds"]),
                    "furthest": {str(int(s)): int(c) for s, c in r["furthest"].items()},
                }
            return rows
        except (OSError, ValueError, TypeError, KeyError, AttributeError,
                OverflowError, RecursionError):
            return {}

    def _save(self) -> None:
        oldest = (self._today() - timedelta(days=KEEP_DAYS - 1)).isoformat()
        for old in [k for k in self._rows if k < oldest]:
            del self._rows[old]
        tmp = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(self._rows, f)
            os.replace(tmp, self._path)
        except OSError:
            log.warning("could not save visitor counts to %s", self._path, exc_info=True)
            if tmp:
                Path(tmp).unlink(missing_ok=True)
