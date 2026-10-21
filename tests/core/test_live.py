"""Open P/L from live marks (#24)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

from optionsjourney.core.live import leg_keys, unrealized_pl
from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import build_positions
from optionsjourney.core.symbols import occ_symbol
from optionsjourney.core.transactions import Effect, Fill, InstrumentType, Kind, OptionDetails, Side

AT = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
EXP = date(2026, 10, 16)
_ids = count(1)
S, B = Side.SELL, Side.BUY


def fill(side, effect, *, strike=None, qty=1, price, order, at=AT, shares=False):
    qty, price = Decimal(qty), Decimal(price)
    if shares:
        option, symbol, kind, mult = None, "AAPL", InstrumentType.STOCK, 1
    else:
        option = OptionDetails("PUT", Decimal(strike), EXP)
        symbol, kind, mult = occ_symbol("SPXW", option), InstrumentType.OPTION, 100
    return Fill(
        broker="tastytrade",
        account_number="5WX01234",
        transaction_id=str(next(_ids)),
        leg=0,
        executed_at=at,
        kind=Kind.TRADE,
        instrument_type=kind,
        symbol=symbol,
        underlying="AAPL" if shares else "SPX",
        option=option,
        side=side,
        effect=effect,
        quantity=qty,
        price=price,
        value=price * qty * mult * (1 if side is S else -1),
        fees=Decimal("0"),
        order_id=order,
    )


def position(*fills):
    [p] = build_positions(group_fills(fills)).positions
    return p


SHORT_PUT = "SPXW  261016P05800000"
LONG_PUT = "SPXW  261016P05790000"


def test_a_short_put_gains_as_its_price_falls():
    p = position(fill(S, Effect.OPEN, strike=5800, qty=2, price="2.00", order="1"))
    assert leg_keys(p) == {SHORT_PUT}
    assert unrealized_pl(p, {SHORT_PUT: Decimal("1.50")}) == Decimal(
        "100"
    )  # 2 x (2.00 - 1.50) x 100


def test_a_spread_uses_every_leg():
    p = position(
        fill(S, Effect.OPEN, strike=5800, price="3.00", order="1"),
        fill(B, Effect.OPEN, strike=5790, price="1.00", order="1"),
    )
    marks = {SHORT_PUT: Decimal("2.50"), LONG_PUT: Decimal("0.80")}
    assert unrealized_pl(p, marks) == Decimal("30")  # credit 200, costs 170 to close


def test_only_whats_still_open_counts():
    p = position(
        fill(S, Effect.OPEN, strike=5800, qty=2, price="2.00", order="1"),
        fill(B, Effect.CLOSE, strike=5800, price="1.00", order="2", at=AT + timedelta(days=1)),
    )
    assert unrealized_pl(p, {SHORT_PUT: Decimal("1.50")}) == Decimal("50")  # the one left


def test_shares_use_a_multiplier_of_one():
    p = position(fill(B, Effect.OPEN, qty=10, price="200", order="1", shares=True))
    assert unrealized_pl(p, {"AAPL": Decimal("210")}) == Decimal("100")


def test_a_missing_mark_means_no_number():
    p = position(fill(S, Effect.OPEN, strike=5800, price="2.00", order="1"))
    assert unrealized_pl(p, {}) is None


def test_futures_options_use_the_multiplier_from_their_fills():
    from optionsjourney.core.live import multipliers_from_fills

    es_put = Fill(
        broker="tastytrade",
        account_number="5WX01234",
        transaction_id="es1",
        leg=0,
        executed_at=AT,
        kind=Kind.TRADE,
        instrument_type=InstrumentType.FUTURE_OPTION,
        symbol="./ESZ6 EWX6  261130P7000",
        underlying="/ESZ6",
        option=OptionDetails("PUT", Decimal("7000"), date(2026, 11, 30)),
        side=S,
        effect=Effect.OPEN,
        quantity=Decimal("1"),
        price=Decimal("20.00"),
        value=Decimal("1000.00"),
        fees=Decimal("0"),
        order_id="es",
    )
    p = position(es_put)
    multipliers = multipliers_from_fills([es_put])
    assert multipliers == {"./ESZ6 EWX6  261130P7000": Decimal("50")}  # $1,000 for 20.00 points
    assert unrealized_pl(p, {"./ESZ6 EWX6  261130P7000": Decimal("12.50")}, multipliers) == Decimal(
        "375"
    )
    assert (
        unrealized_pl(p, {"./ESZ6 EWX6  261130P7000": Decimal("12.50")}) is None
    )  # unknown multiplier
