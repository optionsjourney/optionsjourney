"""A Paper bot's exits: the app watches each open paper trade on the stream and records
pretend exits at the mid (services/bot_paper_exits.watch_pass, decision 0061)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots import futures
from optionsjourney.bots.definition import ProfitActionDef, TimeActionDef
from optionsjourney.core.quotes import Quote
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import BotRun, BotTradeOverride, PaperFill
from optionsjourney.services import (
    bot_entry,
    bot_exits,
    bot_expiry,
    bot_paper,
    bot_paper_exits,
    bot_store,
    bot_watch,
    bot_watched_stops,
    paper_journal,
)
from tests.services.bot_fakes import (
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeTasty,
    fut_sym,
    make,
    make_futures,
    make_shape,
    on_futures,
    sym,
)

D = Decimal
SHORT, LONG = sym(5700, "put"), sym(5650, "put")


def ny(hh, mm, ss=0):
    """A time on OPEN_TIME's day (2026-10-13, New York is UTC-4)."""
    return datetime(2026, 10, 13, hh + 4, mm, ss, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fresh_memory():
    for module in (bot_paper_exits, bot_watch, bot_watched_stops):
        module.reset()
    yield
    for module in (bot_paper_exits, bot_watch, bot_watched_stops):
        module.reset()


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    alerts = []
    clock = [OPEN_TIME]
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty],
        FakeHub(),
        FakeChain(),
        notify=alerts.append,
        sleep=lambda s: None,
        now=lambda: clock[0],
        background=lambda fn, *args: fn(*args),
    )
    d.tasty, d.alerts, d.clock = tasty, alerts, clock
    return d


def quote(deps, symbol, mid, at, half="0.05"):
    deps.hub.set(symbol, D(mid) - D(half), D(mid) + D(half), at)


def open_paper(deps, factory, bot_id, legs, at=OPEN_TIME, contracts=1, ratio=1):
    """A paper run of `legs` ((symbol, side, group, mid)), entered at `at` at those mids."""
    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key=f"t{bot_id}-{at.isoformat()}", trading_date=at.date(),
            entry_time="09:47", status="pending", live=False, account_number="5WX01234",
            broker="tastytrade", filled_legs=[], chase_log=[], contracts=0,
            legs=[{"symbol": s, "side": side, "ratio": ratio, "group": g, "position": n,
                   "strike": str(option_of(s).strike),
                   "right": option_of(s).put_call.lower()}
                  for n, (s, side, g, _) in enumerate(legs)],
            created_at=at, updated_at=at,
        )  # fmt: skip
        session.add(run)
        session.commit()
        run_id = run.id
    for symbol, _, _, mid in legs:
        quote(deps, symbol, mid, at)
    picked = [{"symbol": s, "side": side, "ratio": ratio, "mid": mid} for s, side, _, mid in legs]
    assert bot_paper.enter(deps, run_id, picked, contracts, at) is None
    deps.alerts.clear()
    return run_id


def option_of(symbol):
    parsed = parse_occ(symbol)
    return parsed[1] if parsed is not None else futures.option_of(symbol)


def exits(factory, kind=("exit", "expiry")):
    with factory() as session:
        return list(
            session.scalars(
                select(PaperFill).where(PaperFill.kind.in_(kind)).order_by(PaperFill.id)
            )
        )


def tick(deps, at):
    deps.clock[0] = at
    bot_paper_exits.watch_pass(deps, at)


def titles(deps):
    return [a.title for a in deps.alerts]


def short_put_bot(factory, stop="100", **changes):
    """A short put whose stop (leg premium) rests at the broker on a live bot: fill 2.40
    x (1 + 100%) is a stop at 4.80."""
    bot_id = make_shape(factory, "short_put", stop=D(stop))
    if changes:
        bot = bot_store.load(factory, bot_id)
        _, problems = bot_store.save(factory, replace(bot, **changes), OPEN_TIME)
        assert problems == {}
    return bot_id


def test_paper_stop_closes_group_at_mid(deps, factory):
    run_id = open_paper(deps, factory, short_put_bot(factory), [(SHORT, "sell", 0, "2.40")])
    t1 = OPEN_TIME + timedelta(seconds=20)
    quote(deps, SHORT, "4.90", t1)
    tick(deps, t1)
    assert exits(factory) == []
    tick(deps, t1 + timedelta(seconds=2))
    [fill] = exits(factory)
    assert (fill.run_id, fill.kind, fill.group, fill.reason) == (run_id, "exit", 0, "stop")
    assert fill.price == D("-4.90") and fill.contracts == 1
    assert [t for t in titles(deps) if t.startswith("Paper:")] == [
        "Paper: short_put stop at 4.90 (estimated at mid)"
    ]
    assert bot_paper_exits.open_runs(factory) == []
    tick(deps, t1 + timedelta(seconds=4))
    assert len(exits(factory)) == 1


def test_paper_stop_needs_confirm_passes(deps, factory):
    open_paper(deps, factory, short_put_bot(factory), [(SHORT, "sell", 0, "2.40")])
    t1 = OPEN_TIME + timedelta(seconds=20)
    quote(deps, SHORT, "4.90", t1)
    tick(deps, t1)
    quote(deps, SHORT, "4.50", t1 + timedelta(seconds=2))
    tick(deps, t1 + timedelta(seconds=2))
    quote(deps, SHORT, "4.90", t1 + timedelta(seconds=4))
    tick(deps, t1 + timedelta(seconds=4))
    assert exits(factory) == []
    assert len(bot_paper_exits.open_runs(factory)) == 1


def test_paper_target_fills(deps, factory):
    # No stop, a 50% target: the live path rests this target at the broker (decision 0048)
    bot_id = make_shape(factory, "put_credit_spread", target=D("50"))
    run_id = open_paper(
        deps, factory, bot_id, [(SHORT, "sell", 0, "2.40"), (LONG, "buy", 0, "0.40")]
    )
    t1 = OPEN_TIME + timedelta(seconds=20)
    quote(deps, SHORT, "1.15", t1)  # natural: 1.20 - 0.25 = 0.95, at or under the 1.00 close-at
    quote(deps, LONG, "0.30", t1)
    tick(deps, t1)
    tick(deps, t1 + timedelta(seconds=2))
    [fill] = exits(factory)
    assert (fill.run_id, fill.reason, fill.group, fill.contracts) == (run_id, "target", 0, 1)
    assert fill.price == D("-0.85")  # at the mid: 1.15 - 0.30
    assert any(t.startswith("Paper: put_credit_spread target at 0.85") for t in titles(deps))


def test_paper_timed_close(deps, factory):
    bot_id = short_put_bot(factory, stop="300", close_time="15:45")
    run_id = open_paper(deps, factory, bot_id, [(SHORT, "sell", 0, "2.40")])
    early = ny(15, 44, 50)
    quote(deps, SHORT, "2.00", early)
    tick(deps, early)
    assert exits(factory) == []
    tick(deps, ny(15, 45))
    [fill] = exits(factory)
    assert (fill.run_id, fill.reason, fill.price) == (run_id, "timed close", D("-2.00"))


def test_paper_expiry_settles_intrinsic(deps, factory):
    run_id = open_paper(deps, factory, make(factory), [
        (sym(7760, "call"), "sell", 0, "2.50"), (sym(7810, "call"), "buy", 0, "0.50"),
        (sym(7710, "put"), "sell", 1, "2.50"), (sym(7660, "put"), "buy", 1, "0.50"),
    ])  # fmt: skip
    deps.hub.set_last("SPX", "7720", ny(15, 59, 58))
    tick(deps, ny(16, 5))
    fills = exits(factory)
    assert [(f.kind, f.group, f.reason, f.price) for f in fills] == [
        ("expiry", 0, "expired", D(0)),
        ("expiry", 1, "expired", D(0)),
    ]
    assert {leg["symbol"]: D(leg["value"]) for f in fills for leg in f.legs} == {
        sym(7760, "call"): 0, sym(7810, "call"): 0, sym(7710, "put"): 0, sym(7660, "put"): 0,
    }  # fmt: skip
    assert sum(f.price * f.contracts for f in fills) == 0
    assert bot_paper_exits.open_runs(factory) == [] and run_id


def test_paper_expiry_in_the_money(deps, factory):
    open_paper(deps, factory, make(factory), [
        (sym(7760, "call"), "sell", 0, "2.50"), (sym(7810, "call"), "buy", 0, "0.50"),
        (sym(7710, "put"), "sell", 1, "2.50"), (sym(7660, "put"), "buy", 1, "0.50"),
    ])  # fmt: skip
    deps.hub.set_last("SPX", "7700", ny(15, 59, 58))
    tick(deps, ny(16, 5))
    assert [(f.group, f.price) for f in exits(factory)] == [(0, D(0)), (1, D(-10))]


def test_paper_expiry_waits_without_that_days_price(deps, factory):
    open_paper(deps, factory, short_put_bot(factory), [(SHORT, "sell", 0, "2.40")])
    tick(deps, ny(16, 5))
    tick(deps, ny(16, 6))
    assert exits(factory) == []
    assert titles(deps).count("Paper: short_put can't settle at expiry") == 1


def test_paper_lapse_no_backfill(deps, factory):
    start = ny(9, 59, 50)
    open_paper(deps, factory, short_put_bot(factory), [(SHORT, "sell", 0, "2.40")], at=start)
    quote(deps, SHORT, "4.90", ny(10, 0))
    tick(deps, ny(10, 0))
    quote(deps, SHORT, "6.00", ny(11, 0))
    tick(deps, ny(11, 0))
    assert exits(factory) == []  # the 10:00 pass doesn't confirm one an hour later
    assert titles(deps) == ["Paper: short_put wasn't watched"]
    quote(deps, SHORT, "6.00", ny(11, 0, 2))
    tick(deps, ny(11, 0, 2))
    [fill] = exits(factory)
    assert fill.price == D("-6.00") and fill.at == ny(11, 0, 2)
    assert titles(deps).count("Paper: short_put wasn't watched") == 1


def test_paper_missing_mid_waits(deps, factory):
    bot_id = make_shape(factory, "put_credit_spread", stop=D("100"))
    open_paper(deps, factory, bot_id, [(SHORT, "sell", 0, "2.40"), (LONG, "buy", 0, "0.40")])
    t1 = OPEN_TIME + timedelta(seconds=20)
    quote(deps, SHORT, "4.90", t1)
    deps.hub.quotes[LONG] = Quote(LONG, None, D("0.45"), None, "tastytrade", t1)
    tick(deps, t1)
    tick(deps, t1 + timedelta(seconds=2))
    assert exits(factory) == []
    quote(deps, LONG, "0.40", t1 + timedelta(seconds=4))
    tick(deps, t1 + timedelta(seconds=4))
    [fill] = exits(factory)
    assert fill.reason == "stop" and fill.price == D("-4.50")


def test_paper_profit_action_closes_part(deps, factory):
    bot_id = make_shape(factory, "short_put", stop=D("300"))
    bot = bot_store.load(factory, bot_id)
    action = ProfitActionDef(D("50"), close_pct=50, stop_pct=D("0"))
    group = replace(bot.groups[0], use_profit_actions=True, profit_actions=(action,))
    _, problems = bot_store.save(factory, replace(bot, groups=(group,), max_per_trade=2,
                                                  max_per_day=4), OPEN_TIME)  # fmt: skip
    assert problems == {}
    open_paper(deps, factory, bot_id, [(SHORT, "sell", 0, "2.40")], contracts=2)
    t1 = OPEN_TIME + timedelta(seconds=70)  # past the opening minute's pause
    quote(deps, SHORT, "1.10", t1)
    tick(deps, t1)
    tick(deps, t1 + timedelta(seconds=2))
    [fill] = exits(factory)
    assert (fill.reason, fill.contracts, fill.price) == ("profit action 1", 1, D("-1.10"))
    [action] = exits(factory, ("action",))
    assert action.legs[0]["stop"] == {"trigger": "2.40", "on": "short"}  # break-even
    # the moved stop is watched from here: the short back at 2.50 closes the rest
    quote(deps, SHORT, "2.50", t1 + timedelta(seconds=4))
    tick(deps, t1 + timedelta(seconds=4))
    tick(deps, t1 + timedelta(seconds=6))
    assert [(f.reason, f.contracts) for f in exits(factory)] == [
        ("profit action 1", 1),
        ("stop", 1),
    ]


def test_paper_watch_rule_closes_the_trade(deps, factory):
    bot_id = short_put_bot(factory, stop="300", use_underlying_exit=True,
                           short_put_tested_pts=D("10"))  # fmt: skip
    open_paper(deps, factory, bot_id, [(SHORT, "sell", 0, "2.40")])
    t1 = OPEN_TIME + timedelta(seconds=70)
    quote(deps, SHORT, "3.00", t1)
    deps.hub.set_last("SPX", "5705", t1)  # the short put 5700 is 5 points from it
    tick(deps, t1)
    tick(deps, t1 + timedelta(seconds=2))
    [fill] = exits(factory)
    assert fill.reason == "Short put tested" and fill.price == D("-3.00")


def test_paper_ratio_rule_must_hold_30_seconds(deps, factory):
    """#201 (Tracker2 16f11f50): paper runs the live path's rules, the S/L ratio's 30 s
    hold included."""
    bot_id = make_shape(factory, "put_credit_spread", stop=D("900"))
    bot = bot_store.load(factory, bot_id)
    ratio = replace(bot, use_sl_ratio_exit=True, sl_ratio_above=D("5"))
    assert bot_store.save(factory, ratio, OPEN_TIME)[1] == {}
    open_paper(deps, factory, bot_id, [(SHORT, "sell", 0, "2.40"), (LONG, "buy", 0, "0.40")])
    t1 = OPEN_TIME + timedelta(seconds=70)
    for seconds in (0, 2, 28):
        at = t1 + timedelta(seconds=seconds)
        quote(deps, SHORT, "3.00", at)  # 3.00 / 0.40 = 7.5
        quote(deps, LONG, "0.40", at)
        tick(deps, at)
    assert exits(factory) == []
    quote(deps, SHORT, "3.00", t1 + timedelta(seconds=30))
    quote(deps, LONG, "0.40", t1 + timedelta(seconds=30))
    tick(deps, t1 + timedelta(seconds=30))
    assert {f.reason for f in exits(factory)} == {"S/L ratio exit"}


def test_paper_time_action_closes_part(deps, factory):
    action = TimeActionDef("dit", 0, "10:00", close_pct=50)
    bot_id = short_put_bot(factory, stop="300", use_time_actions=True, max_per_trade=2,
                           max_per_day=4, time_actions=(action,))  # fmt: skip
    open_paper(deps, factory, bot_id, [(SHORT, "sell", 0, "2.40")], contracts=2)
    quote(deps, SHORT, "2.00", ny(9, 59, 58))
    tick(deps, ny(9, 59, 58))
    assert exits(factory) == []
    tick(deps, ny(10, 0))
    tick(deps, ny(10, 0, 2))
    [fill] = exits(factory)
    assert (fill.reason, fill.contracts, fill.price) == ("time action 1", 1, D("-2.00"))
    assert len(exits(factory, ("action",))) == 1


def test_paper_time_action_counts_a_ratio_2_group_in_units(deps, factory):
    """Final review D1: a group whose ratios share a factor (here one leg x2) is counted
    in units (contracts x gcd), as a live run's group_units: 50% of 1 contract x2 closes
    1 of its 2 units, priced per unit, and the P/L adds up."""
    action = TimeActionDef("dit", 0, "10:00", close_pct=50)
    bot_id = short_put_bot(factory, stop="300", use_time_actions=True, time_actions=(action,))
    run_id = open_paper(deps, factory, bot_id, [(SHORT, "sell", 0, "2.40")], ratio=2)
    quote(deps, SHORT, "2.00", ny(9, 59, 58))
    tick(deps, ny(9, 59, 58))
    tick(deps, ny(10, 0))
    tick(deps, ny(10, 0, 2))
    [fill] = exits(factory)
    assert (fill.reason, fill.contracts, fill.price) == ("time action 1", 1, D("-2.00"))
    [run] = [r for r in bot_paper_exits.open_runs(factory) if r.id == run_id]
    with factory() as session:
        held = bot_paper_exits._held(run, list(session.scalars(select(PaperFill))))
    assert held == {0: 1}
    quote(deps, SHORT, "1.50", ny(10, 1))
    assert bot_paper_exits.end_bot(deps, bot_id, "switched to Live", ny(10, 1)) == 1
    assert [(f.contracts, f.price) for f in exits(factory)] == [(1, D("-2.00")), (1, D("-1.50"))]
    [trade] = paper_journal.trades(factory)
    assert trade.pl == (D("4.80") - D("2.00") - D("1.50")) * trade.multiplier  # 1 x2 at 2.40


def test_real_watchers_ignore_paper(deps, factory):
    run_id = open_paper(deps, factory, short_put_bot(factory), [(SHORT, "sell", 0, "2.40")])
    t1 = OPEN_TIME + timedelta(seconds=20)
    quote(deps, SHORT, "4.90", t1)
    for at in (t1, t1 + timedelta(seconds=2), ny(16, 5)):
        deps.clock[0] = at
        bot_watch.watch_pass(deps)
        bot_watched_stops.watch_pass(deps)
        bot_exits.watch_pass(deps)
        bot_exits.monitor_pass(deps)
        bot_expiry.warn_pass(deps)
    assert deps.tasty.calls == []
    assert [a for a in deps.alerts if a.run_id == run_id] == []
    assert exits(factory) == []


def test_paper_futures_expiry_settles_on_the_contract(deps, factory):
    put = fut_sym(7000, "put")
    open_paper(deps, factory, make_futures(factory, live=False), [(put, "sell", 0, "12.00")])
    on_futures(deps)  # the nested chain names /ESZ6's stream "/ESZ26:XCME"
    closed = datetime(2026, 11, 20, 21, 5, tzinfo=UTC)  # 16:05 New York (EST)
    deps.hub.set_last("/ESZ6", "6990", closed - timedelta(minutes=10))  # never published there
    tick(deps, closed)
    assert exits(factory) == []  # no price under the stream's key: it waits, said once
    assert titles(deps).count("Paper: ES can't settle at expiry") == 1
    deps.hub.set_last("dx:/ESZ26:XCME", "6990", closed - timedelta(minutes=10))
    tick(deps, closed + timedelta(seconds=2))
    [fill] = exits(factory)
    assert (fill.kind, fill.price) == ("expiry", D("-10"))


def test_paper_closed_trade_frees_its_open_trade_cap(deps, factory):
    from tests.services.bot_fakes import stream_chain

    bot_id = make(factory, max_open_trades=1)
    run_id = open_paper(deps, factory, bot_id, [
        (sym(5900, "call"), "sell", 0, "2.50"), (sym(5700, "put"), "sell", 1, "2.50"),
    ])  # fmt: skip
    bot = bot_store.load(factory, bot_id)
    assert bot_entry._open_trades(deps, bot) > 0
    with factory() as session:
        for group in (0, 1):
            session.add(PaperFill(run_id=run_id, kind="exit", group=group, price=D("-5"),
                                  contracts=1, reason="stop", legs=[], at=OPEN_TIME))  # fmt: skip
        session.commit()
    assert bot_entry._open_trades(deps, bot) == 0
    stream_chain(deps.hub, OPEN_TIME)
    with factory() as session:
        new = session.get(BotRun, bot_entry.fire(deps, bot_id, "09:47"))
        assert new.status == "filled" and new.paper


def test_paper_watch_rule_keeps_closing_what_is_left(deps, factory):
    bot_id = make(factory, use_underlying_exit=True, short_put_tested_pts=D("10"))
    call, put = sym(5900, "call"), sym(5700, "put")
    open_paper(deps, factory, bot_id, [(call, "sell", 0, "2.50"), (put, "sell", 1, "2.50")])
    t1 = OPEN_TIME + timedelta(seconds=70)
    quote(deps, put, "3.00", t1)
    deps.hub.quotes[call] = Quote(call, None, D("2.55"), None, "tastytrade", t1)
    deps.hub.set_last("SPX", "5705", t1)
    tick(deps, t1)
    tick(deps, t1 + timedelta(seconds=2))
    assert [(f.group, f.reason) for f in exits(factory)] == [(1, "Short put tested")]
    t2 = t1 + timedelta(seconds=4)
    deps.hub.set_last("SPX", "5800", t2)  # the rule no longer holds; the close goes on
    quote(deps, call, "2.40", t2)
    tick(deps, t2)
    assert [(f.group, f.reason) for f in exits(factory)] == [
        (1, "Short put tested"),
        (0, "Short put tested"),
    ]


def _switch_off(factory, run_id, **off):
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=0, changed_at=OPEN_TIME,
                                     log=[], **off))  # fmt: skip
        session.commit()


def test_paper_stop_switched_off_is_not_watched(deps, factory):
    run_id = open_paper(deps, factory, short_put_bot(factory), [(SHORT, "sell", 0, "2.40")])
    _switch_off(factory, run_id, stop_off=True)
    t1 = OPEN_TIME + timedelta(seconds=20)
    quote(deps, SHORT, "4.90", t1)
    tick(deps, t1)
    tick(deps, t1 + timedelta(seconds=2))
    assert exits(factory) == []


def test_paper_target_switched_off_is_not_watched(deps, factory):
    bot_id = make_shape(factory, "put_credit_spread", target=D("50"))
    run_id = open_paper(
        deps, factory, bot_id, [(SHORT, "sell", 0, "2.40"), (LONG, "buy", 0, "0.40")]
    )
    _switch_off(factory, run_id, target_off=True)
    t1 = OPEN_TIME + timedelta(seconds=20)
    quote(deps, SHORT, "1.15", t1)
    quote(deps, LONG, "0.30", t1)
    tick(deps, t1)
    tick(deps, t1 + timedelta(seconds=2))
    assert exits(factory) == []
