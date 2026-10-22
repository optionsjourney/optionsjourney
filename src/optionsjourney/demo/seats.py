"""The demo seat pool and queue: who gets which copy of the app, and when.

Pure decision logic. It does no I/O and reads no clock: every method takes
``now`` (monotonic seconds). A control loop calls ``tick`` for the actions to
carry out and reports back what happened (``started``, ``healthy``,
``stopped``, ``exited`` ...).

The loop's flow for one seat::

    tick -> Start(n, token) -> process starts -> started(n) -> health ok
         -> healthy(n)  (READY) -> arrive/poll hands it out (ASSIGNED)
         -> tick -> Reset(n, why) -> process stops -> stopped(n)  (STOPPED)

A visit counts only once its visitor shows up: the seat reports activity after it was
handed out (``visits_began``). A seat no one shows up on (a link preview, a crawler) is
reset after NO_SHOW seconds and counted as nothing.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

COOKIE = "oj_seat"  # the seat cookie, "<n>.<token>": set by the manager, checked by the seat
CAP = 10
WARM = 2
IDLE_LIMIT = 20 * 60
TOTAL_LIMIT = 60 * 60
NO_SHOW = 90  # seconds a handed-out seat may go without its visitor's first request
# A seat's demo day starts when its process does, and the tour's staged moments follow
# (the Weekly calendar's target fills 6 minutes in, director.CALENDAR_AFTER). A warm seat
# no one has taken is started afresh before that. The warm seats take turns: one restarts
# only when no other unassigned seat is starting or stopping and WARM_STAGGER has passed
# since the last, so there is always one ready. Waiting its turn can't take a seat past
# WARM_HARD_AGE; then it restarts anyway. WARM_HARD_AGE stays under CALENDAR_AFTER.
WARM_MAX_AGE = 3 * 60
WARM_STAGGER = 2 * 60
WARM_HARD_AGE = WARM_MAX_AGE + WARM_STAGGER
QUEUE_LAPSE = 120  # seconds without a poll before a queue place is dropped (hidden tabs
# poll slowly: browsers throttle their timers)
START_TIMEOUT = 60
BACKOFF = (10, 30, 60, 120, 300)
NEW_DAY = 75  # exit code: the demo day rolled over; restart, keep the visitor
UNHEALTHY_LIMIT = 3


class State(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    ASSIGNED = "assigned"
    RESETTING = "resetting"
    FAILED = "failed"


@dataclass
class Seat:
    n: int
    port: int
    state: State = State.STOPPED
    token: str | None = None
    assigned_at: float | None = None
    started_at: float | None = None
    failures: int = 0
    retry_at: float | None = None
    tour_furthest: int = 0
    visit_id: str | None = None
    idle_since: float = 0.0
    bad_checks: int = 0
    seen: bool = False  # the visitor has made a request since the seat was handed out
    waited: float | None = None  # how long they queued for it (None: they didn't)


@dataclass(frozen=True)
class Assign:
    """A seat for this visitor: set the cookie ``oj_seat=<cookie_value>``."""

    seat_n: int
    cookie_value: str
    waited: float = 0.0


@dataclass(frozen=True)
class Wait:
    """No seat yet: poll again with ``token``; ``place`` is 1-based."""

    token: str
    place: int


@dataclass(frozen=True)
class Start:
    n: int
    token: str


@dataclass(frozen=True)
class Reset:
    n: int
    reason: str


@dataclass(frozen=True)
class Stop:
    n: int


Action = Start | Reset | Stop


@dataclass(frozen=True)
class VisitBegin:
    waited: float | None  # seconds in the queue, or None if they didn't queue


@dataclass(frozen=True)
class VisitEnd:
    seconds: float
    tour_furthest: int


@dataclass
class _Waiting:
    token: str
    arrived: float
    last_poll: float


class Pool:
    def __init__(
        self,
        cap: int = CAP,
        warm: int = WARM,
        first_port: int = 5101,
        tokens: Callable[[], str] = secrets.token_urlsafe,
    ) -> None:
        self.cap = cap
        self.warm = warm
        self._tokens = tokens
        self.seats = [Seat(n=i + 1, port=first_port + i) for i in range(cap)]
        self._queue: list[_Waiting] = []
        self._restart: set[int] = set()  # NEW_DAY exits waiting for a Start
        self._ended: dict[int, VisitEnd] = {}
        self._began: list[VisitBegin] = []
        self._leaving: set[int] = set()  # visitors who asked for a fresh seat
        self._visits = 0
        self._last_stale: float | None = None  # when a warm seat was last restarted for age

    # ----- visitors -----

    def arrive(self, now: float) -> Assign | Wait:
        """A new visitor: a free READY seat if everyone ahead has one too, else a queue
        place."""
        self._drop_lapsed(now)
        free = self._free_ready()
        if len(free) > len(self._queue):
            return self._assign(free[0], now, now)
        entry = _Waiting(secrets.token_urlsafe(16), now, now)
        self._queue.append(entry)
        return Wait(entry.token, len(self._queue))

    def poll(self, wait_token: str, now: float) -> Assign | Wait | None:
        """A waiting visitor checks in. None if the token is unknown or lapsed."""
        entry = next((e for e in self._queue if e.token == wait_token), None)
        if entry is None:
            return None
        if now - entry.last_poll > QUEUE_LAPSE:
            self._queue.remove(entry)
            return None
        entry.last_poll = now
        self._drop_lapsed(now)
        free = self._free_ready()
        if self._queue.index(entry) < len(free):  # k seats ready: the first k in line
            self._queue.remove(entry)
            return self._assign(free[0], now, entry.arrived, queued=True)
        return Wait(entry.token, self._queue.index(entry) + 1)

    def holds(self, n: int, token: str) -> Seat | None:
        """Seat n, if it is still handed out to the visitor with this token."""
        if not 1 <= n <= self.cap:
            return None
        seat = self.seats[n - 1]
        if seat.assigned_at is None or seat.token is None or not token:
            return None
        if not hmac.compare_digest(seat.token.encode(), token.encode()):
            return None
        return seat

    def leave(self, n: int, token: str) -> bool:
        """The visitor on seat n is done with it (Start again): reset it at the next tick."""
        if self.holds(n, token) is None:
            return False
        self._leaving.add(n)
        return True

    def restarting(self, n: int, token: str) -> bool:
        """Whether this visitor's seat is coming back up for them (Start a new day)."""
        seat = self.holds(n, token)
        return seat is not None and seat.state == State.STARTING

    # ----- decisions -----

    def tick(self, now: float) -> list[Action]:
        """What the control loop should do now."""
        self._drop_lapsed(now)
        actions: list[Action] = []
        for seat in self.seats:
            reason = self._reset_reason(seat, now)
            if reason is not None:
                actions.append(Reset(seat.n, reason))
                self._begin_reset(seat, now, reason)
        stale = [s for s in self.seats if self._stale(s, now)]
        if stale:  # one a pass, oldest first, and only on its turn (see _stale_turn)
            seat = min(stale, key=lambda s: (s.started_at, s.n))
            if self._stale_turn(seat, now):
                actions.append(Reset(seat.n, "stale"))
                self._begin_reset(seat, now, "stale")
                self._last_stale = now
        if not self._queue:
            ready = [s for s in self.seats if s.state == State.READY and s.assigned_at is None]
            extra = self._available() - self.warm  # STARTING seats count, only READY stop
            for seat in sorted(ready, key=lambda s: s.n, reverse=True)[: max(0, extra)]:
                actions.append(Stop(seat.n))
                seat.state = State.RESETTING
        for n in sorted(self._restart):
            seat = self.seats[n - 1]
            assert seat.token is not None
            actions.append(Start(n, seat.token))
            seat.started_at = now
        self._restart.clear()
        need = self.warm + len(self._queue) - self._available()
        for seat in self.seats:
            if need <= 0:
                break
            if seat.state == State.STOPPED and (seat.retry_at is None or seat.retry_at <= now):
                seat.token = self._tokens()
                seat.state = State.STARTING
                seat.started_at = now
                actions.append(Start(seat.n, seat.token))
                need -= 1
        return actions

    # ----- reports from the control loop -----

    def started(self, n: int, now: float) -> None:
        seat = self.seats[n - 1]
        if seat.state == State.STARTING:
            seat.started_at = now

    def healthy(self, n: int, now: float) -> None:
        """A health check passed: STARTING becomes ready; a good check clears the count."""
        seat = self.seats[n - 1]
        if seat.state == State.STARTING:
            seat.state = State.ASSIGNED if seat.assigned_at is not None else State.READY
            seat.failures = 0
            seat.retry_at = None
            if seat.assigned_at is not None:
                seat.idle_since = now
        if seat.state in (State.READY, State.ASSIGNED):
            seat.bad_checks = 0

    def unhealthy(self, n: int, now: float) -> None:
        """A health check failed; the third in a row gets the seat reset."""
        seat = self.seats[n - 1]
        if seat.state in (State.READY, State.ASSIGNED):
            seat.bad_checks += 1

    def exited(self, n: int, code: int, now: float) -> None:
        """The seat's process died on its own. NEW_DAY keeps the visitor."""
        seat = self.seats[n - 1]
        if seat.state in (State.STOPPED, State.RESETTING, State.FAILED):
            return  # expected (we asked it to stop) or already reported
        if code == NEW_DAY:
            seat.state = State.STARTING
            seat.started_at = now
            seat.bad_checks = 0
            self._restart.add(n)
            return
        self._restart.discard(n)
        self._end_visit(seat, now)
        seat.failures += 1
        seat.retry_at = now + BACKOFF[min(seat.failures - 1, len(BACKOFF) - 1)]
        seat.state = State.FAILED
        seat.token = None
        seat.bad_checks = 0

    def activity(self, n: int, idle_seconds: float, tour_furthest: int, now: float) -> None:
        """The seat reports how long since its visitor last did anything."""
        seat = self.seats[n - 1]
        seat.idle_since = now - idle_seconds
        if seat.assigned_at is not None:  # a warm seat's idle time before its visitor
            seat.idle_since = max(seat.idle_since, seat.assigned_at)
        seat.tour_furthest = max(seat.tour_furthest, tour_furthest)
        if seat.state in (State.READY, State.ASSIGNED):
            seat.bad_checks = 0
        if seat.assigned_at is not None and not seat.seen and now - idle_seconds > seat.assigned_at:
            seat.seen = True  # the visitor's first request since the seat was handed out
            self._began.append(VisitBegin(seat.waited))

    def stopped(self, n: int, now: float) -> None:
        """The process is gone: the seat is free to start again with a new token."""
        seat = self.seats[n - 1]
        self._end_visit(seat, now)
        self._restart.discard(n)
        seat.state = State.STOPPED
        seat.token = None
        seat.started_at = None
        seat.bad_checks = 0

    # ----- counts -----

    def assigned(self) -> int:
        return sum(1 for s in self.seats if s.assigned_at is not None)

    def queue_length(self) -> int:
        return len(self._queue)

    def visits_began(self) -> list[VisitBegin]:
        """The visits that began since the last call (their visitors showed up)."""
        began, self._began = self._began, []
        return began

    def visit_ended(self, n: int) -> VisitEnd | None:
        """The visit that just ended on seat n (once), for the counts."""
        return self._ended.pop(n, None)

    # ----- internals -----

    def _drop_lapsed(self, now: float) -> None:
        self._queue = [e for e in self._queue if now - e.last_poll <= QUEUE_LAPSE]

    def _free_ready(self) -> list[Seat]:
        return [
            seat
            for seat in self.seats
            if seat.state == State.READY
            and seat.assigned_at is None
            and seat.bad_checks < UNHEALTHY_LIMIT
        ]

    def _available(self) -> int:
        """Unassigned seats that are up or on their way up."""
        return sum(
            1
            for s in self.seats
            if s.state in (State.READY, State.STARTING) and s.assigned_at is None
        )

    def _assign(self, seat: Seat, now: float, arrived: float, *, queued: bool = False) -> Assign:
        self._visits += 1
        seat.state = State.ASSIGNED
        seat.assigned_at = now
        seat.idle_since = now
        seat.tour_furthest = 0
        seat.visit_id = f"v{self._visits}"
        seat.seen = False
        seat.waited = now - arrived if queued else None
        self._ended.pop(seat.n, None)
        return Assign(seat.n, f"{seat.n}.{seat.token}", now - arrived)

    def _stale(self, seat: Seat, now: float) -> bool:
        """A warm seat no one took, whose demo day has run too far for a new visitor."""
        return (
            seat.state == State.READY
            and seat.assigned_at is None
            and seat.started_at is not None
            and now - seat.started_at >= WARM_MAX_AGE
        )

    def _stale_turn(self, seat: Seat, now: float) -> bool:
        """Whether this stale seat may restart now. The turns go by age, not seat number
        (a replacement takes whichever number is free): wait while another unassigned seat
        is starting or stopping, and WARM_STAGGER after the last stale restart, so a warm
        seat stays ready meanwhile. Past WARM_HARD_AGE it goes anyway."""
        assert seat.started_at is not None
        if now - seat.started_at >= WARM_HARD_AGE:
            return True
        if self._last_stale is not None and now - self._last_stale < WARM_STAGGER:
            return False
        return not any(
            s is not seat and s.assigned_at is None and s.state in (State.STARTING, State.RESETTING)
            for s in self.seats
        )

    def _reset_reason(self, seat: Seat, now: float) -> str | None:
        if seat.n in self._leaving:
            return "left"
        if seat.state == State.ASSIGNED:
            assert seat.assigned_at is not None
            if now - seat.idle_since >= IDLE_LIMIT:
                return "idle"
            if now - seat.assigned_at >= TOTAL_LIMIT:
                return "time"
            if not seat.seen and now - seat.assigned_at >= NO_SHOW:
                return "no-show"
        if seat.state in (State.READY, State.ASSIGNED) and seat.bad_checks >= UNHEALTHY_LIMIT:
            return "unhealthy"
        if (
            seat.state == State.STARTING
            and seat.started_at is not None
            and now - seat.started_at >= START_TIMEOUT
        ):
            return "start timeout"
        if seat.state == State.FAILED and seat.retry_at is not None and seat.retry_at <= now:
            return "retry"
        return None

    def _begin_reset(self, seat: Seat, now: float, reason: str) -> None:
        self._leaving.discard(seat.n)
        self._end_visit(seat, now)
        self._restart.discard(seat.n)
        if reason == "start timeout":
            seat.failures += 1
            seat.retry_at = now + BACKOFF[min(seat.failures - 1, len(BACKOFF) - 1)]
        elif reason == "retry":
            seat.retry_at = None  # the backoff has been served
        seat.state = State.RESETTING

    def _end_visit(self, seat: Seat, now: float) -> None:
        if seat.assigned_at is not None and seat.seen:  # a no-show was never a visit
            self._ended[seat.n] = VisitEnd(now - seat.assigned_at, seat.tour_furthest)
        self._leaving.discard(seat.n)
        seat.assigned_at = None
        seat.visit_id = None
        seat.tour_furthest = 0
        seat.seen = False
        seat.waited = None
