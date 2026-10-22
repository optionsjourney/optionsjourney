"""The demo's starting account: a made-up trader's last three months, ready to tour.

``build`` makes a new database the way the app itself would have filled it: the setup
wizard's answers, three bots saved through the bot store, about thirteen weeks of
made-up trades at the pretend TastyTrade and Schwab brought in by the real sync, day
by day, the account values the app reads from them, the trades' tags and notes saved
as the journal saves them, a watchlist, two alerts and a made-up economic calendar
stored by the calendar's own refresh. Everything runs under a clock travelled to the
moment each thing happened (time-machine, not ticking), ending at the demo morning, so
every stored time fits and two builds are identical.

The past runs of the two Live bots, the Paper bot's runs and the open calendar's run
are written as rows: the app records a run only as its bot trades, live. The open
calendar's exits are then placed by the bot's own exit code, so its target rests at
the pretend broker as a working order.

``prepare`` is what create_app calls: demo mode only ever runs on its own database.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple
from unittest import mock

import time_machine
from sqlalchemy import select

from optionsjourney.bots import adopt as rules
from optionsjourney.bots import floating, schedule
from optionsjourney.bots.definition import (
    BotDef,
    EntryTimeDef,
    GroupDef,
    LegDef,
)
from optionsjourney.bots.manual import ManualRules
from optionsjourney.bots.timeline import SENT
from optionsjourney.config import ConfigError
from optionsjourney.core.market import market_date
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import BotExit, BotRun, PaperFill, Setting
from optionsjourney.demo import history
from optionsjourney.demo.book import SCHWAB_ACCOUNT, TASTY_ACCOUNT, Book
from optionsjourney.demo.broker import PretendSchwab, PretendTasty
from optionsjourney.demo.clock import DEMO_DAY, DEMO_START, NY
from optionsjourney.demo.feeds import DemoStream
from optionsjourney.demo.history import (
    CALENDAR,
    CALENDAR_DAY,
    CONDOR,
    CONDOR_SHORTS,
    PAPER,
    History,
    ny,
)
from optionsjourney.demo.marker import DB_NAME, DEMO_MARK, refusal
from optionsjourney.demo.market import PretendMarket
from optionsjourney.services import (
    account_values,
    bot_entry,
    bot_store,
    chart_grid,
    economic_events,
    journal,
    manual_bots,
    setup,
    trade_events,
    user_alerts,
)
from optionsjourney.services.chain import ChainData
from optionsjourney.services.streaming import QuoteHub
from optionsjourney.services.sync import sync_all
from optionsjourney.trading.orders import Side

__all__ = [
    "MARKET_SEED",
    "NY",
    "SEEDED_AT",
    "Books",
    "DemoDataError",
    "build",
    "prepare",
]

SEEDED_AT = DEMO_START  # the demo morning, when the app's own clock starts
# The made-up market's seed. Chosen so the tour's two scenes fit one upward story: SPX
# was near 5885 on Monday morning, when the Weekly calendar opened at that strike, and
# starts the demo near 5800. The 0DTE condor's call-side stop (a push up of about 40
# points) leaves the calendar short of its 25% target; a little further up reaches it,
# never toward the condor's put side.
MARKET_SEED = 2280
_SIDECARS = ("-journal", "-wal", "-shm")
STYLES = ["0DTE", "Credit Spreads", "Iron Condors", "Calendars", "Covered Calls", "Earnings"]
WATCHLIST_ID = "demo-watchlist"
WATCHLIST = ["SPX", "QQQ", "IWM", "AAPL", "MSFT", "NVDA"]
MANUAL_EXITS = "Put spread exits"  # the manual bot the tour has the visitor attach


class Books(NamedTuple):
    """The pretend accounts the demo's brokers start from."""

    tasty: Book
    schwab: Book


class DemoDataError(ConfigError):
    """The data folder holds a real database: demo mode never runs on it."""


# ----- create_app's entry -----


def prepare(data_dir: Path, *, seed: int = MARKET_SEED) -> Books:
    """The starting account, built fresh in `data_dir`. A database the demo made
    before is replaced (the demo day always starts over); any other is refused and
    left exactly as it is."""
    path = data_dir / DB_NAME
    if path.exists():
        problem = refusal(data_dir, demo=True)
        if problem:
            raise DemoDataError(problem)
        for suffix in ("", *_SIDECARS):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
    return build(data_dir, seed=seed)


# ----- the build -----


def build(data_dir: Path, *, seed: int = MARKET_SEED) -> Books:
    """Make `data_dir/optionsjourney.db` (which must not exist) with the starting
    account, and return the pretend accounts the brokers start from."""
    path = data_dir / DB_NAME
    if path.exists():
        raise FileExistsError(path)
    start = datetime.combine(DEMO_DAY, SEEDED_AT, tzinfo=NY)
    market = PretendMarket(seed=seed, start=start)
    books = Books(Book(TASTY_ACCOUNT), Book(SCHWAB_ACCOUNT))
    clients = [PretendTasty(market, books.tasty), PretendSchwab(market, books.schwab)]
    engine = make_engine(path)
    factory = make_session_factory(engine)
    try:
        # The trader set the app up the Friday before the history starts
        before = ny(history.FIRST_DAY - timedelta(days=3), "17:00")
        with time_machine.travel(before, tick=False) as clock, _seeded_uuids(seed):
            migrate(engine)
            _mark(factory, before)
            _setup(factory)
            bots = _bots(factory, before)
            _manual_bots(factory, before)
            past = history.make(
                market, books.tasty, books.schwab,
                {name: bot_store.load(factory, bots[name]) for name in (CONDOR, CALENDAR)},
                seed,
            )  # fmt: skip
            for day in past.days:
                clock.move_to(ny(day, "16:30"))  # each evening the app synced the day
                sync_all(clients, factory)
                _past_runs(factory, bots, past, day)
                _paper_runs(factory, bots[PAPER], past, day)
                _file(factory, past, day)
                trade_events.record(factory, datetime.now(UTC))  # as each sync does
            clock.move_to(start)
            sync_all(clients, factory)
            account_values.sync_values(clients, factory)
            _open_calendar(factory, clients, bots[CALENDAR], past)
            bot_store.set_armed(factory, bots[CONDOR], False, start)  # for the tour to arm
            _calendar(factory, start)
            _watchlist(factory)
            _alerts(factory)
    finally:
        engine.dispose()
    return books


@contextmanager
def _seeded_uuids(seed: int) -> Iterator[None]:
    """uuid4 from a seeded generator while building (an order's tag is one): two builds
    write the same rows."""
    rng = random.Random(seed * 1299709)
    with mock.patch.object(uuid, "uuid4", lambda: uuid.UUID(int=rng.getrandbits(128), version=4)):
        yield


def _mark(factory, now: datetime) -> None:
    """The demo's own mark (no service writes it): prepare only ever replaces a
    database that carries it."""
    with factory() as session:
        session.add(Setting(key=DEMO_MARK, value="1", updated_at=now))
        session.commit()


def _setup(factory) -> None:
    """The setup wizard, finished: both brokers, the trading styles (their tags) and
    every risk box ticked, so the demo opens on the journal."""
    setup.accept_risk(factory)
    setup.choose_brokers(factory, ["tastytrade", "schwab"])
    setup.apply_styles(factory, STYLES)
    setup.accept_broker_risk(factory)
    setup.mark_complete(factory)


def _tag(factory, name: str) -> int:
    return journal.tag_for(factory, name).id


def _bots(factory, now: datetime) -> dict[str, int]:
    """The three bots, saved as the automation page saves them."""
    condor = BotDef(
        None, CONDOR, _tag(factory, "Iron Condors"), "tastytrade", TASTY_ACCOUNT,
        underlying="SPX", contracts=1, max_entries_per_day=1,
        legs=(
            LegDef(0, Side.SELL, "call", pick="spot", target=Decimal(CONDOR_SHORTS), group=0),
            LegDef(1, Side.BUY, "call", pick="offset", target=Decimal(10), link=0, group=0),
            LegDef(2, Side.SELL, "put", pick="spot", target=Decimal(-CONDOR_SHORTS), group=1),
            LegDef(3, Side.BUY, "put", pick="offset", target=Decimal(-10), link=2, group=1),
        ),
        groups=(
            GroupDef(0, "Call side", stop_pct=Decimal(200), target_pct=Decimal(50)),
            GroupDef(1, "Put side", stop_pct=Decimal(200), target_pct=Decimal(50)),
        ),
        use_floating_entry=True, floating_start="09:40", floating_end="15:00",
        # Mondays, Wednesdays and Fridays (the demo day is a Wednesday); the history
        # trades the same days
        frequency="weekly", entry_days=(1, 3, 5),
    )  # fmt: skip
    calendar = BotDef(
        None, CALENDAR, _tag(factory, "Calendars"), "tastytrade", TASTY_ACCOUNT,
        underlying="SPX", contracts=1, max_open_trades=2,
        legs=(
            LegDef(0, Side.SELL, "put", dte=4, pick="spot", target=Decimal(0)),
            LegDef(1, Side.BUY, "put", dte=11, pick="offset", target=Decimal(0), link=0),
        ),
        groups=(
            GroupDef(0, "Calendar", target_pct=Decimal(25), stop_basis="group_entry",
                     broker_exit="target"),
        ),
        frequency="weekly", entry_days=(1, 4), entry_times=(EntryTimeDef("10:00"),),
        close_time="15:45", close_dte=0,
    )  # fmt: skip
    qqq = BotDef(
        None, PAPER, _tag(factory, "Credit Spreads"), "tastytrade", TASTY_ACCOUNT,
        underlying="QQQ", contracts=1,
        legs=(
            LegDef(0, Side.SELL, "put", dte=3, pick="delta", target=Decimal("0.20")),
            LegDef(1, Side.BUY, "put", dte=3, pick="offset", target=Decimal(-5), link=0),
        ),
        groups=(GroupDef(0, "Put spread", stop_pct=Decimal(100), target_pct=Decimal(50)),),
        frequency="weekly", entry_days=(2,), entry_times=(EntryTimeDef("10:30"),),
    )  # fmt: skip
    ids = {}
    for bot in (condor, calendar, qqq):
        bot_id, problems = bot_store.save(factory, bot, now)
        if problems:
            raise ValueError(f"{bot.name}: {problems}")
        ids[bot.name] = bot_id
    setup.accept_live_risk(factory)  # the first bot to go Live asks it once
    bot_store.set_mode(factory, ids[PAPER], "paper", now)
    for bot_id in ids.values():
        bot_store.set_armed(factory, bot_id, True, now)
    return ids


def _manual_bots(factory, now: datetime) -> None:
    """The manual bot the tour's Trade by hand step attaches: a 50% profit target and a
    200% stop."""
    manual_bots.save(
        factory, None,
        ManualRules(name=MANUAL_EXITS, target_value=Decimal(50), stop_value=Decimal(200)),
        now,
    )  # fmt: skip


def _file(factory, past: History, day: date) -> None:
    """That evening the trader tagged the day's own trades and wrote a few notes."""
    for trade in past.manual:
        if trade.day == day and (trade.tags or trade.note):
            key = f"{trade.broker}:{trade.account}:{trade.order_id}"
            journal.save_trade(factory, key, name="", tags=trade.tags, notes=trade.note,
                               close_reason="")  # fmt: skip


# ----- the bots' runs -----


def _iso(at: datetime) -> str:
    return at.isoformat()


def _run_row(bot_id: int, key: str, trade: history.BotTrade, matched) -> BotRun:
    return BotRun(
        bot_id=bot_id, key=key, trading_date=trade.day, entry_time=trade.hhmm,
        status="filled", live=True, account_number=TASTY_ACCOUNT, broker="tastytrade",
        legs=matched.legs, filled_legs=matched.filled_legs, target_credit=matched.credit,
        fill_credit=matched.credit, contracts=matched.contracts,
        broker_order_id=trade.order_id, entry_spot=trade.spot,
        chase_log=[
            {"at": _iso(trade.at), "price": f"{matched.credit:f}", "result": SENT},
            {"at": _iso(trade.at), "tranche": 1, "filled": matched.contracts},
        ],
        filled_at=trade.at, created_at=trade.at, updated_at=trade.at,
    )  # fmt: skip


def _matched(factory, bot_id: int, trade: history.BotTrade) -> rules.Matched:
    """The trade as the bot's filled run holds it (the hand-over's own match)."""
    return rules.match(bot_store.load(factory, bot_id), trade.held, today=trade.day)


def _past_runs(factory, bots: dict[str, int], past: History, day: date) -> None:
    """Each trade of a Live bot that closed on `day`, as the run that opened it, with
    the exits that closed it (or were cancelled when the other one filled)."""
    for trade in past.bot_trades:
        if trade.is_open or market_date(max(x.at for x in trade.exits)) != day:
            continue
        bot_id = bots[trade.bot]
        matched = _matched(factory, bot_id, trade)
        if trade.bot == CONDOR:
            key = floating.minute_key(bot_id, trade.day, trade.hhmm)
        else:
            key = schedule.entry_key(bot_id, trade.hhmm, trade.day)
        last = max(x.at for x in trade.exits)
        with factory() as session:
            run = _run_row(bot_id, key, trade, matched)
            run.updated_at = last
            session.add(run)
            session.flush()
            sides = {side.position: side for side in trade.sides}
            for done in sorted(trade.exits, key=lambda x: (x.at, x.group, x.kind)):
                session.add(_exit_row(run.id, sides[done.group], done, trade))
            session.commit()


def _exit_row(run_id: int, side, done: history.Exit, trade: history.BotTrade) -> BotExit:
    """One exit row with its log in the words the run page reads (bots/timeline.py)."""
    legs = [{"symbol": leg.symbol, "side": leg.side.value, "ratio": leg.ratio}
            for leg in side.legs]  # fmt: skip
    placed = _iso(trade.at + timedelta(seconds=3))
    ended = _iso(done.at)
    sent = {"at": ended, "result": SENT}
    if done.kind == "stop":
        log = [{"at": placed, "stop": f"{side.stop.configured:f}"},
               {"at": placed, "result": SENT, "price": f"{done.trigger:f}"}]  # fmt: skip
    elif done.kind == "target":
        first = {"at": placed, "target": "50" if trade.bot == CONDOR else "25", "unit": "pct",
                 "credit": f"{side.entry:f}", "close_at": f"{side.target:f}"}  # fmt: skip
        log = [first]
        if side.rests == "target":
            first["rests"] = "target"
            log.append({"at": placed, "result": SENT, "price": f"{side.target:f}"})
        elif done.state == "filled":
            log.append(sent | {"price": f"{side.target:f}"})
    elif done.kind == "wing":
        legs = [{"symbol": side.long, "side": "buy", "ratio": 1}]
        log = [{"at": ended, "mode": "close_market"}, sent | {"market": 1}]
    else:  # timed
        log = [{"at": ended, "event": "timed close", "why": "15:45, on its expiration day"},
               sent | {"market": 1}]  # fmt: skip
    if done.state == "filled":
        log.append({"at": ended, "event": "filled", "price": f"{done.fill_price:f}"})
    else:
        why = "the side's stop filled" if done.kind == "target" else "the target filled"
        if trade.bot == CALENDAR:
            why = "the timed close took the trade"
        log.append({"at": ended, "event": "cancelled", "why": why})
    return BotExit(
        run_id=run_id, group_position=done.group, kind=done.kind, state=done.state,
        short_symbol=side.short, long_symbol=side.long, contracts=side.contracts,
        broker_order_id=done.order_id, trigger=done.trigger, limit=done.limit,
        fill_price=done.fill_price, rearms=0, log=log, legs=legs,
        anchor_symbol=side.short or None, watched=not side.short,
        created_at=trade.at if done.kind in ("stop", "target") else done.at,
        updated_at=done.at,
    )  # fmt: skip


def _paper_runs(factory, bot_id: int, past: History, day: date) -> None:
    """The QQQ bot's Paper runs that ended on `day`: a pretend entry at the mids and
    how each ended."""
    for trade in past.paper:
        if market_date(trade.exit_at) != day:
            continue
        key = schedule.entry_key(bot_id, trade.hhmm, trade.day)
        filled = [{"symbol": leg["symbol"], "side": leg["side"], "fill_price": leg["mid"],
                   "contracts": 1} for leg in trade.legs]  # fmt: skip
        used = [{"symbol": leg["symbol"], "side": leg["side"], "ratio": 1, "mid": leg["mid"]}
                for leg in trade.legs]  # fmt: skip
        with factory() as session:
            run = BotRun(
                bot_id=bot_id, key=key, trading_date=trade.day, entry_time=trade.hhmm,
                status="filled", live=False, paper=True, account_number=TASTY_ACCOUNT,
                broker="tastytrade", legs=trade.legs, filled_legs=filled,
                target_credit=trade.credit, fill_credit=trade.credit, contracts=1,
                entry_spot=trade.spot, chase_log=[], filled_at=trade.at,
                created_at=trade.at, updated_at=trade.exit_at,
            )  # fmt: skip
            session.add(run)
            session.flush()
            session.add(PaperFill(run_id=run.id, kind="entry", group=None, price=trade.credit,
                                  contracts=1, reason="entry", legs=used, at=trade.at))  # fmt: skip
            exit_legs = [
                {**leg, "mid": f"{mid:f}"}
                for leg, mid in zip(used, trade.exit_mids or [Decimal(0)] * len(used),
                                    strict=True)
            ]  # fmt: skip
            session.add(PaperFill(run_id=run.id, kind=trade.exit_kind, group=0,
                                  price=trade.exit_price, contracts=1, reason=trade.reason,
                                  legs=exit_legs, at=trade.exit_at))  # fmt: skip
            session.commit()


def _open_calendar(factory, clients, bot_id: int, past: History) -> None:
    """The Weekly calendar's trade from two days ago, still open: its run, then its
    exits placed by the bot's own code (its target rests at the broker, GTC)."""
    [trade] = [t for t in past.bot_trades if t.is_open]
    assert trade.day == CALENDAR_DAY
    matched = _matched(factory, bot_id, trade)
    with factory() as session:
        run = _run_row(bot_id, schedule.entry_key(bot_id, trade.hhmm, trade.day), trade, matched)
        session.add(run)
        session.commit()
        run_id = run.id
    deps = bot_entry.BotDeps(
        factory, lambda: clients, QuoteHub([DemoStream()]), ChainData(),
        notify=lambda event: None, now=lambda: datetime.now(UTC),
    )  # fmt: skip
    bot_entry.hand_to_exits(deps, bot_store.load(factory, bot_id), run_id)
    with factory() as session:
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
    if not any(r.kind == "target" and r.state == "working" for r in rows):
        raise RuntimeError(f"the open calendar's target didn't rest: {[r.state for r in rows]}")


# ----- the rest of the account -----

_FOMC_PAGE = "\n".join(
    ['<div class="panel"><h4>2026 FOMC Meetings</h4>']
    + [
        f'<div class="fomc-meeting__month"><strong>{month}</strong></div>'
        f'<div class="fomc-meeting__date">{days}</div>'
        for month, days in (
            ("January", "27-28"), ("March", "17-18"), ("May", "5-6"), ("June", "16-17"),
            ("July", "28-29"), ("September", "22-23"), ("November", "3-4"),
            ("December", "15-16"),
        )
    ]
    + ["</div>"]
)  # fmt: skip
_CPI_DAYS = ("2026-11-12", "2026-12-10")
_THIS_WEEK = (  # ForexFactory's feed for the demo week; nothing on the demo day itself
    ("CB Leading Index m/m", "2026-10-19T10:00:00-04:00", "Medium", "-0.1%", "-0.2%"),
    ("Unemployment Claims", "2026-10-22T08:30:00-04:00", "High", "221K", "218K"),
    ("Existing Home Sales", "2026-10-22T10:00:00-04:00", "Medium", "4.02M", "3.96M"),
    ("Durable Goods Orders m/m", "2026-10-23T08:30:00-04:00", "High", "0.4%", "-0.3%"),
    # The next jobs report, ahead of the feed's week (made up, as the rest)
    ("Non-Farm Employment Change", "2026-11-06T08:30:00-05:00", "High", "165K", "142K"),
)


def _calendar(factory, now: datetime) -> None:
    """A made-up economic calendar, stored by the calendar's own refreshes from made-up
    replies (no fetch): FOMC and CPI days ahead for the bots' date lists, and this
    week's releases (jobless claims on Thursday) and November's jobs report (payrolls,
    Friday 6 November). Nothing on the demo day."""
    feed = [
        {"title": title, "country": "USD", "date": when, "impact": impact,
         "forecast": forecast, "previous": previous}
        for title, when, impact, forecast, previous in _THIS_WEEK
    ]  # fmt: skip
    problems = [
        economic_events.refresh_fomc(factory, now, lambda: _FOMC_PAGE),
        economic_events.refresh_fred(
            factory, now,
            lambda release, key, start: {"release_dates": [{"date": d} for d in _CPI_DAYS]},
            "demo",
        ),
        economic_events.refresh_forexfactory(factory, now, lambda: feed),
    ]  # fmt: skip
    problems = [p for p in problems if p]
    if problems:
        raise RuntimeError(f"the demo's calendar didn't store: {problems}")


def _watchlist(factory) -> None:
    chart_grid.create_group(factory, {"id": WATCHLIST_ID, "name": "Demo watchlist"})
    chart_grid.update_group(factory, WATCHLIST_ID, {"symbols": WATCHLIST})


def _alerts(factory) -> None:
    user_alerts.create_price(factory, "SPX", "below", "5760",
                             "Made-up level: the bottom of last week's range")  # fmt: skip
    user_alerts.create_price(factory, "NVDA", "above", "140", "Breakout watch")
