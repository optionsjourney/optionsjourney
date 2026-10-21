from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from optionsjourney.bots import watched_stops as ws
from optionsjourney.bots.exit_math import GroupLeg
from optionsjourney.trading.orders import Side

D = Decimal
FILL = datetime(2026, 10, 13, 13, 47, tzinfo=UTC)
NOW = FILL + timedelta(minutes=30)
TRIGGER = D("4.80")


def read(bid, ask, at=NOW, now=NOW, filled_at=FILL):
    bid = D(bid) if bid is not None else None
    ask = D(ask) if ask is not None else None
    return ws.evaluate("SPXW 5910C", bid, ask, at, now, filled_at, TRIGGER)


def test_a_mid_at_or_past_the_stop_fires():
    assert read("4.70", "4.90").fired  # mid 4.80: at the stop
    reading = read("4.90", "5.10")
    assert reading.fired and reading.mid == D("5.00") and reading.skipped is None
    assert reading.detail == "SPXW 5910C mid 5.00 ≥ stop 4.80"


def test_a_mid_below_the_stop_holds():
    reading = read("4.60", "4.90")  # mid 4.75
    assert not reading.fired and reading.skipped is None


def test_a_stale_quote_never_fires():
    """Tracker2's evaluate_armed: stale is skipped, never fired."""
    reading = read("9.00", "9.20", at=NOW - timedelta(seconds=31))
    assert not reading.fired and reading.skipped == "stale"
    assert read("9.00", "9.20", at=None).skipped == "stale"


def test_a_quote_from_before_the_fill_never_fires():
    reading = read("9.00", "9.20", at=FILL - timedelta(seconds=1), now=FILL + timedelta(seconds=5))
    assert not reading.fired and reading.skipped == "stale"


def test_an_unusable_quote_never_fires():
    for bid, ask in ((None, "5.00"), ("5.00", None), ("5.20", "5.00"), ("-0.10", "5.00"),
                     ("0", "0")):  # fmt: skip
        reading = read(bid, ask)
        assert not reading.fired and reading.skipped == "bad_quote", (bid, ask)


def test_a_bid_of_zero_is_usable():
    assert read("0", "9.70").fired  # mid 4.85


def test_a_lapse_counts_from_the_last_watch_today():
    open_ = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
    last = NOW - timedelta(seconds=61)
    assert ws.lapse_since(last, NOW, open_, FILL) == last
    assert ws.lapse_since(NOW - timedelta(seconds=59), NOW, open_, FILL) is None


def test_a_watch_before_the_stop_came_counts_from_the_stop():
    """No stop was watched between the last watch and this stop's fill: no gap."""
    open_ = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
    earlier = FILL - timedelta(hours=1)
    assert ws.lapse_since(earlier, FILL + timedelta(seconds=30), open_, FILL) is None
    assert ws.lapse_since(earlier, NOW, open_, FILL) == FILL


def test_with_no_watch_today_a_lapse_counts_from_the_open_or_the_first_stop():
    open_ = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
    yesterday = NOW - timedelta(days=1)
    assert ws.lapse_since(yesterday, NOW, open_, FILL) == FILL  # the stop came after the open
    early = open_ - timedelta(hours=1)
    assert ws.lapse_since(None, NOW, open_, early) == open_
    assert ws.lapse_since(None, open_ + timedelta(seconds=30), open_, early) is None


def test_a_quiet_quote_counts_while_its_stream_is_healthy():
    """Review M-4, Tracker2's QUOTE_QUIET_MAX_SECONDS: a stream sends on change, so a
    quiet short's quote up to 300 s old is current while the stream is healthy."""
    quiet = NOW - timedelta(seconds=200)
    assert ws.evaluate("S", D("4.90"), D("5.10"), quiet, NOW, FILL, TRIGGER, quiet_ok=True).fired
    assert read("4.90", "5.10", at=quiet).skipped == "stale"  # without a healthy stream
    old = NOW - timedelta(seconds=301)
    reading = ws.evaluate("S", D("4.90"), D("5.10"), old, NOW, FILL, TRIGGER, quiet_ok=True)
    assert reading.skipped == "stale"
    before = FILL - timedelta(seconds=1)
    later = FILL + timedelta(seconds=60)
    assert ws.evaluate("S", D("4.90"), D("5.10"), before, later, FILL, TRIGGER,
                       quiet_ok=True).skipped == "stale"  # fmt: skip


# Slice 13: a watched group, on its whole P&L (watch_rules._usd_stop_outcome)


def _q(bid, ask, at=NOW):
    return SimpleNamespace(bid=D(bid) if bid is not None else None,
                           ask=D(ask) if ask is not None else None, at=at)  # fmt: skip


CALL = GroupLeg("SPXW  261013C05800000", Side.BUY, 1)
PUT = GroupLeg("SPXW  261013P05800000", Side.BUY, 1)
STRADDLE_ENTRY = D("-6.00")  # a $6.00 debit
STRADDLE_LEVEL = D("-1.50")


def straddle(call, put, entry=STRADDLE_ENTRY, now=NOW, quiet_ok=False):
    return ws.evaluate_group("Long straddle", [(CALL, call), (PUT, put)], entry,
                             STRADDLE_LEVEL, now, FILL, quiet_ok=quiet_ok)  # fmt: skip


def test_a_watched_group_fires_at_or_past_its_level_on_fresh_mids():
    # Value: −(2.20 + 2.18) = −4.38; P&L = −6.00 + 4.38 = −1.62 ≤ −1.50
    reading = straddle(_q("2.10", "2.30"), _q("2.08", "2.28"))
    assert reading.fired and reading.skipped is None
    assert reading.pnl == D("-1.62") and reading.mid == D("-4.38")
    assert reading.detail == "Long straddle P&L −1.62 ≤ stop −1.50 (watched by the app)"
    assert straddle(_q("2.15", "2.35"), _q("2.15", "2.35")).fired  # P&L −1.50: at the level
    held = straddle(_q("2.20", "2.40"), _q("2.20", "2.40"))  # P&L −1.40
    assert not held.fired and held.skipped is None and held.pnl == D("-1.40")
    assert held.detail == "Long straddle P&L −1.40 > stop −1.50 (watched by the app)"
    # A credit group: two shorts (a short strangle sold for 3.00), level −1.50
    sells = [(GroupLeg("C", Side.SELL, 1), _q("2.20", "2.40")),
             (GroupLeg("P", Side.SELL, 1), _q("2.10", "2.30"))]  # fmt: skip
    reading = ws.evaluate_group("Strangle", sells, D("3.00"), D("-1.50"), NOW, FILL)
    assert reading.fired and reading.pnl == D("-1.50")  # 3.00 − (2.30 + 2.20)


def test_one_stale_or_unusable_leg_never_fires():
    far = _q("0.50", "0.60")  # P&L −4.90: far past the level
    old = NOW - timedelta(seconds=31)
    for call, put, why in (
        (far, _q("0.50", "0.60", at=old), "stale"),  # one leg 31 s old
        (far, None, "stale"),  # one leg with no quote
        (far, _q("0.50", "0.60", at=None), "stale"),
        (far, _q("0.50", "0.60", at=FILL - timedelta(seconds=1)), "stale"),  # before the fill
        (far, _q(None, "0.60"), "bad_quote"),
        (far, _q("0.70", "0.60"), "bad_quote"),  # crossed
        (far, _q("-0.10", "0.60"), "bad_quote"),
    ):
        reading = straddle(call, put)
        assert not reading.fired and reading.skipped == why, (put, why)
        assert reading.pnl is None
    reading = straddle(far, _q("0.50", "0.60", at=old))
    assert reading.detail == "Long straddle: no fresh price for SPXW  261013P05800000"
    # A sell leg at 0/0 has no mid (exit_math.leg_mid)
    sells = [(GroupLeg("C", Side.SELL, 1), _q("0", "0"))]
    assert ws.evaluate_group("G", sells, D("1"), D("-1"), NOW, FILL).skipped == "bad_quote"
    # No entry price yet (a leg without a fill): never fires
    assert straddle(far, far, entry=None).skipped == "incomplete"
    # The quiet rule applies to every leg: one older than QUIET_MAX blocks the group
    quiet, too_old = NOW - timedelta(seconds=200), NOW - timedelta(seconds=301)
    assert straddle(_q("0.50", "0.60", quiet), _q("0.50", "0.60", quiet), quiet_ok=True).fired
    reading = straddle(_q("0.50", "0.60", quiet), _q("0.50", "0.60", too_old), quiet_ok=True)
    assert reading.skipped == "stale"


def test_a_worthless_bought_leg_at_0_0_counts_as_0_but_a_sold_one_is_unusable():
    """Controller ruling (review Minor 4; Tracker2's close_value counts a no-bid long as
    0): a fresh 0/0 quote on a BUY leg values it at 0, which only fires sooner. A SELL
    leg at 0/0 stays unusable."""
    # Call at 4.30 mid, put worthless: value −4.30, P&L −6.00 + 4.30 = −1.70
    reading = straddle(_q("4.20", "4.40"), _q("0", "0"))
    assert reading.skipped is None and reading.fired and reading.pnl == D("-1.70")
    sells = [(GroupLeg("C", Side.SELL, 1), _q("0", "0"))]
    assert ws.evaluate_group("G", sells, D("1"), D("-1"), NOW, FILL).skipped == "bad_quote"


def test_a_short_strangle_fires_once_its_far_short_has_no_bid():
    """Final review C1: SPX rallies; the short put goes to 0/0.05 and the short call to
    20.00/20.40. Entry 4.00, stop at 100%: level −4.00. Value 0.025 + 20.20 = 20.225,
    P&L −16.225: the stop fires (Tracker2's leg_mid accepts bid 0 under a positive ask)."""
    sells = [(GroupLeg("C", Side.SELL, 1), _q("20.00", "20.40")),
             (GroupLeg("P", Side.SELL, 1), _q("0", "0.05"))]  # fmt: skip
    reading = ws.evaluate_group("Strangle", sells, D("4.00"), D("-4.00"), NOW, FILL)
    assert reading.skipped is None and reading.fired
    assert reading.pnl == D("-16.225")
    calm = [(GroupLeg("C", Side.SELL, 1), _q("1.00", "1.20")),
            (GroupLeg("P", Side.SELL, 1), _q("0", "0.05"))]  # fmt: skip
    reading = ws.evaluate_group("Strangle", calm, D("4.00"), D("-4.00"), NOW, FILL)
    assert reading.skipped is None and not reading.fired and reading.pnl == D("2.875")


def test_missed_sessions_names_whole_earlier_sessions_unwatched():
    """Bots slice 14: a held trade whose watched stops went a whole session unwatched."""
    from datetime import UTC, date, datetime

    from optionsjourney.bots.watched_stops import missed_sessions

    placed = datetime(2026, 10, 13, 13, 48, tzinfo=UTC)  # Tue 09:48
    tuesday_watch = datetime(2026, 10, 13, 19, 0, tzinfo=UTC)
    # Thursday's startup: nothing watched since Tuesday afternoon: Wednesday missed
    assert missed_sessions(tuesday_watch, placed, date(2026, 10, 15)) == [date(2026, 10, 14)]
    # Wednesday was watched (from its open): nothing missed
    wednesday = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
    assert missed_sessions(wednesday, placed, date(2026, 10, 15)) == []
    # the stop came after Wednesday's close: nothing to watch then
    late = datetime(2026, 10, 14, 21, 0, tzinfo=UTC)
    assert missed_sessions(None, late, date(2026, 10, 15)) == []
    # never watched, over a weekend: Friday and Monday
    friday = datetime(2026, 10, 16, 13, 48, tzinfo=UTC)
    assert missed_sessions(None, friday, date(2026, 10, 20)) == [date(2026, 10, 16),
                                                                date(2026, 10, 19)]  # fmt: skip
