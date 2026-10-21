from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from optionsjourney.core.holdings import StockPosition
from optionsjourney.core.sessions import NYSE_EARLY_CLOSES
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services import stock_history as sh


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def held():
    return [
        StockPosition("tt:1", "AAPL", Decimal("10"), Decimal("100")),
        StockPosition("tt:1", "MSFT", Decimal("-5"), Decimal("200")),
        StockPosition("tt:1", "ZERO", Decimal("0"), Decimal("1")),
    ]


class Read:
    def __init__(self, price):
        self.price = price


def quotes(clients, hub, wanted, *, now=None):
    prices = {"AAPL": 110.5, "MSFT": 190}
    return {k: Read(prices[k[0]]) for k in wanted if k[0] in prices}


def test_waits_for_the_close_and_saves_once(factory):
    def take(now):
        return sh.take_stock_snapshot(
            factory, [], None, now, positions=lambda c, strict: held(), prices=quotes
        )

    assert take(datetime(2026, 10, 21, 20, 0, tzinfo=UTC)) is None  # 16:00 ET
    assert take(datetime(2026, 10, 21, 20, 20, tzinfo=UTC)) == 2
    assert take(datetime(2026, 10, 21, 20, 30, tzinfo=UTC)) is None
    assert take(datetime(2026, 10, 24, 20, 20, tzinfo=UTC)) is None  # Saturday
    rows = sh.load_rows(factory)
    assert [(r.ticker, r.close) for r in rows] == [
        ("AAPL", Decimal("110.5")),
        ("MSFT", Decimal("190")),
    ]
    assert rows[0].day == date(2026, 10, 21)


def test_half_day_saves_after_1_15(factory):
    day = date(2026, 11, 27)
    assert day in NYSE_EARLY_CLOSES
    args = dict(positions=lambda c, strict: held(), prices=quotes)
    # EST: 13:00 ET = 18:00Z; 13:10 ET too early, 13:20 ET fine
    assert (
        sh.take_stock_snapshot(
            factory, [], None, datetime(2026, 11, 27, 18, 10, tzinfo=UTC), **args
        )
        is None
    )
    assert (
        sh.take_stock_snapshot(
            factory, [], None, datetime(2026, 11, 27, 18, 20, tzinfo=UTC), **args
        )
        == 2
    )


def test_a_failing_broker_saves_nothing_and_retries(factory):
    now = datetime(2026, 10, 21, 20, 20, tzinfo=UTC)
    assert (
        sh.take_stock_snapshot(
            factory, [], None, now, positions=lambda c, strict: None, prices=quotes
        )
        is None
    )
    assert sh.load_rows(factory) == []
    assert (
        sh.take_stock_snapshot(
            factory, [], None, now, positions=lambda c, strict: held(), prices=quotes
        )
        == 2
    )
    assert len(sh.load_rows(factory)) == 2


def test_next_morning_does_not_save_today_early(factory):
    assert (
        sh.take_stock_snapshot(
            factory,
            [],
            None,
            datetime(2026, 10, 22, 13, 0, tzinfo=UTC),
            positions=lambda c, strict: held(),
            prices=quotes,
        )
        is None
    )


def test_no_holdings_writes_nothing(factory):
    assert (
        sh.take_stock_snapshot(
            factory,
            [],
            None,
            datetime(2026, 10, 21, 20, 20, tzinfo=UTC),
            positions=lambda c, strict: [],
            prices=quotes,
        )
        == 0
    )
    assert sh.load_rows(factory) == []


def test_missing_price_waits_then_saves(factory):
    none = lambda c, h, w, *, now=None: {}  # noqa: E731
    args = dict(positions=lambda c, strict: held())
    t = datetime(2026, 10, 21, 20, 20, tzinfo=UTC)  # 16:20 ET
    assert sh.take_stock_snapshot(factory, [], None, t, prices=none, **args) is None
    assert sh.load_rows(factory) == []
    assert sh.take_stock_snapshot(factory, [], None, t, prices=quotes, **args) == 2


def test_still_missing_at_8pm_saves_close_none(factory):
    none = lambda c, h, w, *, now=None: {}  # noqa: E731
    args = dict(positions=lambda c, strict: held(), prices=none)
    assert (
        sh.take_stock_snapshot(factory, [], None, datetime(2026, 10, 22, 0, 0, tzinfo=UTC), **args)
        == 2
    )
    assert [r.close for r in sh.load_rows(factory)] == [None, None]


def test_zero_price_counts_as_missing_and_duplicates_collapse(factory):
    dup = held() + [StockPosition("tt:1", "AAPL", Decimal("10"), Decimal("120"))]
    zero = lambda c, h, w, *, now=None: {k: Read(0) for k in w}  # noqa: E731
    t = datetime(2026, 10, 21, 20, 20, tzinfo=UTC)
    assert (
        sh.take_stock_snapshot(factory, [], None, t, positions=lambda c, strict: dup, prices=zero)
        is None
    )
    assert (
        sh.take_stock_snapshot(factory, [], None, t, positions=lambda c, strict: dup, prices=quotes)
        == 2
    )
    aapl = sh.load_rows(factory)[0]
    assert (aapl.quantity, aapl.cost_basis) == (Decimal("20"), Decimal("110"))


def test_load_rows_since(factory):
    args = dict(positions=lambda c, strict: held(), prices=quotes)
    sh.take_stock_snapshot(factory, [], None, datetime(2026, 10, 20, 20, 20, tzinfo=UTC), **args)
    sh.take_stock_snapshot(factory, [], None, datetime(2026, 10, 21, 20, 20, tzinfo=UTC), **args)
    assert len(sh.load_rows(factory)) == 4
    assert {r.day for r in sh.load_rows(factory, date(2026, 10, 21))} == {date(2026, 10, 21)}


def test_has_any(factory):
    from optionsjourney.db.models import StockSnapshot

    assert sh.has_any(factory) is False
    with factory() as session:
        session.add(
            StockSnapshot(
                day=date(2026, 9, 1), account="tt:1", ticker="AAA", quantity=Decimal(1),
                cost_basis=Decimal(1), close=None, taken_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )  # fmt: skip
        session.commit()
    assert sh.has_any(factory) is True
