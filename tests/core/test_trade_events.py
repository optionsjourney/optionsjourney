from datetime import timedelta
from decimal import Decimal

from tests.core.test_positions import NOV20, OCT16, T0, B, CLOSE, OPEN, S, book_of, opt

from optionsjourney.core.trade_events import events_of


def test_a_meic_side_stopped_then_its_wing_sold_is_partial_then_closed():
    stop, wing = T0 + timedelta(seconds=30), T0 + timedelta(seconds=40)
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="0.29", order="1"),
        opt(B, OPEN, "PUT", 5790, price="0.04", order="1"),
        opt(B, CLOSE, "PUT", 5800, price="0.34", order="2", at=stop),
        opt(S, CLOSE, "PUT", 5790, price="0.03", order="3", at=wing),
    )
    [position] = book.positions
    events = events_of(position)
    assert [(e.kind, e.at) for e in events] == [("open", T0), ("partial_close", stop),
                                                ("close", wing)]  # fmt: skip
    assert events[0].amount == Decimal("25") and events[1].amount == Decimal("-34")
    assert len({e.dedupe_key for e in events}) == 3


def test_a_partly_closed_trade_has_no_close_yet():
    book = book_of(
        opt(S, OPEN, "PUT", 5800, qty=4, order="1"),
        opt(B, CLOSE, "PUT", 5800, qty=1, order="2", at=T0 + timedelta(hours=1)),
    )
    assert [e.kind for e in events_of(book.positions[0])] == ["open", "partial_close"]


def test_a_roll_is_one_alert_and_the_same_fills_give_the_same_keys():
    fills = (
        opt(S, OPEN, "PUT", 5800, price="3.25", order="1", expiration=OCT16),
        opt(B, CLOSE, "PUT", 5800, price="1.50", order="2", at=T0 + timedelta(days=1), expiration=OCT16),
        opt(S, OPEN, "PUT", 5750, price="3.33", order="2", at=T0 + timedelta(days=1), expiration=NOV20),
    )  # fmt: skip
    events = events_of(book_of(*fills).positions[0])
    assert [e.kind for e in events] == ["open", "roll"]
    assert [e.dedupe_key for e in events] == [e.dedupe_key for e in events_of(book_of(*fills).positions[0])]


# #212 (Tracker2's trade-closed alert): the strikes held at the close, and a line per roll


def test_a_closed_trade_lists_the_strikes_it_held():
    book = book_of(
        opt(S, OPEN, "PUT", 5800, price="0.29", order="1"),
        opt(B, OPEN, "PUT", 5790, price="0.04", order="1"),
        opt(B, CLOSE, "PUT", 5800, price="0.10", order="2", at=T0 + timedelta(hours=1)),
        opt(S, CLOSE, "PUT", 5790, price="0.01", order="2", at=T0 + timedelta(hours=1)),
    )
    events = events_of(book.positions[0])
    assert events[-1].kind == "close"
    assert events[-1].detail == "Strikes: 5800/5790"
    assert events[0].detail == ""  # only the close carries it


def test_a_rolled_trade_lists_the_strikes_at_the_close_and_each_roll():
    roll1, roll2 = T0 + timedelta(days=1), T0 + timedelta(days=8)
    fills = (
        opt(S, OPEN, "PUT", 5800, price="3.25", order="1", expiration=OCT16),
        opt(B, CLOSE, "PUT", 5800, price="1.50", order="2", at=roll1, expiration=OCT16),
        opt(S, OPEN, "PUT", 5750, price="3.33", order="2", at=roll1, expiration=NOV20),
        opt(B, CLOSE, "PUT", 5750, price="1.10", order="3", at=roll2, expiration=NOV20),
        opt(S, OPEN, "PUT", 5712.5, price="2.40", order="3", at=roll2, expiration=NOV20),
        opt(B, CLOSE, "PUT", 5712.5, price="0.20", order="4", at=roll2 + timedelta(days=3),
            expiration=NOV20),
    )  # fmt: skip
    [position] = book_of(*fills).positions
    close = events_of(position)[-1]
    assert close.kind == "close"
    assert close.detail.splitlines() == [
        "Strikes: 5712.5",
        "Rolled: 2026-09-02 5800 → 5750",
        "Rolled: 2026-09-09 5750 → 5712.5",
    ]


def test_a_stock_trade_has_no_strikes():
    from tests.core.test_stock_positions import B as BUY  # noqa: PLC0415
    from tests.core.test_stock_positions import S as SELL  # noqa: PLC0415
    from tests.core.test_stock_positions import book, shares  # noqa: PLC0415

    [position] = book(shares(BUY, "100", "10", 0), shares(SELL, "100", "12", 1)).positions
    assert events_of(position)[-1].detail == ""
