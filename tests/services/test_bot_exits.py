import logging
import threading
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from optionsjourney.bots import exit_plan, stops
from optionsjourney.bots.definition import EntryTimeDef, GroupDef, TimeActionDef
from optionsjourney.brokers.http import BrokerError, RequestUnclear
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import Bot, BotExit, BotLegGroup, BotRun
from optionsjourney.services import bot_entry, bot_exits, bot_manage, bot_store, orders
from optionsjourney.trading.orders import OrderStatus, Side, plain
from tests.services.bot_fakes import (
    OPEN_TIME,
    SHAPE_MATRIX,
    FakeChain,
    FakeHub,
    FakeSchwab,
    FakeTasty,
    arm_the_target,
    force_contracts,
    make,
    make_schwab,
    make_shape,
    mid_for,
    rest_the_target,
    sym,
)

D = Decimal


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
    return d


def live_run(deps, factory, target_pct=None):
    bot_id = make(factory, live=True)
    if target_pct is not None:
        bot = bot_store.load(factory, bot_id)
        groups = tuple(replace(g, target_pct=D(target_pct)) for g in bot.groups)
        bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)
    return bot_entry.fire(deps, bot_id, "09:47")


def exits(factory, kind=None):
    with factory() as session:
        query = select(BotExit).order_by(BotExit.id)
        if kind:
            query = query.where(BotExit.kind == kind)
        return list(session.scalars(query))


def test_a_fill_rests_one_stop_per_side_on_the_short(deps, factory):
    live_run(deps, factory)
    stops = exits(factory, "stop")
    assert [s.state for s in stops] == ["working", "working"]
    bodies = [c[2] for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]
    assert len(bodies) == 2 and all(len(b["legs"]) == 1 for b in bodies)
    assert {b["legs"][0]["action"] for b in bodies} == {"Buy to Close"}
    assert all(b["time-in-force"] == "GTC" for b in bodies)
    # The call short fills at its mid, 2.50: 2.50 x 1.95 = 4.875, and a single SPXW leg at
    # $3 or more trades in $0.10, rounded down: 4.80
    assert stops[0].trigger == D("4.80")


def test_no_stop_until_the_broker_shows_the_legs(deps, factory):
    deps.tasty.option_positions = lambda account: {}
    live_run(deps, factory)
    assert {e.state for e in exits(factory)} == {"failed"}
    assert not [c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]


def test_a_stop_fill_closes_its_wing_at_market_and_leaves_the_other_side(deps, factory):
    live_run(deps, factory, target_pct="50")
    give_wing_a_bid(deps, factory)
    call_stop = exits(factory, "stop")[0]
    deps.tasty.fill(call_stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    by_kind = {(e.kind, e.group_position): e.state for e in exits(factory)}
    assert by_kind[("stop", 0)] == "filled" and by_kind[("target", 0)] == "cancelled"
    assert by_kind[("wing", 0)] in ("working", "filled")
    assert by_kind[("stop", 1)] == "working" and by_kind[("target", 1)] == "armed"
    wing = [c[2] for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Market"]
    assert len(wing) == 1 and wing[0]["legs"][0]["action"] == "Sell to Close"


def test_a_stop_cancelled_outside_the_app_is_rearmed_up_to_three_times(deps, factory):
    live_run(deps, factory)
    for _ in range(4):
        stop = [e for e in exits(factory, "stop") if e.group_position == 0][0]
        deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
        bot_exits.monitor_pass(deps)
    stop = [e for e in exits(factory, "stop") if e.group_position == 0][0]
    assert stop.rearms == 3 and stop.state == "failed"


def test_the_monitor_reads_working_exits_without_holding_the_exits_lock(deps, factory):
    live_run(deps, factory)
    seen = []
    read = deps.tasty.get_order

    def probe(account, order_id):
        got = []

        def other():
            ok = bot_exits.EXITS_LOCK.acquire(timeout=1)
            if ok:
                bot_exits.EXITS_LOCK.release()
            got.append(ok)

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        seen.extend(got)
        return read(account, order_id)

    deps.tasty.get_order = probe
    bot_exits.monitor_pass(deps)
    assert seen == [True, True]


def test_a_stop_claimed_while_its_order_is_read_is_left_to_the_claim(deps, factory):
    run_id = live_run(deps, factory)
    call_stop = exits(factory, "stop")[0]
    read = deps.tasty.get_order

    def probe(account, order_id):
        if order_id == call_stop.broker_order_id:
            with bot_exits.EXITS_LOCK:
                bot_exits._claim_side(deps, run_id, 0, "close", "a close of 1 you asked for")
            deps.tasty.fill(order_id, D("4.80"))
        return read(account, order_id)

    deps.tasty.get_order = probe
    bot_exits.monitor_pass(deps)
    stop = [e for e in exits(factory, "stop") if e.id == call_stop.id][0]
    assert stop.state == "cancelled"
    assert stop.log[-1]["event"] == bot_exits.CANCELLED_FOR_CLOSE
    assert not [c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Market"]
    assert [e.state for e in exits(factory, "close")] == ["firing"]


def quotes(deps, run_legs, short_mid, long_mid, at):
    short, long = run_legs
    deps.hub.set(short, short_mid - 0.05, short_mid + 0.05, at)
    deps.hub.set(long, long_mid - 0.05, long_mid + 0.05, at)


def call_side(factory):
    stop = exits(factory, "stop")[0]
    return stop.short_symbol, stop.long_symbol


def give_wing_a_bid(deps, factory, bid="0.30"):
    """A fresh bid on the call side's wing, so "sold now" goes at market."""
    long = exits(factory, "stop")[0].long_symbol
    deps.hub.set(long, D(bid), D(bid) + D("0.10"), OPEN_TIME)


def test_a_target_needs_two_fresh_passes_then_cancels_the_stop_and_closes(deps, factory):
    live_run(deps, factory, target_pct="50")
    later = OPEN_TIME + timedelta(minutes=30)
    deps.now = lambda: later
    quotes(deps, call_side(factory), 0.60, 0.05, later)  # cheap to close: breached
    bot_exits.watch_pass(deps)
    assert exits(factory, "target")[0].state == "armed"  # one pass isn't enough
    bot_exits.watch_pass(deps)
    target = exits(factory, "target")[0]
    assert target.state == "filled"
    assert exits(factory, "stop")[0].state == "cancelled"
    closes = [
        c[2]
        for c in deps.tasty.calls
        if c[0] == "place"
        and len(c[2]["legs"]) == 2
        and c[2]["legs"][0]["action"].endswith("Close")
    ]
    assert len(closes) == 1


def test_stale_quotes_never_fire_a_target(deps, factory):
    live_run(deps, factory, target_pct="50")
    later = OPEN_TIME + timedelta(minutes=30)
    deps.now = lambda: later
    quotes(deps, call_side(factory), 0.60, 0.05, later - timedelta(seconds=40))
    bot_exits.watch_pass(deps)
    bot_exits.watch_pass(deps)
    assert exits(factory, "target")[0].state == "armed"


def test_a_quiet_quote_on_a_live_stream_still_fires_a_target(deps, factory):
    """#202 (Tracker2 483df63e): the armed pass reads its legs as the watch rules do; a
    quote unchanged for 90 s is current while SPX streams through the same feed."""
    live_run(deps, factory, target_pct="50")
    later = OPEN_TIME + timedelta(minutes=30)
    deps.now = lambda: later
    quotes(deps, call_side(factory), 0.60, 0.05, later - timedelta(seconds=90))
    deps.hub.set_last("SPX", "5800", later)
    bot_exits.watch_pass(deps)
    bot_exits.watch_pass(deps)
    assert exits(factory, "target")[0].state == "filled"


def test_a_target_paused_alone_is_logged_not_alerted(deps, factory, caplog):
    """#203 (Tracker2 b57aab7c): a pause of an app-watched profit target only delays a
    take-profit, so after a minute it is logged once, with the legs' ages, never pushed;
    its recovery is silent."""

    live_run(deps, factory, target_pct="50")
    start = OPEN_TIME + timedelta(minutes=30)
    quotes(deps, call_side(factory), 2.00, 0.05, start - timedelta(seconds=40))  # stale
    seen = []
    deps.notify = seen.append
    with caplog.at_level(logging.WARNING, logger=bot_exits.__name__):
        for seconds in (0, 30, 62, 64):
            deps.now = lambda s=seconds: start + timedelta(seconds=s)
            bot_exits.watch_pass(deps)
    found = [r.getMessage() for r in caplog.records if "profit target paused" in r.getMessage()]
    assert len(found) == 2  # once for each side's target, at 62 s, not again at 64 s
    assert all("no fresh quote for 62s (not alerted)" in f for f in found)
    assert any(f"{call_side(factory)[0]} quote=102s greeks=never src=tastytrade" in f
               for f in found)  # fmt: skip
    assert seen == []
    assert exits(factory, "target")[0].state == "armed"


def test_target_stands_down_when_the_stop_filled_first(deps, factory):
    live_run(deps, factory, target_pct="50")
    stop = exits(factory, "stop")[0]
    deps.tasty.fill(stop.broker_order_id, D("4.80"))  # filled before the target's cancel lands
    target = exits(factory, "target")[0]
    bot_exits.fire_target(deps, target.id)
    assert exits(factory, "target")[0].state == "cancelled"
    assert not [
        c
        for c in deps.tasty.calls
        if c[0] == "place"
        and len(c[2]["legs"]) == 2
        and c[2]["legs"][0]["action"].endswith("Close")
    ]


def test_an_unfilled_target_close_puts_the_stop_back(deps, factory):
    deps.tasty.close = "work"
    live_run(deps, factory, target_pct="50")
    target = exits(factory, "target")[0]
    bot_exits.fire_target(deps, target.id)
    assert exits(factory, "target")[0].state == "failed"
    stop = exits(factory, "stop")[0]
    assert stop.state == "working" and stop.rearms == 1


def test_freeze_stops_both_loops_sending(deps, factory):
    live_run(deps, factory, target_pct="50")
    orders.set_switch(factory, "freeze", True)
    before = len(deps.tasty.calls)
    stop = exits(factory, "stop")[0]
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    bot_exits.watch_pass(deps)
    assert len(deps.tasty.calls) == before


def test_exits_survive_editing_the_bot(deps, factory):
    run_id = live_run(deps, factory, target_pct="50")
    with factory() as session:
        bot_id = session.get(BotRun, run_id).bot_id
    bot = bot_store.load(factory, bot_id)
    bot_store.save(factory, replace(bot, name="Renamed"), OPEN_TIME)  # replaces legs and groups
    stop = exits(factory, "stop")[0]
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    assert any(e.kind == "wing" for e in exits(factory))


def alerts_of(deps):
    seen = []
    deps.notify = seen.append
    return seen


def titles(seen):
    return [e.title for e in seen]


def two_contract_run(deps, factory, target_pct="50"):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, target_pct=D(target_pct)) for g in bot.groups)
    bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)
    force_contracts(factory, bot_id, 2)  # past the 1-contract limit, for the sizing paths
    return bot_entry.fire(deps, bot_id, "09:47")


def test_a_partly_filled_target_close_puts_the_stop_back_at_the_size_still_open(deps, factory):
    deps.tasty.close = "partial"
    two_contract_run(deps, factory)
    seen = alerts_of(deps)
    target = exits(factory, "target")[0]
    bot_exits.fire_target(deps, target.id)
    stop = exits(factory, "stop")[0]
    assert stop.state == "working" and stop.contracts == 1  # one of two contracts still open
    replaced = [c[2] for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]
    assert replaced[-1]["legs"][0]["quantity"] == 1
    didnt_fill = [e for e in seen if "didn't fill" in e.title]
    assert didnt_fill and "put back" in didnt_fill[0].detail


def test_an_unclear_target_close_is_not_rearmed_and_says_to_check_the_broker(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    deps.tasty.errors["place"] = RequestUnclear("timed out")
    target = exits(factory, "target")[0]
    bot_exits.fire_target(deps, target.id)
    assert exits(factory, "target")[0].state == "failed"
    stop = exits(factory, "stop")[0]
    assert stop.rearms == 0 and stop.state == "cancelled"
    assert any("Check your broker" in e.detail and "without a stop" in e.detail for e in seen)


def test_an_error_mid_fire_rearms_the_stop_fails_the_target_and_alerts(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    real, calls = deps.tasty.get_order, {"n": 0}

    def flaky(account, order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("broker hiccup")  # the confirm read; the cancel did land
        return real(account, order_id)

    deps.tasty.get_order = flaky
    target = exits(factory, "target")[0]
    bot_exits.fire_target(deps, target.id)
    assert exits(factory, "target")[0].state == "failed"
    stop = exits(factory, "stop")[0]
    assert stop.state == "working" and stop.rearms == 1
    assert any("profit target error" in e.title for e in seen)


def test_the_wing_is_sold_at_the_size_held(deps, factory):
    two_contract_run(deps, factory)
    give_wing_a_bid(deps, factory)
    stop = exits(factory, "stop")[0]
    deps.tasty.held[plain(stop.long_symbol)] = 1  # one of the two was closed elsewhere
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    wing = [c[2] for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Market"]
    assert len(wing) == 1 and wing[0]["legs"][0]["quantity"] == 1


def test_no_wing_order_when_the_wing_is_no_longer_held(deps, factory):
    live_run(deps, factory)
    stop = exits(factory, "stop")[0]
    deps.tasty.held[plain(stop.long_symbol)] = 0
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    assert not [c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Market"]
    assert [e.state for e in exits(factory, "wing")] == ["expired"]


def test_a_target_fires_when_the_wing_has_no_bid(deps, factory):
    live_run(deps, factory, target_pct="50")
    later = OPEN_TIME + timedelta(minutes=30)
    deps.now = lambda: later
    quotes(deps, call_side(factory), 0.60, 0.05, later)  # the wing bids 0.00
    assert deps.hub.quotes[call_side(factory)[1]].bid == 0
    bot_exits.watch_pass(deps)
    bot_exits.watch_pass(deps)
    assert exits(factory, "target")[0].state == "filled"


def test_a_stale_wing_quote_blocks_the_target(deps, factory):
    live_run(deps, factory, target_pct="50")
    later = OPEN_TIME + timedelta(minutes=30)
    deps.now = lambda: later
    short, long = call_side(factory)
    deps.hub.set(short, 0.55, 0.65, later)
    deps.hub.set(long, 0.0, 0.10, later - timedelta(seconds=40))
    bot_exits.watch_pass(deps)
    bot_exits.watch_pass(deps)
    assert exits(factory, "target")[0].state == "armed"


def test_an_unconfirmed_stop_cancel_drops_the_target_and_says_so(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    deps.tasty.cancel_order = lambda account, order_id: None  # the cancel never lands
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert exits(factory, "target")[0].state == "cancelled"
    assert exits(factory, "stop")[0].state == "working"
    assert any("profit target dropped" in e.title for e in seen)


def test_a_wing_order_that_ends_rejected_alerts(deps, factory):
    live_run(deps, factory)
    seen = alerts_of(deps)
    stop = exits(factory, "stop")[0]
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    wing = exits(factory, "wing")[0]
    assert wing.state == "working"
    old = deps.tasty.orders[wing.broker_order_id]
    deps.tasty.orders[wing.broker_order_id] = replace(old, status=OrderStatus.REJECTED)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "wing")[0].state == "failed"
    assert any("wing wasn't sold" in e.title for e in seen)


def test_a_stop_for_a_position_that_is_gone_expires_and_drops_its_target(deps, factory):
    live_run(deps, factory, target_pct="50")
    stop = exits(factory, "stop")[0]
    deps.tasty.held[plain(stop.short_symbol)] = 0
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "expired"
    assert exits(factory, "target")[0].state == "cancelled"
    assert exits(factory, "target")[1].state == "armed"  # the other side is untouched


def test_error_mid_fire_with_the_cancel_not_landed_leaves_the_stop_working(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    deps.tasty.cancel_order = lambda account, order_id: None  # the cancel never lands
    stop = exits(factory, "stop")[0]
    calls = {"n": 0}
    real = deps.tasty.get_order

    def flaky(account, order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("hiccup")  # the confirm read inside fire_target
        return real(account, order_id)

    deps.tasty.get_order = flaky
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    stop = exits(factory, "stop")[0]
    assert stop.state == "working" and stop.rearms == 0
    assert exits(factory, "target")[0].state == "failed"
    assert (
        len([c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]) == 2
    )
    assert any("still in place" in e.detail for e in seen)


def test_error_mid_fire_with_the_old_stop_filled_gets_the_wing_sold(deps, factory):
    live_run(deps, factory, target_pct="50")
    give_wing_a_bid(deps, factory)
    stop = exits(factory, "stop")[0]
    deps.tasty.cancel_order = lambda account, order_id: None
    calls = {"n": 0}
    real = deps.tasty.get_order

    def flaky(account, order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            deps.tasty.fill(order_id, D("4.80"))  # it fills while we were cancelling
            raise RuntimeError("hiccup")
        return real(account, order_id)

    deps.tasty.get_order = flaky
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert exits(factory, "stop")[0].state == "working"
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "filled"
    wing = [c[2] for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Market"]
    assert len(wing) == 1 and stop.id


def test_unreadable_positions_never_expire_a_stop_or_drop_its_target(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    stop = exits(factory, "stop")[0]
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    deps.tasty.errors["positions"] = RuntimeError("outage")
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "failed"
    assert exits(factory, "target")[0].state == "armed"
    assert any("Couldn't read positions" in e.detail for e in seen)


def test_protections_are_placed_only_once(deps, factory):
    run_id = live_run(deps, factory)
    with factory() as session:
        before = session.scalars(select(BotExit.id)).all()
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    bot_exits.place_protections(deps, run_id)
    with factory() as session:
        assert session.scalars(select(BotExit.id)).all() == before
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed
    assert before


# Slice 13: what slice 12 placed for make()'s condor, taken from slice 12's code before
# any slice 13 change to place_protections. Never edit these numbers to make a test pass.
_CALL_SHORT, _CALL_WING = "SPXW  261013C05910000", "SPXW  261013C05985000"
_PUT_SHORT, _PUT_WING = "SPXW  261013P05690000", "SPXW  261013P05615000"
_STOP_LOG = {
    "stop": "95",
    "stop_calc": {
        "basis": "leg_premium", "configured": "95", "short_fill": "2.50",
        "entry_credit": "4.90", "configured_raw": "4.8750", "co_mode": "off",
        "co_amount": None, "co_raw": None, "winner": "basis", "raw": "4.8750",
        "tick": "0.10", "trigger": "4.80", "problem": None,
    },
}  # fmt: skip
_TARGET_LOG = {"target": "50", "unit": "pct", "credit": "2.45", "close_at": "1.225"}
_AT = OPEN_TIME.isoformat()
SLICE_12_ROWS = [
    (0, "stop", "working", _CALL_SHORT, _CALL_WING, 3, D("4.80"), None,
     [{"at": _AT, **_STOP_LOG}, {"at": _AT, "result": "Sent."}]),
    (0, "target", "armed", _CALL_SHORT, _CALL_WING, 3, None, D("1.225"),
     [{"at": _AT, **_TARGET_LOG}]),
    (1, "stop", "working", _PUT_SHORT, _PUT_WING, 3, D("4.80"), None,
     [{"at": _AT, **_STOP_LOG}, {"at": _AT, "result": "Sent."}]),
    (1, "target", "armed", _PUT_SHORT, _PUT_WING, 3, None, D("1.225"),
     [{"at": _AT, **_TARGET_LOG}]),
]  # fmt: skip
SLICE_12_STOPS = [
    {"time-in-force": "GTC", "order-type": "Stop", "stop-trigger": "4.80",
     "legs": [{"instrument-type": "Equity Option", "symbol": short, "quantity": 3,
               "action": "Buy to Close"}]}
    for short in (_CALL_SHORT, _PUT_SHORT)
]  # fmt: skip


def test_an_iron_condor_places_exactly_what_slice_12_did(deps, factory):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, target_pct=D("50")) for g in bot.groups)
    bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)
    force_contracts(factory, bot_id, 3)
    bot_entry.fire(deps, bot_id, "09:47")
    rows = [
        (e.group_position, e.kind, e.state, e.short_symbol, e.long_symbol, e.contracts,
         e.trigger, e.limit, e.log)
        for e in exits(factory)
    ]  # fmt: skip
    assert rows == SLICE_12_ROWS
    sent = [
        {k: v for k, v in c[2].items() if k != "external-identifier"}
        for c in deps.tasty.calls
        if c[0] == "place" and not c[2]["legs"][0]["action"].endswith("Open")
    ]
    assert sent == SLICE_12_STOPS


def stop_places(deps):
    return [c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]


def side_closes(deps):
    return [
        c
        for c in deps.tasty.calls
        if c[0] == "place"
        and len(c[2]["legs"]) == 2
        and c[2]["legs"][0]["action"].endswith("Close")
    ]


def bot_of_run(factory, run_id):
    with factory() as session:
        return session.get(BotRun, run_id).bot_id


# 1. A run's exits follow the run's live setting; a preview result is never "placed".


def test_flipping_a_live_bot_to_preview_mid_trade_keeps_the_target_close_live(deps, factory):
    run_id = live_run(deps, factory, target_pct="50")
    bot_store.set_live(factory, bot_of_run(factory, run_id), False, OPEN_TIME)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert exits(factory, "target")[0].state == "filled"
    assert len(side_closes(deps)) == 1  # a real close, not a preview


def test_flipping_a_live_bot_to_preview_mid_trade_still_really_rearms(deps, factory):
    deps.tasty.close = "work"
    run_id = live_run(deps, factory, target_pct="50")
    bot_store.set_live(factory, bot_of_run(factory, run_id), False, OPEN_TIME)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    stop = exits(factory, "stop")[0]
    assert stop.state == "working" and stop.rearms == 1
    assert len(stop_places(deps)) == 3  # two first stops and the real re-arm
    assert deps.tasty.orders[stop.broker_order_id].may_be_working


def preview_run(factory, run_id):
    with factory() as session:
        session.get(BotRun, run_id).live = False
        session.commit()


def test_a_preview_result_is_never_recorded_as_working(deps, factory):
    run_id = live_run(deps, factory)
    seen = alerts_of(deps)
    preview_run(factory, run_id)  # its exits now only preview
    stop = exits(factory, "stop")[0]
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    placed = len(stop_places(deps))
    bot_exits.monitor_pass(deps)
    stop = exits(factory, "stop")[0]
    assert stop.state == "failed" and len(stop_places(deps)) == placed
    assert any("no stop" in e.detail for e in seen)


def test_preview_stops_and_wings_are_not_recorded_as_working(deps, factory, monkeypatch):
    monkeypatch.setattr(bot_entry, "place_protections", lambda d, r: None)
    run_id = live_run(deps, factory)
    preview_run(factory, run_id)
    bot_exits.place_protections(deps, run_id)
    assert {s.state for s in exits(factory, "stop")} == {"failed"}
    assert all(s.broker_order_id is None for s in exits(factory, "stop"))


# 2. Exit rows are written before the stops are sent.


def test_exit_rows_are_written_as_placing_before_any_stop_is_sent(deps, factory, monkeypatch):
    seen = []
    real = orders.submit

    def spy(session_factory, clients, request, **kw):
        if not request.order.opening:
            seen.append(sorted(e.state for e in exits(factory)))
        return real(session_factory, clients, request, **kw)

    monkeypatch.setattr(orders, "submit", spy)
    live_run(deps, factory, target_pct="50")
    assert seen[0] == ["placing"] * 4
    assert sorted(e.state for e in exits(factory)) == ["armed", "armed", "working", "working"]


# 3. A target a crash left firing.


def crash_mid_target(factory):
    """What fire_target commits before its cancel: target firing, stop cancelled."""
    with factory() as session:
        target = session.scalars(select(BotExit).where(BotExit.kind == "target")).first()
        stop = session.scalars(
            select(BotExit).where(
                BotExit.kind == "stop", BotExit.group_position == target.group_position
            )
        ).first()
        target.state, stop.state = "firing", "cancelled"
        stop.log = [*stop.log, {"event": bot_exits.CANCELLED_FOR_TARGET}]
        session.commit()
        return target.id, stop.id


def test_a_target_left_firing_by_a_crash_puts_its_live_stop_back(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    target_id, stop_id = crash_mid_target(factory)
    bot_exits.monitor_pass(deps)
    with factory() as session:
        assert session.get(BotExit, stop_id).state == "working"
        assert session.get(BotExit, target_id).state == "failed"
    assert any("profit target error" in e.title and "still in place" in e.detail for e in seen)
    assert len(stop_places(deps)) == 2  # the old stop still stands; nothing re-placed


def test_a_target_left_firing_whose_stop_is_gone_is_rearmed_at_startup(deps, factory):
    live_run(deps, factory, target_pct="50")
    target_id, stop_id = crash_mid_target(factory)
    with factory() as session:
        order_id = session.get(BotExit, stop_id).broker_order_id
    deps.tasty.cancel_order("5WX01234", order_id)  # the cancel went out before the crash
    bot_exits.recover_targets(deps)
    with factory() as session:
        stop = session.get(BotExit, stop_id)
        assert stop.state == "working" and stop.broker_order_id != order_id
        assert session.get(BotExit, target_id).state == "failed"


def test_a_target_being_fired_is_not_recovered_from_under_itself(deps, factory):
    live_run(deps, factory, target_pct="50")
    target_id, stop_id = crash_mid_target(factory)
    bot_exits.FIRING.add(target_id)
    try:
        bot_exits.monitor_pass(deps)
    finally:
        bot_exits.FIRING.clear()
    with factory() as session:
        assert session.get(BotExit, target_id).state == "firing"
        assert session.get(BotExit, stop_id).state == "cancelled"


# 4. Freeze holds stops as pending; the monitor places them after unfreeze.


def test_stops_refused_by_freeze_wait_as_pending_and_are_placed_after(deps, factory):
    seen = alerts_of(deps)
    # Freeze goes on while the entry is out: it fills, then its stops are refused.
    deps.sleep = lambda s: orders.set_switch(factory, "freeze", True)
    live_run(deps, factory, target_pct="50")
    assert [s.state for s in exits(factory, "stop")] == ["pending", "pending"]
    assert not stop_places(deps)
    assert any("waiting for Freeze" in e.title for e in seen)
    bot_exits.monitor_pass(deps)  # still frozen: nothing
    assert not stop_places(deps)
    orders.set_switch(factory, "freeze", False)
    bot_exits.monitor_pass(deps)
    stops = exits(factory, "stop")
    assert [s.state for s in stops] == ["working", "working"] and len(stop_places(deps)) == 2
    assert all(s.rearms == 0 for s in stops)  # placing a held stop isn't a re-arm


def test_a_pending_stop_claimed_after_the_monitor_listed_it_is_not_placed(deps, factory):
    # Freeze held both stops "pending"; once it's off, only the call side's is placed.
    deps.sleep = lambda s: orders.set_switch(factory, "freeze", True)
    run_id = live_run(deps, factory, target_pct="50")
    orders.set_switch(factory, "freeze", False)
    call_stop, put_stop = exits(factory, "stop")
    with bot_exits.EXITS_LOCK:
        bot_exits.rearm(deps, call_stop.id, first=True)
    call_order = exits(factory, "stop")[0].broker_order_id
    assert len(stop_places(deps)) == 1
    read = deps.tasty.get_order

    def probe(account, order_id):  # the put side is claimed while the call stop is read
        if order_id == call_order:
            with bot_exits.EXITS_LOCK:
                bot_exits._claim_side(deps, run_id, 1, "close", "a close of 1 you asked for")
        return read(account, order_id)

    deps.tasty.get_order = probe
    bot_exits.monitor_pass(deps)
    assert len(stop_places(deps)) == 1  # no stop placed for the claimed side
    put = [e for e in exits(factory, "stop") if e.id == put_stop.id][0]
    assert put.state == "cancelled" and put.broker_order_id is None
    assert put.log[-1]["event"] == bot_exits.CANCELLED_FOR_CLOSE


def test_a_stop_that_filled_under_a_claim_is_settled_once_the_claim_hands_it_back(deps, factory):
    run_id = live_run(deps, factory, target_pct="50")
    give_wing_a_bid(deps, factory)
    call_stop = exits(factory, "stop")[0]
    read = deps.tasty.get_order
    claims = []

    def probe(account, order_id):
        if order_id == call_stop.broker_order_id and not claims:
            with bot_exits.EXITS_LOCK:
                claims.append(
                    bot_exits._claim_side(deps, run_id, 0, "close", "a close of 1 you asked for")
                )
            deps.tasty.fill(order_id, D("4.80"))
        return read(account, order_id)

    deps.tasty.get_order = probe
    bot_exits.monitor_pass(deps)  # the reading is dropped: the claim has the stop
    assert [e.state for e in exits(factory, "stop")][0] == "cancelled"
    close_id, bot_id, stop_id = claims[0]
    assert stop_id == call_stop.id
    # The claim's own job reads the stop back, finds it filled, drops the close and hands
    # the stop back to the monitor as working; no closing order is sent.
    bot_exits._target_job(deps, close_id, run_id, bot_id, stop_id)
    assert [e for e in exits(factory, "stop") if e.id == stop_id][0].state == "working"
    assert [e.state for e in exits(factory, "close")] == ["failed"]
    assert not side_closes(deps)
    bot_exits.monitor_pass(deps)  # now the fill is recorded and the wing sold, once
    by_kind = {(e.kind, e.group_position): e.state for e in exits(factory)}
    assert by_kind[("stop", 0)] == "filled" and by_kind[("wing", 0)] in ("working", "filled")
    wing = [c[2] for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Market"]
    assert len(wing) == 1 and wing[0]["legs"][0]["action"] == "Sell to Close"
    assert not side_closes(deps)
    assert by_kind[("stop", 1)] == "working"


# 5. The daily limit counts entries only.


def test_a_bots_own_stops_do_not_use_up_its_daily_limit(deps, factory):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    times = (EntryTimeDef("09:47"), EntryTimeDef("09:48"))
    bot_store.save(factory, replace(bot, max_per_day=2, entry_times=times), OPEN_TIME)
    first = bot_entry.fire(deps, bot_id, "09:47")
    assert len([e for e in exits(factory, "stop") if e.run_id == first]) == 2
    second = bot_entry.fire(deps, bot_id, "09:48")
    with factory() as session:
        assert session.get(BotRun, second).status == "filled"


# 7. Expiry evening.


def test_an_expired_stop_on_expiry_evening_is_marked_expired_quietly(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    evening = OPEN_TIME.replace(hour=20, minute=30)  # 16:30 New York, the expiration day
    deps.now = lambda: evening
    for stop in exits(factory, "stop"):
        old = deps.tasty.orders[stop.broker_order_id]
        deps.tasty.orders[stop.broker_order_id] = replace(old, status=OrderStatus.EXPIRED)
    placed = len(stop_places(deps))
    bot_exits.monitor_pass(deps)
    assert [s.state for s in exits(factory, "stop")] == ["expired", "expired"]
    assert len(stop_places(deps)) == placed and seen == []


def test_a_stop_gone_during_the_session_is_still_rearmed(deps, factory):
    live_run(deps, factory)
    stop = exits(factory, "stop")[0]
    old = deps.tasty.orders[stop.broker_order_id]
    deps.tasty.orders[stop.broker_order_id] = replace(old, status=OrderStatus.EXPIRED)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "working" and exits(factory, "stop")[0].rearms == 1


# 10. A leg with no fill price.


def test_a_side_with_no_fill_price_gets_no_exits_and_an_alert(deps, factory, monkeypatch):
    monkeypatch.setattr(bot_entry, "place_protections", lambda d, r: None)
    run_id = live_run(deps, factory, target_pct="50")
    with factory() as session:
        run = session.get(BotRun, run_id)
        call_short = run.legs[0]["symbol"]
        run.filled_legs = [f for f in run.filled_legs if f["symbol"] != call_short]
        session.commit()
    seen = alerts_of(deps)
    bot_exits.place_protections(deps, run_id)
    by_side = {(e.kind, e.group_position): e for e in exits(factory)}
    assert by_side[("stop", 0)].state == "failed" and by_side[("stop", 0)].trigger is None
    assert by_side[("target", 0)].state == "failed"
    assert by_side[("stop", 1)].state == "working" and by_side[("target", 1)].state == "armed"
    assert any("no fill price" in e.detail and "no stop" in e.detail for e in seen)


# 12. _recover's alerts say when a side may have no stop.


def test_a_stop_that_cant_be_read_back_says_the_side_may_have_no_stop(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    deps.tasty.errors["get_order"] = RuntimeError("outage")  # every read fails
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert exits(factory, "target")[0].state == "failed"
    errors = [e for e in seen if "profit target error" in e.title]
    assert errors and "may have no stop" in errors[0].detail


def test_a_rearm_that_fails_during_recovery_says_the_side_may_have_no_stop(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    real, calls = deps.tasty.get_order, {"n": 0}

    def flaky(account, order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            deps.tasty.errors["positions"] = RuntimeError("outage")  # the re-arm can't size
            raise RuntimeError("hiccup")  # the confirm read; the cancel did land
        return real(account, order_id)

    deps.tasty.get_order = flaky
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    errors = [e for e in seen if "profit target error" in e.title]
    assert errors and "may have no stop" in errors[0].detail


def test_a_recovery_that_raises_says_the_side_may_have_no_stop(deps, factory, monkeypatch):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    deps.tasty.errors["get_order"] = RuntimeError("outage")  # the target fails part-way

    def broken(*args):
        raise RuntimeError("the re-arm or its commit broke")

    monkeypatch.setattr(bot_exits, "_settle_old_stop", broken)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    errors = [e for e in seen if "profit target error" in e.title]
    assert errors and "may have no stop" in errors[0].detail


# Account on the run.


def test_exits_keep_the_runs_account_when_the_bot_is_moved(deps, factory):
    run_id = live_run(deps, factory)
    bot = bot_store.load(factory, bot_of_run(factory, run_id))
    bot_store.save(factory, replace(bot, account_number="5WX09999"), OPEN_TIME)
    accounts = []
    for name in ("get_order", "option_positions", "place_order"):
        real = getattr(deps.tasty, name)

        def spy(account, *args, _real=real):
            accounts.append(account)
            return _real(account, *args)

        setattr(deps.tasty, name, spy)
    stop = exits(factory, "stop")[0]
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    assert exits(factory, "wing")[0].state in ("working", "filled")
    assert accounts and set(accounts) == {"5WX01234"}


# Slice 2: stop styles and units.


def styled_run(deps, factory, co_mode="off", co_amount=None, **side):
    """A live run whose sides take `side` (GroupDef fields), with the bot's closing order."""
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, **side) for g in bot.groups)
    styled = replace(bot, groups=groups, stop_co_mode=co_mode, stop_co_amount=co_amount)
    assert bot_store.save(factory, styled, OPEN_TIME)[1] == {}
    return bot_entry.fire(deps, bot_id, "09:47")


def triggers(factory):
    return [s.trigger for s in exits(factory, "stop")]


def test_a_closing_order_stop_is_dollars_off_the_whole_entry_credit(deps, factory):
    styled_run(deps, factory, stop_basis="closing_order", stop_pct=D("0.20"))
    # each short fills at 2.50 and each wing at 0.05: the entry is 4.90; 4.90 - 0.20 = 4.70
    assert triggers(factory) == [D("4.70"), D("4.70")]
    calc = exits(factory, "stop")[0].log[0]["stop_calc"]
    assert calc["basis"] == "closing_order" and D(calc["entry_credit"]) == D("4.90")


def test_entry_premium_stop_is_a_percent_of_the_entry_credit(deps, factory):
    styled_run(deps, factory, stop_basis="entry_premium", stop_pct=D("95"))
    assert triggers(factory) == [D("4.60"), D("4.60")]  # 4.655, rounded down to the $0.10 tick


def test_a_dollar_stop_rests_on_the_short_at_its_fill_plus_the_dollars(deps, factory):
    styled_run(deps, factory, co_mode="both", co_amount=D("0.50"),
               stop_unit="usd", stop_usd=D("2.00"), stop_pct=None)  # fmt: skip
    assert triggers(factory) == [D("4.50"), D("4.50")]  # 2.50 + 2.00; the overlay skips $ stops
    bodies = [c[2] for c in stop_places(deps)]
    assert len(bodies) == 2 and all(b["legs"][0]["action"] == "Buy to Close" for b in bodies)


def test_the_tighter_closing_order_wins_only_where_it_is_lower(deps, factory):
    deps.tasty.prices[sym(5690, "put")] = D("2.00")  # the put short fills at 2.00: entry 4.40
    styled_run(deps, factory, co_mode="tighter", co_amount=D("0.15"))
    # call: own 4.875, closing order 4.25 is lower -> 4.20; put: own 3.90 is lower -> 3.90
    assert triggers(factory) == [D("4.20"), D("3.90")]


def test_a_closing_order_at_or_above_the_entry_credit_is_refused_and_alerted(deps, factory):
    seen = alerts_of(deps)
    styled_run(deps, factory, stop_basis="closing_order", stop_pct=D("5.00"))
    assert {s.state for s in exits(factory, "stop")} == {"failed"}
    assert not stop_places(deps)
    assert any("a stop was not placed" in e.title and "no stop" in e.detail for e in seen)
    problem = exits(factory, "stop")[0].log[0]["stop_calc"]["problem"]
    assert "at or above the entry credit" in problem


def test_a_stop_that_would_fire_at_once_is_not_sent(deps, factory):
    seen = alerts_of(deps)
    styled_run(deps, factory, stop_basis="entry_premium", stop_pct=D("40"))  # 1.95 < 2.50
    assert {s.state for s in exits(factory, "stop")} == {"failed"} and not stop_places(deps)
    assert any("fire at once" in e.detail for e in seen)


def test_a_dollar_target_closes_at_the_entry_credit_less_the_dollars(deps, factory):
    styled_run(deps, factory, target_unit="usd", target_usd=D("1.00"))
    targets = exits(factory, "target")
    assert [t.state for t in targets] == ["armed", "armed"]
    assert targets[0].limit == D("1.45")  # side credit 2.50 - 0.05 = 2.45, less 1.00


def test_a_dollar_target_at_or_above_the_entry_credit_is_dropped_and_the_stop_stands(deps, factory):
    seen = alerts_of(deps)
    styled_run(deps, factory, target_unit="usd", target_usd=D("3.00"))
    assert {t.state for t in exits(factory, "target")} == {"failed"}
    assert {s.state for s in exits(factory, "stop")} == {"working"}
    assert any("profit target not set" in e.title for e in seen)


def test_the_exits_subscribe_the_wings_quotes(deps, factory):
    live_run(deps, factory)
    assert {s.long_symbol for s in exits(factory, "stop")} <= deps.hub.subscribed


# Slice 2: the wing after a stop.


def stop_out(deps, factory, bid=None, **side):
    """Fill the call side's stop and run the monitor; `bid` gives the wing a fresh quote."""
    styled_run(deps, factory, **side)
    stop = exits(factory, "stop")[0]
    if bid is not None:
        deps.hub.set(stop.long_symbol, D(bid), D(bid) + D("0.10"), OPEN_TIME)
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    return stop


def wing_orders(deps, factory):
    """Every single-leg order sent or replaced on the call side's wing."""
    long = plain(exits(factory, "stop")[0].long_symbol)
    return [
        c[-1]
        for c in deps.tasty.calls
        if c[0] in ("place", "replace")
        and len(c[-1]["legs"]) == 1
        and plain(c[-1]["legs"][0]["symbol"]) == long
    ]


def test_sold_now_with_a_live_bid_goes_at_market(deps, factory):
    stop_out(deps, factory, bid="0.30")
    sent = wing_orders(deps, factory)
    assert [b["order-type"] for b in sent] == ["Market"]
    assert sent[0]["legs"][0]["action"] == "Sell to Close"


def test_no_bid_rests_a_one_tick_limit_instead_of_a_market_order(deps, factory):
    stop_out(deps, factory)  # no quote for the wing at all
    assert [(b["order-type"], b["price"]) for b in wing_orders(deps, factory)] == [
        ("Limit", "0.05")
    ]
    assert exits(factory, "wing")[0].state in ("working", "filled")


def test_a_stale_bid_counts_as_no_bid(deps, factory):
    styled_run(deps, factory)
    stop = exits(factory, "stop")[0]
    deps.hub.set(stop.long_symbol, D("0.30"), D("0.40"), OPEN_TIME - timedelta(seconds=40))
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    assert [b["order-type"] for b in wing_orders(deps, factory)] == ["Limit"]


def test_a_market_order_the_broker_turns_down_falls_back_to_one_tick(deps, factory):
    real = deps.tasty.place_order

    def no_market(account, body):
        if body["order-type"] == "Market":
            raise BrokerError("market orders aren't accepted")
        return real(account, body)

    deps.tasty.place_order = no_market
    stop_out(deps, factory, bid="0.30")
    assert [(b["order-type"], b["price"]) for b in wing_orders(deps, factory)] == [
        ("Limit", "0.05")
    ]
    assert exits(factory, "wing")[0].state in ("working", "filled")


def test_a_central_check_refusal_never_falls_back(deps, factory, monkeypatch):
    styled_run(deps, factory)
    stop = exits(factory, "stop")[0]
    deps.hub.set(stop.long_symbol, D("0.30"), D("0.40"), OPEN_TIME)
    sent = []

    def refuse(session_factory, clients, request, **kw):
        sent.append(request.order)
        return orders.Result("refuse", False, "Refused by the order check.")

    monkeypatch.setattr(orders, "submit", refuse)
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    assert len(sent) == 1 and exits(factory, "wing")[0].state == "failed"


def test_sold_at_a_limit_rests_at_the_sides_price_rounded_down(deps, factory):
    deps.tasty.close = "work"
    stop_out(deps, factory, on_stop_remainder="close_limit", remainder_limit=D("0.33"))
    assert [(b["order-type"], b["price"]) for b in wing_orders(deps, factory)] == [
        ("Limit", "0.30")
    ]
    assert exits(factory, "wing")[0].state == "working"


def test_sold_at_a_limit_without_a_price_rests_at_the_bid(deps, factory):
    deps.tasty.close = "work"
    stop_out(deps, factory, bid="0.40", on_stop_remainder="close_limit")
    assert [(b["order-type"], b["price"]) for b in wing_orders(deps, factory)] == [
        ("Limit", "0.40")
    ]


def test_a_chased_wing_starts_at_the_mid_and_sells_the_rest_now_when_it_runs_out(deps, factory):
    deps.tasty.close = "work"
    stop_out(deps, factory, bid="0.30", on_stop_remainder="close_chase")  # bid 0.30, ask 0.40
    sent = wing_orders(deps, factory)
    assert sent[0]["price"] == "0.35" and sent[-1]["order-type"] == "Market"
    assert [b["order-type"] for b in sent[:-1]] == ["Limit"] * (len(sent) - 1)
    assert [w.state for w in exits(factory, "wing")] == ["cancelled", "working"]


def test_a_chased_wing_that_fills_is_sold(deps, factory):
    stop_out(deps, factory, bid="0.30", on_stop_remainder="close_chase")
    wings = exits(factory, "wing")
    assert [w.state for w in wings] == ["filled"] and wings[0].fill_price is not None


def test_an_unclear_chase_sends_nothing_more(deps, factory):
    deps.tasty.close = "work"
    deps.tasty.errors["replace"] = RequestUnclear("timed out")
    seen = alerts_of(deps)
    stop_out(deps, factory, bid="0.30", on_stop_remainder="close_chase")
    assert not [b for b in wing_orders(deps, factory) if b["order-type"] == "Market"]
    assert len(exits(factory, "wing")) == 1
    assert any("Check your broker" in e.detail for e in seen if "stop filled" in e.title)


def test_left_open_sends_nothing_and_says_so(deps, factory):
    seen = alerts_of(deps)
    stop_out(deps, factory, bid="0.30", on_stop_remainder="leave")
    assert not wing_orders(deps, factory)
    assert [w.state for w in exits(factory, "wing")] == ["left"]
    assert any("left open" in e.detail for e in seen)


def test_left_open_is_sold_now_while_leave_is_paused(deps, factory):
    bot_store.pause_leave(factory, None, OPEN_TIME)
    seen = alerts_of(deps)
    stop_out(deps, factory, bid="0.30", on_stop_remainder="leave")
    assert [b["order-type"] for b in wing_orders(deps, factory)] == ["Market"]
    assert any("paused" in e.detail for e in seen)


def test_a_left_wing_is_sold_once_leave_is_paused(deps, factory):
    stop_out(deps, factory, bid="0.30", on_stop_remainder="leave")
    bot_store.pause_leave(factory, None, OPEN_TIME)
    bot_exits.monitor_pass(deps)
    bot_exits.monitor_pass(deps)
    assert [b["order-type"] for b in wing_orders(deps, factory)] == ["Market"]
    states = [w.state for w in exits(factory, "wing")]
    assert states[0] == "cancelled" and states[1] in ("working", "filled") and len(states) == 2


def test_a_wing_limit_that_expires_at_expiry_is_quiet(deps, factory):
    deps.tasty.close = "work"
    stop_out(deps, factory, on_stop_remainder="close_limit", remainder_limit=D("0.30"))
    seen = alerts_of(deps)
    wing = exits(factory, "wing")[0]
    old = deps.tasty.orders[wing.broker_order_id]
    deps.tasty.orders[wing.broker_order_id] = replace(old, status=OrderStatus.EXPIRED)
    deps.now = lambda: OPEN_TIME + timedelta(hours=7)  # 16:47 New York, after the expiry close
    bot_exits.monitor_pass(deps)
    assert exits(factory, "wing")[0].state == "expired"
    assert not any("wing wasn't sold" in e.title for e in seen)


def refuse_every_order(monkeypatch):
    """The central check refuses every order from now on; returns what was asked for."""
    sent = []

    def refuse(session_factory, clients, request, **kw):
        sent.append(request.order)
        return orders.Result("refuse", False, "Refused by the order check.")

    monkeypatch.setattr(orders, "submit", refuse)
    return sent


def test_a_left_wing_that_cant_be_sold_says_it_wasnt_sold(deps, factory, monkeypatch):
    stop_out(deps, factory, bid="0.30", on_stop_remainder="leave")
    bot_store.pause_leave(factory, None, OPEN_TIME)
    refuse_every_order(monkeypatch)
    seen = alerts_of(deps)
    bot_exits.monitor_pass(deps)
    assert [w.state for w in exits(factory, "wing")] == ["cancelled", "failed"]
    assert [e.title for e in seen] == ["IC: a wing left open wasn't sold"]
    assert '"Leave" is paused' in seen[0].detail and "couldn't be sold" in seen[0].detail
    assert "Refused by the order check." in seen[0].detail
    assert "Check your broker" in seen[0].detail and "being sold" not in seen[0].detail


def test_a_left_wing_sale_that_breaks_part_way_alerts(deps, factory, monkeypatch):
    stop_out(deps, factory, bid="0.30", on_stop_remainder="leave")
    bot_store.pause_leave(factory, None, OPEN_TIME)

    def broken(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_exits, "_sell_now", broken)
    seen = alerts_of(deps)
    bot_exits.monitor_pass(deps)
    assert [w.state for w in exits(factory, "wing")] == ["cancelled"]  # never put back
    assert [e.title for e in seen] == ["IC: a wing left open wasn't sold"]
    assert "failed part-way. Check your broker." in seen[0].detail


def test_the_stop_alert_says_the_app_tried_when_a_paused_leave_sale_fails(
    deps, factory, monkeypatch
):
    bot_store.pause_leave(factory, None, OPEN_TIME)
    styled_run(deps, factory, on_stop_remainder="leave")
    stop = exits(factory, "stop")[0]
    deps.hub.set(stop.long_symbol, D("0.30"), D("0.40"), OPEN_TIME)
    refuse_every_order(monkeypatch)
    seen = alerts_of(deps)
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    detail = next(e.detail for e in seen if "stop filled" in e.title)
    assert "wasn't sold" in detail and "so the app tried to sell it" in detail
    assert "being sold" not in detail


def test_a_stop_fill_whose_wing_handling_breaks_still_alerts(deps, factory, monkeypatch):
    styled_run(deps, factory)
    stop = exits(factory, "stop")[0]

    def broken(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_exits, "_record_wings", broken)
    seen = alerts_of(deps)
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "filled"
    hit = [e for e in seen if "a stop filled but the wing wasn't handled" in e.title]
    assert len(hit) == 1 and stop.long_symbol.strip() in hit[0].detail
    assert "Check your broker" in hit[0].detail


def test_a_chase_the_broker_turns_down_first_is_sold_now(deps, factory):
    deps.tasty.close = "work"
    real, tries = deps.tasty.place_order, []

    def reject_first_wing(account, body):
        if len(body["legs"]) == 1 and body["legs"][0]["action"] == "Sell to Close":
            tries.append(body["order-type"])
            if len(tries) == 1:
                raise BrokerError("not now")
        return real(account, body)

    deps.tasty.place_order = reject_first_wing
    stop_out(deps, factory, bid="0.30", on_stop_remainder="close_chase")
    assert tries == ["Limit", "Market"]
    wings = exits(factory, "wing")
    assert [w.state for w in wings] == ["working"]
    events = [entry.get("event") for entry in wings[0].log]
    assert "the broker turned the chase down; selling now" in events


def test_a_chase_the_central_check_refuses_first_sends_nothing_more(deps, factory, monkeypatch):
    styled_run(deps, factory, on_stop_remainder="close_chase")
    stop = exits(factory, "stop")[0]
    deps.hub.set(stop.long_symbol, D("0.30"), D("0.40"), OPEN_TIME)
    sent = refuse_every_order(monkeypatch)
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    assert len(sent) == 1 and [w.state for w in exits(factory, "wing")] == ["failed"]


def test_a_chase_that_partly_fills_sells_exactly_the_rest(deps, factory):
    deps.tasty.close = "partial"
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, on_stop_remainder="close_chase") for g in bot.groups)
    bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)
    force_contracts(factory, bot_id, 2)
    bot_entry.fire(deps, bot_id, "09:47")
    stop = exits(factory, "stop")[0]
    deps.hub.set(stop.long_symbol, D("0.30"), D("0.40"), OPEN_TIME)
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    sent = wing_orders(deps, factory)
    assert [b["order-type"] for b in sent] == ["Limit", "Market"]
    assert sent[-1]["legs"][0]["quantity"] in (1, "1")
    wings = exits(factory, "wing")
    assert [(w.state, w.contracts) for w in wings] == [("cancelled", 1), ("working", 1)]


# Slice 3: closes run in the background, off the exits lock.


class Later:
    """A background hook that holds the job until the test runs it."""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn, *args):
        self.jobs.append((fn, args))

    def run(self):
        while self.jobs:
            fn, args = self.jobs.pop(0)
            fn(*args)


def lock_is_free():
    """Whether another thread could take the exits lock right now."""
    import threading

    got = []

    def probe():
        got.append(bot_exits.EXITS_LOCK.acquire(blocking=False))
        if got[0]:
            bot_exits.EXITS_LOCK.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return got[0]


class Crash(BaseException):
    """The app dying mid-close: not an Exception, so nothing in the job catches it."""


def test_a_target_close_runs_in_the_background_after_the_lock_is_released(deps, factory):
    live_run(deps, factory, target_pct="50")
    later = deps.background = Later()
    target = exits(factory, "target")[0]
    bot_exits.fire_target(deps, target.id)
    assert len(later.jobs) == 1 and lock_is_free()
    assert exits(factory, "target")[0].state == "firing"
    assert exits(factory, "stop")[0].state == "cancelled"
    assert target.id in bot_exits.FIRING and not side_closes(deps)
    bot_exits.monitor_pass(deps)  # the monitor leaves a close in progress alone
    assert exits(factory, "target")[0].state == "firing" and len(stop_places(deps)) == 2
    later.run()
    assert exits(factory, "target")[0].state == "filled" and len(side_closes(deps)) == 1
    assert target.id not in bot_exits.FIRING


def test_a_chased_wing_runs_in_the_background_and_is_chasing_meanwhile(deps, factory):
    seen = alerts_of(deps)
    later = deps.background = Later()
    stop_out(deps, factory, bid="0.30", on_stop_remainder="close_chase")
    wings = exits(factory, "wing")
    assert [w.state for w in wings] == ["chasing"] and not wing_orders(deps, factory)
    assert wings[0].id in bot_exits.FIRING and lock_is_free()
    bot_exits.monitor_pass(deps)
    assert [w.state for w in exits(factory, "wing")] == ["chasing"]
    assert not any("stop filled" in t for t in titles(seen))
    later.run()
    assert exits(factory, "wing")[0].state in ("filled", "cancelled", "working")
    assert wing_orders(deps, factory) and any("stop filled" in t for t in titles(seen))
    assert wings[0].id not in bot_exits.FIRING


def test_a_target_close_records_its_working_order(deps, factory):
    deps.tasty.close = "work"
    live_run(deps, factory, target_pct="50")
    recorded = []
    deps.sleep = lambda s: recorded.append(exits(factory, "target")[0].broker_order_id)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    first = side_closes(deps)[0][1]
    assert recorded[0] == first


def crash_mid_close(deps, factory):
    """Fire the call side's target and let the app die after the close went out."""
    deps.tasty.close = "work"
    live_run(deps, factory, target_pct="50")

    def die(seconds):
        raise Crash

    deps.sleep = die
    target = exits(factory, "target")[0]
    with pytest.raises(Crash):
        bot_exits.fire_target(deps, target.id)
    deps.sleep = lambda s: None
    assert target.id not in bot_exits.FIRING
    close_id = exits(factory, "target")[0].broker_order_id
    assert close_id == side_closes(deps)[0][1] and deps.tasty.orders[close_id].may_be_working
    return target.id, close_id


def test_a_close_cut_off_by_a_restart_is_cancelled_before_the_stop_is_put_back(deps, factory):
    target_id, close_id = crash_mid_close(deps, factory)
    seen = alerts_of(deps)
    bot_exits.monitor_pass(deps)
    cancel_at = deps.tasty.calls.index(("cancel", close_id))
    restop_at = deps.tasty.calls.index(stop_places(deps)[-1])
    assert len(stop_places(deps)) == 3 and cancel_at < restop_at
    assert exits(factory, "stop")[0].state == "working"
    assert exits(factory, "target")[0].state == "failed"
    assert any("profit target error" in t for t in titles(seen))


def test_a_close_cut_off_after_it_filled_is_not_rearmed_over(deps, factory):
    target_id, close_id = crash_mid_close(deps, factory)
    deps.tasty.fill(close_id)
    seen = alerts_of(deps)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "target")[0].state == "filled"
    assert exits(factory, "stop")[0].state == "cancelled" and len(stop_places(deps)) == 2
    assert any("profit target filled" in t for t in titles(seen))


def test_a_close_whose_order_wont_cancel_is_not_rearmed_and_says_so(deps, factory):
    target_id, close_id = crash_mid_close(deps, factory)
    deps.tasty.stuck.add(close_id)
    seen = alerts_of(deps)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "target")[0].state == "failed" and len(stop_places(deps)) == 2
    assert any("may still be working" in e.detail for e in seen)


def test_a_wing_chase_cut_off_by_a_restart_is_watched(deps, factory):
    def die(seconds):
        raise Crash

    styled_run(deps, factory, on_stop_remainder="close_chase")
    stop = exits(factory, "stop")[0]
    deps.hub.set(stop.long_symbol, D("0.30"), D("0.40"), OPEN_TIME)
    deps.tasty.close = "work"
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    deps.sleep = die
    with pytest.raises(Crash):
        bot_exits.monitor_pass(deps)
    deps.sleep = lambda s: None
    wing = exits(factory, "wing")[0]
    assert wing.state == "chasing" and wing.broker_order_id and wing.id not in bot_exits.FIRING
    seen = alerts_of(deps)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "wing")[0].state == "working"
    assert any("wing" in e.title and "restart" in e.detail for e in seen)


def test_a_pending_stop_is_held_back_while_its_side_closes(deps, factory):
    deps.tasty.close = "work"
    live_run(deps, factory, target_pct="50")
    with factory() as session:
        stop = session.get(BotExit, exits(factory, "stop")[0].id)
        deps.tasty.cancel_order("5WX01234", stop.broker_order_id)  # gone; its re-arm was held
        stop.state = "pending"  # held by Freeze, now off
        session.commit()
    later = deps.background = Later()
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert exits(factory, "stop")[0].state == "cancelled"
    bot_exits.monitor_pass(deps)
    assert len(stop_places(deps)) == 2  # not placed while its side is closing
    later.run()  # the close doesn't fill: the stop goes back
    assert exits(factory, "stop")[0].state == "working" and len(stop_places(deps)) == 3


# Slice 3: natural price, then leg out.


def natural_run(deps, factory, minutes=1):
    """A live run whose closes go to the natural price, then leg out."""
    bot_id = make(factory, live=True, exhaust="natural_then_leg_out")
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, target_pct=D("50")) for g in bot.groups)
    chase = replace(bot.exit_chase, natural_minutes=minutes)
    assert (
        bot_store.save(factory, replace(bot, groups=groups, exit_chase=chase), OPEN_TIME)[1] == {}
    )
    return bot_entry.fire(deps, bot_id, "09:47")


def exact_quotes(deps, short, long, short_ask, long_bid, at):
    """The short at ask - 0.10 x ask, the wing at bid x bid + 0.10, as exact decimals."""
    deps.hub.set(short, D(short_ask) - D("0.10"), D(short_ask), at)
    deps.hub.set(long, D(long_bid), D(long_bid) + D("0.10"), at)


def single_leg_orders(deps, symbol):
    """Every single-leg order on `symbol` other than a stop."""
    return [
        c[-1]
        for c in deps.tasty.calls
        if c[0] in ("place", "replace")
        and len(c[-1]["legs"]) == 1
        and c[-1]["order-type"] != "Stop"
        and plain(c[-1]["legs"][0]["symbol"]) == plain(symbol)
    ]


def combo_orders(deps):
    return [
        c[-1]
        for c in deps.tasty.calls
        if c[0] in ("place", "replace")
        and len(c[-1]["legs"]) == 2
        and c[-1]["legs"][0]["action"].endswith("Close")
    ]


def test_give_up_is_unchanged_for_existing_bots(deps, factory):
    deps.tasty.close = "work"
    live_run(deps, factory, target_pct="50")
    short, long = call_side(factory)
    quotes(deps, (short, long), 0.60, 0.05, OPEN_TIME)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert len(combo_orders(deps)) == 6  # the exit chase's tries, nothing after
    assert not single_leg_orders(deps, short) and not single_leg_orders(deps, long)
    assert exits(factory, "stop")[0].state == "working"


def test_natural_stage_reprices_to_the_current_natural(deps, factory):
    deps.tasty.close = "work"
    natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)  # 0.55 to pay
    sleeps = []

    def tick(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 9:  # into the natural stage: the short's ask moves up
            exact_quotes(deps, short, long, "0.85", "0.10", OPEN_TIME)  # 0.75 to pay

    deps.sleep = tick
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    prices = [(b["price"], b["price-effect"]) for b in combo_orders(deps)]
    assert prices[6] == ("0.55", "Debit") and ("0.75", "Debit") in prices[7:]
    assert all(b["order-type"] == "Limit" for b in combo_orders(deps))


def test_natural_then_leg_out_closes_the_short_first_then_sells_the_wing(deps, factory):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    combos = combo_orders(deps)
    assert combos and all(b["order-type"] == "Limit" for b in combos)  # never a two-leg market
    shorts, wings = single_leg_orders(deps, short), single_leg_orders(deps, long)
    assert [b["order-type"] for b in shorts] == ["Limit"] * 6 + ["Market"]
    assert {b["legs"][0]["action"] for b in shorts} == {"Buy to Close"}
    assert shorts[0]["price"] == "0.60"  # the short's mid, 0.60, on its $0.05 tick
    calls = [c[-1] for c in deps.tasty.calls if c[0] in ("place", "replace")]
    assert calls.index(shorts[-1]) < calls.index(wings[0])  # the short is bought back first
    assert {b["legs"][0]["action"] for b in wings} == {"Sell to Close"}
    assert exits(factory, "target")[0].state == "filled"
    assert exits(factory, "stop")[0].state == "cancelled" and len(stop_places(deps)) == 2
    assert [w.kind for w in exits(factory, "wing")]
    assert any("profit target filled" in e.title and "legged out" in e.detail for e in seen)


def test_the_wing_is_not_sold_when_the_short_market_order_does_not_fill(deps, factory):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    natural_run(deps, factory)
    short, long = call_side(factory)
    deps.tasty.market_works.add(plain(short))
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert single_leg_orders(deps, short)[-1]["order-type"] == "Market"
    assert not single_leg_orders(deps, long)
    assert exits(factory, "target")[0].state == "failed"
    assert len(stop_places(deps)) == 2  # never a stop on top of a working close
    assert any("Check your broker" in e.detail and "wing" in e.detail for e in seen)


def test_a_position_changed_by_someone_else_is_not_legged_out(deps, factory, monkeypatch):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    monkeypatch.setattr(bot_exits, "_positions", lambda deps, account: {plain(long): 1})
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert not single_leg_orders(deps, short) and not single_leg_orders(deps, long)
    assert exits(factory, "target")[0].state == "failed"
    assert any("changed" in e.detail for e in seen)


def test_an_unclear_natural_stage_sends_nothing_more(deps, factory):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    real, places = deps.tasty.place_order, {"combo": 0}

    def place(account, body):
        if len(body["legs"]) == 2 and body["legs"][0]["action"].endswith("Close"):
            places["combo"] += 1
            if places["combo"] == 2:  # the natural stage's first order
                raise RequestUnclear("timed out")
        return real(account, body)

    deps.tasty.place_order = place
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert not single_leg_orders(deps, short) and not single_leg_orders(deps, long)
    assert exits(factory, "target")[0].state == "failed" and len(stop_places(deps)) == 2
    assert any("unclear" in e.title for e in seen)


def test_no_leg_out_after_the_session_ends(deps, factory):
    deps.tasty.close = "work"
    natural_run(deps, factory)
    late = OPEN_TIME.replace(hour=20, minute=5)  # 16:05 New York
    deps.now = lambda: late
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", late)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert len(combo_orders(deps)) == 6  # the chase, then nothing after the close
    assert not single_leg_orders(deps, short) and not single_leg_orders(deps, long)
    assert exits(factory, "target")[0].state == "failed"


def test_no_fresh_short_quote_puts_the_stop_back(deps, factory):
    deps.tasty.close = "work"
    natural_run(deps, factory)
    short, long = call_side(factory)  # no quotes at all: no natural, no mid for the short
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert len(combo_orders(deps)) == 6
    assert not single_leg_orders(deps, short) and not single_leg_orders(deps, long)
    assert exits(factory, "stop")[0].state == "working" and len(stop_places(deps)) == 3


# Keep conceding (Tracker2's EXHAUST_KEEP): past the last try, one step at a time.


def keep_run(deps, factory):
    """A live run whose closes keep conceding after their last try."""
    bot_id = make(factory, live=True, exhaust="keep_conceding")
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, target_pct=D("50")) for g in bot.groups)
    assert bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)[1] == {}
    return bot_entry.fire(deps, bot_id, "09:47")


def debits(deps):
    return [D(b["price"]) for b in combo_orders(deps)]


def test_keep_conceding_steps_past_the_last_try_until_filled(deps, factory):
    deps.tasty.close = "work"
    keep_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "2.10", "0.10", OPEN_TIME)  # natural 2.00: far away

    def tick(seconds):
        if len(combo_orders(deps)) >= 8:
            deps.tasty.close = "fill"  # the third order past the last try fills

    deps.sleep = tick
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    paid = debits(deps)
    assert len(paid) == 9  # the chase's 6 tries, then 3 more
    step = paid[1] - paid[0]
    assert step > 0 and all(b - a == step for a, b in zip(paid, paid[1:], strict=False))
    assert exits(factory, "target")[0].state == "filled"
    assert all(b["order-type"] == "Limit" for b in combo_orders(deps))


def test_keep_conceding_never_prices_past_the_natural(deps, factory):
    deps.tasty.close = "work"
    keep_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "1.70", "0.10", OPEN_TIME)  # 1.60 to pay at the natural
    sleeps = []

    def tick(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 30:  # the order waiting at the natural fills
            [oid] = [o.broker_order_id for o in deps.tasty.orders.values()
                     if o.may_be_working and len(o.legs) == 2]  # fmt: skip
            deps.tasty.fill(oid)

    deps.sleep = tick
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    paid = debits(deps)
    assert paid[5] == D("1.50")  # the chase's last try
    assert paid[6:] == [D("1.55"), D("1.60")]  # then it waits at the natural, never past
    assert len(sleeps) == 30
    assert exits(factory, "target")[0].state == "filled"


def test_keep_conceding_stops_at_the_session_close(deps, factory):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    keep_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "2.10", "0.10", OPEN_TIME)
    sleeps = []

    def tick(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 10:
            deps.now = lambda: LATE

    deps.sleep = tick
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert len(combo_orders(deps)) > 6
    assert exits(factory, "target")[0].state == "failed"
    assert any("the session ended" in e.detail for e in seen)
    stops_out = {e.broker_order_id for e in exits(factory, "stop")}
    working = {o.broker_order_id for o in deps.tasty.orders.values() if o.may_be_working}
    assert working <= stops_out  # nothing of the close left working


def test_keep_conceding_obeys_cancel(deps, factory):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    keep_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "2.10", "0.10", OPEN_TIME)
    target_id = exits(factory, "target")[0].id
    sleeps = []

    def tick(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 10:
            with factory() as session:
                session.get(BotExit, target_id).cancel_requested_at = OPEN_TIME
                session.commit()

    deps.sleep = tick
    bot_exits.fire_target(deps, target_id)
    assert len(combo_orders(deps)) > 6
    assert exits(factory, "target")[0].state == "cancelled"
    assert any("Close cancelled" in e.title for e in seen)
    stops_out = {e.broker_order_id for e in exits(factory, "stop")}
    working = {o.broker_order_id for o in deps.tasty.orders.values() if o.may_be_working}
    assert working <= stops_out


# Slice 3: cancel a running close.


def test_cancel_is_recorded_only_while_the_close_runs(deps, factory):
    run_id = live_run(deps, factory, target_pct="50")
    target = exits(factory, "target")[0]
    assert not bot_exits.request_cancel(factory, run_id, target.id, OPEN_TIME)  # armed
    deps.background = Later()
    bot_exits.fire_target(deps, target.id)
    assert bot_exits.request_cancel(factory, run_id, target.id, OPEN_TIME)
    assert exits(factory, "target")[0].cancel_requested_at == OPEN_TIME
    assert not bot_exits.request_cancel(factory, run_id + 1, target.id, OPEN_TIME)  # not its run
    stop = exits(factory, "stop")[0]
    assert not bot_exits.request_cancel(factory, run_id, stop.id, OPEN_TIME)  # not a target


def test_cancel_stops_a_running_close_and_puts_the_stop_back(deps, factory):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    run_id = live_run(deps, factory, target_pct="50")
    target = exits(factory, "target")[0]
    sleeps = []

    def press_cancel_on_the_third_wait(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            assert bot_exits.request_cancel(factory, run_id, target.id, OPEN_TIME)

    deps.sleep = press_cancel_on_the_third_wait
    bot_exits.fire_target(deps, target.id)
    last_close = combo_orders(deps)[-1]
    assert len(combo_orders(deps)) == 3  # the send and two replaces, then it stopped
    close_ids = [c[-2] for c in deps.tasty.calls if c[0] == "replace"]
    assert ("cancel", close_ids[-1]) in deps.tasty.calls and last_close["order-type"] == "Limit"
    assert exits(factory, "target")[0].state == "cancelled"
    stop = exits(factory, "stop")[0]
    assert stop.state == "working" and len(stop_places(deps)) == 3
    cancelled = [e for e in seen if "Close cancelled" in e.title]
    assert (
        cancelled
        and "closed 0 of 1" in cancelled[0].detail
        and "stop is back" in cancelled[0].detail
    )


def test_a_cancel_that_is_not_confirmed_rearms_nothing(deps, factory):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    run_id = live_run(deps, factory, target_pct="50")
    target = exits(factory, "target")[0]
    real_place = deps.tasty.place_order

    def stuck_close(account, body):
        oid = real_place(account, body)
        if len(body["legs"]) == 2:
            deps.tasty.stuck.add(oid)  # the broker ignores its cancel
        return oid

    deps.tasty.place_order = stuck_close
    deps.sleep = lambda s: bot_exits.request_cancel(factory, run_id, target.id, OPEN_TIME)
    bot_exits.fire_target(deps, target.id)
    assert exits(factory, "target")[0].state == "failed" and len(stop_places(deps)) == 2
    assert any("Check your broker" in e.detail for e in seen)
    assert not any("Close cancelled" in e.title and "stop is back" in e.detail for e in seen)


def test_a_cancel_during_the_natural_stage_stops_it(deps, factory):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    run_id = natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    target = exits(factory, "target")[0]
    sleeps = []

    def press_cancel_in_the_natural_stage(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 8:
            bot_exits.request_cancel(factory, run_id, target.id, OPEN_TIME)

    deps.sleep = press_cancel_in_the_natural_stage
    bot_exits.fire_target(deps, target.id)
    assert not single_leg_orders(deps, short) and not single_leg_orders(deps, long)
    assert exits(factory, "target")[0].state == "cancelled"
    assert exits(factory, "stop")[0].state == "working"
    assert any("Close cancelled" in e.title for e in seen)


# Final review fixes.


def hold_call_stop_for_freeze(deps, factory):
    """The call side's stop as Freeze leaves it: "pending", never placed (no order id)."""
    with factory() as session:
        stop = session.get(BotExit, exits(factory, "stop")[0].id)
        deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
        stop.state, stop.broker_order_id = "pending", None
        session.commit()
        return stop.id


def test_a_pending_stop_claimed_by_a_close_that_breaks_goes_back_to_pending(
    deps, factory, monkeypatch
):
    deps.tasty.close = "work"
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    stop_id = hold_call_stop_for_freeze(deps, factory)
    real = bot_exits._stage

    def broken(*args, **kwargs):
        raise RuntimeError("the job broke")

    monkeypatch.setattr(bot_exits, "_stage", broken)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    monkeypatch.setattr(bot_exits, "_stage", real)
    with factory() as session:
        assert session.get(BotExit, stop_id).state == "pending"
    assert not any("still in place" in e.detail for e in seen)
    assert any("never placed" in e.detail for e in seen)
    placed = len(stop_places(deps))
    bot_exits.monitor_pass(deps)
    assert len(stop_places(deps)) == placed + 1
    with factory() as session:
        stop = session.get(BotExit, stop_id)
        assert stop.state == "working" and stop.broker_order_id


def test_a_pending_stop_claimed_by_a_close_cut_off_by_a_restart_is_placed(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    stop_id = hold_call_stop_for_freeze(deps, factory)
    deps.background = Later()  # the close never runs: the app restarted
    target_id = exits(factory, "target")[0].id
    bot_exits.fire_target(deps, target_id)
    bot_exits.FIRING.discard(target_id)
    placed = len(stop_places(deps))
    bot_exits.recover_targets(deps)
    with factory() as session:
        assert session.get(BotExit, stop_id).state == "pending"
        assert session.get(BotExit, target_id).state == "failed"
    assert not any("still in place" in e.detail for e in seen)
    assert any("never placed" in e.detail for e in seen)
    bot_exits.monitor_pass(deps)
    assert len(stop_places(deps)) == placed + 1
    with factory() as session:
        stop = session.get(BotExit, stop_id)
        assert stop.state == "working" and stop.broker_order_id


def test_a_close_whose_cancel_lands_late_is_read_again_before_the_stop_goes_back(deps, factory):
    target_id, close_id = crash_mid_close(deps, factory)
    real_cancel, real_get = deps.tasty.cancel_order, deps.tasty.get_order
    late = set()

    def slow_cancel(account, order_id):
        late.add(order_id)  # the broker takes a moment to cancel it

    def get(account, order_id):
        found = real_get(account, order_id)
        if order_id in late:
            late.discard(order_id)
            real_cancel(account, order_id)
        return found

    deps.tasty.cancel_order, deps.tasty.get_order = slow_cancel, get
    waits = []
    deps.sleep = waits.append
    bot_exits.monitor_pass(deps)
    assert 1 in waits  # read again a second later
    assert exits(factory, "stop")[0].state == "working" and len(stop_places(deps)) == 3


def test_a_leg_out_wing_is_never_seen_chasing_without_its_job(deps, factory, monkeypatch):
    import threading

    deps.tasty.close = "work"
    seen = alerts_of(deps)
    natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    real = bot_exits._new_wing_row

    def then_the_monitor_runs(*args):
        wing_id = real(*args)
        t = threading.Thread(target=bot_exits.monitor_pass, args=(deps,))
        t.start()  # the monitor, on its own thread, the moment the row is written
        t.join(timeout=10)
        assert not t.is_alive()
        return wing_id

    monkeypatch.setattr(bot_exits, "_new_wing_row", then_the_monitor_runs)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert not any("restart" in e.detail for e in seen)
    assert [w.state for w in exits(factory, "wing")] != ["failed"]
    assert single_leg_orders(deps, long)  # the wing was sold


def test_a_recovery_that_cant_load_the_bot_still_alerts(deps, factory, monkeypatch):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)

    def broken(*args):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(bot_store, "load", broken)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)  # never raises
    assert exits(factory, "target")[0].state == "failed"
    assert exits(factory, "stop")[0].state == "working"  # its cancel was never sent
    assert any("profit target error" in e.title for e in seen)


LATE = OPEN_TIME.replace(hour=20, minute=5)  # 16:05 New York: the session is over


def test_a_leg_out_never_sells_the_wing_after_the_session_ends(deps, factory, monkeypatch):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    real = bot_exits._buy_back

    def then_the_bell(*args):
        out = real(*args)
        deps.now = lambda: LATE
        exact_quotes(deps, short, long, "0.65", "0.10", LATE)
        return out

    monkeypatch.setattr(bot_exits, "_buy_back", then_the_bell)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert single_leg_orders(deps, short)[-1]["order-type"] == "Market"  # bought back in time
    assert not single_leg_orders(deps, long)
    assert any("wasn't sold" in e.detail and "session" in e.detail for e in seen)


def test_a_leg_out_wing_chase_that_runs_past_the_close_ends_at_one_tick(deps, factory, monkeypatch):
    deps.tasty.close = "work"
    natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    real = bot_exits._new_wing_row

    def bell_during_the_wing_chase(*args):
        wing_id = real(*args)
        deps.now = lambda: LATE
        exact_quotes(deps, short, long, "0.65", "0.30", LATE)  # a fresh bid on the wing
        return wing_id

    monkeypatch.setattr(bot_exits, "_new_wing_row", bell_during_the_wing_chase)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    wings = single_leg_orders(deps, long)
    assert wings and all(b["order-type"] == "Limit" for b in wings)  # never at market
    assert wings[-1]["price"] == "0.05"  # what the chase didn't sell rests at one tick


def test_a_leg_out_whose_wing_isnt_held_says_so(deps, factory, monkeypatch):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    monkeypatch.setattr(bot_exits, "_positions", lambda deps, account: {plain(short): -1})
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert not single_leg_orders(deps, long)
    filled = [e for e in seen if "profit target filled" in e.title]
    assert filled and "wasn't held" in filled[0].detail and "wing sold" not in filled[0].detail


def test_a_cancel_pressed_before_the_close_is_sent_sends_nothing(deps, factory):
    seen = alerts_of(deps)
    run_id = live_run(deps, factory, target_pct="50")
    later = deps.background = Later()
    target = exits(factory, "target")[0]
    bot_exits.fire_target(deps, target.id)
    assert bot_exits.request_cancel(factory, run_id, target.id, OPEN_TIME)
    later.run()
    assert not side_closes(deps)
    assert exits(factory, "target")[0].state == "cancelled"
    assert exits(factory, "stop")[0].state == "working" and len(stop_places(deps)) == 3
    assert any("Close cancelled" in e.title for e in seen)


def test_cancel_reaches_a_close_until_it_legs_out_at_market(deps, factory):
    live_run(deps, factory, target_pct="50")
    deps.background = Later()
    target = exits(factory, "target")[0]
    bot_exits.fire_target(deps, target.id)
    assert bot_exits.cancel_reaches(factory, target.id)
    with factory() as session:
        row = session.get(BotExit, target.id)
        row.log = [*row.log, {"stage": "leg_out", "leg": row.short_symbol, "market": 1}]
        session.commit()
    assert not bot_exits.cancel_reaches(factory, target.id)


def test_cancel_doesnt_reach_the_wing_stage(deps, factory):
    live_run(deps, factory, target_pct="50")
    deps.background = Later()
    target = exits(factory, "target")[0]
    bot_exits.fire_target(deps, target.id)
    with factory() as session:
        row = session.get(BotExit, target.id)
        row.log = [*row.log, {"stage": "leg_out", "leg": row.short_symbol, "status": "filled"}]
        session.commit()
    assert not bot_exits.cancel_reaches(factory, target.id)


# Real threads: the claim, and the monitor alongside a running close.


def wait_until(check, seconds=5.0):
    import time

    end = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def test_two_fires_of_one_target_at_once_claim_it_once(deps, factory, monkeypatch):
    import threading

    live_run(deps, factory, target_pct="50")
    jobs = []
    deps.background = lambda fn, *args: jobs.append((fn, args))
    target_id = exits(factory, "target")[0].id
    entered, release = threading.Event(), threading.Event()
    real = bot_exits._claim_target

    def held_claim(deps, exit_id):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)  # the second fire arrives while this claim is going on
        return real(deps, exit_id)

    monkeypatch.setattr(bot_exits, "_claim_target", held_claim)
    first = threading.Thread(target=bot_exits.fire_target, args=(deps, target_id))
    second = threading.Thread(target=bot_exits.fire_target, args=(deps, target_id))
    first.start()
    assert entered.wait(5)
    second.start()
    second.join(timeout=0.05)
    assert second.is_alive()  # waiting on the exits lock
    release.set()
    first.join(5)
    second.join(5)
    assert len(jobs) == 1
    fn, args = jobs[0]
    fn(*args)
    assert len(side_closes(deps)) == 1 and exits(factory, "target")[0].state == "filled"
    assert target_id not in bot_exits.FIRING


def test_the_monitor_leaves_a_close_running_on_its_own_thread_alone(deps, factory):
    import threading

    deps.tasty.close = "work"
    live_run(deps, factory, target_pct="50")
    deps.background = bot_entry.in_background  # a real thread
    mid_chase, go = threading.Event(), threading.Event()

    def gated(seconds):
        if threading.current_thread() is not threading.main_thread() and not mid_chase.is_set():
            mid_chase.set()
            assert go.wait(5)

    deps.sleep = gated
    target_id = exits(factory, "target")[0].id
    bot_exits.fire_target(deps, target_id)
    assert mid_chase.wait(5)  # the close is out and the job is waiting on it
    close_id = exits(factory, "target")[0].broker_order_id
    assert deps.tasty.orders[close_id].may_be_working
    bot_exits.monitor_pass(deps)  # on this thread, while the close works
    bot_exits.recover_targets(deps)
    assert len(side_closes(deps)) == 1 and len(stop_places(deps)) == 2
    assert exits(factory, "target")[0].state == "firing"
    assert exits(factory, "stop")[0].state == "cancelled"
    go.set()
    wait_until(lambda: target_id not in bot_exits.FIRING)
    # The close ran out of tries and gave up: its order cancelled, then the stop back.
    assert not deps.tasty.orders[exits(factory, "target")[0].broker_order_id].may_be_working
    assert exits(factory, "target")[0].state == "failed" and len(stop_places(deps)) == 3
    assert len(side_closes(deps)) == 1


# Re-review: a pending stop that still carries its old order id, and the short's market order.


def hold_call_stop_with_its_old_id(deps, factory):
    """A stop re-armed while frozen: "pending", its old (cancelled) order id still on the row."""
    with factory() as session:
        stop = session.get(BotExit, exits(factory, "stop")[0].id)
        deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
        stop.state = "pending"
        session.commit()
        return stop.id


def test_a_rearm_held_by_freeze_drops_the_old_order_id(deps, factory):
    live_run(deps, factory)
    stop = exits(factory, "stop")[0]
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    orders.set_switch(factory, "freeze", True)
    with bot_exits.EXITS_LOCK:
        assert not bot_exits.rearm(deps, stop.id)
    stop = exits(factory, "stop")[0]
    assert stop.state == "pending" and stop.broker_order_id is None


def test_a_pending_stop_with_an_old_id_goes_back_to_pending_when_the_job_breaks(
    deps, factory, monkeypatch
):
    deps.tasty.close = "work"
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    stop_id = hold_call_stop_with_its_old_id(deps, factory)

    def broken(*args, **kwargs):
        raise RuntimeError("the job broke")

    monkeypatch.setattr(bot_exits, "_stage", broken)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    monkeypatch.undo()
    with factory() as session:
        assert session.get(BotExit, stop_id).state == "pending"
    assert not any("still in place" in e.detail for e in seen)
    placed = len(stop_places(deps))
    bot_exits.monitor_pass(deps)
    assert len(stop_places(deps)) == placed + 1


def test_a_pending_stop_with_an_old_id_goes_back_to_pending_if_the_close_cant_start(deps, factory):
    live_run(deps, factory, target_pct="50")
    seen = alerts_of(deps)
    stop_id = hold_call_stop_with_its_old_id(deps, factory)

    def no_thread(fn, *args):
        raise RuntimeError("can't start new thread")

    deps.background = no_thread
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    with factory() as session:
        assert session.get(BotExit, stop_id).state == "pending"
    assert not any("still in place" in e.detail for e in seen)
    placed = len(stop_places(deps))
    bot_exits.monitor_pass(deps)
    assert len(stop_places(deps)) == placed + 1


def market_ids(deps, symbol):
    return {
        c[1]
        for c in deps.tasty.calls
        if c[0] == "place"
        and c[2]["order-type"] == "Market"
        and plain(c[2]["legs"][0]["symbol"]) == plain(symbol)
    }


def test_a_market_buy_back_that_cant_be_recorded_puts_no_stop_back(deps, factory, monkeypatch):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    natural_run(deps, factory)
    short, long = call_side(factory)
    deps.tasty.market_works.add(plain(short))  # the market buy hasn't filled yet
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    real = bot_exits._note_order

    def note(deps_, exit_id, order_id):
        if order_id in market_ids(deps, short):
            raise RuntimeError("database is locked")
        return real(deps_, exit_id, order_id)

    monkeypatch.setattr(bot_exits, "_note_order", note)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert market_ids(deps, short) and not single_leg_orders(deps, long)
    assert len(stop_places(deps)) == 2  # never a stop on top of a live market buy
    assert exits(factory, "stop")[0].state == "cancelled"  # no re-arm was even tried
    bot_exits.monitor_pass(deps)
    assert len(stop_places(deps)) == 2 and exits(factory, "stop")[0].state == "cancelled"
    assert any("market" in e.detail and "Check your broker" in e.detail for e in seen)


def test_a_log_write_that_breaks_after_the_market_buy_puts_no_stop_back(deps, factory, monkeypatch):
    deps.tasty.close = "work"
    seen = alerts_of(deps)
    natural_run(deps, factory)
    short, long = call_side(factory)
    deps.tasty.market_works.add(plain(short))
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    real_stage, real_note = bot_exits._stage, bot_exits._note_order

    def stage(deps_, close, name, **entry):
        if "market" in entry:
            raise RuntimeError("database is locked")
        return real_stage(deps_, close, name, **entry)

    def note(deps_, exit_id, order_id):
        if order_id in market_ids(deps, short):
            raise RuntimeError("database is locked")
        return real_note(deps_, exit_id, order_id)

    monkeypatch.setattr(bot_exits, "_stage", stage)
    monkeypatch.setattr(bot_exits, "_note_order", note)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert market_ids(deps, short) and not single_leg_orders(deps, long)
    assert len(stop_places(deps)) == 2 and exits(factory, "stop")[0].state == "cancelled"
    assert any("market" in e.detail and "Check your broker" in e.detail for e in seen)


def test_a_stop_held_by_freeze_is_never_a_routine_alert_and_names_its_run(deps, factory):
    seen = alerts_of(deps)
    deps.sleep = lambda s: orders.set_switch(factory, "freeze", True)
    live_run(deps, factory, target_pct="50")
    [event] = [e for e in seen if "waiting for Freeze" in e.title]
    assert not event.routine and event.run_id == exits(factory, "stop")[0].run_id


def test_a_target_that_filled_is_routine(deps, factory):
    target_id, close_id = crash_mid_close(deps, factory)
    deps.tasty.fill(close_id)
    seen = alerts_of(deps)
    bot_exits.monitor_pass(deps)
    [event] = [e for e in seen if "profit target filled" in e.title]
    assert event.routine and event.bot_id is not None


def test_a_tastytrade_run_keeps_its_broker_after_the_bot_changes(deps, factory):
    """Exits follow the run: a bot moved to Schwab after its trade filled still
    re-arms that trade's stop at TastyTrade, and never asks another broker."""
    run_id = live_run(deps, factory)
    with factory() as session:
        bot = session.get(Bot, session.get(BotRun, run_id).bot_id)
        bot.broker = "schwab"
        session.commit()
    asked = []

    class Other:
        broker, name = "schwab", "Schwab"

        def __getattr__(self, name):
            asked.append(name)
            raise AssertionError(f"the Schwab client was asked for {name}")

    deps.clients = lambda: [deps.tasty, Other()]
    stop = exits(factory, "stop")[0]
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    bot_exits.monitor_pass(deps)
    stop = exits(factory, "stop")[0]
    assert stop.state == "working" and stop.rearms == 1
    assert asked == []


def test_exit_legs_reads_legs_and_only_falls_back_for_rows_without_them():
    from optionsjourney.bots.exit_math import GroupLeg
    from optionsjourney.trading.orders import Side

    old = BotExit(short_symbol="S", long_symbol="L", legs=[])
    assert bot_exits.exit_legs(old) == (GroupLeg("S", Side.SELL, 1), GroupLeg("L", Side.BUY, 1))
    new = BotExit(
        short_symbol="S",
        long_symbol="L",
        legs=[
            {"symbol": "A", "side": "sell", "ratio": 2},
            {"symbol": "B", "side": "buy", "ratio": 1},
        ],
    )
    assert bot_exits.exit_legs(new) == (GroupLeg("A", Side.SELL, 2), GroupLeg("B", Side.BUY, 1))
    wing = BotExit(
        short_symbol="S", long_symbol="L", legs=[{"symbol": "L", "side": "buy", "ratio": 1}]
    )
    assert bot_exits.exit_legs(wing) == (GroupLeg("L", Side.BUY, 1),)


def test_group_units_is_run_contracts_times_the_gcd_less_closed_units(deps, factory):
    live_run(deps, factory)
    with factory() as session:
        run = session.scalars(select(BotRun)).one()
        run.contracts = 3
        run.legs = [
            {"group": 0, "symbol": "A", "side": "sell", "ratio": 2},
            {"group": 0, "symbol": "B", "side": "buy", "ratio": 4},
        ]
        session.add(
            BotExit(
                run_id=run.id, group_position=0, kind="close", state="reduced",
                short_symbol="A", long_symbol="B", contracts=1, log=[{"closed": 2}],
                created_at=OPEN_TIME, updated_at=OPEN_TIME,
            )
        )  # fmt: skip
        session.flush()
        assert bot_exits.group_units(session, run, 0) == 3 * 2 - 2


# Slice 13, Task 7: each group's protections, per kind.


def shape_run(deps, factory, key, stop=None, target=None, contracts=None, ratio=None):
    """A live run of a preset shape (bot_fakes.make_shape), `stop`/`target` % on every
    group. `ratio` sets the first leg's ratio (a credit put ratio spread from the put
    credit spread); `contracts` sizes the run past validation's one contract."""
    bot_id = make_shape(factory, key, live=True, stop=stop, target=target)
    if ratio is not None:
        bot = bot_store.load(factory, bot_id)
        legs = tuple(replace(leg, ratio=ratio) if leg.position == 0 else leg for leg in bot.legs)
        sized = replace(bot, legs=legs, max_per_trade=bot.contracts * ratio)
        assert bot_store.save(factory, sized, OPEN_TIME)[1] == {}
    if contracts is not None or ratio is not None:
        with factory() as session:
            row = session.get(Bot, bot_id)
            row.contracts = contracts or row.contracts
            row.max_per_trade = row.contracts * (ratio or 1)
            row.max_per_day = 20
            session.commit()
    return bot_entry.fire(deps, bot_id, "09:47")


def closing_sent(deps):
    """Every order sent that isn't an opening one (stops, closes), without its id."""
    return [
        {k: v for k, v in c[2].items() if k != "external-identifier"}
        for c in deps.tasty.calls
        if c[0] == "place" and not c[2]["legs"][0]["action"].endswith("Open")
    ]


def test_a_ratio_2_anchor_stop_rests_for_units_times_two(deps, factory):
    # A credit put ratio spread: sell 2 of the 5690 put (2.50 each), buy 1 of the 5615
    # put (0.05). 3 units: a stop of 6 contracts on the sold put.
    shape_run(deps, factory, "put_credit_spread", stop=D("50"), contracts=3, ratio=2)
    (stop,) = exits(factory, "stop")
    assert stop.state == "working" and stop.contracts == 3
    assert stop.anchor_symbol == _PUT_SHORT and stop.watched is False
    assert stop.short_symbol == _PUT_SHORT and stop.long_symbol == _PUT_WING
    assert stop.legs == [
        {"symbol": _PUT_SHORT, "side": "sell", "ratio": 2},
        {"symbol": _PUT_WING, "side": "buy", "ratio": 1},
    ]
    assert stop.trigger == D("3.70")  # the body's fill 2.50 x 1.50 = 3.75, rounded down
    (sent,) = closing_sent(deps)
    assert sent["order-type"] == "Stop" and sent["stop-trigger"] == "3.70"
    assert sent["legs"] == [{"instrument-type": "Equity Option", "symbol": _PUT_SHORT,
                             "quantity": 6, "action": "Buy to Close"}]  # fmt: skip


def test_a_naked_short_rests_its_stop_and_has_no_other_legs(deps, factory):
    shape_run(deps, factory, "short_put", stop=D("50"), target=D("50"))
    stop, target = exits(factory, "stop")[0], exits(factory, "target")[0]
    assert (stop.state, stop.trigger, stop.contracts) == ("working", D("3.70"), 1)
    assert (stop.short_symbol, stop.long_symbol, stop.anchor_symbol) == (_PUT_SHORT, "", _PUT_SHORT)
    assert stop.legs == [{"symbol": _PUT_SHORT, "side": "sell", "ratio": 1}]
    assert (target.state, target.limit) == ("armed", D("1.25"))  # 2.50 credit, half kept
    (sent,) = closing_sent(deps)
    assert sent["order-type"] == "Stop" and sent["legs"][0]["symbol"] == _PUT_SHORT


def test_a_watched_group_watches_its_stop_and_sends_nothing(deps, factory):
    # A long call bought for 2.50 (a debit: entry -2.50), stop at 50% of the entry lost.
    run_id = shape_run(deps, factory, "long_call", stop=D("50"))
    with factory() as session:
        assert session.get(BotRun, run_id).broker == "tastytrade"
    (stop,) = exits(factory, "stop")
    assert stop.state == bot_exits.WATCHING and stop.watched is True
    assert stop.anchor_symbol is None and stop.short_symbol == ""
    assert stop.long_symbol == _CALL_SHORT  # the 5910 call, the group's first leg
    assert stop.trigger == -abs(D("-2.50")) / 2
    assert stop.log[0]["stop_calc"]["basis"] == "group_entry"
    assert stop.broker_order_id is None and closing_sent(deps) == []


def test_a_debit_groups_target_fires_on_its_natural_value(deps, factory, monkeypatch):
    run_id = shape_run(deps, factory, "long_call", target=D("50"))
    arm_the_target(deps, factory, run_id)  # a trade opened before decision 0048
    (target,) = exits(factory, "target")
    assert target.state == "armed" and target.limit == D("-3.75")  # entry -2.50 less 1.25
    fired = []
    monkeypatch.setattr(bot_exits, "fire_target", lambda d, exit_id: fired.append(exit_id))
    later = OPEN_TIME + timedelta(minutes=30)
    deps.now = lambda: later
    deps.hub.set(_CALL_SHORT, D("3.70"), D("3.90"), later)  # sells for 3.70: value -3.70
    bot_exits.watch_pass(deps)
    bot_exits.watch_pass(deps)
    assert fired == []
    deps.hub.set(_CALL_SHORT, D("3.75"), D("3.95"), later)  # value -3.75, at the limit
    bot_exits.watch_pass(deps)
    assert fired == []  # one pass isn't enough
    bot_exits.watch_pass(deps)
    assert fired == [target.id]


def test_a_stale_leg_blocks_a_debit_groups_target(deps, factory, monkeypatch):
    shape_run(deps, factory, "long_call", target=D("50"))
    fired = []
    monkeypatch.setattr(bot_exits, "fire_target", lambda d, exit_id: fired.append(exit_id))
    later = OPEN_TIME + timedelta(minutes=30)
    deps.now = lambda: later
    deps.hub.set(_CALL_SHORT, D("5.00"), D("5.20"), later - timedelta(seconds=40))
    for _ in range(3):
        bot_exits.watch_pass(deps)
    assert fired == []


def test_a_groups_target_needs_every_leg_fresh(deps, factory, monkeypatch):
    # An iron butterfly as one group: one stale wing blocks however cheap the rest reads.
    run_id = shape_run(deps, factory, "iron_butterfly", target=D("50"))
    arm_the_target(deps, factory, run_id)  # a trade opened before decision 0048
    (target,) = exits(factory, "target")
    fired = []
    monkeypatch.setattr(bot_exits, "fire_target", lambda d, exit_id: fired.append(exit_id))
    later = OPEN_TIME + timedelta(minutes=30)
    deps.now = lambda: later
    legs = bot_exits.exit_legs(target)
    for leg in legs:
        deps.hub.set(leg.symbol, D("0.05"), D("0.10"), later)
    deps.hub.set(legs[-1].symbol, D("0.05"), D("0.10"), later - timedelta(seconds=40))
    bot_exits.watch_pass(deps)
    bot_exits.watch_pass(deps)
    assert fired == []
    deps.hub.set(legs[-1].symbol, D("0.05"), D("0.10"), later)
    bot_exits.watch_pass(deps)
    bot_exits.watch_pass(deps)
    assert fired == [target.id]


RESTING_SHAPES = ("iron_condor", "put_credit_spread", "short_put")


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_gets_one_stop_and_one_target_per_group(deps, factory, key):
    run_id = shape_run(deps, factory, key, stop=D("50"), target=D("50"))
    with factory() as session:
        groups = sorted({leg["group"] for leg in session.get(BotRun, run_id).legs})
    stops, targets = exits(factory, "stop"), exits(factory, "target")
    assert [s.group_position for s in stops] == groups
    assert [t.group_position for t in targets] == groups
    # Decision 0048: a group whose stop can't rest rests its target instead
    assert {t.state for t in targets} == {"armed" if key in RESTING_SHAPES else "working"}
    sent = closing_sent(deps)
    if key in RESTING_SHAPES:
        assert {s.state for s in stops} == {"working"}
        assert not any(s.watched for s in stops)
        assert sorted(b["legs"][0]["symbol"] for b in sent) == sorted(
            s.anchor_symbol for s in stops
        )
        assert all(b["order-type"] == "Stop" for b in sent)
    else:
        assert {s.state for s in stops} == {bot_exits.WATCHING}
        assert all(s.watched and s.anchor_symbol is None for s in stops)
        assert all(s.trigger < 0 for s in stops)
        assert [(b["time-in-force"], b["order-type"]) for b in sent] == [("GTC", "Limit")] * len(
            groups
        )  # one resting target a group, nothing else
    for row in [*stops, *targets]:
        assert row.legs and row.contracts == 1


def test_a_watched_groups_stop_is_watched_again_and_never_sent_on_a_rearm(deps, factory):
    shape_run(deps, factory, "long_call", stop=D("50"))
    (stop,) = exits(factory, "stop")
    with factory() as session:
        session.get(BotExit, stop.id).state = "cancelled"
        session.commit()
    with bot_exits.EXITS_LOCK:
        bot_exits.rearm(deps, stop.id)
    (stop,) = exits(factory, "stop")
    # Task 11: watching again isn't a re-arm; it never counts toward the 3 a day
    assert stop.state == bot_exits.WATCHING and stop.contracts == 1 and stop.rearms == 0
    assert closing_sent(deps) == []


def test_an_unpriced_watched_group_gets_no_watch_and_an_alert_naming_it(deps, factory, monkeypatch):
    monkeypatch.setattr(bot_entry, "place_protections", lambda d, r, **k: None)
    run_id = shape_run(deps, factory, "call_debit_spread", stop=D("50"), target=D("50"))
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.filled_legs = [{**f, "fill_price": None} if f["side"] == "sell" else f
                           for f in run.filled_legs]  # fmt: skip
        session.commit()
    seen = alerts_of(deps)
    bot_exits.place_protections(deps, run_id)
    assert [(e.kind, e.state) for e in exits(factory)] == [("stop", "failed"), ("target", "failed")]
    assert closing_sent(deps) == []
    (said,) = [e for e in seen if "no fill price" in e.detail]
    assert "Call spread has no stop" in said.detail


def test_app_watches_what_the_row_says_never_the_broker():
    """Decision 0060: a watched group or an app-held stop, on either broker; a Schwab stop
    opened before it is app-held (migration 0036)."""
    assert bot_exits.app_watches(BotExit(watched=True, app_held=False))
    assert bot_exits.app_watches(BotExit(watched=False, app_held=True))
    assert not bot_exits.app_watches(BotExit(watched=False, app_held=False))


# Decision 0060: a Schwab bot's exits follow TastyTrade's rules


@pytest.fixture
def schwab_deps(factory):
    tasty, schwab = FakeTasty(), FakeSchwab()
    d = bot_entry.BotDeps(
        factory, lambda: [tasty, schwab], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: OPEN_TIME, background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    d.tasty, d.schwab, d.seen = tasty, schwab, []
    d.notify = d.seen.append
    return d


def schwab_sent(deps, kind=None):
    return [c[-1] for c in deps.schwab.calls
            if c[0] == "place" and (kind is None or c[-1]["orderType"] == kind)]  # fmt: skip


def schwab_fire(deps, factory, **group_changes):
    bot_id = make_schwab(factory, live=True)
    if group_changes:
        bot = bot_store.load(factory, bot_id)
        groups = tuple(replace(g, **group_changes) for g in bot.groups)
        _, problems = bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)
        assert problems == {}, problems
        # a change that leaves a stop to the app switches the bot off: armed again knowingly
        bot_store.set_live(factory, bot_id, True, OPEN_TIME)
        bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    return bot_entry.fire(deps, bot_id, "09:47")


def test_a_schwab_fill_rests_one_single_leg_stop_per_side_at_schwab(schwab_deps, factory):
    deps = schwab_deps
    schwab_fire(deps, factory)
    stops = exits(factory, "stop")
    assert [(s.state, s.app_held, s.watched) for s in stops] == [("working", False, False)] * 2
    bodies = schwab_sent(deps, "STOP")
    assert len(bodies) == 2 and all(s.broker_order_id for s in stops)
    for body, stop in zip(bodies, stops, strict=True):
        (leg,) = body["orderLegCollection"]  # one leg: Schwab never gets a multi-leg stop
        assert leg["instruction"] == "BUY_TO_CLOSE" and leg["quantity"] == 1
        assert leg["instrument"]["symbol"] == stop.short_symbol
        assert body["duration"] == "GOOD_TILL_CANCEL" and "price" not in body
        assert body["stopPrice"] == "4.80" and stop.trigger == D("4.80")  # as TastyTrade's
    assert deps.tasty.calls == []  # nothing goes to the other broker
    assert not any("watched by the app" in str(e.log) for e in stops)


def test_a_schwab_stop_cancelled_outside_the_app_is_placed_again_at_schwab(schwab_deps, factory):
    deps = schwab_deps
    schwab_fire(deps, factory)
    stop = exits(factory, "stop")[0]
    deps.schwab.cancel_order("5WX01234", stop.broker_order_id)
    bot_exits.monitor_pass(deps)
    again = exits(factory, "stop")[0]
    assert again.state == "working" and again.rearms == 1
    assert again.broker_order_id != stop.broker_order_id
    assert len(schwab_sent(deps, "STOP")) == 3


def test_a_schwab_group_that_chose_its_target_rests_it_at_schwab(schwab_deps, factory):
    """As on TastyTrade (decision 0048): one GTC closing limit for the group rests, and the
    app holds its stop on the short."""
    deps = schwab_deps
    schwab_fire(deps, factory, target_pct=D("50"), broker_exit="target")
    stops, targets = exits(factory, "stop"), exits(factory, "target")
    assert [(s.state, s.app_held) for s in stops] == [("watching", True)] * 2
    assert all(s.log[-1]["result"] == bot_exits.watched_short(bot_exits._Run(
        1, "x", True, "schwab")) for s in stops)  # fmt: skip
    assert "rests at Schwab" in stops[0].log[-1]["result"]
    assert [t.state for t in targets] == ["working", "working"]
    assert all(t.log[-1]["result"].startswith("rests at Schwab") for t in targets)
    bodies = schwab_sent(deps, "NET_DEBIT")
    assert len(bodies) == 2 and schwab_sent(deps, "STOP") == []
    assert all(b["duration"] == "GOOD_TILL_CANCEL" and len(b["orderLegCollection"]) == 2
               and {leg["instruction"] for leg in b["orderLegCollection"]}
               == {"BUY_TO_CLOSE", "SELL_TO_CLOSE"} for b in bodies)  # fmt: skip


def test_a_schwab_stop_from_before_0060_stays_watched_and_is_never_sent(schwab_deps, factory):
    """A Schwab trade opened before decision 0060: its stop row is watching with nothing at
    Schwab, and migration 0036 marked it app-held. Re-armed or checked, it's watched again,
    sized to what's held: no stop is ever placed at Schwab for it."""
    deps = schwab_deps
    schwab_fire(deps, factory)
    for stop in exits(factory, "stop"):  # as such a trade's rows are, after migration 0036
        deps.schwab.cancel_order("5WX01234", stop.broker_order_id)
    with factory() as session:
        for row in session.scalars(select(BotExit).where(BotExit.kind == "stop")):
            row.state, row.broker_order_id, row.app_held = bot_exits.WATCHING, None, True
        session.commit()
    sent = len(schwab_sent(deps))
    bot_exits.monitor_pass(deps)
    stop = exits(factory, "stop")[0]
    with bot_exits.EXITS_LOCK:
        assert bot_exits.rearm(deps, stop.id)
    assert [(s.state, s.broker_order_id) for s in exits(factory, "stop")] == [
        (bot_exits.WATCHING, None)
    ] * 2
    assert len(schwab_sent(deps)) == sent  # nothing sent


def all_legs_at_ratio(deps, factory, key, ratio, stop=None, target=None):
    """A live run of `key` with every leg at `ratio` (each group's gcd is `ratio`)."""
    bot_id = make_shape(factory, key, live=True, stop=stop, target=target)
    bot = bot_store.load(factory, bot_id)
    legs = tuple(replace(leg, ratio=ratio) for leg in bot.legs)
    sized = replace(bot, legs=legs, max_per_trade=bot.contracts * ratio)
    assert bot_store.save(factory, sized, OPEN_TIME)[1] == {}
    return bot_entry.fire(deps, bot_id, "09:47")


def test_every_groups_stop_is_placed_when_the_last_group_has_a_gcd_of_2(deps, factory):
    # Every leg at ratio 2: each group is 2 units of a 1x1 spread; the broker holds 2 of
    # each leg, and each stop is 2 contracts on its short.
    all_legs_at_ratio(deps, factory, "iron_condor", 2, stop=D("50"), target=D("50"))
    stops = exits(factory, "stop")
    assert [(s.state, s.contracts) for s in stops] == [("working", 2), ("working", 2)]
    assert [t.state for t in exits(factory, "target")] == ["armed", "armed"]
    sent = closing_sent(deps)
    assert [(b["legs"][0]["symbol"], b["legs"][0]["quantity"]) for b in sent] == [
        (_CALL_SHORT, 2),
        (_PUT_SHORT, 2),
    ]


def test_a_missing_wing_fill_says_the_stop_stands_and_the_target_is_not_set(
    deps, factory, monkeypatch
):
    monkeypatch.setattr(bot_entry, "place_protections", lambda d, r, **k: None)
    run_id = shape_run(deps, factory, "put_credit_spread", stop=D("50"), target=D("50"))
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.filled_legs = [{**f, "fill_price": None} if f["side"] == "buy" else f
                           for f in run.filled_legs]  # fmt: skip
        session.commit()
    seen = alerts_of(deps)
    bot_exits.place_protections(deps, run_id)
    assert [(e.kind, e.state) for e in exits(factory)] == [
        ("stop", "working"),
        ("target", "failed"),
    ]
    (said,) = [e for e in seen if "no fill price" in e.detail]
    assert "Put spread has no profit target; its stop stands" in said.detail
    assert "has no stop" not in said.detail


def test_legs_not_shown_says_stops_are_watched_only_when_every_one_is(deps, factory, monkeypatch):
    deps.tasty.option_positions = lambda account: {}
    seen = alerts_of(deps)
    shape_run(deps, factory, "long_call", stop=D("50"), target=D("50"))
    assert [e.title for e in seen if "not set" in e.title or "not placed" in e.title] == [
        "long_call: profit targets not set"
    ]
    assert exits(factory, "stop")[0].state == bot_exits.WATCHING


def test_legs_not_shown_with_an_unpriced_watched_group_never_says_it_is_watched(
    deps, factory, monkeypatch
):
    # A reverse iron condor bought for a debit: each group's stop is watched by the app on
    # the debit fill. One is watching, the other has no fill on its sold leg, so no stop.
    from optionsjourney.trading.orders import Side

    monkeypatch.setattr(bot_entry, "place_protections", lambda d, r, **k: None)
    bot_id = make_shape(factory, "iron_condor", live=True, stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_id)
    flip = {Side.SELL: Side.BUY, Side.BUY: Side.SELL}
    legs = tuple(replace(leg, side=flip[leg.side]) for leg in bot.legs)
    groups = tuple(replace(g, stop_basis="group_entry") for g in bot.groups)
    reverse = replace(bot, legs=legs, groups=groups)
    assert bot_store.save(factory, reverse, OPEN_TIME)[1] == {}
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)  # live and armed to fire
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        run = session.get(BotRun, run_id)
        put_sell = next(g["symbol"] for g in run.legs if g["group"] == 1 and g["side"] == "sell")
        run.filled_legs = [{**f, "fill_price": None} if f["symbol"] == put_sell else f
                           for f in run.filled_legs]  # fmt: skip
        session.commit()
    deps.tasty.option_positions = lambda account: {}
    seen = alerts_of(deps)
    bot_exits.place_protections(deps, run_id)
    assert [s.state for s in exits(factory, "stop")] == [bot_exits.WATCHING, "failed"]
    assert not any("watched" in e.detail for e in seen)
    assert any(e.title.endswith("exits not placed") and "no stop" in e.detail for e in seen)


# Slice 13, Task 8: closing any group with one combo order and its chase.

DEBIT_SHAPES = ("call_debit_spread", "long_call", "long_call_butterfly")


def quote_legs(deps, legs, mids=None, at=None):
    """Fresh quotes on every leg, 0.05 either side of its mid (bot_fakes.mid_for unless
    `mids` gives one), at `at` (default: now)."""
    at = at or deps.now()
    for leg in legs:
        _, details = parse_occ(leg.symbol)
        mid = D((mids or {}).get(leg.symbol, mid_for(details.strike, details.put_call.lower())))
        deps.hub.set(leg.symbol, max(mid - D("0.05"), D(0)), mid + D("0.05"), at)


def group_closes(deps):
    """Every closing order sent or replaced that isn't a stop, without its id."""
    return [
        {k: v for k, v in c[-1].items() if k != "external-identifier"}
        for c in deps.tasty.calls
        if c[0] in ("place", "replace")
        and c[-1]["order-type"] != "Stop"
        and not c[-1]["legs"][0]["action"].endswith("Open")
    ]


def past_the_target(sent):
    """Decision 0048: a group whose stop can't rest sent its profit target at the fill, as
    one GTC closing limit: the closes after it."""
    assert (sent[0]["time-in-force"], sent[0]["order-type"]) == ("GTC", "Limit")
    return sent[1:]


def exit_row(factory, exit_id):
    with factory() as session:
        return session.get(BotExit, exit_id)


def hold(deps, legs, units):
    """The broker holds `units` of the group: each leg units x its ratio, signed."""
    for leg in legs:
        size = units * leg.ratio
        deps.tasty.held[plain(leg.symbol)] = size if leg.side is Side.BUY else -size


def test_a_group_close_sizes_every_leg_units_times_ratio_capped_at_whats_held(deps, factory):
    # A 1x2x1 call butterfly of 3 units; the broker shows only 2 of each unit
    # (close_jobs.select_close_legs): the close is 2 units, 2, 4 and 2 contracts.
    run_id = shape_run(deps, factory, "long_call_butterfly", stop=D("50"), contracts=3, ratio=2)
    (stop,) = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)
    assert [leg.ratio for leg in legs] == [2, 1, 1]
    hold(deps, legs, 2)
    quote_legs(deps, legs)
    close_id = bot_exits.fire_close(deps, run_id, 0, 3, 3)
    row = exit_row(factory, close_id)
    assert row.contracts == 2 and row.state == "filled"
    (sent,) = group_closes(deps)
    assert [(leg["symbol"], leg["quantity"], leg["action"]) for leg in sent["legs"]] == [
        (legs[0].symbol, 4, "Buy to Close"),
        (legs[1].symbol, 2, "Sell to Close"),
        (legs[2].symbol, 2, "Sell to Close"),
    ]


def test_a_leg_held_the_wrong_way_refuses_the_close_and_alerts(deps, factory):
    run_id = shape_run(deps, factory, "put_credit_spread", stop=D("50"), target=D("50"))
    deps.tasty.held[plain(_PUT_WING)] = -1  # short where the group bought it
    seen = alerts_of(deps)
    close_id = bot_exits.fire_close(deps, run_id, 0, 1, 1)
    row = exit_row(factory, close_id)
    assert row.state == "failed" and "held the wrong way" in row.log[-1]["event"]
    assert group_closes(deps) == []
    (said,) = [e for e in seen if e.title.endswith("not done")]
    assert _PUT_WING.strip() in said.detail and "wrong way" in said.detail
    assert "Check your broker" in said.detail


def test_some_legs_gone_and_others_held_never_reads_as_closed(deps, factory):
    # An iron butterfly as one group: its short call was bought back by hand, the rest
    # is still held. Nothing is sent, and the group isn't called closed.
    run_id = shape_run(deps, factory, "iron_butterfly", stop=D("50"), target=D("50"))
    (stop,) = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)
    gone = next(leg for leg in legs if leg.side is Side.SELL)
    deps.tasty.held.pop(plain(gone.symbol))
    quote_legs(deps, legs)
    seen = alerts_of(deps)
    close_id = bot_exits.fire_timed(deps, run_id, 0, "test")
    row = exit_row(factory, close_id)
    assert row.state == "failed" and past_the_target(group_closes(deps)) == []
    (said,) = [e for e in seen if e.title.endswith("timed close not done")]
    assert gone.symbol.strip() in said.detail
    assert all(leg.symbol.strip() in said.detail for leg in legs if leg != gone)
    assert "Iron butterfly" in said.detail


def test_a_receiving_close_starts_at_the_mid_and_asks_less_down_to_one_tick(deps, factory):
    run_id = shape_run(deps, factory, "long_call", stop=D("50"), target=D("50"))
    (stop,) = exits(factory, "stop")
    (leg,) = bot_exits.exit_legs(stop)
    deps.tasty.close = "work"
    quote_legs(deps, [leg], {leg.symbol: "0.25"})
    close_id = bot_exits.fire_timed(deps, run_id, 0, "test")
    *sent, back = past_the_target(group_closes(deps))
    assert back["time-in-force"] == "GTC"  # given up: its profit target rests again
    # From the mid 0.25, a nickel less each try, and never under one tick
    assert [(D(b["price"]), b["price-effect"]) for b in sent] == [
        (D(p), "Credit") for p in ("0.25", "0.20", "0.15", "0.10", "0.05")
    ]
    assert all(b["legs"][0]["action"] == "Sell to Close" for b in sent)
    assert exit_row(factory, close_id).state == "failed"  # gave up; never asked 0


def test_a_paying_close_starts_at_the_mid_and_pays_more(deps, factory):
    run_id = shape_run(deps, factory, "put_credit_spread", stop=D("50"), target=D("50"))
    deps.tasty.close = "work"
    quote_legs(deps, bot_exits.exit_legs(exits(factory, "stop")[0]),
               {_PUT_SHORT: "0.60", _PUT_WING: "0.05"})  # fmt: skip
    bot_exits.fire_timed(deps, run_id, 0, "test")
    sent = group_closes(deps)
    assert [(D(b["price"]), b["price-effect"]) for b in sent[:3]] == [
        (D("0.55"), "Debit"), (D("0.60"), "Debit"), (D("0.65"), "Debit"),
    ]  # fmt: skip


def test_a_target_close_on_a_debit_group_sells_at_its_limit_or_better(deps, factory):
    # A long call bought for 2.50, target 40%: close for a credit of 3.50.
    shape_run(deps, factory, "long_call", stop=D("50"), target=D("40"))
    (target,) = exits(factory, "target")
    assert target.limit == D("-3.50")
    (sent,) = group_closes(deps)  # decision 0048: it rests at TastyTrade from the fill
    assert (D(sent["price"]), sent["price-effect"]) == (D("3.50"), "Credit")
    assert sent["legs"][0]["action"] == "Sell to Close" and sent["order-type"] == "Limit"
    deps.tasty.fill(target.broker_order_id, D("3.50"))
    bot_exits.monitor_pass(deps)
    assert exit_row(factory, target.id).state == "filled"


@pytest.mark.parametrize("path", ["target", "timed", "you"])
@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_closes_by_target_timed_and_you(deps, factory, key, path):
    run_id = shape_run(deps, factory, key, stop=D("50"), target=D("50"))
    target = next(t for t in exits(factory, "target") if t.group_position == 0)
    legs = bot_exits.exit_legs(target)
    quote_legs(deps, legs)
    rests = key not in RESTING_SHAPES  # decision 0048: its target rests at TastyTrade
    if path == "target" and rests:
        deps.tasty.fill(target.broker_order_id)
        bot_exits.monitor_pass(deps)
        close_id = target.id
    elif path == "target":
        bot_exits.fire_target(deps, target.id)
        close_id = target.id
    elif path == "timed":
        close_id = bot_exits.fire_timed(deps, run_id, 0, "test")
    else:
        close_id = bot_exits.fire_close(deps, run_id, 0, 1, 1)
    assert exit_row(factory, close_id).state == "filled"
    if rests and path != "target":  # the resting target, then a cancel before the close
        assert ("cancel", target.broker_order_id) in deps.tasty.calls
        (sent,) = past_the_target(group_closes(deps))
    else:
        (sent,) = group_closes(deps)
    want = [(leg.symbol, leg.ratio, "Buy to Close" if leg.side is Side.SELL else "Sell to Close")
            for leg in legs]  # fmt: skip
    assert [(b["symbol"], b["quantity"], b["action"]) for b in sent["legs"]] == want
    assert sent["order-type"] == "Limit" and D(sent["price"]) > 0
    assert sent["price-effect"] == ("Credit" if key in DEBIT_SHAPES else "Debit")


# Task 8, fix round 1.


def test_a_receiving_close_below_the_start_offset_stays_a_credit_of_one_tick(deps, factory):
    # A long call worth 0.05 at the mid, the bot's exit chase starting 0.10 off the mid:
    # sold for one tick of credit, never paying to give it away, then it gives up.
    run_id = shape_run(deps, factory, "long_call", stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_of_run(factory, run_id))
    chase = replace(bot.exit_chase, start_offset=D("0.10"))
    assert bot_store.save(factory, replace(bot, exit_chase=chase), OPEN_TIME)[1] == {}
    (leg,) = bot_exits.exit_legs(exits(factory, "stop")[0])
    deps.tasty.close = "work"
    quote_legs(deps, [leg], {leg.symbol: "0.05"})
    close_id = bot_exits.fire_timed(deps, run_id, 0, "test")
    *sent, back = past_the_target(group_closes(deps))
    assert back["time-in-force"] == "GTC"  # given up: its profit target rests again
    assert [(D(b["price"]), b["price-effect"]) for b in sent] == [(D("0.05"), "Credit")]
    assert sent[0]["legs"][0]["action"] == "Sell to Close"
    assert exit_row(factory, close_id).state == "failed"


def test_a_wrong_way_refusal_puts_the_resting_stop_back(deps, factory):
    run_id = shape_run(deps, factory, "put_credit_spread", stop=D("50"), target=D("50"))
    deps.tasty.held[plain(_PUT_WING)] = -1  # the wing is short; the short is still short
    seen = alerts_of(deps)
    close_id = bot_exits.fire_timed(deps, run_id, 0, "test")
    assert exit_row(factory, close_id).state == "failed" and group_closes(deps) == []
    (stop,) = exits(factory, "stop")
    assert stop.state == "working"
    assert len([b for b in closing_sent(deps) if b["order-type"] == "Stop"]) == 2  # placed again
    assert exits(factory, "target")[0].state == "armed"  # the timed close's target is back
    (said,) = [e for e in seen if e.title.endswith("not done")]
    assert "wrong way" in said.detail and "stop is back" in said.detail


def test_a_short_held_the_wrong_way_gets_no_stop_put_back(deps, factory):
    run_id = shape_run(deps, factory, "put_credit_spread", stop=D("50"), target=D("50"))
    deps.tasty.held[plain(_PUT_SHORT)] = 1  # long where the stop would buy more
    seen = alerts_of(deps)
    bot_exits.fire_close(deps, run_id, 0, 1, 1)
    assert exits(factory, "stop")[0].state == "cancelled"
    assert len([b for b in closing_sent(deps) if b["order-type"] == "Stop"]) == 1
    (said,) = [e for e in seen if e.title.endswith("not done")]
    assert _PUT_SHORT.strip() in said.detail and "no stop" in said.detail


def test_a_some_legs_gone_refusal_watches_the_groups_stop_again(deps, factory):
    run_id = shape_run(deps, factory, "iron_butterfly", stop=D("50"), target=D("50"))
    legs = bot_exits.exit_legs(exits(factory, "stop")[0])
    deps.tasty.held.pop(plain(next(leg for leg in legs if leg.side is Side.SELL).symbol))
    quote_legs(deps, legs)
    seen = alerts_of(deps)
    bot_exits.fire_timed(deps, run_id, 0, "test")
    assert past_the_target(group_closes(deps)) == []
    assert past_the_target(closing_sent(deps)) == []
    assert exits(factory, "stop")[0].state == bot_exits.WATCHING
    # decision 0048: its resting target was cancelled for the close, and a group missing a
    # leg isn't held whole, so no target is placed again (_put_resting_back)
    assert exits(factory, "target")[0].state == "cancelled"
    (said,) = [e for e in seen if e.title.endswith("timed close not done")]
    assert "stop is back" in said.detail


def test_a_claimed_group_on_a_debit_bot_with_no_exits_is_watched(deps, factory):
    run_id = shape_run(deps, factory, "long_call")  # no stop, no target: no exit rows
    deps.hub.set(_CALL_SHORT, D("2.40"), D("2.60"), OPEN_TIME)  # the bought 5910 call
    close_id = bot_exits.fire_close(deps, run_id, 0, 1, 1)
    row = exit_row(factory, close_id)
    assert row.watched is True and row.anchor_symbol is None and row.short_symbol == ""


# Slice 13, Task 9: the natural price and the leg-out over sells, then buys.


def natural_shape_run(deps, factory, key, contracts=None):
    """A live run of a preset shape whose closes go to the natural price for a minute,
    then leg out; a 50% stop and target on every group."""
    bot_id = make_shape(factory, key, live=True, exhaust="natural_then_leg_out",
                        stop=D("50"), target=D("50"))  # fmt: skip
    bot = bot_store.load(factory, bot_id)
    chase = replace(bot.exit_chase, natural_minutes=1)
    assert bot_store.save(factory, replace(bot, exit_chase=chase), OPEN_TIME)[1] == {}
    if contracts is not None:
        with factory() as session:
            row = session.get(Bot, bot_id)
            row.contracts, row.max_per_trade, row.max_per_day = contracts, contracts * 2, 20
            session.commit()
    return bot_entry.fire(deps, bot_id, "09:47")


def placed(deps):
    """Every order sent or replaced, in order, without its id."""
    return [c[-1] for c in deps.tasty.calls if c[0] in ("place", "replace")]


def leg_orders(deps, symbol):
    """Every single-leg closing order on `symbol` (no stops)."""
    return [
        b for b in placed(deps)
        if len(b["legs"]) == 1 and b["order-type"] != "Stop"
        and b["legs"][0]["action"].endswith("Close")
        and plain(b["legs"][0]["symbol"]) == plain(symbol)
    ]  # fmt: skip


def test_leg_out_sells_no_buy_leg_until_every_sell_leg_is_bought_back(deps, factory):
    # An iron butterfly as one group: no combo fills, the call body is bought back at
    # market, but the put body's market buy-back stays working. Neither wing is sold.
    run_id = natural_shape_run(deps, factory, "iron_butterfly")
    arm_the_target(deps, factory, run_id)  # a trade opened before decision 0048
    (target,) = exits(factory, "target")
    legs = bot_exits.exit_legs(target)
    call_body, put_body, call_wing, put_wing = legs
    assert [leg.side for leg in legs] == [Side.SELL, Side.SELL, Side.BUY, Side.BUY]
    deps.tasty.close = "work"
    deps.tasty.market_works.add(plain(put_body.symbol))
    quote_legs(deps, legs)
    seen = alerts_of(deps)
    bot_exits.fire_target(deps, target.id)
    assert leg_orders(deps, call_body.symbol)[-1]["order-type"] == "Market"
    assert leg_orders(deps, put_body.symbol)[-1]["order-type"] == "Market"
    assert not leg_orders(deps, call_wing.symbol) and not leg_orders(deps, put_wing.symbol)
    row = exit_row(factory, target.id)
    assert row.state == "failed" and "unclear" in row.log[-1]["event"]
    (said,) = [e for e in seen if "unclear" in e.title]
    assert put_body.symbol.strip() in said.detail and "Check your broker" in said.detail


def test_leg_out_buys_sell_legs_back_one_at_a_time_then_sells_the_buys(deps, factory):
    run_id = natural_shape_run(deps, factory, "iron_butterfly")
    arm_the_target(deps, factory, run_id)  # a trade opened before decision 0048
    (target,) = exits(factory, "target")
    legs = bot_exits.exit_legs(target)
    deps.tasty.close = "work"
    quote_legs(deps, legs)
    seen = alerts_of(deps)
    bot_exits.fire_target(deps, target.id)
    combos = [b for b in placed(deps) if len(b["legs"]) > 1]
    assert combos and all(b["order-type"] == "Limit" for b in combos)  # never a combo at market
    singles = [b for b in placed(deps) if len(b["legs"]) == 1 and b["order-type"] != "Stop"]
    order = []
    for b in singles:  # each leg's run of orders, in the order the legs were worked
        key = (b["legs"][0]["symbol"], b["legs"][0]["action"])
        if not order or order[-1] != key:
            order.append(key)
    assert order == [
        (legs[0].symbol, "Buy to Close"),  # the call body
        (legs[1].symbol, "Buy to Close"),  # the put body
        (legs[2].symbol, "Sell to Close"),  # then the call wing
        (legs[3].symbol, "Sell to Close"),  # and the put wing
    ]
    assert exit_row(factory, target.id).state == "filled"
    assert {w.long_symbol for w in exits(factory, "wing")} == {legs[2].symbol, legs[3].symbol}
    assert any("profit target filled" in e.title and "legged out" in e.detail for e in seen)


def test_a_long_only_group_legs_out_by_selling_each_long(deps, factory):
    run_id = natural_shape_run(deps, factory, "long_straddle")
    arm_the_target(deps, factory, run_id)  # a trade opened before decision 0048
    (target,) = exits(factory, "target")
    legs = bot_exits.exit_legs(target)
    assert {leg.side for leg in legs} == {Side.BUY}
    deps.tasty.close = "work"
    quote_legs(deps, legs)
    bot_exits.fire_target(deps, target.id)
    combos = [b for b in placed(deps) if len(b["legs"]) > 1 and b["order-type"] != "Stop"]
    assert combos and all(b["order-type"] == "Limit" for b in combos)  # never at market
    for leg in legs:
        sold = leg_orders(deps, leg.symbol)
        assert sold and {b["legs"][0]["action"] for b in sold} == {"Sell to Close"}
    assert exit_row(factory, target.id).state == "filled"
    assert {w.long_symbol for w in exits(factory, "wing")} == {leg.symbol for leg in legs}
    assert not bot_exits.cancel_reaches(factory, target.id)  # its longs were being sold


def test_the_natural_stage_stops_when_legs_fill_unevenly(deps, factory):
    # A 1x2x1 call butterfly of 2 units. The natural stage's first order fills 2 of the
    # body's 4 and none of the wings: the stage stops and the leg-out closes the rest.
    run_id = natural_shape_run(deps, factory, "long_call_butterfly", contracts=2)
    arm_the_target(deps, factory, run_id)  # a trade opened before decision 0048
    (target,) = exits(factory, "target")
    legs = bot_exits.exit_legs(target)
    body, wing_a, wing_b = legs
    assert (body.side, body.ratio) == (Side.SELL, 2)
    deps.tasty.close = "work"
    quote_legs(deps, legs)
    real_book, combos = deps.tasty._book, {"n": 0}

    def book(oid, body_):
        real_book(oid, body_)
        closing = body_["legs"][0]["action"].endswith("Close")
        if not (closing and len(body_["legs"]) == 3):
            return
        natural = any(e.get("stage") == "natural" for e in exit_row(factory, target.id).log)
        if not natural:
            return
        combos["n"] += 1
        if combos["n"] != 1:
            return
        order = deps.tasty.orders[oid]
        new_legs = []
        for leg in order.legs:
            done = 2 if plain(leg.symbol) == plain(body.symbol) else 0
            new_legs.append(replace(leg, remaining=leg.quantity - done,
                                    fill_price=D("1") if done else None))  # fmt: skip
        deps.tasty.orders[oid] = replace(order, status=OrderStatus.PARTIAL, legs=tuple(new_legs))
        deps.tasty.held[plain(body.symbol)] += 2  # two of the four sold calls bought back

    deps.tasty._book = book
    bot_exits.fire_target(deps, target.id)
    assert combos["n"] == 1  # nothing more as a combo once the legs are uneven
    bought = leg_orders(deps, body.symbol)
    assert bought and {b["legs"][0]["quantity"] for b in bought} == {2}  # the 2 left
    for wing in (wing_a, wing_b):
        sold = leg_orders(deps, wing.symbol)
        assert sold and {b["legs"][0]["quantity"] for b in sold} == {2}
    calls = placed(deps)
    assert calls.index(bought[-1]) < min(
        calls.index(leg_orders(deps, w.symbol)[0]) for w in (wing_a, wing_b)
    )
    assert exit_row(factory, target.id).state == "filled"


# Task 9, fix round 1: a part-done leg-out never claims a stop it can't use.


def butterfly_legging_out(deps, factory):
    """An iron butterfly whose close goes to the natural price, then legs out; no
    combo fills. Its target is armed, as a trade opened before decision 0048 has it.
    Returns the target and its legs."""
    arm_the_target(deps, factory, natural_shape_run(deps, factory, "iron_butterfly"))
    (target,) = exits(factory, "target")
    legs = bot_exits.exit_legs(target)
    deps.tasty.close = "work"
    quote_legs(deps, legs)
    return target, legs


def test_a_crash_after_the_first_sell_leg_is_bought_back_is_never_filled(
    deps, factory, monkeypatch
):
    target, legs = butterfly_legging_out(deps, factory)
    call_body, put_body, call_wing, put_wing = legs
    real, calls = bot_exits._buy_back, []

    def crash_on_the_second(*args):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("cut off")
        return real(*args)

    monkeypatch.setattr(bot_exits, "_buy_back", crash_on_the_second)
    seen = alerts_of(deps)
    bot_exits.fire_target(deps, target.id)
    assert leg_orders(deps, call_body.symbol)  # the call body was bought back
    assert not leg_orders(deps, put_body.symbol)
    row = exit_row(factory, target.id)
    assert row.state == "failed" and "left to you" in row.log[-1]["event"]
    assert exits(factory, "stop")[0].state == "cancelled"  # never watched again
    (said,) = [e for e in seen if "error" in e.title]
    assert put_body.symbol.strip() in said.detail and "nothing protects" in said.detail
    assert call_body.symbol.strip() not in said.detail
    assert not bot_exits.cancel_reaches(factory, target.id)


def test_a_leg_out_stopped_after_a_sell_leg_puts_nothing_back(deps, factory, monkeypatch):
    # A timed close: the call body is bought back, then the put body has no fresh quote.
    run_id = natural_shape_run(deps, factory, "iron_butterfly")
    legs = bot_exits.exit_legs(exits(factory, "stop")[0])
    call_body, put_body, call_wing, put_wing = legs
    deps.tasty.close = "work"
    quote_legs(deps, legs)
    real = bot_exits._buy_back

    def then_the_quote_goes(*args):
        out = real(*args)
        deps.hub.quotes.pop(put_body.symbol, None)
        return out

    monkeypatch.setattr(bot_exits, "_buy_back", then_the_quote_goes)
    seen = alerts_of(deps)
    close_id = bot_exits.fire_timed(deps, run_id, 0, "test")
    assert leg_orders(deps, call_body.symbol) and not leg_orders(deps, put_body.symbol)
    assert not leg_orders(deps, call_wing.symbol) and not leg_orders(deps, put_wing.symbol)
    row = exit_row(factory, close_id)
    assert row.state == "failed" and "left to you" in row.log[-1]["event"]
    assert exits(factory, "stop")[0].state == "cancelled"  # no stop put back
    assert exits(factory, "target")[0].state == "cancelled"  # nor the target
    (said,) = [e for e in seen if "stopped part-way" in e.title]
    for leg in (put_body, call_wing, put_wing):
        assert leg.symbol.strip() in said.detail
    assert call_body.symbol.strip() not in said.detail
    assert "nothing protects" in said.detail and "Check your broker" in said.detail


def test_a_leg_out_stopped_before_any_leg_filled_puts_the_stop_back(deps, factory):
    target, legs = butterfly_legging_out(deps, factory)
    deps.hub.quotes.pop(legs[0].symbol)  # the call body: no natural, nothing to leg out on
    bot_exits.fire_target(deps, target.id)
    assert not [b for b in placed(deps) if len(b["legs"]) == 1 and b["order-type"] != "Stop"]
    assert exit_row(factory, target.id).state == "failed"
    assert exits(factory, "stop")[0].state == bot_exits.WATCHING  # as any give-up


def test_a_second_sell_leg_changed_outside_the_close_sends_no_leg(deps, factory, monkeypatch):
    target, legs = butterfly_legging_out(deps, factory)
    held = {plain(k): v for k, v in deps.tasty.held.items()}
    held.pop(plain(legs[1].symbol))  # the put body was bought back by hand
    monkeypatch.setattr(bot_exits, "_positions", lambda deps, run: dict(held))
    seen = alerts_of(deps)
    bot_exits.fire_target(deps, target.id)
    assert not [b for b in placed(deps) if len(b["legs"]) == 1 and b["order-type"] != "Stop"]
    assert exit_row(factory, target.id).state == "failed"
    assert exits(factory, "stop")[0].state == bot_exits.WATCHING  # nothing filled: put back
    assert any("changed" in e.detail for e in seen)


# Slice 13, Task 10: the rest of a group after its resting stop fills, and re-arming
# on the stop's leg. Only a credit bot rests a stop, so the ratio-2 anchor is a credit
# put ratio spread (correction 7), not the long butterfly.


def credit_ratio_run(deps, factory, buys=1, contracts=1, **group):
    """A live credit put ratio spread, one group: sell 2 of the 5690 put (2.50 each), buy
    1 of the 5615 put, and with `buys=2` one more of the 5590 put. A 50% stop rests on
    the sold put and a 50% target is armed; `group` sets the group's other fields."""
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_id)
    legs = tuple(replace(leg, ratio=2) if leg.position == 0 else leg for leg in bot.legs)
    if buys == 2:
        legs += (replace(legs[1], position=2, target=D("-100")),)
    groups = tuple(replace(g, **group) for g in bot.groups)
    shaped = replace(bot, legs=legs, groups=groups, max_per_trade=2, max_per_day=20)
    assert bot_store.save(factory, shaped, OPEN_TIME)[1] == {}
    if contracts != 1:
        with factory() as session:
            row = session.get(Bot, bot_id)
            row.contracts, row.max_per_trade = contracts, contracts * 2
            session.commit()
    return bot_entry.fire(deps, bot_id, "09:47")


def group_run(deps, factory, key, **group):
    """A live run of `key` ("put_credit_spread", or "ratio": the credit ratio spread with
    two bought puts), one group, its stop resting on the one sold put."""
    if key == "ratio":
        return credit_ratio_run(deps, factory, buys=2, **group)
    return styled_shape(deps, factory, key, **group)


def styled_shape(deps, factory, key, **group):
    bot_id = make_shape(factory, key, live=True, stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, **group) for g in bot.groups)
    assert bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)[1] == {}
    return bot_entry.fire(deps, bot_id, "09:47")


def bid_on(deps, symbols, bid="0.30"):
    for symbol in symbols:
        deps.hub.set(symbol, D(bid), D(bid) + D("0.10"), OPEN_TIME)


def fill_the_stop(deps, factory):
    """Fill the group's resting stop and run the monitor; returns the stop and its other
    legs, in leg order."""
    (stop,) = exits(factory, "stop")
    others = [leg for leg in bot_exits.exit_legs(stop) if leg.symbol != stop.anchor_symbol]
    deps.tasty.fill(stop.broker_order_id, stop.trigger)
    bot_exits.monitor_pass(deps)
    return stop, others


def sells_of(deps, symbol):
    """Every single-leg sell-to-close sent or replaced on `symbol`."""
    return [b for b in leg_orders(deps, symbol) if b["legs"][0]["action"] == "Sell to Close"]


def test_after_a_ratio_2_anchor_stop_each_other_leg_is_sold_on_its_own(deps, factory):
    credit_ratio_run(deps, factory, buys=2)
    (stop,) = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)
    assert [(leg.side, leg.ratio) for leg in legs] == [(Side.SELL, 2), (Side.BUY, 1), (Side.BUY, 1)]
    bid_on(deps, [legs[1].symbol, legs[2].symbol])
    seen = alerts_of(deps)
    stop, others = fill_the_stop(deps, factory)
    wings = exits(factory, "wing")
    assert [w.long_symbol for w in wings] == [legs[1].symbol, legs[2].symbol]
    for wing, leg in zip(wings, others, strict=True):
        assert wing.legs == [{"symbol": leg.symbol, "side": "buy", "ratio": 1}]
        assert wing.anchor_symbol == stop.anchor_symbol and wing.short_symbol == stop.anchor_symbol
        assert wing.contracts == 1 and wing.state in ("working", "filled")
        (sold,) = sells_of(deps, leg.symbol)
        assert sold["order-type"] == "Market" and sold["legs"][0]["quantity"] == 1
    assert not [b for b in placed(deps) if len(b["legs"]) > 1 and b["order-type"] == "Market"]
    (said,) = [e for e in seen if "stop filled" in e.title]
    opens = f"The stop on {stop.anchor_symbol.strip()} filled; the group's other legs: "
    assert said.detail.startswith(opens)
    assert legs[1].symbol.strip() in said.detail and legs[2].symbol.strip() in said.detail
    assert exits(factory, "target")[0].state == "cancelled"


@pytest.mark.parametrize("key", ["put_credit_spread", "ratio"])
@pytest.mark.parametrize("mode", ["close_market", "close_limit", "close_chase", "leave"])
def test_the_rest_of_the_group_after_a_stop_in_each_remainder_mode(deps, factory, key, mode):
    # A typed limit fits one leg left (the vertical); with two, each rests at its bid 0.30.
    typed = mode == "close_limit" and key == "put_credit_spread"
    extra = {"remainder_limit": D("0.33")} if typed else {}
    group_run(deps, factory, key, on_stop_remainder=mode, **extra)
    deps.tasty.close = "work"
    (stop,) = exits(factory, "stop")
    bid_on(deps, [leg.symbol for leg in bot_exits.exit_legs(stop) if leg.side is Side.BUY])
    seen = alerts_of(deps)
    stop, others = fill_the_stop(deps, factory)
    assert others and all(leg.side is Side.BUY for leg in others)
    wings = exits(factory, "wing")
    # One row per other leg, in leg order (a chase that ran out adds the rest's own row).
    firsts = [next(w for w in wings if w.long_symbol == leg.symbol) for leg in others]
    assert [w.long_symbol for w in firsts] == [leg.symbol for leg in others]
    for leg in others:
        rows = [w for w in wings if w.long_symbol == leg.symbol]
        assert all(w.legs == [{"symbol": leg.symbol, "side": "buy", "ratio": leg.ratio}]
                   for w in rows)  # fmt: skip
        assert all(w.anchor_symbol == stop.anchor_symbol for w in rows)
        sent = sells_of(deps, leg.symbol)
        if mode == "leave":
            assert sent == [] and [w.state for w in rows] == ["left"]
        elif mode == "close_market":
            assert [b["order-type"] for b in sent] == ["Market"]
        elif mode == "close_limit":
            assert [(b["order-type"], b["price"]) for b in sent] == [("Limit", "0.30")]
        else:  # chased from the leg's own mid, then the rest sold now
            assert sent[0]["order-type"] == "Limit" and sent[0]["price"] == "0.35"
            assert sent[-1]["order-type"] == "Market"
        assert all(b["legs"][0]["quantity"] == 1 for b in sent)
    assert exits(factory, "target")[0].state == "cancelled"
    (said,) = [e for e in seen if "stop filled" in e.title]
    assert stop.anchor_symbol.strip() in said.detail
    assert all(leg.symbol.strip() in said.detail for leg in others)
    if mode == "leave":
        assert "left open" in said.detail


def test_a_naked_short_stop_fill_closes_the_group_with_no_wing(deps, factory):
    from optionsjourney.bots import timing

    shape_run(deps, factory, "short_put", stop=D("50"), target=D("50"))
    seen = alerts_of(deps)
    stop, others = fill_the_stop(deps, factory)
    assert others == []
    assert exits(factory, "wing") == []
    assert [b for b in placed(deps) if b["legs"][0]["action"] == "Sell to Close"] == []
    rows = exits(factory)
    assert {(r.kind, r.state) for r in rows} == {("stop", "filled"), ("target", "cancelled")}
    assert timing.side_state((r.kind, r.state) for r in rows) == "closed"
    (said,) = [e for e in seen if "stop filled" in e.title]
    assert said.detail.startswith(f"The stop on {_PUT_SHORT.strip()} filled")
    assert "no other legs" in said.detail and "on  filled" not in said.detail
    assert not [e for e in seen if "wasn't handled" in e.title]


def test_the_remainder_never_sells_more_than_this_runs_units_of_a_leg(deps, factory):
    # 2 units of the ratio spread with two bought puts; another run holds 3 more of each
    # bought put. Only this run's 2 of each are sold.
    credit_ratio_run(deps, factory, buys=2, contracts=2)
    (stop,) = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)
    for leg in legs[1:]:
        deps.tasty.held[plain(leg.symbol)] += 3
    bid_on(deps, [leg.symbol for leg in legs[1:]])
    stop, others = fill_the_stop(deps, factory)
    assert stop.contracts == 2
    for leg in others:
        (sold,) = sells_of(deps, leg.symbol)
        assert sold["legs"][0]["quantity"] == 2
    assert [w.contracts for w in exits(factory, "wing")] == [2, 2]


def test_a_rearm_on_a_ratio_2_anchor_sends_units_times_two(deps, factory):
    # 3 units of a credit put ratio spread: the stop is 6 of the sold put. Cancelled at
    # the broker, it's re-placed at 6, still 3 units on its row.
    shape_run(deps, factory, "put_credit_spread", stop=D("50"), contracts=3, ratio=2)
    (stop,) = exits(factory, "stop")
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    bot_exits.monitor_pass(deps)
    (stop,) = exits(factory, "stop")
    assert (stop.state, stop.contracts, stop.rearms) == ("working", 3, 1)
    stops = [b for b in placed(deps) if b["order-type"] == "Stop"]
    assert [b["legs"][0]["quantity"] for b in stops] == [6, 6]
    assert {b["legs"][0]["symbol"] for b in stops} == {_PUT_SHORT}


def test_a_rearm_on_a_ratio_2_anchor_holding_less_sends_whole_units(deps, factory):
    # 3 units, but the broker now shows only 4 of the sold put (2 units): the stop goes
    # back for 2 units, 4 contracts, never more than is held.
    shape_run(deps, factory, "put_credit_spread", stop=D("50"), contracts=3, ratio=2)
    (stop,) = exits(factory, "stop")
    deps.tasty.held[plain(_PUT_SHORT)] = -4
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    bot_exits.monitor_pass(deps)
    (stop,) = exits(factory, "stop")
    assert (stop.state, stop.contracts) == ("working", 2)
    assert [b["legs"][0]["quantity"] for b in placed(deps) if b["order-type"] == "Stop"] == [6, 4]


def test_leg_out_wing_rows_record_their_own_leg(deps, factory):
    run_id = natural_shape_run(deps, factory, "long_straddle")
    arm_the_target(deps, factory, run_id)  # a trade opened before decision 0048
    (target,) = exits(factory, "target")
    legs = bot_exits.exit_legs(target)
    deps.tasty.close = "work"
    quote_legs(deps, legs)
    bot_exits.fire_target(deps, target.id)
    wings = exits(factory, "wing")
    assert {w.long_symbol for w in wings} == {leg.symbol for leg in legs}
    for wing in wings:
        assert wing.legs == [{"symbol": wing.long_symbol, "side": "buy", "ratio": 1}]
        assert wing.anchor_symbol is None


def test_a_chased_rest_of_the_group_chases_one_leg_at_a_time_and_alerts_once(deps, factory):
    seen = alerts_of(deps)
    later = deps.background = Later()
    credit_ratio_run(deps, factory, buys=2, on_stop_remainder="close_chase")
    (stop,) = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)
    bid_on(deps, [leg.symbol for leg in legs[1:]])
    fill_the_stop(deps, factory)
    wings = exits(factory, "wing")
    assert [(w.long_symbol, w.state) for w in wings] == [
        (legs[1].symbol, "chasing"), (legs[2].symbol, "chasing"),
    ]  # fmt: skip
    assert all(w.id in bot_exits.FIRING for w in wings) and lock_is_free()
    bot_exits.monitor_pass(deps)  # recovery leaves both alone while their job waits
    assert [w.state for w in exits(factory, "wing")] == ["chasing", "chasing"]
    later.run()
    order = []
    for b in placed(deps):
        sell = len(b["legs"]) == 1 and b["legs"][0]["action"] == "Sell to Close"
        if sell and (not order or order[-1] != b["legs"][0]["symbol"]):
            order.append(b["legs"][0]["symbol"])
    assert order == [legs[1].symbol, legs[2].symbol]  # one leg's chase, then the next
    assert [w.state for w in exits(factory, "wing")] == ["filled", "filled"]
    assert len([e for e in seen if "stop filled" in e.title]) == 1
    assert not any(w.id in bot_exits.FIRING for w in wings)


def test_a_watched_groups_stop_expires_with_its_legs(deps, factory):
    shape_run(deps, factory, "call_debit_spread", stop=D("50"), target=D("50"))
    assert exits(factory, "stop")[0].state == bot_exits.WATCHING
    seen = alerts_of(deps)
    deps.now = lambda: OPEN_TIME.replace(hour=20, minute=30)  # 16:30 New York, expiry day
    # decision 0048: its target rests at TastyTrade, which expires it with the options
    (target,) = exits(factory, "target")
    order = deps.tasty.orders[target.broker_order_id]
    deps.tasty.orders[target.broker_order_id] = replace(order, status=OrderStatus.EXPIRED)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "stop")[0].state == "expired"
    assert exits(factory, "target")[0].state == "expired"
    assert seen == []


def test_the_rest_of_a_group_that_breaks_names_every_other_leg(deps, factory, monkeypatch):
    credit_ratio_run(deps, factory, buys=2)
    (stop,) = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)

    def broken(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_exits, "_record_wings", broken)
    seen = alerts_of(deps)
    fill_the_stop(deps, factory)
    (hit,) = [e for e in seen if "a stop filled but" in e.title]
    assert "the group's other legs weren't handled" in hit.title
    assert legs[1].symbol.strip() in hit.detail and legs[2].symbol.strip() in hit.detail
    assert "Check your broker" in hit.detail


def test_a_stored_limit_on_a_group_with_two_legs_left_uses_each_legs_bid(deps, factory):
    # Saved before validation refused it: the typed 0.33 is ignored, each leg rests at
    # its own bid, and the log says why.
    run_id = credit_ratio_run(deps, factory, buys=2, on_stop_remainder="close_limit")
    with factory() as session:
        bot_id = session.get(BotRun, run_id).bot_id
        session.execute(
            update(BotLegGroup)
            .where(BotLegGroup.bot_id == bot_id)
            .values(on_stop_remainder_limit=D("0.33"))
        )
        session.commit()
    deps.tasty.close = "work"
    (stop,) = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)
    bid_on(deps, [legs[1].symbol], "0.40")
    bid_on(deps, [legs[2].symbol], "0.20")
    fill_the_stop(deps, factory)
    assert [(b["order-type"], b["price"]) for b in sells_of(deps, legs[1].symbol)] == [
        ("Limit", "0.40")
    ]
    assert [(b["order-type"], b["price"]) for b in sells_of(deps, legs[2].symbol)] == [
        ("Limit", "0.20")
    ]
    events = [e.get("event") for w in exits(factory, "wing") for e in w.log]
    assert any(e and "each leg's bid" in e for e in events)


def test_a_rearm_never_places_a_stop_over_an_anchor_held_long(deps, factory):
    shape_run(deps, factory, "put_credit_spread", stop=D("50"))
    (stop,) = exits(factory, "stop")
    deps.tasty.held[plain(_PUT_SHORT)] = 2  # the account is now long that put
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    bot_exits.monitor_pass(deps)
    (stop,) = exits(factory, "stop")
    assert stop.state == "expired"
    assert len([b for b in placed(deps) if b["order-type"] == "Stop"]) == 1  # the first only


def test_a_naked_short_whose_stop_handling_breaks_says_check_your_broker(
    deps, factory, monkeypatch
):
    shape_run(deps, factory, "short_put", stop=D("50"), target=D("50"))

    def broken(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_exits, "_cancel_side_target", broken)
    seen = alerts_of(deps)
    fill_the_stop(deps, factory)
    (hit,) = [e for e in seen if "a stop filled but" in e.title]
    assert hit.detail == "The stop filled; check your broker."


def test_the_remainder_caps_a_bought_leg_at_units_times_its_ratio(deps, factory):
    # Sell 1 put, buy 2 of a put further out (a credit backspread shape): 2 units hold 4
    # of the bought put; another run holds 3 more. 4 are sold, never 7.
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("50"))
    bot = bot_store.load(factory, bot_id)
    legs = tuple(replace(leg, ratio=2) if leg.position == 1 else leg for leg in bot.legs)
    assert bot_store.save(factory, replace(bot, legs=legs, max_per_trade=2), OPEN_TIME)[1] == {}
    with factory() as session:
        row = session.get(Bot, bot_id)
        row.contracts, row.max_per_trade, row.max_per_day = 2, 4, 20
        session.commit()
    bot_entry.fire(deps, bot_id, "09:47")
    (stop,) = exits(factory, "stop")
    wing = bot_exits.exit_legs(stop)[1]
    assert wing.ratio == 2 and stop.contracts == 2
    deps.tasty.held[plain(wing.symbol)] += 3
    bid_on(deps, [wing.symbol])
    fill_the_stop(deps, factory)
    (sold,) = sells_of(deps, wing.symbol)
    assert sold["legs"][0]["quantity"] == 4
    assert [w.contracts for w in exits(factory, "wing")] == [4]


# --- Task 12: moving a watched level ---------------------------------------------------


def long_call_at_3(deps, factory, contracts=None):
    """A watched long call bought at 3.00 (entry -3.00), its stop at 50% of the entry
    lost: P&L -1.50. The clock moves on half an hour, past the fill."""
    run_id = shape_run(deps, factory, "long_call", stop=D("50"), contracts=contracts)
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.filled_legs = [{**f, "fill_price": "3.00"} for f in run.filled_legs]
        run.fill_credit = D("-3.00")
        session.scalar(select(BotExit).where(BotExit.kind == "stop")).trigger = D("-1.50")
        session.commit()
    later = OPEN_TIME + timedelta(minutes=30)
    deps.now = lambda: later
    return run_id


def call_mid(deps, mid):
    deps.hub.set(_CALL_SHORT, D(mid) - D("0.05"), D(mid) + D("0.05"), deps.now())


def test_a_time_action_moves_a_watched_level_without_an_order(deps, factory):
    run_id = long_call_at_3(deps, factory)
    call_mid(deps, "3.50")  # P&L +0.50
    action = TimeActionDef("dte", 0, "14:00", stop_pct=D("25"))
    state, notes = bot_exits.apply_time_action(deps, run_id, 0, action, 1, False)
    (stop,) = exits(factory, "stop")
    assert (state, stop.state, stop.trigger) == ("filled", bot_exits.WATCHING, D("-0.75"))
    assert notes == ["stop -1.50 → -0.75 (watched by the app)"]
    calc = stops.calc_of(stop.log)
    assert (calc["basis"], calc["moved_by"]) == ("group_level", "time action 1")
    assert closing_sent(deps) == [] and not [c for c in deps.tasty.calls if c[0] == "cancel"]


def test_a_watched_level_is_never_moved_to_where_it_would_fire_at_once(deps, factory):
    run_id = long_call_at_3(deps, factory)
    action = TimeActionDef("dte", 0, "14:00", stop_pct=D("25"))
    # No fresh price on the call: nothing touched, tried again next pass.
    assert bot_exits.apply_time_action(deps, run_id, 0, action, 1, False) == (
        "waiting", ["no fresh prices for the group's legs yet"],
    )  # fmt: skip
    assert exits(factory, "action") == []
    call_mid(deps, "2.20")  # P&L -0.80: a level of -0.75 is already reached
    state, (note,) = bot_exits.apply_time_action(deps, run_id, 0, action, 1, False)
    (stop,) = exits(factory, "stop")
    assert (state, stop.state, stop.trigger) == ("failed", bot_exits.WATCHING, D("-1.50"))
    assert "at or above the group's P&L −0.80" in note and "would fire at once" in note
    assert closing_sent(deps) == []


def test_a_profit_action_moves_a_watched_level_by_the_entrys_size(deps, factory):
    run_id = long_call_at_3(deps, factory, contracts=3)
    call_mid(deps, "5.00")  # P&L +2.00
    seen = alerts_of(deps)
    # -25: keep a quarter of the entry's size, 3.00 x 25% = P&L +0.75 per contract
    then = {"pct": "-25", "by": "profit action 1"}
    assert bot_exits.fire_close(deps, run_id, 0, 1, 3, by="profit action 1", then_stop=then)
    (close,) = exits(factory, "close")
    assert close.state == "reduced"
    (stop,) = exits(factory, "stop")
    assert (stop.state, stop.contracts, stop.trigger) == (bot_exits.WATCHING, 2, D("0.75"))
    assert stops.calc_of(stop.log)["moved_by"] == "profit action 1"
    assert not [b for b in closing_sent(deps) if b["order-type"] == "Stop"]
    assert any("The stop moved -1.50 → 0.75 (profit action 1)" in e.detail for e in seen)


def test_a_profit_action_never_loosens_a_watched_level(deps, factory):
    run_id = long_call_at_3(deps, factory, contracts=3)
    call_mid(deps, "5.00")
    seen = alerts_of(deps)
    then = {"pct": "75", "by": "profit action 1"}  # P&L -2.25: looser than -1.50
    bot_exits.fire_close(deps, run_id, 0, 1, 3, by="profit action 1", then_stop=then)
    (stop,) = exits(factory, "stop")
    assert (stop.state, stop.contracts, stop.trigger) == (bot_exits.WATCHING, 2, D("-1.50"))
    assert any("The stop wasn't moved: the stop is already at -1.50" in e.detail for e in seen)


def test_level_problem_on_a_watched_level():
    entry = D("-3.00")
    calc = stops.watched_level(entry, D("25"), "test")  # P&L -0.75
    assert bot_exits.level_problem(calc, D("-1.50"), pnl=D("0.50")) is None
    assert "as tight or tighter" in bot_exits.level_problem(calc, D("-0.75"), pnl=D("0.50"))
    assert "as tight or tighter" in bot_exits.level_problem(calc, D("-0.50"), pnl=D("0.50"))
    assert "no fresh prices" in bot_exits.level_problem(calc, D("-1.50"))
    assert "would fire at once" in bot_exits.level_problem(calc, D("-1.50"), pnl=D("-0.75"))


def test_a_moved_stop_on_a_ratio_2_anchor_is_priced_per_contract():
    # A credit put ratio spread: sell 2 at 2.50, buy 1 at 0.05; a whole trade's credit of
    # 4.95 a unit. 105% of it is 5.1975 for the 2 sold: 2.55 a contract, rounded down to
    # the 0.05 tick (not 5.15 on one contract: a stop twice as far away).
    sold = bot_exits.exit_math.GroupLeg(_PUT_SHORT, Side.SELL, 2)
    wing = bot_exits.exit_math.GroupLeg(_PUT_WING, Side.BUY, 1)
    fills = {_PUT_SHORT: {"fill_price": "2.50"}, _PUT_WING: {"fill_price": "0.05"}}
    g = bot_exits._Group((sold, wing), sold, 1, fills, D("4.95"))
    group = GroupDef(0, "", stop_pct=D("50"), stop_unit="pct", stop_basis="entry_premium")
    (calc, _), problem = bot_exits._moved_calc(group, D("105"), g, D("4.95"), "test")
    assert problem is None and calc.trigger == D("2.55")


def test_a_profit_actions_stop_on_a_ratio_2_anchor_is_priced_per_contract(deps, factory):
    # A credit put ratio spread, 3 units: sell 2 of the 5690 put at 2.50, buy 1 of the
    # 5615 put at 0.05, 4.95 a unit. Break-even is 4.95 for the 2 sold: 2.475 a
    # contract, 2.45 on the 0.05 tick (4.90 on one contract would lose twice as much).
    run_id = shape_run(deps, factory, "put_credit_spread", stop=D("50"), contracts=3, ratio=2)
    (stop,) = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)
    quote_legs(deps, legs, mids={legs[0].symbol: "1.00", legs[1].symbol: "0.05"})
    then = {"pct": "0", "by": "profit action 1"}
    assert bot_exits.fire_close(deps, run_id, 0, 1, 3, by="profit action 1", then_stop=then)
    (stop,) = exits(factory, "stop")
    assert (stop.state, stop.contracts, stop.trigger) == ("working", 2, D("2.45"))
    sent = [b for b in closing_sent(deps) if b["order-type"] == "Stop"][-1]
    assert D(str(sent["stop-trigger"])) == D("2.45") and sent["legs"][0]["quantity"] == 4


# Slice 13, Task 16: the shape matrix, completed.

WATCHED_SHAPES = ("call_debit_spread", "long_call", "iron_butterfly", "long_call_butterfly")


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_recovers_a_close_cut_off_by_a_restart(deps, factory, key):
    run_id = shape_run(deps, factory, key, stop=D("50"), target=D("50"))
    if key not in RESTING_SHAPES:  # an armed target's close: a trade opened before 0048
        arm_the_target(deps, factory, run_id)
    target = next(t for t in exits(factory, "target") if t.group_position == 0)
    quote_legs(deps, bot_exits.exit_legs(target))
    deps.tasty.close = "work"
    placed_before = len(stop_places(deps))

    def die(seconds):
        raise Crash

    deps.sleep = die
    with pytest.raises(Crash):
        bot_exits.fire_target(deps, target.id)
    deps.sleep = lambda s: None
    row = exit_row(factory, target.id)
    close_id = row.broker_order_id
    assert close_id and deps.tasty.orders[close_id].may_be_working
    assert target.id not in bot_exits.FIRING
    seen = alerts_of(deps)
    bot_exits.recover_targets(deps)
    # the close's order is cancelled before anything is put back
    assert ("cancel", close_id) in deps.tasty.calls
    cancel_at = deps.tasty.calls.index(("cancel", close_id))
    assert exit_row(factory, target.id).state == "failed"
    stop = next(s for s in exits(factory, "stop") if s.group_position == 0)
    assert any("profit target error" in t for t in titles(seen))
    if key in WATCHED_SHAPES:
        # watched again; no order of any kind goes on top of it
        assert stop.state == bot_exits.WATCHING and stop.watched
        assert len(stop_places(deps)) == placed_before
    else:
        # a resting stop goes back on its anchor, after the cancel
        assert stop.state == "working" and not stop.watched
        again = stop_places(deps)[placed_before:]
        assert len(again) == 1 and again[0][2]["legs"][0]["symbol"] == stop.anchor_symbol
        assert again[0][2]["legs"][0]["quantity"] == stop.contracts  # one unit, ratio 1
        assert deps.tasty.calls.index(again[0]) > cancel_at


def test_a_run_with_mixed_gcds_stops_every_group_at_its_own_size(deps, factory):
    # An iron condor whose calls are a 1x1 spread (gcd 1) and whose puts are a 2x2 spread
    # (gcd 2): the first group holds 1 unit, the last 2. Each stop is sized from its own
    # group, not from the last group's units (Task 7).
    bot_id = make_shape(factory, "iron_condor", live=True, stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_id)
    legs = tuple(replace(leg, ratio=2) if leg.position in (2, 3) else leg for leg in bot.legs)
    sized = replace(bot, legs=legs, max_per_trade=bot.contracts * 2)
    assert bot_store.save(factory, sized, OPEN_TIME)[1] == {}
    bot_entry.fire(deps, bot_id, "09:47")
    stops, targets = exits(factory, "stop"), exits(factory, "target")
    assert [(s.group_position, s.state, s.contracts) for s in stops] == [
        (0, "working", 1),
        (1, "working", 2),
    ]
    assert [(t.group_position, t.state, t.contracts) for t in targets] == [
        (0, "armed", 1),
        (1, "armed", 2),
    ]
    sent = {b["legs"][0]["symbol"]: b["legs"][0]["quantity"] for b in closing_sent(deps)}
    assert sent == {_CALL_SHORT: 1, _PUT_SHORT: 2}


@pytest.mark.parametrize("lost", [1, 2])
def test_a_crash_before_a_bought_back_marker_is_proven_from_the_filled_order(
    deps, factory, monkeypatch, lost
):
    # Task 9: the buy-back of a sell leg filled, but the app died before it wrote that
    # leg's "bought back" marker. Recovery proves the leg from the close's last order
    # (filled at the broker); the close is never called filled while buy legs are unsold.
    target, legs = butterfly_legging_out(deps, factory)
    call_body, put_body, call_wing, put_wing = legs
    real, markers = bot_exits._stage, []

    def die_on_the_marker(deps_, close, stage, **entry):
        if "bought_back" in entry:
            markers.append(entry["bought_back"])
            if len(markers) == lost:
                raise Crash
        return real(deps_, close, stage, **entry)

    monkeypatch.setattr(bot_exits, "_stage", die_on_the_marker)
    with pytest.raises(Crash):
        bot_exits.fire_target(deps, target.id)
    assert len(markers) == lost
    row = exit_row(factory, target.id)
    assert row.state == "firing" and row.broker_order_id
    logged = [e["bought_back"] for e in row.log if "bought_back" in e]
    assert len(logged) == lost - 1  # the last marker never got written
    seen = alerts_of(deps)
    bot_exits.recover_targets(deps)
    row = exit_row(factory, target.id)
    assert row.state == "failed" and "left to you" in row.log[-1]["event"]
    assert exits(factory, "stop")[0].state == "cancelled"  # nothing put back
    assert exits(factory, "target")[0].state == "failed"
    (said,) = [e for e in seen if "error" in e.title]
    assert "nothing protects" in said.detail
    if lost == 1:  # the call body is proven by the filled order; the put body is untouched
        assert put_body.symbol.strip() in said.detail
        assert call_body.symbol.strip() not in said.detail
    else:  # both bodies proven (one logged, one by the order): only the wings are open
        assert put_body.symbol.strip() not in said.detail
        assert call_body.symbol.strip() not in said.detail
    assert call_wing.symbol.strip() in said.detail and put_wing.symbol.strip() in said.detail
    assert not [e for e in seen if "filled" in e.title]


def test_a_close_from_before_this_slice_is_never_rearmed_over_a_part_bought_back_short(
    deps, factory
):
    # A close row written before slice 13 has no stage markers in its log. Its last order
    # is a buy-back of the short that filled 1 of 2 before the upgrade cut it off.
    # Recovery cancels the order, reads what filled and sizes what it puts back to what
    # is still held; it never rests a stop for the full 2 over a short of 1.
    run_id = shape_run(deps, factory, "put_credit_spread", stop=D("50"), target=D("50"),
                       contracts=2)  # fmt: skip
    quote_legs(deps, bot_exits.exit_legs(exits(factory, "target")[0]))
    deps.tasty.close = "work"

    def die(seconds):
        raise Crash

    deps.sleep = die
    with pytest.raises(Crash):
        bot_exits.fire_close(deps, run_id, 0, 2, 2)
    deps.sleep = lambda s: None
    (close,) = exits(factory, "close")
    assert close.state == "firing" and close.broker_order_id
    deps.tasty.cancel_order("5WX01234", close.broker_order_id)
    deps.tasty.market_works.add(plain(_PUT_SHORT))
    buy_back = {"order-type": "Market", "time-in-force": "Day", "legs": [
        {"symbol": _PUT_SHORT, "action": "Buy to Close", "quantity": 2}]}  # fmt: skip
    old_id = deps.tasty.place_order("5WX01234", buy_back)
    part = deps.tasty.orders[old_id]
    legs = tuple(replace(leg, remaining=1, fill_price=D("0.50")) for leg in part.legs)
    deps.tasty.orders[old_id] = replace(part, status=OrderStatus.PARTIAL, legs=legs)
    deps.tasty.held[plain(_PUT_SHORT)] = -1  # one of the two shorts bought back
    with factory() as session:
        row = session.get(BotExit, close.id)
        row.broker_order_id = old_id
        row.log = [e for e in row.log if "stage" not in e]  # as an older release wrote it
        session.commit()
    placed_before = len(stop_places(deps))
    seen = alerts_of(deps)
    bot_exits.recover_targets(deps)
    assert ("cancel", old_id) in deps.tasty.calls  # the order is dealt with first
    row = exit_row(factory, close.id)
    assert row.state == "reduced" and row.log[-1]["closed"] == 1
    again = stop_places(deps)[placed_before:]
    assert [(b[2]["legs"][0]["symbol"], b[2]["legs"][0]["quantity"]) for b in again] == [
        (_PUT_SHORT, 1)
    ]  # for the one short still held, not the two it was
    (stop,) = exits(factory, "stop")
    assert (stop.state, stop.contracts) == ("working", 1)
    assert any("close error" in e.title for e in seen)


def test_a_paused_leave_says_the_groups_other_legs_one_or_many(monkeypatch):
    """Task 17 wording: never "the wing" when a group has several other legs."""
    from types import SimpleNamespace

    said = []
    monkeypatch.setattr(bot_exits, "_alert", lambda deps, title, detail, bot_id, **kw:
                        said.append(detail))  # fmt: skip
    bot = SimpleNamespace(name="Fly", id=1)
    working = SimpleNamespace(state="working", note="")
    failed = SimpleNamespace(state="failed", note="")
    bot_exits._stop_alert(None, bot, "A", [("B", [working]), ("C", [working])], True)
    bot_exits._stop_alert(None, bot, "A", [("B", [working])], True)
    bot_exits._stop_alert(None, bot, "A", [("B", [failed]), ("C", [failed])], True)
    assert '"Leave" is paused, so the other legs are being sold.' in said[0]
    assert '"Leave" is paused, so the other leg is being sold.' in said[1]
    assert "so the app tried to sell them" in said[2]
    assert not any("wing" in detail for detail in said)


def test_legs_not_shown_on_a_mixed_bot_names_the_resting_groups_and_the_watched_ones(
    deps, factory, monkeypatch
):
    """Final review M4: a TastyTrade bot with a resting group (the put spread) and a
    watched one (the call group, both calls sold) whose legs don't show: the put side's
    stop isn't placed, but the call group's stop is watching, so the alert doesn't say
    "no stop was placed"."""
    from optionsjourney.trading.orders import Side

    monkeypatch.setattr(bot_entry, "place_protections", lambda d, r, **k: None)
    bot_id = make_shape(factory, "iron_condor", live=True, stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_id)
    legs = tuple(replace(leg, side=Side.SELL) if leg.position == 1 else leg for leg in bot.legs)
    groups = tuple(replace(g, stop_basis="group_entry") if g.position == 0 else g
                   for g in bot.groups)  # fmt: skip
    assert bot_store.save(factory, replace(bot, legs=legs, groups=groups), OPEN_TIME)[1] == {}
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)  # app-watched now: switched off
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    deps.tasty.option_positions = lambda account: {}
    seen = alerts_of(deps)
    bot_exits.place_protections(deps, run_id)
    stops = exits(factory, "stop")
    assert [(s.group_position, s.watched, s.state) for s in stops] == [
        (0, True, bot_exits.WATCHING), (1, False, "failed")]  # fmt: skip
    (said,) = [e for e in seen if e.title.endswith("exits not placed")]
    assert "no stop was placed" not in said.detail
    assert "Put side" in said.detail and "watched by the app" in said.detail


# --- Bots slice 20: the run page's timeline, read from what the real writers log --------


def _steps(row):
    from optionsjourney.bots import timeline

    return [s.event for _, s in timeline.exit_steps(row)]


def test_the_timeline_reads_a_wing_chase_with_no_stage(deps, factory):
    deps.tasty.close = "work"
    stop_out(deps, factory, bid="0.30", on_stop_remainder="close_chase")
    chased, sold = exits(factory, "wing")
    assert _steps(chased) == [
        "Wing: chase it", "Limit: Placed", *["Limit: Replaced"] * 5, "Limit: Cancelled",
        "Limit chase ended: missed", "The chase sold 0 of 1; selling the rest now", "Cancelled",
    ]  # fmt: skip
    assert _steps(sold) == ["Placed at market (bid 0.30)"]


def test_the_timeline_reads_staged_chases(deps, factory):
    deps.tasty.close = "work"
    natural_run(deps, factory)
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0.10", OPEN_TIME)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    target = exits(factory, "target")[0]
    assert _steps(target) == [
        "Worked out", "Target reached", "Limit: Placed", *["Limit: Replaced"] * 5,
        "Limit: Cancelled", "Limit chase ended: missed", "Natural started", "Natural: Placed",
        "Natural: Cancelled", "Natural chase ended: missed", "Leg out started",
        "Leg out: Placed", *["Leg out: Replaced"] * 5, "Leg out: Cancelled",
        "Leg out chase ended: missed", "Leg out: Placed at market", "Leg out: short bought back",
        "Leg out: selling the rest", "Leg out: wing handed to its own close",
        "Closed (legged out: the short was bought back, then its wing SPXW  261013C05985000 sold)",
        "Filled",
    ]  # fmt: skip


def test_a_dollar_stop_on_a_watched_group_is_a_level_at_minus_the_dollars():
    """Bots slice 21: $ a contract on a group the app watches is its P&L level at -$
    (stops.watched_calc), nothing at the broker."""
    from optionsjourney.bots.exit_math import GroupLeg
    from optionsjourney.trading.orders import Side

    g = bot_exits._Group((GroupLeg("A", Side.BUY, 1),), None, 1, {}, D("-2.00"))
    (calc, entry), problem = bot_exits._moved_calc(None, None, g, None, "you", usd=D("1.25"))
    assert problem is None and calc.trigger == D("-1.25")
    assert entry["stop_calc"]["moved_by"] == "you"


# --- Bots slice 21: closing another group after a stop (remainder._handle_paired_close) ----


def paired_run(deps, factory, **changes):
    """make()'s iron condor with the call side (group 0) closing the put side (group 1)
    after its stop; `changes` on the put side's group."""
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = (replace(bot.groups[0], close_on_stop=1), replace(bot.groups[1], **changes))
    assert bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)[1] == {}
    return bot_entry.fire(deps, bot_id, "09:47")


def fresh_quotes(deps, factory, run_id, when=OPEN_TIME):
    with factory() as session:
        legs = list(session.get(BotRun, run_id).legs)
    for leg in legs:
        mid = D("0.60") if leg["side"] == "sell" else D("0.05")
        deps.hub.set(leg["symbol"], max(mid - D("0.05"), D(0)), mid + D("0.05"), when)


def fill_call_stop(deps, factory):
    stop = [e for e in exits(factory, "stop") if e.group_position == 0][0]
    deps.tasty.fill(stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    return stop


def test_a_stop_fill_closes_the_paired_group_by_its_own_rule(deps, factory):
    """Pairing sets the timing, the target's own rule the manner: the put side (two legs,
    sold now) is closed by slice 6's close, claimed like any other: its stop and target
    pulled first, sized to what's held, chased from the mid."""
    seen = alerts_of(deps)
    run_id = paired_run(deps, factory)
    fresh_quotes(deps, factory, run_id)
    put_stop = [e for e in exits(factory, "stop") if e.group_position == 1][0]
    fill_call_stop(deps, factory)
    [row] = exits(factory, "paired")
    assert (row.group_position, row.state) == (1, "filled")
    [close] = exits(factory, "close")
    assert close.group_position == 1 and close.state == "filled"
    assert close.log[0]["by"] == "Call side's stop"
    assert ("cancel", put_stop.broker_order_id) in [c[:2] for c in deps.tasty.calls]
    assert [e for e in exits(factory, "stop") if e.group_position == 1][0].state != "working"
    assert any("closing Put side too" in e.detail for e in seen)


def test_a_paired_one_leg_group_set_to_sell_now_goes_at_market(deps, factory):
    """Tracker2's MEIC pairing: the short and its protective long in separate groups so the
    short's stop rests; the long is sold the moment the stop fills."""
    from optionsjourney.bots.definition import LegDef

    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    legs = (LegDef(0, Side.SELL, "put", target=D("2.90"), group=0),
            LegDef(1, Side.BUY, "put", pick="offset", target=D("-75"), link=0,
                   group=1))  # fmt: skip
    groups = (GroupDef(0, "Short put", stop_pct=D("95"), close_on_stop=1),
              GroupDef(1, "Long put"))  # fmt: skip
    assert bot_store.save(factory, replace(bot, legs=legs, groups=groups), OPEN_TIME)[1] == {}
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    fresh_quotes(deps, factory, run_id)
    fill_call_stop(deps, factory)
    [row] = exits(factory, "paired")
    assert row.state == "filled"
    sells = [c[2] for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Market"]
    assert len(sells) == 1 and sells[0]["legs"][0]["action"] == "Sell to Close"


def test_a_paired_group_set_to_leave_is_left(deps, factory):
    run_id = paired_run(deps, factory, on_stop_remainder="leave")
    fresh_quotes(deps, factory, run_id)
    fill_call_stop(deps, factory)
    [row] = exits(factory, "paired")
    assert row.state == "left" and exits(factory, "close") == []
    assert [e for e in exits(factory, "stop") if e.group_position == 1][0].state == "working"


def test_a_busy_or_held_back_paired_group_is_left_and_alerted(deps, factory):
    """Question 8: nothing is sent, the row says why, one alert; never tried again."""
    seen = alerts_of(deps)
    paired_run(deps, factory, target_pct=D("50"))
    with factory() as session:
        target = session.scalar(select(BotExit).where(
            BotExit.kind == "target", BotExit.group_position == 1))  # fmt: skip
        target.state = "firing"  # a target close is running on the put side
        session.commit()
    bot_exits.FIRING.add(target.id)
    try:
        fill_call_stop(deps, factory)
        bot_exits.monitor_pass(deps)
    finally:
        bot_exits.FIRING.discard(target.id)
    [row] = exits(factory, "paired")
    assert row.state == "failed" and "already running" in row.log[-1]["event"]
    assert exits(factory, "close") == []
    assert any("Put side wasn't closed" in e.detail for e in seen)


def test_a_paired_close_out_of_the_session_sends_nothing(deps, factory):
    seen = alerts_of(deps)
    run_id = paired_run(deps, factory)
    after = OPEN_TIME.replace(hour=21)  # 17:47 New York
    deps.now = lambda: after
    fill_call_stop(deps, factory)
    [row] = exits(factory, "paired")
    assert row.state == "failed" and "session" in row.log[-1]["event"]
    assert exits(factory, "close") == [] and run_id
    assert any("Put side wasn't closed" in e.detail for e in seen)


def _pending_pair(factory, claiming=False):
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    with factory() as session:
        stop = session.scalar(select(BotExit).where(BotExit.kind == "stop",
                                                    BotExit.group_position == 0))  # fmt: skip
        stop.state = "filled"
        log = [{"at": now.isoformat(), "event": "after Call side's stop filled", "from": 0,
                "stop": stop.id, "by": "Call side's stop"}]  # fmt: skip
        if claiming:
            log.append({"at": now.isoformat(), "event": bot_exits.CLAIMING})
        session.add(
            BotExit(
                run_id=stop.run_id,
                group_position=1,
                kind="paired",
                state="pending",
                short_symbol="",
                long_symbol="",
                contracts=1,
                rearms=0,
                log=log,
                created_at=OPEN_TIME,
                updated_at=OPEN_TIME,
            )
        )
        session.commit()


def test_a_paired_close_cut_off_before_its_claim_is_claimed_once(deps, factory):
    run_id = paired_run(deps, factory)
    fresh_quotes(deps, factory, run_id)
    _pending_pair(factory)
    bot_exits.monitor_pass(deps)
    bot_exits.monitor_pass(deps)
    assert len(exits(factory, "close")) == 1
    assert exits(factory, "paired")[0].state == "filled"


def test_a_paired_close_cut_off_after_its_claim_is_finished_not_sent_again(deps, factory):
    """In the real order: the paired row is pending, its claim is logged, the close is
    claimed and started, then the app dies before the row is finished."""
    run_id = paired_run(deps, factory)
    fresh_quotes(deps, factory, run_id)
    _pending_pair(factory, claiming=True)
    deps.now = lambda: OPEN_TIME + timedelta(seconds=1)
    assert bot_exits.fire_close(deps, run_id, 1, 1, 1, by="Call side's stop") is not None
    before = len(deps.tasty.calls)
    bot_exits.monitor_pass(deps)
    [row] = exits(factory, "paired")
    assert row.state == "filled" and len(exits(factory, "close")) == 1
    assert [c for c in deps.tasty.calls[before:] if c[0] == "place"] == []


def test_a_held_back_paired_group_is_left_and_alerted(deps, factory):
    """A close on the put side that ended unclear holds it back: the paired close sends
    nothing for it."""
    seen = alerts_of(deps)
    run_id = paired_run(deps, factory)
    with factory() as session:
        stop = session.scalar(select(BotExit).where(BotExit.kind == "stop",
                                                    BotExit.group_position == 1))  # fmt: skip
        session.add(
            BotExit(
                run_id=run_id,
                group_position=1,
                kind="close",
                state="failed",
                short_symbol=stop.short_symbol,
                long_symbol=stop.long_symbol,
                contracts=1,
                rearms=0,
                log=[{"event": "unclear: check"}],
                created_at=OPEN_TIME,
                updated_at=OPEN_TIME + timedelta(seconds=1),
            )
        )
        session.commit()
    before = len(deps.tasty.calls)
    fill_call_stop(deps, factory)
    [row] = exits(factory, "paired")
    assert row.state == "failed" and "still be working" in row.log[-1]["event"]
    after = [c for c in deps.tasty.calls[before:] if c[0] == "place"]
    assert all(
        len(c[2]["legs"]) == 1 and c[2]["legs"][0]["symbol"] != stop.short_symbol for c in after
    )  # only the call side's own wing, nothing on the put side
    assert any("Put side wasn't closed" in e.detail for e in seen)


def test_a_pairing_that_cant_be_written_never_loses_the_stops_fill(deps, factory, monkeypatch):
    """Review I5: the fill is committed whatever the paired row does, and it's said."""
    seen = alerts_of(deps)
    paired_run(deps, factory)

    def broken(deps_, session, stop):
        session.add(
            BotExit(
                run_id=stop.run_id,
                group_position=1,
                kind="paired",
                state="pending",
                short_symbol="",
                long_symbol="",
                contracts=1,
                rearms=0,
                log=[],
                created_at=OPEN_TIME,
                updated_at=OPEN_TIME,
            )
        )
        session.flush()
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_exits, "_paired_row", broken)
    fill_call_stop(deps, factory)
    stop = [e for e in exits(factory, "stop") if e.group_position == 0][0]
    assert stop.state == "filled" and exits(factory, "paired") == []
    assert exits(factory, "wing")  # its own wing was still handled
    assert any("paired close not started" in e.title for e in seen)


def test_a_paired_close_held_by_freeze_past_its_day_expires_with_an_alert(deps, factory):
    seen = alerts_of(deps)
    paired_run(deps, factory)
    _pending_pair(factory)
    deps.now = lambda: OPEN_TIME + timedelta(days=1)
    bot_exits.monitor_pass(deps)
    [row] = exits(factory, "paired")
    assert row.state == "expired" and exits(factory, "close") == []
    assert any("paired close not done" in e.title for e in seen)


def test_a_paired_close_that_waited_through_freeze_closes_once_it_is_off(deps, factory):
    run_id = paired_run(deps, factory)
    fresh_quotes(deps, factory, run_id)
    _pending_pair(factory)
    orders.set_switch(factory, "freeze", True)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "paired")[0].state == "pending"
    orders.set_switch(factory, "freeze", False)
    bot_exits.monitor_pass(deps)
    assert exits(factory, "paired")[0].state == "filled"
    assert len(exits(factory, "close")) == 1


def test_a_paired_close_sizes_to_what_the_broker_holds(deps, factory):
    """The records say 1 of the put side; the broker holds none of its short: the close
    sends nothing for a leg not held (_size_close)."""
    from optionsjourney.trading.orders import plain as p_

    run_id = paired_run(deps, factory)
    fresh_quotes(deps, factory, run_id)
    put = [e for e in exits(factory, "stop") if e.group_position == 1][0]
    deps.tasty.held[p_(put.short_symbol)] = 0
    deps.tasty.held[p_(put.long_symbol)] = 0
    fill_call_stop(deps, factory)
    closes = [c[2] for c in deps.tasty.calls if c[0] == "place" and len(c[2]["legs"]) == 2]
    assert closes == []
    [close] = exits(factory, "close")
    assert close.state in ("expired", "failed")


def test_a_paired_one_leg_group_sold_at_a_limit_never_asks_less(deps, factory):
    """Review 12: "sold at a limit" with a price: the chase is floored there, and after
    its last try it gives up (no market, no leg-out)."""
    from optionsjourney.bots.definition import LegDef

    bot_id = make(factory, live=True, exhaust="natural_then_leg_out")
    bot = bot_store.load(factory, bot_id)
    legs = (LegDef(0, Side.SELL, "put", target=D("2.90"), group=0),
            LegDef(1, Side.BUY, "put", pick="offset", target=D("-75"), link=0,
                   group=1))  # fmt: skip
    groups = (GroupDef(0, "Short put", stop_pct=D("95"), close_on_stop=1),
              GroupDef(1, "Long put", on_stop_remainder="close_limit",
                       remainder_limit=D("0.30")))  # fmt: skip
    assert bot_store.save(factory, replace(bot, legs=legs, groups=groups), OPEN_TIME)[1] == {}
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    fresh_quotes(deps, factory, run_id)  # the long's mid is 0.05
    deps.tasty.close = "work"
    fill_call_stop(deps, factory)
    sells = [c for c in deps.tasty.calls if c[0] in ("place", "replace")
             and c[-1]["legs"][0]["action"] == "Sell to Close"]  # fmt: skip
    assert sells and all(D(c[-1]["price"]) >= D("0.30") for c in sells)
    assert all(c[-1]["order-type"] == "Limit" for c in sells)
    [close] = exits(factory, "close")
    assert close.state == "failed"


# --- Decision 0048: a stop the app holds on a TastyTrade short ----------------------------


def app_held_run(deps, factory):
    """A live put credit spread whose stop the app holds on its short (what a group whose
    profit target rests gets): its resting stop cancelled at the fake, its row watching."""
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"), target=D("50"))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    (stop,) = exits(factory, "stop")
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.state, row.broker_order_id, row.app_held = bot_exits.WATCHING, None, True
        session.commit()
    return run_id, stop.id


def stop_orders(deps):
    return [c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]


def test_an_app_held_stop_is_watched_again_and_never_sent(deps, factory):
    _, stop_id = app_held_run(deps, factory)
    sent = len(stop_orders(deps))
    with factory() as session:
        session.get(BotExit, stop_id).state = "cancelled"  # pulled by a close that gave up
        session.commit()
    with bot_exits.EXITS_LOCK:
        assert bot_exits.rearm(deps, stop_id, first=True)
        assert bot_exits._settle_old_stop(deps, stop_id, cancel_sent=True) == "back"
    assert exits(factory, "stop")[0].state == bot_exits.WATCHING
    assert len(stop_orders(deps)) == sent


def test_a_close_of_an_app_held_group_must_close_and_carries_the_mark(deps, factory):
    run_id, _ = app_held_run(deps, factory)
    run = bot_exits._Run(run_id, "5WX01234", True, "tastytrade")
    held = bot_exits._Close(1, None, run, None, (), 1, None, kind="close",
                            by=bot_exits.WATCHED_STOP_BY, app_held=True)  # fmt: skip
    assert bot_exits._must_close(held)
    assert not bot_exits._must_close(replace(held, app_held=False))
    with factory() as session:
        run_row = session.get(BotRun, run_id)
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        assert bot_exits.group_columns(session, run_row, 0, rows)["app_held"] is True


def test_a_close_with_no_entry_takes_the_runs_side_for_its_sign():
    def side(fill, target):
        return bot_exits._side_sign(BotRun(fill_credit=fill, target_credit=target))

    # The fill's price decides, else the price the entry asked for, else a credit.
    assert side(D("-1.20"), D("1.00")) == D(-1)
    assert side(D("1.20"), D("-1.00")) == D(1)
    assert side(None, D("-0.80")) == D(-1)
    assert side(None, None) == D(1)
    run = bot_exits._Run(1, "A", True, "tastytrade")
    close = bot_exits._Close(1, None, run, None, (), 1, None, side_sign=side(D("-1.20"), None))
    assert close.sign == D(-1)  # no bot is read: a close's bot may be None
    assert replace(close, entry=D("0.40")).sign == D("0.40")  # an entry wins over the side


# --- Decision 0048: a resting target on any TastyTrade group -------------------------------


def resting_run(deps, factory, contracts=None):
    """A live put credit spread (stop 95, target 50) whose target rests at the fake and whose
    stop the app holds on its short. Returns (run id, stop id, target id)."""
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"), target=D("50"))
    if contracts is not None:
        force_contracts(factory, bot_id, contracts)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    return (run_id, *rest_the_target(deps, factory, run_id))


def test_a_resting_target_fill_ends_the_watched_stop(deps, factory):
    _, stop_id, target_id = resting_run(deps, factory)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    deps.tasty.fill(order_id, D("1.05"))
    bot_exits.monitor_pass(deps)
    with factory() as session:
        assert session.get(BotExit, target_id).state == "filled"
        stop = session.get(BotExit, stop_id)
        assert stop.state == "cancelled" and stop.log[-1]["event"] == "the profit target filled"


def test_a_resting_target_gone_outside_the_app_says_its_stop_still_works(deps, factory):
    seen = []
    deps.notify = seen.append
    run_id, stop_id, target_id = resting_run(deps, factory)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    deps.tasty.cancel_order("5WX01234", order_id)
    bot_exits.monitor_pass(deps)
    with factory() as session:
        assert session.get(BotExit, target_id).state == "failed"
        assert session.get(BotExit, stop_id).state == bot_exits.WATCHING
        assert not (session.get(BotRun, run_id).reason or "").startswith(bot_entry.NEEDS_YOU)
    [gone] = [e for e in seen if e.title.endswith("profit target gone")]
    assert "Its stop is still watched by the app" in gone.detail


def test_rested_knows_a_target_placed_to_rest():
    assert bot_exits.rested(BotExit(kind="target", state="working", log=[]))
    assert bot_exits.rested(BotExit(kind="target", state="failed", log=[{"rests": "target"}]))
    assert not bot_exits.rested(BotExit(kind="target", state="armed", log=[]))


# Task 10 (decision 0048): a close cancels the resting target and reads it back first.


def gtc_closes(deps):
    """Every resting target placed: a GTC closing limit (never the GTC stop a run first
    rests, which rest_the_target cancels)."""
    return [c for c in deps.tasty.calls if c[0] == "place" and c[2]["time-in-force"] == "GTC"
            and c[2]["order-type"] == "Limit"
            and not c[2]["legs"][0]["action"].endswith("Open")]  # fmt: skip


def quote_target_legs(deps, factory, run_id):
    """Fresh quotes on every leg at its fill, so a close can price from the mid."""
    with factory() as session:
        fills = session.get(BotRun, run_id).filled_legs
    for f in fills:
        price = D(f["fill_price"])
        deps.hub.set(f["symbol"], max(price - D("0.05"), D("0.01")), price + D("0.05"), OPEN_TIME)


def stop_fires(deps, run_id, units=1):
    """What bot_watched_stops._fire does once a watched stop is reached."""
    return bot_exits.fire_close(deps, run_id, 0, units, units, by=bot_exits.WATCHED_STOP_BY)


def test_a_watched_stop_cancels_the_resting_target_reads_it_back_then_closes(deps, factory):
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    close_id = stop_fires(deps, run_id)
    assert ("cancel", order_id) in deps.tasty.calls
    cancel_at = deps.tasty.calls.index(("cancel", order_id))
    closing = [i for i, c in enumerate(deps.tasty.calls) if c[0] == "place"
               and c[2]["time-in-force"] != "GTC"
               and not c[2]["legs"][0]["action"].endswith("Open")]  # fmt: skip
    assert closing and closing[0] > cancel_at  # the cancel was read back before the close
    with factory() as session:
        assert session.get(BotExit, close_id).state == "filled"
        target = session.get(BotExit, target_id)
        assert target.state == "cancelled" and target.log[-1]["cancel"] == "confirmed"


def test_a_failed_cancel_of_the_resting_target_holds_the_close_and_alerts(deps, factory):
    seen = []
    deps.notify = seen.append
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    deps.tasty.stuck.add(order_id)  # the broker ignores the cancel
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    close_id = stop_fires(deps, run_id)
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed  # nothing sent
    with factory() as session:
        assert session.get(BotExit, close_id).state == "failed"
        assert session.get(BotExit, target_id).state == "working"  # left as it is
        assert session.get(BotExit, stop_id).state == bot_exits.WATCHING  # watched again
    [held] = [e for e in seen if e.title.endswith("not started")]
    assert "left as it is" in held.detail and "Check your broker" in held.detail


def test_a_resting_target_found_filled_is_recorded_and_nothing_else_is_sent(deps, factory):
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    deps.tasty.fill(order_id, D("1.05"))  # filled before the monitor read it
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    close_id = stop_fires(deps, run_id)
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed
    with factory() as session:
        assert session.get(BotExit, target_id).state == "filled"
        assert session.get(BotExit, close_id).state == "cancelled"
        assert session.get(BotExit, stop_id).state == "cancelled"


def test_a_watched_stop_closes_only_what_a_part_filled_target_left(deps, factory):
    run_id, _, target_id = resting_run(deps, factory, contracts=2)
    quote_target_legs(deps, factory, run_id)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    deps.tasty.part_fill(order_id, 1)  # one of the two units closed at the target
    close_id = stop_fires(deps, run_id, units=2)
    closes = [c[2] for c in deps.tasty.calls if c[0] == "place" and c[2]["time-in-force"] != "GTC"
              and not c[2]["legs"][0]["action"].endswith("Open")]  # fmt: skip
    assert closes and {int(leg["quantity"]) for leg in closes[0]["legs"]} == {1}
    with factory() as session:
        assert session.get(BotExit, target_id).log[-1]["partly_filled"] == 1
        assert session.get(BotExit, close_id).state == "filled"
    assert len(gtc_closes(deps)) == 1  # no target placed again: the group is closed


def test_a_close_that_gives_up_places_the_resting_target_again(deps, factory):
    deps.tasty.close = "work"  # the close never fills: it gives up
    run_id, _, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    with factory() as session:
        target = session.get(BotExit, target_id)
        assert target.state == "working" and target.log[-1]["event"].startswith("placed again")
    assert len(gtc_closes(deps)) == 2


def claimed_and_cut_off(deps, run_id):
    """A watched stop's close claimed, then the app died before its job ran."""
    with bot_exits.EXITS_LOCK:
        claim = bot_exits._claim_side(
            deps, run_id, 0, "close", "a close of 1 for the stop", contracts=1, open=1,
            by=bot_exits.WATCHED_STOP_BY,
        )  # fmt: skip
    assert claim is not None
    return claim[0]


def test_a_restart_with_the_targets_cancel_unsent_leaves_it_resting(deps, factory):
    run_id, stop_id, target_id = resting_run(deps, factory)
    claimed_and_cut_off(deps, run_id)
    bot_exits.recover_targets(deps)
    with factory() as session:
        assert session.get(BotExit, target_id).state == "working"
        assert session.get(BotExit, stop_id).state == bot_exits.WATCHING
    assert len(gtc_closes(deps)) == 1


def test_a_restart_after_the_targets_cancel_landed_places_it_again(deps, factory):
    run_id, stop_id, target_id = resting_run(deps, factory)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    claimed_and_cut_off(deps, run_id)
    deps.tasty.cancel_order("5WX01234", order_id)  # the cancel landed; the close never sent
    bot_exits.recover_targets(deps)
    with factory() as session:
        target = session.get(BotExit, target_id)
        assert target.state == "working" and target.broker_order_id != order_id
        assert session.get(BotExit, stop_id).state == bot_exits.WATCHING
    assert len(gtc_closes(deps)) == 2


def test_a_restart_finding_the_target_filled_records_it_and_watches_nothing(deps, factory):
    run_id, stop_id, target_id = resting_run(deps, factory)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    close_id = claimed_and_cut_off(deps, run_id)
    deps.tasty.fill(order_id, D("1.05"))
    bot_exits.recover_targets(deps)
    with factory() as session:
        assert session.get(BotExit, target_id).state == "filled"
        assert session.get(BotExit, stop_id).state == "cancelled"
        assert session.get(BotExit, close_id).state == "cancelled"
    assert len(gtc_closes(deps)) == 1


def test_a_resting_targets_fill_and_the_end_of_its_watch_are_one_commit(deps, factory, monkeypatch):
    _, stop_id, target_id = resting_run(deps, factory)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    deps.tasty.fill(order_id, D("1.05"))
    ending = bot_exits._end_the_watch

    def breaks(*args, **kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(bot_exits, "_end_the_watch", breaks)
    bot_exits.monitor_pass(deps)
    with factory() as session:  # neither written: never a filled target with a live watch
        assert session.get(BotExit, target_id).state == "working"
        assert session.get(BotExit, stop_id).state == bot_exits.WATCHING
    monkeypatch.setattr(bot_exits, "_end_the_watch", ending)
    bot_exits.monitor_pass(deps)
    with factory() as session:
        assert session.get(BotExit, target_id).state == "filled"
        assert session.get(BotExit, stop_id).state == "cancelled"


def target_reads_fail(deps, order_id):
    """The broker can't be read for one order (the resting target's)."""
    reading = deps.tasty.get_order

    def get_order(account, oid):
        if oid == order_id:
            raise RuntimeError("outage")
        return reading(account, oid)

    deps.tasty.get_order = get_order


def test_a_target_unreadable_after_its_cancel_holds_the_close_and_says_no_stop(deps, factory):
    seen = []
    deps.notify = seen.append
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    target_reads_fail(deps, order_id)
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    close_id = stop_fires(deps, run_id)
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed  # nothing sent
    with factory() as session:
        assert session.get(BotExit, close_id).state == "failed"
        target = session.get(BotExit, target_id)
        assert target.state == "failed" and target.log[-1]["event"] == bot_exits.TARGET_UNCLEAR
        assert session.get(BotExit, stop_id).state == "cancelled"  # never a second close
        reason = session.get(BotRun, run_id).reason
    said = "Its stop isn't watched either, so Put spread may have no stop and no profit target"
    assert reason.startswith(bot_entry.NEEDS_YOU) and said in reason
    [held] = [e for e in seen if e.title.endswith("not started")]
    assert said in held.detail and "Check your broker" in held.detail


def test_a_refused_cancel_of_the_resting_target_holds_the_close(deps, factory):
    seen = []
    deps.notify = seen.append
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    orders.set_switch(factory, "freeze", True)  # the cancel is refused: never sent
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    close_id = stop_fires(deps, run_id)
    assert ("cancel", order_id) not in deps.tasty.calls
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed
    with factory() as session:
        assert session.get(BotExit, close_id).state == "failed"
        target = session.get(BotExit, target_id)
        assert target.state == "working" and "its cancel wasn't sent" in target.log[-1]["event"]
        assert session.get(BotExit, stop_id).state == bot_exits.WATCHING
    [held] = [e for e in seen if e.title.endswith("not started")]
    assert "left as it is: its cancel wasn't sent" in held.detail


def test_a_restart_that_cant_read_the_pulled_target_holds_it(deps, factory):
    seen = []
    deps.notify = seen.append
    run_id, stop_id, target_id = resting_run(deps, factory)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    claimed_and_cut_off(deps, run_id)
    target_reads_fail(deps, order_id)
    bot_exits.recover_targets(deps)
    assert ("cancel", order_id) not in deps.tasty.calls
    assert len(gtc_closes(deps)) == 1  # never a second target
    with factory() as session:
        target = session.get(BotExit, target_id)
        assert target.state == "failed" and target.log[-1]["event"] == bot_exits.TARGET_UNCLEAR
        assert session.get(BotExit, target_id).broker_order_id == order_id
        assert session.get(BotExit, stop_id).state == "cancelled"  # its stop isn't watched
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)
    [unclear] = [e for e in seen if e.title.endswith("profit target change unclear")]
    assert "no close is sent on this group while a profit target may still rest" in unclear.detail
    [error] = [e for e in seen if e.title.endswith("close error")]
    assert "its stop isn't watched either" in error.detail
    assert "armed again" not in error.detail
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    bot_exits.monitor_pass(deps)  # nothing settles it while it may rest
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed


def test_a_stop_firing_while_a_target_may_still_rest_is_held(deps, factory):
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    claimed_and_cut_off(deps, run_id)
    target_reads_fail(deps, order_id)
    bot_exits.recover_targets(deps)  # held: the target may still rest
    with factory() as session:  # the stop watched again anyway (any other way back)
        session.get(BotExit, stop_id).state = bot_exits.WATCHING
        session.commit()
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        assert bot_exits.target_may_rest(rows) and bot_exits.held_back(rows) == "close"
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    assert stop_fires(deps, run_id) is None  # the claim refuses it
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed
    assert not bot_manage.close_part(deps, run_id, 0, "1").ok
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed


def test_a_stop_firing_after_an_unclear_target_change_is_held(deps, factory):
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    deps.tasty.stuck.add(order_id)  # the change's cancel is never confirmed
    ok, said = bot_exits.change_resting_target(deps, run_id, 0, D("0.80"))
    assert not ok and "no close is sent on this group" in said
    with factory() as session:
        assert session.get(BotExit, target_id).log[-1]["event"] == bot_exits.TARGET_UNCLEAR
        stop = session.get(BotExit, stop_id)
        stop.updated_at = OPEN_TIME + timedelta(minutes=5)  # touched after the change
        session.commit()
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    assert stop_fires(deps, run_id) is None
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed


def switch_target_off(factory, run_id):
    from optionsjourney.db.models import BotTradeOverride

    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=0, stop_off=False,
                                     target_off=True, changed_at=OPEN_TIME, log=[]))  # fmt: skip
        session.commit()


def test_a_restart_with_the_target_off_cancels_the_pulled_target_never_places_it(deps, factory):
    run_id, stop_id, target_id = resting_run(deps, factory)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    claimed_and_cut_off(deps, run_id)  # its cancel never went out
    switch_target_off(factory, run_id)  # you switched the target off meanwhile
    bot_exits.recover_targets(deps)
    assert ("cancel", order_id) in deps.tasty.calls
    assert deps.tasty.orders[order_id].status is OrderStatus.CANCELLED  # read back: gone
    assert len(gtc_closes(deps)) == 1  # never placed again
    with factory() as session:
        target = session.get(BotExit, target_id)
        assert target.state == "cancelled" and target.log[-1]["cancel"] == "confirmed"
        assert target.log[-1]["event"] == bot_exits.TARGET_SWITCHED_OFF
        assert session.get(BotExit, stop_id).state == bot_exits.WATCHING


def test_a_restart_with_the_target_off_whose_cancel_wont_land_leaves_it_working(deps, factory):
    seen = []
    deps.notify = seen.append
    run_id, _, target_id = resting_run(deps, factory)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    claimed_and_cut_off(deps, run_id)
    switch_target_off(factory, run_id)
    deps.tasty.stuck.add(order_id)
    bot_exits.recover_targets(deps)
    assert len(gtc_closes(deps)) == 1
    with factory() as session:  # working: the next close pulls it, never a second order
        assert session.get(BotExit, target_id).state == "working"
    [rests] = [e for e in seen if e.title.endswith("profit target still rests")]
    assert "the switched-off target still rests there" in rests.detail
    [error] = [e for e in seen if e.title.endswith("close error")]
    assert "armed again" not in error.detail


def test_a_held_target_is_released_once_nothing_rests_on_its_legs(deps, factory, monkeypatch):
    monkeypatch.setattr(bot_exits, "_HELD_SAID", set())  # said once a side: not by other tests
    seen = []
    deps.notify = seen.append
    run_id, _, target_id = resting_run(deps, factory)
    with factory() as session:
        order_id = session.get(BotExit, target_id).broker_order_id
    claimed_and_cut_off(deps, run_id)
    reading = deps.tasty.get_order
    target_reads_fail(deps, order_id)
    bot_exits.recover_targets(deps)
    bot_exits.monitor_pass(deps)  # still resting at the broker: held, said once
    [waiting] = [e for e in seen if e.title.endswith("a side is held back")]
    assert "A profit target on Put spread may still be working" in waiting.detail
    deps.tasty.get_order = reading
    deps.tasty.cancel_order("5WX01234", order_id)  # you cancelled it at your broker
    bot_exits.monitor_pass(deps)
    bot_exits.monitor_pass(deps)
    with factory() as session:
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        assert bot_exits.held_back(rows) is None and not bot_exits.target_may_rest(rows)
    assert len([e for e in seen if e.title.endswith("is no longer held back")]) == 1


def test_a_time_action_moves_a_resting_target_by_cancel_and_place(deps, factory):
    run_id, _, target_id = resting_run(deps, factory)
    with factory() as session:
        old = session.get(BotExit, target_id).broker_order_id
    action = TimeActionDef("dit", 0, "10:00", target_pct=D("40"))
    state, notes = bot_exits.apply_time_action(deps, run_id, 0, action, 1, False)
    assert state == "filled", notes
    assert ("cancel", old) in deps.tasty.calls and len(gtc_closes(deps)) == 2


def test_a_time_action_puts_a_resting_target_switched_off_back_at_tastytrade(deps, factory):
    """A time action's target on a group whose resting target you switched off switches it
    back on by placing it again at the action's level, never by arming one."""
    run_id, _, target_id = resting_run(deps, factory)
    assert bot_manage.target_off(deps, run_id, 0).ok
    action = TimeActionDef("dit", 0, "10:00", target_pct=D("40"))
    state, notes = bot_exits.apply_time_action(deps, run_id, 0, action, 1, False)
    assert state == "filled", notes
    with factory() as session:
        targets = list(session.scalars(select(BotExit).where(
            BotExit.run_id == run_id, BotExit.kind == "target")))  # fmt: skip
    assert [t.state for t in targets] == ["working"] and len(gtc_closes(deps)) == 2
    assert not bot_exits.target_off_sides(factory, run_id)


# --- Decision 0048: place_protections follows the table ----------------------------------


def chosen(deps, factory, key, rests, stop="50", target="50", **group):
    """A live run of `key` with `stop`/`target` % on every group and Rests at the broker
    `rests`, armed and live again after the save. Returns (run id, alerts seen)."""
    seen = []
    deps.notify = seen.append
    bot_id = make_shape(factory, key, live=True, stop=D(stop) if stop else None,
                        target=D(target) if target else None)  # fmt: skip
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, broker_exit=rests, **group) for g in bot.groups)
    assert bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)[1] == {}
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    return bot_entry.fire(deps, bot_id, "09:47"), seen


def exit_states(factory):
    return [(e.kind, e.state, e.watched, e.app_held) for e in exits(factory)
            if e.kind in ("stop", "target")]  # fmt: skip


def resting_limits(deps):
    return [c[2] for c in deps.tasty.calls if c[0] == "place"
            and c[2]["time-in-force"] == "GTC" and c[2]["order-type"] == "Limit"
            and not c[2]["legs"][0]["action"].endswith("Open")]  # fmt: skip


def test_a_credit_spread_that_chose_stop_rests_its_stop(deps, factory):
    chosen(deps, factory, "put_credit_spread", "stop", stop="95")
    assert exit_states(factory) == [("stop", "working", False, False),
                                    ("target", "armed", False, False)]  # fmt: skip
    assert resting_limits(deps) == []


def test_a_credit_spread_that_chose_target_rests_it_and_holds_its_stop_on_the_short(deps, factory):
    chosen(deps, factory, "put_credit_spread", "target", stop="95")
    assert exit_states(factory) == [("stop", "watching", False, True),
                                    ("target", "working", False, False)]  # fmt: skip
    [body] = resting_limits(deps)
    assert {leg["action"] for leg in body["legs"]} == {"Buy to Close", "Sell to Close"}
    assert not [c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]
    (stop,) = exits(factory, "stop")
    assert stop.anchor_symbol and stop.trigger > 0  # the stop it would have rested
    assert stop.log[-1]["result"] == bot_exits.WATCHED_SHORT


def test_a_group_with_two_sold_legs_rests_its_target_and_watches_its_pnl(deps, factory):
    _, seen = chosen(deps, factory, "iron_butterfly", "stop")
    assert exit_states(factory) == [("stop", "watching", True, False),
                                    ("target", "working", True, False)]  # fmt: skip
    assert len(resting_limits(deps)) == 1
    assert not [e for e in seen if "opened for" in e.title]  # shown at save, no alert


def test_a_debit_fill_on_a_group_that_chose_stop_falls_back_and_says_so(deps, factory):
    run_id, seen = chosen(deps, factory, "call_debit_spread", "stop")
    assert exit_states(factory) == [("stop", "watching", True, False),
                                    ("target", "working", True, False)]  # fmt: skip
    [said] = [e for e in seen if "opened for a debit" in e.title]
    assert said.title == "call_debit_spread: opened for a debit, so its stop is watched by the bot."
    assert "a stop can't rest at the broker on a debit spread" in said.detail
    assert "rests at TastyTrade" in said.detail and said.run_id == run_id
    (stop,) = exits(factory, "stop")  # the run page shows it on the group (spec section 3)
    assert {"note": exit_plan.FALLBACK_NOTE} in [{"note": e.get("note")} for e in stop.log]


def test_a_debit_fill_on_a_group_that_chose_target_says_nothing(deps, factory):
    _, seen = chosen(deps, factory, "call_debit_spread", "target")
    assert exit_states(factory) == [("stop", "watching", True, False),
                                    ("target", "working", True, False)]  # fmt: skip
    assert not [e for e in seen if "opened for" in e.title]


def test_a_150_percent_target_on_a_credit_fill_sets_none_and_alerts(deps, factory):
    _, seen = chosen(deps, factory, "put_credit_spread", "stop", stop="95", target="150")
    assert [k for k, *_ in exit_states(factory)] == ["stop"]
    [said] = [e for e in seen if "some exits changed" in e.title]
    assert "a 150% target can't be kept on a credit. No target set." in said.detail


def test_on_a_debit_fill_a_percent_of_the_credit_is_the_same_loss_on_the_debit(deps, factory):
    chosen(deps, factory, "call_debit_spread", "target", stop="200",
           stop_basis="entry_premium")  # fmt: skip
    (stop,) = exits(factory, "stop")
    calc = stops.calc_of(stop.log)
    assert calc["basis"] == "group_entry" and calc["configured"] == "100"


def test_on_a_debit_fill_a_stop_on_the_shorts_fill_is_dropped_and_alerted(deps, factory):
    _, seen = chosen(deps, factory, "call_debit_spread", "target", stop="95",
                     stop_basis="leg_premium")  # fmt: skip
    assert [k for k, *_ in exit_states(factory)] == ["target"]
    [said] = [e for e in seen if "some exits changed" in e.title]
    assert "a % above the short's fill only works on a credit. No stop set." in said.detail
    (target,) = exits(factory, "target")
    assert any(e.get("note") for e in target.log)  # the run page shows it on the group


def test_on_a_debit_fill_the_closing_order_is_ignored_and_alerted(deps, factory):
    bot_id = make_shape(factory, "call_debit_spread", live=True, stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_id)
    overlay = replace(bot, stop_co_mode="both", stop_co_amount=D("0.15"))
    assert bot_store.save(factory, overlay, OPEN_TIME)[1] == {}
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)
    seen = []
    deps.notify = seen.append
    bot_entry.fire(deps, bot_id, "09:47")
    assert any("the closing order works only on a credit trade's stop" in e.detail for e in seen)


def test_exits_follow_the_bot_as_it_is_at_the_fill(deps, factory, monkeypatch):
    """A bot edited between the entry's send and its fill: the exits read the bot then."""
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"), target=D("50"))
    real = bot_exits.place_protections

    def edit_then_place(d, run_id, **options):
        bot = bot_store.load(factory, bot_id)
        groups = tuple(replace(g, broker_exit="target") for g in bot.groups)
        assert bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)[1] == {}
        real(d, run_id, **options)

    monkeypatch.setattr(bot_exits, "place_protections", edit_then_place)
    bot_entry.fire(deps, bot_id, "09:47")
    assert [s for k, s, *_ in exit_states(factory) if k == "target"] == ["working"]


def test_a_paired_group_whose_stop_is_watched_says_pairing_wont_run(deps, factory):
    bot_id = make_shape(factory, "iron_condor", live=True, stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_id)
    groups = (replace(bot.groups[0], stop_basis="group_entry", close_on_stop=1),
              replace(bot.groups[1], stop_basis="group_entry"))  # fmt: skip
    assert bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)[1] == {}
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    deps.tasty.prices.update({sym(5985, "call"): D("40.10"), sym(5615, "put"): D("40.10")})
    seen = []
    deps.notify = seen.append
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        assert session.get(BotRun, run_id).fill_credit < 0  # priced a credit, filled a debit
    [said] = [e for e in seen if "opened for a debit" in e.title]
    assert (
        "Call side: Its stop is watched by the bot, so closing Put side after it doesn't "
        "run on this trade."
    ) in said.detail


# --- Final review (issue #107): a resting target's send that may have landed ---------------


def target_sends_unclear(deps):
    """The broker takes each resting target (a GTC closing limit) but its answer is lost:
    the order works at the fake while the app is told Unclear."""
    from tests.services.bot_fakes import _a_target

    placing = deps.tasty.place_order

    def place(account, body):
        oid = placing(account, body)
        if not body["legs"][0]["action"].endswith("Open") and _a_target(body):
            raise RequestUnclear("timed out")
        return oid

    deps.tasty.place_order = place


def held_by_target(factory, run_id):
    with factory() as session:
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        return bot_exits.target_may_rest(rows), bot_exits.held_back(rows)


def test_an_unclear_target_send_at_the_fill_holds_the_group(deps, factory):
    target_sends_unclear(deps)
    run_id, seen = chosen(deps, factory, "call_debit_spread", "target")
    (target,) = exits(factory, "target")
    assert target.state == "failed" and target.log[-1]["event"] == bot_exits.TARGET_UNCLEAR
    assert held_by_target(factory, run_id) == (True, "close")
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)
    [held] = [e for e in seen if e.title.endswith("profit target unclear")]
    assert "no close is sent on this group" in held.detail
    assert not [e for e in seen if "isn't resting" in e.detail]  # it may well rest
    assert not [e for e in seen if "Restore missing exits" in e.detail]
    quote_target_legs(deps, factory, run_id)
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    assert stop_fires(deps, run_id) is None  # never a second closing order
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed


def test_a_stale_placing_resting_target_after_a_restart_is_held(deps, factory):
    run_id, _ = chosen(deps, factory, "call_debit_spread", "target")
    (target,) = exits(factory, "target")
    with factory() as session:  # the app died between the send and recording it
        row = session.get(BotExit, target.id)
        row.state, row.broker_order_id = "placing", None
        session.commit()
    seen = []
    deps.notify = seen.append
    deps.now = lambda: OPEN_TIME + timedelta(minutes=3)
    from optionsjourney.services import bot_loops

    bot_loops.check_exits(deps)
    with factory() as session:
        row = session.get(BotExit, target.id)
        assert row.state == "failed" and row.log[-1]["event"] == bot_exits.TARGET_UNCLEAR
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)
    assert held_by_target(factory, run_id) == (True, "close")
    [said] = [e for e in seen if "needs you" in e.title]
    assert "A profit target may rest at TastyTrade" in said.detail
    quote_target_legs(deps, factory, run_id)
    placed = len([c for c in deps.tasty.calls if c[0] == "place"])
    assert stop_fires(deps, run_id) is None
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == placed


def test_an_unclear_send_putting_a_pulled_target_back_holds_it(deps, factory):
    seen = []
    deps.notify = seen.append
    deps.tasty.close = "work"  # the close never fills: it gives up
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    target_sends_unclear(deps)
    bot_manage.close_part(deps, run_id, 0, "1")
    with factory() as session:
        target = session.get(BotExit, target_id)
        assert target.state == "failed" and target.log[-1]["event"] == bot_exits.TARGET_UNCLEAR
    assert held_by_target(factory, run_id) == (True, "close")
    [didnt] = [e for e in seen if e.title.endswith("close didn't fill")]
    assert "armed again" not in didnt.detail and "placed again" not in didnt.detail


def test_an_unclear_send_switching_a_resting_target_back_on_holds_it(deps, factory):
    run_id, _, target_id = resting_run(deps, factory)
    assert bot_manage.target_off(deps, run_id, 0).ok
    target_sends_unclear(deps)
    assert not bot_manage.target_on(deps, run_id, 0).ok
    with factory() as session:
        target = session.get(BotExit, target_id)
        assert target.state == "failed" and target.log[-1]["event"] == bot_exits.TARGET_UNCLEAR
    assert held_by_target(factory, run_id) == (True, "close")


def test_a_refused_send_switching_a_resting_target_back_on_is_recorded(deps, factory):
    run_id, _, target_id = resting_run(deps, factory)
    assert bot_manage.target_off(deps, run_id, 0).ok
    deps.tasty.errors["place"] = BrokerError("buying power")
    assert not bot_manage.target_on(deps, run_id, 0).ok
    with factory() as session:
        target = session.get(BotExit, target_id)
        assert target.state == "cancelled"
        assert target.log[-1]["event"].startswith("not placed again")
        assert "buying power" in target.log[-1]["result"]


def test_an_unclear_send_of_a_target_freeze_held_back_holds_it(deps, factory):
    run_id, _ = chosen(deps, factory, "call_debit_spread", "target")
    (target,) = exits(factory, "target")
    deps.tasty.cancel_order("5WX01234", target.broker_order_id)
    with factory() as session:  # what a fill under Freeze leaves: waiting to be placed
        row = session.get(BotExit, target.id)
        row.state, row.broker_order_id = "pending", None
        session.commit()
    target_sends_unclear(deps)
    bot_exits.monitor_pass(deps)
    (target,) = exits(factory, "target")
    assert target.state == "failed" and target.log[-1]["event"] == bot_exits.TARGET_UNCLEAR
    assert held_by_target(factory, run_id) == (True, "close")


# --- Final review C2: alerts never say a group is protected when it isn't ---------------


def target_sends_refused(deps, error="buying power"):
    """The broker turns down every resting target (a GTC closing limit); others go."""
    from tests.services.bot_fakes import _a_target

    placing = deps.tasty.place_order

    def place(account, body):
        if not body["legs"][0]["action"].endswith("Open") and _a_target(body):
            raise BrokerError(error)
        return placing(account, body)

    deps.tasty.place_order = place


def test_a_fall_back_alert_never_says_a_target_rests_when_its_send_failed(deps, factory):
    target_sends_refused(deps)
    run_id, seen = chosen(deps, factory, "call_debit_spread", "stop")
    [fell] = [e for e in seen if "opened for a debit" in e.title]
    assert "rests at TastyTrade" not in fell.detail
    [unplaced] = [e for e in seen if e.title.endswith("profit target not placed")]
    assert "Its stop is still watched by the app." in unplaced.detail
    with factory() as session:
        assert not (session.get(BotRun, run_id).reason or "").startswith(bot_entry.NEEDS_YOU)


def test_a_target_not_placed_on_a_group_with_no_stop_says_it_has_no_exit(deps, factory):
    target_sends_refused(deps)
    run_id, seen = chosen(deps, factory, "put_credit_spread", "target", stop=None)
    [unplaced] = [e for e in seen if e.title.endswith("profit target not placed")]
    assert "watched" not in unplaced.detail
    assert "It has no stop, so it has no exit now." in unplaced.detail
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)


def test_a_resting_target_gone_on_a_group_with_no_stop_says_it_has_no_exit(deps, factory):
    run_id, seen = chosen(deps, factory, "put_credit_spread", "target", stop=None)
    (target,) = exits(factory, "target")
    deps.tasty.cancel_order("5WX01234", target.broker_order_id)
    bot_exits.monitor_pass(deps)
    [gone] = [e for e in seen if e.title.endswith("profit target gone")]
    assert "watched" not in gone.detail and "It has no stop, so it has no exit now." in gone.detail
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)


def test_a_freeze_held_target_not_placed_on_a_group_with_no_stop_says_no_exit(deps, factory):
    run_id, seen = chosen(deps, factory, "put_credit_spread", "target", stop=None)
    (target,) = exits(factory, "target")
    deps.tasty.cancel_order("5WX01234", target.broker_order_id)
    with factory() as session:
        row = session.get(BotExit, target.id)
        row.state, row.broker_order_id = "pending", None
        session.commit()
    target_sends_refused(deps)
    bot_exits.monitor_pass(deps)
    [unplaced] = [e for e in seen if e.title.endswith("profit target not placed")]
    assert "watched" not in unplaced.detail
    assert "It has no stop, so it has no exit now." in unplaced.detail
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)


def test_a_pulled_target_not_placed_again_on_a_group_with_no_stop_says_no_exit(deps, factory):
    deps.tasty.close = "work"  # the close never fills: it gives up
    run_id, seen = chosen(deps, factory, "put_credit_spread", "target", stop=None)
    quote_target_legs(deps, factory, run_id)
    target_sends_refused(deps)
    bot_manage.close_part(deps, run_id, 0, "1")
    [unplaced] = [e for e in seen if e.title.endswith("profit target not placed")]
    assert "watched" not in unplaced.detail
    assert "It has no stop, so it has no exit now." in unplaced.detail
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)


def test_a_refused_close_says_its_pulled_target_was_not_placed_again(deps, factory):
    """C2d: a close refused because a leg is gone (nothing sent) can't put the resting
    target back (nothing of the group is held its way): you're told it was cancelled and
    not placed again, not just that the stop is back."""
    seen = []
    deps.notify = seen.append
    run_id, _, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    with factory() as session:
        legs = session.get(BotRun, run_id).legs
    wing = next(leg["symbol"] for leg in legs if leg["side"] == "buy")
    deps.tasty.held[plain(wing)] = 0  # the wing was closed outside the app
    stop_fires(deps, run_id)
    with factory() as session:
        target = session.get(BotExit, target_id)
        assert target.state == "cancelled"
        assert target.log[-1]["event"].startswith("not placed again")
    said = " ".join(e.detail for e in seen)
    assert "profit target was cancelled" in said and "wasn't placed again" in said


# --- Final review I1: a close that ends without closing puts the target back first --------


def test_a_close_that_gives_up_puts_the_resting_target_back_before_the_stop(
    deps, factory, monkeypatch
):
    deps.tasty.close = "work"  # the close never fills: it gives up
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    seen_at_rearm = []
    rearming = bot_exits.rearm

    def rearm(d, exit_id, first=False):
        if exit_id == stop_id:
            with factory() as session:
                seen_at_rearm.append(session.get(BotExit, target_id).state)
        return rearming(d, exit_id, first=first)

    monkeypatch.setattr(bot_exits, "rearm", rearm)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    assert seen_at_rearm == ["working"]  # the target rested again before the stop came back
    with factory() as session:
        assert session.get(BotExit, stop_id).state == bot_exits.WATCHING


def test_a_target_held_putting_it_back_keeps_the_stop_off(deps, factory):
    """As _recover does: a target that may rest never has its stop watched again (a close
    that stop fired would be a second closing order), and the alert never says it's back."""
    seen = []
    deps.notify = seen.append
    deps.tasty.close = "work"
    run_id, stop_id, target_id = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    target_sends_unclear(deps)
    bot_manage.close_part(deps, run_id, 0, "1")
    with factory() as session:
        assert session.get(BotExit, stop_id).state == "cancelled"
    [didnt] = [e for e in seen if e.title.endswith("close didn't fill")]
    assert "the stop is back" not in didnt.detail


def test_a_target_is_never_put_back_while_another_close_runs_on_the_group(deps, factory):
    run_id, stop_id, target_id = resting_run(deps, factory)
    close_id = claimed_and_cut_off(deps, run_id)  # pulled the target; then it ended
    with factory() as session:
        session.get(BotExit, close_id).state = "failed"
        target = session.get(BotExit, target_id)
        deps.tasty.cancel_order("5WX01234", target.broker_order_id)  # its cancel landed
        session.add(BotExit(run_id=run_id, group_position=0, kind="close", state="firing",
                            short_symbol=target.short_symbol, long_symbol=target.long_symbol,
                            legs=target.legs, contracts=1, rearms=0, log=[],
                            created_at=OPEN_TIME, updated_at=OPEN_TIME))  # fmt: skip
        session.commit()
    assert not bot_exits._restore_target(deps, close_id)
    assert len(gtc_closes(deps)) == 1  # never a target beside a running close
    with factory() as session:
        assert session.get(BotExit, target_id).state == "cancelled"


def test_a_resting_target_put_back_is_said_placed_again_not_armed(deps, factory):
    """Final review minor: a resting target put back after a close is "placed again at
    TastyTrade", never "armed again"."""
    seen = []
    deps.notify = seen.append
    deps.tasty.close = "work"
    run_id, _, _ = resting_run(deps, factory)
    quote_target_legs(deps, factory, run_id)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    [didnt] = [e for e in seen if e.title.endswith("close didn't fill")]
    assert "its profit target is placed again at TastyTrade" in didnt.detail
    assert "armed again" not in didnt.detail


# Ignore bid-less longs (Tracker2's close_jobs.select_close_legs): a close leaves a bought
# leg nobody bids on to expire. Never on a close you start by hand or a partial close.


def bidless_bot(factory, on=True, key=None, contracts=None):
    """A live bot whose exit chase leaves bid-less longs (or not); its id."""
    bot_id = make(factory, live=True) if key is None else make_shape(factory, key, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, target_pct=D("50")) for g in bot.groups)
    chase = replace(bot.exit_chase, ignore_bidless_longs=on)
    assert bot_store.save(factory, replace(bot, groups=groups, exit_chase=chase),
                          OPEN_TIME)[1] == {}  # fmt: skip
    if contracts is not None:
        force_contracts(factory, bot_id, contracts)
    return bot_id


def closing_orders(deps):
    """Every closing order a close sent (not a stop, not a resting GTC target)."""
    return [
        c[-1] for c in deps.tasty.calls
        if c[0] in ("place", "replace") and c[-1]["order-type"] != "Stop"
        and c[-1].get("time-in-force") != "GTC"
        and all(leg["action"].endswith("Close") for leg in c[-1]["legs"])
    ]  # fmt: skip


def order_symbols(deps):
    return {plain(leg["symbol"]) for body in closing_orders(deps) for leg in body["legs"]}


def left_entries(row):
    return [e for e in row.log if e.get("stage") == "left"]


def test_bidless_long_is_left_out_of_a_target_close(deps, factory):
    bot_entry.fire(deps, bidless_bot(factory), "09:47")
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0", OPEN_TIME)  # the wing: bid 0
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert order_symbols(deps) == {plain(short)}
    assert all(len(body["legs"]) == 1 for body in closing_orders(deps))
    target = exits(factory, "target")[0]
    assert target.state == "filled"
    [left] = left_entries(target)
    assert [plain(s) for s in left["left"]] == [plain(long)] and "no bid" in left["why"]


def test_shorts_are_always_closed_even_with_no_bid(deps, factory):
    bot_entry.fire(deps, bidless_bot(factory), "09:47")
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.10", "0", OPEN_TIME)  # the short's bid is 0 too
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert order_symbols(deps) == {plain(short)}


def test_flag_off_closes_every_leg(deps, factory):
    bot_entry.fire(deps, bidless_bot(factory, on=False), "09:47")
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0", OPEN_TIME)
    bot_exits.fire_target(deps, exits(factory, "target")[0].id)
    assert order_symbols(deps) == {plain(short), plain(long)}
    assert left_entries(exits(factory, "target")[0]) == []


def test_a_rule_close_leaves_the_bidless_long(deps, factory):
    run_id = bot_entry.fire(deps, bidless_bot(factory), "09:47")
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0", OPEN_TIME)
    close_id = bot_exits.fire_close(deps, run_id, 0, 1, 1, by="profit action 1")
    assert order_symbols(deps) == {plain(short)}
    with factory() as session:
        row = session.get(BotExit, close_id)
        assert row.state == "filled" and left_entries(row)


def test_a_close_you_start_by_hand_includes_bidless_longs(deps, factory):
    run_id = bot_entry.fire(deps, bidless_bot(factory), "09:47")
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0", OPEN_TIME)
    assert bot_manage.close_part(deps, run_id, 0, "1").ok
    assert order_symbols(deps) == {plain(short), plain(long)}


def test_a_partial_close_includes_bidless_longs(deps, factory):
    run_id = bot_entry.fire(deps, bidless_bot(factory, contracts=2), "09:47")
    short, long = call_side(factory)
    exact_quotes(deps, short, long, "0.65", "0", OPEN_TIME)
    bot_exits.fire_close(deps, run_id, 0, 1, 2, by="profit action 1")  # 1 of the 2 held
    assert order_symbols(deps) == {plain(short), plain(long)}


def test_all_legs_bidless_longs_gives_up_with_a_reason(deps, factory):
    seen = alerts_of(deps)
    run_id = bot_entry.fire(deps, bidless_bot(factory, key="long_call"), "09:47")
    with factory() as session:
        [leg] = session.get(BotRun, run_id).filled_legs
    deps.hub.set(leg["symbol"], D("0"), D("0.10"), OPEN_TIME)
    close_id = bot_exits.fire_close(deps, run_id, 0, 1, 1, by="profit action 1")
    assert closing_orders(deps) == []
    with factory() as session:
        row = session.get(BotExit, close_id)
        assert row.state == "failed" and left_entries(row)
    assert any("no bid" in e.detail for e in seen)
