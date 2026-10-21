"""Bots slice 14: trades held overnight, across the session change, and the pre-open
check. The clock is moved across days; every order goes to the fake broker."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import (
    bot_entry,
    bot_exits,
    bot_integrity,
    bot_timed,
    bot_watched_stops,
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


def ny(year, month, day, hour, minute=0):
    """A New York wall-clock time in October 2026 (UTC-4), as UTC."""
    return datetime(year, month, day, tzinfo=UTC) + timedelta(hours=hour + 4, minutes=minute)


@pytest.fixture(autouse=True)
def _fresh_watch():
    bot_watched_stops.reset()
    yield
    bot_watched_stops.reset()


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


def exits(factory, kind=None, position=None):
    with factory() as session:
        query = select(BotExit).order_by(BotExit.id)
        if kind:
            query = query.where(BotExit.kind == kind)
        if position is not None:
            query = query.where(BotExit.group_position == position)
        return list(session.scalars(query))


def run_of(factory, run_id):
    with factory() as session:
        return session.get(BotRun, run_id)


def titles(deps):
    return [e.title for e in deps.seen]


def credit_diagonal(deps, factory, **kw):
    """A put diagonal opened for a credit: the 1 DTE 5690 put sold at about 3.00, the
    3 DTE 5590 put bought at about 1.55. Its stop rests on the near short."""
    bot_id = make_calendar(factory, live=True, near=1, far=3, offset="-100", credit=True, **kw)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    assert run_of(factory, run_id).status == "filled"
    return run_id


def calendar(deps, factory, **kw):
    """A put calendar for a debit: the 7 DTE 5690 put sold at 6.00, the 14 DTE bought at
    9.50, a 3.50 debit. Its 50% stop is watched by the app at a P&L of −1.75."""
    run_id = bot_entry.fire(deps, make_calendar(factory, live=True, **kw), "09:47")
    assert run_of(factory, run_id).status == "filled"
    return run_id


def test_a_credit_diagonals_stop_rests_gtc_on_the_near_short(deps, factory):
    credit_diagonal(deps, factory)
    (stop,) = exits(factory, "stop")
    assert stop.state == "working" and stop.anchor_symbol.startswith("SPXW  261014P")
    body = next(c[-1] for c in deps.tasty.calls if c[0] == "place" and
                c[-1]["order-type"] == "Stop")  # fmt: skip
    assert body["time-in-force"] == "GTC"


def test_a_resting_stop_cancelled_overnight_is_re_armed_next_morning(deps, factory):
    """Review focus 3: the GTC stop rests overnight; the monitor reads it at any hour, and
    a stop the broker dropped overnight goes back (rearm; Tracker2's _maybe_rearm, off
    hours allowed)."""
    credit_diagonal(deps, factory)
    (stop,) = exits(factory, "stop")
    first = stop.broker_order_id
    at(deps, ny(2026, 10, 13, 16, 30))  # after the close, not its expiry day
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "working"  # nothing changes overnight
    at(deps, ny(2026, 10, 14, 3, 0))
    deps.tasty.cancel_order("5WX01234", first)  # a broker purge overnight
    bot_exits.monitor_pass(deps)
    (stop,) = exits(factory, "stop")
    assert stop.state == "working" and stop.broker_order_id != first
    assert stop.rearms == 1
    stops = [c[-1] for c in deps.tasty.calls if c[0] == "place" and
             c[-1]["order-type"] == "Stop"]  # fmt: skip
    assert len(stops) == 2 and stops[-1]["time-in-force"] == "GTC"


def test_a_calendars_watched_stop_resumes_at_the_next_open(deps, factory):
    """Watched stops run in the regular session only; the next day they read the group
    again and fire on two fresh passes."""
    calendar(deps, factory)
    (stop,) = exits(factory, "stop")
    assert stop.watched and stop.state == "watching" and stop.trigger == D("-1.75")
    near, far = (leg.symbol for leg in bot_exits.exit_legs(stop))
    night = at(deps, ny(2026, 10, 13, 20, 0))
    deps.hub.set(near, D("0.90"), D("1.10"), night)
    deps.hub.set(far, D("2.40"), D("2.60"), night)
    bot_watched_stops.watch_pass(deps)
    assert exits(factory, "stop")[0].state == "watching"  # nothing outside the session
    for minute in (0, 1):
        moment = at(deps, ny(2026, 10, 14, 10, minute))
        deps.hub.set(near, D("0.90"), D("1.10"), moment)  # near 1.00, far 2.50: P&L −2.00
        deps.hub.set(far, D("2.40"), D("2.60"), moment)
        bot_watched_stops.watch_pass(deps)
    closes = exits(factory, "close")
    assert len(closes) == 1 and closes[0].log[0]["by"] == "stop"


def test_a_held_trade_from_two_weeks_ago_is_still_checked(deps, factory):
    """The integrity look-back is MAX_DTE + 10 days, not 10."""
    deps.chain.exps = [*HELD_EXPIRATIONS, "2026-11-03"]
    calendar(deps, factory, near=14, far=21)
    with factory() as session:
        stop = session.scalars(select(BotExit).where(BotExit.kind == "stop")).one()
        stop.state, stop.updated_at = "failed", ny(2026, 10, 26, 9, 0)
        session.commit()
    at(deps, ny(2026, 10, 26, 10, 0))  # 13 days after the fill
    found = bot_integrity.check(deps)
    assert [f.check for f in found] == ["side_unprotected"]


def test_timed_close_runs_per_group_on_its_own_expiration(deps, factory):
    """Question 8: each group closes by its own nearest expiration. A 0 DTE group and a
    7 DTE group: at 15:45 today only the 0 DTE one is closed."""
    from dataclasses import replace

    from optionsjourney.bots.definition import GroupDef, LegDef
    from optionsjourney.services import bot_store
    from optionsjourney.trading.orders import Side

    bot_id = make_calendar(factory, live=True, near=0, far=0, offset="-100", credit=True,
                           close_time="15:45")  # fmt: skip
    bot = bot_store.load(factory, bot_id)
    later = (
        LegDef(2, Side.SELL, "call", dte=7, target=D("6.40"), group=1),
        LegDef(3, Side.BUY, "call", dte=7, pick="offset", target=D("100"), link=2, group=1),
    )
    group = GroupDef(1, "Week", stop_pct=D("50"))
    bot = replace(bot, legs=(*bot.legs, *later), groups=(*bot.groups, group))
    assert bot_store.save(factory, bot, OPEN_TIME)[1] == {}
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    assert run_of(factory, run_id).status == "filled"
    at(deps, ny(2026, 10, 13, 15, 46))
    bot_timed.timed_pass(deps)
    timed = exits(factory, "timed")
    assert [t.group_position for t in timed] == [0]
    assert {leg["symbol"][:12] for leg in timed[0].legs} == {"SPXW  261013"}
    bot_timed.timed_pass(deps)  # once per group per day
    assert len(exits(factory, "timed")) == 1


def test_a_resting_wing_sale_of_a_far_leg_is_gtc(deps, factory):
    """Question 7: the far leg's one-tick sale keeps working overnight."""
    credit_diagonal(deps, factory)
    (stop,) = exits(factory, "stop")
    deps.tasty.fill(stop.broker_order_id, D("4.50"))
    bot_exits.monitor_pass(deps)  # no fresh bid on the far put: a one-tick limit
    (wing,) = exits(factory, "wing")
    assert wing.state == "working"
    body = next(c[-1] for c in deps.tasty.calls if c[0] == "place" and
                c[-1]["legs"][0]["action"] == "Sell to Close")  # fmt: skip
    assert body["order-type"] == "Limit" and body["time-in-force"] == "GTC"


def test_a_same_day_wing_sale_stays_day(deps, factory):
    from tests.services.bot_fakes import make

    bot_entry.fire(deps, make(factory, live=True), "09:47")
    stop = exits(factory, "stop")[0]
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    body = next(c[-1] for c in deps.tasty.calls if c[0] == "place" and
                c[-1]["legs"][0]["action"] == "Sell to Close")  # fmt: skip
    assert body["time-in-force"] == "Day"


def test_a_watched_calendar_stop_ends_when_its_near_leg_expires(deps, factory):
    """Its P&L can't be read whole once the near leg has gone: the watch ends (and the
    settle step takes the far leg)."""
    calendar(deps, factory)
    at(deps, ny(2026, 10, 20, 15, 59))
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "watching"
    at(deps, ny(2026, 10, 20, 16, 0))
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "expired"


def test_watch_rules_skip_a_group_with_an_expired_leg(deps, factory):
    """Question 10."""
    from optionsjourney.services import bot_watch

    calendar(deps, factory, use_delta_exit=True, position_delta_below=D("-50"))
    at(deps, ny(2026, 10, 21, 10, 0))
    assert bot_watch._trades(deps, deps.now()) == []  # its one group is closed for the rules
    at(deps, ny(2026, 10, 19, 10, 0))
    (trade,) = bot_watch._trades(deps, deps.now())
    assert trade.states == {0: "open"}


# --- The pre-open check (section 7) -----------------------------------------------------


def _broker_writes(deps):
    return [c for c in deps.tasty.calls if c[0] in ("place", "replace", "cancel")]


def test_preopen_check_says_all_is_well_once_a_day(deps, factory):
    from optionsjourney.services import bot_overnight

    calendar(deps, factory)
    credit_diagonal(deps, factory)
    at(deps, ny(2026, 10, 14, 9, 19))
    deps.seen.clear()
    bot_overnight.preopen_pass(deps)
    assert deps.seen == []  # not yet 09:20
    at(deps, ny(2026, 10, 14, 9, 20))
    bot_overnight.preopen_pass(deps)
    [said] = deps.seen
    assert said.title == "Held trades checked" and said.routine
    assert "2 trades held overnight, every leg held and every stop in place" in said.detail
    assert "Stops the app watches (Cal Calendar) need the app running from 09:30" in said.detail
    at(deps, ny(2026, 10, 14, 11, 0))
    bot_overnight.preopen_pass(deps)
    assert len(deps.seen) == 1  # once a day, remembered in the settings
    at(deps, ny(2026, 10, 15, 9, 25))
    bot_overnight.preopen_pass(deps)
    assert len(deps.seen) == 2  # the next trading day


def test_preopen_check_alerts_a_leg_changed_at_the_broker_and_sends_nothing(deps, factory):
    """Review focus 4."""
    from optionsjourney.services import bot_overnight
    from optionsjourney.trading.orders import plain

    calendar(deps, factory)
    (stop,) = exits(factory, "stop")
    far = bot_exits.exit_legs(stop)[1].symbol
    deps.tasty.held[plain(far)] = 0  # sold at the broker overnight
    writes = len(_broker_writes(deps))
    at(deps, ny(2026, 10, 14, 9, 20))
    deps.seen.clear()
    problems = bot_overnight.check(deps)
    assert problems == [
        f"Cal Calendar: {plain(far)} — your broker shows 0, the trade should hold 1"
    ]
    [said] = deps.seen
    assert said.title == "Held trades: 1 needs a look" and not said.routine
    assert "The app changed nothing. Check your broker." in said.detail
    assert len(_broker_writes(deps)) == writes


def test_preopen_check_alerts_a_held_group_with_no_stop(deps, factory):
    from optionsjourney.services import bot_overnight

    credit_diagonal(deps, factory)
    (stop,) = exits(factory, "stop")
    with factory() as session:
        session.get(BotExit, stop.id).state = "failed"
        session.commit()
    at(deps, ny(2026, 10, 14, 9, 20))
    assert bot_overnight.check(deps) == ["Cal Calendar has no stop in place (its stop is failed)"]


def test_preopen_check_names_a_stop_the_broker_dropped(deps, factory):
    from optionsjourney.services import bot_overnight

    credit_diagonal(deps, factory)
    (stop,) = exits(factory, "stop")
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    at(deps, ny(2026, 10, 14, 9, 20))
    assert bot_overnight.check(deps) == [
        "Cal Calendar: its stop shows cancelled at your broker (the app re-places a stop the "
        "broker dropped)"
    ]
    assert exits(factory, "stop")[0].state == "working"  # the check changes nothing


def test_preopen_check_unreadable_positions_alerts(deps, factory):
    from optionsjourney.services import bot_overnight

    calendar(deps, factory)

    def broken(account):
        raise RuntimeError("down")

    deps.tasty.option_positions = broken
    at(deps, ny(2026, 10, 14, 9, 20))
    assert bot_overnight.check(deps) == [
        "couldn't read your positions in account ••1234, so 1 held trade wasn't checked"
    ]


def test_startup_check_alerts_problems_only(deps, factory):
    from optionsjourney.services import bot_loops
    from optionsjourney.trading.orders import plain

    calendar(deps, factory)
    at(deps, ny(2026, 10, 14, 2, 0))
    deps.seen.clear()
    bot_loops.recover(deps)
    assert not [e for e in deps.seen if e.title.startswith("Held trades")]
    (stop,) = exits(factory, "stop")
    deps.tasty.held[plain(bot_exits.exit_legs(stop)[0].symbol)] = 0
    bot_loops.recover(deps)
    assert [e.title for e in deps.seen if e.title.startswith("Held trades")] == [
        "Held trades: 1 needs a look"
    ]


def test_a_trade_opened_today_is_not_held_overnight(deps, factory):
    from optionsjourney.services import bot_overnight

    calendar(deps, factory)
    at(deps, OPEN_TIME + timedelta(hours=1))
    assert bot_overnight.held_runs(deps, deps.now()) == []
    at(deps, ny(2026, 10, 14, 9, 20))
    assert len(bot_overnight.held_runs(deps, deps.now())) == 1


def test_preopen_check_reports_a_pending_stop(deps, factory):
    """Review 5: a stop waiting to be placed isn't at the broker overnight."""
    from optionsjourney.services import bot_overnight

    credit_diagonal(deps, factory)
    (stop,) = exits(factory, "stop")
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.state, row.broker_order_id = "pending", None
        session.commit()
    at(deps, ny(2026, 10, 14, 9, 20))
    assert bot_overnight.check(deps) == [
        "Cal Calendar: its stop isn't at your broker yet (waiting to be placed)"
    ]


def test_preopen_check_reports_far_legs_left_after_the_expiry(deps, factory):
    from optionsjourney.services import bot_overnight

    calendar(deps, factory)
    at(deps, ny(2026, 10, 21, 9, 20))  # the near leg expired yesterday
    [problem] = bot_overnight.check(deps)
    assert "near legs expired and its far legs are still held" in problem


def test_startup_says_a_held_calendar_went_a_whole_session_unwatched(deps, factory):
    """Review 8: the app was off all of Wednesday."""
    calendar(deps, factory)
    at(deps, ny(2026, 10, 13, 15, 0))
    bot_watched_stops.watch_pass(deps)  # watched on Tuesday
    deps.seen.clear()
    bot_watched_stops.reset()
    at(deps, ny(2026, 10, 15, 8, 0))  # Thursday, before the open
    bot_watched_stops.recover(deps)
    [said] = [e for e in deps.seen if e.title == "Cal: stops weren't watched all session"]
    assert "Wed Oct 14" in said.detail and "Calendar" in said.detail


def test_a_missed_session_is_said_once_however_often_the_app_restarts(deps, factory):
    calendar(deps, factory)
    at(deps, ny(2026, 10, 13, 15, 0))
    bot_watched_stops.watch_pass(deps)
    for _ in range(3):
        bot_watched_stops.reset()
        at(deps, ny(2026, 10, 15, 8, 0))
        bot_watched_stops.recover(deps)
    said = [e for e in deps.seen if e.title == "Cal: stops weren't watched all session"]
    assert len(said) == 1


def test_preopen_check_says_an_american_short_may_have_been_assigned(deps, factory):
    """Bots slice 16, question 4: an early assignment is processed overnight, so it shows
    at 09:20 as a short leg missing at the broker."""
    from optionsjourney.services import bot_overnight
    from optionsjourney.trading.orders import plain

    deps.chain = FakeChain(expirations=HELD_EXPIRATIONS, root="SPY")
    run_id = credit_diagonal(deps, factory, underlying="SPY")
    short = next(leg["symbol"] for leg in run_of(factory, run_id).legs if leg["side"] == "sell")
    assert short.startswith("SPY   ")
    deps.tasty.held[plain(short)] = 0  # assigned overnight
    at(deps, ny(2026, 10, 14, 9, 20))
    problems = bot_overnight.check(deps)
    assert problems == [
        f"Cal Calendar: {plain(short)} — your broker shows 0, the trade should hold -1. It's "
        "an American-style option: it may have been assigned, so you may hold shares, and its "
        "stop may still be resting at your broker"
    ]
