"""P/L Day's snapshots and previous closes (#51)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.core.quotes import Quote
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import CloseMark, PLSnapshot
from optionsjourney.services import pl_day
from optionsjourney.services.pl_day import _pick_close, last_session_before
from optionsjourney.services.streaming import QuoteHub

FRIDAY, SATURDAY, MONDAY = date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 12)


def test_the_last_session_before_skips_weekends_and_holidays():
    assert last_session_before(SATURDAY) == FRIDAY
    assert last_session_before(MONDAY) == FRIDAY
    assert last_session_before(date(2026, 11, 27)) == date(2026, 11, 25)  # after Thanksgiving


def test_the_close_wanted_is_picked_by_its_date():
    # At the weekend TastyTrade's prev-close is Thursday's, and "close" Friday's
    item = {"prev-close": "60.78", "prev-close-date": "2026-10-08", "close": "33.25"}
    assert _pick_close(item, FRIDAY) == Decimal("33.25")
    # Monday mid-session: prev-close is Friday's
    item = {"prev-close": "33.25", "prev-close-date": "2026-10-09", "close": None}
    assert _pick_close(item, FRIDAY) == Decimal("33.25")
    assert _pick_close({"prev-close": "1"}, FRIDAY) is None


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


class Hub:
    def subscribe(self, keys):
        pass

    def latest(self, key):
        return Quote(key, Decimal("1"), Decimal("1"), None, "tastytrade", datetime.now(UTC))

    def good_marks(self, keys):
        return {}


class Open:
    """Just enough of a position for a snapshot."""

    key = "k1"
    status = None
    events = []
    live_legs = []
    realized_pl = Decimal("25")


def test_snapshots_wait_for_the_close_and_are_taken_once(factory, monkeypatch):
    monkeypatch.setattr(pl_day, "total_pl", lambda p, marks, mult: Decimal("40"))
    before = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)  # 16:00 New York
    after = datetime(2026, 10, 9, 20, 20, tzinfo=UTC)  # 16:20
    assert pl_day.take_snapshots(factory, [Open()], Hub(), before) is None
    assert pl_day.take_snapshots(factory, [Open()], Hub(), after) == 1
    assert pl_day.take_snapshots(factory, [Open()], Hub(), after) is None  # done for today
    weekend = datetime(2026, 10, 10, 21, 0, tzinfo=UTC)
    assert pl_day.take_snapshots(factory, [Open()], Hub(), weekend) is None
    with factory() as session:
        [row] = session.scalars(select(PLSnapshot))
    assert (row.position_key, row.day, row.total_pl) == ("k1", FRIDAY, Decimal("40"))
    assert pl_day.snapshots_before(factory, {"k1"}, MONDAY) == {"k1": Decimal("40")}
    assert pl_day.snapshots_before(factory, {"k1"}, FRIDAY) == {}


class Leg:
    def __init__(self, symbol):
        self.symbol, self.remaining = symbol, 1


def test_the_snapshot_never_stores_a_pulled_market_and_saves_real_marks(factory, monkeypatch):
    """#241: at 16:20 an SPX weekly's market is pulled. Its trade is snapshotted at the
    leg's last real mark, which is saved for after a restart; with none, not at all."""
    put, call = "SPXW  261016P05800000", "SPXW  261016C06000000"
    monkeypatch.setattr(pl_day, "total_pl", lambda p, marks, mult: marks.get(p.symbol))
    trades = []
    for key, symbol in (("k1", put), ("k2", call)):
        trade = Open()
        trade.key, trade.symbol, trade.live_legs = key, symbol, [Leg(symbol)]
        trades.append(trade)
    hub = QuoteHub([])
    hub.update("tastytrade", put, bid=Decimal("7.00"), ask=Decimal("7.20"), last=None)
    hub.update("tastytrade", put, bid=Decimal("0"), ask=Decimal("20.40"), last=None)
    hub.update("tastytrade", call, bid=Decimal("4.60"), ask=Decimal("10.00"), last=None)
    after = datetime(2026, 10, 9, 20, 20, tzinfo=UTC)  # 16:20
    assert pl_day.take_snapshots(factory, trades, hub, after) == 1
    with factory() as session:
        snapshots = [(r.position_key, r.total_pl) for r in session.scalars(select(PLSnapshot))]
        saved = [(r.symbol, r.mark) for r in session.scalars(select(CloseMark))]
    assert snapshots == [("k1", Decimal("7.10"))]
    assert saved == [(put, Decimal("7.10"))]
    restarted = QuoteHub([])
    pl_day.load_close_marks(factory, restarted)
    assert restarted.good_mark(put) == Decimal("7.10")


def test_on_a_day_without_trading_it_shows_the_last_sessions_change():
    from optionsjourney.services.pl_day import session_day

    assert session_day(datetime(2026, 10, 11, 18, 0, tzinfo=UTC)) == FRIDAY  # Sunday
    assert session_day(datetime(2026, 10, 12, 18, 0, tzinfo=UTC)) == MONDAY
    assert session_day(datetime(2026, 11, 26, 18, 0, tzinfo=UTC)) == date(2026, 11, 25)


# --- previous closes: retry, and Schwab during the trading day ---------------------

OPT = "SPXW  261030P05800000"
MON = date(2026, 10, 19)
MORNING = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)


class FakeTasty:
    def __init__(self, items=None, error=None):
        self.items, self.error, self.calls = items or [], error, 0

    def market_data(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return self.items


class FakeSchwab:
    def __init__(self, close):
        self.close = close

    def quotes(self, symbols):
        return {s: {"quote": {"closePrice": self.close}} for s in symbols}


def test_a_failed_close_is_tried_again_after_two_minutes():
    tasty = FakeTasty(error=RuntimeError("down"))
    assert pl_day.previous_closes([tasty], {OPT}, MON, MORNING) == {}
    assert pl_day.previous_closes([tasty], {OPT}, MON, MORNING + timedelta(minutes=1)) == {}
    assert tasty.calls == 1
    tasty.error = None
    tasty.items = [
        {"symbol": OPT, "prev-close": "4.10", "prev-close-date": "2026-10-16", "close": None}
    ]
    later = MORNING + timedelta(minutes=3)
    assert pl_day.previous_closes([tasty], {OPT}, MON, later) == {OPT: Decimal("4.10")}


def test_schwab_answers_an_options_close_when_tastytrade_is_down():
    clients = [FakeTasty(error=RuntimeError("down")), FakeSchwab(4.1)]
    assert pl_day.previous_closes(clients, {OPT}, MON, MORNING) == {OPT: Decimal("4.1")}


def test_schwab_close_isnt_used_after_the_close_or_at_the_weekend():
    clients = [FakeTasty(error=RuntimeError("down")), FakeSchwab(4.1)]
    evening = datetime(2026, 10, 19, 21, 0, tzinfo=UTC)
    assert pl_day.previous_closes(clients, {OPT}, MON, evening) == {}
    pl_day.forget_closes()
    saturday = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
    assert pl_day.previous_closes(clients, {OPT}, date(2026, 10, 23), saturday) == {}


def test_a_schwab_answer_without_a_close_doesnt_stop_tastytrade_being_asked_again():
    tasty = FakeTasty(error=RuntimeError("down"))
    clients = [tasty, FakeSchwab(None)]
    assert pl_day.previous_closes(clients, {OPT}, MON, MORNING) == {}
    tasty.error = None
    tasty.items = [
        {"symbol": OPT, "prev-close": "4.10", "prev-close-date": "2026-10-16", "close": None}
    ]
    later = MORNING + timedelta(minutes=3)
    assert pl_day.previous_closes(clients, {OPT}, MON, later) == {OPT: Decimal("4.10")}


def test_a_schwab_failure_while_tastytrade_is_down_stays_retryable():
    class BrokenSchwab:
        def quotes(self, symbols):
            raise RuntimeError("down too")

    tasty = FakeTasty(error=RuntimeError("down"))
    clients = [tasty, BrokenSchwab()]
    assert pl_day.previous_closes(clients, {OPT}, MON, MORNING) == {}
    tasty.error = None
    tasty.items = [
        {"symbol": OPT, "prev-close": "4.10", "prev-close-date": "2026-10-16", "close": None}
    ]
    later = MORNING + timedelta(minutes=3)
    assert pl_day.previous_closes(clients, {OPT}, MON, later) == {OPT: Decimal("4.10")}
