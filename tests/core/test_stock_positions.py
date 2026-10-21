"""One running position per stock per account (#45)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import EventKind, Status, build_positions
from optionsjourney.core.transactions import Effect, Fill, InstrumentType, Kind, Side

T0 = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
_ids = count(1)
B, S = Side.BUY, Side.SELL


def shares(side, qty, price, days, *, account="5WX01234", symbol="VUG", fees="0"):
    qty, price = Decimal(qty), Decimal(price)
    n = next(_ids)
    return Fill(
        broker="tastytrade",
        account_number=account,
        transaction_id=str(n),
        leg=0,
        executed_at=T0 + timedelta(days=days),
        kind=Kind.TRADE,
        instrument_type=InstrumentType.STOCK,
        symbol=symbol,
        underlying=symbol,
        option=None,
        side=side,
        effect=Effect.OPEN if side is B else Effect.CLOSE,
        quantity=qty,
        price=price,
        value=price * qty * (1 if side is S else -1),
        fees=Decimal(fees),
        order_id=f"o{n}",
    )


def book(*fills):
    return build_positions(group_fills(fills))


def test_buys_add_to_one_position_and_a_sale_spans_them():
    b = book(shares(B, "100", "10", 0), shares(B, "0.5", "11", 1), shares(S, "100.5", "12", 2))
    assert not b.unmatched
    [p] = b.positions
    assert p.status is Status.CLOSED
    assert p.units == Decimal("100.5")
    assert p.realized_pl == Decimal("100.5") * 12 - Decimal("1000") - Decimal("5.5")
    assert [e.kind for e in p.events] == [EventKind.OPEN, EventKind.ADD, EventKind.CLOSE]


def test_adding_after_a_sale_keeps_what_the_sale_made():
    b = book(
        shares(B, "100", "10", 0),
        shares(S, "50", "12", 1),  # +100
        shares(B, "10", "11", 2),  # a dividend reinvestment
        shares(S, "60", "13", 3),  # cost (50 x 10 + 110) = 610: +170
    )
    [p] = b.positions
    assert p.status is Status.CLOSED
    assert p.realized_pl == Decimal("270")
    assert (
        sum((e.realized for e in p.events), Decimal("0")) == p.realized_pl
    )  # the calendar adds up


def test_each_account_has_its_own_position():
    b = book(shares(B, "10", "10", 0), shares(B, "10", "10", 1, account="5WY05678"))
    assert len(b.positions) == 2


def test_selling_everything_then_buying_again_starts_a_new_position():
    b = book(shares(B, "10", "10", 0), shares(S, "10", "11", 1), shares(B, "5", "12", 2))
    assert [p.status for p in b.positions] == [Status.CLOSED, Status.OPEN]


def test_different_stocks_stay_apart():
    b = book(shares(B, "10", "10", 0), shares(B, "10", "10", 1, symbol="QQQ"))
    assert len(b.positions) == 2
