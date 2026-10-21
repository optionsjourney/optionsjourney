"""Live quotes: one symbol key for the app, each broker's own spelling on its
stream, and their messages read into the same Quote (#22)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from optionsjourney.core.quotes import (
    Quote,
    closing_mark,
    dxlink_symbol,
    no_market,
    parse_dxlink_feed,
    pick_quote,
    quotes_are_the_market,
    schwab_service_and_symbol,
    update_from_schwab,
)

AT = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def test_dxlink_symbols():
    assert dxlink_symbol("SPX") == "SPX"
    assert dxlink_symbol("SPY") == "SPY"
    assert dxlink_symbol("SPXW  261016P05800000") == ".SPXW261016P5800"
    assert dxlink_symbol("SPY   261016C00577500") == ".SPY261016C577.5"


def test_schwab_symbols():
    assert schwab_service_and_symbol("SPX") == ("LEVELONE_EQUITIES", "$SPX")
    assert schwab_service_and_symbol("SPY") == ("LEVELONE_EQUITIES", "SPY")
    assert schwab_service_and_symbol("SPXW  261016P05800000") == (
        "LEVELONE_OPTIONS",
        "SPXW  261016P05800000",
    )


def test_dxlink_compact_quotes_are_read_by_their_fields():
    fields = ["eventType", "eventSymbol", "bidPrice", "askPrice"]
    data = ["Quote", ["Quote", "SPY", 778.5, 778.6, "Quote", ".SPXW261016P5800", 1.2, 1.4]]
    events = parse_dxlink_feed(data, {"Quote": fields})
    assert events == [
        {"eventType": "Quote", "eventSymbol": "SPY", "bidPrice": 778.5, "askPrice": 778.6},
        {"eventType": "Quote", "eventSymbol": ".SPXW261016P5800", "bidPrice": 1.2, "askPrice": 1.4},
    ]


def test_dxlink_nan_prices_are_missing_not_numbers():
    fields = ["eventType", "eventSymbol", "bidPrice", "askPrice"]
    [event] = parse_dxlink_feed(["Quote", ["Quote", "XYZ", "NaN", 2.0]], {"Quote": fields})
    assert event["bidPrice"] is None and event["askPrice"] == 2.0


def test_mark_is_the_mid_or_the_last():
    assert Quote("SPY", Decimal("1.00"), Decimal("1.20"), None, "tastytrade", AT).mark == Decimal(
        "1.10"
    )
    assert Quote("SPY", None, None, Decimal("5"), "tastytrade", AT).mark == Decimal("5")
    assert Quote("SPY", None, None, None, "tastytrade", AT).mark is None


def test_schwab_sends_only_what_changed():
    first = update_from_schwab(
        None, "SPY", "LEVELONE_EQUITIES", {"key": "SPY", "1": 778.5, "2": 778.6, "3": 778.55}, AT
    )
    assert (first.bid, first.ask, first.last) == (
        Decimal("778.5"),
        Decimal("778.6"),
        Decimal("778.55"),
    )
    later = update_from_schwab(first, "SPY", "LEVELONE_EQUITIES", {"key": "SPY", "2": 778.7}, AT)
    assert (later.bid, later.ask) == (Decimal("778.5"), Decimal("778.7"))


def test_schwab_option_fields_are_numbered_differently():
    q = update_from_schwab(
        None,
        "SPXW  261016P05800000",
        "LEVELONE_OPTIONS",
        {"key": "x", "2": 1.2, "3": 1.4, "4": 1.3},
        AT,
    )
    assert (q.bid, q.ask, q.last) == (Decimal("1.2"), Decimal("1.4"), Decimal("1.3"))


def test_tastytrade_first_schwab_as_backup():
    tasty = Quote("SPY", Decimal("1"), Decimal("2"), None, "tastytrade", AT)
    schwab = Quote("SPY", Decimal("1.1"), Decimal("2.1"), None, "schwab", AT)
    assert pick_quote(tasty, schwab, tasty_up=True) is tasty
    assert pick_quote(tasty, schwab, tasty_up=False) is schwab  # TastyTrade's stream is down
    assert pick_quote(None, schwab, tasty_up=True) is schwab  # TastyTrade has no quote for it
    assert pick_quote(tasty, None, tasty_up=False) is tasty  # better a last-known quote than none


def test_a_stale_tastytrade_quote_gives_way_to_a_fresher_schwab_one():
    tasty = Quote("SPY", Decimal("1"), Decimal("2"), None, "tastytrade", AT)
    newer = Quote("SPY", Decimal("1.1"), Decimal("2.1"), None, "schwab", AT + timedelta(seconds=11))
    assert pick_quote(tasty, newer, tasty_up=True) is newer  # the stream is up but has gone quiet
    slightly = Quote(
        "SPY", Decimal("1.1"), Decimal("2.1"), None, "schwab", AT + timedelta(seconds=9)
    )
    assert pick_quote(tasty, slightly, tasty_up=True) is tasty  # a quiet symbol stays on TastyTrade


def test_the_stale_gap_boundaries():
    def schwab_at(seconds):
        return Quote(
            "SPY", Decimal("1.1"), Decimal("2.1"), None, "schwab", AT + timedelta(seconds=seconds)
        )

    tasty = Quote("SPY", Decimal("1"), Decimal("2"), None, "tastytrade", AT)
    assert pick_quote(tasty, schwab_at(10), tasty_up=True) is tasty  # exactly 10 s: not stale yet
    assert pick_quote(tasty, schwab_at(-30), tasty_up=True) is tasty  # TastyTrade is the newer
    fresh = schwab_at(1)  # TastyTrade down: Schwab's, however close the times
    assert pick_quote(tasty, fresh, tasty_up=False) is fresh


# --- After hours (#241) ---------------------------------------------------------------

PUT = "SPXW  261023P06700000"
EVENING = datetime(2026, 10, 21, 22, 0, tzinfo=UTC)  # 18:00 New York, a Wednesday
MIDDAY = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)  # 12:00
SAVED = Decimal("7.10")


def _q(bid, ask, key=PUT):
    number = lambda v: None if v is None else Decimal(v)  # noqa: E731
    return Quote(key, number(bid), number(ask), None, "tastytrade", EVENING)


def test_a_pulled_or_missing_bid_under_a_real_ask_is_no_market():
    assert no_market(_q("0", "20.40"))
    assert no_market(_q("4.60", "10.00"))  # under half the ask
    assert no_market(_q(None, "3.00"))
    assert not no_market(_q("5.00", "10.00"))  # half the ask is a market
    assert not no_market(_q("0", "0.05"))  # a worthless wing: the mid is close enough
    assert not no_market(_q("0", "20.40", key="SPY"))  # only options
    assert not no_market(None)


def test_quotes_are_the_market_from_the_open_until_options_stop_after_the_close():
    at = lambda h, m: datetime(2026, 10, 21, h, m, tzinfo=UTC)  # noqa: E731  (New York is UTC-4)
    assert not quotes_are_the_market(at(13, 29))  # 09:29
    assert quotes_are_the_market(at(13, 30))
    assert quotes_are_the_market(at(20, 14))  # 16:14
    assert not quotes_are_the_market(at(20, 15))
    assert not quotes_are_the_market(datetime(2026, 10, 24, 16, 0, tzinfo=UTC))  # Saturday


def test_after_hours_a_pulled_market_is_marked_at_its_last_real_mark():
    assert closing_mark(_q("4.60", "10.00"), SAVED, EVENING) == SAVED
    assert closing_mark(_q("4.60", "10.00"), SAVED, MIDDAY) == Decimal("7.30")  # in session: live
    # a real overnight market stays live
    assert closing_mark(_q("6.00", "6.40"), SAVED, EVENING) == Decimal("6.20")
    # nothing saved: the live mark for the page, nothing for the snapshot
    assert closing_mark(_q("4.60", "10.00"), None, EVENING) == Decimal("7.30")
    assert closing_mark(_q("4.60", "10.00"), None, EVENING, strict=True) is None
    assert closing_mark(None, SAVED, EVENING) is None
