"""The Dynamic stop's ratchet (#329, decision 0131): each side's stop on its short moves down
as the side's long decays, once a minute in the regular session, never up."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots import stops
from optionsjourney.bots.definition import GroupDef, iron_condor_skeleton
from optionsjourney.bots.exit_math import round_stop
from optionsjourney.brokers.http import BrokerError
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import bot_dynamic_stops, bot_entry, bot_exits, bot_store, orders
from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make

D = Decimal
LATER = OPEN_TIME + timedelta(minutes=5)


@pytest.fixture(autouse=True)
def fresh():
    bot_dynamic_stops.reset()
    yield
    bot_dynamic_stops.reset()


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: OPEN_TIME, background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    d.tasty = tasty
    return d


def run(deps, factory, buffer="0.20", mode="dynamic"):
    """A live iron condor on a Dynamic stop: each side's short and its wing in one group."""
    bot_id = make(factory, live=True, stop_co_mode=mode, stop_co_amount=D(buffer))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    deps.now = lambda: LATER
    return run_id


def call_stop(factory) -> BotExit:
    with factory() as session:
        return session.scalars(
            select(BotExit).where(BotExit.kind == "stop", BotExit.group_position == 0)
        ).one()


def quote(deps, symbol, bid, ask, at=LATER):
    deps.hub.set(symbol, D(bid), D(ask), at)


def market(deps, stop, long_mid, short_ask="1.00", at=LATER):
    """The call side's long at `long_mid` (a 0.10 wide quote), its short's ask."""
    mid = D(long_mid)
    quote(deps, stop.long_symbol, max(mid - D("0.05"), D(0)), mid + D("0.05"), at)
    quote(deps, stop.short_symbol, D(short_ask) - D("0.10"), short_ask, at)


def replaces(deps):
    return [c for c in deps.tasty.calls if c[0] == "replace"]


def alerts(deps):
    seen = []
    deps.notify = seen.append
    return seen


def test_a_decayed_long_moves_the_resting_stop_down_by_replacing_it(deps, factory):
    run(deps, factory)
    stop = call_stop(factory)
    calc = stops.calc_of(stop.log)
    credit = D(calc["entry_credit"])
    with factory() as session:  # resting higher than the long now says: room to move
        session.get(BotExit, stop.id).trigger = stop.trigger + D("1.00")
        session.commit()
    seen = alerts(deps)
    market(deps, stop, "0.00", short_ask="1.00")  # the wing now bid 0, ask 0.05: 0.025
    summary = bot_dynamic_stops.ratchet_pass(deps)
    moved = call_stop(factory)
    want = round_stop(stop.short_symbol, credit + D("0.025") - D("0.20"))[1]
    assert summary["moved"] == 1 and moved.trigger == want
    [(_, old, new, body)] = replaces(deps)
    assert old == stop.broker_order_id and moved.broker_order_id == new
    assert body["order-type"] == "Stop" and D(body["stop-trigger"]) == want
    assert body["legs"][0]["action"] == "Buy to Close" and body["time-in-force"] == "GTC"
    last = stops.calc_of(moved.log)
    assert (last["basis"], last["long_value"], last["trigger"]) == ("dynamic", "0.025", str(want))
    assert moved.log[-1]["event"].startswith("moved down as the long decayed")
    assert stops.describe(last)[0] == "moved by the Dynamic stop as the long decayed"
    assert seen == []  # quiet: a move is no alert
    assert not deps.tasty.orders[stop.broker_order_id].may_be_working  # replaced, not doubled


def test_the_worked_example_moves_down_as_the_long_decays_and_never_up(deps, factory):
    run(deps, factory)
    stop = call_stop(factory)
    with factory() as session:  # the condor's numbers: 3.30 credit, the long filled at 1.00
        row = session.get(BotExit, stop.id)
        calc = dict(stops.calc_of(row.log), entry_credit="3.30", co_amount="0.20")
        row.trigger, row.log = D("4.10"), [*row.log, {"stop_calc": calc}]
        session.commit()
    market(deps, stop, "0.50", short_ask="2.00")
    bot_dynamic_stops.ratchet_pass(deps)
    assert call_stop(factory).trigger == D("3.60")  # 3.30 + 0.50 - 0.20
    deps.now = lambda: LATER + timedelta(minutes=1)
    market(deps, stop, "0.90", short_ask="2.00", at=LATER + timedelta(minutes=1))
    bot_dynamic_stops.ratchet_pass(deps)
    assert call_stop(factory).trigger == D("3.60")  # a long worth more never moves it up
    assert len(replaces(deps)) == 1


def test_never_moved_to_a_level_the_short_trades_through(deps, factory):
    run(deps, factory)
    stop = call_stop(factory)
    with factory() as session:
        row = session.get(BotExit, stop.id)
        calc = dict(stops.calc_of(row.log), entry_credit="3.30", co_amount="0.20")
        row.trigger, row.log = D("4.10"), [*row.log, {"stop_calc": calc}]
        session.commit()
    market(deps, stop, "0.50", short_ask="3.85")  # 3.60 is through the 3.85 ask
    bot_dynamic_stops.ratchet_pass(deps)
    assert call_stop(factory).trigger == D("3.90")  # one tick above the ask


@pytest.mark.parametrize(
    "spoil",
    [
        "stale_long",  # the long's quote is over 30 s old
        "no_market_long",  # 0/0 is no market, not a free option
        "no_short_ask",
        "stale_short",
    ],
)
def test_no_move_without_fresh_usable_quotes(deps, factory, spoil):
    run(deps, factory)
    stop = call_stop(factory)
    market(deps, stop, "0.00", short_ask="1.00")
    old = LATER - timedelta(seconds=31)
    if spoil == "stale_long":
        quote(deps, stop.long_symbol, "0", "0.05", old)
    elif spoil == "no_market_long":
        quote(deps, stop.long_symbol, "0", "0", LATER)
    elif spoil == "no_short_ask":
        quote(deps, stop.short_symbol, "0.90", "0", LATER)
    else:
        quote(deps, stop.short_symbol, "0.90", "1.00", old)
    summary = bot_dynamic_stops.ratchet_pass(deps)
    assert summary["moved"] == 0 and replaces(deps) == []
    assert call_stop(factory).trigger == stop.trigger


def test_a_refused_replace_leaves_the_old_stop_and_alerts_once(deps, factory):
    run(deps, factory)
    stop = call_stop(factory)
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.trigger = stop.trigger + D("1.00")  # room to move
        session.commit()
    deps.tasty.errors["replace"] = BrokerError("cannot_update_order")
    seen = alerts(deps)
    market(deps, stop, "0.00", short_ask="1.00")
    summary = bot_dynamic_stops.ratchet_pass(deps)
    after = call_stop(factory)
    assert summary["errors"] == 1 and after.state == "working"
    assert (after.broker_order_id, after.trigger) == (stop.broker_order_id, stop.trigger + 1)
    assert deps.tasty.orders[stop.broker_order_id].may_be_working  # the old stop still rests
    assert [e.title for e in seen] == ["IC: Dynamic stop not moved"]
    assert "still stands" in seen[0].detail
    deps.now = lambda: LATER + timedelta(minutes=2)
    market(deps, stop, "0.00", short_ask="1.00", at=LATER + timedelta(minutes=2))
    bot_dynamic_stops.ratchet_pass(deps)
    assert len(seen) == 1  # once a day per stop, not every minute


def test_only_dynamic_stops_move(deps, factory):
    run(deps, factory, mode="tighter")
    stop = call_stop(factory)
    market(deps, stop, "0.00", short_ask="1.00")
    assert bot_dynamic_stops.ratchet_pass(deps)["checked"] == 0
    assert call_stop(factory).trigger == stop.trigger


def test_another_rule_moving_the_stop_ends_the_ratchet(deps, factory):
    run(deps, factory)
    stop = call_stop(factory)
    with factory() as session:  # a time action moved it: its own arithmetic is the latest
        row = session.get(BotExit, stop.id)
        moved = dict(stops.calc_of(row.log), basis="leg_premium", moved_by="time action 1")
        row.log = [*row.log, {"stop_calc": moved}]
        session.commit()
    market(deps, stop, "0.00", short_ask="1.00")
    assert bot_dynamic_stops.ratchet_pass(deps)["checked"] == 1  # only the put side's
    assert call_stop(factory).trigger == stop.trigger and replaces(deps) == []


def test_once_a_minute_in_the_regular_session_and_not_under_freeze(deps, factory):
    run(deps, factory)
    stop = call_stop(factory)
    market(deps, stop, "0.00", short_ask="1.00")
    assert bot_dynamic_stops.ratchet_pass(deps)["checked"] == 2
    assert bot_dynamic_stops.ratchet_pass(deps)["checked"] == 0  # within the minute
    bot_dynamic_stops.reset()
    deps.now = lambda: OPEN_TIME.replace(hour=21)  # 17:00 New York
    assert bot_dynamic_stops.ratchet_pass(deps)["checked"] == 0
    bot_dynamic_stops.reset()
    deps.now = lambda: LATER
    orders.set_switch(factory, "freeze", True, LATER)
    assert bot_dynamic_stops.ratchet_pass(deps)["checked"] == 0


def test_a_stop_the_app_holds_on_the_short_moves_with_nothing_sent(deps, factory):
    run(deps, factory)
    stop = call_stop(factory)
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    with factory() as session:  # its target rests (decision 0048), so the app holds the stop
        row = session.get(BotExit, stop.id)
        row.state, row.app_held, row.broker_order_id = bot_exits.WATCHING, True, None
        row.trigger = stop.trigger + D("1.00")
        session.commit()
        since = session.get(BotExit, stop.id).updated_at
    market(deps, stop, "0.00", short_ask="1.00")
    sent = len(deps.tasty.calls)
    assert bot_dynamic_stops.ratchet_pass(deps)["moved"] >= 1
    after = call_stop(factory)
    assert after.trigger < stop.trigger + D("1.00") and after.state == bot_exits.WATCHING
    assert [c for c in deps.tasty.calls[sent:] if c[0] in ("place", "replace")] == []
    # its watch's "since" stays: a gap in watching before the move is still said
    assert after.updated_at == since


def test_a_closed_paired_long_counts_zero(deps, factory):
    # An imported bot's short and long in their own groups, paired by close on stop
    bot_id = make(factory, live=True, stop_co_mode="dynamic", stop_co_amount=D("0.20"))
    bot = bot_store.load(factory, bot_id)
    legs = tuple(replace(leg, group=leg.position) for leg in bot.legs)
    groups = (
        GroupDef(0, "Short call", stop_pct=D("95"), close_on_stop=1),
        GroupDef(1, "Long call"),
        GroupDef(2, "Short put", stop_pct=D("95"), close_on_stop=3),
        GroupDef(3, "Long put"),
    )
    _, problems = bot_store.save(factory, replace(bot, legs=legs, groups=groups), OPEN_TIME)
    assert problems == {}
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    deps.now = lambda: LATER
    with factory() as session:
        stop = session.scalars(select(BotExit).where(
            BotExit.kind == "stop", BotExit.group_position == 0)).one()  # fmt: skip
        calc = stops.calc_of(stop.log)
        run_row = session.get(BotRun, run_id)
        long_symbol = next(leg["symbol"] for leg in run_row.legs if leg["group"] == 1)
    assert calc["long_group"] == 1 and calc["long_legs"][0]["symbol"] == long_symbol
    with factory() as session:  # the long group closed: its value is 0, with no quote at all
        session.add(BotExit(run_id=run_id, group_position=1, kind="close", state="filled",
                            short_symbol="", long_symbol="", legs=[], contracts=1, log=[],
                            created_at=LATER, updated_at=LATER))  # fmt: skip
        row = session.get(BotExit, stop.id)
        row.trigger = stop.trigger + D("1.00")
        session.commit()
    quote(deps, stop.short_symbol, "0.90", "1.00")
    bot_dynamic_stops.ratchet_pass(deps)
    credit = D(calc["entry_credit"])
    want = round_stop(stop.short_symbol, credit - D("0.20"))[1]
    assert call_stop(factory).trigger == min(want, stop.trigger + D("1.00"))


def test_the_default_condor_holds_its_dynamic_stop_on_the_short_and_ratchets_it(deps, factory):
    # Decision 0054: each side (short and wing) rests its target; the app holds the stop
    groups = tuple(replace(g, stop_pct=D("95"), target_pct=D("50"), broker_exit="target")
                   for g in iron_condor_skeleton("IC").groups)  # fmt: skip
    bot_id = make(factory, live=True, stop_co_mode="dynamic", stop_co_amount=D("0.20"),
                  groups=groups)  # fmt: skip
    bot_entry.fire(deps, bot_id, "09:47")
    deps.now = lambda: LATER
    stop = call_stop(factory)
    calc = stops.calc_of(stop.log)
    assert (stop.state, stop.app_held, calc["basis"]) == (bot_exits.WATCHING, True, "dynamic")
    credit, long = D(calc["entry_credit"]), D(calc["long_value"])
    assert stop.trigger == round_stop(stop.short_symbol, credit + long - D("0.20"))[1]
    with factory() as session:  # resting higher than the long now says: room to move
        session.get(BotExit, stop.id).trigger = stop.trigger + D("1.00")
        session.commit()
    market(deps, stop, "0.00", short_ask="1.00")
    sent = len(deps.tasty.calls)
    assert bot_dynamic_stops.ratchet_pass(deps)["moved"] >= 1
    want = round_stop(stop.short_symbol, credit + D("0.025") - D("0.20"))[1]
    assert call_stop(factory).trigger == want
    assert [c for c in deps.tasty.calls[sent:] if c[0] != "preview"] == []
