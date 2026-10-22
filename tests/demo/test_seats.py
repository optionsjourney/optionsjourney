"""The seat pool and queue: pure decisions, no processes or clocks."""

from __future__ import annotations

import itertools

from optionsjourney.demo.director import CALENDAR_AFTER
from optionsjourney.demo.seats import (
    BACKOFF,
    IDLE_LIMIT,
    NO_SHOW,
    QUEUE_LAPSE,
    TOTAL_LIMIT,
    WARM_HARD_AGE,
    WARM_MAX_AGE,
    WARM_STAGGER,
    Assign,
    Pool,
    Reset,
    Start,
    State,
    Stop,
    VisitBegin,
    VisitEnd,
    Wait,
)


def make(cap: int = 10, warm: int = 2) -> Pool:
    counter = itertools.count(1)
    return Pool(cap=cap, warm=warm, tokens=lambda: f"t{next(counter)}")


def bring_up(pool: Pool, now: float) -> list:
    """Run one tick and play the control loop: start and health-check every Start."""
    actions = pool.tick(now)
    for a in actions:
        if isinstance(a, Start):
            pool.started(a.n, now)
            pool.healthy(a.n, now)
    return actions


def release(pool: Pool, n: int, now: float) -> None:
    """Idle-reset seat n (the caller makes it idle) and bring it back."""
    assert Reset(n, "idle") in pool.tick(now)
    pool.stopped(n, now)
    bring_up(pool, now)


def test_two_seats_are_kept_warm():
    pool = make()
    assert pool.tick(0) == [Start(1, "t1"), Start(2, "t2")]
    assert pool.tick(1) == []  # already starting
    for n in (1, 2):
        pool.started(n, 1)
        pool.healthy(n, 2)
    assert pool.tick(3) == []


def test_a_visitor_gets_a_warm_seat():
    pool = make()
    bring_up(pool, 0)
    got = pool.arrive(5)
    assert got == Assign(1, "1.t1")
    assert got.waited == 0
    assert pool.assigned() == 1
    assert pool.tick(6) == [Start(3, "t3")]


def test_a_full_house_queues_in_order():
    pool = make(cap=2, warm=2)
    bring_up(pool, 0)
    assert isinstance(pool.arrive(1), Assign)
    assert isinstance(pool.arrive(1), Assign)
    third = pool.arrive(2)
    fourth = pool.arrive(3)
    assert isinstance(third, Wait) and third.place == 1
    assert isinstance(fourth, Wait) and fourth.place == 2
    assert pool.queue_length() == 2
    assert pool.poll(fourth.token, 4) == Wait(fourth.token, 2)


def test_the_head_of_the_queue_gets_the_next_seat():
    pool = make(cap=2, warm=2)
    bring_up(pool, 0)
    pool.arrive(0)
    pool.arrive(0)
    w = pool.arrive(1190)
    assert isinstance(w, Wait)
    pool.activity(2, 0, 1, 1200)  # seat 2 is busy; seat 1 never moved
    assert pool.poll(w.token, 1195) == Wait(w.token, 1)
    release(pool, 1, 1200)
    got = pool.poll(w.token, 1201)
    assert isinstance(got, Assign) and got.seat_n == 1
    assert got.cookie_value.startswith("1.")
    assert got.waited == 1201 - 1190
    assert pool.queue_length() == 0


def test_a_queue_place_lapses_when_not_polled():
    pool = make(cap=2, warm=2)
    bring_up(pool, 0)
    pool.arrive(1)
    pool.arrive(1)
    first = pool.arrive(0)
    second = pool.arrive(10)
    assert pool.poll(second.token, QUEUE_LAPSE - 5) == Wait(second.token, 2)
    assert pool.poll(second.token, QUEUE_LAPSE + 1) == Wait(second.token, 1)
    assert pool.poll(first.token, QUEUE_LAPSE + 2) is None
    assert pool.queue_length() == 1


def test_a_hidden_tab_keeps_its_place_for_two_minutes():
    """Browsers slow a hidden tab's timers to about once a minute."""
    assert QUEUE_LAPSE == 120
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.arrive(0)
    w = pool.arrive(1)
    assert pool.poll(w.token, 61) == Wait(w.token, 1)
    assert pool.poll(w.token, 121) == Wait(w.token, 1)


def test_tick_drops_lapsed_queue_entries():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.arrive(0)
    pool.arrive(1)
    pool.tick(QUEUE_LAPSE + 10)
    assert pool.queue_length() == 0


def test_idle_and_total_limits_release_a_seat():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.arrive(0)
    pool.activity(1, 0, 1, 1)  # the visitor shows up
    assert pool.tick(IDLE_LIMIT - 1) == []
    pool.activity(1, 5 * 60, 3, IDLE_LIMIT)  # last click 5 minutes ago
    assert pool.tick(IDLE_LIMIT) == []
    assert pool.tick(IDLE_LIMIT + 15 * 60) == [Reset(1, "idle")]
    assert pool.seats[0].state == State.RESETTING
    end = pool.visit_ended(1)
    assert end == VisitEnd(IDLE_LIMIT + 15 * 60, 3)
    assert pool.visit_ended(1) is None
    assert pool.assigned() == 0

    busy = make(cap=1, warm=1)
    bring_up(busy, 0)
    busy.arrive(0)
    for t in range(0, TOTAL_LIMIT, 600):
        busy.activity(1, 0, 0, t)
    busy.activity(1, 0, 0, TOTAL_LIMIT - 1)
    assert busy.tick(TOTAL_LIMIT - 1) == []
    busy.activity(1, 0, 0, TOTAL_LIMIT)
    assert busy.tick(TOTAL_LIMIT) == [Reset(1, "time")]


def test_released_seat_gets_a_new_token():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    old = pool.arrive(0)
    assert old.cookie_value == "1.t1"
    release(pool, 1, IDLE_LIMIT)
    assert pool.seats[0].token not in (None, "t1")
    assert pool.arrive(IDLE_LIMIT + 1).cookie_value != old.cookie_value


def test_new_day_keeps_the_visitor():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.arrive(0)
    pool.exited(1, 75, 100)
    assert pool.assigned() == 1
    assert pool.tick(101) == [Start(1, "t1")]
    pool.started(1, 102)
    pool.healthy(1, 103)
    seat = pool.seats[0]
    assert seat.state == State.ASSIGNED and seat.token == "t1"
    assert pool.visit_ended(1) is None


def test_a_seat_that_keeps_failing_backs_off():
    pool = make(cap=2, warm=1)
    assert pool.tick(0) == [Start(1, "t1")]
    pool.started(1, 0)
    pool.exited(1, 1, 0)
    seat = pool.seats[0]
    assert seat.state == State.FAILED and seat.retry_at == BACKOFF[0]
    # meanwhile the other seat is started and a queued visitor gets it
    assert pool.tick(1) == [Start(2, "t2")]
    w = pool.arrive(2)
    assert isinstance(w, Wait)
    pool.started(2, 2)
    pool.healthy(2, 3)
    got = pool.poll(w.token, 4)
    assert isinstance(got, Assign) and got.seat_n == 2
    pool.activity(2, 0, 1, 5)  # its visitor shows up

    now = 0.0
    for i, delay in enumerate(BACKOFF[:3]):
        assert seat.retry_at == now + delay
        assert pool.tick(seat.retry_at - 1) == []
        now = seat.retry_at
        assert pool.tick(now) == [Reset(1, "retry")]
        pool.stopped(1, now)
        (start,) = pool.tick(now)
        assert start.n == 1
        pool.started(1, now)
        pool.exited(1, 1, now)
        assert seat.failures == i + 2


def test_failure_of_an_assigned_seat_ends_the_visit():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.arrive(0)
    pool.activity(1, 0, 2, 50)
    pool.exited(1, 1, 60)
    assert pool.visit_ended(1) == VisitEnd(60, 2)
    assert pool.assigned() == 0
    assert pool.seats[0].token is None


def test_cold_seats_start_for_the_queue_up_to_the_cap():
    pool = make(cap=3, warm=1)
    bring_up(pool, 0)
    pool.arrive(0)
    waits = [pool.arrive(1) for _ in range(3)]
    assert [w.place for w in waits] == [1, 2, 3]
    starts = pool.tick(2)
    assert [a.n for a in starts] == [2, 3]
    assert pool.tick(3) == []  # nothing more to start: the cap is reached


def test_extra_warm_seats_stop_when_quiet():
    pool = make(cap=3, warm=3)
    bring_up(pool, 0)
    pool.warm = 1
    assert pool.tick(1) == [Stop(3), Stop(2)]
    pool.stopped(3, 2)
    pool.stopped(2, 2)
    assert pool.tick(3) == []

    queued = make(cap=3, warm=3)
    bring_up(queued, 0)
    for _ in range(4):
        queued.arrive(1)
    queued.warm = 1
    assert not any(isinstance(a, Stop) for a in queued.tick(2))


def test_a_stuck_start_is_reset():
    pool = make(cap=1, warm=1)
    pool.tick(0)
    pool.started(1, 1)
    assert pool.tick(60) == []
    assert pool.tick(62) == [Reset(1, "start timeout")]


def test_an_unhealthy_seat_is_reset_after_three_failed_checks():
    pool = make()
    bring_up(pool, 0)
    pool.unhealthy(1, 1)
    pool.unhealthy(1, 2)
    assert pool.tick(3) == []
    pool.healthy(1, 3)  # a good check clears the count
    pool.unhealthy(1, 4)
    pool.unhealthy(1, 5)
    assert pool.tick(5) == []
    pool.unhealthy(1, 6)
    # seat 1 goes down and a replacement warm seat starts in the same pass
    assert pool.tick(6) == [Reset(1, "unhealthy"), Start(3, "t3")]


def test_a_second_exit_report_for_a_failed_seat_is_ignored():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.exited(1, 1, 5)
    pool.exited(1, 75, 6)  # a late or repeated report must not restart a seat with no token
    assert pool.seats[0].state == State.FAILED
    assert pool.tick(7) == []


def test_stops_count_starting_seats_and_only_stop_ready_ones():
    pool = make(cap=4, warm=1)
    bring_up(pool, 0)
    pool.warm = 3
    starts = pool.tick(1)  # seats 2 and 3 starting, seat 1 ready
    assert [a.n for a in starts] == [2, 3]
    pool.warm = 1
    # three available (1 ready, 2 starting); two too many, but only READY seats stop
    assert pool.tick(2) == [Stop(1)]
    assert pool.seats[1].state == State.STARTING


def test_the_first_waiters_take_the_ready_seats_in_order():
    pool = make(cap=3, warm=1)
    bring_up(pool, 0)
    pool.arrive(0)  # seat 1
    first, second, third = (pool.arrive(1) for _ in range(3))
    for a in pool.tick(2):
        pool.started(a.n, 2)
        pool.healthy(a.n, 3)
    # two seats ready: the second in line need not wait for the first to poll
    got = pool.poll(second.token, 4)
    assert isinstance(got, Assign)
    assert pool.poll(third.token, 4) == Wait(third.token, 2)
    assert isinstance(pool.poll(first.token, 5), Assign)
    assert pool.poll(third.token, 5) == Wait(third.token, 1)


def test_a_newcomer_takes_a_seat_only_when_everyone_ahead_has_one():
    pool = make(cap=3, warm=1)
    bring_up(pool, 0)
    pool.arrive(0)  # seat 1
    queued = pool.arrive(1)
    for a in pool.tick(2):  # seats 2 and 3: one for the queue, one warm
        pool.started(a.n, 2)
        pool.healthy(a.n, 3)
    assert isinstance(pool.arrive(4), Assign)  # two free, one ahead: there's one to spare
    assert isinstance(pool.arrive(4), Wait)  # one free, one ahead: queue behind them
    assert isinstance(pool.poll(queued.token, 5), Assign)


def test_a_warm_seats_idle_time_starts_at_assignment():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.arrive(IDLE_LIMIT + 100)
    pool.activity(1, IDLE_LIMIT + 100, 1, IDLE_LIMIT + 101)  # idle since it started
    assert pool.tick(IDLE_LIMIT + 102) == []


# ----- warm seats age; visitors who never show up -----


def test_a_warm_seat_no_one_takes_is_restarted_before_its_day_runs_on():
    """The demo day starts with the seat's process; the calendar's target fills 6 minutes
    in, so an untaken warm seat is started afresh before that."""
    assert WARM_MAX_AGE < 6 * 60
    pool = make(cap=4, warm=1)
    bring_up(pool, 0)
    assert pool.tick(WARM_MAX_AGE - 1) == []
    assert pool.tick(WARM_MAX_AGE) == [Reset(1, "stale"), Start(2, "t2")]
    assert pool.visit_ended(1) is None  # no one was on it


def test_warm_seats_restart_one_at_a_time_and_apart():
    pool = make(cap=4, warm=2)
    bring_up(pool, 0)  # seats 1 and 2, started together
    first = bring_up(pool, WARM_MAX_AGE)  # seat 1 goes; a fresh seat 3 comes up
    assert [a for a in first if isinstance(a, Reset)] == [Reset(1, "stale")]
    assert pool.seats[1].state == State.READY  # seat 2 keeps going for now
    pool.stopped(1, WARM_MAX_AGE + 2)
    assert pool.tick(WARM_MAX_AGE + WARM_STAGGER - 1) == []  # not yet its turn
    later = pool.tick(WARM_MAX_AGE + WARM_STAGGER)
    assert [a for a in later if isinstance(a, Reset)] == [Reset(2, "stale")]


def test_a_stale_seat_waits_while_another_warm_seat_is_on_its_way():
    pool = make(cap=4, warm=2)
    bring_up(pool, 0)
    bring_up(pool, WARM_MAX_AGE)  # seat 1 restarts; seat 3 is up
    # seat 1's process is slow to stop: seat 2 waits past its turn (WARM_STAGGER)
    assert pool.seats[0].state == State.RESETTING
    assert pool.tick(WARM_HARD_AGE - 1) == []
    assert pool.seats[1].state == State.READY
    # ... but never past WARM_HARD_AGE, which stays under the calendar's target
    assert CALENDAR_AFTER.total_seconds() > WARM_HARD_AGE
    assert Reset(2, "stale") in pool.tick(WARM_HARD_AGE)


def test_at_most_one_stale_seat_a_pass():
    pool = make(cap=4, warm=2)
    bring_up(pool, 0)
    resets = [a for a in pool.tick(10 * WARM_MAX_AGE) if isinstance(a, Reset)]
    assert resets == [Reset(1, "stale")]
    resets = [a for a in pool.tick(10 * WARM_MAX_AGE + 2) if isinstance(a, Reset)]
    assert resets == [Reset(2, "stale")]


def test_a_taken_seat_never_goes_stale():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.arrive(WARM_MAX_AGE - 1)
    pool.activity(1, 0, 1, WARM_MAX_AGE)
    assert pool.tick(WARM_MAX_AGE * 3) == []


def test_a_visitor_who_never_shows_up_frees_the_seat_and_is_not_counted():
    pool = make(cap=2, warm=1)
    bring_up(pool, 0)
    pool.arrive(10)  # a link preview: it got the cookie and never came back
    pool.activity(1, 30, 1, 20)  # the seat's last request was before it was handed out
    assert pool.visits_began() == []
    assert not any(isinstance(a, Reset) for a in pool.tick(10 + NO_SHOW - 1))
    assert Reset(1, "no-show") in pool.tick(10 + NO_SHOW)
    assert pool.visit_ended(1) is None
    assert pool.assigned() == 0


def test_a_visit_begins_with_the_first_request_after_the_seat_is_handed_out():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.arrive(10)
    pool.activity(1, 0.5, 1, 12)  # a request at 11.5
    assert pool.visits_began() == [VisitBegin(None)]
    pool.activity(1, 0, 2, 14)
    assert pool.visits_began() == []  # once a visit
    assert pool.tick(10 + NO_SHOW + 1) == []


def test_a_queued_visitors_wait_is_counted_when_they_show_up():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    pool.arrive(0)
    pool.activity(1, 0, 1, 1)
    assert pool.visits_began() == [VisitBegin(None)]
    w = pool.arrive(IDLE_LIMIT - 10)
    pool.activity(1, IDLE_LIMIT, 1, IDLE_LIMIT + 1)  # the first visitor went quiet
    release(pool, 1, IDLE_LIMIT + 1)
    got = pool.poll(w.token, IDLE_LIMIT + 5)
    assert isinstance(got, Assign)
    assert pool.visits_began() == []
    pool.activity(1, 0, 1, IDLE_LIMIT + 7)
    assert pool.visits_began() == [VisitBegin(15)]


def test_start_again_frees_the_visitors_seat_only_with_its_token():
    pool = make(cap=2, warm=1)
    bring_up(pool, 0)
    got = pool.arrive(1)
    token = got.cookie_value.partition(".")[2]
    assert not pool.leave(1, "wrong")
    assert not pool.leave(1, token + "x")
    assert not pool.leave(2, token)
    assert not pool.leave(99, token)
    assert pool.leave(1, token)
    assert Reset(1, "left") in pool.tick(2)


def test_a_seat_restarting_for_a_new_day_is_known_by_its_token():
    pool = make(cap=1, warm=1)
    bring_up(pool, 0)
    token = pool.arrive(0).cookie_value.partition(".")[2]
    assert not pool.restarting(1, token)
    pool.exited(1, 75, 5)
    assert pool.restarting(1, token)
    assert not pool.restarting(1, "other")


# ----- warm seats over many cycles -----


def _simulate(pool: Pool, ticks: int, visitors: bool = False):
    """Play the control loop: 2 s ticks, a start takes ~10 s to pass its health check, a
    stop takes one tick. Yields (now, ready unassigned seats) after each tick."""
    from optionsjourney.demo.seats import Assign as _Assign

    coming_up: dict[int, float] = {}  # seat -> when it turns healthy
    stopping: set[int] = set()
    visits: dict[int, float] = {}  # seat -> when its visitor leaves
    for i in range(ticks):
        now = i * 2.0
        for n in sorted(stopping):
            pool.stopped(n, now)
            coming_up.pop(n, None)
        stopping.clear()
        for n, due in list(coming_up.items()):
            if due <= now:
                pool.healthy(n, now)
                del coming_up[n]
        if visitors and i % 211 == 0 and i:
            got = pool.arrive(now)
            if isinstance(got, _Assign):
                pool.activity(got.seat_n, 0, 1, now + 1)
                visits[got.seat_n] = now + 300 + (i % 7) * 60
        for n, leaving in list(visits.items()):
            seat = pool.seats[n - 1]
            if seat.assigned_at is None:
                del visits[n]
            elif now >= leaving:
                pool.leave(n, seat.token or "")
                del visits[n]
            else:
                pool.activity(n, 0, 1, now)
        for a in pool.tick(now):
            if isinstance(a, Start):
                pool.started(a.n, now)
                coming_up[a.n] = now + 10 + (a.n % 3)
            else:
                stopping.add(a.n)
        yield now, [s for s in pool.seats if s.state == State.READY and s.assigned_at is None]


def test_warm_seats_take_turns_over_many_cycles():
    """Replacement seats take other numbers, so the turns go by age, not seat number: there
    is always a warm seat ready, and none has run past the calendar's target."""
    pool = make(cap=10, warm=2)
    empty = 0
    oldest = 0.0
    for now, ready in _simulate(pool, 2400):
        if now < 30:
            continue  # warming up
        if not ready:
            empty += 1
        for seat in ready:
            assert seat.started_at is not None
            oldest = max(oldest, now - seat.started_at)
    assert empty == 0
    assert oldest <= CALENDAR_AFTER.total_seconds()


def test_warm_seats_stay_young_with_visitors_coming_and_going():
    pool = make(cap=10, warm=2)
    for now, ready in _simulate(pool, 3000, visitors=True):
        for seat in ready:
            assert seat.started_at is not None
            assert now - seat.started_at <= CALENDAR_AFTER.total_seconds()
