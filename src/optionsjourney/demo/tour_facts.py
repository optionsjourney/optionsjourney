"""What has happened in the demo, as the tour reads it.

The tour moves on from facts (a bot armed, a trade stopped, a rule attached), not from
the page the visitor was on, so doing a step any way counts. The only thing taken from
page visits is which tour pages were opened (`Visited`).
"""

from __future__ import annotations

import re
import threading

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from optionsjourney.core.market import market_date
from optionsjourney.db.models import Bot, BotExit, BotRun, WatchlistGroup
from optionsjourney.demo.history import CALENDAR, CALENDAR_DAY, CONDOR, PAPER
from optionsjourney.demo.seed import WATCHLIST, WATCHLIST_ID
from optionsjourney.demo.tour import TourFacts

_FILLED = ("filled", "partial")
_EXACT = (
    re.compile(r"/automation"),
    re.compile(r"/automation/runs/\d+"),
    re.compile(r"/automation/\d+/runs"),
)
_PREFIXES = ("/trade", "/charts")


class Visited:
    """The tour pages one visitor has opened. Safe to record from any request thread."""

    def __init__(self) -> None:
        self.paths: set[str] = set()
        self._lock = threading.Lock()

    def record(self, path: str) -> None:
        path = path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
        if any(pattern.fullmatch(path) for pattern in _EXACT):
            keep = path
        else:
            keep = next((x for x in _PREFIXES if path == x or path.startswith(x + "/")), None)
        if keep is not None:
            with self._lock:
                self.paths.add(keep)

    def snapshot(self) -> set[str]:
        """A copy of the paths seen so far."""
        with self._lock:
            return set(self.paths)


def facts(session_factory: sessionmaker, visited: Visited, clock) -> TourFacts:
    """Everything the tour needs to know, worked out from the database and the pages
    opened."""
    today = market_date(clock.now())
    seen = visited.snapshot()
    with session_factory() as session:
        rows = session.execute(select(Bot.name, Bot.id).where(Bot.name.in_([CONDOR, CALENDAR])))
        ids = {name: bot_id for name, bot_id in rows}
        condor_id, calendar_id = ids.get(CONDOR), ids.get(CALENDAR)
        run = session.scalar(
            select(BotRun)
            .where(BotRun.bot_id == condor_id, BotRun.trading_date == today,
                   BotRun.status.in_(_FILLED))
            .order_by(BotRun.id.desc())
            .limit(1)
        )  # fmt: skip
        armed = bool(session.scalar(select(Bot.enabled).where(Bot.id == condor_id)))
        stopped = run is not None and session.scalar(
            select(BotExit.id)
            .where(BotExit.run_id == run.id, BotExit.kind == "stop", BotExit.state == "filled")
            .limit(1)
        ) is not None  # fmt: skip
        run_id = None if run is None else run.id
        target_filled = session.scalar(
            select(BotExit.id)
            .join(BotRun, BotRun.id == BotExit.run_id)
            .where(BotRun.bot_id == calendar_id, BotRun.trading_date == CALENDAR_DAY,
                   BotExit.kind == "target", BotExit.state == "filled")
            .limit(1)
        ) is not None  # fmt: skip
        managed = session.scalar(
            select(BotRun.id)
            .join(Bot, Bot.id == BotRun.bot_id)
            .where(Bot.per_trade.is_(True), BotRun.trading_date == today)
            .limit(1)
        ) is not None  # fmt: skip
        group = session.get(WatchlistGroup, WATCHLIST_ID)
        changed = group is not None and list(group.symbols or []) != WATCHLIST
        own = session.scalar(
            select(BotRun.id)
            .join(Bot, Bot.id == BotRun.bot_id)
            .where(Bot.per_trade.is_(False), Bot.name.not_in([CONDOR, CALENDAR, PAPER]),
                   BotRun.trading_date == today, BotRun.status.in_(_FILLED))
            .limit(1)
        ) is not None  # fmt: skip
    return TourFacts(
        opened_automation="/automation" in seen,
        condor_armed=armed or run is not None,
        condor_stopped=stopped,
        condor_run_id=run_id,
        opened_condor_run=(run_id is not None and f"/automation/runs/{run_id}" in seen)
        or (condor_id is not None and f"/automation/{condor_id}/runs" in seen),
        spread_managed=managed,
        calendar_target_filled=target_filled,
        calendar_bot_id=calendar_id,
        watchlist_changed=changed,
        own_bot_entered=own,
        day_over=clock.is_over(),
    )
