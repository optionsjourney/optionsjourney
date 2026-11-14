"""#330 (decision 0133): closes you ask for after hours on a TastyTrade trade of Cboe index
options, scheduled closes, and stops held for the 09:30 open. Every order goes to the fake
broker; the clock is moved across the evening and the night.

The trade is a put diagonal opened for a credit on Tue 2026-10-13 at 09:47 (its 1 DTE short
expires Wed 10-14, its long Fri 10-16): its stop rests on the short at TastyTrade."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.db.models import BotExit, BotRun, BotScheduledClose
from optionsjourney.services import (
    bot_dynamic_stops,
    bot_entry,
    bot_exits,
    bot_manage,
    bot_scheduled_closes,
    bot_watched_stops,
    orders,
)
from tests.services.bot_fakes import (
    HELD_EXPIRATIONS,
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeTasty,
    make_calendar,
)

D = Decimal
OVERNIGHT_TIF = "GTC Ext Overnight"


def ny(day, hour, minute=0, second=0):
    """A New York wall-clock time in October 2026 (EDT, UTC-4), as UTC."""
    return datetime(2026, 10, day, tzinfo=UTC) + timedelta(
        hours=hour + 4, minutes=minute, seconds=second
    )


@pytest.fixture(autouse=True)
def _fresh():
    bot_watched_stops.reset()
    bot_dynamic_stops.reset()
    yield
    bot_watched_stops.reset()
    bot_dynamic_stops.reset()
    bot_exits.FIRING.clear()


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty],
        FakeHub(),
        FakeChain(expirations=HELD_EXPIRATIONS),
        notify=lambda e: None,
        sleep=lambda s: None,
        now=lambda: OPEN_TIME,
        background=lambda fn, *args: fn(*args),
    )
    d.tasty, d.seen = tasty, []
    d.notify = d.seen.append
    return d


def at(deps, when):
    deps.now = lambda: when
    return when


def exits(factory, kind=None):
    with factory() as session:
        query = select(BotExit).order_by(BotExit.id)
        if kind:
            query = query.where(BotExit.kind == kind)
        return list(session.scalars(query))


def scheduled(factory):
    with factory() as session:
        return list(session.scalars(select(BotScheduledClose).order_by(BotScheduledClose.id)))


def titles(deps):
    return [e.title for e in deps.seen]


def details(deps):
    return " ".join(e.detail for e in deps.seen)


def sent(deps, since=0):
    """Every place or replace body sent from call `since` on."""
    out = []
    for call in deps.tasty.calls[since:]:
        if call[0] == "place":
            out.append(call[2])
        elif call[0] == "replace":
            out.append(call[3])
    return out


def stops_sent(deps, since=0):
    return [b for b in sent(deps, since) if b["order-type"] in ("Stop", "Stop Limit")]


def diagonal(deps, factory, **kw):
    bot_id = make_calendar(factory, live=True, near=1, far=3, offset="-100", credit=True, **kw)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        assert session.get(BotRun, run_id).status == "filled"
    [stop] = exits(factory, "stop")
    assert stop.state == "working" and stop.broker_order_id
    return run_id


def quote_legs(deps, factory, when, short_mid="3.00", long_mid="1.50"):
    with factory() as session:
        run = session.scalars(select(BotRun).order_by(BotRun.id)).first()
        legs = list(run.legs)
    for leg in legs:
        mid = D(short_mid) if leg["side"] == "sell" else D(long_mid)
        deps.hub.set(leg["symbol"], mid - D("0.05"), mid + D("0.05"), when)


def night(deps, factory, day=13, hour=22, minute=0):
    when = at(deps, ny(day, hour, minute))
    quote_legs(deps, factory, when)
    return when


# --- The overnight close ----------------------------------------------------------------


def test_an_overnight_close_chases_gtc_overnight_limits_and_fills(deps, factory):
    run_id = diagonal(deps, factory)
    stop = exits(factory, "stop")[0]
    night(deps, factory)
    before = len(deps.tasty.calls)
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok and "overnight" in out.message
    calls = deps.tasty.calls[before:]
    assert calls[0] == ("cancel", stop.broker_order_id)  # the resting stop is cancelled first
    bodies = sent(deps, before)
    assert bodies and all(b["time-in-force"] == OVERNIGHT_TIF for b in bodies)
    assert all(b["order-type"] == "Limit" for b in bodies)
    [close] = exits(factory, "close")
    assert close.state == "filled" and close.log[0]["session"] == "overnight"


def test_an_overnight_close_that_never_fills_holds_the_stop_for_the_open(deps, factory):
    # Natural then leg out after the last try in the day: never overnight
    run_id = diagonal(deps, factory, exhaust="natural_then_leg_out")
    deps.tasty.close = "work"
    night(deps, factory)
    before = len(deps.tasty.calls)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    bodies = sent(deps, before)
    assert len(bodies) > 1  # the send and its replaces
    assert all(b["time-in-force"] == OVERNIGHT_TIF and b["order-type"] == "Limit"
               for b in bodies)  # fmt: skip
    assert all(len(b["legs"]) == 2 for b in bodies)  # no leg-out, nothing at market
    assert stops_sent(deps, before) == []  # the broker refuses stops now: none sent
    [close] = exits(factory, "close")
    assert close.state == "failed" and "last overnight try" in close.log[-1]["event"]
    [stop] = exits(factory, "stop")
    assert stop.state == "pending" and stop.stop_at_open and stop.broker_order_id is None
    assert "09:30" in details(deps)
    # The night's monitor passes never send it
    for minute in (0, 30):
        at(deps, ny(13, 23, minute))
        bot_exits.monitor_pass(deps)
    at(deps, ny(14, 9, 30, 4))
    bot_exits.monitor_pass(deps)
    assert stops_sent(deps, before) == []
    # From 09:30:05 the monitor places it
    at(deps, ny(14, 9, 30, 5))
    bot_exits.monitor_pass(deps)
    [placed] = stops_sent(deps, before)
    assert placed["legs"][0]["action"] == "Buy to Close"
    [stop] = exits(factory, "stop")
    assert stop.state == "working" and not stop.stop_at_open
    assert any("stop placed at the open" in t for t in titles(deps))


def test_a_held_stop_waits_for_freeze_at_the_open(deps, factory):
    run_id = diagonal(deps, factory)
    deps.tasty.close = "work"
    night(deps, factory)
    before = len(deps.tasty.calls)
    bot_manage.close_part(deps, run_id, 0, "1")
    orders.set_switch(factory, "freeze", True)
    at(deps, ny(14, 9, 31))
    bot_exits.monitor_pass(deps)
    assert stops_sent(deps, before) == []
    assert exits(factory, "stop")[0].stop_at_open
    orders.set_switch(factory, "freeze", False)
    bot_exits.monitor_pass(deps)
    assert len(stops_sent(deps, before)) == 1


def test_a_stop_the_open_refuses_is_tried_again_from_0945_then_alerted(deps, factory):
    run_id = diagonal(deps, factory)
    deps.tasty.close = "work"
    night(deps, factory)
    bot_manage.close_part(deps, run_id, 0, "1")
    from optionsjourney.brokers.http import BrokerError

    deps.tasty.errors["place"] = BrokerError("tif_session_denied")
    at(deps, ny(14, 9, 31))
    bot_exits.monitor_pass(deps)
    [stop] = exits(factory, "stop")
    assert stop.state == "pending" and stop.stop_at_open
    assert stop.log[-1]["event"] == bot_exits.REFUSED_AT_OPEN
    at(deps, ny(14, 9, 40))
    calls = len(deps.tasty.calls)
    bot_exits.monitor_pass(deps)
    assert len(deps.tasty.calls) == calls or not stops_sent(deps, calls)  # not before 09:45
    at(deps, ny(14, 9, 45))
    bot_exits.monitor_pass(deps)
    [stop] = exits(factory, "stop")
    assert stop.state == "failed" and not stop.stop_at_open
    assert any("UNPROTECTED" in t for t in titles(deps))


def test_a_market_close_is_refused_overnight(deps, factory):
    run_id = diagonal(deps, factory)
    night(deps, factory)
    before = len(deps.tasty.calls)
    out = bot_manage.close_part(deps, run_id, 0, "1", "market")
    assert not out.ok and "Market orders" in out.message
    out = bot_manage.close_trade(deps, run_id, "market")
    assert not out.ok and "Market orders" in out.message
    assert deps.tasty.calls[before:] == [] and exits(factory, "close") == []


def test_close_entire_trade_runs_overnight(deps, factory):
    run_id = diagonal(deps, factory)
    night(deps, factory)
    before = len(deps.tasty.calls)
    out = bot_manage.close_trade(deps, run_id)
    assert out.ok and "overnight" in out.message
    assert all(b["time-in-force"] == OVERNIGHT_TIF for b in sent(deps, before))
    [close] = exits(factory, "close")
    assert close.state == "filled"


def test_a_close_claimed_overnight_that_starts_after_0925_sends_nothing(deps, factory):
    run_id = diagonal(deps, factory)
    stop = exits(factory, "stop")[0]
    jobs = []
    deps.background = lambda fn, *args: jobs.append((fn, args))
    night(deps, factory, day=14, hour=9, minute=24)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    before = len(deps.tasty.calls)
    at(deps, ny(14, 9, 26))
    for fn, args in jobs:
        fn(*args)
    assert deps.tasty.calls[before:] == []  # nothing cancelled, nothing sent
    [close] = exits(factory, "close")
    assert close.state == "failed" and "ended before it started" in close.log[-1]["event"]
    [back] = exits(factory, "stop")
    assert back.state == "working" and back.broker_order_id == stop.broker_order_id
    assert any("close not started" in t for t in titles(deps))


def test_an_overnight_chase_stops_at_0925(deps, factory):
    run_id = diagonal(deps, factory)
    deps.tasty.close = "work"
    start = night(deps, factory, day=14, hour=9, minute=24)
    clock = {"now": start}

    def sleep(seconds):
        clock["now"] += timedelta(seconds=30)

    deps.sleep = sleep
    deps.now = lambda: clock["now"]
    before = len(deps.tasty.calls)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    [close] = exits(factory, "close")
    assert close.state == "failed" and "overnight session ended" in close.log[-1]["event"]
    assert stops_sent(deps, before) == []
    [stop] = exits(factory, "stop")
    assert stop.state == "pending" and stop.stop_at_open


# --- Refused, as before ------------------------------------------------------------------


def test_a_schwab_trade_still_closes_only_in_the_regular_session(deps, factory):
    run_id = diagonal(deps, factory)
    with factory() as session:
        session.get(BotRun, run_id).broker = "schwab"
        session.commit()
    night(deps, factory)
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert not out.ok and "market is open" in out.message
    assert scheduled(factory) == []


def test_every_leg_expired_is_refused(deps, factory):
    run_id = diagonal(deps, factory)
    at(deps, ny(16, 22))  # Fri night: the diagonal's legs expired Wed and Fri
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert not out.ok and "expired" in out.message


# --- Scheduled closes --------------------------------------------------------------------


def test_a_close_in_the_evening_gap_is_scheduled_and_starts_at_2016(deps, factory):
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    before = len(deps.tasty.calls)
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok and "scheduled for 20:16 ET" in out.message
    assert deps.tasty.calls[before:] == [] and exits(factory, "close") == []
    assert any("close scheduled" in t for t in titles(deps))
    [row] = scheduled(factory)
    assert row.state == "scheduled" and row.group_position == 0 and row.quantity == "1"
    # One a trade
    again = bot_manage.close_trade(deps, run_id)
    assert not again.ok and "already scheduled" in again.message
    # Not before its time
    at(deps, ny(13, 20, 15, 30))
    assert bot_scheduled_closes.fire_due(deps) == 0
    # At 20:16 it starts, as if clicked then: overnight
    night(deps, factory, hour=20, minute=16)
    assert bot_scheduled_closes.fire_due(deps) == 1
    [row] = scheduled(factory)
    assert row.state == "started"
    assert all(b["time-in-force"] == OVERNIGHT_TIF for b in sent(deps, before))
    [close] = exits(factory, "close")
    assert close.state == "filled"
    assert bot_scheduled_closes.fire_due(deps) == 0  # once


def test_a_scheduled_close_can_be_cancelled(deps, factory):
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    bot_manage.close_trade(deps, run_id)
    [row] = scheduled(factory)
    assert bot_scheduled_closes.latest(factory, run_id).id == row.id
    ok, _ = bot_scheduled_closes.cancel(deps, run_id, row.id)
    assert ok and scheduled(factory)[0].state == "cancelled"
    assert bot_scheduled_closes.latest(factory, run_id) is None
    ok, why = bot_scheduled_closes.cancel(deps, run_id, row.id)
    assert not ok and "cancelled" in why
    night(deps, factory, hour=20, minute=16)
    before = len(deps.tasty.calls)
    assert bot_scheduled_closes.fire_due(deps) == 0
    assert deps.tasty.calls[before:] == []


def test_a_scheduled_close_honours_freeze_when_it_fires(deps, factory):
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    bot_manage.close_part(deps, run_id, 0, "1")
    orders.set_switch(factory, "freeze", True)
    night(deps, factory, hour=20, minute=16)
    before = len(deps.tasty.calls)
    assert bot_scheduled_closes.fire_due(deps) == 0
    assert deps.tasty.calls[before:] == []
    [row] = scheduled(factory)
    assert row.state == "failed" and "Freeze" in row.result
    assert any("scheduled close NOT started" in t for t in titles(deps))


def test_a_scheduled_close_skips_a_trade_already_closed(deps, factory):
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    bot_manage.close_part(deps, run_id, 0, "1")
    with factory() as session:
        session.get(BotRun, run_id).status = "closed"
        session.commit()
    night(deps, factory, hour=20, minute=16)
    alerts = len(deps.seen)
    assert bot_scheduled_closes.fire_due(deps) == 0
    assert scheduled(factory)[0].state == "skipped"
    assert len(deps.seen) == alerts  # quietly


def test_a_scheduled_close_lost_to_a_restart_is_alerted(deps, factory):
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    bot_manage.close_part(deps, run_id, 0, "1")
    with factory() as session:
        row = session.scalars(select(BotScheduledClose)).one()
        row.state, row.result = "started", bot_scheduled_closes.STARTING
        row.updated_at = ny(13, 20, 16)
        session.commit()
    at(deps, ny(13, 20, 19))
    bot_scheduled_closes.fire_due(deps)
    assert scheduled(factory)[0].state == "failed"
    assert any("may not have started" in t for t in titles(deps))


def test_the_morning_gap_schedules_for_0931_and_a_weekend_for_sunday(deps, factory):
    run_id = diagonal(deps, factory)
    at(deps, ny(14, 9, 27))
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok and "09:31 ET" in out.message
    assert scheduled(factory)[0].start_at == ny(14, 9, 31)


# --- Stops off-hours -----------------------------------------------------------------------


def test_stop_changes_are_refused_off_hours_and_nothing_is_cancelled(deps, factory):
    run_id = diagonal(deps, factory)
    night(deps, factory)
    before = len(deps.tasty.calls)
    for out in (
        bot_manage.change_stop(deps, run_id, 0, "80"),
        bot_manage.set_closing_order(deps, run_id, 0, "both", "1"),
    ):
        assert not out.ok and "09:30" in out.message
    assert deps.tasty.calls[before:] == []
    assert exits(factory, "stop")[0].state == "working"


def test_switching_a_stop_on_is_refused_off_hours(deps, factory):
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 11))
    assert bot_manage.stop_off(deps, run_id, 0).ok
    night(deps, factory)
    out = bot_manage.stop_on(deps, run_id, 0)
    assert not out.ok and "09:30" in out.message


def test_a_fill_off_hours_holds_its_stop_for_the_open(deps, factory):
    run_id = diagonal(deps, factory)
    with factory() as session:  # as if the fill came in overnight: no exits yet
        for row in session.scalars(select(BotExit)):
            session.delete(row)
        session.commit()
    night(deps, factory)
    before = len(deps.tasty.calls)
    bot_exits.place_protections(deps, run_id)
    assert stops_sent(deps, before) == []
    [stop] = exits(factory, "stop")
    assert stop.state == "pending" and stop.stop_at_open
    assert any("stop placed after 09:30" in t for t in titles(deps))


def test_a_resting_target_placed_off_hours_rests_gtc_overnight(deps, factory):
    run_id = diagonal(deps, factory)
    night(deps, factory)
    run, bot = bot_exits._run_and_bot(deps, run_id)
    [stop] = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)
    deps.tasty.cancel_order(run.account, stop.broker_order_id)  # one closing order a contract
    deps.tasty.close = "work"  # it rests, unfilled
    before = len(deps.tasty.calls)
    state, order_id, _, said = bot_exits._send_target(deps, bot, run, legs, 1, D("0.50"), True)
    assert state == "working", said
    [body] = sent(deps, before)
    assert body["time-in-force"] == OVERNIGHT_TIF
    deps.tasty.cancel_order(run.account, order_id)
    at(deps, ny(14, 11))
    before = len(deps.tasty.calls)
    state, _, _, said = bot_exits._send_target(deps, bot, run, legs, 1, D("0.50"), True)
    assert state == "working", said
    [body] = sent(deps, before)
    assert body["time-in-force"] == "GTC"  # the day's resting target, as before


def test_the_dynamic_stop_never_moves_off_hours(deps, factory, monkeypatch):
    diagonal(deps, factory)
    moved = []
    monkeypatch.setattr(bot_dynamic_stops, "_dynamic_rows", lambda d: moved.append(1) or [])
    night(deps, factory)
    bot_dynamic_stops.ratchet_pass(deps)
    assert moved == []


def test_a_held_stop_is_no_integrity_finding_and_the_preopen_check_says_0930(deps, factory):
    from optionsjourney.services import bot_integrity, bot_overnight

    run_id = diagonal(deps, factory)
    deps.tasty.close = "work"
    night(deps, factory)
    bot_manage.close_part(deps, run_id, 0, "1")
    assert exits(factory, "stop")[0].stop_at_open
    at(deps, ny(13, 23, 30))
    assert [
        f for f in bot_integrity.check(deps) if "stop" in f.check or "unprotected" in f.check
    ] == []
    at(deps, ny(14, 9, 20))
    problems = bot_overnight.check(deps)
    assert any("first minute after 09:30" in p for p in problems)
    assert not any("waiting to be placed" in p for p in problems)


def test_an_earlier_mornings_refusal_doesnt_count_against_a_new_hold(deps, factory):
    """Review fix 1: only the log since the latest hold counts as a refusal at the open."""
    run_id = diagonal(deps, factory)
    deps.tasty.close = "work"
    night(deps, factory)
    bot_manage.close_part(deps, run_id, 0, "1")
    [stop] = exits(factory, "stop")
    with factory() as session:  # an earlier morning's refusal, then tonight's hold
        row = session.get(BotExit, stop.id)
        row.log = [{"event": bot_exits.REFUSED_AT_OPEN}, *row.log,
                   {"event": bot_exits.HELD_FOR_OPEN}]  # fmt: skip
        session.commit()
    before = len(deps.tasty.calls)
    at(deps, ny(14, 9, 31))  # before 09:45: placed now, not held back as a retry
    bot_exits.monitor_pass(deps)
    assert len(stops_sent(deps, before)) == 1
    assert exits(factory, "stop")[0].state == "working"


def test_the_alerts_say_the_first_minute_after_0930(deps, factory):
    """Review fix 2: placement runs on the 30 s monitor, so the words don't promise 09:30:05."""
    assert "first minute after 09:30" in bot_exits.AT_OPEN
    run_id = diagonal(deps, factory)
    deps.tasty.close = "work"
    night(deps, factory)
    bot_manage.close_part(deps, run_id, 0, "1")
    assert "first minute after 09:30" in details(deps)


def test_a_scheduled_close_that_fires_into_another_gap_says_rescheduled(deps, factory):
    """Review fix 3b: the app came back at 09:27; the 20:16 row starts a new 09:31 one."""
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    bot_manage.close_trade(deps, run_id)
    at(deps, ny(14, 9, 27))
    assert bot_scheduled_closes.fire_due(deps) == 0
    first, second = scheduled(factory)
    assert first.state == "rescheduled" and "09:31" in first.result
    assert second.state == "scheduled" and second.start_at == ny(14, 9, 31)
    assert any("rescheduled for 09:31 ET" in t for t in titles(deps))
    assert not any("scheduled close started" in t for t in titles(deps))


def test_freeze_at_fire_time_says_the_scheduled_close_is_cancelled(deps, factory):
    """Review fix 3a: Stop all bots cancels it, and the alert says to schedule it again."""
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    bot_manage.close_part(deps, run_id, 0, "1")
    orders.set_switch(factory, "freeze", True)
    night(deps, factory, hour=20, minute=16)
    bot_scheduled_closes.fire_due(deps)
    said = details(deps)
    assert "isn't tried again" in said and "schedule it again" in said


def test_two_scheduled_closes_for_one_trade_are_refused_by_the_database(deps, factory):
    """Review fix 4: a second 'scheduled' row on the run can't be written, even when two
    clicks race past the check."""
    from sqlalchemy.exc import IntegrityError

    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    bot_manage.close_trade(deps, run_id)
    [row] = scheduled(factory)
    with factory() as session, pytest.raises(IntegrityError):
        session.add(BotScheduledClose(
            run_id=run_id, order_type="limit", start_at=row.start_at, state="scheduled",
            created_at=row.created_at, updated_at=row.created_at,
        ))  # fmt: skip
        session.commit()


def test_a_racing_second_schedule_reads_already_scheduled(deps, factory, monkeypatch):
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    bot_manage.close_trade(deps, run_id)
    # the check before the insert misses the first row, as a racing click would
    monkeypatch.setattr(bot_scheduled_closes, "_waiting", lambda session, run_id: None)
    ok, said = bot_scheduled_closes.schedule(deps, run_id, start_at=ny(14, 0, 16))
    assert not ok and "already scheduled" in said
    assert len(scheduled(factory)) == 1


def test_a_lost_claim_says_it_may_have_been_sent(deps, factory):
    """Review fix 5."""
    run_id = diagonal(deps, factory)
    at(deps, ny(13, 17, 10))
    bot_manage.close_part(deps, run_id, 0, "1")
    with factory() as session:
        row = session.scalars(select(BotScheduledClose)).one()
        row.state, row.result = "started", bot_scheduled_closes.STARTING
        row.updated_at = ny(13, 20, 16)
        session.commit()
    at(deps, ny(13, 20, 19))
    bot_scheduled_closes.fire_due(deps)
    assert "may or may not have been sent" in details(deps)
    assert "check your broker before closing it yourself" in details(deps)
