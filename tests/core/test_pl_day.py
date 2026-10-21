"""P/L Day (#51): how much each trade made or lost today, as Tracker2's
services/pl_day.py: its total P/L now minus its total at last night's
snapshot (or, without one, rebuilt from the previous close)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

from optionsjourney.core.orders import group_fills
from optionsjourney.core.pl_day import pl_day, schwab_close_counts, total_pl
from optionsjourney.core.positions import build_positions
from optionsjourney.core.symbols import occ_symbol
from optionsjourney.core.transactions import Effect, Fill, InstrumentType, Kind, OptionDetails, Side

TODAY = date(2026, 10, 13)
YESTERDAY_AT = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
TODAY_AT = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
EXP = date(2026, 10, 16)
PUT = "SPXW  261016P05800000"
_ids = count(1)
S, B = Side.SELL, Side.BUY


def fill(side, effect, *, qty=1, price, order, at):
    qty, price = Decimal(qty), Decimal(price)
    option = OptionDetails("PUT", Decimal("5800"), EXP)
    return Fill(
        broker="tastytrade", account_number="5WX01234", transaction_id=str(next(_ids)), leg=0,
        executed_at=at, kind=Kind.TRADE, instrument_type=InstrumentType.OPTION,
        symbol=occ_symbol("SPXW", option), underlying="SPX", option=option, side=side,
        effect=effect, quantity=qty, price=price,
        value=price * qty * 100 * (1 if side is S else -1), fees=Decimal("0"), order_id=order,
    )  # fmt: skip


def position(*fills):
    [p] = build_positions(group_fills(fills)).positions
    return p


def test_total_pl_is_realized_plus_open():
    p = position(fill(S, Effect.OPEN, qty=2, price="2.00", order="1", at=YESTERDAY_AT))
    assert total_pl(p, {PUT: Decimal("1.50")}) == Decimal("100")
    assert total_pl(p, {}) is None


def test_measured_from_last_nights_snapshot():
    p = position(fill(S, Effect.OPEN, qty=2, price="2.00", order="1", at=YESTERDAY_AT))
    result = pl_day(p, {PUT: Decimal("1.50")}, TODAY, snapshot=Decimal("60"), prev_closes={})
    assert result.value == Decimal("40") and not result.approximate  # 100 now, 60 last night


def test_a_trade_opened_today_counts_all_of_its_pl():
    p = position(fill(S, Effect.OPEN, qty=2, price="2.00", order="1", at=TODAY_AT))
    result = pl_day(p, {PUT: Decimal("1.80")}, TODAY, snapshot=None, prev_closes={})
    assert result.value == Decimal("40")


def test_without_a_snapshot_the_previous_close_stands_in():
    p = position(fill(S, Effect.OPEN, qty=2, price="2.00", order="1", at=YESTERDAY_AT))
    result = pl_day(p, {PUT: Decimal("1.50")}, TODAY, None, prev_closes={PUT: Decimal("1.70")})
    assert result.value == Decimal("40") and result.approximate  # 2 x (1.70 - 1.50) x 100


def test_a_close_today_counts_its_fill_against_last_night():
    p = position(
        fill(S, Effect.OPEN, qty=2, price="2.00", order="1", at=YESTERDAY_AT),
        fill(B, Effect.CLOSE, qty=2, price="1.20", order="2", at=TODAY_AT),
    )
    result = pl_day(p, {}, TODAY, snapshot=Decimal("60"), prev_closes={})
    assert result.value == Decimal("100")  # realized 160, 60 of it was there last night
    # Without a snapshot: last night it held 2, worth 400 - 2 x 1.70 x 100 = 60
    rebuilt = pl_day(p, {}, TODAY, None, prev_closes={PUT: Decimal("1.70")})
    assert rebuilt.value == Decimal("100") and rebuilt.approximate


def test_a_trade_finished_before_today_made_nothing_today():
    p = position(
        fill(S, Effect.OPEN, price="2.00", order="1", at=YESTERDAY_AT - timedelta(days=1)),
        fill(B, Effect.CLOSE, price="1.00", order="2", at=YESTERDAY_AT),
    )
    assert pl_day(p, {}, TODAY, None, {}).value == Decimal("0")


def test_an_open_leg_with_no_price_means_no_number():
    p = position(fill(S, Effect.OPEN, price="2.00", order="1", at=YESTERDAY_AT))
    assert pl_day(p, {}, TODAY, Decimal("10"), {}).value is None


def test_schwab_close_counts_only_on_the_day_before_its_close():
    monday = date(2026, 10, 19)
    assert schwab_close_counts(monday, datetime(2026, 10, 19, 15, 0, tzinfo=UTC))
    assert not schwab_close_counts(monday, datetime(2026, 10, 19, 20, 1, tzinfo=UTC))
    assert not schwab_close_counts(date(2026, 10, 23), datetime(2026, 10, 24, 15, 0, tzinfo=UTC))


def test_schwab_close_counts_on_a_half_day_only_before_one_oclock():
    day = date(2026, 11, 27)  # the day after Thanksgiving, closes 13:00 New York
    assert schwab_close_counts(day, datetime(2026, 11, 27, 17, 0, tzinfo=UTC))  # 12:00 NY
    assert not schwab_close_counts(day, datetime(2026, 11, 27, 18, 30, tzinfo=UTC))  # 13:30 NY
