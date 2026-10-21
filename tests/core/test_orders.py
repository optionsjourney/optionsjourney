from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from optionsjourney.core.orders import group_fills
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    Side,
)

OPEN_TIME = datetime(2026, 10, 9, 13, 32, tzinfo=UTC)
_ids = iter(range(1, 1000))


def fill(
    side,
    effect,
    put_call,
    strike,
    quantity=1,
    order_id="100",
    kind=Kind.TRADE,
    when=OPEN_TIME,
    value=0,
    account="5WX01234",
):
    return Fill(
        broker="tastytrade",
        account_number=account,
        transaction_id=str(next(_ids)),
        leg=0,
        executed_at=when,
        kind=kind,
        instrument_type=InstrumentType.OPTION,
        symbol=f"SPXW {put_call[0]}{strike}",
        underlying="SPX",
        option=OptionDetails(put_call, Decimal(strike), date(2026, 10, 9)),
        side=side,
        effect=effect,
        quantity=Decimal(quantity),
        price=Decimal("1"),
        value=Decimal(value),
        fees=Decimal("0.5"),
        order_id=order_id,
    )


def iron_condor(effect, order_id, when=OPEN_TIME):
    """Opening sells the inner strikes; closing buys them back."""
    sell, buy = (Side.SELL, Side.BUY) if effect is Effect.OPEN else (Side.BUY, Side.SELL)
    return [
        fill(buy, effect, "PUT", 5780, order_id=order_id, when=when, value=-50),
        fill(sell, effect, "PUT", 5800, order_id=order_id, when=when, value=150),
        fill(sell, effect, "CALL", 6000, order_id=order_id, when=when, value=140),
        fill(buy, effect, "CALL", 6020, order_id=order_id, when=when, value=-40),
    ]


def test_one_order_is_one_group():
    [order] = group_fills(iron_condor(Effect.OPEN, "100"))
    assert order.name == "SPX Iron Condor"
    assert order.action_label == "Open"
    assert order.value == Decimal("200")
    assert order.fees == Decimal("2.0")
    assert len(order.legs) == 4


def test_closing_order_is_named_after_the_position_it_closes():
    later = OPEN_TIME + timedelta(hours=3)
    orders = group_fills(iron_condor(Effect.OPEN, "100") + iron_condor(Effect.CLOSE, "200", later))
    assert [(o.name, o.action_label) for o in orders] == [
        ("SPX Iron Condor", "Close"),  # newest first
        ("SPX Iron Condor", "Open"),
    ]


def test_roll_splits_into_close_and_open():
    roll = [
        fill(Side.BUY, Effect.CLOSE, "PUT", 5800, order_id="300"),
        fill(Side.SELL, Effect.OPEN, "PUT", 5750, order_id="300"),
    ]
    orders = group_fills(roll)
    assert sorted((o.action_label, o.structure) for o in orders) == [
        ("Close", "Short Put"),
        ("Open", "Short Put"),
    ]


def test_partial_fills_of_a_leg_add_up():
    partials = [
        fill(Side.SELL, Effect.OPEN, "PUT", 5800, quantity=2),
        fill(Side.SELL, Effect.OPEN, "PUT", 5800, quantity=1),
        fill(Side.BUY, Effect.OPEN, "PUT", 5790, quantity=3),
    ]
    [order] = group_fills(partials)
    assert order.structure == "Put Credit Spread"
    assert sorted(order.leg_label(leg) for leg in order.legs) == [
        "+3 SPX 10/09/26 5790 Put",
        "-3 SPX 10/09/26 5800 Put",
    ]


def test_expirations_group_by_time_and_underlying():
    expiry = datetime(2026, 10, 9, 21, 0, tzinfo=UTC)
    expired = [
        fill(Side.BUY, Effect.CLOSE, "PUT", 5800, order_id=None, kind=Kind.EXPIRATION, when=expiry),
        fill(
            Side.SELL, Effect.CLOSE, "PUT", 5790, order_id=None, kind=Kind.EXPIRATION, when=expiry
        ),
    ]
    [order] = group_fills(expired)
    assert order.action_label == "Expiration"
    assert order.name == "SPX Put Credit Spread"


def test_same_order_id_in_different_accounts_stays_apart():
    fills = [
        fill(Side.SELL, Effect.OPEN, "PUT", 5800, account="5WX01234"),
        fill(Side.SELL, Effect.OPEN, "PUT", 5800, account="5WY05678"),
    ]
    assert len(group_fills(fills)) == 2
