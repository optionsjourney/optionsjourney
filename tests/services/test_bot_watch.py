"""Slice 7: watch rules and profit actions, with the fake broker (Tracker2's
watch_loop.py, scale_out.py, time_actions.py)."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import ProfitActionDef, TimeActionDef
from optionsjourney.core import sessions
from optionsjourney.db.models import BotExit, BotRun, BotTradeOverride
from optionsjourney.services import bot_entry, bot_exits, bot_store, bot_timed, bot_watch, orders
from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make

D = Decimal
DAY = date(2026, 10, 13)


def ny(hhmm, day=DAY, seconds=0):
    """A New York wall-clock time on the test day, as the UTC moment (EDT: UTC-4)."""
    hours, minutes = (int(x) for x in hhmm.split(":"))
    start = datetime(day.year, day.month, day.day, hours + 4, minutes, tzinfo=UTC)
    return start + timedelta(seconds=seconds)


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
    bot_watch.reset()
    yield d
    bot_exits.FIRING.clear()
    bot_watch.reset()
    sessions.use_fetched_calendar([], {})


def trade(deps, factory, target_pct="80", contracts=1, groups=None, **changes):
    """A live iron condor bot's filled trade (09:47): short call 5910 / long 5985 and
    short put 5690 / long 5615, each short filled at 2.50 and each wing at 0.05 (a
    2.45 credit a side), a stop at 4.80 on each short; the run's id."""
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    sides = groups or tuple(
        replace(g, target_pct=D(target_pct) if target_pct else None) for g in bot.groups
    )
    _, problems = bot_store.save(factory, replace(bot, groups=sides, **changes), OPEN_TIME)
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


def legs_of(factory, run_id):
    with factory() as session:
        return list(session.get(BotRun, run_id).legs)


def at(deps, hhmm, seconds=0):
    deps.now = lambda: ny(hhmm, seconds=seconds)


def quotes(deps, factory, run_id, when, mids=None, deltas=None, spot=None):
    """Fresh quotes (bid and ask 0.05 either side of each mid) on every leg at `when`,
    raw deltas if given, and SPX's last trade if given. `mids`/`deltas` map the
    strike to a number; default mids: shorts 0.60, wings 0.05."""
    for leg in legs_of(factory, run_id):
        default = "0.60" if leg["side"] == "sell" else "0.05"
        m = D((mids or {}).get(leg["strike"], default))
        deps.hub.set(leg["symbol"], max(m - D("0.05"), D(0)), m + D("0.05"), when)
        if deltas and leg["strike"] in deltas:
            deps.hub.set_delta(leg["symbol"], deltas[leg["strike"]], when)
    if spot is not None:
        deps.hub.set_last("SPX", spot, when)


def titles(deps):
    return [e.title for e in deps.seen]


def details(deps):
    return [e.detail for e in deps.seen]


def placed(deps, kind=None):
    return [c for c in deps.tasty.calls if c[0] == "place"
            and (kind is None or c[2]["order-type"] == kind)]  # fmt: skip


def side_closes(deps):
    return [c[2] for c in placed(deps) if len(c[2]["legs"]) == 2
            and c[2]["legs"][0]["action"].endswith("Close")]  # fmt: skip


def stops_sent(deps):
    return [c[2] for c in placed(deps, "Stop")]


def pass_at(deps, factory, run_id, hhmm, seconds=0, **quoting):
    """One watch pass at `hhmm:seconds`, with fresh quotes then."""
    at(deps, hhmm, seconds)
    quotes(deps, factory, run_id, ny(hhmm, seconds=seconds), **quoting)
    bot_watch.watch_pass(deps)


# --- Task 4: closes started by a rule -------------------------------------------------


def test_a_close_by_a_rule_says_so_in_its_alerts(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    at(deps, "11:00")
    quotes(deps, factory, run_id, ny("11:00"))
    deps.tasty.close = "work"  # the close never fills: it gives up
    assert bot_exits.fire_close(deps, run_id, 0, 3, 3, by="S/L ratio exit") is not None
    [close] = rows(factory, "close")
    assert close.log[0]["by"] == "S/L ratio exit"
    assert "a close of 3 for the S/L ratio exit" in close.log[0]["event"]
    assert any("The close for the S/L ratio exit on" in d for d in details(deps))
    assert not any("you asked for" in d for d in details(deps))


def test_then_stop_moves_the_stop_for_what_is_left(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    at(deps, "11:00")
    quotes(deps, factory, run_id, ny("11:00"))
    then = {"pct": "0", "by": "profit action 1"}
    assert bot_exits.fire_close(deps, run_id, 0, 1, 3, by="profit action 1", then_stop=then)
    [close] = rows(factory, "close")
    assert close.state == "reduced"
    stop = rows(factory, "stop", 0)[0]
    # Break-even: the side's 2.45 credit, rounded down to the 0.05 tick
    assert (stop.state, stop.contracts, stop.trigger) == ("working", 2, D("2.45"))
    sent = stops_sent(deps)[-1]
    assert D(str(sent["stop-trigger"])) == D("2.45") and sent["legs"][0]["quantity"] == 2
    assert any(e.get("stop_calc", {}).get("moved_by") == "profit action 1" for e in stop.log)
    assert any("the stop moved 4.80 → 2.45 (profit action 1)" in d.lower() for d in details(deps))


def test_a_profit_close_that_does_not_fill_keeps_the_old_stop(deps, factory):
    run_id = trade(deps, factory, contracts=3)
    at(deps, "11:00")
    quotes(deps, factory, run_id, ny("11:00"))
    deps.tasty.close = "work"
    then = {"pct": "0", "by": "profit action 1"}
    bot_exits.fire_close(deps, run_id, 0, 1, 3, by="profit action 1", then_stop=then)
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.contracts, stop.trigger) == ("working", 3, D("4.80"))
    assert any("the stop wasn't moved, as the close didn't fill" in d for d in details(deps))


def test_a_time_action_with_a_close_closes_part_after_its_moves(deps, factory):
    action = TimeActionDef("dte", 0, "14:00", target_pct=D("25"), close_pct=50)
    run_id = trade(deps, factory, contracts=4, use_time_actions=True, time_actions=(action,))
    at(deps, "14:00")
    quotes(deps, factory, run_id, ny("14:00"))
    bot_timed.actions_pass(deps)
    closes = rows(factory, "close")
    assert [(c.group_position, c.state, c.contracts) for c in closes] == [
        (0, "reduced", 2), (1, "reduced", 2),
    ]  # fmt: skip
    assert all(c.log[0]["by"] == "time action 1" for c in closes)
    for side in (0, 1):
        target = [t for t in rows(factory, "target", side) if t.state == "armed"]
        assert [(t.contracts, t.limit) for t in target] == [(2, D("1.8375"))]
        assert rows(factory, "stop", side)[0].contracts == 2
    actions = rows(factory, "action")
    assert all(a.state == "filled" for a in actions)
    assert all("closing 2 of 4" in a.log[0]["notes"] for a in actions)
    # Done once: a later pass closes nothing more
    bot_timed.actions_pass(deps)
    assert len(rows(factory, "close")) == 2


def test_a_time_action_close_that_would_leave_nothing_closes_nothing(deps, factory):
    action = TimeActionDef("dte", 0, "14:00", close_pct=50)
    run_id = trade(deps, factory, use_time_actions=True, time_actions=(action,))
    at(deps, "14:00")
    quotes(deps, factory, run_id, ny("14:00"))
    bot_timed.actions_pass(deps)
    assert rows(factory, "close") == []
    assert all(a.state == "filled" and "nothing closed" in a.log[0]["notes"][0]
               for a in rows(factory, "action"))  # fmt: skip


# --- Task 5: the watch pass -------------------------------------------------------------


def turn_off_in_db(factory, run_id, side, tasty=None):
    """Exits off, as bot_manage leaves a side (slice 6): the stop and target cancelled
    (the stop's order at the fake broker too, when `tasty` is given)."""
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=side, exits_off=True,
                                     changed_at=OPEN_TIME, log=[]))  # fmt: skip
        for row in session.scalars(
            select(BotExit).where(BotExit.run_id == run_id, BotExit.group_position == side)
        ):
            if row.state in ("working", "pending", "armed"):
                if tasty is not None and row.kind == "stop" and row.broker_order_id:
                    tasty.cancel_order("5WX01234", row.broker_order_id)
                row.state = "cancelled"
                row.log = [*row.log, {"event": bot_exits.EXITS_OFF}]
        session.commit()


RATIO = {"use_sl_ratio_exit": True, "sl_ratio_above": D("10")}  # shorts 1.20 / wings 0.10 = 12


def test_a_confirmed_ratio_rule_closes_every_open_side_once_exits_off_too(deps, factory):
    """Bots slice 18, the author's ruling (Tracker2's watch_rules read no
    stop_loss_off/profit_target_off): a side with exits off is closed like any other."""
    run_id = trade(deps, factory, **RATIO)
    turn_off_in_db(factory, run_id, 1, deps.tasty)
    pass_at(deps, factory, run_id, "11:00", 0)
    assert rows(factory, "close") == []  # one pass isn't enough
    pass_at(deps, factory, run_id, "11:00", 30)  # #201: held 30 s
    closes = rows(factory, "close")
    assert [(c.group_position, c.state) for c in closes] == [(0, "filled"), (1, "filled")]
    assert closes[0].log[0]["by"] == "S/L ratio exit"
    fired = rows(factory, "watch")
    assert [(f.group_position, f.state, f.log[0]["rule"]) for f in fired] == [
        (0, "filled", "S/L ratio exit"),
        (1, "filled", "S/L ratio exit"),
    ]
    assert "S/L ratio 12.000 > 10" in fired[0].log[0]["detail"]
    alert = next(e for e in deps.seen if e.title == "IC: S/L ratio exit")
    assert "closing the trade (Call side, Put side)" in alert.detail
    for seconds in (32, 34, 62, 64):
        pass_at(deps, factory, run_id, "11:00", seconds)
    assert len(rows(factory, "close")) == 2 and len(rows(factory, "watch")) == 2


def test_a_trade_with_every_side_off_is_still_closed_by_its_rule(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    turn_off_in_db(factory, run_id, 0, deps.tasty)
    turn_off_in_db(factory, run_id, 1, deps.tasty)
    for seconds in (0, 2, 30, 32):
        pass_at(deps, factory, run_id, "11:00", seconds)
    assert [c.group_position for c in rows(factory, "close")] == [0, 1]
    assert "IC: S/L ratio exit: nothing closed" not in titles(deps)


def test_one_pass_is_not_enough_and_a_break_starts_the_count_again(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    calm = {"5985": "0.30", "5615": "0.30"}  # wings dearer: 1.20 / 0.60 = 2
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2, mids=calm)
    pass_at(deps, factory, run_id, "11:00", 4)
    pass_at(deps, factory, run_id, "11:00", 6)
    assert rows(factory, "close") == []
    pass_at(deps, factory, run_id, "11:00", 34)  # #201: 30 s from the break
    assert len(rows(factory, "close")) == 2  # both sides


def test_stale_data_never_fires_and_alerts_once_then_resumed(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    stops_before = [s.state for s in rows(factory, "stop")]
    quotes(deps, factory, run_id, ny("10:58"))  # breached, but two minutes old
    for seconds in (0, 2, 30, 60, 62, 90):
        at(deps, "11:00", seconds)
        bot_watch.watch_pass(deps)
    assert rows(factory, "close") == [] and side_closes(deps) == []
    assert [s.state for s in rows(factory, "stop")] == stops_before  # the stops stand
    assert titles(deps).count("IC: watch rules paused") == 1
    paused = next(e for e in deps.seen if e.title == "IC: watch rules paused")
    assert "S/L ratio" in paused.detail
    # #203: the alert names the stops still resting at the broker
    assert "Your stops at TastyTrade still work: Call side 4.80, Put side 4.80." in paused.detail
    calm = {"5985": "0.30", "5615": "0.30"}
    pass_at(deps, factory, run_id, "11:01", 40, mids=calm)
    assert titles(deps).count("IC: watch rules resumed") == 1
    assert rows(factory, "close") == []


def test_a_missing_delta_never_fires(deps, factory):
    run_id = trade(deps, factory, use_delta_exit=True, position_delta_above=D("-100"))
    for seconds in (0, 2, 4):
        pass_at(deps, factory, run_id, "11:00", seconds)  # quotes, but no Greeks
    assert rows(factory, "close") == []
    deltas = {"5910": "0.20", "5985": "0.05", "5690": "-0.20", "5615": "-0.05"}
    pass_at(deps, factory, run_id, "11:00", 6, deltas=deltas)
    pass_at(deps, factory, run_id, "11:00", 36, deltas=deltas)  # #201: held 30 s
    assert len(rows(factory, "close")) == 2
    assert rows(factory, "watch")[0].log[0]["rule"] == "Delta exit"


def test_a_fired_trade_never_fires_again(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    deps.tasty.close = "work"  # the closes give up: both sides stay open
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 30)  # #201: held 30 s
    assert [c.state for c in rows(factory, "close")] == ["failed", "failed"]
    for seconds in (32, 34, 64):
        pass_at(deps, factory, run_id, "11:00", seconds)
    assert len(rows(factory, "close")) == 2  # a failed watch close is left to you
    assert all(s.state == "working" for s in rows(factory, "stop"))


def test_a_short_tested_rule_closes_the_trade(deps, factory):
    run_id = trade(deps, factory, use_underlying_exit=True, short_put_tested_pts=D("10"))
    pass_at(deps, factory, run_id, "11:00", 0, spot="5710")
    pass_at(deps, factory, run_id, "11:00", 2, spot="5710")
    assert rows(factory, "close") == []  # 20 points away
    pass_at(deps, factory, run_id, "11:00", 4, spot="5699.50")
    pass_at(deps, factory, run_id, "11:00", 6, spot="5699.50")
    assert len(rows(factory, "close")) == 2
    assert "short put 5690 is +9.5 pts" in rows(factory, "watch")[0].log[0]["detail"]
    assert "IC: Short put tested" in titles(deps)


def test_an_underlying_move_rule_closes_the_trade(deps, factory):
    run_id = trade(deps, factory, use_underlying_exit=True, underlying_down_pct=D("1"))
    pass_at(deps, factory, run_id, "11:00", 0, spot="5742")  # exactly -1%
    pass_at(deps, factory, run_id, "11:00", 2, spot="5742")
    assert len(rows(factory, "close")) == 2
    assert "-1.00% from the entry 5800.00" in rows(factory, "watch")[0].log[0]["detail"]


def test_an_old_spx_price_never_fires(deps, factory):
    run_id = trade(deps, factory, use_underlying_exit=True, underlying_down_pct=D("1"))
    deps.hub.set_last("SPX", "5700", ny("10:59"))
    for seconds in (0, 2, 4):
        pass_at(deps, factory, run_id, "11:00", seconds)
    assert rows(factory, "close") == []


def test_a_trade_with_no_entry_spot_alerts_once(deps, factory):
    run_id = trade(deps, factory, use_underlying_exit=True, underlying_down_pct=D("1"))
    with factory() as session:
        session.get(BotRun, run_id).entry_spot = None
        session.commit()
    for seconds in (0, 2, 4):
        pass_at(deps, factory, run_id, "11:00", seconds, spot="5600")
    assert rows(factory, "close") == []
    assert titles(deps).count("IC: underlying move rule off for a trade") == 1


def test_nothing_is_watched_outside_the_session_or_under_freeze(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    pass_at(deps, factory, run_id, "16:30", 0)
    pass_at(deps, factory, run_id, "16:30", 2)
    orders.set_switch(factory, "freeze", True)
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    assert rows(factory, "close") == [] and deps.hub.subscribed.isdisjoint({"SPX"})


def test_a_bot_with_no_rules_reads_nothing(deps, factory):
    run_id = trade(deps, factory)
    deps.hub.subscribed.clear()
    pass_at(deps, factory, run_id, "11:00", 0)
    assert deps.hub.subscribed == set() and rows(factory, "watch") == []


# --- Task 6: profit actions ---------------------------------------------------------------


def with_actions(factory, *actions, side=0, target_pct="80"):
    """The test bot's sides, `side` with these profit actions."""
    bot = bot_store.load(factory, make(factory))
    return tuple(
        replace(g, target_pct=D(target_pct),
                use_profit_actions=g.position == side and bool(actions),
                profit_actions=actions if g.position == side else ())
        for g in bot.groups
    )  # fmt: skip


A = ProfitActionDef


def test_a_stop_only_profit_action_moves_the_stop_after_the_live_check_and_never_loosens_it(
    deps, factory
):
    # Call side: 2.45 credit, stop 4.80. At 50% (P/L 1.225), stop to break-even (2.45).
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    old = rows(factory, "stop", 0)[0]
    pass_at(deps, factory, run_id, "11:00", 0)
    assert rows(factory, "profit") == []
    pass_at(deps, factory, run_id, "11:00", 2)  # P/L 2.45 - 0.55 = 1.90
    [profit] = rows(factory, "profit")
    assert (profit.group_position, profit.state) == (0, "filled")
    assert "stop 4.80 → 2.45" in profit.log[0]["notes"][0]
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.trigger) == ("working", D("2.45"))
    assert stop.broker_order_id != old.broker_order_id
    assert ("cancel", old.broker_order_id) in [c[:2] for c in deps.tasty.calls]
    assert any(e.get("event") == bot_exits.MOVING_STOP_PROFIT for e in stop.log)
    assert rows(factory, "stop", 1)[0].trigger == D("4.80")  # the other side untouched
    assert "IC: profit action 1" in titles(deps)
    # Done once
    pass_at(deps, factory, run_id, "11:00", 4)
    pass_at(deps, factory, run_id, "11:00", 6)
    assert len(rows(factory, "profit")) == 1


def test_a_profit_action_never_loosens_the_stop(deps, factory):
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    stop = rows(factory, "stop", 0)[0]
    with factory() as session:  # as if you'd tightened it to 1.00 yourself
        session.get(BotExit, stop.id).trigger = D("1.00")
        session.commit()
    cancels = [c for c in deps.tasty.calls if c[0] == "cancel"]
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    [profit] = rows(factory, "profit")
    assert profit.state == "failed" and "already at 1.00" in profit.log[0]["notes"][0]
    assert rows(factory, "stop", 0)[0].trigger == D("1.00")
    assert [c for c in deps.tasty.calls if c[0] == "cancel"] == cancels  # nothing cancelled


def test_a_profit_stop_that_would_fire_at_once_is_not_moved(deps, factory):
    # At 50% keep 49%: 2.45 x 0.51 = 1.2495, rounded down to 1.20; the short's ask 1.30
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("-49"))))
    mids = {"5910": "1.25"}  # P/L 2.45 - 1.20 = 1.25, past 1.225
    pass_at(deps, factory, run_id, "11:00", 0, mids=mids)
    pass_at(deps, factory, run_id, "11:00", 2, mids=mids)
    [profit] = rows(factory, "profit")
    assert profit.state == "failed" and "would fire at once" in profit.log[0]["notes"][0]
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.trigger) == ("working", D("4.80"))


def test_a_profit_stop_with_no_fresh_ask_waits(deps, factory):
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    short = rows(factory, "stop", 0)[0].short_symbol
    pass_at(deps, factory, run_id, "11:00", 0)
    at(deps, "11:00", 2)
    quotes(deps, factory, run_id, ny("11:00", seconds=2))
    real = bot_exits._fresh_ask
    bot_exits._fresh_ask = lambda d, symbol: None if symbol == short else real(d, symbol)
    try:
        bot_watch.watch_pass(deps)
    finally:
        bot_exits._fresh_ask = real
    assert rows(factory, "profit") == []  # nothing recorded: tried again
    assert "IC: profit action 1 waiting for a price" in titles(deps)
    pass_at(deps, factory, run_id, "11:00", 4)
    pass_at(deps, factory, run_id, "11:00", 6)
    assert [p.state for p in rows(factory, "profit")] == ["filled"]


def test_a_close_part_profit_action_closes_one_of_three_and_moves_the_stop_for_two(deps, factory):
    run_id = trade(deps, factory, contracts=3,
                   groups=with_actions(factory, A(D("50"), 33, D("0"))))  # fmt: skip
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    [close] = rows(factory, "close")
    assert (close.group_position, close.state, close.contracts) == (0, "reduced", 1)
    assert close.log[0]["by"] == "profit action 1"
    [profit] = rows(factory, "profit")
    assert profit.state == "filled" and "closing 1 of 3" in profit.log[0]["notes"][0]
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.contracts, stop.trigger) == ("working", 2, D("2.45"))
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 2
    target = [t for t in rows(factory, "target", 0) if t.state == "armed"]
    assert [t.contracts for t in target] == [2]
    assert any("The stop moved 4.80 → 2.45 (profit action 1)." in d for d in details(deps))


def test_an_action_that_would_leave_nothing_is_skipped_once(deps, factory):
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 50, None)))
    for seconds in (0, 2, 4, 6):
        pass_at(deps, factory, run_id, "11:00", seconds)
    [profit] = rows(factory, "profit")
    assert profit.state == "failed" and "leave nothing" in profit.log[0]["notes"][0]
    assert rows(factory, "close") == []
    assert titles(deps).count("IC: profit action 1 skipped") == 1


def test_actions_fire_in_order_one_at_a_time(deps, factory):
    actions = (A(D("25"), 0, D("0")), A(D("50"), 0, D("-20")))
    run_id = trade(deps, factory, groups=with_actions(factory, *actions))
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    assert [p.log[0]["number"] for p in rows(factory, "profit")] == [1]
    assert rows(factory, "stop", 0)[0].trigger == D("2.45")
    pass_at(deps, factory, run_id, "11:00", 4)
    pass_at(deps, factory, run_id, "11:00", 6)
    assert [p.log[0]["number"] for p in rows(factory, "profit")] == [1, 2]
    assert rows(factory, "stop", 0)[0].trigger == D("1.95")  # 2.45 x 0.80 = 1.96, rounded down


def test_a_profit_actions_stop_move_turns_a_switched_off_stop_back_on(deps, factory):
    """Tracker2's record_profit_action_fill: "the action sets a stop, even over a removed
    one". The stop is placed at the action's level, after the live check."""
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    turn_off_in_db(factory, run_id, 0, deps.tasty)
    for seconds in (0, 2, 4):
        pass_at(deps, factory, run_id, "11:00", seconds)
    [profit] = rows(factory, "profit")
    assert profit.state == "filled" and "the stop is back on" in profit.log[0]["notes"][0]
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.trigger) == ("working", D("2.45"))
    with factory() as session:
        saved = session.scalar(select(BotTradeOverride).where(
            BotTradeOverride.run_id == run_id, BotTradeOverride.group_position == 0))  # fmt: skip
        assert (saved.stop_off, saved.target_off) == (False, True)


def test_a_profit_actions_stop_move_that_would_fire_leaves_the_stop_off(deps, factory, monkeypatch):
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    turn_off_in_db(factory, run_id, 0, deps.tasty)
    short = rows(factory, "stop", 0)[0].short_symbol
    real = bot_exits._fresh_ask

    def ask(d, symbol):
        return D("2.60") if symbol == short else real(d, symbol)

    monkeypatch.setattr(bot_exits, "_fresh_ask", ask)
    sent = len([c for c in deps.tasty.calls if c[0] == "place"])
    for seconds in (0, 2, 4):
        pass_at(deps, factory, run_id, "11:00", seconds)
    [profit] = rows(factory, "profit")
    assert profit.state == "failed" and "the stop stays off" in profit.log[0]["notes"][0]
    assert rows(factory, "stop", 0)[0].state == "cancelled"
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == sent
    with factory() as session:
        saved = session.scalar(select(BotTradeOverride).where(
            BotTradeOverride.run_id == run_id, BotTradeOverride.group_position == 0))  # fmt: skip
        assert saved.stop_off is True


def test_profit_actions_wait_on_stale_quotes(deps, factory):
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    quotes(deps, factory, run_id, ny("10:58"))
    for seconds in (0, 2, 4, 70):
        at(deps, "11:00", seconds)
        bot_watch.watch_pass(deps)
    assert rows(factory, "profit") == []
    paused = next(e for e in deps.seen if e.title == "IC: watch rules paused")
    assert "Call side profit action 1" in paused.detail


def test_watch_pass_never_raises(deps, factory, monkeypatch):
    run_id = trade(deps, factory, **RATIO)

    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_watch, "_watch", broken)
    pass_at(deps, factory, run_id, "11:00", 0)
    monkeypatch.setattr(bot_watch, "_trades", broken)
    pass_at(deps, factory, run_id, "11:00", 2)


# --- Slice 6's hold-back (after the rebase onto its review fixes) ----------------------


def hold_back(factory, run_id, side):
    """A side whose earlier close may still be live (slice 6's held_back: its stop
    cancelled for a target close that never settled)."""
    with factory() as session:
        stop = session.scalar(
            select(BotExit).where(
                BotExit.run_id == run_id, BotExit.kind == "stop", BotExit.group_position == side
            )
        )
        stop.state = "cancelled"
        stop.log = [*stop.log, {"event": bot_exits.CANCELLED_FOR_TARGET}]
        session.commit()
    with factory() as session:
        rows_ = list(
            session.scalars(
                select(BotExit).where(BotExit.run_id == run_id, BotExit.group_position == side)
            )
        )
    assert bot_exits.held_back(rows_)


def test_a_watch_rule_never_closes_a_held_back_side(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    hold_back(factory, run_id, 0)
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 30)  # #201: held 30 s
    assert [c.group_position for c in rows(factory, "close")] == [1]
    assert [w.group_position for w in rows(factory, "watch")] == [1]
    alert = next(e for e in deps.seen if e.title == "IC: S/L ratio exit")
    assert "Call side: an earlier close or stop change may still be working" in alert.detail


def test_a_profit_action_waits_on_a_held_back_side(deps, factory):
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    hold_back(factory, run_id, 0)
    cancels = [c for c in deps.tasty.calls if c[0] == "cancel"]
    for seconds in (0, 2, 4, 6):
        pass_at(deps, factory, run_id, "11:00", seconds)
    assert rows(factory, "profit") == [] and rows(factory, "close") == []  # not spent
    assert [c for c in deps.tasty.calls if c[0] == "cancel"] == cancels
    assert titles(deps).count("IC: profit action 1 waiting") == 1


# --- Final review fixes -------------------------------------------------------------


def filled_early(factory, run_id):
    """As if the trade had filled before the open (to test the opening minute)."""
    with factory() as session:
        session.get(BotRun, run_id).filled_at = ny("09:20")
        session.commit()


def test_nothing_fires_in_the_first_minute_of_the_session(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    filled_early(factory, run_id)
    for seconds in (10, 12, 14, 50):
        pass_at(deps, factory, run_id, "09:30", seconds)
    assert rows(factory, "close") == []
    pass_at(deps, factory, run_id, "09:31", 5)
    assert rows(factory, "close") == []  # the count starts after the pause
    pass_at(deps, factory, run_id, "09:31", 35)  # #201: held 30 s
    assert len(rows(factory, "close")) == 2


def test_a_profit_action_waits_out_the_opening_minute_too(deps, factory):
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    filled_early(factory, run_id)
    for seconds in (10, 12, 14):
        pass_at(deps, factory, run_id, "09:30", seconds)
    assert rows(factory, "profit") == []
    pass_at(deps, factory, run_id, "09:31", 5)
    pass_at(deps, factory, run_id, "09:31", 7)
    assert [p.state for p in rows(factory, "profit")] == ["filled"]


def test_a_side_that_cant_be_closed_is_alerted_as_partly_started(deps, factory, monkeypatch):
    run_id = trade(deps, factory, **RATIO)
    real = bot_exits.fire_close

    def fire_close(deps_, run, position, *args, **kw):
        if position == 1:
            raise RuntimeError("database is locked")
        return real(deps_, run, position, *args, **kw)

    monkeypatch.setattr(bot_exits, "fire_close", fire_close)
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 30)  # #201: held 30 s
    assert [c.group_position for c in rows(factory, "close")] == [0]
    alert = next(e for e in deps.seen if "PARTLY started" in e.title)
    assert "Put side" in alert.detail and "close it yourself" in alert.detail
    [failed] = [w for w in rows(factory, "watch") if w.group_position == 1]
    assert failed.state == "failed"
    # bots slice 20: the row says the close broke, so the run page's banner is red
    assert failed.log[0]["notes"] == ["the close broke: RuntimeError"]


def test_a_watch_row_that_cant_be_written_sends_nothing_for_that_side(deps, factory, monkeypatch):
    run_id = trade(deps, factory, **RATIO)
    real = bot_watch._record

    def record(deps_, trade_, position, *args, **kw):
        if position == 1:
            raise RuntimeError("disk full")
        return real(deps_, trade_, position, *args, **kw)

    monkeypatch.setattr(bot_watch, "_record", record)
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 30)  # #201: held 30 s
    assert [c.group_position for c in rows(factory, "close")] == [0]
    assert any("PARTLY started" in t for t in titles(deps))


def test_a_profit_action_whose_row_cant_be_finished_never_fires_twice(deps, factory, monkeypatch):
    run_id = trade(deps, factory, contracts=3,
                   groups=with_actions(factory, A(D("50"), 33, None)))  # fmt: skip

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(bot_watch, "_finish", broken)
    for seconds in (0, 2, 4, 6, 8):
        pass_at(deps, factory, run_id, "11:00", seconds)
    assert len(rows(factory, "close")) == 1
    assert len(rows(factory, "profit")) == 1


def _edit_actions(factory, run_id, *actions):
    with factory() as session:
        bot_id = session.get(BotRun, run_id).bot_id
    bot = bot_store.load(factory, bot_id)
    groups = (replace(bot.groups[0], profit_actions=actions), bot.groups[1])
    _, problems = bot_store.save(factory, replace(bot, groups=groups), OPEN_TIME)
    assert problems == {}


def test_inserting_an_action_before_a_done_one_never_runs_the_done_one_again(deps, factory):
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    assert rows(factory, "stop", 0)[0].trigger == D("2.45")
    _edit_actions(factory, run_id, A(D("25"), 0, D("10")), A(D("50"), 0, D("0")),
                  A(D("60"), 0, D("-20")))  # fmt: skip
    for seconds in range(4, 20, 2):
        pass_at(deps, factory, run_id, "11:00", seconds)
    sigs = [p.log[0]["sig"] for p in rows(factory, "profit")]
    assert sigs.count("50|0|0") == 1  # never again
    assert "60|0|-20" in sigs and rows(factory, "stop", 0)[0].trigger == D("1.95")


def test_deleting_a_done_action_never_skips_the_next(deps, factory):
    actions = (A(D("25"), 0, D("0")), A(D("50"), 0, D("-20")))
    run_id = trade(deps, factory, groups=with_actions(factory, *actions))
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    _edit_actions(factory, run_id, actions[1])
    pass_at(deps, factory, run_id, "11:00", 4)
    pass_at(deps, factory, run_id, "11:00", 6)
    assert [p.log[0]["sig"] for p in rows(factory, "profit")] == ["25|0|0", "50|0|-20"]
    assert rows(factory, "stop", 0)[0].trigger == D("1.95")


def test_after_an_action_is_blocked_the_later_ones_are_skipped(deps, factory):
    actions = (A(D("25"), 50, None), A(D("50"), 0, D("0")))
    run_id = trade(deps, factory, groups=with_actions(factory, *actions))
    for seconds in range(0, 12, 2):
        pass_at(deps, factory, run_id, "11:00", seconds)
    assert [p.state for p in rows(factory, "profit")] == ["failed"]
    assert rows(factory, "stop", 0)[0].trigger == D("4.80")


def test_exits_turned_off_just_before_a_watch_close_dont_stop_it(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    at(deps, "11:00")
    [found] = bot_watch._trades(deps, deps.now())
    turn_off_in_db(factory, run_id, 1, deps.tasty)  # between the pass's read and its close
    from optionsjourney.bots import watch as rules

    bot_watch._fire(deps, found, rules.Outcome("sl_ratio", True, None, "S/L ratio 12.000 > 10"))
    assert [c.group_position for c in rows(factory, "close")] == [0, 1]


def test_the_paused_alert_is_said_again_after_a_freeze_resets_it(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    quotes(deps, factory, run_id, ny("10:58"))
    for seconds in (0, 30, 60):
        at(deps, "11:00", seconds)
        bot_watch.watch_pass(deps)
    orders.set_switch(factory, "freeze", True)
    at(deps, "11:02")
    bot_watch.watch_pass(deps)
    orders.set_switch(factory, "freeze", False)
    for seconds in (0, 30, 60):
        at(deps, "11:03", seconds)
        bot_watch.watch_pass(deps)
    assert titles(deps).count("IC: watch rules paused") == 2
    assert titles(deps).count("IC: watch rules resumed") == 0


def test_a_delta_rule_with_no_deltas_alerts_once_a_day_per_bot(deps, factory):
    run_id = trade(deps, factory, use_delta_exit=True, position_delta_above=D("90"))
    for minute in ("11:00", "11:03"):
        for seconds in (0, 30, 60):
            pass_at(deps, factory, run_id, minute, seconds)  # quotes but no Greeks
        orders.set_switch(factory, "freeze", True)
        bot_watch.watch_pass(deps)
        orders.set_switch(factory, "freeze", False)
    paused = [e for e in deps.seen if e.title == "IC: watch rules paused"]
    assert len(paused) == 1 and "TastyTrade" in paused[0].detail


def test_a_watch_close_whose_row_cant_be_finished_still_counts_as_started(
    deps, factory, monkeypatch
):
    run_id = trade(deps, factory, **RATIO)

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(bot_watch, "_finish", broken)
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 30)  # #201: held 30 s
    assert len(rows(factory, "close")) == 2
    assert not any("close it yourself" in d for d in details(deps))
    alert = next(e for e in deps.seen if e.title == "IC: S/L ratio exit")
    assert "Call side, Put side" in alert.detail


def test_a_profit_close_that_breaks_alerts_once_and_never_runs_twice(deps, factory, monkeypatch):
    run_id = trade(deps, factory, contracts=3,
                   groups=with_actions(factory, A(D("50"), 33, None)))  # fmt: skip

    def broken(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(bot_exits, "fire_close", broken)
    for seconds in (0, 2, 4, 6, 8):
        pass_at(deps, factory, run_id, "11:00", seconds)
    [profit] = rows(factory, "profit")
    assert profit.state == "failed"
    assert titles(deps).count("IC: a profit action on Call side couldn't start") == 1


def test_a_profit_stop_move_that_breaks_before_the_move_alerts(deps, factory, monkeypatch):
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))

    def broken(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(bot_exits, "_run_and_bot", broken)
    for seconds in (0, 2, 4, 6):
        pass_at(deps, factory, run_id, "11:00", seconds)
    [profit] = rows(factory, "profit")
    assert profit.state == "failed"
    assert titles(deps).count("IC: profit action 1") == 1
    assert rows(factory, "stop", 0)[0].trigger == D("4.80")


def test_a_profit_stop_move_on_a_stop_not_at_the_broker_is_a_safety_alert(deps, factory):
    from optionsjourney.core.alert_format import is_safety

    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    with factory() as session:  # held by Freeze or waiting to be placed again
        session.get(BotExit, rows(factory, "stop", 0)[0].id).state = "pending"
        session.commit()
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    [event] = [e for e in deps.seen if e.title == "IC: profit action 1"]
    assert not event.routine and is_safety(event.title, event.detail)
    assert "no stop at your broker until it's placed" in event.detail
    assert (rows(factory, "stop", 0)[0].state, rows(factory, "stop", 0)[0].trigger) == (
        "pending", D("2.45"),
    )  # fmt: skip


# --- Slice 13: watch rules and profit actions for any group ------------------------------

from optionsjourney.bots.definition import largest_ratio  # noqa: E402
from optionsjourney.core.symbols import parse_occ  # noqa: E402
from optionsjourney.db.models import Bot  # noqa: E402
from optionsjourney.trading.orders import plain  # noqa: E402
from tests.services.bot_fakes import SHAPE_MATRIX, make_shape, mid_for  # noqa: E402


def shape_trade(deps, factory, key, contracts=None, ratio=None, actions=(), target="50",
                **changes):  # fmt: skip
    """A live run of a preset shape, a 50% stop and `target`% target on every group,
    group 0 with these profit actions and the bot `changes` (watch rules). `ratio`
    sets the first leg's ratio (a credit put ratio spread from the put credit spread);
    `contracts` sizes the run past validation's one contract."""
    bot_id = make_shape(factory, key, live=True, stop=D("50"), target=D(target))
    bot = bot_store.load(factory, bot_id)
    legs = bot.legs
    if ratio is not None:
        legs = tuple(replace(leg, ratio=ratio) if leg.position == 0 else leg for leg in legs)
    groups = tuple(
        replace(g, use_profit_actions=g.position == 0 and bool(actions),
                profit_actions=actions if g.position == 0 else ())
        for g in bot.groups
    )  # fmt: skip
    changed = replace(bot, legs=legs, groups=groups, **changes)
    changed = replace(changed, max_per_trade=changed.contracts * largest_ratio(changed))
    assert bot_store.save(factory, changed, OPEN_TIME)[1] == {}
    if contracts is not None:
        with factory() as session:
            row = session.get(Bot, bot_id)
            row.contracts, row.max_per_trade, row.max_per_day = contracts, 40, 40
            session.commit()
    return bot_entry.fire(deps, bot_id, "09:47")


def quote_shape(deps, factory, run_id, when, mids=None, spot=None):
    """Fresh quotes on every leg at `when`, 0.05 either side of its mid (bot_fakes.mid_for
    unless `mids` maps the symbol to one), and SPX's last trade if given."""
    for leg in legs_of(factory, run_id):
        _, details = parse_occ(leg["symbol"])
        m = D((mids or {}).get(leg["symbol"], mid_for(details.strike, details.put_call.lower())))
        deps.hub.set(leg["symbol"], max(m - D("0.05"), D(0)), m + D("0.05"), when)
    if spot is not None:
        deps.hub.set_last("SPX", spot, when)


def shape_pass(deps, factory, run_id, hhmm, seconds=0, **quoting):
    at(deps, hhmm, seconds)
    quote_shape(deps, factory, run_id, ny(hhmm, seconds=seconds), **quoting)
    bot_watch.watch_pass(deps)


ANY_RATIO = {"use_sl_ratio_exit": True, "sl_ratio_above": D("0.01")}  # any S/L ratio fires
SPX_DOWN = {"use_underlying_exit": True, "underlying_down_pct": D("1")}


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_a_watch_rule_closes_every_matrix_shape(deps, factory, key):
    # Shapes with a sold and a bought leg close on the S/L ratio; a naked short and a
    # long call (no ratio to read) on SPX 1% down from the entry's 5800.
    probe = bot_store.load(factory, make_shape(factory, key))
    both = {leg.side for leg in probe.legs} == {"sell", "buy"}
    run_id = shape_trade(deps, factory, key, **(ANY_RATIO if both else SPX_DOWN))
    with factory() as session:
        groups = sorted({leg["group"] for leg in session.get(BotRun, run_id).legs})
    shape_pass(deps, factory, run_id, "11:00", 0, spot="5742")
    assert rows(factory, "close") == []  # one pass isn't enough
    shape_pass(deps, factory, run_id, "11:00", 30, spot="5742")  # #201: held 30 s
    fired = rows(factory, "watch")
    assert [(w.group_position, w.state) for w in fired] == [(g, "filled") for g in groups]
    rule = "S/L ratio exit" if both else "Underlying move"
    assert {w.log[0]["rule"] for w in fired} == {rule}
    for w in fired:  # each row names its group's legs, as the group's stop does
        (stop,) = rows(factory, "stop", w.group_position)
        assert (w.legs, w.anchor_symbol, w.watched) == (stop.legs, stop.anchor_symbol,
                                                        stop.watched)  # fmt: skip
        assert w.contracts == 1
    closes = rows(factory, "close")
    assert [(c.group_position, c.state) for c in closes] == [(g, "filled") for g in groups]
    assert f"{key}: {rule}" in titles(deps)


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_a_profit_action_moves_the_stop_of_every_matrix_shape_to_break_even(deps, factory, key):
    run_id = shape_trade(deps, factory, key, actions=(A(D("25"), 0, D("0")),))
    before = rows(factory, "stop", 0)[0]
    legs = legs_of(factory, run_id)
    with factory() as session:
        debit = session.get(BotRun, run_id).fill_credit < 0
    # Every sold leg nearly worthless; on a debit bot the bought legs worth a lot
    mids = {leg["symbol"]: "0.10" if leg["side"] == "sell" else ("50" if debit else None)
            for leg in legs}  # fmt: skip
    mids = {symbol: mid for symbol, mid in mids.items() if mid is not None}
    shape_pass(deps, factory, run_id, "11:00", 0, mids=mids)
    shape_pass(deps, factory, run_id, "11:00", 2, mids=mids)
    [profit] = rows(factory, "profit")
    assert (profit.group_position, profit.state) == (0, "filled"), profit.log
    after = rows(factory, "stop", 0)[-1]
    if before.watched:
        assert (after.state, after.trigger) == ("watching", D("0.00"))
        assert stops_sent(deps) == []
    else:
        assert after.state == "working" and after.trigger < before.trigger
        assert ("cancel", before.broker_order_id) in [c[:2] for c in deps.tasty.calls]


def test_a_profit_action_closes_a_share_of_a_debit_group(deps, factory):
    # A long call bought for 2.50 (entry -2.50 a unit), 3 units, the stop watched at 50%
    # of the entry lost (P&L -1.25). At 25% gained (P&L 0.625, shown 0.62) close 33% (1
    # unit) and move the stop to break-even. Its mid 3.50: P&L 1.00.
    run_id = shape_trade(deps, factory, "long_call", contracts=3,
                         actions=(A(D("25"), 33, D("0")),))  # fmt: skip
    (stop,) = rows(factory, "stop")
    assert (stop.watched, stop.trigger, stop.state) == (True, D("-1.25"), "watching")
    long_call = legs_of(factory, run_id)[0]["symbol"]
    mids = {long_call: "3.50"}
    shape_pass(deps, factory, run_id, "11:00", 0, mids=mids)
    assert rows(factory, "profit") == []
    shape_pass(deps, factory, run_id, "11:00", 2, mids=mids)
    [profit] = rows(factory, "profit")
    assert profit.state == "filled" and "closing 1 of 3" in profit.log[0]["notes"][0]
    assert "P/L 1.00 ≥ 0.62" in profit.log[0]["detail"]
    assert (profit.legs, profit.anchor_symbol, profit.watched) == (stop.legs, None, True)
    [close] = rows(factory, "close")
    assert (close.state, close.contracts, close.log[0]["by"]) == ("reduced", 1, "profit action 1")
    # decision 0048: its target rests at TastyTrade (3 units from the fill), is cancelled
    # before the close, and rests again for the 2 left
    rested, sold, again = [c[2] for c in placed(deps)
                           if c[2]["legs"][0]["action"] == "Sell to Close"]  # fmt: skip
    first = next(c[1] for c in placed(deps) if c[2]["time-in-force"] == "GTC")
    assert ("cancel", first) in deps.tasty.calls
    assert [(b["time-in-force"], b["legs"][0]["quantity"]) for b in (rested, again)] == [
        ("GTC", 3),
        ("GTC", 2),
    ]
    assert sold["legs"] == [{"instrument-type": "Equity Option", "symbol": long_call,
                             "quantity": 1, "action": "Sell to Close"}]  # fmt: skip
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts, stop.trigger) == ("watching", 2, D("0.00"))
    assert stops_sent(deps) == []  # nothing rests at the broker for a watched group


def test_a_profit_action_moves_a_resting_stop_on_a_ratio_2_anchor(deps, factory):
    # A credit put ratio spread, 3 units: sell 2 of the 5690 put at 2.50, buy 1 of the
    # 5615 put at 0.05, 4.95 a unit; the stop rests at 3.70 on 6 of the sold put. At 25%
    # (P&L 1.2375) move the stop to break-even: 4.95 over the 2 sold is 2.475 a
    # contract, 2.45 on the tick. The sold put's mid 1.60: P&L 4.95 - 3.15 = 1.80.
    run_id = shape_trade(deps, factory, "put_credit_spread", contracts=3, ratio=2,
                         actions=(A(D("25"), 0, D("0")),))  # fmt: skip
    (old,) = rows(factory, "stop")
    assert (old.trigger, old.contracts) == (D("3.70"), 3)
    mids = {old.anchor_symbol: "1.60"}
    shape_pass(deps, factory, run_id, "11:00", 0, mids=mids)
    shape_pass(deps, factory, run_id, "11:00", 2, mids=mids)
    [profit] = rows(factory, "profit")
    assert profit.state == "filled" and "stop 3.70 → 2.45" in profit.log[0]["notes"][0]
    assert "P/L 1.80 ≥ 1.24" in profit.log[0]["detail"]
    assert (profit.legs, profit.anchor_symbol) == (old.legs, old.anchor_symbol)
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.trigger, stop.contracts) == ("working", D("2.45"), 3)
    assert ("cancel", old.broker_order_id) in [c[:2] for c in deps.tasty.calls]
    sent = stops_sent(deps)[-1]
    assert D(str(sent["stop-trigger"])) == D("2.45") and sent["legs"][0]["quantity"] == 6


def test_a_debit_groups_profit_action_never_loosens_its_watched_level(deps, factory):
    # The long call's watched level tightened by hand to +0.50: break-even is looser.
    run_id = shape_trade(deps, factory, "long_call", actions=(A(D("25"), 0, D("0")),))
    (stop,) = rows(factory, "stop")
    with factory() as session:
        session.get(BotExit, stop.id).trigger = D("0.50")
        session.commit()
    mids = {legs_of(factory, run_id)[0]["symbol"]: "3.50"}
    shape_pass(deps, factory, run_id, "11:00", 0, mids=mids)
    shape_pass(deps, factory, run_id, "11:00", 2, mids=mids)
    [profit] = rows(factory, "profit")
    assert profit.state == "failed" and "already at 0.50" in profit.log[0]["notes"][0]
    assert rows(factory, "stop")[0].trigger == D("0.50")


def test_after_a_profit_close_a_later_stop_fill_and_rearm_use_the_units_left(deps, factory):
    # 3 units of the credit put ratio spread; a profit action closes 1. Another run holds
    # more of both legs, so only the stop row's 2 units cap what's sent: a re-arm sends
    # 2 x 2 of the sold put, and the stop's fill sells 2 x 1 of the bought put.
    run_id = shape_trade(deps, factory, "put_credit_spread", contracts=3, ratio=2,
                         actions=(A(D("25"), 33, None),))  # fmt: skip
    (stop,) = rows(factory, "stop")
    short, wing = stop.anchor_symbol, stop.long_symbol
    mids = {short: "1.60"}
    shape_pass(deps, factory, run_id, "11:00", 0, mids=mids)
    shape_pass(deps, factory, run_id, "11:00", 2, mids=mids)
    [close] = rows(factory, "close")
    assert (close.state, close.contracts) == ("reduced", 1)
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts) == ("working", 2)
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 4
    deps.tasty.held[plain(short)] -= 10
    deps.tasty.held[plain(wing)] += 10
    deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    bot_exits.monitor_pass(deps)
    (stop,) = rows(factory, "stop")
    assert (stop.state, stop.contracts) == ("working", 2)
    assert stops_sent(deps)[-1]["legs"][0]["quantity"] == 4
    deps.tasty.fill(stop.broker_order_id, stop.trigger)
    bot_exits.monitor_pass(deps)
    (sold,) = [c[2] for c in placed(deps) if c[2]["legs"][0]["action"] == "Sell to Close"
               and c[2]["legs"][0]["symbol"] == wing and len(c[2]["legs"]) == 1]  # fmt: skip
    assert sold["legs"][0]["quantity"] == 2


def test_a_profit_action_moves_a_strangles_stop_with_its_shorts_at_no_bid(deps, factory):
    """Final review C1: both shorts at 0 bid / 0.10 ask (mid 0.05). The action fires
    on the group's P&L and moves its watched stop to break-even, on _live_pnl's reading
    of the same quotes."""
    run_id = shape_trade(deps, factory, "short_strangle", actions=(A(D("25"), 0, D("0")),))
    legs = legs_of(factory, run_id)
    mids = {leg["symbol"]: "0.05" for leg in legs}
    shape_pass(deps, factory, run_id, "11:00", 0, mids=mids)
    shape_pass(deps, factory, run_id, "11:00", 2, mids=mids)
    [profit] = rows(factory, "profit")
    assert profit.state == "filled", profit.log
    after = rows(factory, "stop", 0)[-1]
    assert (after.state, after.trigger) == ("watching", D("0.00"))


def test_a_close_part_profit_action_on_a_stop_off_side_closes_and_turns_the_stop_on(deps, factory):
    """Bots slice 18 (Tracker2's scale_out → record_profit_action_fill): the close runs
    with the stop off, and its stop move puts the stop back on for what's left."""
    run_id = trade(deps, factory, contracts=3,
                   groups=with_actions(factory, A(D("50"), 33, D("0"))))  # fmt: skip
    turn_off_in_db(factory, run_id, 0, deps.tasty)
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    [close] = rows(factory, "close")
    assert (close.group_position, close.state, close.contracts) == (0, "reduced", 1)
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.contracts, stop.trigger) == ("working", 2, D("2.45"))
    assert any("the stop is back on at 2.45" in d.lower() for d in details(deps))


def test_a_time_actions_close_runs_on_a_side_with_exits_off(deps, factory):
    action = TimeActionDef("dte", 0, "14:00", close_pct=50)
    run_id = trade(deps, factory, contracts=2, use_time_actions=True, time_actions=(action,))
    turn_off_in_db(factory, run_id, 0, deps.tasty)
    at(deps, "14:00")
    quotes(deps, factory, run_id, ny("14:00"))
    bot_timed.actions_pass(deps)
    assert sorted(c.group_position for c in rows(factory, "close")) == [0, 1]


# --- Bots slice 19: a profit action's stop moved to a $ P&L level --------------------------


def test_a_dollar_profit_action_moves_the_stop_to_entry_less_the_level(deps, factory):
    # Call side: 2.45 credit, stop 4.80. At 50%, keep 0.40 a contract: the short at 2.05
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, None, D("0.40"))))
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    [profit] = rows(factory, "profit")
    assert profit.state == "filled" and "stop 4.80 → 2.05" in profit.log[0]["notes"][0]
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.trigger) == ("working", D("2.05"))
    calc = next(e["stop_calc"] for e in reversed(stop.log) if "stop_calc" in e)
    assert calc["basis"] == "level_usd" and calc["configured"] == "0.40"


def test_a_dollar_profit_close_moves_the_stop_for_what_is_left(deps, factory):
    run_id = trade(deps, factory, contracts=3,
                   groups=with_actions(factory, A(D("50"), 33, None, D("0.40"))))  # fmt: skip
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    [close] = rows(factory, "close")
    assert close.log[0]["then_stop"]["usd"] == "0.40"
    stop = rows(factory, "stop", 0)[0]
    assert (stop.contracts, stop.trigger) == (2, D("2.05"))


def test_a_dollar_profit_stop_that_would_fire_at_once_is_not_moved(deps, factory):
    # keep 1.20 a contract: the short at 1.25; its ask is 1.30
    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, None, D("1.20"))))
    mids = {"5910": "1.25"}
    pass_at(deps, factory, run_id, "11:00", 0, mids=mids)
    pass_at(deps, factory, run_id, "11:00", 2, mids=mids)
    [profit] = rows(factory, "profit")
    assert profit.state == "failed" and "would fire at once" in profit.log[0]["notes"][0]
    assert rows(factory, "stop", 0)[0].trigger == D("4.80")


# --- Bots slice 19: leg delta exits ------------------------------------------------------


def test_a_legs_delta_past_its_threshold_closes_the_whole_trade(deps, factory):
    bot = bot_store.load(factory, make(factory))
    legs = (replace(bot.legs[0], exit_delta_below=D("-40")), *bot.legs[1:])  # the short call
    run_id = trade(deps, factory, use_delta_exit=True, legs=legs)
    calm = {"5910": "0.30", "5985": "0.05", "5690": "-0.20", "5615": "-0.05"}
    tested = calm | {"5910": "0.45"}  # the short call reads -45 by position
    pass_at(deps, factory, run_id, "11:00", 0, deltas=calm)
    pass_at(deps, factory, run_id, "11:00", 2, deltas=tested)
    pass_at(deps, factory, run_id, "11:00", 4, deltas=tested)
    assert rows(factory, "close") == []  # two passes aren't enough: #201, 30 s
    pass_at(deps, factory, run_id, "11:00", 32, deltas=tested)
    assert [c.group_position for c in rows(factory, "close")] == [0, 1]  # the whole trade
    watch_row = rows(factory, "watch")[0]
    assert watch_row.log[0]["rule"] == "Leg 1 delta exit"
    assert "leg 1 delta -45.0 < -40" in watch_row.log[0]["detail"]


# --- Bots slice 21: a trade's own profit actions -------------------------------------------


def _own_actions(*rows_, use=True, side=0):
    form = {f"group-{side}-profits": str(len(rows_))}
    if use:
        form[f"group-{side}-use-profit"] = "on"
    for i, (at_, close, stop) in enumerate(rows_):
        form |= {f"pa-{side}-{i}-at": at_, f"pa-{side}-{i}-close": close,
                 f"pa-{side}-{i}-stop": stop}  # fmt: skip
    return form


def test_the_trades_profit_actions_run_instead_of_the_groups(deps, factory):
    """The drawer's per-group profit actions (BotPositionOverride.profit_actions) replace
    the group's list for this trade: the group's own 50% action doesn't run; the trade's
    runs instead."""
    from optionsjourney.services import bot_manage

    run_id = trade(deps, factory, groups=with_actions(factory, A(D("50"), 0, D("0"))))
    out = bot_manage.set_profit_actions(deps, run_id, 0, _own_actions(("78", "0", "0")))
    assert out.ok, out.message
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)  # 77% of the credit: the group's would fire
    assert rows(factory, "profit") == []
    pass_at(deps, factory, run_id, "11:00", 4, mids={"5910": "0.10"})  # P/L 2.40: 98%
    pass_at(deps, factory, run_id, "11:00", 6, mids={"5910": "0.10"})
    [profit] = rows(factory, "profit")
    assert profit.log[0]["sig"] == A(D("78"), 0, D("0")).sig and profit.state == "filled"


def test_a_trade_whose_bot_has_no_profit_actions_runs_its_own(deps, factory):
    from optionsjourney.services import bot_manage

    run_id = trade(deps, factory)
    assert bot_manage.set_profit_actions(deps, run_id, 0, _own_actions(("50", "0", "0"))).ok
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    [profit] = rows(factory, "profit")
    assert rows(factory, "stop", 0)[0].trigger == D("2.45")


def test_profit_actions_that_ran_cant_be_dropped_and_the_list_cant_be_reverted(deps, factory):
    """management._fired_group_actions_unchanged and "a profit action has already fired on
    this group; edit the fields instead of reverting"."""
    from optionsjourney.services import bot_manage

    run_id = trade(deps, factory)
    assert bot_manage.set_profit_actions(deps, run_id, 0, _own_actions(("50", "0", "0"))).ok
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 2)
    assert len(rows(factory, "profit")) == 1
    dropped = bot_manage.set_profit_actions(deps, run_id, 0, _own_actions(("60", "0", "0")))
    assert not dropped.ok and "already ran" in dropped.message
    assert not bot_manage.revert_profit_actions(deps, run_id, 0).ok
    kept = bot_manage.set_profit_actions(
        deps, run_id, 0, _own_actions(("50", "0", "0"), ("70", "0", "-50"))
    )
    assert kept.ok, kept.message


def test_a_trades_target_pct_never_makes_a_dollar_profit_action_fire(deps, factory):
    """Review I1, with a trade upgraded from slice 6 (an override row holding only a target
    %): the $1.50 profit action stays $1.50, so a 1.10 profit doesn't fire it."""
    bot = bot_store.load(factory, make(factory))
    groups = tuple(
        replace(g, target_pct=None, target_unit="usd", target_usd=D("2.00"),
                use_profit_actions=g.position == 0,
                profit_actions=(A(D("1.50"), 0, D("0")),) if g.position == 0 else ())
        for g in bot.groups
    )  # fmt: skip
    run_id = trade(deps, factory, groups=groups)
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=0, target_pct=D("25"),
                                     changed_at=OPEN_TIME, log=[]))  # fmt: skip
        session.commit()
    pass_at(deps, factory, run_id, "11:00", 0, mids={"5910": "1.40"})  # P/L 2.45 - 1.35
    pass_at(deps, factory, run_id, "11:00", 2, mids={"5910": "1.40"})
    assert rows(factory, "profit") == []
    pass_at(deps, factory, run_id, "11:00", 4, mids={"5910": "0.80"})  # P/L 1.70
    pass_at(deps, factory, run_id, "11:00", 6, mids={"5910": "0.80"})
    assert len(rows(factory, "profit")) == 1


def test_a_loss_action_fires_while_a_profit_action_waits(deps, factory):
    """Decision 0055: loss rows have their own turn, so a -50 row fires before an
    earlier +50 one is reached."""
    actions = (A(D("50"), 0, D("0")), A(D("-50"), 33, None))
    run_id = trade(deps, factory, contracts=3, groups=with_actions(factory, *actions))
    lost = {"5910": "3.80"}  # the call side costs 3.75 to close: 1.30 lost of 2.45
    pass_at(deps, factory, run_id, "11:00", 0, mids=lost)
    pass_at(deps, factory, run_id, "11:00", 2, mids=lost)
    [profit] = rows(factory, "profit")
    assert profit.log[0]["number"] == 2 and "closing 1 of 3" in profit.log[0]["notes"][0]
    [close] = rows(factory, "close")
    assert (close.group_position, close.contracts) == (0, 1)


# --- #201: delta and S/L ratio breaches must hold 30 s (Tracker2 16f11f50) --------------


def test_a_ratio_breach_must_hold_30_seconds_before_it_closes(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    for seconds in (0, 2, 4, 28):
        pass_at(deps, factory, run_id, "11:00", seconds)
    assert rows(factory, "close") == []  # held 28 s: not yet
    pass_at(deps, factory, run_id, "11:00", 30)
    assert len(rows(factory, "close")) == 2


def test_a_clean_pass_restarts_the_hold(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    calm = {"5985": "0.30", "5615": "0.30"}
    pass_at(deps, factory, run_id, "11:00", 0)
    pass_at(deps, factory, run_id, "11:00", 20)
    pass_at(deps, factory, run_id, "11:00", 22, mids=calm)  # the clock starts again
    for seconds in (24, 40, 52):
        pass_at(deps, factory, run_id, "11:00", seconds)
    assert rows(factory, "close") == []  # 28 s since 11:00:24
    pass_at(deps, factory, run_id, "11:00", 54)
    assert len(rows(factory, "close")) == 2


def test_a_delta_breach_must_hold_30_seconds_too(deps, factory):
    run_id = trade(deps, factory, use_delta_exit=True, position_delta_above=D("-100"))
    deltas = {"5910": "0.20", "5985": "0.05", "5690": "-0.20", "5615": "-0.05"}
    pass_at(deps, factory, run_id, "11:00", 0, deltas=deltas)
    pass_at(deps, factory, run_id, "11:00", 2, deltas=deltas)
    assert rows(factory, "close") == []
    pass_at(deps, factory, run_id, "11:00", 30, deltas=deltas)
    assert rows(factory, "watch")[0].log[0]["rule"] == "Delta exit"


# --- #202: an unchanged quote on a live stream is not stale (Tracker2 483df63e) ---------


def quiet_pass(deps, factory, run_id, hhmm, seconds, quoted_at):
    """A pass with every leg last quoted at `quoted_at`, SPX trading now on the stream."""
    at(deps, hhmm, seconds)
    quotes(deps, factory, run_id, quoted_at)
    deps.hub.set_last("SPX", "5800", ny(hhmm, seconds=seconds))
    bot_watch.watch_pass(deps)


def test_a_quiet_quote_on_a_live_stream_still_counts(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    quiet = ny("10:58")  # two minutes without a change, SPX streaming through it
    quiet_pass(deps, factory, run_id, "11:00", 0, quiet)
    quiet_pass(deps, factory, run_id, "11:00", 30, quiet)
    assert len(rows(factory, "close")) == 2
    assert "IC: watch rules paused" not in titles(deps)


def test_a_quiet_quote_is_stale_once_the_stream_is_down_or_after_five_minutes(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    deps.hub.streams["tastytrade"] = "reconnecting"
    for seconds in (0, 30, 62):
        quiet_pass(deps, factory, run_id, "11:00", seconds, ny("10:58"))
    assert rows(factory, "close") == []
    assert titles(deps).count("IC: watch rules paused") == 1
    deps.hub.streams["tastytrade"] = "streaming"
    bot_watch.reset()
    for seconds in (0, 30, 62):
        quiet_pass(deps, factory, run_id, "11:06", seconds, ny("11:00"))  # over 5 minutes
    assert rows(factory, "close") == []


def test_a_quiet_quote_is_stale_when_the_underlying_stops_updating(deps, factory):
    """Connected but silent: the underlying hasn't traded through the stream in 30 s."""
    run_id = trade(deps, factory, **RATIO)
    for seconds in (0, 30, 62):
        at(deps, "11:00", seconds)
        quotes(deps, factory, run_id, ny("10:58"))
        deps.hub.set_last("SPX", "5800", ny("10:59"))
        bot_watch.watch_pass(deps)
    assert rows(factory, "close") == []
    assert "IC: watch rules paused" in titles(deps)


# --- #203: the PAUSED alert names the broker stop; a WARNING logs each leg's age -------


def test_a_paused_trade_with_no_stop_at_the_broker_says_so(deps, factory):
    run_id = trade(deps, factory, **RATIO)
    with factory() as session:
        for row in session.scalars(select(BotExit).where(BotExit.kind == "stop")):
            row.state = "cancelled"
        session.commit()
    quotes(deps, factory, run_id, ny("10:58"))
    for seconds in (0, 62):
        at(deps, "11:00", seconds)
        bot_watch.watch_pass(deps)
    paused = next(e for e in deps.seen if e.title == "IC: watch rules paused")
    assert "no stop rests at TastyTrade for this trade" in paused.detail
    assert "still work" not in paused.detail


def test_a_pause_logs_each_legs_quote_age_and_the_streams(deps, factory, caplog):
    import logging

    run_id = trade(deps, factory, **RATIO)
    quotes(deps, factory, run_id, ny("10:58"))
    deps.hub.set_last("SPX", "5800", ny("10:59"))
    with caplog.at_level(logging.WARNING, logger=bot_watch.__name__):
        for seconds in (0, 62, 64):
            at(deps, "11:00", seconds)
            bot_watch.watch_pass(deps)
    [record] = [r for r in caplog.records if "watch rules paused for run" in r.getMessage()]
    text = record.getMessage()
    assert f"run {run_id}" in text and "S/L ratio: no fresh quotes" in text
    short_call = next(leg["symbol"] for leg in legs_of(factory, run_id) if leg["strike"] == "5910")
    assert f"{short_call} quote=182s greeks=never src=tastytrade" in text
    assert "streams: tastytrade=streaming (not live)" in text
