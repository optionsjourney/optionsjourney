"""Estimating what an expired trade made before the broker posts its
expiration record (#23)."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count

from optionsjourney.core.expiry import estimate_expiration
from optionsjourney.core.expiry_prices import PriceRead
from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import build_positions
from optionsjourney.core.symbols import normalize_underlying, occ_symbol
from optionsjourney.core.transactions import (
    Effect,
    Fill,
    InstrumentType,
    Kind,
    OptionDetails,
    Side,
)

FRI = date(2026, 10, 9)  # a weekly Friday
THIRD_FRI = date(2026, 10, 16)  # the monthly expiration
AT = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
_ids = count(1)


def opt(side, put_call, strike, price, *, root="SPXW", expiration=FRI, qty=1):
    option = OptionDetails(put_call, Decimal(strike), expiration)
    qty, price = Decimal(qty), Decimal(price)
    return Fill(
        broker="tastytrade",
        account_number="5WX01234",
        transaction_id=str(next(_ids)),
        leg=0,
        executed_at=AT,
        kind=Kind.TRADE,
        instrument_type=InstrumentType.OPTION,
        symbol=occ_symbol(root, option),
        underlying=normalize_underlying(root),
        option=option,
        side=side,
        effect=Effect.OPEN,
        quantity=qty,
        price=price,
        value=price * qty * 100 * (1 if side is Side.SELL else -1),
        fees=Decimal("0"),
        order_id="1",
    )


S, B = Side.SELL, Side.BUY


def position(*fills):
    [p] = build_positions(group_fills(fills)).positions
    return p


def put_spread(**kw):
    # 2.00 credit for the 5800 / 1.00 debit for the 5790: $100 credit
    return position(opt(S, "PUT", 5800, "2.00", **kw), opt(B, "PUT", 5790, "1.00", **kw))


def test_out_of_the_money_keeps_the_credit():
    assert estimate_expiration(put_spread(), {("SPX", FRI): Decimal("5850")}) == Decimal("100")


def test_in_the_money_pays_the_intrinsic_value():
    # SPX closes at 5795: the short 5800 put is worth 5 ($500), the long 5790 nothing
    assert estimate_expiration(put_spread(), {("SPX", FRI): Decimal("5795")}) == Decimal("-400")


def test_through_both_strikes_loses_the_width():
    assert estimate_expiration(put_spread(), {("SPX", FRI): Decimal("5700")}) == Decimal("-900")


def test_no_close_no_estimate():
    assert estimate_expiration(put_spread(), {}) is None


def test_monthly_spx_settles_on_the_next_morning_so_no_estimate():
    monthly = put_spread(root="SPX", expiration=THIRD_FRI)
    assert estimate_expiration(monthly, {("SPX", THIRD_FRI): Decimal("5850")}) is None


def test_weekly_spxw_on_the_third_friday_is_still_priced():
    weekly = put_spread(root="SPXW", expiration=THIRD_FRI)
    assert estimate_expiration(weekly, {("SPX", THIRD_FRI): Decimal("5850")}) == Decimal("100")


def test_a_stock_option_out_of_the_money_expires_worthless():
    short_put = position(opt(S, "PUT", 150, "1.20", root="AAPL"))
    assert estimate_expiration(short_put, {("AAPL", FRI): Decimal("160")}) == Decimal("120")


def test_a_stock_option_in_the_money_is_assigned_so_no_estimate():
    short_put = position(opt(S, "PUT", 150, "1.20", root="AAPL"))
    assert estimate_expiration(short_put, {("AAPL", FRI): Decimal("140")}) is None


def test_xsp_uses_its_own_close():
    xsp = position(opt(S, "CALL", 790, "0.50", root="XSP"))
    assert estimate_expiration(xsp, {("XSP", FRI): Decimal("791.15")}) == Decimal("-65")  # 50 - 115


# --- Settling at the close (#147) ----------------------------------------------------

from optionsjourney.core.expiry import (  # noqa: E402
    EXERCISE_FEE,
    is_settlement,
    settle_expiring,
    settled_by_app,
    with_settlements,
)
from optionsjourney.core.positions import Status  # noqa: E402


def _rebuilt(fills, settlements):
    [p] = build_positions(group_fills(with_settlements(list(fills), settlements))).positions
    return p


def _spread_fills(**kw):
    return [opt(S, "PUT", 5800, "2.00", **kw), opt(B, "PUT", 5790, "1.00", **kw)]


def test_out_of_the_money_settles_at_zero_and_closes_the_trade():
    fills = _spread_fills()
    rows, skipped = settle_expiring(
        [position(*fills)], FRI, {("SPX", FRI): PriceRead(Decimal("5850"), "test", True)}
    )
    assert skipped == []
    assert {(r.sign, r.quantity, r.value, r.fee) for r in rows} == {
        (-1, 1, 0, 0),
        (1, 1, 0, 0),
    }
    p = _rebuilt(fills, rows)
    assert p.status is Status.CLOSED
    assert p.close_reason == "Expired"
    assert p.realized_pl == Decimal("100")
    assert settled_by_app(p)


def test_in_the_money_short_pays_long_receives_plus_the_exercise_fee():
    fills = _spread_fills()
    rows, _ = settle_expiring(
        [position(*fills)], FRI, {("SPX", FRI): PriceRead(Decimal("5795"), "test", True)}
    )
    assert sorted(r.value for r in rows) == [Decimal("-500"), Decimal("0")]
    assert sum(r.fee for r in rows) == EXERCISE_FEE  # once per trade, TastyTrade
    assert _rebuilt(fills, rows).realized_pl == Decimal("100") - 500 - EXERCISE_FEE


def test_only_whats_left_settles():
    fills = [
        opt(S, "PUT", 5800, "2.00", qty=2),
        replace(
            opt(B, "PUT", 5800, "0.50"), effect=Effect.CLOSE, order_id="2", transaction_id="c1"
        ),
    ]
    rows, _ = settle_expiring(
        [position(*fills)], FRI, {("SPX", FRI): PriceRead(Decimal("5850"), "test", True)}
    )
    assert [r.quantity for r in rows] == [1]
    p = _rebuilt(fills, rows)
    assert p.status is Status.CLOSED and p.realized_pl == Decimal("350")


def test_a_prelim_price_marks_the_settlement_prelim():
    p = position(*_spread_fills())
    read = PriceRead(Decimal("5850"), "tastytrade last", False)
    rows, _ = settle_expiring([p], FRI, {("SPX", FRI): read})
    assert {(r.status, r.source) for r in rows} == {("prelim", "tastytrade last")}


def test_skips_carry_reasons():
    am = position(*_spread_fills(root="SPX", expiration=THIRD_FRI))
    stock_itm = position(opt(S, "PUT", 250, "2.00", root="AAPL"))
    no_close = position(opt(S, "PUT", 500, "2.00", root="QQQ"))
    unmatched = position(opt(S, "PUT", 5000, "2.00"))
    closes = {
        ("SPX", THIRD_FRI): PriceRead(Decimal("5850"), "test", True),
        ("AAPL", FRI): PriceRead(Decimal("240"), "test", True),
        ("SPX", FRI): PriceRead(Decimal("5850"), "test", True),
    }
    held = {("tastytrade", "5WX01234", unmatched.legs[0].symbol)}
    rows, skipped = settle_expiring([stock_itm, no_close, unmatched], FRI, closes, held)
    assert rows == []
    reasons = " | ".join(s.reason for s in skipped)
    assert "becomes shares" in reasons and "no close for QQQ" in reasons
    assert "unmatched close" in reasons
    _, [am_skip] = settle_expiring([am], THIRD_FRI, closes)
    assert "next morning" in am_skip.reason


def test_futures_options_are_skipped():
    fill = replace(opt(S, "PUT", 5800, "2.00"), instrument_type=InstrumentType.FUTURE_OPTION)
    _, [skip] = settle_expiring(
        [position(fill)], FRI, {("SPX", FRI): PriceRead(Decimal("5850"), "test", True)}
    )
    assert "futures" in skip.reason


def test_out_of_the_money_stock_options_settle():
    p = position(opt(S, "PUT", 250, "2.00", root="AAPL"))
    rows, skipped = settle_expiring(
        [p], FRI, {("AAPL", FRI): PriceRead(Decimal("260"), "test", True)}
    )
    assert skipped == [] and len(rows) == 1


def test_other_days_are_left_alone():
    rows, skipped = settle_expiring(
        [position(*_spread_fills())],
        THIRD_FRI,
        {("SPX", THIRD_FRI): PriceRead(Decimal("1"), "test", True)},
    )
    assert rows == [] and skipped == []


def test_the_brokers_record_replaces_the_settlement():
    fills = _spread_fills()
    rows, _ = settle_expiring(
        [position(*fills)], FRI, {("SPX", FRI): PriceRead(Decimal("5850"), "test", True)}
    )
    broker = [
        replace(
            f,
            kind=Kind.EXPIRATION,
            effect=Effect.CLOSE,
            side=B if f.side is S else S,
            transaction_id=f"x{i}",
            order_id=None,
            price=None,
            value=Decimal("0"),
            executed_at=datetime(2026, 10, 10, 12, tzinfo=UTC),
        )
        for i, f in enumerate(fills)
    ]
    merged = with_settlements([*fills, *broker], rows)
    assert not any(is_settlement(f) for f in merged)
    p = _rebuilt([*fills, *broker], rows)
    assert p.status is Status.CLOSED and not settled_by_app(p)


def test_a_contract_shared_with_a_skipped_trade_is_skipped_too():
    shared = opt(S, "PUT", 5800, "2.00")
    first = position(shared, opt(S, "PUT", 500, "1.00", root="QQQ"))  # no QQQ close
    second = position(replace(shared, transaction_id="s2", order_id="2"))
    rows, skipped = settle_expiring(
        [first, second], FRI, {("SPX", FRI): PriceRead(Decimal("5850"), "test", True)}
    )
    assert rows == []
    assert [s.reason for s in skipped] == [
        "no close for QQQ yet",
        "shares a contract with a skipped trade",
    ]
