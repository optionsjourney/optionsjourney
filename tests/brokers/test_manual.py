"""Trades entered by hand (#9, docs/decisions/0023): stored like broker
transactions, read into fills like any broker's."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from optionsjourney.brokers.manual import ManualEntryError, build_entry, parse_transaction
from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import Status, build_positions
from optionsjourney.core.transactions import Effect, InstrumentType, Kind, Side

AT = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)


def put_spread(effect="open", sell_price="2.00", buy_price="1.00", at=AT, fees="2.60"):
    short_side, long_side = ("sell", "buy") if effect == "open" else ("buy", "sell")
    return build_entry(
        executed_at=at,
        effect=effect,
        fees=fees,
        legs=[
            {
                "type": "put",
                "side": short_side,
                "quantity": "1",
                "underlying": "spx",
                "strike": "5800",
                "expiration": "2026-10-16",
                "price": sell_price,
            },
            {
                "type": "put",
                "side": long_side,
                "quantity": "1",
                "underlying": "SPX",
                "strike": "5790",
                "expiration": "2026-10-16",
                "price": buy_price,
            },
        ],
    )


def test_an_entry_reads_as_fills():
    fills = parse_transaction(put_spread(), "M001")
    assert len(fills) == 2
    short, long = fills
    assert (short.broker, short.account_number) == ("manual", "M001")
    assert (short.kind, short.instrument_type) == (Kind.TRADE, InstrumentType.OPTION)
    assert (short.side, short.effect) == (Side.SELL, Effect.OPEN)
    assert short.symbol == "SPX   261016P05800000"
    assert short.underlying == "SPX"
    assert short.option.strike == Decimal("5800")
    assert short.value == Decimal("200")  # 2.00 x 1 x 100, received
    assert long.value == Decimal("-100")
    assert short.fees == Decimal("2.60") and long.fees == Decimal("0")  # fees on the first leg
    assert short.order_id == long.order_id


def test_opening_and_closing_by_hand_makes_a_closed_trade():
    opened = put_spread()
    closed = put_spread(
        "close", "0.50", "0.10", at=datetime(2026, 9, 2, 14, 0, tzinfo=UTC), fees="0"
    )
    fills = parse_transaction(opened, "M001") + parse_transaction(closed, "M001")
    [position] = build_positions(group_fills(fills)).positions
    assert position.status is Status.CLOSED
    # +200 - 100 - 50 + 10 - 2.60 fees
    assert position.realized_pl == Decimal("57.40")


def test_marking_expired_closes_whatever_is_left():
    opened = put_spread()
    expired = build_entry(
        executed_at=datetime(2026, 10, 16, 20, 0, tzinfo=UTC),
        effect="expire",
        fees="0",
        legs=[
            {
                "type": "put",
                "side": "buy",
                "quantity": "1",
                "underlying": "SPX",
                "strike": "5800",
                "expiration": "2026-10-16",
                "price": "0",
            },
            {
                "type": "put",
                "side": "sell",
                "quantity": "1",
                "underlying": "SPX",
                "strike": "5790",
                "expiration": "2026-10-16",
                "price": "0",
            },
        ],
    )
    fills = parse_transaction(opened, "M001") + parse_transaction(expired, "M001")
    assert {f.kind for f in fills[2:]} == {Kind.EXPIRATION}
    [position] = build_positions(group_fills(fills)).positions
    assert position.status is Status.CLOSED
    assert position.close_reason == "Expired"
    assert position.realized_pl == Decimal("97.40")


def test_shares():
    entry = build_entry(
        executed_at=AT,
        effect="open",
        fees="0",
        legs=[
            {
                "type": "shares",
                "side": "buy",
                "quantity": "100",
                "underlying": "aapl",
                "price": "230.50",
            }
        ],
    )
    [fill] = parse_transaction(entry, "M001")
    assert fill.instrument_type is InstrumentType.STOCK
    assert fill.option is None
    assert (fill.symbol, fill.underlying) == ("AAPL", "AAPL")
    assert fill.value == Decimal("-23050.00")


@pytest.mark.parametrize(
    ("leg", "message"),
    [
        (
            {
                "type": "put",
                "side": "sell",
                "quantity": "1",
                "underlying": "SPX",
                "strike": "",
                "expiration": "2026-10-16",
                "price": "1",
            },
            "strike",
        ),
        (
            {
                "type": "put",
                "side": "sell",
                "quantity": "0",
                "underlying": "SPX",
                "strike": "5800",
                "expiration": "2026-10-16",
                "price": "1",
            },
            "quantity",
        ),
        (
            {
                "type": "put",
                "side": "sell",
                "quantity": "1",
                "underlying": "",
                "strike": "5800",
                "expiration": "2026-10-16",
                "price": "1",
            },
            "underlying",
        ),
        (
            {
                "type": "put",
                "side": "sell",
                "quantity": "1",
                "underlying": "SPX",
                "strike": "5800",
                "expiration": "16/10/2026",
                "price": "1",
            },
            "expiration",
        ),
        (
            {
                "type": "put",
                "side": "sell",
                "quantity": "1",
                "underlying": "SPX",
                "strike": "5800",
                "expiration": "2026-10-16",
                "price": "-1",
            },
            "price",
        ),
        (
            {"type": "bond", "side": "sell", "quantity": "1", "underlying": "SPX", "price": "1"},
            "type",
        ),
    ],
)
def test_bad_legs_are_refused_with_a_reason(leg, message):
    with pytest.raises(ManualEntryError, match=message):
        build_entry(executed_at=AT, effect="open", fees="0", legs=[leg])


def test_an_entry_needs_a_leg():
    with pytest.raises(ManualEntryError, match="leg"):
        build_entry(executed_at=AT, effect="open", fees="0", legs=[])


def test_expiration_dates_are_kept_exactly():
    [fill, _] = parse_transaction(put_spread(), "M001")
    assert fill.option.expiration == date(2026, 10, 16)
