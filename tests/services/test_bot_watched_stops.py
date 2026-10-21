"""Schwab stops watched by the app (bots slice 11), never resting at Schwab: since decision
0060 that's a Schwab trade opened before it (migration 0036 marked its stops app-held).
Each run here is made as one was then: `opened_before_0060` gives every Schwab group the
old plan (nothing rests, its stop held by the app on its short), so the watched-stop
machinery these tests cover is the one those trades still run on until they close."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots import exit_plan
from optionsjourney.bots.definition import TimeActionDef
from optionsjourney.db.models import Bot, BotExit, BotRun
from optionsjourney.services import (
    bot_entry,
    bot_exits,
    bot_integrity,
    bot_manage,
    bot_store,
    bot_watched_stops,
    orders,
)
from optionsjourney.trading.orders import Side
from tests.services.bot_fakes import (
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeSchwab,
    FakeTasty,
    force_contracts,
    make_schwab,
    make_shape,
)

D = Decimal


@pytest.fixture(autouse=True)
def opened_before_0060(monkeypatch):
    """Every Schwab group planned as before decision 0060: nothing rests at Schwab, and its
    stop is the app's on the short (app_held), as migration 0036 leaves such a trade."""
    plan_group = exit_plan.plan_group

    def old(group, sides, credit, broker, co_mode="off"):
        plan = plan_group(group, sides, credit, broker, co_mode)
        if broker != "schwab":
            return plan
        stop_on = "short" if plan.stop_on == "broker" else plan.stop_on
        return replace(plan, rests=None, stop_on=stop_on)

    monkeypatch.setattr(exit_plan, "plan_group", old)


@pytest.fixture
def deps(factory):
    tasty, schwab = FakeTasty(), FakeSchwab()
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty, schwab],
        FakeHub(),
        FakeChain(),
        notify=lambda e: None,
        sleep=lambda s: None,
        now=lambda: OPEN_TIME,
        background=lambda fn, *args: fn(*args),
    )
    d.tasty, d.schwab, d.seen = tasty, schwab, []
    d.notify = d.seen.append
    return d


def schwab_run(deps, factory, target_pct=None, exhaust="give_up"):
    bot_id = make_schwab(factory, live=True, exhaust=exhaust)
    if target_pct is not None:
        bot = bot_store.load(factory, bot_id)
        groups = tuple(replace(g, target_pct=D(target_pct)) for g in bot.groups)
        bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)  # fmt: skip
    return bot_entry.fire(deps, bot_id, "09:47")


def exits(factory, kind=None, position=None):
    with factory() as session:
        query = select(BotExit).order_by(BotExit.id)
        if kind:
            query = query.where(BotExit.kind == kind)
        if position is not None:
            query = query.where(BotExit.group_position == position)
        return list(session.scalars(query))


def schwab_orders(deps, kind=None):
    return [
        c for c in deps.schwab.calls
        if c[0] in ("place", "replace") and (kind is None or c[-1]["orderType"] == kind)
    ]  # fmt: skip


def at(deps, minutes):
    later = OPEN_TIME + timedelta(minutes=minutes)
    deps.now = lambda: later
    return later


# Task 4: watched stops


def test_a_schwab_fill_watches_its_stops_and_sends_none(deps, factory):
    run_id = schwab_run(deps, factory)
    stops = exits(factory, "stop")
    assert [s.state for s in stops] == ["watching", "watching"]
    assert all(s.broker_order_id is None for s in stops)
    # The same arithmetic as a resting stop: 2.50 x 1.95 = 4.875, rounded down: 4.80
    assert stops[0].trigger == D("4.80")
    assert schwab_orders(deps, "STOP") == [] and deps.tasty.calls == []
    assert len(schwab_orders(deps)) == 1  # the entry, and nothing else
    found, failed = bot_integrity.run_checks(deps, OPEN_TIME + timedelta(minutes=30))
    assert found == [] and not failed
    with factory() as session:
        assert session.get(BotRun, run_id).broker == "schwab"


def test_a_schwab_condor_watches_exactly_what_slice_12_did(deps, factory):
    """Slice 13's regression check, taken from slice 12's code: a Schwab condor's
    stops watch the short's price (not a group P&L), and its rows are unchanged."""
    schwab_run(deps, factory, target_pct="50")
    rows = [
        (e.group_position, e.kind, e.state, e.contracts, e.trigger, e.limit,
         e.broker_order_id, e.watched, e.log[-1])
        for e in exits(factory)
    ]  # fmt: skip
    at = OPEN_TIME.isoformat()
    # The words are the fixture's (an app-held stop's); the rows are what slice 12 wrote
    watching = {"at": at, "result": "watched by the app on its short: its profit target rests "
                "at Schwab"}  # fmt: skip
    target = {"at": at, "target": "50", "unit": "pct", "credit": "2.45", "close_at": "1.225"}
    assert rows == [
        (0, "stop", "watching", 1, D("4.80"), None, None, False, watching),
        (0, "target", "armed", 1, None, D("1.225"), None, False, target),
        (1, "stop", "watching", 1, D("4.80"), None, None, False, watching),
        (1, "target", "armed", 1, None, D("1.225"), None, False, target),
    ]
    assert exits(factory, "stop")[0].log[0]["stop_calc"]["basis"] == "leg_premium"
    assert len(schwab_orders(deps)) == 1  # the entry, and nothing else


def test_a_rearm_keeps_watching_sized_to_what_is_held(deps, factory):
    schwab_run(deps, factory)
    stop = exits(factory, "stop", 0)[0]
    with bot_exits.EXITS_LOCK:
        assert bot_exits.rearm(deps, stop.id)
    stop = exits(factory, "stop", 0)[0]
    assert (stop.state, stop.rearms, stop.broker_order_id) == ("watching", 1, None)
    assert schwab_orders(deps, "STOP") == []


def test_unreadable_positions_keep_a_stop_watching(deps, factory):
    schwab_run(deps, factory)
    stop = exits(factory, "stop", 0)[0]
    deps.schwab.errors["positions"] = RuntimeError("down")
    with bot_exits.EXITS_LOCK:
        assert bot_exits.rearm(deps, stop.id, first=True)
    assert exits(factory, "stop", 0)[0].state == "watching"


def test_a_watched_stop_whose_position_is_gone_expires(deps, factory):
    schwab_run(deps, factory)
    stop = exits(factory, "stop", 0)[0]
    deps.schwab.held = {}
    with bot_exits.EXITS_LOCK:
        assert not bot_exits.rearm(deps, stop.id)
    assert exits(factory, "stop", 0)[0].state == "expired"


def test_a_target_close_pulls_a_watched_stop_and_a_give_up_puts_it_back(deps, factory):
    deps.schwab.close = "work"
    schwab_run(deps, factory, target_pct="50")
    target = exits(factory, "target", 0)[0]
    bot_exits.fire_target(deps, target.id)
    assert exits(factory, "target", 0)[0].state == "failed"
    stop = exits(factory, "stop", 0)[0]
    assert (stop.state, stop.rearms) == ("watching", 1)
    assert not [c for c in deps.schwab.calls if c[0] == "cancel" and c[1] is None]
    assert schwab_orders(deps, "STOP") == []


def test_a_filled_target_close_leaves_the_watched_stop_cancelled(deps, factory):
    schwab_run(deps, factory, target_pct="50")
    target = exits(factory, "target", 0)[0]
    bot_exits.fire_target(deps, target.id)
    assert exits(factory, "target", 0)[0].state == "filled"
    assert exits(factory, "stop", 0)[0].state == "cancelled"
    assert exits(factory, "stop", 1)[0].state == "watching"


def test_a_time_action_moves_a_watched_stop_without_an_order(deps, factory):
    run_id = schwab_run(deps, factory)
    short = exits(factory, "stop", 0)[0].short_symbol
    deps.hub.set(short, D("0.60"), D("0.70"), OPEN_TIME)
    action = TimeActionDef("dte", 0, "14:00", stop_pct=D("150"))
    state, notes = bot_exits.apply_time_action(deps, run_id, 0, action, 1, False)
    stop = exits(factory, "stop", 0)[0]
    # 2.50 x 2.5 = 6.25, rounded down to the $0.10 tick: 6.20
    assert (state, stop.state, stop.trigger) == ("filled", "watching", D("6.20"))
    assert notes == ["stop 4.80 → 6.20 (watched by the app)"]
    assert len(schwab_orders(deps)) == 1 and not [c for c in deps.schwab.calls if c[0] == "cancel"]


def test_turning_exits_off_and_on_never_sends_for_a_watched_stop(deps, factory):
    run_id = schwab_run(deps, factory)
    short = exits(factory, "stop", 0)[0].short_symbol
    assert bot_manage.exits_off(deps, run_id, 0).ok
    assert exits(factory, "stop", 0)[0].state == "cancelled"
    deps.hub.set(short, D("0.60"), D("0.70"), OPEN_TIME)
    assert bot_manage.exits_on(deps, run_id, 0).ok
    assert exits(factory, "stop", 0)[0].state == "watching"
    assert len(schwab_orders(deps)) == 1 and not [c for c in deps.schwab.calls if c[0] == "cancel"]


def test_changing_a_watched_stop_from_the_run_page(deps, factory):
    run_id = schwab_run(deps, factory)
    short = exits(factory, "stop", 0)[0].short_symbol
    deps.hub.set(short, D("0.60"), D("0.70"), OPEN_TIME)
    assert bot_manage.change_stop(deps, run_id, 0, "150").ok
    stop = exits(factory, "stop", 0)[0]
    assert (stop.state, stop.trigger) == ("watching", D("6.20"))


def test_a_watched_stop_past_expiry_is_expired_quietly(deps, factory):
    schwab_run(deps, factory, target_pct="50")
    deps.seen.clear()
    evening = OPEN_TIME.replace(hour=20, minute=30)  # 16:30 New York, the expiration day
    deps.now = lambda: evening
    bot_exits.monitor_pass(deps)
    assert [s.state for s in exits(factory, "stop")] == ["expired", "expired"]
    assert [t.state for t in exits(factory, "target")] == ["cancelled", "cancelled"]
    assert deps.seen == []


def test_a_watched_stop_is_a_live_exit_for_archive(deps, factory):
    run_id = schwab_run(deps, factory)
    with factory() as session:
        bot_id = session.get(BotRun, run_id).bot_id
    reasons = bot_store.open_trade_reasons(factory, bot_id, OPEN_TIME)
    assert any("exit" in r for r in reasons)


# Task 5: the stop watch


@pytest.fixture(autouse=True)
def _fresh_watch():
    bot_watched_stops.reset()
    yield
    bot_watched_stops.reset()


def breach(deps, factory, short_mid="5.00", age=0, position=0):
    """Fresh quotes on a side: its short at `short_mid` (past a 4.80 stop by default)."""
    stop = exits(factory, "stop", position)[0]
    at_ = deps.now() - timedelta(seconds=age)
    mid = D(short_mid)
    deps.hub.set(stop.short_symbol, mid - D("0.10"), mid + D("0.10"), at_)
    deps.hub.set(stop.long_symbol, D("0.20"), D("0.30"), at_)
    return stop


def closes(factory):
    return [c for c in exits(factory, "close")]


def side_close_orders(deps):
    return [c for c in schwab_orders(deps) if len(c[-1]["orderLegCollection"]) == 2]


def titles(deps):
    return [e.title for e in deps.seen]


def test_two_fresh_passes_past_the_stop_close_the_side(deps, factory):
    schwab_run(deps, factory)
    at(deps, 30)
    breach(deps, factory)
    deps.seen.clear()
    bot_watched_stops.watch_pass(deps)
    assert closes(factory) == []  # one pass isn't enough
    bot_watched_stops.watch_pass(deps)
    [close] = closes(factory)
    assert close.group_position == 0 and close.state == "filled"
    assert close.log[0]["by"] == "stop" and "mid 5.00 ≥ stop 4.80" in close.log[0]["reading"]
    assert exits(factory, "stop", 0)[0].state == "cancelled"
    assert exits(factory, "stop", 1)[0].state == "watching"  # the other side is untouched
    assert len(side_close_orders(deps)) == 1
    assert "IC: stop reached, closing the group" in titles(deps)


def test_a_stale_quote_never_fires_a_watched_stop(deps, factory):
    schwab_run(deps, factory)
    at(deps, 30)
    for _ in range(5):
        breach(deps, factory, short_mid="9.00", age=31)
        bot_watched_stops.watch_pass(deps)
    assert closes(factory) == [] and side_close_orders(deps) == []
    assert exits(factory, "stop", 0)[0].state == "watching"


def test_a_stale_price_alerts_after_a_minute_and_again_when_it_is_back(deps, factory):
    schwab_run(deps, factory)
    start = at(deps, 30)
    breach(deps, factory, age=40)
    deps.seen.clear()
    for seconds in (0, 30, 59):
        deps.now = lambda s=seconds: start + timedelta(seconds=s)
        bot_watched_stops.watch_pass(deps)
    assert not [t for t in titles(deps) if "stale" in t]
    deps.now = lambda: start + timedelta(seconds=61)
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    stale = [e for e in deps.seen if e.title == "IC: stop not watched (stale price)"]
    assert len(stale) == 2  # one per side, once each
    assert "nothing at your broker protects Call side" in stale[0].detail
    deps.seen.clear()
    breach(deps, factory, short_mid="1.00")
    breach(deps, factory, short_mid="1.00", position=1)
    bot_watched_stops.watch_pass(deps)
    assert titles(deps) == ["IC: stop watched again", "IC: stop watched again"]


def test_freeze_sends_nothing_and_says_so_once(deps, factory):
    schwab_run(deps, factory)
    at(deps, 0.5)  # half a minute after the fill: no gap in watching to speak of
    breach(deps, factory)
    orders.set_switch(factory, "freeze", True)
    deps.seen.clear()
    for _ in range(4):
        bot_watched_stops.watch_pass(deps)
    assert closes(factory) == [] and side_close_orders(deps) == []
    assert titles(deps) == ["IC: stop reached, Freeze is on"]
    orders.set_switch(factory, "freeze", False)
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    assert len(closes(factory)) == 1


def test_a_watched_stop_fires_at_most_three_times_a_day(deps, factory):
    schwab_run(deps, factory)
    # Its closes can't run (positions unreadable): each gives up and the stop is back
    deps.schwab.errors["positions"] = RuntimeError("down")
    at(deps, 30)
    for _ in range(3):
        breach(deps, factory)
        bot_watched_stops.watch_pass(deps)
        bot_watched_stops.watch_pass(deps)
        assert exits(factory, "stop", 0)[0].state == "watching"
    assert len(closes(factory)) == 3
    deps.seen.clear()
    breach(deps, factory)
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    assert len(closes(factory)) == 3  # never a fourth
    assert exits(factory, "stop", 0)[0].state == "failed"
    left = next(e for e in deps.seen if e.title == "IC: stop left to you")
    assert "no stop" in left.detail
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    assert len(closes(factory)) == 3


def test_a_gap_in_watching_alerts_once_with_its_times(deps, factory):
    schwab_run(deps, factory)
    at(deps, 30)  # 10:17:30 New York
    bot_watched_stops.watch_pass(deps)
    deps.seen.clear()
    at(deps, 35)
    bot_watched_stops.watch_pass(deps)
    [gap] = [e for e in deps.seen if e.title == "IC: stops weren't watched"]
    assert "from 10:17 to 10:22" in gap.detail and "Check your broker" in gap.detail
    bot_watched_stops.watch_pass(deps)
    assert len([t for t in titles(deps) if "weren't watched" in t]) == 1


def test_startup_alerts_stops_left_unwatched(deps, factory):
    schwab_run(deps, factory)
    at(deps, 30)
    bot_watched_stops.watch_pass(deps)  # written down as the last watch
    bot_watched_stops.reset()  # the app stops: nothing in memory
    deps.seen.clear()
    at(deps, 50)
    bot_watched_stops.recover(deps)
    [gap] = [e for e in deps.seen if e.title == "IC: stops weren't watched"]
    assert "from 10:17 to 10:37" in gap.detail
    bot_watched_stops.watch_pass(deps)
    assert len([t for t in titles(deps) if "weren't watched" in t]) == 1


def test_startup_after_the_close_still_says_the_stops_went_unwatched(deps, factory):
    schwab_run(deps, factory)
    at(deps, 30)
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.reset()
    deps.seen.clear()
    deps.now = lambda: OPEN_TIME.replace(hour=20, minute=30)  # 16:30 New York
    bot_watched_stops.recover(deps)
    [gap] = [e for e in deps.seen if e.title == "IC: stops weren't watched"]
    assert "from 10:17 to 16:00" in gap.detail


def test_nothing_is_watched_outside_the_session(deps, factory):
    schwab_run(deps, factory)
    deps.now = lambda: OPEN_TIME.replace(hour=20, minute=30)
    breach(deps, factory)
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    assert closes(factory) == []


def test_a_tastytrade_stop_is_never_watched_by_the_app(deps, factory):
    from tests.services.bot_fakes import make

    run_id = bot_entry.fire(deps, make(factory, live=True), "09:47")
    with factory() as session:
        assert session.get(BotRun, run_id).broker == "tastytrade"
    assert {s.state for s in exits(factory, "stop")} == {"working"}
    at(deps, 30)
    breach(deps, factory)
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    assert closes(factory) == [] and deps.schwab.calls == []


def test_legs_the_broker_hasnt_shown_yet_still_get_watched_stops(deps, factory):
    """A watched stop sends nothing until it fires, and its close sizes itself to what's
    held then, so it watches from the start rather than leave the side with none."""
    deps.schwab.option_positions = lambda account: {}
    schwab_run(deps, factory, target_pct="50")
    assert [s.state for s in exits(factory, "stop")] == ["watching", "watching"]
    assert {t.state for t in exits(factory, "target")} == {"failed"}
    said = next(e for e in deps.seen if e.title == "IC: profit targets not set")
    assert "watched by the app" in said.detail


def test_a_quote_read_that_breaks_counts_as_no_price(deps, factory):
    schwab_run(deps, factory)
    start = at(deps, 0.5)

    def broken(key):
        raise RuntimeError("hub down")

    deps.hub.latest = broken
    deps.seen.clear()
    for seconds in (0, 61):
        deps.now = lambda s=seconds: start + timedelta(seconds=s)
        bot_watched_stops.watch_pass(deps)
    assert "IC: stop not watched (stale price)" in titles(deps)


def test_a_stop_watched_again_later_is_no_gap(deps, factory):
    """Exits off at 10:17 and back on at 14:00: nothing needed watching in between."""
    run_id = schwab_run(deps, factory)
    with factory() as session:  # only the call side is open
        put = session.get(BotExit, exits(factory, "stop", 1)[0].id)
        put.state = "expired"
        session.commit()
    at(deps, 30)
    bot_watched_stops.watch_pass(deps)
    assert bot_manage.exits_off(deps, run_id, 0).ok
    later = at(deps, 4 * 60 + 12)  # 14:00 New York
    short = exits(factory, "stop", 0)[0].short_symbol
    deps.hub.set(short, D("0.60"), D("0.70"), later)
    assert bot_manage.exits_on(deps, run_id, 0).ok
    deps.seen.clear()
    bot_watched_stops.watch_pass(deps)
    assert not [t for t in titles(deps) if "weren't watched" in t]


def test_a_watched_stops_close_always_ends_buying_the_short_at_market(deps, factory):
    """Controller ruling on review I-3: a watched stop must actually close. Whatever the
    bot's exit-chase ending (here "give up"), a stop's close goes on to the natural price
    and legs out, ending with the short bought at market; putting the stop back never
    counts as a re-arm."""
    deps.schwab.close = "work"  # no limit close fills
    schwab_run(deps, factory, exhaust="give_up")
    at(deps, 0.5)
    breach(deps, factory)
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    [close] = closes(factory)
    assert close.state == "filled" and any(e.get("stage") == "leg_out" for e in close.log)
    market = [c for c in schwab_orders(deps, "MARKET")
              if c[-1]["orderLegCollection"][0]["instruction"] == "BUY_TO_CLOSE"]  # fmt: skip
    assert len(market) == 1
    assert exits(factory, "stop", 0)[0].rearms == 0
    assert not [e for e in deps.seen if "re-placed" in e.detail]


def test_a_stop_close_that_gives_up_puts_the_stop_back_without_a_rearm(deps, factory):
    """A stop's close that can't run (positions unreadable) is no re-arm: it can't use up
    the side's 3 re-arms a day."""
    schwab_run(deps, factory)
    at(deps, 0.5)
    deps.schwab.errors["positions"] = RuntimeError("down")
    for _ in range(2):
        breach(deps, factory)
        bot_watched_stops.watch_pass(deps)
        bot_watched_stops.watch_pass(deps)
    stop = exits(factory, "stop", 0)[0]
    assert stop.state == "watching" and stop.rearms == 0
    assert [c.state for c in closes(factory)] == ["failed", "failed"]


def test_a_watched_stop_past_its_rearm_limit_says_watching_ended(deps, factory):
    """Review M-2: worded for a stop the app watches, not one re-placed at a broker."""
    schwab_run(deps, factory)
    stop = exits(factory, "stop", 0)[0]
    with bot_exits.EXITS_LOCK:
        for _ in range(4):
            bot_exits.rearm(deps, stop.id)
    assert exits(factory, "stop", 0)[0].state == "failed"
    said = next(e for e in deps.seen if e.title == "IC: stop no longer watched")
    assert "3 times today" in said.detail and "no stop" in said.detail
    assert "re-placed" not in said.detail


def test_a_quiet_short_on_a_healthy_stream_is_watched_not_stale(deps, factory):
    """Review M-4: while the stream is healthy (SPX fresh from it) a quiet short's quote
    up to 300 s old counts: no stale alert, and it still fires on two passes."""
    schwab_run(deps, factory)
    start = at(deps, 10)
    deps.seen.clear()
    for seconds in (0, 70):
        deps.now = lambda s=seconds: start + timedelta(seconds=s)
        deps.hub.set_last("SPX", 5800, deps.now() - timedelta(seconds=1))
        breach(deps, factory, short_mid="1.00", age=200)
        breach(deps, factory, short_mid="1.00", age=200, position=1)
        bot_watched_stops.watch_pass(deps)
    assert not [t for t in titles(deps) if "stale" in t]
    breach(deps, factory, age=200)
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    assert len(closes(factory)) == 1


def test_a_stop_reached_on_a_held_back_side_says_so_once(deps, factory, monkeypatch):
    """Review M-6: a held-back side sends nothing (an earlier close may be live), but a
    reached stop there is said, once."""
    monkeypatch.setattr(bot_watched_stops, "_held_said", set())  # said once: not by others
    run_id = schwab_run(deps, factory)
    at(deps, 0.5)
    stop = exits(factory, "stop", 0)[0]
    later = deps.now()
    with factory() as session:  # an earlier close that ended unclear, after the stop
        session.add(BotExit(
            run_id=run_id, group_position=0, kind="close", state="failed",
            short_symbol=stop.short_symbol, long_symbol=stop.long_symbol, contracts=1,
            rearms=0, log=[{"event": "unclear: nothing more sent"}], created_at=later,
            updated_at=later,
        ))  # fmt: skip
        session.commit()
    deps.seen.clear()
    for _ in range(4):
        breach(deps, factory)
        bot_watched_stops.watch_pass(deps)
    assert len(closes(factory)) == 1  # only the earlier one
    held = [e for e in deps.seen if e.title == "IC: stop reached, group held back"]
    assert len(held) == 1 and "Check your broker" in held[0].detail


def test_a_rearm_on_a_ratio_2_anchor_watches_whole_units(deps, factory):
    # Slice 13: a stop row of 3 units on a short sold 2 to a unit. The broker shows 4 of
    # the short: watched again at 2 units, never 4.
    from optionsjourney.trading.orders import plain

    schwab_run(deps, factory)
    stop = exits(factory, "stop", 0)[0]
    short, long = stop.short_symbol, stop.long_symbol
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.legs = [{"symbol": short, "side": "sell", "ratio": 2},
                    {"symbol": long, "side": "buy", "ratio": 1}]  # fmt: skip
        row.anchor_symbol, row.contracts = short, 3
        session.commit()
    deps.schwab.held[plain(short)] = -4
    with bot_exits.EXITS_LOCK:
        assert bot_exits.rearm(deps, stop.id)
    stop = exits(factory, "stop", 0)[0]
    assert (stop.state, stop.contracts) == ("watching", 2)
    assert schwab_orders(deps, "STOP") == []


# Slice 13, Task 11: groups the app watches on their whole P&L, on every broker


def watched_run(deps, factory, key, exhaust="give_up", broker=None, target=None, contracts=None):
    """A live run of a preset shape whose every group is watched, a 50% stop on each
    (50% of the entry price lost); `target` % and `contracts` when given."""
    bot_id = make_shape(factory, key, live=True, exhaust=exhaust, stop=D("50"), target=target)
    if contracts is not None:
        force_contracts(factory, bot_id, contracts)
    if broker is not None:
        with factory() as session:
            session.get(Bot, bot_id).broker = broker
            session.commit()
    return bot_entry.fire(deps, bot_id, "09:47")


def quote_group(deps, stop, mids, ages=None):
    """Each leg of the stop's group at its mid (0.10 either side), `ages` seconds old."""
    legs = bot_exits.exit_legs(stop)
    ages = ages or [0] * len(legs)
    for leg, mid, age in zip(legs, mids, ages, strict=True):
        mid = D(mid)
        deps.hub.set(leg.symbol, mid - D("0.10"), mid + D("0.10"),
                     deps.now() - timedelta(seconds=age))  # fmt: skip


def closing_orders(deps, client):
    return [c[-1] for c in client.calls if c[0] in ("place", "replace")
            and not _opening(c[-1])]  # fmt: skip


def _opening(body):
    legs = body.get("legs") or body.get("orderLegCollection")
    action = legs[0].get("action") or legs[0].get("instruction")
    return action.endswith("Open") or action.endswith("OPEN")


# A long straddle: the 5800 call and put bought at 30.00 each, a 60.00 debit. 50% of
# the entry lost is a P&L of −30.00 a unit; both legs at a 14.00 mid is −32.00.
STRADDLE_PAST = ("14.00", "14.00")


def test_a_long_straddle_stop_is_a_watched_group_at_its_pnl_level(deps, factory):
    watched_run(deps, factory, "long_straddle")
    (stop,) = exits(factory, "stop")
    assert (stop.state, stop.watched, stop.anchor_symbol) == ("watching", True, None)
    assert stop.trigger == D("-30.00")


def test_a_watched_group_never_fires_on_one_stale_leg(deps, factory):
    watched_run(deps, factory, "long_straddle")
    (stop,) = exits(factory, "stop")
    start = at(deps, 30)
    deps.seen.clear()
    for seconds in range(0, 130, 2):
        deps.now = lambda s=seconds: start + timedelta(seconds=s)
        quote_group(deps, stop, STRADDLE_PAST, ages=[0, 31])  # the put's quote 31 s old
        bot_watched_stops.watch_pass(deps)
    assert closes(factory) == [] and closing_orders(deps, deps.tasty) == []
    assert exits(factory, "stop")[0].state == "watching"
    [stale] = [e for e in deps.seen if e.title.endswith("stop not watched (stale price)")]
    put = bot_exits.exit_legs(stop)[1].symbol.strip()
    assert "Straddle" in stale.detail and put in stale.detail
    assert "Schwab" not in stale.detail and "for  " not in stale.detail
    assert "nothing at your broker protects" in stale.detail


def test_two_fresh_passes_close_the_whole_group_then_natural_and_leg_out(deps, factory):
    # A call debit spread: the 5840 call bought at 20.00, the 5915 sold at 1.25: an
    # 18.75 debit; 50% lost is −9.375. The long at 9.00 and the short at 0.50: −10.25.
    deps.tasty.close = "work"  # no limit close fills
    watched_run(deps, factory, "call_debit_spread", exhaust="give_up")
    (stop,) = exits(factory, "stop")
    assert stop.watched and stop.trigger < 0
    legs = bot_exits.exit_legs(stop)
    long, short = (next(leg for leg in legs if leg.side is side) for side in (Side.BUY, Side.SELL))
    mids = ["9.00" if leg is long else "0.50" for leg in legs]
    at(deps, 0.5)
    quote_group(deps, stop, mids)
    deps.seen.clear()
    bot_watched_stops.watch_pass(deps)
    assert closes(factory) == []  # one pass isn't enough
    quote_group(deps, stop, mids)
    bot_watched_stops.watch_pass(deps)
    [close] = closes(factory)
    assert close.log[0]["by"] == "stop" and "P&L −10.25 ≤ stop" in close.log[0]["reading"]
    assert close.state == "filled" and any(e.get("stage") == "leg_out" for e in close.log)
    sent = closing_orders(deps, deps.tasty)
    assert len(sent[0]["legs"]) == 2  # the whole group, as one order first
    market = [b for b in sent if b["order-type"] == "Market"]
    # The sold leg bought back at market first; only then the bought leg is sold
    assert (market[0]["legs"][0]["symbol"], market[0]["legs"][0]["action"]) == (
        short.symbol,
        "Buy to Close",
    )
    assert {w.long_symbol for w in exits(factory, "wing")} == {long.symbol}  # on its own rows
    said = next(e for e in deps.seen if e.title.endswith("stop reached, closing the group"))
    assert "P&L −10.25" in said.detail and "Closing Call spread now." in said.detail


@pytest.mark.parametrize("broker", ["tastytrade", "schwab"])
def test_watched_groups_are_watched_for_tastytrade_too(deps, factory, broker):
    watched_run(deps, factory, "long_straddle", broker=broker)
    (stop,) = exits(factory, "stop")
    assert stop.state == "watching" and stop.watched
    at(deps, 0.5)
    for _ in range(2):
        quote_group(deps, stop, STRADDLE_PAST)
        bot_watched_stops.watch_pass(deps)
    assert {leg.symbol for leg in bot_exits.exit_legs(stop)} | {"SPX"} <= deps.hub.subscribed
    [close] = closes(factory)
    assert close.log[0]["by"] == "stop" and "P&L −32.00 ≤ stop −30.00" in close.log[0]["reading"]
    client = deps.tasty if broker == "tastytrade" else deps.schwab
    assert closing_orders(deps, client)


def test_schwab_single_short_stops_still_read_the_shorts_mid(deps, factory):
    schwab_run(deps, factory)
    assert [s.watched for s in exits(factory, "stop")] == [False, False]
    at(deps, 0.5)
    breach(deps, factory)
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    [close] = closes(factory)
    reading = close.log[0]["reading"]
    assert "mid 5.00 ≥ stop 4.80" in reading and "P&L" not in reading


def test_the_lapse_alert_names_no_broker_for_a_tastytrade_bot(deps, factory):
    watched_run(deps, factory, "long_straddle")
    at(deps, 30)
    bot_watched_stops.watch_pass(deps)
    deps.seen.clear()
    at(deps, 35)
    bot_watched_stops.watch_pass(deps)
    [gap] = [e for e in deps.seen if e.title.endswith("stops weren't watched")]
    assert "nothing at your broker protected Straddle then" in gap.detail
    assert "Schwab" not in gap.detail and "TastyTrade" not in gap.detail


def test_freeze_and_the_three_fires_limit_apply_to_watched_groups(deps, factory):
    watched_run(deps, factory, "long_straddle")
    (stop,) = exits(factory, "stop")
    at(deps, 0.5)
    orders.set_switch(factory, "freeze", True)
    deps.seen.clear()
    for _ in range(4):
        quote_group(deps, stop, STRADDLE_PAST)
        bot_watched_stops.watch_pass(deps)
    assert closes(factory) == [] and closing_orders(deps, deps.tasty) == []
    [frozen] = deps.seen
    assert frozen.title.endswith("stop reached, Freeze is on") and "Straddle" in frozen.detail
    orders.set_switch(factory, "freeze", False)
    # Its closes can't run (positions unreadable): each gives up and the stop is back
    deps.tasty.errors["positions"] = RuntimeError("down")
    for _ in range(3):
        quote_group(deps, stop, STRADDLE_PAST)
        bot_watched_stops.watch_pass(deps)
        bot_watched_stops.watch_pass(deps)
        assert exits(factory, "stop")[0].state == "watching"
    assert len(closes(factory)) == 3
    deps.seen.clear()
    for _ in range(4):
        quote_group(deps, stop, STRADDLE_PAST)
        bot_watched_stops.watch_pass(deps)
    assert len(closes(factory)) == 3  # never a fourth
    assert exits(factory, "stop")[0].state == "failed"
    left = next(e for e in deps.seen if e.title.endswith("stop left to you"))
    assert "Straddle" in left.detail and "on  " not in left.detail and "no stop" in left.detail


def test_a_watched_groups_rewatch_never_uses_up_the_rearm_limit(deps, factory):
    """A watched group's "re-arm" is only watching again: it never trips the resting
    stop's 3 re-arms a day, never leaves the group unwatched (the 3 fires a day without
    a close are its limit)."""
    watched_run(deps, factory, "long_straddle")
    (stop,) = exits(factory, "stop")
    deps.seen.clear()
    with bot_exits.EXITS_LOCK:
        for _ in range(5):
            assert bot_exits.rearm(deps, stop.id)
    stop = exits(factory, "stop")[0]
    assert (stop.state, stop.rearms) == ("watching", 0)
    assert deps.seen == [] and closing_orders(deps, deps.tasty) == []


def test_a_watched_group_is_watched_only_while_this_run_holds_a_leg(deps, factory):
    from optionsjourney.trading.orders import plain

    watched_run(deps, factory, "long_straddle", exhaust="give_up")
    (stop,) = exits(factory, "stop")
    call, put = bot_exits.exit_legs(stop)
    # Every long handed to its own wing row (a leg-out cut off before its close was
    # marked filled): the broker still shows them, but they're the wing rows' to sell.
    with factory() as session:
        for leg in (call, put):
            session.add(BotExit(
                run_id=stop.run_id, group_position=0, kind="wing", state="chasing",
                short_symbol="", long_symbol=leg.symbol, anchor_symbol=None, contracts=1,
                legs=[{"symbol": leg.symbol, "side": "buy", "ratio": 1}], rearms=0, log=[],
                created_at=OPEN_TIME, updated_at=OPEN_TIME,
            ))  # fmt: skip
        session.commit()
    assert deps.tasty.held[plain(call.symbol)] == 1
    with bot_exits.EXITS_LOCK:
        assert not bot_exits.rearm(deps, stop.id, first=True)
    assert exits(factory, "stop")[0].state == "expired"


def test_a_watched_group_with_one_leg_still_held_is_watched_again(deps, factory):
    from optionsjourney.trading.orders import plain

    watched_run(deps, factory, "long_straddle")
    (stop,) = exits(factory, "stop")
    call, _ = bot_exits.exit_legs(stop)
    deps.tasty.held = {plain(call.symbol): 1}
    with bot_exits.EXITS_LOCK:
        assert bot_exits.rearm(deps, stop.id)
    assert exits(factory, "stop")[0].state == "watching"
    deps.tasty.held = {}
    with bot_exits.EXITS_LOCK:
        assert not bot_exits.rearm(deps, stop.id)
    assert exits(factory, "stop")[0].state == "expired"


def test_a_watched_groups_stop_a_close_pulled_is_watched_again_on_any_broker(deps, factory):
    watched_run(deps, factory, "long_straddle")
    (stop,) = exits(factory, "stop")
    with factory() as session:
        session.get(BotExit, stop.id).state = "cancelled"  # pulled by a close
        session.commit()
    with bot_exits.EXITS_LOCK:
        assert bot_exits._settle_old_stop(deps, stop.id, cancel_sent=True) == "back"
    assert exits(factory, "stop")[0].state == "watching"


def test_wings_already_sold_never_count_twice_against_what_the_group_holds(deps, factory):
    """Review I-1: a 2-unit long straddle whose partial close legged out 1 of each long
    to wing rows, both since filled (sold). The broker shows 1 of each still held: the
    group is watched again on a later re-arm and its target stays."""
    from optionsjourney.trading.orders import plain

    watched_run(deps, factory, "long_straddle", target=D("50"), contracts=2)
    (stop,) = exits(factory, "stop")
    assert stop.contracts == 2
    call, put = bot_exits.exit_legs(stop)
    with factory() as session:
        for leg in (call, put):
            session.add(BotExit(
                run_id=stop.run_id, group_position=0, kind="wing", state="filled",
                short_symbol="", long_symbol=leg.symbol, anchor_symbol=None, contracts=1,
                legs=[{"symbol": leg.symbol, "side": "buy", "ratio": 1}], rearms=0, log=[],
                created_at=OPEN_TIME, updated_at=OPEN_TIME,
            ))  # fmt: skip
        session.commit()
    deps.tasty.held = {plain(call.symbol): 1, plain(put.symbol): 1}
    with bot_exits.EXITS_LOCK:
        assert bot_exits.rearm(deps, stop.id)
    stop = exits(factory, "stop")[0]
    assert (stop.state, stop.contracts) == ("watching", 1)
    # decision 0048: its target rests at TastyTrade
    assert [t.state for t in exits(factory, "target")] == ["working"]


@pytest.mark.parametrize(
    "key", ["call_debit_spread", "long_call", "iron_butterfly", "long_call_butterfly"]
)
def test_every_watched_matrix_shape_closes_on_its_stop_then_natural_then_leg_out(
    deps, factory, key
):
    # No limit close fills, so the group goes whole first (never at market), then each
    # sold leg is bought back, then each bought leg is sold on its own wing row.
    deps.tasty.close = "work"
    watched_run(deps, factory, key, exhaust="natural_then_leg_out")
    with factory() as session:
        bot_id = session.scalar(select(Bot.id))
    loaded = bot_store.load(factory, bot_id)
    chase = replace(loaded.exit_chase, natural_minutes=1)
    assert bot_store.save(factory, replace(loaded, exit_chase=chase), OPEN_TIME)[1] == {}
    (stop,) = exits(factory, "stop")
    assert stop.watched and stop.state == "watching" and stop.trigger < 0
    legs = bot_exits.exit_legs(stop)
    # Sold legs far dearer than bought ones for the credit fly; every leg cheap otherwise
    # (a debit group worth nothing has lost all it cost).
    loss = {leg.symbol: "30.00" if key == "iron_butterfly" and leg.side is Side.SELL else "0.15"
            for leg in legs}  # fmt: skip
    at(deps, 0.5)
    for _ in range(2):
        quote_group(deps, stop, [loss[leg.symbol] for leg in legs])
        bot_watched_stops.watch_pass(deps)
    [close] = closes(factory)
    assert close.log[0]["by"] == "stop" and close.state == "filled"
    sent = closing_orders(deps, deps.tasty)
    combos = [b for b in sent if len(b["legs"]) > 1]
    if len(legs) > 1:
        assert combos and all(b["order-type"] == "Limit" for b in combos)
    else:
        assert not combos
    singles = [b for b in sent if len(b["legs"]) == 1]
    bought_back = {b["legs"][0]["symbol"] for b in singles if b["order-type"] == "Market"
                   and b["legs"][0]["action"] == "Buy to Close"}  # fmt: skip
    assert bought_back == {leg.symbol for leg in legs if leg.side is Side.SELL}
    sold = {w.long_symbol for w in exits(factory, "wing")}
    assert sold == {leg.symbol for leg in legs if leg.side is Side.BUY}
    assert any(e.get("stage") == "leg_out" for e in close.log)


def test_a_short_strangle_stop_fires_once_its_far_short_has_no_bid(deps, factory):
    """Final review C1: SPX rallies; the short put goes to 0 bid / 0.05 ask (a routine
    far-OTM 0DTE quote) and the short call far past the level. Tracker2's leg_mid
    values the put at 0.025, so the stop fires on two fresh passes."""
    deps.tasty.close = "work"
    watched_run(deps, factory, "short_strangle")
    (stop,) = exits(factory, "stop")
    assert stop.watched and stop.anchor_symbol is None and stop.trigger < 0
    legs = bot_exits.exit_legs(stop)
    call = next(leg for leg in legs if leg.symbol.strip()[12] == "C")
    put = next(leg for leg in legs if leg.symbol.strip()[12] == "P")
    at(deps, 0.5)
    deps.seen.clear()
    for _ in range(2):
        deps.hub.set(call.symbol, D("20.00"), D("20.40"), deps.now())
        deps.hub.set(put.symbol, D("0"), D("0.05"), deps.now())
        bot_watched_stops.watch_pass(deps)
    [close] = closes(factory)
    assert close.log[0]["by"] == "stop" and "≤ stop" in close.log[0]["reading"]
    assert not [e for e in deps.seen if "stale price" in e.title]


def test_a_short_strangle_with_a_sold_leg_at_0_0_still_never_fires(deps, factory):
    watched_run(deps, factory, "short_strangle")
    (stop,) = exits(factory, "stop")
    legs = bot_exits.exit_legs(stop)
    call = next(leg for leg in legs if leg.symbol.strip()[12] == "C")
    put = next(leg for leg in legs if leg.symbol.strip()[12] == "P")
    at(deps, 0.5)
    for _ in range(3):
        deps.hub.set(call.symbol, D("20.00"), D("20.40"), deps.now())
        deps.hub.set(put.symbol, D("0"), D("0"), deps.now())
        bot_watched_stops.watch_pass(deps)
    assert closes(factory) == []
    assert exits(factory, "stop")[0].state == "watching"


def test_an_xsp_stop_reads_the_streams_health_off_spx(deps, factory):
    """Bots slice 16 (Tracker2's dx_symbol): XSP's quiet short counts while SPX is fresh
    from the stream, whatever XSP's own index print says."""
    run_id = schwab_run(deps, factory)
    with factory() as session:
        session.get(Bot, session.get(BotRun, run_id).bot_id).underlying = "XSP"
        session.commit()
    start = at(deps, 10)
    deps.seen.clear()
    for seconds in (0, 70):
        deps.now = lambda s=seconds: start + timedelta(seconds=s)
        deps.hub.set_last("SPX", 5800, deps.now() - timedelta(seconds=1))
        deps.hub.set_last("XSP", 580, start - timedelta(minutes=10))
        breach(deps, factory, short_mid="1.00", age=200)
        breach(deps, factory, short_mid="1.00", age=200, position=1)
        bot_watched_stops.watch_pass(deps)
    assert not [t for t in titles(deps) if "stale" in t]
    assert "SPX" in deps.hub.subscribed


def test_a_tastytrade_stop_the_app_holds_fires_on_its_shorts_quote(deps, factory):
    """Decision 0048: a group whose target rests has its stop watched by the app on the
    short's quote, the way a Schwab bot's is."""
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"))
    bot_entry.fire(deps, bot_id, "09:47")
    (stop,) = exits(factory, "stop")
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.state, row.broker_order_id, row.app_held = bot_exits.WATCHING, None, True
        session.commit()
    at(deps, 30)
    breach(deps, factory, short_mid=str(stop.trigger + D("0.50")))
    bot_watched_stops.watch_pass(deps)
    bot_watched_stops.watch_pass(deps)
    [close] = closes(factory)
    assert close.log[0]["by"] == "stop" and close.app_held
    assert exits(factory, "stop")[0].state == "cancelled"


def test_a_held_stop_is_worded_by_its_reason_and_a_new_reason_is_said(deps, factory, monkeypatch):
    """Final review minor: a reached stop refused only because the group's exits are still
    being placed says so (not "an earlier close may still be working"); a later, different
    hold on the same stop is said too."""
    monkeypatch.setattr(bot_watched_stops, "_held_said", set())
    run_id = schwab_run(deps, factory)
    at(deps, 0.5)
    stop = exits(factory, "stop", 0)[0]
    later = deps.now()
    with factory() as session:  # an exit of the group still being placed
        placing = BotExit(
            run_id=run_id, group_position=0, kind="target", state="placing",
            short_symbol=stop.short_symbol, long_symbol=stop.long_symbol, contracts=1,
            rearms=0, log=[], created_at=later, updated_at=later,
        )  # fmt: skip
        session.add(placing)
        session.commit()
        placing_id = placing.id
    deps.seen.clear()
    for _ in range(3):
        breach(deps, factory)
        bot_watched_stops.watch_pass(deps)
    held = [e for e in deps.seen if e.title == "IC: stop reached, group held back"]
    assert len(held) == 1 and "still being placed" in held[0].detail
    assert "earlier close" not in held[0].detail
    with factory() as session:  # then a close that ended unclear holds it
        session.get(BotExit, placing_id).state = "failed"
        session.add(BotExit(
            run_id=run_id, group_position=0, kind="close", state="failed",
            short_symbol=stop.short_symbol, long_symbol=stop.long_symbol, contracts=1,
            rearms=0, log=[{"event": "unclear: nothing more sent"}], created_at=later,
            updated_at=later,
        ))  # fmt: skip
        session.commit()
    for _ in range(3):
        breach(deps, factory)
        bot_watched_stops.watch_pass(deps)
    held = [e for e in deps.seen if e.title == "IC: stop reached, group held back"]
    assert len(held) == 2 and "an earlier close" in held[1].detail
