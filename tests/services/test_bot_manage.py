"""Slice 6: managing one bot trade from the journal, with the fake broker."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import TimeActionDef
from optionsjourney.core import sessions
from optionsjourney.db.models import BotExit, BotTradeOverride
from optionsjourney.services import bot_entry, bot_exits, bot_manage, bot_store, bot_timed
from optionsjourney.trading.orders import OrderStatus
from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make

D = Decimal
DAY = date(2026, 10, 13)


def ny(hhmm, day=DAY):
    """A New York wall-clock time on the test day, as the UTC moment (EDT: UTC-4)."""
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hours + 4, minutes, tzinfo=UTC)


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty],
        FakeHub(),
        FakeChain(),
        notify=lambda e: None,
        sleep=lambda s: None,
        now=lambda: OPEN_TIME,
        background=lambda fn, *args: fn(*args),
    )
    d.tasty = tasty
    d.seen = []
    d.notify = d.seen.append
    yield d
    bot_exits.FIRING.clear()
    sessions.use_fetched_calendar([], {})


def trade(deps, factory, target_pct="50", contracts=1, **changes):
    """A live iron condor bot's filled trade (09:47), with a stop and a target on each
    side; the run's id."""
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, target_pct=D(target_pct) if target_pct else None)
                   for g in bot.groups)  # fmt: skip
    _, problems = bot_store.save(factory, replace(bot, groups=groups, **changes), OPEN_TIME)
    assert problems == {}
    if contracts > 1:
        from tests.services.bot_fakes import force_contracts

        force_contracts(factory, bot_id, contracts)
    return bot_entry.fire(deps, bot_id, "09:47")


def rows(factory, kind=None, side=None):
    with factory() as session:
        query = select(BotExit).order_by(BotExit.id)
        if kind:
            query = query.where(BotExit.kind == kind)
        if side is not None:
            query = query.where(BotExit.group_position == side)
        return list(session.scalars(query))


def at(deps, hhmm):
    deps.now = lambda: ny(hhmm)


def quotes(deps, factory, hhmm, short_mid="0.60", long_mid="0.05"):
    """Fresh quotes on every side's two legs at `hhmm` (bid and ask 0.05 either side)."""
    for stop in rows(factory, "stop"):
        for symbol, mid in ((stop.short_symbol, short_mid), (stop.long_symbol, long_mid)):
            m = D(mid)
            deps.hub.set(symbol, max(m - D("0.05"), D(0)), m + D("0.05"), ny(hhmm))


def titles(deps):
    return [e.title for e in deps.seen]


def placed(deps, kind=None):
    return [c for c in deps.tasty.calls if c[0] == "place"
            and (kind is None or c[2]["order-type"] == kind)]  # fmt: skip


def side_closes(deps):
    return [c[2] for c in placed(deps) if len(c[2]["legs"]) == 2
            and c[2]["legs"][0]["action"].endswith("Close")]  # fmt: skip


def turn_off_in_db(factory, run_id, side):
    """Exits off, as bot_manage leaves a side: the override set, the stop and target
    cancelled (and the stop's order cancelled at the broker by the test)."""
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=side, exits_off=True,
                                     changed_at=OPEN_TIME, log=[]))  # fmt: skip
        for row in session.scalars(
            select(BotExit).where(BotExit.run_id == run_id, BotExit.group_position == side)
        ):
            if row.state in ("working", "pending", "armed"):
                row.state = "cancelled"
                row.log = [*row.log, {"event": bot_exits.EXITS_OFF}]
        session.commit()


# --- Task 3: the exits-off guard -------------------------------------------------------


def test_rearm_sends_nothing_while_exits_are_off(deps, factory):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    turn_off_in_db(factory, run_id, 0)
    with factory() as session:
        session.get(BotExit, stop.id).state = "pending"  # as if Freeze had held it
        session.commit()
    before = len(placed(deps))
    bot_exits.monitor_pass(deps)
    assert bot_exits.rearm(deps, stop.id, first=True) is False
    assert len(placed(deps)) == before
    row = rows(factory, "stop", 0)[0]
    assert row.state == "cancelled" and "exits are off" in row.log[-1]["event"]


def test_a_target_on_a_side_with_exits_off_never_fires(deps, factory):
    run_id = trade(deps, factory)
    target = rows(factory, "target", 0)[0]
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=0, exits_off=True,
                                     changed_at=OPEN_TIME, log=[]))  # fmt: skip
        session.commit()
    bot_exits.fire_target(deps, target.id)
    assert side_closes(deps) == []
    assert rows(factory, "target", 0)[0].state == "cancelled"
    assert rows(factory, "stop", 0)[0].state == "working"  # not touched by the claim


def test_with_exits_off_the_timed_close_still_runs_and_a_time_action_switches_back_on(
    deps, factory
):
    """Bots slice 18, the author's ruling: the timed close closes a side with exits off
    (Tracker2), and it's said. A time action's target move switches the target back on."""
    action = TimeActionDef("dte", 0, "14:00", target_pct=D("25"))
    run_id = trade(deps, factory, close_time="15:45", use_time_actions=True, time_actions=(action,))
    turn_off_in_db(factory, run_id, 0)
    deps.tasty.cancel_order("5WX01234", rows(factory, "stop", 0)[0].broker_order_id)
    at(deps, "14:00")
    quotes(deps, factory, "14:00")
    bot_timed.actions_pass(deps)
    assert [(a.group_position, a.state) for a in rows(factory, "action")] == [
        (0, "filled"),
        (1, "filled"),
    ]
    assert rows(factory, "target", 0)[-1].state == "armed"  # the action switched it back on
    at(deps, "15:45")
    quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert [t.group_position for t in rows(factory, "timed")] == [0, 1]
    assert "IC: timed close on a side with exits off" in titles(deps)
    state, notes = bot_exits.apply_time_action(
        deps, run_id, 0, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")), 1, False
    )
    assert state == "closed"  # the timed close took it


# --- Task 4: change the stop and the target -------------------------------------------


def override(factory, run_id, side):
    with factory() as session:
        return session.scalar(
            select(BotTradeOverride).where(
                BotTradeOverride.run_id == run_id, BotTradeOverride.group_position == side
            )
        )


def test_a_stop_change_is_refused_when_it_would_fire_at_once_and_the_old_stop_stands(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="9.00")  # the short's ask is past a 200% stop
    out = bot_manage.change_stop(deps, run_id, 0, "200")
    assert not out.ok and "would fire at once" in out.message
    assert [c[0] for c in deps.tasty.calls].count("cancel") == 0  # nothing was cancelled
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.broker_order_id, stop.trigger) == (
        "working",
        old.broker_order_id,
        old.trigger,
    )
    assert override(factory, run_id, 0) is None  # a refusal saves nothing


def test_changing_the_stop_cancels_confirms_then_places_the_new_one(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.change_stop(deps, run_id, 0, "200")
    assert out.ok, out.message
    calls = [c for c in deps.tasty.calls if c[0] in ("cancel", "place")]
    assert calls[-2] == ("cancel", old.broker_order_id)
    assert calls[-1][0] == "place" and calls[-1][2]["order-type"] == "Stop"
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.broker_order_id == calls[-1][1]
    assert stop.trigger > old.trigger and stop.rearms == 0  # not counted as a re-arm
    assert stop.log[-2]["stop_calc"]["moved_by"] == "you"
    saved = override(factory, run_id, 0)
    assert saved.stop_pct == D("200") and saved.log[-1]["what"] == "stop 200%"
    assert rows(factory, "stop", 1)[0].broker_order_id != stop.broker_order_id  # other side


def test_a_stop_change_without_a_fresh_price_is_refused(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")  # no quotes at all
    out = bot_manage.change_stop(deps, run_id, 0, "200")
    assert not out.ok and "no fresh price" in out.message
    assert rows(factory, "stop", 0)[0].state == "working"


def test_a_stop_whose_cancel_is_not_confirmed_is_not_changed(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    deps.tasty.stuck.add(old.broker_order_id)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.change_stop(deps, run_id, 0, "200")
    assert not out.ok and "wasn't confirmed" in out.message
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.trigger, stop.broker_order_id) == (
        "working",
        old.trigger,
        old.broker_order_id,
    )
    assert len(placed(deps, "Stop")) == 2  # the two first stops only
    assert override(factory, run_id, 0) is None


def test_a_failed_stop_gets_a_new_one_at_the_new_level(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    deps.tasty.cancel_order("5WX01234", old.broker_order_id)  # gone at the broker
    with factory() as session:
        session.get(BotExit, old.id).state = "failed"  # e.g. its re-arm was refused
        session.commit()
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.change_stop(deps, run_id, 0, "200")
    assert out.ok, out.message
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.broker_order_id != old.broker_order_id
    assert stop.trigger > old.trigger and stop.rearms == 0


def test_a_busy_closed_or_off_side_is_refused(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    target = rows(factory, "target", 0)[0]
    with factory() as session:
        session.get(BotExit, target.id).state = "firing"
        session.commit()
    out = bot_manage.change_stop(deps, run_id, 0, "200")
    assert not out.ok and "close is running" in out.message
    assert not bot_manage.change_target(deps, run_id, 0, "25").ok
    with factory() as session:
        session.get(BotExit, target.id).state = "filled"
        session.commit()
    out = bot_manage.change_stop(deps, run_id, 0, "200")
    assert not out.ok and "already closed" in out.message
    turn_off_in_db(factory, run_id, 1)
    out = bot_manage.change_stop(deps, run_id, 1, "200")
    assert not out.ok and "Exits are off" in out.message
    assert not bot_manage.change_target(deps, run_id, 1, "25").ok
    assert not bot_manage.change_stop(deps, run_id, 0, "5000").ok  # out of range
    assert [c[0] for c in deps.tasty.calls].count("cancel") == 0


def test_a_stop_change_cut_off_part_way_is_recovered(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    # The app died after marking the stop and sending its cancel.
    deps.tasty.cancel_order("5WX01234", old.broker_order_id)
    with factory() as session:
        row = session.get(BotExit, old.id)
        row.state = "cancelled"
        row.log = [*row.log, {"event": bot_exits.MOVING_STOP_BY_YOU, "to": "8.10"}]
        session.commit()
    bot_exits.monitor_pass(deps)
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.broker_order_id != old.broker_order_id
    assert stop.trigger == old.trigger  # placed again at its old trigger
    assert "IC: a stop move was cut off" in titles(deps)
    assert override(factory, run_id, 0) is None


def test_changing_the_target_reprices_the_armed_target(deps, factory):
    run_id = trade(deps, factory)
    old, other = rows(factory, "target", 0)[0], rows(factory, "target", 1)[0]
    out = bot_manage.change_target(deps, run_id, 0, "25")
    assert out.ok, out.message
    target = rows(factory, "target", 0)[0]
    assert target.state == "armed" and target.limit > old.limit  # keep 25%: close dearer
    assert target.log[-1]["event"] == "moved by you"
    assert override(factory, run_id, 0).target_pct == D("25")
    assert rows(factory, "target", 1)[0].limit == other.limit  # the other side as it was


def test_changing_the_target_arms_one_when_there_is_none(deps, factory):
    run_id = trade(deps, factory, target_pct=None)
    assert rows(factory, "target") == []
    out = bot_manage.change_target(deps, run_id, 1, "40")
    assert out.ok, out.message
    [target] = rows(factory, "target")
    assert (target.group_position, target.state, target.contracts) == (1, "armed", 1)


# --- Task 5: exits off and on ----------------------------------------------------------


def test_exits_off_cancels_the_stop_disarms_the_target_and_saves_it(deps, factory):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    out = bot_manage.exits_off(deps, run_id, 0)
    assert out.ok and "not protected" in out.message
    assert ("cancel", stop.broker_order_id) in deps.tasty.calls
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "cancelled" and stop.log[-1]["event"] == bot_exits.EXITS_OFF
    target = rows(factory, "target", 0)[0]
    assert target.state == "cancelled" and target.log[-1]["event"] == bot_exits.EXITS_OFF
    assert override(factory, run_id, 0).exits_off is True
    assert rows(factory, "stop", 1)[0].state == "working"  # the other side untouched
    assert rows(factory, "target", 1)[0].state == "armed"
    again = bot_manage.exits_off(deps, run_id, 0)
    assert not again.ok and "already off" in again.message


def test_exits_off_whose_cancel_is_not_confirmed_turns_nothing_off(deps, factory):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    deps.tasty.stuck.add(stop.broker_order_id)
    out = bot_manage.exits_off(deps, run_id, 0)
    assert not out.ok and "wasn't confirmed" in out.message and "still on" in out.message
    row = rows(factory, "stop", 0)[0]
    assert (row.state, row.broker_order_id) == ("working", stop.broker_order_id)
    assert rows(factory, "target", 0)[0].state == "armed"
    assert override(factory, run_id, 0).exits_off is False


def test_exits_off_when_the_stop_filled_first_leaves_it_to_the_monitor(deps, factory):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    deps.tasty.fill(stop.broker_order_id, D("5.20"))
    out = bot_manage.exits_off(deps, run_id, 0)
    assert not out.ok and "filled" in out.message
    assert rows(factory, "stop", 0)[0].state == "working"
    assert override(factory, run_id, 0).exits_off is False
    bot_exits.monitor_pass(deps)  # sees the fill and handles the wing
    assert rows(factory, "stop", 0)[0].state == "filled"
    assert [w.state for w in rows(factory, "wing", 0)] in (["working"], ["filled"])


def test_while_exits_are_off_no_stop_or_target_but_the_timed_close_still_closes(deps, factory):
    """Exits off: nothing places a stop or fires a target; the timed close still runs
    (bots slice 18, the author's ruling, as Tracker2)."""
    run_id = trade(deps, factory, close_time="15:45")
    stop = rows(factory, "stop", 0)[0]
    assert bot_manage.exits_off(deps, run_id, 0).ok
    sent = len(placed(deps))
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="2.50")  # side 1 short of its target
    deps.hub.set(stop.short_symbol, "0.00", "0.05", ny("11:00"))  # side 0 is past its target
    for _ in range(3):
        bot_exits.watch_pass(deps)
        bot_exits.monitor_pass(deps)
    assert bot_exits.rearm(deps, stop.id) is False
    side0 = {c[2]["legs"][0]["symbol"] for c in placed(deps)[sent:]}
    assert stop.short_symbol not in side0  # no stop and no target close for side 0
    assert {r.state for r in rows(factory, side=0) if r.kind in ("stop", "target")} == {"cancelled"}
    at(deps, "15:45")
    quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert [t.group_position for t in rows(factory, "timed")] == [0, 1]


def test_the_integrity_check_reports_exits_off_not_a_missing_stop(deps, factory):
    from datetime import timedelta

    from optionsjourney.services import bot_integrity

    run_id = trade(deps, factory)
    assert bot_manage.exits_off(deps, run_id, 0).ok
    deps.now = lambda: OPEN_TIME + timedelta(minutes=10)
    found = bot_integrity.check(deps)
    assert [(f.check, f.subject) for f in found] == [("exits_off", "side 0")]
    assert "Bot integrity: IC" in titles(deps)
    assert "turned its exits off" in found[0].summary


def test_turning_exits_on_places_the_stop_after_the_live_check(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    bot_manage.exits_off(deps, run_id, 0)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.exits_on(deps, run_id, 0, "")
    assert out.ok, out.message
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.broker_order_id != old.broker_order_id
    assert stop.trigger == old.trigger and stop.rearms == 0
    assert rows(factory, "target", 0)[0].state == "armed"
    assert override(factory, run_id, 0).exits_off is False


def test_turning_exits_on_refuses_a_stop_that_would_fire_at_once(deps, factory):
    run_id = trade(deps, factory)
    bot_manage.exits_off(deps, run_id, 0)
    sent = len(placed(deps))
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="9.00")
    out = bot_manage.exits_on(deps, run_id, 0, "")
    assert not out.ok and "would fire at once" in out.message and "stay off" in out.message
    assert len(placed(deps)) == sent
    assert override(factory, run_id, 0).exits_off is True
    assert rows(factory, "target", 0)[0].state == "cancelled"
    quotes(deps, factory, "11:00", short_mid="0.60")
    out = bot_manage.exits_on(deps, run_id, 0, "")  # at a sane price it goes back on
    assert out.ok


def test_turning_exits_on_at_a_new_stop(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    bot_manage.exits_off(deps, run_id, 0)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.exits_on(deps, run_id, 0, "200")
    assert out.ok, out.message
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.trigger > old.trigger
    saved = override(factory, run_id, 0)
    assert saved.exits_off is False and saved.stop_pct == D("200")
    deps.hub.quotes.clear()
    bot_manage.exits_off(deps, run_id, 0)
    out = bot_manage.exits_on(deps, run_id, 0, "")
    assert not out.ok and "no fresh price" in out.message
    assert override(factory, run_id, 0).exits_off is True


def _cut_off_turning_off(factory, deps):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=0, exits_off=True,
                                     changed_at=OPEN_TIME, log=[]))  # fmt: skip
        row = session.get(BotExit, stop.id)
        row.state = "cancelled"
        row.log = [*row.log, {"event": bot_exits.TURNING_OFF}]
        target = rows(factory, "target", 0)[0]
        t = session.get(BotExit, target.id)
        t.state, t.log = "cancelled", [*t.log, {"event": bot_exits.EXITS_OFF}]
        session.commit()
    return run_id, stop


def test_an_exits_off_cut_off_part_way_is_recovered(deps, factory):
    # The stop is still there: exits stay on.
    run_id, stop = _cut_off_turning_off(factory, deps)
    bot_manage.recover(deps)
    assert rows(factory, "stop", 0)[0].state == "working"
    assert rows(factory, "target", 0)[0].state == "armed"
    assert override(factory, run_id, 0).exits_off is False
    assert any("still there" in e.detail for e in deps.seen)


def test_an_exits_off_cut_off_after_its_cancel_finishes(deps, factory):
    run_id, stop = _cut_off_turning_off(factory, deps)
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    bot_manage.recover(deps)
    row = rows(factory, "stop", 0)[0]
    assert row.state == "cancelled" and row.log[-1]["event"] == bot_exits.EXITS_OFF
    assert override(factory, run_id, 0).exits_off is True
    assert any("not protected" in e.detail for e in deps.seen)


# --- Task 6: close a quantity ------------------------------------------------------------


def stops_sent(deps):
    return [c[2] for c in placed(deps, "Stop")]


def test_closing_one_of_three_re_protects_two_even_while_the_broker_still_shows_three(
    deps, factory
):
    run_id = trade(deps, factory, contracts=3)
    old = rows(factory, "stop", 0)[0]
    assert old.contracts == 3
    stale = dict(deps.tasty.held)
    deps.tasty.option_positions = lambda account: dict(stale)  # the broker lags behind
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok, out.message
    [close] = rows(factory, "close")
    assert (close.state, close.contracts) == ("reduced", 1)
    assert any(e.get("closed") == 1 for e in close.log)
    [order] = side_closes(deps)
    assert order["legs"][0]["quantity"] == 1
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.contracts == 2 and stop.rearms == 0
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 2  # never more than is left
    target = rows(factory, "target", 0)[0]
    assert (target.state, target.contracts) == ("armed", 2)
    assert rows(factory, "stop", 1)[0].contracts == 3  # the other side untouched
    assert "IC: closed 1 of 3" in titles(deps)
    # A second close sizes from what's left (2), not the trade's 3.
    out = bot_manage.close_part(deps, run_id, 0, "3")
    assert not out.ok and "Only 2" in out.message


def test_closing_everything_closes_the_side(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok, out.message
    assert [c.state for c in rows(factory, "close")] == ["filled"]
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "cancelled" and len(stops_sent(deps)) == 2  # not put back
    assert rows(factory, "target", 0)[0].state == "cancelled"
    assert "IC: close filled" in titles(deps)
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert not out.ok and "already closed" in out.message


def test_a_close_of_more_than_is_held_is_refused(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    sent = len(deps.tasty.calls)
    for text, why in (("4", "Only 3"), ("0", "1 or more"), ("x", "whole number")):
        out = bot_manage.close_part(deps, run_id, 0, text)
        assert not out.ok and why in out.message
    assert len(deps.tasty.calls) == sent and rows(factory, "close") == []


def test_no_close_outside_the_session_or_under_freeze(deps, factory):
    from optionsjourney.services import orders

    run_id = trade(deps, factory)
    at(deps, "16:30")
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert not out.ok and "market is open" in out.message
    at(deps, "11:00")
    orders.set_switch(factory, "freeze", True)
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert not out.ok and "Freeze" in out.message
    assert rows(factory, "close") == []


def test_a_target_cannot_fire_while_a_close_runs_and_a_close_waits_for_a_target(deps, factory):
    run_id = trade(deps, factory)
    jobs = []
    deps.background = lambda fn, *args: jobs.append((fn, args))
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    assert [c.state for c in rows(factory, "close")] == ["firing"]
    target = rows(factory, "target", 0)[0]
    assert target.state == "cancelled"
    bot_exits.fire_target(deps, target.id)  # the watch can't fire it
    again = bot_manage.close_part(deps, run_id, 0, "1")  # nor a second close
    assert not again.ok and "close is running" in again.message
    assert len(jobs) == 1 and side_closes(deps) == []
    other = rows(factory, "target", 1)[0]
    with factory() as session:
        session.get(BotExit, other.id).state = "firing"  # its target is closing side 1
        session.commit()
    out = bot_manage.close_part(deps, run_id, 1, "1")
    assert not out.ok and "close is running" in out.message
    fn, args = jobs[0]
    fn(*args)
    assert len(side_closes(deps)) == 1


def test_a_close_that_gives_up_re_protects_what_is_still_open(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    deps.tasty.close = "partial"  # the two-lot close fills 1, then gives up
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.close_part(deps, run_id, 0, "2").ok
    [close] = rows(factory, "close")
    assert close.state == "reduced" and any(e.get("closed") == 1 for e in close.log)
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.contracts) == ("working", 2)
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 2
    assert rows(factory, "target", 0)[0].contracts == 2
    alert = next(e for e in deps.seen if e.title == "IC: close didn't fill")
    assert "closed 1 of 2" in alert.detail and "stop is back for the 2" in alert.detail


def test_a_close_that_closes_nothing_puts_everything_back(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    deps.tasty.close = "work"
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    assert [c.state for c in rows(factory, "close")] == ["failed"]
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.contracts) == ("working", 3)
    assert (rows(factory, "target", 0)[0].state, rows(factory, "target", 0)[0].contracts) == (
        "armed",
        3,
    )


def test_a_running_close_can_be_cancelled_and_re_protects(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    jobs = []
    deps.background = lambda fn, *args: jobs.append((fn, args))
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    [close] = rows(factory, "close")
    assert bot_exits.request_cancel(factory, run_id, close.id, ny("11:00"))
    fn, args = jobs[0]
    fn(*args)
    assert rows(factory, "close")[0].state == "cancelled"
    assert side_closes(deps) == []
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.contracts) == ("working", 3)
    assert rows(factory, "target", 0)[0].state == "armed"
    assert "IC: Close cancelled" in titles(deps)


def test_a_close_with_exits_off_re_protects_nothing(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    assert bot_manage.exits_off(deps, run_id, 0).ok
    sent = len(stops_sent(deps))
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok and "not protected" in out.message
    assert rows(factory, "close")[0].state == "reduced"
    assert len(stops_sent(deps)) == sent
    assert rows(factory, "target", 0)[0].state == "cancelled"
    assert override(factory, run_id, 0).exits_off is True


def test_a_close_cut_off_by_a_restart_is_recovered(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    jobs = []
    deps.background = lambda fn, *args: jobs.append((fn, args))  # the job never runs
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    bot_exits.FIRING.clear()  # the app restarted
    bot_exits.recover_targets(deps)
    assert rows(factory, "close")[0].state == "failed"
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.contracts == 3  # its cancel was never sent
    assert rows(factory, "target", 0)[0].state == "armed"
    assert "IC: close error" in titles(deps)


def test_turning_exits_on_after_a_close_protects_only_what_is_left(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    assert bot_manage.exits_off(deps, run_id, 0).ok
    stale = dict(deps.tasty.held)
    deps.tasty.option_positions = lambda account: dict(stale)  # the broker lags behind
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    out = bot_manage.exits_on(deps, run_id, 0, "")
    assert out.ok, out.message
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.contracts) == ("working", 2)
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 2
    assert rows(factory, "target", 0)[0].contracts == 2


def test_a_timed_close_after_a_part_close_is_sized_to_what_is_left(deps, factory):
    run_id = trade(deps, factory, contracts=3, close_time="15:45")
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    stale = dict(deps.tasty.held)
    stale[next(iter(k for k, v in stale.items() if v == -2))] = -3  # the broker lags
    deps.tasty.option_positions = lambda account: dict(stale)
    at(deps, "15:45")
    quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    timed = rows(factory, "timed", 0)[0]
    assert timed.contracts == 2
    assert side_closes(deps)[-2]["legs"][0]["quantity"] == 2  # side 0's timed close


# --- Final review fixes -----------------------------------------------------------------


def _mark(factory, row_id, state, event, **changes):
    with factory() as session:
        row = session.get(BotExit, row_id)
        row.state = state
        row.log = [*row.log, {"at": ny("11:00").isoformat(), "event": event}]
        for name, value in changes.items():
            setattr(row, name, value)
        session.commit()


def test_a_side_whose_close_may_still_be_live_refuses_a_stop_change_and_a_close(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    stop, target = rows(factory, "stop", 0)[0], rows(factory, "target", 0)[0]
    # A target close ended unclear: its order may be live, so its stop was left cancelled.
    _mark(factory, stop.id, "cancelled", bot_exits.CANCELLED_FOR_TARGET)
    _mark(factory, target.id, "failed", "unclear: nothing more sent")
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    sent = len(deps.tasty.calls)
    for out in (
        bot_manage.change_stop(deps, run_id, 0, "200"),
        bot_manage.close_part(deps, run_id, 0, "1"),
    ):
        assert not out.ok and "may still be working at your broker" in out.message
    assert [c for c in deps.tasty.calls[sent:] if c[0] in ("place", "cancel", "replace")] == []
    assert rows(factory, "close") == []


def test_a_cut_off_stop_move_under_freeze_refuses_a_stop_change_and_a_close(deps, factory):
    from optionsjourney.services import orders

    run_id = trade(deps, factory, contracts=3)
    stop = rows(factory, "stop", 0)[0]
    _mark(factory, stop.id, "cancelled", bot_exits.MOVING_STOP)  # cut off mid-move
    orders.set_switch(factory, "freeze", True)
    bot_exits.monitor_pass(deps)  # returns at once under Freeze: nothing recovers it
    orders.set_switch(factory, "freeze", False)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    sent = len(deps.tasty.calls)
    for out in (
        bot_manage.change_stop(deps, run_id, 0, "200"),
        bot_manage.close_part(deps, run_id, 0, "1"),
    ):
        assert not out.ok and "may still be working at your broker" in out.message
    assert [c for c in deps.tasty.calls[sent:] if c[0] in ("place", "cancel", "replace")] == []


def test_a_stop_cancelled_for_an_unknown_reason_is_not_replaced(deps, factory):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    _mark(factory, stop.id, "cancelled", "something the app can't vouch for")
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.change_stop(deps, run_id, 0, "200")
    assert not out.ok and "may still be working" in out.message
    assert len(placed(deps, "Stop")) == 2


def test_a_time_actions_target_move_switches_a_target_off_back_on(deps, factory):
    """Tracker2's write_time_action_override (profit_target_off = False), the author's
    ruling: the action's target move turns the target back on at its %, once."""
    action = TimeActionDef("dte", 0, "14:00", target_pct=D("25"))
    run_id = trade(deps, factory, use_time_actions=True, time_actions=(action,))
    assert bot_manage.target_off(deps, run_id, 0).ok
    at(deps, "14:00")
    quotes(deps, factory, "14:00")
    bot_timed.actions_pass(deps)
    [action_row] = rows(factory, "action", 0)
    assert action_row.state == "filled" and "the target is back on" in action_row.log[0]["notes"][0]
    assert [t.state for t in rows(factory, "target", 0)] == ["cancelled", "armed"]
    assert override(factory, run_id, 0).target_off is False
    bot_timed.actions_pass(deps)
    assert len(rows(factory, "action", 0)) == 1  # never twice


def test_exits_off_when_the_stop_filled_first_leaves_the_target_off(deps, factory):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    deps.tasty.fill(stop.broker_order_id, D("5.20"))
    assert not bot_manage.exits_off(deps, run_id, 0).ok
    assert rows(factory, "target", 0)[0].state == "cancelled"  # _stop_filled's to settle


def test_a_refused_stop_change_saves_nothing(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    assert bot_manage.exits_off(deps, run_id, 0).ok
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.close_part(deps, run_id, 0, "1").ok  # left at 3 on the stop row
    _mark(factory, rows(factory, "stop", 0)[0].id, "failed", "re-arm limit reached")
    with factory() as session:
        session.get(BotTradeOverride, override(factory, run_id, 0).id).exits_off = False
        session.commit()
    deps.hub.quotes.clear()  # no fresh price: refused
    assert not bot_manage.change_stop(deps, run_id, 0, "200").ok
    assert rows(factory, "stop", 0)[0].contracts == 3


def test_turning_exits_on_at_a_new_stop_writes_it_with_the_switch(deps, factory, monkeypatch):
    run_id = trade(deps, factory)
    bot_manage.exits_off(deps, run_id, 0)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")

    def crash(*args, **kwargs):
        raise RuntimeError("the app died")

    monkeypatch.setattr(bot_exits, "_place_pending", crash)
    monkeypatch.setattr(bot_exits, "place_stop_at", crash)
    with pytest.raises(RuntimeError):
        bot_manage.exits_on(deps, run_id, 0, "200")
    stop = rows(factory, "stop", 0)[0]
    assert override(factory, run_id, 0).exits_off is False
    assert stop.state == "pending" and stop.broker_order_id is None  # the monitor places it
    assert stop.log[-1]["stop_calc"]["moved_by"] == "you"
    monkeypatch.undo()
    bot_exits.monitor_pass(deps)
    assert rows(factory, "stop", 0)[0].state == "working"


def test_a_new_stop_percent_on_a_side_with_no_stop_is_refused(deps, factory):
    run_id = trade(deps, factory)
    bot_manage.exits_off(deps, run_id, 0)
    with factory() as session:
        for row in session.scalars(select(BotExit).where(BotExit.kind == "stop")):
            session.delete(row)
        session.commit()
    out = bot_manage.exits_on(deps, run_id, 0, "200")
    assert not out.ok and "no stop" in out.message
    assert override(factory, run_id, 0).exits_off is True


def test_a_time_action_clears_the_chip_for_the_level_it_moves(deps, factory):
    action = TimeActionDef("dte", 0, "14:00", stop_pct=D("150"), target_pct=D("40"))
    run_id = trade(deps, factory, use_time_actions=True, time_actions=(action,))
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="2.50")
    assert bot_manage.change_stop(deps, run_id, 0, "200").ok
    assert bot_manage.change_target(deps, run_id, 0, "25").ok
    at(deps, "14:00")
    quotes(deps, factory, "14:00", short_mid="2.50")
    bot_timed.actions_pass(deps)
    saved = override(factory, run_id, 0)
    assert (saved.stop_pct, saved.target_pct) == (None, None)


def test_a_time_action_sizes_a_new_target_to_what_is_left(deps, factory):
    action = TimeActionDef("dte", 0, "14:00", target_pct=D("25"))
    run_id = trade(
        deps, factory, target_pct=None, contracts=3, use_time_actions=True, time_actions=(action,)
    )
    stop = rows(factory, "stop", 0)[0]
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    with factory() as session:  # a side with no stop and no target
        for row in session.scalars(select(BotExit).where(BotExit.group_position == 0)):
            session.delete(row)
        session.commit()
    at(deps, "11:00")
    deps.hub.set(stop.short_symbol, "0.55", "0.65", ny("11:00"))
    deps.hub.set(stop.long_symbol, "0.00", "0.10", ny("11:00"))
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    assert rows(factory, "close")[0].state == "reduced"
    at(deps, "14:00")
    quotes(deps, factory, "14:00", short_mid="2.50")
    bot_timed.actions_pass(deps)
    assert rows(factory, "target", 0)[0].contracts == 2


def test_a_cut_off_close_records_the_fills_it_can_confirm(deps, factory):
    from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus, Side

    run_id = trade(deps, factory, contracts=3)
    jobs = []
    deps.background = lambda fn, *args: jobs.append((fn, args))  # the job never runs
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.close_part(deps, run_id, 0, "2").ok
    [close] = rows(factory, "close")
    # Its last order filled 1 of 2 and is gone (cancelled) at the broker.
    legs = (BrokerLeg(close.short_symbol, Side.BUY, False, 2, 1, D("1")),
            BrokerLeg(close.long_symbol, Side.SELL, False, 2, 1, D("0.05")))  # fmt: skip
    deps.tasty.orders["999"] = BrokerOrder("999", OrderStatus.CANCELLED, legs, None, ny("11:00"))
    with factory() as session:
        row = session.get(BotExit, close.id)
        row.broker_order_id = "999"
        row.log = [*row.log, {"stage": "close", "open_at_start": 3}]
        session.commit()
    bot_exits.FIRING.clear()  # the app restarted
    bot_exits.recover_targets(deps)
    row = rows(factory, "close")[0]
    assert row.state == "reduced" and any(e.get("closed") == 1 for e in row.log)
    with factory() as session:
        from optionsjourney.db.models import BotRun

        assert bot_exits.group_units(session, session.get(BotRun, run_id), 0) == 2


# --- Re-review: settling a held-back side --------------------------------------------------


def _unclear_target_close(deps, factory, run_id, order_state):
    """Side 0's target close ended unclear: its stop cancelled at the broker and left
    cancelled for the target, the target failed with its last order (id 777) recorded."""
    from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus, Side

    stop, target = rows(factory, "stop", 0)[0], rows(factory, "target", 0)[0]
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    status = {"live": OrderStatus.WORKING, "dead": OrderStatus.CANCELLED,
              "filled": OrderStatus.FILLED}[order_state]  # fmt: skip
    done = 1 if order_state == "filled" else 0
    legs = (BrokerLeg(stop.short_symbol, Side.BUY, False, 1, 1 - done, D("1")),
            BrokerLeg(stop.long_symbol, Side.SELL, False, 1, 1 - done, D("0.05")))  # fmt: skip
    deps.tasty.orders["777"] = BrokerOrder("777", status, legs, None, ny("11:00"))
    _mark(factory, stop.id, "cancelled", bot_exits.CANCELLED_FOR_TARGET)
    _mark(factory, target.id, "failed", "unclear: nothing more sent", broker_order_id="777")
    return stop, target


def _no_position_reads(deps):
    def refuse(account):
        raise AssertionError("settle must never read positions")

    deps.tasty.option_positions = refuse


def test_a_held_back_side_whose_close_is_over_is_released_without_any_order(deps, factory):
    run_id = trade(deps, factory)
    stop, _ = _unclear_target_close(deps, factory, run_id, "filled")
    sent = len(deps.tasty.calls)
    _no_position_reads(deps)
    bot_exits.monitor_pass(deps)
    real = FakeTasty.option_positions
    deps.tasty.option_positions = lambda account: real(deps.tasty, account)
    assert [c for c in deps.tasty.calls[sent:] if c[0] in ("place", "cancel", "replace")] == []
    assert rows(factory, "stop", 0)[0].state == "cancelled"  # no stop put back
    assert rows(factory, "target", 0)[0].state == "failed"  # no target re-armed
    alert = next(e for e in deps.seen if e.title == "IC: Call side is no longer held back")
    assert "didn't put a stop back" in alert.detail and "set a stop" in alert.detail
    bot_exits.monitor_pass(deps)
    assert titles(deps).count("IC: Call side is no longer held back") == 1
    # Released: your own actions work again, with their own checks.
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.change_stop(deps, run_id, 0, "200")
    assert out.ok, out.message
    assert rows(factory, "stop", 0)[0].state == "working"


def test_a_held_back_side_whose_close_may_be_live_stays_held_and_alerts_once(deps, factory):
    run_id = trade(deps, factory)
    _unclear_target_close(deps, factory, run_id, "live")
    _no_position_reads(deps)
    for _ in range(3):
        bot_exits.monitor_pass(deps)
    assert rows(factory, "stop", 0)[0].state == "cancelled"
    assert len(placed(deps, "Stop")) == 2
    assert titles(deps).count("IC: a side is held back") == 1
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert not bot_manage.change_stop(deps, run_id, 0, "200").ok


def test_a_side_whose_close_job_is_still_finishing_is_skipped(deps, factory):
    run_id = trade(deps, factory)
    _, target = _unclear_target_close(deps, factory, run_id, "dead")
    bot_exits.FIRING.add(target.id)  # its job hasn't let go yet
    bot_exits.monitor_pass(deps)
    out = bot_manage.recheck(deps, run_id, 0)
    assert not out.ok and "still finishing" in out.message
    assert not any("held back" in t for t in titles(deps))
    bot_exits.FIRING.discard(target.id)
    bot_exits.monitor_pass(deps)
    assert "IC: Call side is no longer held back" in titles(deps)


def test_a_held_back_side_with_no_stop_is_released_once(deps, factory):
    run_id = trade(deps, factory)
    stop, target = _unclear_target_close(deps, factory, run_id, "live")
    with factory() as session:
        session.delete(session.get(BotExit, stop.id))
        session.commit()
    for _ in range(3):
        bot_exits.monitor_pass(deps)
    assert titles(deps).count("IC: a side is held back") == 1
    deps.tasty.orders["777"] = replace(deps.tasty.orders["777"], status=OrderStatus.CANCELLED)
    for _ in range(3):
        bot_exits.monitor_pass(deps)
    assert titles(deps).count("IC: Call side is no longer held back") == 1
    assert len(placed(deps, "Stop")) == 2


def test_the_recheck_button_reports_and_releases(deps, factory):
    run_id = trade(deps, factory)
    _unclear_target_close(deps, factory, run_id, "live")
    _no_position_reads(deps)
    out = bot_manage.recheck(deps, run_id, 0)
    assert not out.ok and "may still be working" in out.message and "777" in out.message
    deps.tasty.orders["777"] = replace(deps.tasty.orders["777"], status=OrderStatus.CANCELLED)
    out = bot_manage.recheck(deps, run_id, 0)
    assert out.ok and "set a stop" in out.message
    assert rows(factory, "stop", 0)[0].state == "cancelled" and len(placed(deps, "Stop")) == 2
    assert not bot_manage.recheck(deps, run_id, 1).ok  # nothing held back there


def test_a_timed_close_does_not_claim_a_held_back_side(deps, factory):
    run_id = trade(deps, factory, close_time="15:45")
    _unclear_target_close(deps, factory, run_id, "live")
    at(deps, "15:45")
    quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert [t.group_position for t in rows(factory, "timed")] == [1]
    assert all(c["legs"][0]["symbol"] != rows(factory, "stop", 0)[0].short_symbol
               for c in side_closes(deps))  # fmt: skip


def test_exits_on_with_a_new_percent_says_old_to_new(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    bot_manage.exits_off(deps, run_id, 0)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.exits_on(deps, run_id, 0, "200")
    assert out.ok and f"{old.trigger} →" in out.message


# --- Slice 13: any group from the journal -----------------------------------------------

from optionsjourney.bots.exit_math import group_entry  # noqa: E402
from optionsjourney.db.models import BotRun  # noqa: E402
from optionsjourney.trading.orders import Side, plain  # noqa: E402
from tests.services.bot_fakes import SHAPE_MATRIX, make_shape  # noqa: E402


def shape_trade(deps, factory, key, contracts=None):
    """A live run of a preset shape (bot_fakes.make_shape), a 50% stop and target on
    every group; `contracts` sizes it past validation's one contract."""
    bot_id = make_shape(factory, key, live=True, stop=D("50"), target=D("50"))
    if contracts is not None:
        from optionsjourney.db.models import Bot

        with factory() as session:
            row = session.get(Bot, bot_id)
            row.contracts, row.max_per_trade, row.max_per_day = contracts, 40, 40
            session.commit()
    return bot_entry.fire(deps, bot_id, "09:47")


def quote_group(deps, factory, run_id, hhmm, mids=None):
    """Fresh quotes on every leg of the run at `hhmm`, 0.05 either side of its mid (the
    fill, unless `mids` gives one)."""
    with factory() as session:
        run = session.get(BotRun, run_id)
        fills = {f["symbol"]: D(f["fill_price"]) for f in run.filled_legs}
    for symbol, fill in fills.items():
        mid = D((mids or {}).get(symbol, fill))
        deps.hub.set(symbol, max(mid - D("0.05"), D(0)), mid + D("0.05"), ny(hhmm))


def run_entry(factory, run_id, position=0):
    with factory() as session:
        run = session.get(BotRun, run_id)
        legs, _ = bot_exits.exit_math.group_legs(run.legs, position)
        fills = {f["symbol"]: D(f["fill_price"]) for f in run.filled_legs}
    return legs, group_entry(legs, fills)


def test_changing_a_watched_groups_stop_moves_its_level_and_sends_nothing(deps, factory):
    # A long call bought for E (a debit, so E < 0): its 50% stop watches P&L -|E|/2. Your
    # 30% reads "% of the entry price lost": the level moves to -|E| x 0.30, checked on
    # the group's live P&L (0 at the fill price), and nothing is cancelled or sent.
    run_id = shape_trade(deps, factory, "long_call")
    (leg,), entry = run_entry(factory, run_id)
    assert entry < 0
    (stop,) = rows(factory, "stop")
    assert stop.watched and stop.state == bot_exits.WATCHING
    sent = len(deps.tasty.calls)
    at(deps, "11:00")
    quote_group(deps, factory, run_id, "11:00")
    out = bot_manage.change_stop(deps, run_id, 0, "30")
    assert out.ok, out.message
    want = (-abs(entry) * D("0.30")).quantize(D("0.01"))
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.trigger) == (bot_exits.WATCHING, want)
    assert len(deps.tasty.calls) == sent  # nothing cancelled, nothing placed
    assert override(factory, run_id, 0).stop_pct == D("30")
    # The call down 40% of what it cost: a stop at 30% lost would fire at once.
    quote_group(deps, factory, run_id, "11:01", {leg.symbol: -entry * D("0.60")})
    at(deps, "11:01")
    out = bot_manage.change_stop(deps, run_id, 0, "35")
    assert not out.ok and "fire at once" in out.message
    assert rows(factory, "stop")[0].trigger == want and len(deps.tasty.calls) == sent


def test_a_watched_groups_stop_change_without_fresh_prices_is_refused(deps, factory):
    run_id = shape_trade(deps, factory, "long_call")
    (before,) = rows(factory, "stop")
    at(deps, "11:00")
    out = bot_manage.change_stop(deps, run_id, 0, "30")
    assert not out.ok and "no fresh price for the group's legs" in out.message
    assert rows(factory, "stop")[0].trigger == before.trigger


def test_closing_part_of_a_fly_closes_whole_units(deps, factory):
    # A 1x2x1 long call butterfly of 3 units: the broker shows only 5 of the body's 6
    # contracts, so 2 whole units are held. 3 is refused; 2 closes 2, 4 and 2.
    run_id = shape_trade(deps, factory, "long_call_butterfly", contracts=3)
    legs, _ = run_entry(factory, run_id)
    body = next(leg for leg in legs if leg.ratio == 2)
    deps.tasty.held[plain(body.symbol)] = -5
    at(deps, "11:00")
    quote_group(deps, factory, run_id, "11:00")
    out = bot_manage.close_part(deps, run_id, 0, "3")
    assert not out.ok and "Only 2" in out.message
    out = bot_manage.close_part(deps, run_id, 0, "2")
    assert out.ok, out.message
    assert "Closing 2 of 2" in out.message
    (close,) = rows(factory, "close")
    assert close.contracts == 2
    closes = [c[2] for c in placed(deps) if c[2]["legs"][0]["action"].endswith("Close")]
    # decision 0048: its profit target rested at TastyTrade from the fill, cancelled first
    rested, sent = [c for c in closes if c["order-type"] != "Stop"]
    assert rested["time-in-force"] == "GTC" and any(c[0] == "cancel" for c in deps.tasty.calls)
    assert sorted(int(leg["quantity"]) for leg in sent["legs"]) == [2, 2, 4]


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_can_be_closed_from_the_journal(deps, factory, key):
    run_id = shape_trade(deps, factory, key)
    legs, _ = run_entry(factory, run_id)
    at(deps, "11:00")
    quote_group(deps, factory, run_id, "11:00")
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok, out.message
    (close,) = rows(factory, "close")
    assert close.state == "filled", close.log
    sent = [c[2] for c in placed(deps) if c[2]["order-type"] != "Stop"
            and c[2]["legs"][0]["action"].endswith("Close")]  # fmt: skip
    # decision 0048: a group whose stop can't rest has its target resting (one GTC closing
    # limit from the fill), cancelled before the close
    rested = [b for b in sent if b["time-in-force"] == "GTC"]
    resting_stop = key in ("iron_condor", "put_credit_spread", "short_put")
    assert len(rested) == (0 if resting_stop else 1)
    assert resting_stop or any(c[0] == "cancel" for c in deps.tasty.calls)
    (order,) = [b for b in sent if b not in rested]
    want = [(leg.symbol, leg.ratio, "Buy to Close" if leg.side is Side.SELL else "Sell to Close")
            for leg in legs]  # fmt: skip
    assert sorted((b["symbol"], int(b["quantity"]), b["action"]) for b in order["legs"]) == sorted(
        want
    )


def test_the_run_page_marks_a_watched_group(deps, factory):
    run_id = shape_trade(deps, factory, "long_call")
    with factory() as session:
        bot_id = session.get(BotRun, run_id).bot_id
    bot = bot_store.load(factory, bot_id)
    (side,) = bot_manage.sides(factory, run_id, bot)
    assert side.watched and side.state == "open" and side.stop is not None
    condor = trade(deps, factory)
    with factory() as session:
        condor_bot = bot_store.load(factory, session.get(BotRun, condor).bot_id)
    assert [s.watched for s in bot_manage.sides(factory, condor, condor_bot)] == [False, False]


def test_a_watched_groups_exits_go_off_and_back_on_on_its_live_pnl(deps, factory):
    run_id = shape_trade(deps, factory, "long_call")
    (leg,), entry = run_entry(factory, run_id)
    (before,) = rows(factory, "stop")
    sent = len(deps.tasty.calls)
    assert bot_manage.exits_off(deps, run_id, 0).ok
    assert rows(factory, "stop")[0].state == "cancelled"
    at(deps, "11:00")
    # Down 60% of what it cost: the old level (50% lost) would fire at once.
    quote_group(deps, factory, run_id, "11:00", {leg.symbol: -entry * D("0.40")})
    out = bot_manage.exits_on(deps, run_id, 0)
    assert not out.ok and "fire at once" in out.message and "Exits stay off" in out.message
    quote_group(deps, factory, run_id, "11:00")
    out = bot_manage.exits_on(deps, run_id, 0)
    assert out.ok, out.message
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.trigger) == (bot_exits.WATCHING, before.trigger)
    # the stop: nothing at the broker, either way; decision 0048: its resting target is
    # cancelled for Exits off and placed again for Exits on
    new = deps.tasty.calls[sent:]
    assert [c[0] for c in new] == ["cancel", "preview", "place"]
    assert (new[-1][2]["time-in-force"], new[-1][2]["order-type"]) == ("GTC", "Limit")


def test_a_watched_strangles_stop_moves_with_a_far_short_at_no_bid(deps, factory):
    """Final review C1: _live_pnl reads a sold leg at 0 bid under a positive ask (mid
    ask / 2), so a journal stop change on it isn't refused "no fresh price"."""
    run_id = shape_trade(deps, factory, "short_strangle")
    legs, entry = run_entry(factory, run_id)
    assert entry > 0
    put = next(leg for leg in legs if leg.symbol.strip()[12] == "P")
    at(deps, "11:00")
    quote_group(deps, factory, run_id, "11:00", {put.symbol: "0.05"})  # 0 / 0.10
    out = bot_manage.change_stop(deps, run_id, 0, "30")
    assert out.ok, out.message
    (stop,) = rows(factory, "stop")
    assert stop.trigger == (-entry * D("0.30")).quantize(D("0.01"))


# --- Bots slice 18, task 1: the stop's and the target's own switches --------------------


def switch_in_db(factory, run_id, side, stop=False, target=False, tasty=None):
    """One or both switches off, as bot_manage leaves a side: the override set and the
    switched-off exit rows cancelled (a stop's order at the fake broker too)."""
    kinds = {"stop"} if stop else set()
    kinds |= {"target"} if target else set()
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=side, stop_off=stop,
                                     target_off=target, changed_at=OPEN_TIME, log=[]))  # fmt: skip
        for row in session.scalars(
            select(BotExit).where(BotExit.run_id == run_id, BotExit.group_position == side)
        ):
            if row.kind in kinds and row.state in ("working", "pending", "armed"):
                if tasty is not None and row.kind == "stop" and row.broker_order_id:
                    tasty.cancel_order("5WX01234", row.broker_order_id)
                row.state = "cancelled"
                row.log = [*row.log, {"event": bot_exits.EXITS_OFF}]
        session.commit()


def test_with_only_the_stop_off_the_target_still_fires_and_no_stop_is_placed(deps, factory):
    run_id = trade(deps, factory)
    switch_in_db(factory, run_id, 0, stop=True, tasty=deps.tasty)
    stop = rows(factory, "stop", 0)[0]
    assert bot_exits.rearm(deps, stop.id, first=True) is False
    assert "the stop is off" in rows(factory, "stop", 0)[0].log[-1]["event"]
    target = rows(factory, "target", 0)[0]
    assert target.state == "armed"
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="0.10", long_mid="0.02")
    bot_exits.fire_target(deps, target.id)
    assert len(side_closes(deps)) == 1  # the target closed the side


def test_with_only_the_target_off_the_stop_still_rearms_and_the_target_cant_fire(deps, factory):
    run_id = trade(deps, factory)
    switch_in_db(factory, run_id, 0, target=True)
    target = rows(factory, "target", 0)[0]
    with factory() as session:
        session.get(BotExit, target.id).state = "armed"  # as if armed by an older path
        session.commit()
    bot_exits.fire_target(deps, target.id)
    assert side_closes(deps) == []
    assert rows(factory, "target", 0)[0].state == "cancelled"
    stop = rows(factory, "stop", 0)[0]
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)  # gone at the broker
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.state, row.broker_order_id = "pending", None
        session.commit()
    assert bot_exits.rearm(deps, stop.id, first=True) is True
    assert rows(factory, "stop", 0)[0].state == "working"


def test_the_timed_close_runs_with_either_or_both_switches_off_and_says_so(deps, factory):
    """The author's ruling on question 4 (Tracker2: timed_close.fire_timed_closes reads
    only timed_close_overrides): exits off means no stop or target, not hands off."""
    run_id = trade(deps, factory, close_time="15:45")
    switch_in_db(factory, run_id, 0, stop=True, tasty=deps.tasty)
    switch_in_db(factory, run_id, 1, stop=True, target=True, tasty=deps.tasty)
    at(deps, "15:45")
    quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert [(t.group_position, t.state) for t in rows(factory, "timed")] == [
        (0, "filled"),
        (1, "filled"),
    ]
    said = [e for e in deps.seen if e.title == "IC: timed close on a side with exits off"]
    assert len(said) == 2 and "not the timed close" in said[0].detail
    assert said[0].routine is False


def test_a_time_actions_stop_move_switches_the_stop_back_on_after_the_market_check(deps, factory):
    """Tracker2's write_time_action_override (stop_loss_off = False): the stop comes back
    at the action's %, checked against the short's fresh ask first."""
    run_id = trade(deps, factory)
    switch_in_db(factory, run_id, 0, stop=True, tasty=deps.tasty)
    at(deps, "14:00")
    quotes(deps, factory, "14:00")
    action = TimeActionDef("dte", 0, "14:00", stop_pct=D("150"), target_pct=D("25"))
    state, notes = bot_exits.apply_time_action(deps, run_id, 0, action, 1, False)
    assert state == "filled" and notes[0].startswith("the stop is back on"), notes
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working"
    saved = override(factory, run_id, 0)
    assert (saved.stop_off, saved.target_off) == (False, False)


def test_a_time_actions_stop_move_that_would_fire_leaves_the_stop_off(deps, factory):
    run_id = trade(deps, factory)
    switch_in_db(factory, run_id, 0, stop=True, tasty=deps.tasty)
    at(deps, "14:00")
    quotes(deps, factory, "14:00", short_mid="9.00")  # the short is past a 150% stop
    sent = len(placed(deps, "Stop"))
    action = TimeActionDef("dte", 0, "14:00", stop_pct=D("150"))
    state, notes = bot_exits.apply_time_action(deps, run_id, 0, action, 1, False)
    assert state == "failed" and "the stop stays off" in notes[0] and "fire at once" in notes[0]
    assert rows(factory, "stop", 0)[0].state == "cancelled"
    assert override(factory, run_id, 0).stop_off is True
    assert len(placed(deps, "Stop")) == sent


def test_the_integrity_check_says_a_stop_switched_off_alone(deps, factory):
    from optionsjourney.services import bot_integrity

    run_id = trade(deps, factory)
    switch_in_db(factory, run_id, 0, stop=True)
    at(deps, "11:00")
    found, _ = bot_integrity.run_checks(deps, ny("11:00"))
    assert [(f.check, f.subject) for f in found] == [("exits_off", "side 0")]
    assert "switched its stop off" in found[0].summary


def test_an_override_with_exits_off_reads_both_switches(factory, deps):
    run_id = trade(deps, factory)
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=0, exits_off=True,
                                     changed_at=OPEN_TIME, log=[]))  # fmt: skip
        session.commit()
    saved = override(factory, run_id, 0)
    assert (saved.stop_off, saved.target_off, saved.exits_off) == (True, True, True)
    with factory() as session:
        assert bot_exits.exits_off(session, run_id, 0) and bot_exits.stop_off(session, run_id, 0)
        assert not bot_exits.exits_off(session, run_id, 1)


# --- Bots slice 18, task 2: the switches from the run page ------------------------------


def test_stop_off_cancels_the_stop_and_the_target_still_fires(deps, factory):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    out = bot_manage.stop_off(deps, run_id, 0)
    assert out.ok and "The stop is off" in out.message and "target still works" in out.message
    assert ("cancel", stop.broker_order_id) in deps.tasty.calls
    row = rows(factory, "stop", 0)[0]
    assert row.state == "cancelled" and row.log[-1]["event"] == bot_exits.EXITS_OFF
    saved = override(factory, run_id, 0)
    assert (saved.stop_off, saved.target_off) == (True, False)
    target = rows(factory, "target", 0)[0]
    assert target.state == "armed"
    again = bot_manage.stop_off(deps, run_id, 0)
    assert not again.ok and "already off" in again.message
    change = bot_manage.change_stop(deps, run_id, 0, "200")
    assert not change.ok and "stop is off" in change.message
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="0.10", long_mid="0.02")
    bot_exits.fire_target(deps, target.id)
    assert len(side_closes(deps)) == 1


def test_stop_off_whose_cancel_isnt_confirmed_changes_nothing(deps, factory):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    deps.tasty.stuck.add(stop.broker_order_id)
    out = bot_manage.stop_off(deps, run_id, 0)
    assert not out.ok and "wasn't confirmed" in out.message and "stop is still on" in out.message
    assert rows(factory, "stop", 0)[0].state == "working"
    saved = override(factory, run_id, 0)
    assert (saved.stop_off, saved.target_off) == (False, False)


def test_target_off_disarms_only_the_target_and_sends_nothing(deps, factory):
    run_id = trade(deps, factory)
    calls = len(deps.tasty.calls)
    out = bot_manage.target_off(deps, run_id, 0)
    assert out.ok and "The target is off" in out.message
    assert len(deps.tasty.calls) == calls
    assert rows(factory, "target", 0)[0].state == "cancelled"
    assert rows(factory, "stop", 0)[0].state == "working"
    assert not bot_manage.change_target(deps, run_id, 0, "25").ok
    on = bot_manage.target_on(deps, run_id, 0)
    assert on.ok and "target is back on" in on.message
    assert rows(factory, "target", 0)[0].state == "armed"
    assert override(factory, run_id, 0).target_off is False
    assert not bot_manage.target_on(deps, run_id, 0).ok  # already on


def test_stop_on_places_the_stop_after_the_live_check_and_leaves_the_target_off(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    assert bot_manage.target_off(deps, run_id, 0).ok
    assert bot_manage.stop_off(deps, run_id, 0).ok
    assert override(factory, run_id, 0).exits_off is True  # both off: exits off
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="9.00")
    refused = bot_manage.stop_on(deps, run_id, 0, "")
    assert not refused.ok and "would fire at once" in refused.message
    assert "stop stays off" in refused.message
    quotes(deps, factory, "11:00")
    out = bot_manage.stop_on(deps, run_id, 0, "")
    assert out.ok and out.message.startswith("The stop is back on"), out.message
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.trigger == old.trigger
    saved = override(factory, run_id, 0)
    assert (saved.stop_off, saved.target_off) == (False, True)
    assert rows(factory, "target", 0)[0].state == "cancelled"  # still off


def test_a_cut_off_stop_off_leaves_a_switched_off_target_off(deps, factory):
    run_id = trade(deps, factory)
    assert bot_manage.target_off(deps, run_id, 0).ok
    stop = rows(factory, "stop", 0)[0]
    with factory() as session:
        saved = session.get(BotTradeOverride, override(factory, run_id, 0).id)
        saved.stop_off = True
        row = session.get(BotExit, stop.id)
        row.state = "cancelled"
        row.log = [*row.log, {"event": bot_exits.TURNING_OFF, "with_target": False}]
        session.commit()
    bot_manage.recover(deps)  # the stop is still at the broker: it stays on
    assert rows(factory, "stop", 0)[0].state == "working"
    assert rows(factory, "target", 0)[0].state == "cancelled"
    saved = override(factory, run_id, 0)
    assert (saved.stop_off, saved.target_off) == (False, True)


def test_the_side_view_says_which_switch_is_off(deps, factory):
    run_id = trade(deps, factory)
    assert bot_manage.stop_off(deps, run_id, 0).ok
    bot = bot_store.load(factory, bot_store.run(factory, run_id).bot_id)
    view = bot_manage.sides(factory, run_id, bot)[0]
    assert (view.stop_off, view.target_off, view.off) == (True, False, False)
    assert [c.label for c in view.chips] == ["Stop off: no stop"]


# --- Bots slice 18, task 4: preview a stop change ---------------------------------------


def test_the_preview_shows_the_stop_change_stop_then_sets_and_changes_nothing(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    calls = len(deps.tasty.calls)
    out = bot_manage.preview_stop(deps, run_id, 0, "200")
    assert out.ok and out.message.startswith("Preview, nothing changed: a 200% stop would be at")
    assert f"(now {old.trigger})" in out.message and "wouldn't fire at once" in out.message
    assert len(deps.tasty.calls) == calls and override(factory, run_id, 0) is None
    assert rows(factory, "stop", 0)[0].trigger == old.trigger
    assert bot_manage.change_stop(deps, run_id, 0, "200").ok
    new = rows(factory, "stop", 0)[0].trigger
    assert f"would be at {new} " in out.message
    quotes(deps, factory, "11:00", short_mid="9.00")
    late = bot_manage.preview_stop(deps, run_id, 0, "150")
    assert late.ok and "it would fire at once" in late.message
    assert not bot_manage.preview_stop(deps, run_id, 0, "5000").ok


# --- Bots slice 18, task 5: Market, Close entire trade, Close selected --------------------


def one_leg_trade(deps, factory):
    """A live short put's filled 0DTE trade: one group of one leg, with its stop."""
    from tests.services.bot_fakes import make_shape

    bot_id = make_shape(factory, "short_put", live=True, stop=D("150"), target=D("50"))
    return bot_entry.fire(deps, bot_id, "09:47")


def markets(deps):
    return [c[2] for c in placed(deps, "Market")]


def test_market_on_a_one_leg_0dte_group_sends_one_market_order(deps, factory):
    run_id = one_leg_trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    at(deps, "11:00")
    out = bot_manage.close_part(deps, run_id, 0, "1", "market")
    assert out.ok and "at market" in out.message, out.message
    assert ("cancel", stop.broker_order_id) in deps.tasty.calls  # the stop went first
    [order] = markets(deps)
    assert order["legs"][0]["action"] == "Buy to Close" and order["legs"][0]["quantity"] == 1
    [close] = rows(factory, "close", 0)
    assert close.state == "filled" and close.log[0]["order_type"] == "market"


def test_market_on_a_two_leg_group_runs_the_exit_chase(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.close_part(deps, run_id, 0, "1", "market")
    assert out.ok and "never sent at market" in out.message
    assert markets(deps) == [] and len(side_closes(deps)) == 1
    assert side_closes(deps)[0]["order-type"] == "Limit"


def test_market_is_refused_off_0dte_and_the_order_type_is_checked(deps, factory, monkeypatch):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    assert not bot_manage.close_part(deps, run_id, 0, "1", "stop").ok
    monkeypatch.setattr(bot_manage, "_run_0dte", lambda deps, run_id: False)
    out = bot_manage.close_part(deps, run_id, 0, "1", "market")
    assert not out.ok and "0DTE" in out.message
    assert not bot_manage.close_trade(deps, run_id, "market").ok
    assert bot_manage.close_part(deps, run_id, 0, "1", "limit").ok  # Limit still works


def test_zero_dte_means_every_leg_expires_today():
    leg = {"symbol": "SPXW  261013P05700000"}
    later = {"symbol": "SPXW  261014P05700000"}
    assert bot_manage._zero_dte([leg, leg], DAY)
    assert not bot_manage._zero_dte([leg, later], DAY)
    assert not bot_manage._zero_dte([], DAY)


def test_close_entire_trade_closes_each_group_in_turn(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.close_trade(deps, run_id, "limit")
    assert out.ok and "one group at a time (Call side, Put side)" in out.message, out.message
    assert [(c.group_position, c.state) for c in rows(factory, "close")] == [
        (0, "filled"),
        (1, "filled"),
    ]
    assert "IC: whole trade closed" in titles(deps)
    again = bot_manage.close_trade(deps, run_id)
    assert not again.ok and "Nothing of this trade" in again.message


def test_close_this_side_closes_only_its_groups(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.close_trade(deps, run_id, "limit", only={1})
    assert out.ok and "Closing this side" in out.message and "(Put side)" in out.message, out.message
    assert [(c.group_position, c.state) for c in rows(factory, "close")] == [(1, "filled")]
    assert rows(factory, "stop", 0)[0].state == "working"  # the call side keeps its stop


def test_close_entire_trade_stops_at_the_first_group_that_doesnt_close(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    put_stop = rows(factory, "stop", 1)[0]
    deps.tasty.close = "work"  # closes never fill: the first group's chase gives up
    out = bot_manage.close_trade(deps, run_id, "limit")
    assert out.ok
    assert [c.group_position for c in rows(factory, "close")] == [0]  # never reached side 1
    assert rows(factory, "close", 0)[0].state != "filled"
    stop = rows(factory, "stop", 1)[0]
    assert (stop.id, stop.state) == (put_stop.id, "working")
    assert rows(factory, "target", 1)[0].state == "armed"
    alert = next(e for e in deps.seen if e.title == "IC: Close entire trade stopped")
    assert "Put side was NOT closed and keep their stop and target" in alert.detail


def test_close_entire_trade_is_refused_while_a_group_is_held_back(deps, factory):
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 1)[0]
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.state = "cancelled"
        row.log = [*row.log, {"event": bot_exits.MOVING_STOP}]
        session.commit()
    at(deps, "11:00")
    sent = len(placed(deps))
    out = bot_manage.close_trade(deps, run_id)
    assert not out.ok and "may still be working" in out.message
    assert len(placed(deps)) == sent and rows(factory, "close") == []


def test_close_selected_goes_on_past_a_refusal(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    results = bot_manage.close_selected(deps, ["999", str(run_id), "x"], "limit")
    assert [(r, o.ok) for r, o in results] == [(999, False), (run_id, True)]
    assert "0DTE" in results[0][1].message
    assert {c.state for c in rows(factory, "close")} == {"filled"}
    many = bot_manage.close_selected(deps, [str(n) for n in range(16)])
    assert len(many) == 1 and not many[0][1].ok and "at most 15" in many[0][1].message


def test_close_entire_trade_reads_each_group_again_before_its_claim(deps, factory):
    """A group that changed between the plan and its claim: sized to what's held then,
    and one already flat is skipped, not closed or alerted as stopped."""
    from tests.services.bot_fakes import plain

    run_id = trade(deps, factory, contracts=3)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    calls = []
    deps.background = lambda fn, *args: calls.append((fn, args))
    assert bot_manage.close_trade(deps, run_id, "limit").ok
    put = rows(factory, "stop", 1)[0]
    deps.tasty.held[plain(put.short_symbol)] = -1  # you bought 2 of the put side back
    deps.tasty.held[plain(put.long_symbol)] = 1
    call = rows(factory, "stop", 0)[0]
    deps.tasty.held[plain(call.short_symbol)] = 0  # and the call side is flat
    deps.tasty.held[plain(call.long_symbol)] = 0
    fn, args = calls[0]
    fn(*args)
    closes = rows(factory, "close")
    assert [(c.group_position, c.contracts) for c in closes] == [(1, 1)]
    assert "IC: whole trade closed" in titles(deps)


# --- The final review's test gaps: the market close's endings ---------------------------


def one_leg_market(deps, factory, contracts=1):
    from tests.services.bot_fakes import force_contracts, make_shape

    bot_id = make_shape(factory, "short_put", live=True, stop=D("150"), target=D("50"))
    if contracts > 1:
        force_contracts(factory, bot_id, contracts)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    at(deps, "11:00")
    return run_id


def market_order(deps):
    """The latest market order's id at the fake broker, or None before one is sent."""
    return next((c[1] for c in reversed(deps.tasty.calls)
                 if c[0] == "place" and c[2]["order-type"] == "Market"), None)  # fmt: skip


def test_a_market_close_whose_send_is_unclear_sends_nothing_more(deps, factory):
    run_id = one_leg_market(deps, factory)
    deps.tasty.errors["place"] = RuntimeError("the line dropped")
    assert bot_manage.close_part(deps, run_id, 0, "1", "market").ok
    [close] = rows(factory, "close", 0)
    assert close.state == "failed" and close.log[-1]["event"].startswith("unclear")
    assert rows(factory, "stop", 0)[0].state == "cancelled"  # no stop put back on top
    assert any("unclear" in t for t in titles(deps))


def test_a_market_close_whose_id_cant_be_recorded_is_unclear(deps, factory, monkeypatch):
    run_id = one_leg_market(deps, factory)
    monkeypatch.setattr(bot_exits, "record_order", lambda note, oid: True)
    assert bot_manage.close_part(deps, run_id, 0, "1", "market").ok
    [close] = rows(factory, "close", 0)
    assert close.state == "failed" and close.log[-1]["event"].startswith("unclear")


def test_a_market_close_that_ends_unfilled_puts_the_stop_back(deps, factory):
    from dataclasses import replace

    from tests.services.bot_fakes import plain

    run_id = one_leg_market(deps, factory)
    short = rows(factory, "stop", 0)[0].short_symbol
    deps.tasty.market_works.add(plain(short))

    def broker_cancels(seconds):
        if market_order(deps) is None:
            return
        oid = market_order(deps)
        deps.tasty.orders[oid] = replace(deps.tasty.orders[oid], status=OrderStatus.CANCELLED)

    deps.sleep = broker_cancels
    assert bot_manage.close_part(deps, run_id, 0, "1", "market").ok
    [close] = rows(factory, "close", 0)
    assert close.state == "failed" and "market order ended cancelled" in close.log[-1]["event"]
    assert rows(factory, "stop", 0)[0].state == "working"  # protected again


def test_a_part_filled_market_close_keeps_what_filled_and_protects_the_rest(deps, factory):
    from dataclasses import replace

    from tests.services.bot_fakes import plain

    run_id = one_leg_market(deps, factory, contracts=2)
    short = rows(factory, "stop", 0)[0].short_symbol
    deps.tasty.market_works.add(plain(short))

    def one_fills_then_cancelled(seconds):
        if market_order(deps) is None:
            return
        oid = market_order(deps)
        order = deps.tasty.orders[oid]
        legs = tuple(replace(leg, remaining=1, fill_price=D("1.00")) for leg in order.legs)
        deps.tasty.orders[oid] = replace(order, status=OrderStatus.CANCELLED, legs=legs)
        deps.tasty.held[plain(short)] = -1

    deps.sleep = one_fills_then_cancelled
    assert bot_manage.close_part(deps, run_id, 0, "2", "market").ok
    [close] = rows(factory, "close", 0)
    assert close.state == "reduced"
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.contracts == 1


def test_a_market_close_still_working_after_three_reads_is_unclear(deps, factory):
    from tests.services.bot_fakes import plain

    run_id = one_leg_market(deps, factory)
    deps.tasty.market_works.add(plain(rows(factory, "stop", 0)[0].short_symbol))
    assert bot_manage.close_part(deps, run_id, 0, "1", "market").ok
    [close] = rows(factory, "close", 0)
    assert close.state == "failed" and close.log[-1]["event"].startswith("unclear")
    assert any("is still working" in e.detail for e in deps.seen)
    assert rows(factory, "stop", 0)[0].state == "cancelled"


def test_a_market_close_after_the_session_sends_nothing(deps, factory, monkeypatch):
    run_id = one_leg_market(deps, factory)
    answers = iter([True])
    monkeypatch.setattr(bot_exits, "_session_open", lambda deps: next(answers, False))
    assert bot_manage.close_part(deps, run_id, 0, "1", "market").ok
    assert [c for c in placed(deps, "Market")] == []
    [close] = rows(factory, "close", 0)
    assert close.state == "failed" and "session is over" in close.log[-1]["event"]


# --- Close selected and the preview -------------------------------------------------------


def test_close_selected_takes_each_trade_once_and_at_most_15(deps, factory, monkeypatch):
    seen = []
    monkeypatch.setattr(bot_manage, "_run_0dte", lambda deps, run_id: True)
    monkeypatch.setattr(bot_manage, "close_trade", lambda deps, run_id, kind, only=None: (
        seen.append(run_id) or bot_manage.Outcome(True, "ok")))  # fmt: skip
    results = bot_manage.close_selected(deps, ["3", "3", "4", " 3"], "limit")
    assert seen == [3, 4] and [r for r, _ in results] == [3, 4]
    assert len(bot_manage.close_selected(deps, [str(n) for n in range(1, 16)])) == 15
    over = bot_manage.close_selected(deps, [str(n) for n in range(1, 17)])
    assert len(over) == 1 and not over[0][1].ok and seen[-1] == 15


def test_the_journals_close_selected_takes_any_expiration_and_one_side(deps, monkeypatch):
    """#300: the journal's Close isn't 0DTE-only, and a split condor's ticked side closes
    only its own groups, never the other side's."""
    seen = []
    monkeypatch.setattr(bot_manage, "_run_0dte", lambda deps, run_id: False)
    monkeypatch.setattr(bot_manage, "close_trade", lambda deps, run_id, kind, only=None: (
        seen.append((run_id, only)) or bot_manage.Outcome(True, "ok")))  # fmt: skip
    refused = bot_manage.close_selected(deps, [3], "limit")
    assert not refused[0][1].ok and seen == []
    bot_manage.close_selected(deps, [3, 4], "limit", {3: {0, 1}, 4: None}, zero_dte_only=False)
    assert seen == [(3, {0, 1}), (4, None)]


def test_the_preview_with_no_fresh_quote_says_change_stop_would_refuse(deps, factory):
    run_id = trade(deps, factory)
    deps.hub.quotes.clear()
    out = bot_manage.preview_stop(deps, run_id, 0, "200")
    assert out.ok and "no fresh price for the short" in out.message


def test_the_preview_of_a_watched_group_reads_its_pnl(deps, factory):
    from tests.services.test_bot_rearm import debit_trade, group_quotes

    run_id = debit_trade(deps, factory)
    at(deps, "11:00")
    group_quotes(deps, factory, run_id)
    out = bot_manage.preview_stop(deps, run_id, 0, "60")
    assert out.ok and "would be at P&L" in out.message and "wouldn't fire at once" in out.message
    deps.hub.quotes.clear()
    blind = bot_manage.preview_stop(deps, run_id, 0, "60")
    assert blind.ok and "no fresh prices for the group's legs" in blind.message.lower()


# --- The re-review: the stop revive paths --------------------------------------------------


def _stop_off_and(factory, deps, run_id, **stop_changes):
    switch_in_db(factory, run_id, 0, stop=True, tasty=deps.tasty)
    stop = rows(factory, "stop", 0)[0]
    with factory() as session:
        row = session.get(BotExit, stop.id)
        for name, value in stop_changes.items():
            setattr(row, name, value)
        session.commit()
    return stop


def test_a_time_action_never_revives_a_stop_on_a_held_back_side(deps, factory):
    run_id = trade(deps, factory)
    from tests.services.test_bot_manage import ny as at_ny

    stop = _stop_off_and(factory, deps, run_id)
    with factory() as session:  # a close you asked for ended unclear: it may be live
        row = session.get(BotExit, stop.id)
        session.add(BotExit(
            run_id=run_id, group_position=0, kind="close", state="failed",
            short_symbol=row.short_symbol, long_symbol=row.long_symbol, legs=row.legs,
            contracts=1, rearms=0, log=[{"event": "unclear: nothing more sent"}],
            created_at=at_ny("13:00"), updated_at=at_ny("13:00"),
        ))  # fmt: skip
        session.commit()
    with factory() as session:
        assert bot_exits.held_back(list(session.scalars(select(BotExit).where(
            BotExit.run_id == run_id, BotExit.group_position == 0)))) == "close"  # fmt: skip
    at(deps, "14:00")
    quotes(deps, factory, "14:00")
    sent = len(placed(deps, "Stop"))
    state, notes = bot_exits.apply_time_action(
        deps, run_id, 0, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")), 1, False
    )
    assert state == "failed" and "no stop row to switch back on" in notes[0]
    assert len(placed(deps, "Stop")) == sent and override(factory, run_id, 0).stop_off is True


def test_a_time_action_never_revives_a_stop_that_failed_on_an_unclear_send(deps, factory):
    run_id = trade(deps, factory)
    stop = _stop_off_and(factory, deps, run_id, state="failed")
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.log = [*row.log, {"event": "re-armed", "result": "Unclear: no answer"}]
        session.commit()
    at(deps, "14:00")
    quotes(deps, factory, "14:00")
    sent = len(placed(deps, "Stop"))
    state, _ = bot_exits.apply_time_action(
        deps, run_id, 0, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")), 1, False
    )
    assert state == "failed" and len(placed(deps, "Stop")) == sent
    assert override(factory, run_id, 0).stop_off is True


def test_a_refused_revive_is_left_failed_never_pending(deps, factory):
    from optionsjourney.brokers.http import BrokerError

    run_id = trade(deps, factory)
    _stop_off_and(factory, deps, run_id)
    at(deps, "14:00")
    quotes(deps, factory, "14:00")
    deps.tasty.errors["place"] = BrokerError("refused")
    state, notes = bot_exits.apply_time_action(
        deps, run_id, 0, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")), 1, False
    )
    assert state == "failed" and "nothing places it later" in notes[0]
    assert rows(factory, "stop", 0)[0].state == "failed"
    del deps.tasty.errors["place"]
    sent = len(placed(deps, "Stop"))
    bot_exits.monitor_pass(deps)
    assert len(placed(deps, "Stop")) == sent


# --- Bots slice 19: switch to market after the last try ---------------------------------


def _switch_on(factory, run_id):
    with factory() as session:
        bot_id = session.get(BotRun, run_id).bot_id
    bot = bot_store.load(factory, bot_id)
    chase = replace(bot.exit_chase, switch_to_market=True)
    assert bot_store.save(factory, replace(bot, exit_chase=chase), OPEN_TIME)[1] == {}


def _quote_legs(deps, factory, run_id, hhmm, bid="0.55", ask="0.65"):
    with factory() as session:
        legs = session.get(BotRun, run_id).legs
    for leg in legs:
        deps.hub.set(leg["symbol"], D(bid), D(ask), ny(hhmm))


def test_switch_to_market_sends_one_market_order_after_the_last_try(deps, factory):
    run_id = one_leg_market(deps, factory)
    _switch_on(factory, run_id)
    _quote_legs(deps, factory, run_id, "11:00")
    deps.tasty.close = "work"  # the limit chase never fills
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    limits = [c for c in placed(deps, "Limit") if c[2]["legs"][0]["action"] == "Buy to Close"]
    assert len(limits) == 1  # the chase: one limit, replaced, then cancelled
    [order] = markets(deps)
    assert order["legs"][0]["action"] == "Buy to Close" and order["legs"][0]["quantity"] == 1
    [close] = rows(factory, "close", 0)
    assert close.state == "filled"
    assert [e.get("stage") for e in close.log if e.get("stage")][-1] == "market"
    # the market order went out only once the chase's own order read cancelled
    stop_id = rows(factory, "stop", 0)[0].broker_order_id
    cancel = next(c for c in deps.tasty.calls if c[0] == "cancel" and c[1] != stop_id)
    assert deps.tasty.calls.index(cancel) < deps.tasty.calls.index(placed(deps, "Market")[0])


def test_one_leg_switch_to_market_wins_over_keep_conceding(deps, factory):
    run_id = one_leg_market(deps, factory)
    with factory() as session:
        bot_id = session.get(BotRun, run_id).bot_id
    bot = bot_store.load(factory, bot_id)
    chase = replace(bot.exit_chase, switch_to_market=True, exhaust_mode="keep_conceding")
    assert bot_store.save(factory, replace(bot, exit_chase=chase), OPEN_TIME)[1] == {}
    _quote_legs(deps, factory, run_id, "11:00")
    deps.tasty.close = "work"
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    limits = [c for c in placed(deps, "Limit") if c[2]["legs"][0]["action"] == "Buy to Close"]
    assert len(limits) == 1 and len(markets(deps)) == 1  # no conceding after the chase
    [close] = rows(factory, "close", 0)
    assert close.state == "filled"
    assert "keep" not in [e.get("stage") for e in close.log]


def test_switch_to_market_never_sends_a_two_leg_group_at_market(deps, factory):
    run_id = trade(deps, factory)
    _switch_on(factory, run_id)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    deps.tasty.close = "work"
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    assert markets(deps) == []
    [close] = rows(factory, "close", 0)
    assert close.state == "failed"  # given up: the stop goes back
    assert rows(factory, "stop", 0)[0].state == "working"


def test_switch_to_market_never_sells_a_bought_leg_with_no_bid_at_market(deps, factory):
    from tests.services.bot_fakes import make_shape

    bot_id = make_shape(factory, "long_call", live=True, stop=D("50"), target=D("50"))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    at(deps, "11:00")
    _switch_on(factory, run_id)
    _quote_legs(deps, factory, run_id, "11:00", bid="0", ask="0.10")
    deps.tasty.close = "work"
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    assert markets(deps) == []
    [close] = rows(factory, "close", 0)
    assert close.state == "failed"
    assert any("no bid" in (e.get("event") or "") for e in close.log)


def test_a_partial_chase_then_a_market_order_for_the_rest(deps, factory):
    run_id = one_leg_market(deps, factory, contracts=2)
    _switch_on(factory, run_id)
    _quote_legs(deps, factory, run_id, "11:00")
    deps.tasty.close = "partial"  # the limit fills 1 of 2 and stays working: cancelled
    assert bot_manage.close_part(deps, run_id, 0, "2").ok
    [order] = markets(deps)
    assert order["legs"][0]["quantity"] == 1  # only what's left
    assert rows(factory, "close", 0)[0].state == "filled"


def test_then_at_market_on_a_target_close_sends_what_is_held(deps, factory):
    run_id = one_leg_market(deps, factory)
    _switch_on(factory, run_id)
    deps.tasty.close = "work"
    _quote_legs(deps, factory, run_id, "11:00", bid="0.05", ask="0.10")  # target reached
    bot_exits.watch_pass(deps)
    bot_exits.watch_pass(deps)
    [order] = markets(deps)
    assert order["legs"][0]["action"] == "Buy to Close" and order["legs"][0]["quantity"] == 1
    assert rows(factory, "target", 0)[0].state == "filled"


def test_then_at_market_sends_nothing_when_nothing_is_held(deps, factory):
    from tests.services.bot_fakes import plain

    run_id = one_leg_market(deps, factory)
    _switch_on(factory, run_id)
    deps.tasty.close = "work"
    _quote_legs(deps, factory, run_id, "11:00", bid="0.05", ask="0.10")
    short = rows(factory, "stop", 0)[0].short_symbol
    real = bot_exits._positions

    def gone_at_the_end(d, run):  # held for the target's sizing, gone by the market step
        found = real(d, run)
        if any(c[0] == "cancel" for c in deps.tasty.calls[-2:]):
            found = {k: v for k, v in found.items() if k != plain(short)}
        return found

    bot_exits._positions = gone_at_the_end
    try:
        bot_exits.watch_pass(deps)
        bot_exits.watch_pass(deps)
    finally:
        bot_exits._positions = real
    assert markets(deps) == []
    target = rows(factory, "target", 0)[0]
    assert any("nothing left held" in (e.get("event") or "") for e in target.log)


def test_the_timeline_reads_a_close_that_switched_to_market(deps, factory):
    from optionsjourney.bots import timeline

    run_id = one_leg_market(deps, factory)
    _switch_on(factory, run_id)
    _quote_legs(deps, factory, run_id, "11:00")
    deps.tasty.close = "work"
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    [close] = rows(factory, "close", 0)
    assert [s.event for _, s in timeline.exit_steps(close)] == [
        "A close of 1 you asked for", "Sized", "Limit started", "Limit: Placed",
        *["Limit: Replaced"] * 5, "Limit: Cancelled", "Limit chase ended: missed",
        "Market: Placed", "Market ended: filled", "Closed at market", "Filled",
    ]  # fmt: skip


# --- Bots slice 21: the wing rule per trade ---------------------------------------------


def _stop_fills(deps, factory, side=0, price="4.80"):
    stop = rows(factory, "stop", side)[0]
    deps.tasty.fill(stop.broker_order_id, D(price))
    bot_exits.monitor_pass(deps)


def _wing_sells(deps):
    return [c[2] for c in placed(deps) if len(c[2]["legs"]) == 1
            and c[2]["legs"][0]["action"] == "Sell to Close"]  # fmt: skip


def test_the_trades_wing_rule_is_what_its_stop_fill_does(deps, factory):
    """Tracker2's exits.effective_on_stop_remainder: the trade's own "leave" wins over the
    group's "sell now"; the other group keeps its own rule."""
    run_id = trade(deps, factory)
    outcome = bot_manage.set_wing_rule(deps, run_id, 0, "leave", "")
    assert outcome.ok, outcome.message
    _stop_fills(deps, factory)
    [wing] = rows(factory, "wing", 0)
    assert wing.state == "left" and _wing_sells(deps) == []
    with factory() as session:
        row = session.scalar(select(BotTradeOverride).where(BotTradeOverride.run_id == run_id))
        assert (row.on_stop_remainder, row.remainder_limit) == ("leave", None)
    _stop_fills(deps, factory, side=1)
    assert len(_wing_sells(deps)) == 1  # the call side still sells its wing


def test_a_wing_rule_at_a_limit_sells_at_that_price(deps, factory):
    run_id = trade(deps, factory)
    assert bot_manage.set_wing_rule(deps, run_id, 0, "close_limit", "0.15").ok
    _stop_fills(deps, factory)
    [sell] = _wing_sells(deps)
    assert sell["order-type"] == "Limit" and D(sell["price"]) == D("0.15")


def test_reverting_the_wing_rule_goes_back_to_the_groups(deps, factory):
    run_id = trade(deps, factory)
    assert bot_manage.set_wing_rule(deps, run_id, 0, "leave", "").ok
    outcome = bot_manage.revert_wing_rule(deps, run_id, 0)
    assert outcome.ok, outcome.message
    _stop_fills(deps, factory)
    assert len(_wing_sells(deps)) == 1


def test_the_wing_rule_is_refused_once_the_stop_filled_or_with_a_bad_limit(deps, factory):
    run_id = trade(deps, factory)
    assert not bot_manage.set_wing_rule(deps, run_id, 0, "close_limit", "0").ok
    assert not bot_manage.set_wing_rule(deps, run_id, 0, "sideways", "").ok
    _stop_fills(deps, factory)
    refused = bot_manage.set_wing_rule(deps, run_id, 0, "leave", "")
    assert not refused.ok and "closed" in refused.message
    with factory() as session:
        assert session.scalar(select(BotTradeOverride)) is None  # nothing saved


# --- Bots slice 21: the stop style per trade ---------------------------------------------


def _calc(stop):
    from optionsjourney.bots import stops

    return stops.calc_of(stop.log)


def test_a_dollar_stop_moves_the_resting_stop(deps, factory):
    """Tracker2's per-trade stop_loss_unit/usd: the stop at the short's fill plus the
    dollars, through the same live check and move (cancel, confirm, place)."""
    from optionsjourney.bots.exit_math import round_stop

    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.change_stop(deps, run_id, 0, "1.50", "usd")
    assert out.ok, out.message
    stop = rows(factory, "stop", 0)[0]
    fill = D(_calc(old)["short_fill"])
    assert stop.state == "working" and stop.broker_order_id != old.broker_order_id
    assert stop.trigger == round_stop(stop.short_symbol, fill + D("1.50"))[1]
    assert _calc(stop)["basis"] == "usd"
    saved = override(factory, run_id, 0)
    assert (saved.stop_unit, saved.stop_usd, saved.stop_pct) == ("usd", D("1.50"), None)
    assert saved.log[-1]["what"] == "stop $1.50 a contract"


def test_a_dollar_stop_is_refused_when_it_would_fire_at_once(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="9.00")
    out = bot_manage.change_stop(deps, run_id, 0, "0.50", "usd")
    assert not out.ok and "would fire at once" in out.message
    assert rows(factory, "stop", 0)[0].broker_order_id == old.broker_order_id
    assert not bot_manage.change_stop(deps, run_id, 0, "-1", "usd").ok
    assert not bot_manage.change_stop(deps, run_id, 0, "1", "points").ok
    assert override(factory, run_id, 0) is None


def test_a_closing_order_on_the_trade_moves_its_stop(deps, factory):
    """management._apply_override_locked with stop_loss_co_mode/amount: the group's own
    stop with this trade's overlay (stops.stop_calc), moved like any change."""
    from optionsjourney.bots import stops

    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    calc = _calc(old)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.set_closing_order(deps, run_id, 0, "both", "1.00")
    assert out.ok, out.message
    bot = bot_store.load(factory, bot_id_of(factory))
    want = stops.stop_calc(bot.groups[0], "both", D("1.00"), old.short_symbol,
                           D(calc["short_fill"]), D(calc["entry_credit"]))  # fmt: skip
    stop = rows(factory, "stop", 0)[0]
    assert stop.trigger == want.trigger and stop.broker_order_id != old.broker_order_id
    saved = override(factory, run_id, 0)
    assert (saved.co_mode, saved.co_amount) == ("both", D("1.00"))
    off = bot_manage.set_closing_order(deps, run_id, 0, "off", "")
    assert off.ok, off.message
    assert rows(factory, "stop", 0)[0].trigger == old.trigger  # the group's own number again
    assert override(factory, run_id, 0).co_mode == "off"


def bot_id_of(factory):
    from optionsjourney.db.models import Bot

    with factory() as session:
        return session.scalar(select(Bot.id))


def test_a_closing_order_that_would_fire_at_once_leaves_the_old_stop(deps, factory):
    run_id = trade(deps, factory)
    old = rows(factory, "stop", 0)[0]
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="4.00")  # above entry - 1.00
    out = bot_manage.set_closing_order(deps, run_id, 0, "both", "4.00")
    assert not out.ok
    assert [c[0] for c in deps.tasty.calls].count("cancel") == 0
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.broker_order_id) == ("working", old.broker_order_id)
    assert override(factory, run_id, 0) is None
    assert not bot_manage.set_closing_order(deps, run_id, 0, "both", "").ok  # no amount
    assert not bot_manage.set_closing_order(deps, run_id, 0, "sometimes", "1").ok


def test_a_time_action_after_a_dollar_stop_clears_the_trades_stop_style(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.change_stop(deps, run_id, 0, "1.50", "usd").ok
    state, _ = bot_exits.apply_time_action(
        deps, run_id, 0, TimeActionDef("dte", 0, "11:00", stop_pct=D("300")), 1, False
    )
    assert state == "filled"
    saved = override(factory, run_id, 0)
    assert (saved.stop_unit, saved.stop_usd, saved.co_mode) == (None, None, None)


def test_switching_the_stop_back_on_at_a_typed_pct_clears_the_trades_stop_style(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    assert bot_manage.change_stop(deps, run_id, 0, "1.50", "usd").ok
    assert bot_manage.stop_off(deps, run_id, 0).ok
    on = bot_manage.stop_on(deps, run_id, 0, "200")
    assert on.ok, on.message
    saved = override(factory, run_id, 0)
    assert (saved.stop_pct, saved.stop_unit, saved.stop_usd) == (D("200"), None, None)


# --- Decision 0048: a trade whose target rests -------------------------------------------


def run_of_bot(factory, run_id):
    from optionsjourney.db.models import BotRun

    with factory() as session:
        return session.get(BotRun, run_id).bot_id


def resting_trade(deps, factory):
    from tests.services.bot_fakes import make_shape, rest_the_target

    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"), target=D("50"))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    stop_id, target_id = rest_the_target(deps, factory, run_id)
    return run_id, stop_id, target_id


def gtc(deps):
    """Every resting target placed: a GTC closing limit (never the GTC stop a run first
    rests, which rest_the_target cancels)."""
    return [c for c in deps.tasty.calls if c[0] == "place" and c[2]["time-in-force"] == "GTC"
            and c[2]["order-type"] == "Limit"
            and not c[2]["legs"][0]["action"].endswith("Open")]  # fmt: skip


def test_changing_a_resting_target_cancels_it_and_places_the_new_one(deps, factory):
    run_id, _, target_id = resting_trade(deps, factory)
    with factory() as session:
        old = session.get(BotExit, target_id).broker_order_id
    out = bot_manage.change_target(deps, run_id, 0, "40")
    assert out.ok, out.message
    assert ("cancel", old) in deps.tasty.calls and len(gtc(deps)) == 2
    [now] = rows(factory, "target", 0)[-1:]
    assert now.state == "working" and now.broker_order_id != old


def test_a_resting_target_switches_off_and_back_on_at_tastytrade(deps, factory):
    run_id, stop_id, target_id = resting_trade(deps, factory)
    out = bot_manage.target_off(deps, run_id, 0)
    assert out.ok and "Its stop still works" in out.message, out.message
    assert rows(factory, "target", 0)[-1].state == "cancelled"
    assert rows(factory, "stop", 0)[0].state == bot_exits.WATCHING
    out = bot_manage.target_on(deps, run_id, 0)
    assert out.ok, out.message
    assert rows(factory, "target", 0)[-1].state == "working" and len(gtc(deps)) == 2


def test_exits_off_on_a_resting_target_cancels_it_first(deps, factory):
    run_id, _, target_id = resting_trade(deps, factory)
    with factory() as session:
        old = session.get(BotExit, target_id).broker_order_id
    assert bot_manage.exits_off(deps, run_id, 0).ok
    assert ("cancel", old) in deps.tasty.calls
    assert rows(factory, "target", 0)[-1].state == "cancelled"
    assert rows(factory, "stop", 0)[0].state == "cancelled"


def test_exits_back_on_places_a_resting_target_again_never_arms_it(deps, factory):
    """P4: Exits off logs EXITS_OFF on the cancelled resting target; Exits on must place it
    again at TastyTrade (place_target_again), never re-arm it for the app to watch."""
    run_id, stop_id, target_id = resting_trade(deps, factory)
    assert bot_manage.exits_off(deps, run_id, 0).ok
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.exits_on(deps, run_id, 0)
    assert out.ok, out.message
    [target] = rows(factory, "target", 0)
    assert target.state == "working" and len(gtc(deps)) == 2
    assert all(r.state != "armed" for r in rows(factory, "target", 0))
    assert rows(factory, "stop", 0)[0].state == bot_exits.WATCHING


def test_a_changed_resting_target_that_failed_is_placed_again_by_restore(deps, factory):
    """P5: the changed target's new row says it rests, so once its send fails Restore
    places it at TastyTrade again; it never arms it."""
    from optionsjourney.brokers.http import BrokerError
    from optionsjourney.services import bot_rearm

    run_id, _, _ = resting_trade(deps, factory)
    place = deps.tasty.place_order

    def refuse(account, body):
        raise BrokerError("not now")

    deps.tasty.place_order = refuse
    out = bot_manage.change_target(deps, run_id, 0, "40")
    assert not out.ok and "Its stop is still watched by the app" in out.message, out.message
    new = rows(factory, "target", 0)[-1]
    assert new.state == "failed" and new.log[0]["event"] == bot_exits.CHANGE_PLACING
    assert {"rests": "target"} in new.log
    deps.tasty.place_order = place
    before = len(gtc(deps))
    bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert len(gtc(deps)) == before + 1
    assert rows(factory, "target", 0)[-1].state == "working"


def test_change_target_takes_up_to_1000_pct_on_a_debit_fill(deps, factory):
    """G1: the range follows the trade's fill: 1–1000% on a debit, 1–99% on a credit."""
    from tests.services.test_bot_rearm import debit_trade

    run_id = debit_trade(deps, factory)
    out = bot_manage.change_target(deps, run_id, 0, "150")
    assert out.ok, out.message
    assert not bot_manage.change_target(deps, run_id, 0, "1001").ok


def test_change_target_stops_at_99_pct_on_a_credit_fill(deps, factory):
    run_id, _, _ = resting_trade(deps, factory)
    out = bot_manage.change_target(deps, run_id, 0, "150")
    assert not out.ok and out.message == "The target must be from 1% to 99%."
    run_id = trade(deps, factory)
    out = bot_manage.change_target(deps, run_id, 0, "150")
    assert not out.ok and out.message == "The target must be from 1% to 99%."


def test_the_closing_order_is_refused_on_a_trade_that_opened_for_a_debit(deps, factory):
    from tests.services.bot_fakes import make_shape

    bot_id = make_shape(factory, "call_debit_spread", live=True, stop=D("50"), target=D("50"))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    out = bot_manage.set_closing_order(deps, run_id, 0, "both", "0.15")
    assert not out.ok and "opened for a debit" in out.message


def test_the_run_page_shows_each_groups_note_and_its_resting_target(deps, factory):
    run_id, _, target_id = resting_trade(deps, factory)
    with factory() as session:
        row = session.get(BotExit, target_id)
        row.log = [*row.log, {"note": "Its stop is watched by the bot."}]
        session.commit()
        order_id = row.broker_order_id
    bot = bot_store.load(factory, run_of_bot(factory, run_id))
    [side] = bot_manage.sides(factory, run_id, bot)
    assert side.note == "Its stop is watched by the bot." and side.target_order == order_id


def test_exits_off_whose_resting_target_wont_cancel_turns_nothing_off(deps, factory):
    run_id, stop_id, target_id = resting_trade(deps, factory)
    with factory() as session:
        old = session.get(BotExit, target_id).broker_order_id
    deps.tasty.stuck.add(old)
    out = bot_manage.exits_off(deps, run_id, 0)
    assert not out.ok and "Exits are still on" in out.message, out.message
    assert rows(factory, "target", 0)[-1].state == "working"
    assert rows(factory, "stop", 0)[0].state == bot_exits.WATCHING
    assert override(factory, run_id, 0) is None


def _no_stop(factory, stop_id):
    """The group keeps only its resting target (a long option with just a target, say)."""
    with factory() as session:
        session.delete(session.get(BotExit, stop_id))
        session.commit()


def test_a_resting_target_off_on_a_group_with_no_stop_says_it_has_no_exit(deps, factory):
    run_id, stop_id, _ = resting_trade(deps, factory)
    _no_stop(factory, stop_id)
    out = bot_manage.target_off(deps, run_id, 0)
    assert out.ok and "it has no exit at all" in out.message, out.message
    assert "Its stop still works" not in out.message


def test_a_failed_change_on_a_group_with_no_stop_says_it_has_no_exit(deps, factory):
    from optionsjourney.brokers.http import BrokerError

    run_id, stop_id, _ = resting_trade(deps, factory)
    _no_stop(factory, stop_id)

    def refuse(account, body):
        raise BrokerError("not now")

    deps.tasty.place_order = refuse
    out = bot_manage.change_target(deps, run_id, 0, "40")
    assert not out.ok and "It has no stop, so it has no exit now." in out.message, out.message
    assert "stop is still watched" not in out.message


def test_exits_on_whose_resting_target_isnt_placed_needs_you(deps, factory):
    from optionsjourney.brokers.http import BrokerError
    from optionsjourney.db.models import BotRun

    run_id, _, _ = resting_trade(deps, factory)
    assert bot_manage.exits_off(deps, run_id, 0).ok
    at(deps, "11:00")
    quotes(deps, factory, "11:00")

    def refuse(account, body):
        raise BrokerError("not now")

    deps.tasty.place_order = refuse
    out = bot_manage.exits_on(deps, run_id, 0)
    assert "The target wasn't placed again" in out.message, out.message
    assert any(t.endswith("profit target not placed") for t in titles(deps))
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)


def test_the_closing_order_is_hidden_on_a_trade_that_filled_for_a_debit(deps, factory):
    from optionsjourney.db.models import BotRun

    run_id = trade(deps, factory)
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.fill_credit = -abs(run.fill_credit)
        session.commit()
    bot = bot_store.load(factory, run_of_bot(factory, run_id))
    assert all(s.co_mode is None for s in bot_manage.sides(factory, run_id, bot))


def test_a_schwab_stop_moves_by_its_cancel_and_a_new_stop_at_schwab(deps, factory):
    """Decision 0060: a resting Schwab stop moves as a TastyTrade one does, never replaced
    in place: cancelled at Schwab, the cancel read back, then a new stop placed there."""
    from tests.services.bot_fakes import FakeSchwab, make_schwab

    schwab, tasty = FakeSchwab(), deps.tasty
    deps.clients = lambda: [tasty, schwab]
    run_id = bot_entry.fire(deps, make_schwab(factory, live=True), "09:47")
    old = rows(factory, "stop", 0)[0]
    assert old.state == "working" and old.broker_order_id
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    out = bot_manage.change_stop(deps, run_id, 0, "1.50", "usd")
    assert out.ok, out.message
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.broker_order_id != old.broker_order_id
    assert ("cancel", old.broker_order_id) in schwab.calls
    stops = [c[-1] for c in schwab.calls if c[0] == "place" and c[-1]["orderType"] == "STOP"]
    assert len(stops) == 3 and stops[-1]["stopPrice"] == f"{stop.trigger:.2f}"
    assert not [c for c in schwab.calls if c[0] == "replace"]
    assert not [c for c in tasty.calls if c[0] in ("place", "cancel")]


def test_a_paper_trade_is_refused_as_paper_not_preview(deps, factory):
    """Final review M1: a direct close of a paper run says Paper."""
    run_id = trade(deps, factory)
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.live, run.paper = False, True
        session.commit()
    at(deps, "11:00")
    for out in (
        bot_manage.close_trade(deps, run_id),
        bot_manage.close_part(deps, run_id, 0, "1", "limit"),
    ):
        assert not out.ok and out.message == (
            "This is a paper trade, so there's nothing at the broker to close."
        )


# --- #209: Close group on one unit of a xN group closes 1..N contracts ------------------
# Tracker2 (cc60dcf9) counts a 3-lot short call as 1 unit and needs a split close to take
# 1 of the 3. Here a group's unit is its ratios over their gcd (slice 13), so the same
# trade is 3 units of one contract: Close group already offers 1..3, and every path below
# puts the stop back on exactly what is left.


def lot_trade(deps, factory, key="short_put", ratio=3, **group_changes):
    """A live 1-contract run of `key` with every leg's ratio multiplied by `ratio` (a
    short put x3: one bot contract is 3 puts), a 50% stop and target."""
    bot_id = make_shape(factory, key, live=True, stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_id)
    bot = replace(
        bot, max_per_trade=40, max_per_day=40,
        legs=tuple(replace(leg, ratio=leg.ratio * ratio) for leg in bot.legs),
        groups=tuple(replace(g, **group_changes) for g in bot.groups),
    )  # fmt: skip
    _, problems = bot_store.save(factory, bot, OPEN_TIME)
    assert problems == {}, problems
    return bot_entry.fire(deps, bot_id, "09:47")


def lot_open(deps, factory, run_id, hhmm="11:00"):
    at(deps, hhmm)
    quote_group(deps, factory, run_id, hhmm)


def lot_closes(deps):
    return [c[2] for c in placed(deps, "Limit") if c[2]["legs"][0]["action"].endswith("Close")
            and c[2]["time-in-force"] == "Day"]  # fmt: skip


def test_one_of_a_three_lot_closes_one_contract_and_the_stop_comes_back_on_two(deps, factory):
    run_id = lot_trade(deps, factory)
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts) == ("working", 3)
    trigger = stop.trigger
    with factory() as session:
        assert session.get(BotRun, run_id).contracts == 1  # one bot contract, 3 puts
    lot_open(deps, factory, run_id)
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok, out.message
    assert "Closing 1 of 3" in out.message
    (order,) = lot_closes(deps)
    assert [int(leg["quantity"]) for leg in order["legs"]] == [1]
    (close,) = rows(factory, "close")
    assert close.state == "reduced" and any(e.get("closed") == 1 for e in close.log)
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts, stop.trigger) == ("working", 2, trigger)
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 2
    (target,) = rows(factory, "target")
    assert (target.state, target.contracts) == ("armed", 2)
    # what's left is the new size everywhere: the panel, its chip and the next close
    with factory() as session:
        bot = bot_store.load(factory, session.get(BotRun, run_id).bot_id)
    (side,) = bot_manage.sides(factory, run_id, bot)
    assert side.size == 2 and any(c.label == "Closed 1 of 3" for c in side.chips)
    out = bot_manage.close_part(deps, run_id, 0, "3")
    assert not out.ok and "Only 2" in out.message


def test_a_three_lot_close_that_part_fills_puts_the_stop_on_what_is_left(deps, factory):
    run_id = lot_trade(deps, factory)
    deps.tasty.close = "partial"  # the 2-contract close fills 1, then gives up
    lot_open(deps, factory, run_id)
    assert bot_manage.close_part(deps, run_id, 0, "2").ok
    (close,) = rows(factory, "close")
    assert close.state == "reduced" and any(e.get("closed") == 1 for e in close.log)
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts) == ("working", 2)
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 2
    assert rows(factory, "target")[0].contracts == 2


def test_a_three_lot_close_that_fills_nothing_puts_the_stop_back_on_three(deps, factory):
    run_id = lot_trade(deps, factory)
    (before,) = rows(factory, "stop")
    deps.tasty.close = "work"
    lot_open(deps, factory, run_id)
    assert bot_manage.close_part(deps, run_id, 0, "2").ok
    assert [c.state for c in rows(factory, "close")] == ["failed"]
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts, stop.trigger) == ("working", 3, before.trigger)
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 3
    assert (rows(factory, "target")[0].state, rows(factory, "target")[0].contracts) == (
        "armed", 3,
    )  # fmt: skip


def test_a_cancelled_three_lot_close_puts_the_stop_back_on_three(deps, factory):
    run_id = lot_trade(deps, factory)
    jobs = []
    deps.background = lambda fn, *args: jobs.append((fn, args))
    lot_open(deps, factory, run_id)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    (close,) = rows(factory, "close")
    assert bot_exits.request_cancel(factory, run_id, close.id, ny("11:00"))
    fn, args = jobs[0]
    fn(*args)
    assert rows(factory, "close")[0].state == "cancelled" and lot_closes(deps) == []
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts) == ("working", 3)


def test_a_three_lot_close_cut_off_by_a_restart_keeps_the_stop_on_three(deps, factory):
    run_id = lot_trade(deps, factory)
    deps.background = lambda fn, *args: None  # the job never runs
    lot_open(deps, factory, run_id)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    bot_exits.FIRING.clear()  # the app restarted
    bot_exits.recover_targets(deps)
    assert rows(factory, "close")[0].state == "failed"
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts) == ("working", 3)


def test_a_three_lot_close_with_its_stop_off_puts_no_stop_back(deps, factory):
    run_id = lot_trade(deps, factory)
    assert bot_manage.stop_off(deps, run_id, 0).ok
    sent = len(stops_sent(deps))
    lot_open(deps, factory, run_id)
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok and "has no stop" in out.message
    assert rows(factory, "close")[0].state == "reduced"
    assert len(stops_sent(deps)) == sent
    assert rows(factory, "target")[0].contracts == 2


def test_more_contracts_than_the_lot_holds_are_refused_and_nothing_is_sent(deps, factory):
    run_id = lot_trade(deps, factory)
    lot_open(deps, factory, run_id)
    sent = len(deps.tasty.calls)
    for text, why in (("4", "Only 3"), ("0", "1 or more")):
        out = bot_manage.close_part(deps, run_id, 0, text)
        assert not out.ok and why in out.message
    assert len(deps.tasty.calls) == sent and rows(factory, "close") == []
    assert rows(factory, "stop")[0].contracts == 3


def test_a_dollar_stop_on_a_three_lot_keeps_its_level_after_part_closes(deps, factory):
    # Tracker2 leaves a $ group whole-unit only: its split re-prices a per-unit $ level per
    # contract and would loosen it. Here the unit is one contract all along, so the level
    # each contract stops at is the same before and after.
    run_id = lot_trade(deps, factory, stop_unit="usd", stop_usd=D("2"), stop_pct=None)
    (before,) = rows(factory, "stop")
    lot_open(deps, factory, run_id)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts, stop.trigger) == ("working", 2, before.trigger)
    assert stops_sent(deps)[-1]["stop-trigger"] == f"{before.trigger:f}"


def test_a_two_lot_spread_closes_one_spread_and_protects_the_other(deps, factory):
    # 2 short and 2 long puts for one bot contract: 2 units of one spread each.
    run_id = lot_trade(deps, factory, key="put_credit_spread", ratio=2)
    lot_open(deps, factory, run_id)
    out = bot_manage.close_part(deps, run_id, 0, "1")
    assert out.ok and "Closing 1 of 2" in out.message
    (order,) = lot_closes(deps)
    assert sorted(int(leg["quantity"]) for leg in order["legs"]) == [1, 1]
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts) == ("working", 1)
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 1


def test_the_panel_counts_a_one_leg_group_in_contracts(deps, factory):
    run_id = lot_trade(deps, factory)
    with factory() as session:
        bot = bot_store.load(factory, session.get(BotRun, run_id).bot_id)
    (side,) = bot_manage.sides(factory, run_id, bot)
    assert (side.size, side.unit) == (3, "contract")
    spread = lot_trade(deps, factory, key="put_credit_spread", ratio=2)
    with factory() as session:
        bot = bot_store.load(factory, session.get(BotRun, spread).bot_id)
    (side,) = bot_manage.sides(factory, spread, bot)
    assert (side.size, side.unit) == (2, "unit")
