from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import EventKind, Status, build_positions
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    Side,
)

T0 = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)  # 10:00 ET
SEP1 = date(2026, 9, 1)
OCT16 = date(2026, 10, 16)
NOV20 = date(2026, 11, 20)
_ids = count(1)


def opt(
    side,
    effect,
    put_call,
    strike,
    *,
    qty=1,
    price="1.00",
    order="1",
    at=T0,
    expiration=OCT16,
    kind=Kind.TRADE,
    fees="1.00",
    account="5WX01234",
):
    """One option fill. Value follows from price: sold is +, bought is -."""
    qty, price_ = Decimal(qty), (Decimal(price) if price is not None else None)
    value = (
        Decimal("0") if price_ is None else price_ * qty * 100 * (1 if side is Side.SELL else -1)
    )
    return Fill(
        broker="tastytrade",
        account_number=account,
        transaction_id=str(next(_ids)),
        leg=0,
        executed_at=at,
        kind=kind,
        instrument_type=InstrumentType.OPTION,
        symbol=f"SPX {expiration} {put_call[0]}{strike}",
        underlying="SPX",
        option=OptionDetails(put_call, Decimal(strike), expiration),
        side=side,
        effect=effect,
        quantity=qty,
        price=price_,
        value=value,
        fees=Decimal(fees),
        order_id=order,
    )


S, B = Side.SELL, Side.BUY
OPEN, CLOSE = Effect.OPEN, Effect.CLOSE


def book_of(*fills):
    return build_positions(group_fills(fills))


def test_open_and_close_a_put_credit_spread():
    later = T0 + timedelta(days=5)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="3.00", order="1"),
        opt(B, OPEN, "PUT", 5790, price="1.00", order="1"),
        opt(B, CLOSE, "PUT", 5800, price="1.20", order="2", at=later),
        opt(S, CLOSE, "PUT", 5790, price="0.40", order="2", at=later),
    )
    [position] = book.positions
    assert position.name == "SPX Put Credit Spread"
    assert position.status is Status.CLOSED
    assert position.close_reason == "Closed"
    assert position.entry_price == Decimal("2.00")  # 2.00 credit per spread
    assert position.exit_price == Decimal("-0.80")  # 0.80 debit to close
    # +200 credit, -80 to close, $4 fees
    assert position.realized_pl == Decimal("116.00")
    assert position.fees == Decimal("4.00")
    assert position.closed_at == later
    assert not book.unmatched


def test_partial_closes_stay_one_position():
    day2, day3 = T0 + timedelta(days=1), T0 + timedelta(days=2)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, qty=4, price="2.00", order="1", fees="4.00"),
        opt(B, CLOSE, "PUT", 5800, qty=2, price="1.00", order="2", at=day2, fees="2.00"),
        opt(B, CLOSE, "PUT", 5800, qty=2, price="0.50", order="3", at=day3, fees="2.00"),
    )
    [position] = book.positions
    assert position.status is Status.CLOSED
    assert position.units == Decimal("4")
    open_event, first, second = position.events
    # First half: +400 credit for 2, -200 to close, 2 of 4 opening fees + 2 closing
    assert first.realized == Decimal("400") - Decimal("200") - Decimal("2") - Decimal("2")
    # Second half: +400, -100, 2 + 2 fees
    assert second.realized == Decimal("400") - Decimal("100") - Decimal("2") - Decimal("2")
    assert position.realized_pl == first.realized + second.realized
    assert position.closed_at == day3


def test_partly_closed_position_is_partial():
    book = book_of(
        opt(S, OPEN, "PUT", 5800, qty=4, order="1"),
        opt(B, CLOSE, "PUT", 5800, qty=1, order="2", at=T0 + timedelta(hours=1)),
    )
    [position] = book.positions
    assert position.status is Status.PARTIAL
    assert position.closed_at is None


def test_expiration_closes_worthless():
    expiry = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="2.00", order="1"),
        opt(
            B, CLOSE, "PUT", 5800, price=None, order=None, at=expiry, kind=Kind.EXPIRATION, fees="0"
        ),
    )
    [position] = book.positions
    assert position.status is Status.CLOSED
    assert position.close_reason == "Expired"
    assert position.realized_pl == Decimal("199.00")
    assert position.events[-1].kind is EventKind.EXPIRATION


def test_assignment_closes_the_option():
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="2.00", order="1"),
        opt(
            B,
            CLOSE,
            "PUT",
            5800,
            price=None,
            order=None,
            kind=Kind.ASSIGNMENT,
            fees="0",
            at=datetime(2026, 10, 16, 21, 0, tzinfo=UTC),
        ),
    )
    assert book.positions[0].close_reason == "Assigned"


def test_roll_keeps_one_position():
    roll_at = T0 + timedelta(days=10)
    close_at = T0 + timedelta(days=20)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="3.25", order="1", expiration=OCT16),
        # Roll: buy back the October put, sell a November one, in one order
        opt(B, CLOSE, "PUT", 5800, price="1.50", order="2", at=roll_at, expiration=OCT16),
        opt(S, OPEN, "PUT", 5750, price="3.33", order="2", at=roll_at, expiration=NOV20),
        opt(B, CLOSE, "PUT", 5750, price="1.00", order="3", at=close_at, expiration=NOV20),
    )
    [position] = book.positions
    assert position.roll_count == 1
    assert position.original_entry_price == Decimal("3.25")
    assert position.entry_price == Decimal("5.08")  # 3.25 + (3.33 - 1.50)
    assert [e.kind for e in position.events] == [EventKind.OPEN, EventKind.ROLL, EventKind.CLOSE]
    assert position.status is Status.CLOSED
    # All three legs' cash: +325 - 150 + 333 - 100, less 4 fills x $1
    assert position.realized_pl == Decimal("404.00")
    assert position.name == "SPX Short Put"
    assert position.legs[0].rolled


def test_0dte_is_never_rolled():
    morning = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    noon = datetime(2026, 9, 1, 16, 0, tzinfo=UTC)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, order="1", at=morning, expiration=SEP1),
        opt(B, CLOSE, "PUT", 5800, order="2", at=noon, expiration=SEP1),
        opt(S, OPEN, "PUT", 5750, order="2", at=noon, expiration=SEP1),
    )
    assert len(book.positions) == 2
    first, second = book.positions
    assert first.is_0dte
    assert first.status is Status.CLOSED and first.roll_count == 0
    assert second.status is Status.OPEN


def test_partial_roll_is_not_a_roll():
    """Closing only part of a leg while opening others is a close plus a new
    position, as in Tracker2."""
    roll_at = T0 + timedelta(days=10)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, qty=2, order="1"),
        opt(B, CLOSE, "PUT", 5800, qty=1, order="2", at=roll_at),
        opt(S, OPEN, "PUT", 5750, qty=1, order="2", at=roll_at, expiration=NOV20),
    )
    assert len(book.positions) == 2
    assert book.positions[0].status is Status.PARTIAL


def test_closes_match_the_oldest_position_first():
    book = book_of(
        opt(S, OPEN, "PUT", 5800, order="1", at=T0),
        opt(S, OPEN, "PUT", 5800, order="2", at=T0 + timedelta(hours=1)),
        opt(B, CLOSE, "PUT", 5800, order="3", at=T0 + timedelta(hours=2)),
    )
    oldest, newest = book.positions
    assert oldest.status is Status.CLOSED
    assert newest.status is Status.OPEN


def test_close_without_an_open_is_unmatched():
    book = book_of(opt(B, CLOSE, "PUT", 5800, order="9"))
    assert book.positions == []
    assert [order.order_id for order in book.unmatched] == ["9"]


def test_close_spanning_two_positions_closes_both():
    """One order closing legs of two positions (a combined close of two
    iron condors' wings, say) closes both, oldest first, when every contract
    fits. Tracker2 left these for the user to match by hand."""
    book = book_of(
        opt(S, OPEN, "PUT", 5800, order="1"),
        opt(S, OPEN, "PUT", 5800, order="2", at=T0 + timedelta(minutes=1)),
        opt(B, CLOSE, "PUT", 5800, qty=2, order="3", at=T0 + timedelta(hours=1)),
    )
    assert not book.unmatched
    assert all(p.status is Status.CLOSED for p in book.positions)


def test_close_that_does_not_fully_fit_is_unmatched():
    book = book_of(
        opt(S, OPEN, "PUT", 5800, order="1"),
        opt(B, CLOSE, "PUT", 5800, qty=3, order="3", at=T0 + timedelta(hours=1)),
    )
    assert len(book.unmatched) == 1
    assert book.positions[0].status is Status.OPEN


def test_close_in_another_account_does_not_match():
    book = book_of(
        opt(S, OPEN, "PUT", 5800, order="1", account="5WX01234"),
        opt(B, CLOSE, "PUT", 5800, order="2", account="5WY05678", at=T0 + timedelta(hours=1)),
    )
    assert book.positions[0].status is Status.OPEN
    assert len(book.unmatched) == 1


def test_calendar_with_expired_front_month_counts_both_legs():
    """Tracker2 dropped the expired leg's P/L here; we don't."""
    front_expiry = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="2.00", order="1", expiration=OCT16),
        opt(B, OPEN, "PUT", 5800, price="5.00", order="1", expiration=NOV20),
        opt(
            B,
            CLOSE,
            "PUT",
            5800,
            price=None,
            order=None,
            at=front_expiry,
            expiration=OCT16,
            kind=Kind.EXPIRATION,
            fees="0",
        ),
        opt(
            S,
            CLOSE,
            "PUT",
            5800,
            price="4.00",
            order="3",
            at=front_expiry + timedelta(days=3),
            expiration=NOV20,
        ),
    )
    [position] = book.positions
    assert position.name == "SPX Put Calendar"
    assert position.status is Status.CLOSED
    # +200 - 500 + 0 + 400, less 3 fills x $1
    assert position.realized_pl == Decimal("97.00")


def test_unlabeled_trades_close_when_they_can():
    """Some broker trades don't say open or close: a trade that offsets an
    open position closes it, otherwise it opens one."""
    book = book_of(
        opt(S, None, "PUT", 5800, order="1"),
        opt(B, None, "PUT", 5800, order="2", at=T0 + timedelta(hours=1)),
    )
    [position] = book.positions
    assert position.status is Status.CLOSED


def test_more_than_is_left_never_over_closes():
    book = book_of(
        opt(S, OPEN, "PUT", 5800, qty=1, order="1"),
        opt(B, CLOSE, "PUT", 5800, qty=1, order="2", at=T0 + timedelta(hours=1)),
        opt(B, CLOSE, "PUT", 5800, qty=1, order="3", at=T0 + timedelta(hours=2)),
    )
    [position] = book.positions
    assert position.legs[0].closed == Decimal("1")
    assert len(book.unmatched) == 1


def test_unknown_broker_events_are_shown_not_applied():
    """E.g. a corporate action on shares: listed for the user, never applied."""
    book = book_of(
        opt(S, OPEN, "PUT", 5800, order="1"),
        opt(B, CLOSE, "PUT", 5800, order=None, kind=Kind.OTHER, at=T0 + timedelta(days=1)),
    )
    assert book.positions[0].status is Status.OPEN
    assert [o.kind for o in book.unmatched] == [Kind.OTHER]


def test_one_expiration_record_settles_many_positions():
    """Brokers report everything expiring in an account at once."""
    expiry = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, order="1", at=T0),
        opt(B, OPEN, "PUT", 5790, order="1", at=T0),
        opt(S, OPEN, "PUT", 5800, order="2", at=T0 + timedelta(hours=1)),
        opt(B, OPEN, "PUT", 5790, order="2", at=T0 + timedelta(hours=1)),
        # One record: 2 of each strike expire, across both positions
        opt(
            B,
            CLOSE,
            "PUT",
            5800,
            qty=2,
            price=None,
            order=None,
            kind=Kind.EXPIRATION,
            at=expiry,
            fees="0",
        ),
        opt(
            S,
            CLOSE,
            "PUT",
            5790,
            qty=2,
            price=None,
            order=None,
            kind=Kind.EXPIRATION,
            at=expiry,
            fees="0",
        ),
    )
    assert not book.unmatched
    assert [p.close_reason for p in book.positions] == ["Expired", "Expired"]


def test_cash_settled_assignment_counts_once():
    """TastyTrade: a removal row and a cash-settlement row, neither with a
    direction. The contract closes once and the cash counts once."""
    expiry = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)
    removal = opt(
        S, OPEN, "CALL", 6000, price=None, order=None, kind=Kind.ASSIGNMENT, at=expiry, fees="0"
    )
    removal = replace(removal, side=None, effect=None)
    cash = replace(removal, transaction_id="cash-1", value=Decimal("-350"), fees=Decimal("0"))
    book = book_of(opt(S, OPEN, "CALL", 6000, price="2.00", order="1"), removal, cash)
    [position] = book.positions
    assert not book.unmatched
    assert position.close_reason == "Assigned"
    assert position.legs[0].closed == Decimal("1")
    assert position.realized_pl == Decimal("200") - Decimal("350") - Decimal("1")


def test_broker_correction_at_the_same_moment_nets_out():
    """Schwab can post an open and a close for the same contract at the same
    second; the open must be in place for the close."""
    moment = T0 + timedelta(days=2)
    book = book_of(
        opt(B, CLOSE, "PUT", 5800, order=None, at=moment, price="1.00"),
        opt(S, OPEN, "PUT", 5800, order=None, at=moment, price="1.00"),
    )
    assert not book.unmatched
    assert book.positions[0].status is Status.CLOSED


def test_an_error_in_one_record_does_not_hide_the_rest(monkeypatch):
    from optionsjourney.core import positions as module

    real_open = module._open

    def flaky_open(book, order):
        if order.order_id == "2":
            raise RuntimeError("boom")
        return real_open(book, order)

    monkeypatch.setattr(module, "_open", flaky_open)
    book = book_of(opt(S, OPEN, "PUT", 5800, order="1"), opt(S, OPEN, "PUT", 5700, order="2"))
    assert len(book.positions) == 1
    assert [o.order_id for o in book.failed] == ["2"]


# --- Expired, waiting on the broker's record ----------------------------------
# A position whose options have all expired stays open until the broker posts
# its expiration record, which can take a day or more (#2).

SEP1_CLOSE = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)  # 16:00 ET, when Sep 1 options stop trading


def _spread_expiring(expiration):
    return book_of(
        opt(S, OPEN, "PUT", 5800, price="3.00", expiration=expiration),
        opt(B, OPEN, "PUT", 5790, price="1.00", expiration=expiration),
    ).positions[0]


def test_not_awaiting_expiration_before_the_close():
    position = _spread_expiring(SEP1)
    assert not position.awaiting_expiration(SEP1_CLOSE - timedelta(minutes=1))


def test_awaiting_expiration_from_the_close_on_expiry_day():
    position = _spread_expiring(SEP1)
    assert position.status is Status.OPEN
    assert position.awaiting_expiration(SEP1_CLOSE)
    assert position.awaiting_expiration(SEP1_CLOSE + timedelta(days=3))


def test_a_closed_position_is_not_awaiting_expiration():
    later = T0 + timedelta(hours=1)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="3.00", order="1", expiration=SEP1),
        opt(B, CLOSE, "PUT", 5800, price="1.00", order="2", at=later, expiration=SEP1),
    )
    assert not book.positions[0].awaiting_expiration(SEP1_CLOSE + timedelta(days=1))


def test_partly_closed_and_the_rest_expired_is_awaiting_expiration():
    later = T0 + timedelta(hours=1)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="3.00", order="1", qty=2, expiration=SEP1),
        opt(B, CLOSE, "PUT", 5800, price="1.00", order="2", at=later, expiration=SEP1),
    )
    position = book.positions[0]
    assert position.status is Status.PARTIAL
    assert position.awaiting_expiration(SEP1_CLOSE)


def test_a_leg_still_trading_means_not_awaiting_expiration():
    # A calendar: the front leg expired, the back leg trades on
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="3.00", expiration=SEP1),
        opt(B, OPEN, "PUT", 5800, price="5.00", expiration=OCT16),
    )
    assert not book.positions[0].awaiting_expiration(SEP1_CLOSE + timedelta(days=1))


def test_a_close_its_trade_sent_closes_that_trade_not_an_older_one_with_the_same_strikes():
    later, stop = T0 + timedelta(minutes=10), T0 + timedelta(minutes=11)
    fills = (
        opt(S, OPEN, "PUT", 5800, order="1"), opt(B, OPEN, "PUT", 5790, order="1"),
        opt(S, OPEN, "PUT", 5800, order="2", at=later), opt(B, OPEN, "PUT", 5790, order="2", at=later),
        opt(B, CLOSE, "PUT", 5800, order="3", at=stop), opt(S, CLOSE, "PUT", 5790, order="3", at=stop),
    )  # fmt: skip
    oldest_first = build_positions(group_fills(fills))
    assert [p.status for p in oldest_first.positions] == [Status.CLOSED, Status.OPEN]
    owned = build_positions(group_fills(fills), owners={"3": "tastytrade:5WX01234:2"})
    assert [p.status for p in owned.positions] == [Status.OPEN, Status.CLOSED]


def test_one_right_names_a_puts_only_or_calls_only_trade():
    """#280 (Tracker2's put_call_chip)."""
    spread = book_of(
        opt(S, OPEN, "PUT", 5800, order="1"), opt(B, OPEN, "PUT", 5780, order="1")
    ).positions[0]
    assert spread.one_right == "Put"
    calls = book_of(opt(S, OPEN, "CALL", 6000, order="1")).positions[0]
    assert calls.one_right == "Call"
    condor = book_of(
        opt(S, OPEN, "PUT", 5800, order="1"), opt(S, OPEN, "CALL", 6000, order="1")
    ).positions[0]
    assert condor.one_right is None
