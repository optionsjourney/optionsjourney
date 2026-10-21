from datetime import UTC, datetime, timedelta
from decimal import Decimal

from optionsjourney.bots import exit_math
from optionsjourney.bots.strikes import Quote2

D = Decimal
SHORT = "SPXW  261013C05910000"


def test_round_stop_rounds_down_to_the_single_leg_tick():
    assert exit_math.round_stop(SHORT, D("5.265")) == (D("0.10"), D("5.20"))  # $0.10 at $3 up
    assert exit_math.round_stop(SHORT, D("2.73")) == (D("0.05"), D("2.70"))  # $0.05 below $3


def test_quotes_must_be_fresh_and_newer_than_the_fill():
    now = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
    filled = now - timedelta(minutes=10)
    assert exit_math.fresh(now - timedelta(seconds=5), now, filled)
    assert not exit_math.fresh(now - timedelta(seconds=31), now, filled)
    assert not exit_math.fresh(filled - timedelta(seconds=1), now, filled)


def test_a_quiet_quote_on_a_live_stream_stays_fresh_up_to_five_minutes():
    """#202 (Tracker2 483df63e, QUOTE_QUIET_MAX_SECONDS): a stream sends on change, so
    while it's live an unchanged quote is the current one, up to QUIET_MAX."""
    now = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
    filled = now - timedelta(minutes=10)
    assert timedelta(seconds=300) == exit_math.QUIET_MAX
    assert exit_math.fresh(now - timedelta(seconds=90), now, filled, quiet_ok=True)
    assert exit_math.fresh(now - timedelta(seconds=300), now, filled, quiet_ok=True)
    assert not exit_math.fresh(now - timedelta(seconds=301), now, filled, quiet_ok=True)
    assert not exit_math.fresh(now - timedelta(seconds=90), now, filled)
    assert not exit_math.fresh(filled - timedelta(seconds=1), now, filled, quiet_ok=True)


LONG = "SPXW  261013C05985000"


def test_the_wing_sells_on_its_tick_never_below_one():
    assert exit_math.wing_limit(LONG, D("3.04")) == D("3.00")
    assert exit_math.wing_limit(LONG, D("0.47")) == D("0.45")
    assert exit_math.wing_limit(LONG, D("0")) == D("0.05")
    assert exit_math.wing_limit(LONG, D("-0.10")) == D("0.05")


def test_leave_only_while_not_paused_and_unknown_modes_sell_now():
    assert exit_math.remainder_mode("leave", False) == "leave"
    assert exit_math.remainder_mode("leave", True) == "close_market"
    assert exit_math.remainder_mode("close_chase", True) == "close_chase"
    assert exit_math.remainder_mode("bogus", False) == "close_market"


def test_a_recent_quote_is_at_most_30_seconds_old():
    now = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
    assert exit_math.recent(now - timedelta(seconds=30), now)
    assert not exit_math.recent(now - timedelta(seconds=31), now)


SHORT_CALL = "SPXW  261013C05910000"


def test_buy_limit_rounds_up_on_the_short_tick():
    # A single SPXW leg trades in $0.05 under $3 and $0.10 from $3: paying more is toward a fill
    assert exit_math.buy_limit(SHORT_CALL, D("0.47")) == D("-0.50")
    assert exit_math.buy_limit(SHORT_CALL, D("3.04")) == D("-3.10")
    assert exit_math.buy_limit(SHORT_CALL, D("0")) == D("-0.05")


# --- any-legs group math (bots slice 13) ---
A = "SPXW  261013C05900000"
B = "SPXW  261013C05910000"
C = "SPXW  261013C05920000"


def qs(**legs):
    return {
        {"A": A, "B": B, "C": C}[k]: Quote2(k, D(str(v[0])), D(str(v[1]))) for k, v in legs.items()
    }


def fly_legs():
    fly = [
        {"symbol": A, "side": "buy", "ratio": 2, "group": 0},
        {"symbol": B, "side": "sell", "ratio": 4, "group": 0},
        {"symbol": C, "side": "buy", "ratio": 2, "group": 0},
    ]
    return exit_math.group_legs(fly, 0)


def test_group_math_is_per_gcd_unit_and_signed_like_the_entry():
    legs, gcd = fly_legs()
    assert gcd == 2 and [leg.ratio for leg in legs] == [1, 2, 1]
    assert exit_math.anchor_of(legs).symbol == B
    fills = {A: D("3.00"), B: D("1.20"), C: D("0.10")}
    assert exit_math.group_entry(legs, fills) == D("-0.70")  # a debit
    assert exit_math.group_entry(legs, {A: D("3.00")}) is None
    q = qs(A=(2.9, 3.1), B=(0.9, 1.1), C=(0, 0.1))
    assert exit_math.group_value(legs, q, natural=False) == D("-1.05")
    assert exit_math.group_pnl(D("-0.70"), D("-1.05")) == D("0.35")


def test_group_legs_only_reads_its_own_group_and_anchor_needs_exactly_one_sell():
    run = [
        {"symbol": A, "side": "sell", "ratio": 1, "group": 0},
        {"symbol": B, "side": "buy", "ratio": 1, "group": 0},
        {"symbol": C, "side": "sell", "ratio": 1, "group": 1},
    ]
    legs, gcd = exit_math.group_legs(run, 0)
    assert (len(legs), gcd) == (2, 1) and exit_math.anchor_of(legs).symbol == A
    both = exit_math.group_legs(run[:1] + run[2:], 0)[0]
    assert exit_math.anchor_of(both + exit_math.group_legs(run, 1)[0]) is None


def test_natural_value_pays_the_ask_on_sells_and_takes_the_bid_on_buys():
    legs, _ = fly_legs()
    q = qs(A=(2.9, 3.1), B=(0.9, 1.1), C=(0, 0.1))
    # 2 x 1.10 (sell leg's ask) - 2.90 - 0 (no bid counts 0)
    assert exit_math.group_value(legs, q, natural=True) == D("-0.70")
    assert (
        exit_math.group_value(legs, qs(A=(2.9, 3.1), B=(0.9, 0), C=(0, 0.1)), natural=True) is None
    )


def test_no_mid_without_a_usable_quote_on_every_leg():
    legs, _ = fly_legs()
    ok = qs(A=(2.9, 3.1), B=(0.9, 1.1), C=(0, 0.1))
    assert exit_math.group_value(legs, ok) is not None
    assert exit_math.group_value(legs, {k: v for k, v in ok.items() if k != C}) is None
    assert exit_math.group_value(legs, {**ok, C: Quote2("C", None, D("0.1"))}) is None
    assert exit_math.group_value(legs, {**ok, A: Quote2("A", D("3.2"), D("3.1"))}) is None
    assert exit_math.group_value(legs, {**ok, B: Quote2("B", D("0"), D("0"))}) is None


def test_a_sold_leg_with_no_bid_under_a_positive_ask_is_valued_at_half_its_ask():
    """Final review C1 (Tracker2's watch_rules.leg_mid): a bid of 0 under a positive
    ask is a nearly worthless short, exactly when a stop should be able to fire. Its
    mid is ask / 2. A sold leg at 0/0, or crossed, still has no mid."""
    legs = spread_legs()
    # Sell B at 0/0.10 (mid 0.05), buy C at 0/0 (a worthless long counts 0)
    assert exit_math.group_value(legs, qs(B=(0, 0.1), C=(0, 0))) == D("0.05")
    assert exit_math.group_value(legs, qs(B=(0, 0), C=(0, 0))) is None
    assert exit_math.group_value(legs, qs(B=(0.2, 0.1), C=(0, 0))) is None


def test_one_usable_mid_rule():
    """Final review M3: the mid rule lives in exit_math.leg_mid only."""
    assert exit_math.leg_mid(D("0"), D("0.10"), sell=True) == D("0.05")
    assert exit_math.leg_mid(D("0"), D("0"), sell=True) is None
    assert exit_math.leg_mid(D("0"), D("0"), sell=False) == D("0")
    assert exit_math.leg_mid(D("0.2"), D("0.1"), sell=False) is None
    assert exit_math.leg_mid(D("-0.1"), D("0.1"), sell=False) is None
    assert exit_math.leg_mid(None, D("0.1"), sell=True) is None


def spread_legs():
    return exit_math.group_legs(
        [
            {"symbol": B, "side": "sell", "ratio": 1, "group": 0},
            {"symbol": C, "side": "buy", "ratio": 1, "group": 0},
        ],
        0,
    )[0]


def test_close_price_pays_or_receives_and_is_never_zero():
    spread, call = (
        spread_legs(),
        exit_math.group_legs([{"symbol": A, "side": "buy", "ratio": 1, "group": 0}], 0)[0],
    )
    assert exit_math.receives(D("-0.01"), D("1")) and not exit_math.receives(D("0.02"), D("1"))
    assert exit_math.receives(D("0"), D("-1")) and not exit_math.receives(D("0"), D("1"))
    assert exit_math.close_price(spread, D("0.02"), D("1.00")) == D("-0.05")
    assert exit_math.close_price(spread, D("0.80"), D("1.00")) == D("-0.80")
    assert exit_math.close_price(call, D("-0.01"), D("-1.00")) == D("0.05")
    assert exit_math.close_price(call, D("0"), D("-1.00")) == D("0.05")  # receives a tick
    assert exit_math.close_price(call, D("0"), D("1.00")) == D("-0.05")  # pays a tick


def test_next_close_price_steps_toward_a_fill_and_a_receiving_close_stops_at_one_tick():
    spread = spread_legs()
    assert exit_math.next_close_price(spread, D("-0.50"), D("0.05")) == D("-0.55")  # pays more
    assert exit_math.next_close_price(spread, D("0.50"), D("0.05")) == D("0.45")  # takes less
    assert exit_math.next_close_price(spread, D("0.07"), D("0.05")) == D("0.05")
    assert exit_math.next_close_price(spread, D("0.05"), D("0.05")) is None


def test_mid_close_price_concedes_the_offset_both_ways():
    spread = spread_legs()
    q = qs(B=(0.9, 1.1), C=(0.2, 0.4))  # value 1.00 - 0.30 = 0.70
    assert exit_math.group_mid_close_price(spread, q, D("0.10"), D("1.00")) == D("-0.80")
    legs = exit_math.group_legs([{"symbol": A, "side": "buy", "ratio": 1, "group": 0}], 0)[0]
    long_q = qs(A=(0.9, 1.1))  # value -1.00: receive 1.00, conceding 0.10 gives 0.90
    assert exit_math.group_mid_close_price(legs, long_q, D("0.10"), D("-1.00")) == D("0.90")
    assert exit_math.group_mid_close_price(legs, {}, D("0.10"), D("-1.00")) is None


def test_natural_price_closes_at_the_natural_value():
    spread = spread_legs()
    q = qs(B=(0.9, 1.1), C=(0.2, 0.4))  # 1.10 - 0.20
    assert exit_math.natural_price(spread, q, D("1.00")) == D("-0.90")
    assert exit_math.natural_price(spread, {}, D("1.00")) is None


def test_prices_round_toward_a_fill_on_the_combo_tick_and_a_zero_has_no_next():
    spread = spread_legs()  # a multi-leg SPX order ticks by $0.05, even above $3
    assert exit_math.close_price(spread, D("3.07"), D("5.00")) == D("-3.10")  # pays more
    long_call = exit_math.group_legs([{"symbol": A, "side": "buy", "ratio": 1, "group": 0}], 0)[0]
    assert exit_math.close_price(long_call, D("-3.07"), D("-5.00")) == D(
        "3.00"
    )  # a single leg ticks by $0.10 from $3 up
    assert exit_math.next_close_price(spread, D("-3.10"), D("0.02")) == D("-3.15")  # -3.12 rounds
    assert exit_math.next_close_price(spread, D("3.50"), D("0.02")) == D("3.45")  # 3.48 rounds
    assert exit_math.next_close_price(spread, D("0"), D("0.05")) is None


def test_a_natural_value_needs_a_fresh_ask_on_sells_and_fresh_buys_if_quoted():
    from optionsjourney.core.quotes import Quote
    from optionsjourney.trading.orders import Side

    now = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
    filled, old = now - timedelta(minutes=10), now - timedelta(seconds=31)
    legs = (exit_math.GroupLeg("S", Side.SELL, 2), exit_math.GroupLeg("B", Side.BUY, 1))

    def q(bid, ask, at=now):
        return Quote("x", None if bid is None else D(bid), None if ask is None else D(ask),
                     None, "tastytrade", at)  # fmt: skip

    ready = exit_math.natural_fresh
    assert ready(legs, {"S": q("1.0", "1.1"), "B": q("0.1", "0.2")}, now, filled)
    assert ready(legs, {"S": q("1.0", "1.1")}, now, filled)  # an unquoted buy sells for 0
    assert not ready(legs, {"B": q("0.1", "0.2")}, now, filled)  # a sell needs a quote
    assert not ready(legs, {"S": q("1.0", "0"), "B": q("0.1", "0.2")}, now, filled)
    assert not ready(legs, {"S": q("1.0", None), "B": q("0.1", "0.2")}, now, filled)
    assert not ready(legs, {"S": q("1.0", "1.1"), "B": q("0.1", "0.2", old)}, now, filled)
    assert not ready(legs, {"S": q("1.0", "1.1", old)}, now, filled)
    assert not ready(legs, {"S": q("1.0", "1.1", filled)}, now, filled)  # not after the fill


def test_the_start_offset_never_flips_a_close_from_receiving_to_paying():
    # A long call worth 0.05 at the mid, entry -2.50, offset 0.10: still a receiving
    # close (sell to close for a credit), floored at one tick, never a debit.
    legs = exit_math.group_legs([{"symbol": A, "side": "buy", "ratio": 1, "group": 0}], 0)[0]
    q = qs(A=(0, 0.1))
    price = exit_math.group_mid_close_price(legs, q, D("0.10"), D("-2.50"))
    assert price == D("0.05")
    assert exit_math.next_close_price(legs, price, D("0.05")) is None  # never under one tick
    # A credit spread whose value is a little under 0 (closing receives 0.03): the offset
    # concedes it to one tick of credit, never into paying.
    spread = spread_legs()
    tiny = qs(B=(0.1, 0.2), C=(0.13, 0.23))  # 0.15 - 0.18 = -0.03
    assert exit_math.group_mid_close_price(spread, tiny, D("0.10"), D("1.00")) == D("0.05")


def test_the_start_offset_still_concedes_a_paying_close():
    spread = spread_legs()
    tiny = qs(B=(0.1, 0.2), C=(0.08, 0.18))  # 0.15 - 0.13 = 0.02 to pay
    assert exit_math.group_mid_close_price(spread, tiny, D("0.10"), D("1.00")) == D("-0.15")


def test_split_legs_after_the_near_expiry():
    """Bots slice 14: a calendar the evening its near leg expires is split."""
    from datetime import UTC, datetime

    from optionsjourney.bots.exit_math import is_split, split_legs

    near, far = "SPXW  261020P05690000", "SPXW  261027P05690000"
    before = datetime(2026, 10, 20, 19, 59, tzinfo=UTC)  # 15:59 New York
    after = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)  # 16:00: the session closed
    assert split_legs([near, far], before) == ((), (near, far))
    assert not is_split([near, far], before)
    assert split_legs([near, far], after) == ((near,), (far,))
    assert is_split([near, far], after)
    assert not is_split([near], after)  # one expiration: expired, not split


def test_stop_past_expiry_is_one_rule_for_resting_and_watched_stops():
    """Review 6: bot_exits.stop_expired and the integrity checks read the same rule."""
    from datetime import UTC, datetime

    from optionsjourney.bots.exit_math import stop_past_expiry

    near, far = "SPXW  261020P05690000", "SPXW  261027P05690000"
    after = datetime(2026, 10, 20, 20, 0, tzinfo=UTC)
    assert stop_past_expiry(near, [near, far], after)  # a resting stop on the near short
    assert not stop_past_expiry(far, [near, far], after)
    assert stop_past_expiry(None, [near, far], after)  # watched: any leg
    assert not stop_past_expiry(None, [far], after)


def test_a_futures_option_is_past_expiry_after_its_days_close():
    """Bots slice 17: a TastyTrade futures option's date is the end of its symbol
    ("./ESZ6 EW3Z6 261218P7000"); before, an unreadable symbol never expired."""
    symbol = "./ESZ6 EW3Z6 261218P7000"
    assert not exit_math.after_expiry(symbol, datetime(2026, 12, 18, 20, 59, tzinfo=UTC))
    assert exit_math.after_expiry(symbol, datetime(2026, 12, 18, 21, 0, tzinfo=UTC))
    assert exit_math.after_expiry(symbol, datetime(2026, 12, 19, 15, 0, tzinfo=UTC))
    assert not exit_math.after_expiry("./ESZ6 junk", datetime(2027, 1, 1, tzinfo=UTC))
