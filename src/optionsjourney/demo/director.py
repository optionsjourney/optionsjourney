"""The director: stages the two moments the demo's tour shows, by steering SPX.

* The SPX 0DTE condor: once today's run has filled and its stops rest at the broker,
  SPX is steered up, once per run, just past the level where the call side's stop
  triggers, arriving about a minute after the entry filled. Always the call side: the
  push goes up, away from the condor's put side and toward the Weekly calendar's strike
  (the seed's market has the calendar's strike above the demo morning). The stop read is
  the one resting now, so a stop edited before the entry is the one that fills.
* The Weekly calendar: from 6 minutes after the demo started (or sooner, when the tour
  reaches step 5) SPX drifts toward the seeded calendar's strike, just far enough for
  its resting 25% target to fill, and is then let go.

Levels are found by pricing the exit's own legs with the pretend market at each level
(``option_mark_at``, with the VIX the move would bring), stepping outward from spot. No
staged move is faster than MAX_POINTS_PER_MINUTE: a longer way takes longer. The
condor's push goes at once (a calendar drift in flight is taken up again after it); a
calendar drift that comes due while the condor's push is moving SPX waits for it.
Nothing here places or changes an order; the bot and the pretend broker do the rest as
they would on any day.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

from sqlalchemy import select

from optionsjourney.core.market import market_date
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import Bot, BotExit, BotRun
from optionsjourney.demo.history import CALENDAR, CALENDAR_DAY, CONDOR
from optionsjourney.demo.market import PretendMarket

logger = logging.getLogger(__name__)

STOP_AFTER = timedelta(seconds=60)  # from the condor's entry fill to its stop
CALENDAR_AFTER = timedelta(minutes=6)  # from the demo's start to the calendar's target
CALENDAR_DRIFT = timedelta(seconds=150)  # how long SPX takes to get there
TOUR_DRIFT = timedelta(seconds=50)  # the same, when tour step 5 brings it forward
TOUR_CALENDAR_STEP = 5
CALENDAR_TRIES = 3  # drifts toward the target, if one is cut short or falls short
MIN_STEER = timedelta(seconds=5)  # the market never steers sooner
MAX_POINTS_PER_MINUTE = 25.0  # no staged move is faster
SEARCH_STEP = 0.5  # SPX points between the levels tried
SEARCH_RANGE = 400.0  # how far from spot a stop's level is looked for
OVERSHOOT = 2.0  # SPX points past the level, so the noise can't stop it short
CUSHION = 0.15  # the calendar aims this much past its target (the VIX is only about right)
_FILLED = ("filled", "partial")

Legs = list[tuple[str, int]]  # (symbol, +ratio bought back / -ratio sold) per unit


class Director:
    def __init__(
        self, session_factory, market: PretendMarket, clock, started_at: datetime
    ) -> None:  # fmt: skip
        self.session_factory = session_factory
        self.market = market
        self.clock = clock
        self.started_at = started_at
        self._calendar_due = started_at + CALENDAR_AFTER  # when its target should fill
        self._calendar_tries = 0
        self._staged: set[int] = set()  # the condor runs already steered
        self._calendar_until: datetime | None = None  # the calendar's drift arrives then
        self._condor_until: datetime | None = None  # the condor's push arrives then

    def on_tour_step(self, n: int) -> None:
        """The tour reached step `n`. Step 5 shows the calendar: its target comes now."""
        if n == TOUR_CALENDAR_STEP:
            soon = self.clock.now() + TOUR_DRIFT
            if soon < self._calendar_due:
                self._calendar_due = soon
                self._calendar_until = None  # a slower drift in flight is overtaken

    def step(self, now: datetime) -> None:
        for name, stage in (("condor", self._condor), ("calendar", self._calendar)):
            try:
                stage(now)
            except Exception:  # noqa: BLE001 -- the demo keeps going without the scene
                logger.exception("the director couldn't stage the %s", name)

    # ----- the condor's stop -----

    def _condor(self, now: datetime) -> None:
        found = self._condor_stop(now)
        if found is None:
            return
        run_id, filled_at, row = found
        # Staged on the first step that finds the call side's stop resting, found level
        # or not: the push happens once per run, and a stop edited or moved afterwards
        # is the bot's to manage, not a cue for a second push.
        self._staged.add(run_id)
        spot = self.market.last("SPX")
        trigger = float(row.trigger)
        level = self._search(spot, 1.0, SEARCH_RANGE, filled_at + STOP_AFTER, _priced_legs(row),
                             lambda cost: cost >= trigger)  # fmt: skip
        if level is None:
            logger.warning("the director found no SPX level for run %s's stop", run_id)
            return
        level += OVERSHOOT
        self._condor_until = self._steer(level, max(filled_at + STOP_AFTER, now), now)
        self._calendar_until = None  # taken over: the calendar starts again after it

    def _condor_stop(self, now: datetime) -> tuple[int, datetime, BotExit] | None:
        """Today's filled condor run not yet staged, with its call side's stop resting
        now."""
        with self.session_factory() as session:
            run = session.scalar(
                select(BotRun)
                .join(Bot, Bot.id == BotRun.bot_id)
                .where(
                    Bot.name == CONDOR,
                    BotRun.trading_date == market_date(now),
                    BotRun.status.in_(_FILLED),
                    BotRun.filled_at.is_not(None),
                )
                .order_by(BotRun.id.desc())
                .limit(1)
            )
            if run is None or run.id in self._staged:
                return None
            stops = session.scalars(
                select(BotExit).where(
                    BotExit.run_id == run.id,
                    BotExit.kind == "stop",
                    BotExit.state == "working",
                    BotExit.trigger.is_not(None),
                )
            )
            calls = [row for row in stops if _short(row)[1] == "CALL"]
            if not calls:
                return None  # not resting yet
            return run.id, run.filled_at, calls[0]

    # ----- the calendar's target -----

    def _calendar(self, now: datetime) -> None:
        if self._calendar_tries >= CALENDAR_TRIES or now < self._calendar_due - CALENDAR_DRIFT:
            return
        if _moving(self._condor_until, now) or _moving(self._calendar_until, now):
            return
        row = self._calendar_target()
        if row is None:
            self._calendar_tries = CALENDAR_TRIES  # filled (or closed): let go
            return
        spot = self.market.last("SPX")
        strike, _ = _short(row)
        direction = 1.0 if strike > spot else -1.0
        reach = abs(strike - spot)  # never past the strike
        by = max(self._calendar_due, now)
        legs, limit = _priced_legs(row), float(row.limit)
        level = self._search(spot, direction, reach, by, legs,
                             lambda cost: cost <= limit - CUSHION)  # fmt: skip
        if level == spot:
            self._calendar_tries = CALENDAR_TRIES  # there already: the target fills; let go
            return
        if level is None:  # never quite there: as near as it gets
            level = min(self._levels(spot, direction, reach),
                        key=lambda lv: self._cost(legs, lv, by))  # fmt: skip
        else:
            level = spot + direction * min(abs(level - spot) + OVERSHOOT, reach)
        self._calendar_tries += 1
        self._calendar_until = self._steer(level, by, now)

    def _calendar_target(self) -> BotExit | None:
        """The seeded Weekly calendar's target, while it rests at the broker. Only the
        seed's open trade: a calendar the visitor opens is left alone."""
        with self.session_factory() as session:
            return session.scalar(
                select(BotExit)
                .join(BotRun, BotRun.id == BotExit.run_id)
                .join(Bot, Bot.id == BotRun.bot_id)
                .where(
                    Bot.name == CALENDAR,
                    BotRun.trading_date == CALENDAR_DAY,
                    BotExit.kind == "target",
                    BotExit.state == "working",
                    BotExit.limit.is_not(None),
                )
                .order_by(BotExit.id.desc())
                .limit(1)
            )

    # ----- the levels -----

    def _cost(self, legs: Legs, level: float, at: datetime) -> float:
        """What closing `legs` costs per unit with SPX at `level` (debit positive)."""
        vix = self.market.vix_for_spx(level)
        return sum(sign * self.market.option_mark_at(occ, level, at, vix) for occ, sign in legs)

    @staticmethod
    def _levels(spot: float, direction: float, reach: float) -> Iterator[float]:
        steps = int(reach / SEARCH_STEP)
        return (spot + direction * SEARCH_STEP * i for i in range(steps + 1))

    def _search(
        self, spot: float, direction: float, reach: float, at: datetime, legs: Legs,
        reached: Callable[[float], bool],
    ) -> float | None:  # fmt: skip
        """The first level from spot outward (within `reach`) where the close's cost at
        `at` has `reached` its price."""
        for level in self._levels(spot, direction, reach):
            if reached(self._cost(legs, level, at)):
                return level
        return None

    def _steer(self, level: float, by: datetime, now: datetime) -> datetime:
        """SPX to `level` by `by`, or later if that's faster than a staged move may be.
        Returns when it arrives."""
        points = abs(level - self.market.last("SPX"))
        slowest = now + timedelta(minutes=points / MAX_POINTS_PER_MINUTE)
        by = max(by, slowest, now + MIN_STEER)
        self.market.steer("SPX", level, by=by)
        return by


def _moving(until: datetime | None, now: datetime) -> bool:
    return until is not None and now < until


def _priced_legs(row: BotExit) -> Legs:
    """The order this exit rests as: a stop resting on its short is that one leg bought
    back; anything else closes the group's legs."""
    if row.kind == "stop" and row.anchor_symbol and not row.watched:
        return [(row.anchor_symbol, 1)]
    return [
        (leg["symbol"], int(leg.get("ratio", 1)) * (1 if leg["side"] == "sell" else -1))
        for leg in row.legs
    ]


def _short(row: BotExit) -> tuple[float, str]:
    """(strike, "CALL"/"PUT") of the exit's short leg (its anchor, else its first)."""
    symbol = row.anchor_symbol or next(leg["symbol"] for leg in row.legs if leg["side"] == "sell")
    details = parse_occ(symbol)[1]
    return float(details.strike), details.put_call
