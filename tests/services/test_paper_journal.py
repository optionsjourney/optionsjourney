from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.core.quotes import Quote
from optionsjourney.db.models import BotRun, PaperFill
from optionsjourney.services import bot_stats, bot_store, journal, paper_journal
from tests.services.bot_fakes import OPEN_TIME, make

D = Decimal
LEGS = [
    {"symbol": "A", "side": "sell", "ratio": 1, "group": 0, "position": 0},
    {"symbol": "B", "side": "sell", "ratio": 1, "group": 1, "position": 1},
]


def add_run(factory, bot_id, key, *, paper=True, status="filled", fills=(), contracts=1):
    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key=key, trading_date=OPEN_TIME.date(), entry_time="09:47",
            status=status, live=False, paper=paper, account_number="5WX01234",
            broker="tastytrade", filled_legs=[], chase_log=[], contracts=contracts,
            legs=LEGS, created_at=OPEN_TIME, updated_at=OPEN_TIME,
        )  # fmt: skip
        session.add(run)
        session.flush()
        for kind, group, price, n, reason, minutes in fills:
            session.add(PaperFill(
                run_id=run.id, kind=kind, group=group, price=D(price), contracts=n,
                reason=reason, legs=[], at=OPEN_TIME + timedelta(minutes=minutes),
            ))  # fmt: skip
        session.commit()
        return run.id


def test_paper_trade_pl(factory):
    bot_id = make(factory)
    add_run(factory, bot_id, "a", fills=[
        ("entry", None, "5.025", 1, "", 0), ("exit", None, "-4.90", 1, "target", 30),
    ])  # fmt: skip
    [trade] = paper_journal.trades(factory, None, None)
    assert trade.pl == D("12.50") and trade.closed_by == "target"
    assert trade.entry_price == D("5.025") and trade.exit_price == D("-4.90")
    assert trade.closed_at == OPEN_TIME + timedelta(minutes=30) and trade.multiplier == 100


def test_group_exits_close_the_trade_only_when_every_group_is(factory):
    bot_id = make(factory)
    add_run(factory, bot_id, "a", fills=[
        ("entry", None, "3.00", 1, "", 0), ("exit", 0, "-1.00", 1, "stop", 10),
        ("action", 1, "0", 0, "profit action", 12),
    ])  # fmt: skip
    [trade] = paper_journal.trades(factory, None, None)
    assert trade.pl is None and trade.closed_at is None and trade.exit_price is None
    with factory() as session:
        session.add(PaperFill(run_id=trade.run_id, kind="expiry", group=1, price=D(0),
                              contracts=1, reason="expired", legs=[], at=OPEN_TIME))  # fmt: skip
        session.commit()
    [trade] = paper_journal.trades(factory, None, None)
    assert trade.pl == D("200") and trade.closed_by == "stop"  # #181: not its expiry
    totals = paper_journal.totals([trade])
    assert (totals.pl, totals.wins, totals.win_rate) == (D("200"), 1, 100)


def test_paper_journal_excludes_old_preview_runs(factory):
    bot_id = make(factory)
    add_run(factory, bot_id, "old", paper=False, status="preview")
    add_run(factory, bot_id, "skipped", status="skipped")  # paper but never filled
    add_run(factory, bot_id, "mine", fills=[("entry", None, "2.00", 1, "", 0)])
    assert [t.run_id for t in paper_journal.trades(factory, None, None)] == [
        factory().scalar(select(BotRun.id).where(BotRun.key == "mine"))
    ]


def test_filters_by_bot_and_day(factory):
    first, second = make(factory), make(factory)
    add_run(factory, first, "a", fills=[("entry", None, "2.00", 1, "", 0)])
    add_run(factory, second, "b", fills=[("entry", None, "2.00", 1, "", 0)])
    assert len(paper_journal.trades(factory, None, None, first)) == 1
    day = OPEN_TIME.date()
    assert len(paper_journal.trades(factory, day, day)) == 2
    assert paper_journal.trades(factory, day + timedelta(days=1), None) == []
    assert paper_journal.trades(factory, None, day - timedelta(days=1)) == []


def test_filters_by_tag(factory):
    """#219: a bot's filing tag or one of its own labels, ignoring case."""
    meic, other = make(factory, tags=("MEIC",)), make(factory)
    add_run(factory, meic, "a", fills=[("entry", None, "2.00", 1, "", 0)])
    add_run(factory, other, "b", fills=[("entry", None, "2.00", 1, "", 0)])
    assert [t.bot_id for t in paper_journal.trades(factory, tag="meic")] == [meic]
    assert len(paper_journal.trades(factory, tag="0DTE")) == 2  # what both file under
    assert paper_journal.trades(factory, tag="nope") == []
    assert paper_journal.tags(factory) == ["0DTE", "MEIC"]


def test_real_journal_excludes_paper(factory):
    bot_id = make(factory, live=True)
    before = journal.load_journal(factory)
    stats_before = bot_stats.summaries(factory, [bot_store.load(factory, bot_id)])
    add_run(factory, bot_id, "a", fills=[
        ("entry", None, "5.00", 1, "", 0), ("exit", None, "-1.00", 1, "target", 30),
    ])  # fmt: skip
    after = journal.load_journal(factory)
    assert after.entries == before.entries == []
    assert after.overview == before.overview
    live = bot_store.load(factory, bot_id)
    assert live.live and bot_stats.summaries(factory, [live]) == stats_before


def test_a_paper_bots_stats_are_its_paper_trades(factory):
    bot_id = make(factory)  # Paper
    add_run(factory, bot_id, "a", fills=[
        ("entry", None, "5.00", 1, "", 0), ("exit", None, "-1.00", 1, "target", 30),
    ])  # fmt: skip
    add_run(factory, bot_id, "b", fills=[
        ("entry", None, "2.00", 1, "", 0), ("exit", None, "-3.00", 1, "stop", 40),
    ])  # fmt: skip
    add_run(factory, bot_id, "c", fills=[("entry", None, "2.00", 1, "", 0)])  # still open
    stat = bot_stats.summaries(factory, [bot_store.load(factory, bot_id)])[bot_id]
    assert stat.closed == 2
    assert (stat.paper.pl, stat.paper.wins, stat.paper.win_rate) == (D("300"), 1, 50)
    # #208: its P/L graph is those pretend trades, from $0, ending at its Closed P/L
    assert [p.pl for p in stat.curve] == [None, D("400"), D("-100")]
    assert stat.curve[-1].cumulative == stat.paper.pl


CONDOR = [
    {"symbol": "P1", "right": "put", "side": "sell", "ratio": 1, "group": 0, "position": 0},
    {"symbol": "P2", "right": "put", "side": "buy", "ratio": 1, "group": 2, "position": 1},
    {"symbol": "C1", "right": "call", "side": "sell", "ratio": 1, "group": 1, "position": 2},
    {"symbol": "C2", "right": "call", "side": "buy", "ratio": 1, "group": 3, "position": 3},
]
MIDS = {"P1": "2.525", "P2": "0.075", "C1": "2.80", "C2": "0.025"}


def add_condor(factory, bot_id, fills):
    """A paper iron condor entered at its legs' mids (5.225 in all), then `fills`."""
    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key="ic", trading_date=OPEN_TIME.date(), entry_time="09:47",
            status="filled", live=False, paper=True, account_number="5WX01234",
            broker="tastytrade", filled_legs=[], chase_log=[], contracts=1, legs=CONDOR,
            created_at=OPEN_TIME, updated_at=OPEN_TIME,
        )  # fmt: skip
        session.add(run)
        session.flush()
        entry_legs = [{"symbol": leg["symbol"], "side": leg["side"], "ratio": 1,
                       "mid": MIDS[leg["symbol"]]} for leg in CONDOR]  # fmt: skip
        session.add(
            PaperFill(
                run_id=run.id,
                kind="entry",
                group=None,
                price=D("5.225"),
                contracts=1,
                reason="entry",
                legs=entry_legs,
                at=OPEN_TIME,
            )
        )
        for kind, group, price, reason, minutes in fills:
            session.add(PaperFill(
                run_id=run.id, kind=kind, group=group, price=D(price), contracts=1,
                reason=reason, legs=[], at=OPEN_TIME + timedelta(minutes=minutes),
            ))  # fmt: skip
        session.commit()


def test_a_split_condor_is_a_put_side_and_a_call_side(factory):
    """#181: a bot that splits its iron condors shows each side on its own, with the
    stop that closed it, as the real journal does."""
    bot_id = make(factory)  # an iron condor bot: split_iron_condors is on
    add_condor(factory, bot_id, [
        ("exit", 0, "-4.60", "stop", 30),
        ("expiry", 2, "0", "expired", 360), ("expiry", 1, "0", "expired", 360),
        ("expiry", 3, "0", "expired", 360),
    ])  # fmt: skip
    put, call = sorted(paper_journal.trades(factory, None, None, split=True),
                       key=lambda t: t.side)[::-1]  # fmt: skip
    assert (put.side, put.entry_price, put.exit_price) == ("put", D("2.45"), D("-4.60"))
    assert (put.closed_by, put.pl) == ("stop", D("-215"))
    assert (call.side, call.entry_price, call.exit_price) == ("call", D("2.775"), D("0"))
    assert (call.closed_by, call.pl) == ("expired", D("277.5"))
    [whole] = paper_journal.trades(factory, None, None)  # bot stats count the whole trade
    assert whole.side is None and whole.pl == D("62.5") and whole.closed_by == "stop"


def test_a_stopped_side_is_closed_while_its_wing_is_held(factory):
    """#183: once a side's sold leg is gone it's closed then, its wing worth 0 until
    it's sold or expires."""
    bot_id = make(factory)
    add_condor(factory, bot_id, [("exit", 1, "-5.15", "stop", 30)])
    put, call = sorted(paper_journal.trades(factory, None, None, split=True),
                       key=lambda t: t.side)[::-1]  # fmt: skip
    assert put.pl is None and put.closed_at is None and not put.wing_held
    assert (call.closed_by, call.pl, call.wing_held) == ("stop", D("-237.5"), True)
    assert call.closed_at == OPEN_TIME + timedelta(minutes=30)
    assert call.held == (("C2", "buy", 1),)
    with factory() as session:  # the wing expires worth 0.40
        session.add(PaperFill(run_id=call.run_id, kind="expiry", group=3, price=D("0.40"),
                              contracts=1, reason="expired", legs=[],
                              at=OPEN_TIME + timedelta(minutes=360)))  # fmt: skip
        session.commit()
    call = next(t for t in paper_journal.trades(factory, None, None, split=True)
                if t.side == "call")  # fmt: skip
    assert (call.pl, call.wing_held, call.closed_by) == (D("-197.5"), False, "stop")
    assert call.closed_at == OPEN_TIME + timedelta(minutes=30)  # still when it was stopped


class Hub:
    def __init__(self, marks):
        self.marks, self.subscribed = marks, set()

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        mark = self.marks.get(key)
        return None if mark is None else Quote(key, D(mark), D(mark), None, "tastytrade", OPEN_TIME)


def test_live_pl_of_the_open_trades(factory):
    """#183: each open trade or side valued at the legs' marks, after its closes."""
    bot_id = make(factory)
    add_condor(factory, bot_id, [("exit", 1, "-5.15", "stop", 30)])
    trades = paper_journal.trades(factory, None, None, split=True)
    hub = Hub({"P1": "3.00", "P2": "0.10"})
    live = paper_journal.live_pl(trades, hub)
    put = next(t for t in trades if t.side == "put")
    assert live == {put.key: D("-45")}  # 2.45 credit, 3.00 to buy back, 0.10 to sell
    assert hub.subscribed == {"P1", "P2"}  # the closed call side isn't priced
    assert paper_journal.live_pl(trades, Hub({"P1": "3.00"})) == {put.key: None}


def test_a_condor_bot_that_keeps_condors_whole_shows_one_row(factory):
    bot_id = make(factory, split_iron_condors=False)
    add_condor(factory, bot_id, [])
    [trade] = paper_journal.trades(factory, None, None, split=True)
    assert trade.side is None and trade.entry_price == D("5.225")


def test_a_paper_runs_fills_leg_by_leg(factory):
    """#182: each pretend fill's legs, with the time, what it was, price and dollars."""
    bot_id = make(factory)
    add_condor(factory, bot_id, [])
    run_id = factory().scalar(select(BotRun.id).where(BotRun.key == "ic"))
    with factory() as session:
        session.add(PaperFill(
            run_id=run_id, kind="exit", group=0, price=D("-4.60"), contracts=1, reason="stop",
            legs=[{"symbol": "P1", "side": "sell", "ratio": 1, "mid": "4.60"}],
            at=OPEN_TIME + timedelta(minutes=30),
        ))  # fmt: skip
        session.add(PaperFill(
            run_id=run_id, kind="expiry", group=2, price=D("0"), contracts=1, reason="expired",
            legs=[{"symbol": "P2", "side": "buy", "ratio": 1, "value": "0"}],
            at=OPEN_TIME + timedelta(minutes=360),
        ))  # fmt: skip
        session.commit()
    fills = paper_journal.fills(factory, run_id)
    assert [(f.what, f.symbol, f.action, f.price, f.amount) for f in fills] == [
        ("entry", "P1", "sell to open", D("2.525"), D("252.5")),
        ("entry", "P2", "buy to open", D("0.075"), D("-7.5")),
        ("entry", "C1", "sell to open", D("2.80"), D("280")),
        ("entry", "C2", "buy to open", D("0.025"), D("-2.5")),
        ("stop", "P1", "buy to close", D("4.60"), D("-460")),
        ("expired", "P2", "expired", D("0"), D("0")),
    ]
    assert fills[4].at == OPEN_TIME + timedelta(minutes=30) and fills[4].contracts == 1
    assert paper_journal.fills(factory, 999) == []
