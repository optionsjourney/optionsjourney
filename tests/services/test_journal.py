from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from werkzeug.datastructures import MultiDict

from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.transactions import RawTransaction
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import Bot, BotRun, Tag, TradeEdit
from optionsjourney.services import journal
from optionsjourney.services.journal import Filters, JournalError
from optionsjourney.services.sync import sync_broker

NOW = datetime.now(UTC)


def _tasty(transaction_id, action, price, effect, at, order_id, symbol):
    return {
        "id": transaction_id,
        "symbol": symbol,
        "instrument-type": "Equity Option",
        "underlying-symbol": "SPXW",
        "transaction-type": "Trade",
        "action": action,
        "quantity": "1",
        "price": price,
        "value": str(float(price) * 100),
        "value-effect": effect,
        "executed-at": at.isoformat(),
        "order-id": order_id,
    }


PUT = "SPXW  261120P05800000"
CALL = "SPXW  261120C06500000"
RAW = [
    # A closed short put, opened 40 days ago
    _tasty(1, "Sell to Open", "2.00", "Credit", NOW - timedelta(days=40), 10, PUT),
    _tasty(2, "Buy to Close", "1.00", "Debit", NOW - timedelta(days=35), 11, PUT),
    # An open short call, opened 2 days ago
    _tasty(3, "Sell to Open", "1.00", "Credit", NOW - timedelta(days=2), 12, CALL),
]
RAW.append(
    {
        "id": 4,
        "symbol": "AAPL",
        "instrument-type": "Equity",
        "underlying-symbol": "AAPL",
        "transaction-type": "Trade",
        "action": "Buy to Open",
        "quantity": "10",
        "price": "250",
        "value": "2500",
        "value-effect": "Debit",
        "executed-at": (NOW - timedelta(days=3)).isoformat(),
        "order-id": 13,
    }
)
STOCK_KEY = "tastytrade:5WX01234:13"
PUT_KEY = "tastytrade:5WX01234:10"
CALL_KEY = "tastytrade:5WX01234:12"


class FakeBroker:
    name = "TastyTrade"
    broker = "tastytrade"

    def accounts(self):
        return [BrokerAccount("tastytrade", "5WX01234", "Alpha")]

    def transactions(self, number, start, end):
        return [
            RawTransaction(str(r["id"]), datetime.fromisoformat(r["executed-at"]), "Trade", r)
            for r in RAW
        ]


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    factory = make_session_factory(engine)
    sync_broker(FakeBroker(), factory)
    return factory


def _tag_id(session_factory, name):
    with session_factory() as session:
        return session.scalar(select(Tag.id).where(Tag.name == name))


def _entry(session_factory, key):
    return journal.find_entry(journal.load_journal(session_factory), key)


def _keys(view):
    return [e.position.key for e in view.entries]


def test_starter_tags(session_factory):
    names = [t.name for t in journal.load_journal(session_factory).tags]
    assert names == ["0DTE", "Calendars", "Credit Spreads", "Iron Condors", "Stocks", "Wheel"]


def test_journal_shows_every_trade_newest_first(session_factory):
    view = journal.load_journal(session_factory)
    assert _keys(view) == [CALL_KEY, STOCK_KEY, PUT_KEY]
    assert view.summary.trades == 3 and view.summary.closed == 1


def test_save_trade(session_factory):
    journal.save_trade(
        session_factory,
        PUT_KEY,
        name="  My put  ",
        tags="earnings, A+ setup, Earnings, wheel",
        notes="Sold into a dip.",
        close_reason="Profit target",
    )
    entry = _entry(session_factory, PUT_KEY)
    assert entry.name == "My put" and entry.renamed
    assert [t.name for t in entry.tags] == ["A+ setup", "earnings", "Wheel"]  # any case
    assert entry.notes == "Sold into a dip."
    assert entry.close_reason == "Profit target"


def test_blank_fields_go_back_to_defaults(session_factory):
    journal.save_trade(
        session_factory,
        PUT_KEY,
        name="x",
        tags="a",
        notes="n",
        close_reason="Stop loss",
    )
    journal.save_trade(
        session_factory, PUT_KEY, name="", tags="", notes="", close_reason=""
    )
    entry = _entry(session_factory, PUT_KEY)
    assert entry.name == "SPX Short Put" and not entry.renamed
    assert entry.tags == [] and entry.notes is None
    assert entry.close_reason == "Closed"


def test_unknown_close_reason_is_refused(session_factory):
    with pytest.raises(JournalError):
        journal.save_trade(
            session_factory,
            PUT_KEY,
            name="",
            tags="",
            notes="",
            close_reason="Lucky",
        )


def test_filters(session_factory):
    wheel = _tag_id(session_factory, "Wheel")
    journal.save_trade(
        session_factory,
        PUT_KEY,
        name="",
        tags="income, Wheel",
        notes="",
        close_reason="",
    )
    tag_id = next(t.id for t in journal.load_journal(session_factory).tags if t.name == "income")

    def keys(**args):
        return _keys(journal.load_journal(session_factory, Filters.from_args(MultiDict(args))))

    assert keys(tag=str(wheel)) == [PUT_KEY]
    assert keys(tag=journal.UNCATEGORIZED) == [CALL_KEY]  # no tags (a stock trade has Stocks)
    assert _keys(
        journal.load_journal(
            session_factory, Filters.from_args(MultiDict([("tag", str(tag_id)), ("tag", "none")]))
        )
    ) == [CALL_KEY, PUT_KEY]  # any of the picked tags
    assert keys(tag=str(tag_id)) == [PUT_KEY]
    assert keys(status="open") == [CALL_KEY, STOCK_KEY]
    assert keys(status="closed") == [PUT_KEY]
    assert keys(days="30") == [CALL_KEY, STOCK_KEY]  # the put closed 35 days ago
    assert keys(account="schwab:999") == []


def test_status_left_unchosen_is_the_pages_default(session_factory):
    """The Trades list opens on open trades; P/L and metrics on all of them (#68)."""

    def keys(default, **args):
        filters = Filters.from_args(MultiDict(args), default_status=default)
        return sorted(_keys(journal.load_journal(session_factory, filters)))

    everything = sorted([CALL_KEY, PUT_KEY, STOCK_KEY])
    assert keys("open") == sorted([CALL_KEY, STOCK_KEY])
    assert keys("all") == everything
    assert keys("open", status="all") == everything  # asked for, so kept
    assert keys("open", status="nonsense") == sorted([CALL_KEY, STOCK_KEY])

    # Only a status that was asked for goes in links and saved views
    assert Filters.from_args(MultiDict(), default_status="open").to_args() == {}
    asked = Filters.from_args(MultiDict({"status": "all"}), default_status="open")
    assert asked.to_args() == {"status": "all"}


def test_adding_a_tag_hands_it_back(session_factory):
    added = journal.add_tag(session_factory, "Iron fly")
    assert added.name == "Iron fly"
    assert added.id == _tag_id(session_factory, "Iron fly")


def test_tag_names_are_unique_regardless_of_case(session_factory):
    journal.add_tag(session_factory, "MEIC")
    with pytest.raises(JournalError, match="already"):
        journal.add_tag(session_factory, "meic")
    with pytest.raises(JournalError, match="already"):
        journal.rename_tag(session_factory, _tag_id(session_factory, "Wheel"), "Meic")
    with pytest.raises(JournalError, match="name"):
        journal.add_tag(session_factory, "   ")
    with pytest.raises(JournalError, match="comma"):
        journal.add_tag(session_factory, "a, b")
    assert journal.tag_for(session_factory, "meic").name == "MEIC"  # found, not made


def test_renaming_a_tag_renames_it_on_its_trades(session_factory):
    journal.save_trade(
        session_factory, PUT_KEY, name="", tags="MEIC", notes="", close_reason=""
    )
    journal.rename_tag(session_factory, _tag_id(session_factory, "MEIC"), "MEIC v2")
    assert [t.name for t in _entry(session_factory, PUT_KEY).tags] == ["MEIC v2"]
    assert journal.tag_counts(session_factory)[_tag_id(session_factory, "MEIC v2")] == 1


def test_a_tag_a_bot_files_under_cant_be_deleted(session_factory):
    """Decision 0072: as a strategy couldn't be, while the bot exists."""
    zero = _tag_id(session_factory, "0DTE")
    _bot(session_factory, "0DTE", split=False)
    with pytest.raises(JournalError, match="Morning condor"):
        journal.delete_tag(session_factory, zero)
    assert [b[1] for b in journal.bot_tags(session_factory)[zero]] == ["Morning condor"]
    assert _tag_id(session_factory, "0DTE") == zero


def test_deleting_a_tag_removes_it_from_trades(session_factory):
    journal.save_trade(
        session_factory, PUT_KEY, name="", tags="oops", notes="", close_reason=""
    )
    with session_factory() as session:
        tag_id = session.scalar(select(Tag.id).where(Tag.name == "oops"))
    journal.delete_tag(session_factory, tag_id)
    assert _entry(session_factory, PUT_KEY).tags == []


def test_dashboards(session_factory):
    filters = Filters(status="open", days=30)
    dashboard_id = journal.create_dashboard(session_factory, "Open stuff", filters)
    saved = journal.get_dashboard(session_factory, dashboard_id)
    assert saved.name == "Open stuff"
    assert Filters.from_saved(saved.filters) == filters

    journal.update_dashboard(session_factory, dashboard_id, Filters(status="closed"))
    assert journal.get_dashboard(session_factory, dashboard_id).filters == {"status": "closed"}

    journal.delete_dashboard(session_factory, dashboard_id)
    assert journal.get_dashboard(session_factory, dashboard_id) is None


def test_positions_cache_rebuilds_only_when_data_changes(session_factory, monkeypatch):
    builds = []
    real_build = journal._build
    monkeypatch.setattr(journal, "_build", lambda *a: builds.append(1) or real_build(*a))
    cache = journal.PositionsCache()

    journal.load_journal(session_factory, cache=cache)
    journal.load_journal(session_factory, cache=cache)
    assert len(builds) == 1  # second load reused the positions

    # Edits that don't change positions don't rebuild
    journal.save_trade(
        session_factory, PUT_KEY, name="x", tags="t", notes="", close_reason=""
    )
    assert (
        journal.find_entry(journal.load_journal(session_factory, cache=cache), PUT_KEY).name == "x"
    )
    assert len(builds) == 1

    # A split choice does
    journal.set_split(session_factory, PUT_KEY, True)
    journal.load_journal(session_factory, cache=cache)
    assert len(builds) == 2

    # So does a sync that brings in something new
    RAW.append(_tasty(4, "Buy to Close", "0.50", "Debit", NOW, 13, CALL))
    try:
        sync_broker(FakeBroker(), session_factory)
        view = journal.load_journal(session_factory, cache=cache)
    finally:
        RAW.pop()
    assert len(builds) == 3
    assert journal.find_entry(view, CALL_KEY).position.status.value == "closed"


def test_open_trade_past_expiry_is_waiting_on_the_broker(session_factory):
    # The open short call expires 2026-11-20; until the broker posts the
    # expiration record, the journal says so instead of calling it open (#2).
    before = journal.load_journal(session_factory, now=datetime(2026, 11, 20, 20, 59, tzinfo=UTC))
    after = journal.load_journal(session_factory, now=datetime(2026, 11, 20, 21, 0, tzinfo=UTC))
    assert not journal.find_entry(before, CALL_KEY).awaiting_broker
    assert journal.find_entry(after, CALL_KEY).awaiting_broker
    assert not journal.find_entry(after, PUT_KEY).awaiting_broker  # closed


def _account_id(session_factory):
    from optionsjourney.db.models import Account

    with session_factory() as session:
        return session.scalar(select(Account.id).where(Account.number == "5WX01234"))


def test_held_legs_are_the_open_trades_contracts_by_account(session_factory):
    # The Trade page chain's Pos column (#210): the open short call, by account id;
    # the closed put, the shares and other tickers are left out.
    now = datetime(2026, 11, 20, 20, 59, tzinfo=UTC)
    held = journal.held_legs(session_factory, "SPX", now=now)
    assert held == [
        {"account_id": _account_id(session_factory), "expiration": "2026-11-20",
         "strike": 6500.0, "type": "call", "qty": -1},
    ]  # fmt: skip
    assert journal.held_legs(session_factory, "AAPL", now=now) == []
    # Past its expiry the trade waits on the broker's record: no longer held
    after = datetime(2026, 11, 20, 21, 0, tzinfo=UTC)
    assert journal.held_legs(session_factory, "SPX", now=after) == []


def _tag_names(session_factory, key):
    return sorted(t.name for t in _entry(session_factory, key).tags)


def test_stock_trades_are_tagged_stocks_and_have_their_own_tab(session_factory):
    assert _tag_names(session_factory, STOCK_KEY) == ["Stocks"]
    assert _tag_names(session_factory, PUT_KEY) == []

    def keys(**args):
        return _keys(journal.load_journal(session_factory, Filters.from_args(MultiDict(args))))

    assert keys(kind="stocks") == [STOCK_KEY]
    with session_factory() as session:
        stocks = session.scalar(select(Tag.id).where(Tag.name == "Stocks"))
    assert keys(tag=str(stocks)) == [STOCK_KEY]
    assert STOCK_KEY in keys()  # unfiltered views keep stock trades; the All trades page drops them
    assert Filters.from_args(MultiDict({"kind": "odd"})).kind == ""


def _all_tags(session_factory):
    with session_factory() as session:
        return sorted(session.scalars(select(Tag.name)))


def test_search_and_date_range(session_factory):
    journal.save_trade(
        session_factory, PUT_KEY, name="", tags="earnings",
        notes="Sold into a dip", close_reason="",
    )  # fmt: skip

    def keys(**args):
        return _keys(journal.load_journal(session_factory, Filters.from_args(MultiDict(args))))

    assert keys(q="aapl") == [STOCK_KEY]  # the underlying, any case
    assert keys(q="dip") == [PUT_KEY]  # notes
    assert keys(q="EARNINGS") == [PUT_KEY]  # tags
    assert keys(q="short call") == [CALL_KEY]  # the trade's name
    assert keys(q="#12") == [CALL_KEY]  # its broker order number
    assert keys(q="nothing like it") == []
    from optionsjourney.core.market import market_date

    opened = market_date(NOW - timedelta(days=3))  # the stock's opened day, New York time
    assert keys(start=opened.isoformat()) == [CALL_KEY, STOCK_KEY]
    assert keys(start=opened.isoformat(), end=opened.isoformat()) == [STOCK_KEY]
    assert keys(start="not a date") == keys()
    args = Filters.from_args(MultiDict({"q": " dip ", "start": "2026-10-01"})).to_args()
    assert args == {"q": "dip", "start": "2026-10-01"}


def test_sorting_the_trades(session_factory):
    view = journal.load_journal(session_factory)

    def order(column, descending=False):
        return [e.position.key for e in journal.sort_entries(view.entries, column, descending)]

    assert order("opened") == [PUT_KEY, STOCK_KEY, CALL_KEY]
    assert order("opened", descending=True) == [CALL_KEY, STOCK_KEY, PUT_KEY]
    assert order("trade") == [
        STOCK_KEY,
        CALL_KEY,
        PUT_KEY,
    ]  # AAPL Long Stock, SPX Short Call, SPX Short Put
    assert order("qty", descending=True)[0] == STOCK_KEY  # 10 shares
    # Trades without a value (no close yet) go last either way
    assert order("closed")[-2:] == order("closed", descending=True)[-2:]
    assert order("nonsense") == order("opened", descending=True)  # the default


IC_KEY = "tastytrade:5WX01234:IC-1"
IC_LEGS = [
    ("Buy to Open", "1.00", "Debit", "SPXW  261120P05780000"),
    ("Sell to Open", "2.00", "Credit", "SPXW  261120P05800000"),
    ("Sell to Open", "1.50", "Credit", "SPXW  261120C06000000"),
    ("Buy to Open", "0.50", "Debit", "SPXW  261120C06020000"),
]


class CondorBroker(FakeBroker):
    """Adds an iron condor opening order, "IC-1", to the usual transactions."""

    def transactions(self, number, start, end):
        rows = [
            _tasty(100 + i, action, price, effect, NOW - timedelta(hours=1), "IC-1", symbol)
            for i, (action, price, effect, symbol) in enumerate(IC_LEGS)
        ]
        return super().transactions(number, start, end) + [
            RawTransaction(str(r["id"]), datetime.fromisoformat(r["executed-at"]), "Trade", r)
            for r in rows
        ]


def _bot(factory, tag, split):
    with factory() as session:
        bot = Bot(
            name="Morning condor",
            tag_id=_tag_id(factory, tag),
            broker="tastytrade",
            account_number="5WX01234",
            live=False,
            enabled=False,
            max_per_trade=1,
            max_per_day=1,
            split_iron_condors=split,
            created_at=NOW,
        )
        session.add(bot)
        session.commit()
        return bot.id


def _run(factory, bot_id, broker_order_id):
    with factory() as session:
        run = BotRun(
            bot_id=bot_id,
            key=f"{bot_id}:{broker_order_id}",
            trading_date=NOW.date(),
            entry_time="09:45",
            status="filled",
            live=False,
            broker_order_id=broker_order_id,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(run)
        session.commit()
        return run.id


@pytest.fixture
def bot_condor(session_factory):
    """A bot, filed under 0DTE, that split-opened the condor IC-1."""
    sync_broker(CondorBroker(), session_factory)
    bot_id = _bot(session_factory, "0DTE", split=True)
    run_id = _run(session_factory, bot_id, broker_order_id="IC-1")
    return session_factory, bot_id, run_id


def test_a_bots_iron_condor_is_split_and_carries_its_tag(bot_condor):
    factory, bot_id, run_id = bot_condor
    view = journal.load_journal(factory)
    halves = [e for e in view.entries if e.position.split_from]
    assert len(halves) == 2
    assert all(e.position.split_from == IC_KEY for e in halves)
    assert all([t.name for t in e.tags] == ["0DTE"] for e in halves)
    assert all(e.bot and e.bot.bot_id == bot_id for e in halves)
    assert all(e.bot.name == "Morning condor" and e.bot.run_id == run_id for e in halves)
    # Trades the bot didn't open are untouched
    other = journal.find_entry(view, CALL_KEY)
    assert other.bot is None and other.tags == []


def test_combine_overrides_a_bots_split(bot_condor):
    factory, bot_id, _ = bot_condor
    journal.set_split(factory, IC_KEY, False)
    view = journal.load_journal(factory)
    assert not [e for e in view.entries if e.position.split_from]
    whole = journal.find_entry(view, IC_KEY)
    assert whole.bot and whole.bot.bot_id == bot_id
    assert [t.name for t in whole.tags] == ["0DTE"]
    with factory() as session:
        assert session.get(TradeEdit, IC_KEY).split is False


def test_a_bot_that_keeps_condors_whole_leaves_them_whole(session_factory):
    sync_broker(CondorBroker(), session_factory)
    bot_id = _bot(session_factory, "0DTE", split=False)
    _run(session_factory, bot_id, broker_order_id="IC-1")
    view = journal.load_journal(session_factory)
    assert not [e for e in view.entries if e.position.split_from]
    assert journal.find_entry(view, IC_KEY).bot.bot_id == bot_id
    # ...until you split it yourself
    journal.set_split(session_factory, IC_KEY, True)
    view = journal.load_journal(session_factory)
    assert len([e for e in view.entries if e.position.split_from]) == 2


def test_splitting_a_tagged_condor_keeps_its_tags_on_both_halves(session_factory):
    sync_broker(CondorBroker(), session_factory)
    journal.save_trade(
        session_factory, IC_KEY, name="", tags="Wheel, earnings",
        notes="", close_reason="",
    )  # fmt: skip
    journal.set_split(session_factory, IC_KEY, True)
    view = journal.load_journal(session_factory)
    for half in (IC_KEY + "#put", IC_KEY + "#call"):
        entry = journal.find_entry(view, half)
        assert {t.name for t in entry.tags} == {"Wheel", "earnings"}


def test_your_tags_go_with_the_bots(bot_condor):
    factory, _, _ = bot_condor
    journal.save_trade(
        factory, IC_KEY + "#put", name="", tags="Wheel", notes="", close_reason=""
    )
    view = journal.load_journal(factory)
    put = journal.find_entry(view, IC_KEY + "#put")
    call = journal.find_entry(view, IC_KEY + "#call")
    assert [t.name for t in put.tags] == ["0DTE", "Wheel"]
    assert [t.name for t in call.tags] == ["0DTE"]
    assert put.bot is not None and call.bot is not None


def test_edit_many_adds_and_removes_tags_on_ticked_trades(session_factory):
    journal.save_trade(
        session_factory, PUT_KEY, name="", tags="keep, old",
        notes="Mine", close_reason="",
    )  # fmt: skip
    n = journal.edit_many(
        session_factory, [PUT_KEY, CALL_KEY],
        add_tags="earnings", remove_tags="OLD, never-made",
        add_tag_ids=[_tag_id(session_factory, "Wheel")],
    )  # fmt: skip
    assert n == 2
    assert _tag_names(session_factory, PUT_KEY) == ["Wheel", "earnings", "keep"]
    assert _tag_names(session_factory, CALL_KEY) == ["Wheel", "earnings"]
    assert _entry(session_factory, PUT_KEY).notes == "Mine"  # other fields stay
    assert "never-made" not in [t.name for t in journal.load_journal(session_factory).tags]
    with pytest.raises(journal.JournalError):
        journal.edit_many(session_factory, [PUT_KEY], add_tag_ids=[9999])


def test_open_trades_come_first_then_closed_by_day_with_headers(session_factory):
    entries = journal.open_then_closed(journal.load_journal(session_factory).entries)
    statuses = [e.position.status.value for e in entries]
    assert statuses == sorted(statuses, key=lambda s: s == "closed")  # open ones first
    rows = journal.with_headers(entries, entries)
    kinds = [r.kind for r in rows if isinstance(r, journal.RowHeader)]
    closed = [e for e in entries if e.position.status.value == "closed"]
    assert kinds[0] == "open" and "closed" in kinds and "day" in kinds
    section = next(r for r in rows if isinstance(r, journal.RowHeader) and r.kind == "closed")
    assert section.count == len(closed)
    assert len([r for r in rows if not isinstance(r, journal.RowHeader)]) == len(entries)


def test_closed_trades_are_by_closing_day_then_opening_time():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from optionsjourney.core.positions import Status

    def entry(name, opened, closed):
        at = lambda h, m, d=16: datetime(2026, 10, d, h, m, tzinfo=UTC)  # noqa: E731
        position = SimpleNamespace(
            status=Status.CLOSED, opened_at=at(*opened), closed_at=at(*closed),
            broker="tastytrade", account_number="5WX01234", underlying="SPX",
        )  # fmt: skip
        return SimpleNamespace(name=name, position=position, bot=None)

    entries = [
        entry("early, expired", (13, 47), (20, 0)),
        entry("late, stopped", (19, 0), (19, 13)),
        entry("middle", (17, 9), (20, 0)),
        entry("day before", (18, 0), (19, 0, 15)),
    ]
    names = [e.name for e in journal.open_then_closed(entries)]
    assert names == ["late, stopped", "middle", "early, expired", "day before"]


def test_a_matched_trade_shows_its_bot_without_a_run(session_factory):
    """#169: a trade matched by hand is linked to its bot (bot_trade_matches)."""
    from optionsjourney.db.models import BotTradeMatch

    bot_id = _bot(session_factory, "0DTE", False)
    with session_factory() as session:
        session.add(BotTradeMatch(position_key=PUT_KEY, bot_id=bot_id, matched_at=NOW))
        session.commit()
    entry = journal.find_entry(journal.load_journal(session_factory), PUT_KEY)
    assert entry.bot.bot_id == bot_id and entry.bot.matched and entry.bot.run_id is None


def test_a_deleted_bots_tag_stays_on_its_trades_as_their_own(bot_condor):
    """0074: deleting a bot puts its tag on its trades, then stops adding it, so it
    can be taken off them. The deleted bot still holds the tag."""
    from optionsjourney.services import bot_store

    factory, bot_id, _ = bot_condor
    zero = _tag_id(factory, "0DTE")
    journal.keep_bot_tag(factory, bot_id)
    bot_store.hide(factory, bot_id, NOW)
    halves = [e for e in journal.load_journal(factory).entries if e.position.split_from]
    assert all([t.name for t in e.tags] == ["0DTE"] and e.fixed_tags == [] for e in halves)
    assert journal.bot_tags(factory) == {zero: [(bot_id, "Morning condor", True)]}

    journal.edit_many(factory, [IC_KEY], remove_tags="0DTE")
    halves = [e for e in journal.load_journal(factory).entries if e.position.split_from]
    assert all(e.tags == [] for e in halves)
    with pytest.raises(JournalError, match="Morning condor"):
        journal.delete_tag(factory, zero)


def _side(name, account, second, *, minute=47, bot="MEIC", closed=None):
    """#214: one side of an entry, opened at 13:<minute>:<second> UTC."""
    from types import SimpleNamespace

    from optionsjourney.core.positions import Status

    position = SimpleNamespace(
        broker="tastytrade", account_number=account, underlying="SPX",
        opened_at=datetime(2026, 10, 13, 13, minute, second, tzinfo=UTC),
        status=Status.CLOSED if closed else Status.OPEN, closed_at=closed,
    )  # fmt: skip
    return journal.JournalEntry(
        position=position, name=name, tags=[], notes=None, close_reason=None,
        bot=journal.BotLink(bot_id=1, name=bot, run_id=None) if bot else None,
    )  # fmt: skip


def _adjacent(names, a, b):
    return abs(names.index(a) - names.index(b)) == 1


# Two accounts each open a call side then a put side in the same minute, seconds apart
SIDES = [
    ("jjC", "JJ", 1), ("bC", "BETA", 2), ("bP", "BETA", 5), ("jjP", "JJ", 6),
]  # fmt: skip


def test_the_opened_sort_keeps_an_entrys_sides_together():
    entries = [_side(*s) for s in SIDES] + [_side("old", "JJ", 10, minute=32)]
    for descending in (True, False):
        names = [e.name for e in journal.sort_entries(entries, "opened", descending)]
        assert names[4 if descending else 0] == "old"  # other minutes still by time
        assert _adjacent(names, "jjC", "jjP") and _adjacent(names, "bC", "bP")
    # within an entry, the exact second still orders the sides
    names = [e.name for e in journal.sort_entries(entries, "opened", False)]
    assert names.index("bC") < names.index("bP") and names.index("jjC") < names.index("jjP")


def test_the_default_order_keeps_an_entrys_sides_together_open_and_closed():
    open_names = [e.name for e in journal.open_then_closed([_side(*s) for s in SIDES])]
    assert _adjacent(open_names, "jjC", "jjP") and _adjacent(open_names, "bC", "bP")
    at = datetime(2026, 10, 13, 19, 0, tzinfo=UTC)
    closed = [_side(*s, closed=at) for s in SIDES]
    names = [e.name for e in journal.open_then_closed(closed)]
    assert _adjacent(names, "jjC", "jjP") and _adjacent(names, "bC", "bP")


def test_different_bots_in_the_same_minute_are_different_entries():
    entries = [_side("a1", "JJ", 1, bot="A"), _side("b1", "JJ", 2, bot="B"),
               _side("a2", "JJ", 3, bot="A")]  # fmt: skip
    names = [e.name for e in journal.sort_entries(entries, "opened", True)]
    assert _adjacent(names, "a1", "a2")
