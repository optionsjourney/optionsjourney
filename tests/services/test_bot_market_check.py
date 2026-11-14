"""#327 (decision 0130): the 09:00 check that today's entries can be placed. For each
underlying an armed bot trades today it asks both brokers for the expirations and, for
SPX and XSP (which quote overnight), one chain; it alerts only on a problem."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from optionsjourney.bots.definition import iron_condor_skeleton
from optionsjourney.bots.market_check import Listing
from optionsjourney.db.models import Setting
from optionsjourney.services import bot_entry, bot_market_check, bot_store
from optionsjourney.trading.orders import Side
from tests.services.bot_fakes import FakeHub, FakeTasty, make, rows

DAY = date(2026, 10, 14)  # a Wednesday
DAYS = (DAY, date(2026, 10, 16))


def ny(day, hour, minute=0):
    """New York wall-clock time in October 2026 (UTC-4), as UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(
        hours=hour + 4, minutes=minute
    )


def with_deltas(chain):
    """A chain whose every option has a delta, as Schwab's snapshot sends."""
    return {
        strike: {right: side | {"delta": 0.3 if right == "call" else -0.3}
                 for right, side in sides.items()}
        for strike, sides in chain.items()
    }  # fmt: skip


class CheckChain:
    def __init__(self):
        self.listings = {"tastytrade": Listing(True, DAYS), "schwab": Listing(True, DAYS)}
        self.rows = with_deltas(rows(DAY.isoformat()))
        self.source = "schwab"
        self.asked = []

    def broker_expirations(self, clients, ticker):
        self.asked.append(("expirations", ticker))
        return dict(self.listings)

    def option_rows_from(self, clients, hub, ticker, expiration):
        self.asked.append(("chain", ticker, expiration))
        return (self.rows, self.source) if self.rows else (None, None)

    def underlying_last(self, clients, hub, ticker):
        return 5800.0


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    """The check runs on its own thread in the app; here, inline."""
    monkeypatch.setattr(bot_market_check, "start", lambda fn, deps: fn(deps))


@pytest.fixture
def deps(factory):
    d = bot_entry.BotDeps(
        factory,
        lambda: [FakeTasty()],
        FakeHub(),
        CheckChain(),
        notify=lambda e: None,
        now=lambda: ny(DAY, 9),
    )
    d.seen = []
    d.notify = d.seen.append
    return d


def at(deps, when):
    deps.now = lambda: when


def checked_on(factory):
    with factory() as session:
        row = session.get(Setting, bot_market_check.CHECKED_KEY)
        return row.value if row else None


def by_delta(factory, **changes):
    """An iron condor whose shorts are picked by delta."""
    legs = tuple(
        replace(leg, pick="delta", target=Decimal("0.10"))
        if leg.side is Side.SELL
        else replace(leg, target=Decimal("10"))
        for leg in iron_condor_skeleton("IC").legs
    )
    return make(factory, legs=legs, **changes)


def test_all_well_is_quiet_and_remembered_for_the_day(deps, factory):
    make(factory)
    bot_market_check.check_pass(deps)
    assert deps.seen == []
    assert deps.chain.asked == [("expirations", "SPX"), ("chain", "SPX", DAY.isoformat())]
    assert checked_on(factory) == DAY.isoformat()
    deps.chain.asked.clear()
    at(deps, ny(DAY, 11))
    bot_market_check.check_pass(deps)
    assert deps.chain.asked == []  # once a day


def test_not_before_nine(deps, factory):
    make(factory)
    at(deps, ny(DAY, 8, 59))
    bot_market_check.check_pass(deps)
    assert deps.chain.asked == [] and checked_on(factory) is None


def test_a_later_start_still_checks_the_entries_to_come(deps, factory):
    make(factory)  # its entry is at 09:47
    at(deps, ny(DAY, 9, 40))
    bot_market_check.check_pass(deps)
    assert ("expirations", "SPX") in deps.chain.asked
    deps.chain.asked.clear()
    at(deps, ny(DAY + timedelta(days=1), 9, 50))  # the next day, after its only entry
    bot_market_check.check_pass(deps)
    assert deps.chain.asked == [] and checked_on(factory) == (DAY + timedelta(days=1)).isoformat()


def test_weekends_and_holidays_are_skipped(deps, factory):
    make(factory)
    for day in (date(2026, 10, 17), date(2026, 11, 26)):  # a Saturday, Thanksgiving
        at(deps, datetime(day.year, day.month, day.day, 15, tzinfo=UTC))
        bot_market_check.check_pass(deps)
    assert deps.chain.asked == [] and checked_on(factory) is None


def test_the_demo_is_never_checked(deps, factory):
    make(factory)
    deps.demo = True
    bot_market_check.check_pass(deps)
    assert deps.chain.asked == [] and deps.seen == []


def test_no_armed_bot_asks_nothing(deps, factory):
    make(factory, enabled=False)
    bot_market_check.check_pass(deps)
    assert deps.chain.asked == [] and deps.seen == []


def test_paper_and_live_bots_both_count_once_per_underlying(deps, factory):
    make(factory, live=False)
    make(factory, live=True)
    bot_market_check.check_pass(deps)
    assert deps.chain.asked.count(("expirations", "SPX")) == 1


def test_neither_broker_listing_expirations_alerts_not_placed(deps, factory):
    make(factory)
    deps.chain.listings = {
        "tastytrade": Listing(True, (), "the response couldn't be read"),
        "schwab": Listing(True, (), "timed out"),
    }
    bot_market_check.check_pass(deps)
    [said] = deps.seen
    assert said.title == "Market data check: SPX entries will NOT be placed"
    assert said.detail.startswith(
        "SPX: no expirations from TastyTrade (the response couldn't be read) or Schwab "
        "(timed out): entries need them."
    )
    assert said.link == "/automation" and not said.routine


def test_tastytrade_failing_with_schwab_covering_alerts_degraded(deps, factory):
    make(factory)
    deps.chain.listings["tastytrade"] = Listing(True, (), "down")
    bot_market_check.check_pass(deps)
    [said] = deps.seen
    assert said.title == "Market data degraded: entries will use a backup source"
    assert "TastyTrade couldn't list expirations (down); entries will use Schwab's list" in (
        said.detail
    )


def test_a_broker_not_connected_is_quiet(deps, factory):
    make(factory)
    deps.chain.listings["schwab"] = Listing(False)
    bot_market_check.check_pass(deps)
    assert deps.seen == []


def test_no_chain_for_spx_alerts_not_placed(deps, factory):
    make(factory)
    deps.chain.rows = None
    bot_market_check.check_pass(deps)
    [said] = deps.seen
    assert "no 10-14 option chain from your brokers" in said.detail
    assert "NOT be placed" in said.title


def test_a_chain_from_the_backup_broker_alerts_degraded(deps, factory):
    make(factory)
    deps.chain.source = "tastytrade"  # Schwab is connected: its chain should have come
    bot_market_check.check_pass(deps)
    [said] = deps.seen
    assert "Schwab's option chain failed; entries will price from TastyTrade's live quotes" in (
        said.detail
    )


def test_tastytrades_chain_with_no_schwab_connected_is_quiet(deps, factory):
    make(factory)
    deps.chain.listings["schwab"] = Listing(False)
    deps.chain.source = "tastytrade"
    bot_market_check.check_pass(deps)
    assert deps.seen == []


def test_deltas_are_checked_only_for_a_bot_that_picks_by_them(deps, factory):
    make(factory)  # by premium
    # No deltas sent and no bids, so none can be worked out from a mid either
    deps.chain.rows = {k: {r: s | {"bid": None} for r, s in v.items()} for k, v in rows().items()}
    bot_market_check.check_pass(deps)
    assert deps.seen == []


def test_a_chain_without_deltas_alerts_for_a_delta_bot(deps, factory):
    by_delta(factory)
    deps.chain.rows = {k: {r: s | {"bid": None} for r, s in v.items()} for k, v in rows().items()}
    bot_market_check.check_pass(deps)
    [said] = deps.seen
    assert "has no deltas: entries that pick strikes by delta won't be placed" in said.detail


def test_a_delta_bot_with_deltas_is_quiet(deps, factory):
    by_delta(factory)
    bot_market_check.check_pass(deps)
    assert deps.seen == []


def test_a_stock_or_etf_has_no_chain_before_the_open(deps, factory):
    make(factory, underlying="SPY")
    bot_market_check.check_pass(deps)
    assert deps.chain.asked == [("expirations", "SPY")]
    deps.chain.asked.clear()
    at(deps, ny(DAY + timedelta(days=1), 9, 35))  # started after the open
    bot_market_check.check_pass(deps)
    assert deps.chain.asked == [("expirations", "SPY"), ("chain", "SPY", "2026-10-16")]


def test_a_failing_check_never_raises_and_says_nothing(deps, factory):
    make(factory)

    def broken(*a):
        raise RuntimeError("bug")

    deps.chain.broker_expirations = broken
    bot_market_check.check_pass(deps)
    assert deps.seen == []


def test_the_check_runs_off_the_monitors_thread(deps, factory, monkeypatch):
    import threading

    make(factory)
    started = []
    monkeypatch.undo()
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name))
    bot_market_check.check_pass(deps)
    assert started == ["bot-market-check"] and deps.chain.asked == []


def test_the_monitor_runs_the_check(deps, factory, monkeypatch):
    from optionsjourney.services import bot_loops

    ran = []
    monkeypatch.setattr(bot_market_check, "check_pass", ran.append)
    bot_loops._monitor(deps)
    assert ran == [deps]


def test_a_bot_archived_is_not_checked(deps, factory):
    bot_id = make(factory)
    bot_store.archive(factory, bot_id, ny(DAY, 8))
    bot_market_check.check_pass(deps)
    assert deps.chain.asked == []
