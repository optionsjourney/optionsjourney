"""What each row of the trade list shows (#61)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import build_positions
from optionsjourney.core.symbols import occ_symbol
from optionsjourney.core.trade_rows import row_details
from optionsjourney.core.transactions import Effect, Fill, InstrumentType, Kind, OptionDetails, Side

AT = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
EXP = date(2026, 10, 16)
_ids = count(1)
S, B = Side.SELL, Side.BUY


def fill(side, effect, strike, *, qty=1, price, order, at=AT, fees="1.00"):
    qty, price = Decimal(qty), Decimal(price)
    option = OptionDetails("PUT", Decimal(strike), EXP)
    return Fill(
        broker="tastytrade", account_number="5WX01234", transaction_id=str(next(_ids)), leg=0,
        executed_at=at, kind=Kind.TRADE, instrument_type=InstrumentType.OPTION,
        symbol=occ_symbol("SPXW", option), underlying="SPX", option=option, side=side,
        effect=effect, quantity=qty, price=price,
        value=price * qty * 100 * (1 if side is S else -1), fees=Decimal(fees), order_id=order,
    )  # fmt: skip


def test_a_partly_closed_spread():
    later = AT + timedelta(days=1)
    [p] = build_positions(
        group_fills(
            [
                fill(S, Effect.OPEN, 5800, qty=2, price="3.00", order="1"),
                fill(B, Effect.OPEN, 5790, qty=2, price="1.00", order="1"),
                fill(B, Effect.CLOSE, 5800, price="1.50", order="2", at=later),
                fill(S, Effect.CLOSE, 5790, price="0.40", order="2", at=later),
            ]
        )
    ).positions
    row = row_details(p)
    assert [(leg.quantity, leg.label) for leg in row.open_legs] == [
        (Decimal("-1"), "5800 P 10/16"),
        (Decimal("1"), "5790 P 10/16"),
    ]
    assert row.open_legs[0].symbol == "SPXW  261016P05800000"
    assert [(c.quantity, c.label, c.price) for c in row.closing] == [
        (Decimal("1"), "5800 P 10/16", Decimal("1.50")),
        (Decimal("-1"), "5790 P 10/16", Decimal("0.40")),
    ]
    [order] = row.closing_orders  # #152: closing legs grouped by their order
    assert order.at == later and order.kind == "close" and order.legs == row.closing
    assert row.premium == Decimal("400") and row.credit
    assert (row.fees_open, row.fees_close) == (Decimal("2.00"), Decimal("2.00"))
    # P/L % is the realized P/L over the 400 premium
    assert row.realized_percent == p.realized_pl / Decimal("400") * 100


# --- Bots slice 20: the record's P/L % and days in trade (Tracker2's _trade_row) -------


def _spread(closed_at=None):
    fills = [fill(S, Effect.OPEN, 5800, price="3.00", order="1"),
             fill(B, Effect.OPEN, 5790, price="1.00", order="1")]  # fmt: skip
    if closed_at is not None:
        fills += [fill(B, Effect.CLOSE, 5800, price="1.50", order="2", at=closed_at),
                  fill(S, Effect.CLOSE, 5790, price="0.50", order="2", at=closed_at)]  # fmt: skip
    [p] = build_positions(group_fills(fills)).positions
    return p


def test_days_in_trade_counts_new_york_days():
    from optionsjourney.core.trade_rows import days_in_trade

    # 10:00 New York on the 9th, closed 23:30 UTC on the 12th = 19:30 New York on the 12th
    closed = _spread(datetime(2026, 10, 12, 23, 30, tzinfo=UTC))
    assert days_in_trade(closed) == 3
    same_day = _spread(AT + timedelta(hours=5))
    assert days_in_trade(same_day) == 0
    still_open = _spread()
    assert days_in_trade(still_open) is None
    assert days_in_trade(still_open, now=datetime(2026, 10, 14, 1, 0, tzinfo=UTC)) == 4


def test_realized_percent_is_the_journals():
    from optionsjourney.core.trade_rows import realized_percent

    closed = _spread(AT + timedelta(hours=1))
    assert realized_percent(closed) == row_details(closed).realized_percent
    assert realized_percent(closed) is not None
    assert realized_percent(_spread()) is None
