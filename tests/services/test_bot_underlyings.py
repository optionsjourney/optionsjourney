"""Bots slice 16: underlyings other than SPX (XSP, SPY, QQQ, IWM), with the fakes."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import DefaultAlertDef
from optionsjourney.db.models import Bot, BotExit, BotRun, UserAlert
from optionsjourney.services import bot_entry, bot_exits, bot_store, bot_watch
from tests.services.bot_fakes import (
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeTasty,
    make,
    on_underlying,
)

D = Decimal
DAY = date(2026, 10, 13)


def ny(hhmm, seconds=0):
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime(DAY.year, DAY.month, DAY.day, hours + 4, minutes, seconds, tzinfo=UTC)


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


def titles(deps):
    return [e.title for e in deps.seen]


def as_underlying(factory, run_id, underlying, entry_spot=None):
    """Move a filled run's bot to another underlying in the database (its legs stay as
    they are): what its readers then read is the bot's underlying."""
    with factory() as session:
        run = session.get(BotRun, run_id)
        session.get(Bot, run.bot_id).underlying = underlying
        if entry_spot is not None:
            run.entry_spot = D(entry_spot)
        session.commit()


# --- Task 4: prices read per underlying -----------------------------------------------


def watched_trade(deps, factory, **changes):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, target_pct=D("80")) for g in bot.groups)
    _, problems = bot_store.save(factory, replace(bot, groups=groups, **changes), OPEN_TIME)
    assert problems == {}
    return bot_entry.fire(deps, bot_id, "09:47")


def watch_at(deps, factory, run_id, hhmm, seconds, last):
    when = ny(hhmm, seconds)
    deps.now = lambda: when
    with factory() as session:
        legs = session.get(BotRun, run_id).legs
    for leg in legs:
        mid = D("0.60") if leg["side"] == "sell" else D("0.05")
        deps.hub.set(leg["symbol"], max(mid - D("0.05"), D(0)), mid + D("0.05"), when)
    deps.hub.set_last("SPX", last, when)
    bot_watch.watch_pass(deps)


def test_xsp_watch_rules_read_spx_divided_by_ten(deps, factory):
    """watch_loop.spot_view: XSP's price is SPX ÷ 10, in XSP's own points."""
    run_id = watched_trade(deps, factory, use_underlying_exit=True,
                           underlying_down_pct=D("1"))  # fmt: skip
    as_underlying(factory, run_id, "XSP", entry_spot="580")
    watch_at(deps, factory, run_id, "11:00", 0, "5750")  # XSP 575.00: -0.86%
    watch_at(deps, factory, run_id, "11:00", 2, "5750")
    assert [r for r in _rows(factory) if r.kind == "close"] == []
    assert "SPX" in deps.hub.subscribed
    watch_at(deps, factory, run_id, "11:00", 4, "5742")  # XSP 574.20: exactly -1%
    watch_at(deps, factory, run_id, "11:00", 6, "5742")
    closes = [r for r in _rows(factory) if r.kind == "close"]
    assert len(closes) == 2
    watch = next(r for r in _rows(factory) if r.kind == "watch")
    assert watch.log[0]["detail"] == "XSP 574.20 is -1.00% from the entry 580.00 (≤ -1%)"


def test_a_trade_with_no_entry_price_names_the_underlying(deps, factory):
    run_id = watched_trade(deps, factory, use_underlying_exit=True,
                           underlying_down_pct=D("1"))  # fmt: skip
    as_underlying(factory, run_id, "XSP")
    with factory() as session:
        session.get(BotRun, run_id).entry_spot = None
        session.commit()
    watch_at(deps, factory, run_id, "11:00", 0, "5750")
    alert = next(e for e in deps.seen if e.title == "IC: underlying move rule off for a trade")
    assert alert.detail.startswith("This trade's XSP price at entry isn't known")


def _rows(factory):
    with factory() as session:
        return list(session.scalars(select(BotExit).order_by(BotExit.id)))


def test_the_strike_alerts_are_on_the_underlying(deps, factory):
    """trade_alerts/defaults.build_specs: an alert on the legs' own underlying, and its
    own price says whether one is already true."""
    rules = (DefaultAlertDef("strike_offset", points=D("10")),)
    changes = on_underlying(deps, "XSP")
    bot_id = make(factory, live=True, use_default_alerts=True, default_alerts=rules, **changes)
    deps.hub.set_last("XSP", "578", OPEN_TIME)  # the put's level, 579, is already passed
    deps.hub.set_last("SPX", "5800", OPEN_TIME)
    bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        alerts = list(session.scalars(select(UserAlert).order_by(UserAlert.id)))
    assert [(a.target, a.rule_key) for a in alerts] == [("XSP", "strike:C:591:10")]


def test_changing_the_underlying_is_refused_while_open(deps, factory):
    """Spec question 5: a run reads its bot's underlying, so it can't change under an
    open trade; a change switches the bot off and back to preview, like a broker change."""
    bot_id = make(factory, live=True)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    bot = bot_store.load(factory, bot_id)
    saved, problems = bot_store.save(factory, replace(bot, underlying="XSP"), OPEN_TIME)
    assert saved is None
    assert problems["underlying"] == (
        f"The underlying can't change while the bot has 1 open trade (run {run_id}), 2 exits "
        f"still working (run {run_id}). Wait until they're done."
    )
    with factory() as session:
        row = session.get(Bot, bot_id)
        assert (row.underlying, row.enabled, row.live) == ("SPX", True, True)
        session.get(BotRun, run_id).status = "closed"
        for row in session.scalars(select(BotExit)):
            row.state = "cancelled"
        session.commit()
    _, problems = bot_store.save(factory, replace(bot, underlying="XSP"), OPEN_TIME)
    assert problems == {}
    with factory() as session:
        row = session.get(Bot, bot_id)
        assert (row.underlying, row.enabled, row.live) == ("XSP", False, False)
    # Saving with the same underlying leaves it armed as it was
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    assert bot_store.save(factory, bot_store.load(factory, bot_id), OPEN_TIME)[1] == {}
    with factory() as session:
        assert session.get(Bot, bot_id).enabled and session.get(Bot, bot_id).live


def test_the_form_reads_the_underlying_trimmed_and_upper_case():
    assert bot_store.read_form({"underlying": " xsp "})[0].underlying == "XSP"
    assert bot_store.read_form({})[0].underlying == "SPX"


# --- Task 5: end to end, and the increments check -------------------------------------


def entered(deps, factory, underlying, **changes):
    """A live iron condor bot on `underlying`, fired at 09:47 on the fakes' chain on its
    own root (bot_fakes.on_underlying); the run."""
    bot_id = make(factory, live=True, **on_underlying(deps, underlying), **changes)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        return session.get(BotRun, run_id)


def sent(deps, kind):
    return [c[2] for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == kind]


def test_an_xsp_condor_enters_and_rests_penny_stops(deps, factory):
    run = entered(deps, factory, "XSP")
    assert run.status == "filled" and run.live, run.reason
    assert [leg["symbol"][:6] for leg in run.legs] == ["XSP   "] * 4
    assert [leg["strike"] for leg in run.legs] == ["591", "599", "569", "561"]
    assert run.entry_spot == D("580")  # SPX 5800 ÷ 10
    entry = [c[2] for c in deps.tasty.calls if c[0] == "place"][0]
    assert {leg["symbol"][:6] for leg in entry["legs"]} == {"XSP   "}
    stops = [r for r in _rows(factory) if r.kind == "stop"]
    assert [s.state for s in stops] == ["working", "working"]
    # The call short fills at its mid, 2.50: 2.50 x 1.95 = 4.875; XSP is a penny at any
    # price, rounded down: 4.87 (an SPX short's is 4.80, on its $0.10 tier)
    assert stops[0].trigger == D("4.87")
    assert {b["legs"][0]["symbol"][:6] for b in sent(deps, "Stop")} == {"XSP   "}


def test_xsp_entries_read_spx_not_a_stale_xsp_print(deps, factory):
    """Tracker2's entry_spot.live_price and dx_symbol / spot_divisor: XSP is SPX ÷ 10, so
    a fresh SPX prices a spot-offset XSP entry while XSP's own print is stale."""
    changes = on_underlying(deps, "XSP")
    legs = tuple(
        replace(leg, pick="spot", target=D({0: "10", 2: "-10"}[leg.position]))
        if leg.position in (0, 2) else leg
        for leg in changes["legs"]
    )  # fmt: skip
    deps.chain.spots = {"SPX": 5800.0, "XSP": 999.0}
    deps.hub.set_last("SPX", "5800", OPEN_TIME)
    deps.hub.set_last("XSP", "999", OPEN_TIME - timedelta(minutes=5))
    bot_id = make(factory, live=True, **{**changes, "legs": legs})
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        run = session.get(BotRun, run_id)
    assert run.status == "filled", run.reason
    assert [leg["strike"] for leg in run.legs] == ["590", "598", "570", "562"]
    assert "XSP" not in deps.chain.priced
    # SPX stale: the entry waits, whatever XSP says
    deps.hub.set_last("SPX", "5800", OPEN_TIME - timedelta(minutes=5))
    deps.hub.set_last("XSP", "580", OPEN_TIME)
    with factory() as session:
        session.get(BotRun, run_id).status = "closed"
        session.commit()
    run_id = bot_entry.fire(deps, bot_id, "09:47", manual=True)
    with factory() as session:
        assert "price is stale" in session.get(BotRun, run_id).reason


def test_an_spy_put_spread_enters_and_rests_a_penny_stop(deps, factory):
    from optionsjourney.bots.definition import shape

    bot = shape("put_credit_spread", bot_store.load(factory, make(factory)))
    legs = tuple(replace(leg, target=D({0: "2.90", 1: "-75"}[leg.position])) for leg in bot.legs)
    groups = tuple(replace(g, stop_pct=D("95")) for g in bot.groups)
    on_underlying(deps, "SPY")
    bot_id, problems = bot_store.save(
        factory, replace(bot, id=None, name="SPY PCS", underlying="SPY", legs=legs,
                         groups=groups, close_time="15:45"), OPEN_TIME)  # fmt: skip
    assert problems == {}
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        run = session.get(BotRun, run_id)
        assert run.status == "filled"
        assert [leg["symbol"][:6] for leg in run.legs] == ["SPY   ", "SPY   "]
    (stop,) = [r for r in _rows(factory) if r.kind == "stop"]
    # The short put (5690) fills at its mid, 2.50: 2.50 x 1.95 = 4.875, on a penny: 4.87
    assert stop.state == "working" and stop.trigger == D("4.87")


def test_a_stock_enters_and_rests_its_stop_on_the_stocks_own_increments(deps, factory):
    """The author's ruling: single stocks, on TastyTrade's increments (option_ticks.ensure):
    a penny under $3 and a nickel from $3, so a stop at 4.875 rests at 4.85."""
    deps.tasty.ticks = {"AAPL": [{"threshold": "3", "value": "0.01"}, {"value": "0.05"}]}
    run = entered(deps, factory, "AAPL")
    assert run.status == "filled", run.reason
    assert [leg["symbol"][:6] for leg in run.legs] == ["AAPL  "] * 4
    stops = [r for r in _rows(factory) if r.kind == "stop"]
    assert [s.trigger for s in stops] == [D("4.85"), D("4.85")]
    assert {b["stop-trigger"] for b in sent(deps, "Stop")} == {"4.85"}


def test_increments_that_cant_be_loaded_refuse_the_entry(deps, factory):
    """analyzer_entry.run_analyzer_entry: "Couldn't load TastyTrade's option increments"."""
    from optionsjourney.brokers.http import BrokerError

    def broken(symbol):
        raise BrokerError("TastyTrade is down")

    deps.tasty.equity_tick_sizes = broken
    run = entered(deps, factory, "SPY")
    assert run.status == "skipped"
    assert run.reason == (
        "Couldn't load TastyTrade's option increments for SPY. Try again. Not entered."
    )
    assert [c for c in deps.tasty.calls if c[0] == "place"] == []


def test_increments_need_tastytrade(deps, factory):
    """A Schwab-only setup can't load a stock's increments: its entries are refused."""
    from tests.services.bot_fakes import FakeSchwab

    deps.clients = lambda: [FakeSchwab()]
    run = entered(deps, factory, "QQQ")
    assert run.status == "skipped" and "Couldn't load TastyTrade's" in run.reason


def test_an_spx_or_xsp_entry_never_reads_increments(deps, factory):
    asked = []
    deps.tasty.equity_tick_sizes = lambda symbol: asked.append(symbol) or []
    assert entered(deps, factory, "SPX").status == "filled"
    assert asked == []


# --- Re-review: loading the stored increments at startup -------------------------------


def test_a_malformed_stored_table_is_skipped_and_the_rest_learned(factory):
    from optionsjourney.db.models import TickSize
    from optionsjourney.services import orders
    from optionsjourney.trading import ticks

    with factory() as session:
        session.add_all([
            TickSize(key="AAPL", sizes=[{"threshold": "3", "value": "0.01"}, {"value": "0.05"}],
                     fetched_at=OPEN_TIME),
            TickSize(key="MSFT", sizes=None, fetched_at=OPEN_TIME),
            TickSize(key="NVDA", sizes="garbage", fetched_at=OPEN_TIME),
        ])  # fmt: skip
        session.commit()
    assert orders.load_learned(factory) == 1
    assert ticks.learned("AAPL") and ticks.learned("MSFT") is None


def test_a_failed_load_at_startup_alerts_and_never_stops_the_bots(deps, monkeypatch):
    from optionsjourney.services import bot_loops, orders

    def broken(session_factory):
        raise RuntimeError("the database is locked")

    monkeypatch.setattr(orders, "load_learned", broken)
    bot_loops.load_learned(deps)  # never raises
    (alert,) = [e for e in deps.seen if "increments" in e.title]
    assert alert.title == "Stock option increments not loaded" and not alert.routine
    assert "Stock and ETF bots won't enter" in alert.detail
