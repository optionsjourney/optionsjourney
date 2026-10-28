from decimal import Decimal

import pytest

from optionsjourney.bots.definition import LegDef
from optionsjourney.bots.strikes import (
    Chain,
    NoTrade,
    Quote2,
    TooNarrow,
    Unsafe,
    chain_from_rows,
    entry_price,
    next_price,
    pick_legs,
)  # fmt: skip
from optionsjourney.trading.orders import Side

D = Decimal
SPOT = D("5800")


def sym(strike, right):
    return f"SPXW  261013{right[0].upper()}{int(strike * 1000):08d}"


def mid_for(strike, right):
    distance = (strike - SPOT) if right == "call" else (SPOT - strike)
    return max(D("30") - distance * D("0.25"), D("0.05"))


def chain(missing=()):
    quotes = {}
    for k in range(5500, 6105, 5):
        strike = D(k)
        for right in ("call", "put"):
            if (strike, right) in missing:
                continue
            m = mid_for(strike, right)
            quotes[(strike, right)] = Quote2(sym(strike, right), m - D("0.05"), m + D("0.05"))
    return Chain(SPOT, quotes)


IC = (
    LegDef(0, Side.SELL, "call", pick="premium", target=D("2.70"), group=0),
    LegDef(1, Side.BUY, "call", pick="offset", target=D("75"), link=0, group=0),
    LegDef(2, Side.SELL, "put", pick="premium", target=D("2.90"), group=1),
    LegDef(3, Side.BUY, "put", pick="offset", target=D("-75"), link=2, group=1),
)


def test_premium_legs_pick_the_nearest_out_of_the_money_mid():
    picked = pick_legs(IC, chain())
    # call mid at 5910 is 2.50 (0.20 from 2.70), at 5905 it's 3.75: 5910 wins
    assert [p.strike for p in picked] == [D("5910"), D("5985"), D("5690"), D("5615")]
    assert picked[0].symbol == sym(D("5910"), "call") and picked[0].mid == D("2.50")


def test_premium_ties_go_further_out():
    legs = (LegDef(0, Side.SELL, "call", pick="premium", target=D("3.125"), group=0),)
    # 5905 mid 3.75 and 5910 mid 2.50 are 0.625 either side: further out (5910) wins
    assert pick_legs(legs, chain())[0].strike == D("5910")


def test_only_out_of_the_money_strikes_are_considered():
    legs = (LegDef(0, Side.SELL, "put", pick="premium", target=D("40"), group=1),)
    assert pick_legs(legs, chain())[0].strike < SPOT


def test_an_exact_offset_that_isnt_listed_skips():
    with pytest.raises(NoTrade, match="5985"):
        pick_legs(IC, chain(missing={(D("5985"), "call")}))


def test_a_loose_offset_takes_the_next_strike_further_out():
    wing = LegDef(1, Side.BUY, "call", pick="offset", target=D("77"), link=0, exact=False, group=0)
    assert pick_legs((IC[0], wing), chain())[1].strike == D("5990")


def test_a_long_inside_its_short_is_now_a_debit_not_unsafe():
    inside = (IC[0], LegDef(1, Side.BUY, "call", pick="offset", target=D("-75"), link=0, group=0))
    picked = pick_legs(inside, chain())
    assert [p.strike for p in picked] == [D("5910"), D("5835")]
    assert entry_price(picked, D("0")) < 0


def test_entry_price_is_the_signed_sum_of_mids_less_the_offset():
    picked = pick_legs(IC, chain())
    expected = sum((p.mid if p.side is Side.SELL else -p.mid) * p.ratio for p in picked)
    assert entry_price(picked, D("0")) == expected
    assert entry_price(picked, D("0.10")) == expected - D("0.10")


def test_chain_from_the_analyzers_rows():
    rows = {5810.0: {"call": {"bid": 1.0, "ask": 1.2, "symbol": sym(D("5810"), "call")}}}
    built = chain_from_rows(rows, 5800.4)
    assert built.spot == D("5800.4")
    assert built.quotes[(D("5810"), "call")].mid == D("1.1")


# --- Slice 10: strikes by delta and by spot offset ------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from optionsjourney.bots.strikes import NoData  # noqa: E402


def delta_for(strike, right):
    """A made-up |delta| that falls 0.002 a point out of the money (0.30 at 100 out)."""
    distance = (strike - SPOT) if right == "call" else (SPOT - strike)
    size = max(D("0.5") - distance * D("0.002"), D("0.005"))
    return size if right == "call" else -size


def delta_chain(without=0, right="put"):
    """chain() with deltas; `without` out-of-the-money strikes of `right` have none."""
    base = chain()
    quotes, dropped = {}, 0
    for (strike, side), q in sorted(base.quotes.items(), key=lambda kv: abs(kv[0][0] - SPOT)):
        otm = strike < SPOT if side == "put" else strike > SPOT
        if side == right and otm and dropped < without:
            dropped += 1
            quotes[(strike, side)] = q
            continue
        quotes[(strike, side)] = Quote2(q.symbol, q.bid, q.ask, delta_for(strike, side))
    return Chain(SPOT, quotes)


def by_delta(right, target, side=Side.SELL, position=0, group=0):
    return LegDef(position, side, right, pick="delta", target=D(target), group=group)


def test_a_delta_leg_takes_the_strike_nearest_its_delta():
    # put |delta| = 0.5 - 0.002 x distance: 0.16 is 170 points out, 5630
    picked = pick_legs((by_delta("put", "0.16"),), delta_chain())
    assert picked[0].strike == D("5630") and picked[0].delta == D("-0.160")
    assert pick_legs((by_delta("call", "0.10"),), delta_chain())[0].strike == D("6000")


def test_delta_ties_go_further_out():
    # 0.165 sits between 5630 (0.16) and 5635 (0.17): the put further out wins
    assert pick_legs((by_delta("put", "0.165"),), delta_chain())[0].strike == D("5630")
    assert pick_legs((by_delta("call", "0.165"),), delta_chain())[0].strike == D("5970")


def test_a_delta_leg_looks_out_of_the_money_only():
    # a 50-delta target takes the first out-of-the-money strike, never one through spot
    assert pick_legs((by_delta("put", "0.50"),), delta_chain())[0].strike == D("5795")
    assert pick_legs((by_delta("call", "0.50"),), delta_chain())[0].strike == D("5805")


def test_a_delta_leg_with_too_few_deltas_is_no_data():
    # 60 out-of-the-money puts listed (5500-5795); 3 without a delta is 95%: still fine,
    # 4 without is under 95%: the entry can't be trusted and never falls back to a price
    assert pick_legs((by_delta("put", "0.16"),), delta_chain(without=3)) is not None
    with pytest.raises(NoData, match="Deltas unavailable for the puts"):
        pick_legs((by_delta("put", "0.16"),), delta_chain(without=4))
    with pytest.raises(NoData):
        pick_legs((by_delta("put", "0.16"),), chain())  # no deltas at all


def test_a_target_further_out_than_the_chain_reaches_skips():
    # the furthest put listed (5500, 300 out) is 0.0 → clipped 0.005 in delta_for; take a
    # chain that stops at 5700 (0.30) and ask for 0.10
    short = Chain(SPOT, {k: q for k, q in delta_chain().quotes.items() if k[0] >= D("5700")})
    with pytest.raises(NoTrade, match="0.1 delta") as caught:
        pick_legs((by_delta("put", "0.10"),), short)
    assert not isinstance(caught.value, NoData)


def test_a_wing_can_be_measured_from_a_delta_short():
    wing = LegDef(1, Side.BUY, "put", pick="offset", target=D("-50"), link=0, group=0)
    picked = pick_legs((by_delta("put", "0.16"), wing), delta_chain())
    assert [p.strike for p in picked] == [D("5630"), D("5580")]


def test_a_short_at_ninety_delta_is_unsafe():
    stale = dict(delta_chain().quotes)
    q = stale[(D("5795"), "put")]
    stale[(D("5795"), "put")] = Quote2(q.symbol, q.bid, q.ask, D("-0.95"))
    with pytest.raises(Unsafe, match="0.95"):
        pick_legs((by_delta("put", "0.95"),), Chain(SPOT, stale))


def by_spot(right, points, side=Side.SELL):
    return LegDef(0, side, right, pick="spot", target=D(points), group=0)


def test_a_spot_offset_takes_the_nearest_listed_strike():
    odd = Chain(D("5801.2"), chain().quotes)
    assert pick_legs((by_spot("put", "-50"),), odd)[0].strike == D("5750")  # 5751.2
    assert pick_legs((by_spot("call", "52"),), odd)[0].strike == D("5855")  # 5853.2


def test_a_spot_offset_tie_goes_away_from_the_money():
    halfway = Chain(D("5802.5"), chain().quotes)
    assert pick_legs((by_spot("put", "-50"),), halfway)[0].strike == D("5750")  # 5752.5
    assert pick_legs((by_spot("call", "50"),), halfway)[0].strike == D("5855")


def test_a_spot_offset_short_rounds_out_of_the_money():
    # SPX 5800.00, -1: 5799 is nearest 5800 (at the money), so a short put goes out to 5795
    assert pick_legs((by_spot("put", "-1"),), Chain(D("5800"), chain().quotes))[0].strike == D(
        "5795"
    )
    assert pick_legs((by_spot("call", "1"),), Chain(D("5800"), chain().quotes))[0].strike == D(
        "5805"
    )
    # a long leg still takes the nearest
    long = LegDef(0, Side.BUY, "put", pick="spot", target=D("-1"), group=0)
    assert pick_legs((long,), Chain(D("5800"), chain().quotes))[0].strike == D("5800")


NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)  # 10:00 New York
CLOSE = datetime(2026, 10, 13, 20, 0, tzinfo=UTC)  # 16:00 New York


def test_schwab_deltas_are_read_only_when_asked_and_minus_999_is_none():
    rows = {
        5700.0: {"put": {"bid": 1.0, "ask": 1.2, "symbol": "P1", "delta": -0.12}},
        5650.0: {"put": {"bid": 0.5, "ask": 0.6, "symbol": "P2", "delta": -999.0}},
        5600.0: {"put": {"bid": 0.2, "ask": 0.3, "symbol": "P3", "delta": float("nan")}},
    }
    assert chain_from_rows(rows, 5800).quotes[(D("5700"), "put")].delta is None
    built = chain_from_rows(rows, 5800, deltas=True, now=NOW, expires_at=CLOSE)
    assert built.quotes[(D("5700"), "put")].delta == D("-0.12")
    # A snapshot row (no quote time) with no usable delta gets one worked out from its mid
    assert built.quotes[(D("5650"), "put")].delta is not None
    assert built.quotes[(D("5650"), "put")].delta < 0


def test_a_quiet_quote_on_a_healthy_stream_gets_a_model_delta():
    # DXLink sends on change: a far 0DTE quote unchanged for minutes is still current
    quiet = NOW - timedelta(minutes=5)
    rows = {5750.0: {"put": {"bid": 2.0, "ask": 2.2, "symbol": "P", "at": quiet}}}
    built = chain_from_rows(rows, 5800, deltas=True, now=NOW, expires_at=CLOSE, stream_ok=True)
    delta = built.quotes[(D("5750"), "put")].delta
    assert delta is not None and D("-0.5") < delta < 0


def test_a_stale_quote_gives_no_model_delta():
    # the stream itself is down (or SPX isn't updating): no delta from any streamed row
    rows = {5750.0: {"put": {"bid": 2.0, "ask": 2.2, "symbol": "P", "at": NOW}}}
    built = chain_from_rows(rows, 5800, deltas=True, now=NOW, expires_at=CLOSE, stream_ok=False)
    assert built.quotes[(D("5750"), "put")].delta is None


def test_the_streams_greeks_come_first():
    rows = {5750.0: {"put": {"bid": 2.0, "ask": 2.2, "symbol": "P", "at": NOW,
                             "stream_delta": D("-0.21"), "stream_delta_at": NOW}}}  # fmt: skip
    built = chain_from_rows(rows, 5800, deltas=True, now=NOW, expires_at=CLOSE, stream_ok=True)
    assert built.quotes[(D("5750"), "put")].delta == D("-0.21")
    built = chain_from_rows(rows, 5800, deltas=True, now=NOW, expires_at=CLOSE, stream_ok=False)
    assert built.quotes[(D("5750"), "put")].delta is None


def test_a_quiet_quote_with_a_fresh_greek_uses_the_greek():
    # the quote hasn't changed for 15 minutes; its Greeks arrived 10 seconds ago
    rows = {5750.0: {"put": {"bid": 2.0, "ask": 2.2, "symbol": "P",
                             "at": NOW - timedelta(minutes=15), "stream_delta": D("-0.21"),
                             "stream_delta_at": NOW - timedelta(seconds=10)}}}  # fmt: skip
    built = chain_from_rows(rows, 5800, deltas=True, now=NOW, expires_at=CLOSE, stream_ok=True)
    assert built.quotes[(D("5750"), "put")].delta == D("-0.21")


def test_an_old_greek_gives_way_to_the_model_from_the_mid():
    # Greeks recompute as the underlying moves: one 5 minutes old is out of date
    rows = {5750.0: {"put": {"bid": 2.0, "ask": 2.2, "symbol": "P", "at": NOW,
                             "stream_delta": D("-0.21"),
                             "stream_delta_at": NOW - timedelta(minutes=5)}}}  # fmt: skip
    built = chain_from_rows(rows, 5800, deltas=True, now=NOW, expires_at=CLOSE, stream_ok=True)
    delta = built.quotes[(D("5750"), "put")].delta
    assert delta is not None and delta != D("-0.21")


def test_a_delta_with_the_wrong_sign_is_no_data():
    wrong = dict(delta_chain().quotes)
    q = wrong[(D("5630"), "put")]
    wrong[(D("5630"), "put")] = Quote2(q.symbol, q.bid, q.ask, D("0.16"))  # a put's is negative
    with pytest.raises(NoData, match="sign"):
        pick_legs((by_delta("put", "0.16"),), Chain(SPOT, wrong))


def test_no_data_says_what_was_missing():
    with pytest.raises(NoData) as caught:
        pick_legs((by_delta("put", "0.16"),), chain())
    assert caught.value.kind == "deltas"


# --- Slice 13: any structure ------------------------------------------------------------


def test_buy_legs_are_picked_from_every_strike():
    buy = (LegDef(0, Side.BUY, "call", pick="premium", target=D("40"), group=0),)
    sell = (LegDef(0, Side.SELL, "call", pick="premium", target=D("40"), group=0),)
    assert pick_legs(buy, chain())[0].strike == D("5760")  # mid 40, in the money
    assert pick_legs(sell, chain())[0].strike == D("5805")  # the nearest out of the money


ZERO = D("0")


def _fly_legs(call_target=ZERO, put_target=ZERO):
    return (
        LegDef(0, Side.SELL, "call", pick="spot", target=call_target, group=0),
        LegDef(1, Side.SELL, "put", pick="spot", target=put_target, group=0),
    )


def test_spot_zero_puts_a_call_and_a_put_on_one_strike():
    # 5802.5 is as near 5800 as 5805: the tie goes to the lower strike, for either side
    odd = Chain(D("5802.5"), chain().quotes)
    picked = pick_legs(_fly_legs(), odd)
    assert [p.strike for p in picked] == [D("5800"), D("5800")]


def test_a_fly_body_short_is_not_refused_as_in_the_money():
    legs = _fly_legs() + (
        LegDef(2, Side.BUY, "call", pick="offset", target=D("75"), link=0, group=0),
        LegDef(3, Side.BUY, "put", pick="offset", target=D("-75"), link=1, group=0),
    )
    picked = pick_legs(legs, chain())  # spot 5800: the call body is at the money
    assert [p.strike for p in picked] == [D("5800"), D("5800"), D("5875"), D("5725")]


def test_an_inverted_pair_of_shorts_is_unsafe():
    legs = _fly_legs(call_target=D("-50"))  # a short call at 5750, below the short put at 5800
    with pytest.raises(Unsafe, match="Inverted"):
        pick_legs(legs, chain())
    pick_legs(_fly_legs(), chain())  # equal strikes pass


def test_next_price_credit_floors_at_a_nickel_and_debit_stops_at_its_max():
    assert next_price(D("0.10"), D("0.05")) == D("0.05")
    assert next_price(D("0.05"), D("0.05")) is None
    assert next_price(D("-2.00"), D("0.05"), D("2.05")) == D("-2.05")
    assert next_price(D("-2.05"), D("0.05"), D("2.05")) is None
    assert next_price(D("-2.00"), D("0.05")) == D("-2.05")  # no maximum: unbounded


def test_a_credit_group_priced_as_a_debit_is_inverted():
    """Final review I1, narrowed by decision 0048: inside a trade that prices as a credit,
    a group with one sold leg must itself price as a credit (its stop is worked out on that
    short). A trade that prices as a debit has no stop on a short."""
    from optionsjourney.bots.definition import iron_condor_skeleton
    from optionsjourney.bots.strikes import Picked, inverted_group

    bot = iron_condor_skeleton("IC")

    def picked(put_short_mid, put_wing_mid):
        return (
            Picked(0, "C1", D("5910"), "call", Side.SELL, 1, 0, D("2.50")),
            Picked(1, "C2", D("5985"), "call", Side.BUY, 1, 0, D("0.05")),
            Picked(2, "P1", D("5690"), "put", Side.SELL, 1, 1, D(put_short_mid)),
            Picked(3, "P2", D("5695"), "put", Side.BUY, 1, 1, D(put_wing_mid)),
        )

    inverted_group(bot, picked("2.50", "0.40"))  # a normal condor: no error
    with pytest.raises(Unsafe, match=r"^Inverted: the Put side prices as a debit\.$"):
        inverted_group(bot, picked("2.50", "3.75"))
    with pytest.raises(Unsafe, match="Put side"):
        inverted_group(bot, picked("2.50", "2.50"))  # 0: not a credit either
    # The whole trade prices as a debit: the fall-back watches its stop, nothing refused
    inverted_group(bot, picked("2.50", "6.00"))


@pytest.mark.parametrize(
    ("mid", "words"),
    [
        ("0", "Priced too close to even (0.00); not entered."),
        ("0.03", "Priced too close to even (0.03); not entered."),
        ("-0.04", "Priced too close to even (0.04); not entered."),
    ],
)
def test_a_price_under_the_smallest_credit_either_way_is_too_close_to_even(mid, words):
    from optionsjourney.bots.strikes import too_close_to_even

    assert too_close_to_even(D(mid)) == words


@pytest.mark.parametrize("mid", ["0.05", "-0.05", "1.20", "-2.50"])
def test_a_price_of_the_smallest_credit_or_more_either_way_is_not_too_close(mid):
    from optionsjourney.bots.strikes import too_close_to_even

    assert too_close_to_even(D(mid)) is None


# Bots slice 14: legs in other expirations, each picked on its own chain.


def far_chain(day="261020", extra="3"):
    """Another expiration: the same strikes, `extra` more time value on every mid."""
    quotes = {}
    for k in range(5500, 6105, 5):
        strike = D(k)
        for right in ("call", "put"):
            m = mid_for(strike, right) + D(extra)
            name = f"SPXW  {day}{right[0].upper()}{int(strike * 1000):08d}"
            quotes[(strike, right)] = Quote2(name, m - D("0.05"), m + D("0.05"))
    return Chain(SPOT, quotes)


def test_calendar_legs_pick_on_their_own_chains():
    """resolver._resolve_one: each leg reads chains[leg.dte]."""
    from datetime import date

    near = LegDef(0, Side.SELL, "put", dte=0, pick="premium", target=D("2.90"))
    far = LegDef(1, Side.BUY, "put", dte=7, pick="premium", target=D("5.90"))
    picked = pick_legs((near, far), {0: chain(), 7: far_chain()})
    assert picked[0].symbol == sym(D("5690"), "put")
    assert picked[1].symbol == "SPXW  261020P05690000"  # 2.90 + 3 on the far chain
    assert picked[0].expiration == date(2026, 10, 13)
    assert picked[1].expiration == date(2026, 10, 20)


def test_an_offset_is_looked_up_on_its_own_chain():
    """A calendar's far leg sits on the near leg's strike (offset 0), in its own
    expiration; a diagonal's sits points away."""
    near = LegDef(0, Side.SELL, "put", dte=0, pick="premium", target=D("2.90"))
    same = LegDef(1, Side.BUY, "put", dte=7, pick="offset", target=D("0"), link=0)
    picked = pick_legs((near, same), {0: chain(), 7: far_chain()})
    assert picked[1].symbol == "SPXW  261020P05690000"
    assert picked[1].mid == mid_for(D("5690"), "put") + 3
    diagonal = LegDef(1, Side.BUY, "put", dte=7, pick="offset", target=D("-25"), link=0)
    picked = pick_legs((near, diagonal), {0: chain(), 7: far_chain()})
    assert picked[1].symbol == "SPXW  261020P05665000"


def test_a_plain_chain_still_serves_every_leg():
    legs = (LegDef(0, Side.SELL, "put", target=D("2.90")),)
    assert pick_legs(legs, chain())[0].symbol == sym(D("5690"), "put")


def test_a_leg_with_no_chain_for_its_dte_is_no_trade():
    legs = (LegDef(0, Side.SELL, "put", dte=7, target=D("2.90")),)
    with pytest.raises(NoTrade, match="No option chain for 7 DTE"):
        pick_legs(legs, {0: chain()})


def test_a_monthly_spx_leg_is_unsafe():
    """Question 11: AM-settled SPX monthlies stop trading the day before their date."""
    quotes = {
        key: Quote2(q.symbol.replace("SPXW ", "SPX  "), q.bid, q.ask)
        for key, q in far_chain().quotes.items()
    }
    monthly = Chain(SPOT, quotes)
    near = LegDef(0, Side.SELL, "put", dte=0, pick="premium", target=D("2.90"))
    far = LegDef(1, Side.BUY, "put", dte=7, pick="offset", target=D("0"), link=0)
    with pytest.raises(Unsafe, match="monthly SPX option"):
        pick_legs((near, far), {0: chain(), 7: monthly}, root="SPXW")
    pick_legs((near, far), {0: chain(), 7: far_chain()}, root="SPXW")  # weeklies pass


def test_a_leg_off_the_standard_root_is_unsafe():
    """Slice 16: a bot trades its underlying's standard root only, never an adjusted
    contract such as SPY1 (whose deliverable isn't 100 shares)."""
    quotes = {
        key: Quote2(q.symbol.replace("SPXW  ", "SPY1  "), q.bid, q.ask)
        for key, q in chain().quotes.items()
    }
    adjusted = Chain(SPOT, quotes)
    legs = (LegDef(0, Side.SELL, "put", pick="premium", target=D("2.90")),)
    with pytest.raises(Unsafe, match="isn't a standard SPY option; the bot trades SPY only"):
        pick_legs(legs, adjusted, root="SPY", underlying="SPY")
    spy = {k: Quote2(q.symbol.replace("SPXW  ", "SPY   "), q.bid, q.ask)
           for k, q in chain().quotes.items()}  # fmt: skip
    assert pick_legs(legs, Chain(SPOT, spy), root="SPY", underlying="SPY")[0].symbol.startswith(
        "SPY   "
    )


# --- Bots slice 19: the strike pickers -----------------------------------------------------


def _near_shorts():
    """Premium shorts that land 10 points apart: the call at 5805, the put at 5795."""
    return (
        LegDef(0, Side.SELL, "call", pick="premium", target=D("29"), group=0),
        LegDef(1, Side.BUY, "call", pick="offset", target=D("75"), link=0, group=0),
        LegDef(2, Side.SELL, "put", pick="premium", target=D("29"), group=1),
        LegDef(3, Side.BUY, "put", pick="offset", target=D("-75"), link=2, group=1),
    )


def test_min_short_width_widens_the_side_whose_next_strike_misses_less():
    notes = []
    picked = pick_legs(_near_shorts(), chain(), min_width=D("20"), notes=notes)
    # a tie moves the call first (5810), then the put's 5790 misses less than 5815
    assert [p.strike for p in picked] == [D("5810"), D("5885"), D("5790"), D("5715")]
    assert picked[0].mid == D("27.50") and picked[2].symbol == sym(D("5790"), "put")
    assert any("20-point min short width" in note for note in notes)
    # wide enough already: nothing moves
    assert [p.strike for p in pick_legs(_near_shorts(), chain(), min_width=D("10"))][::2] == [
        D("5805"), D("5795")]  # fmt: skip


def test_min_short_width_that_runs_out_of_strikes_skips():
    with pytest.raises(TooNarrow, match="no strikes left to widen"):
        pick_legs(_near_shorts(), chain(), min_width=D("1000"))


def _linked_wing(target, offset):
    return (
        LegDef(0, Side.SELL, "put", pick="premium", target=D("2.90"), group=0),
        LegDef(1, Side.BUY, "put", target=D(target), link=0, max_offset=D(offset)),
    )


def test_a_linked_premium_wing_takes_its_premium_inside_its_max_offset():
    picked = pick_legs(_linked_wing("1.00", "-50"), chain())
    assert [p.strike for p in picked] == [D("5690"), D("5685")]  # 1.25 is nearest 1.00
    # every strike out to the bound is 0.05: a tie goes further out, never past 5640
    assert pick_legs(_linked_wing("0.10", "-50"), chain())[1].strike == D("5640")


def test_a_linked_wing_with_nothing_priced_inside_takes_the_nearest_past_the_bound():
    # select_linked_leg's fallback (question 12): 5685 isn't priced, so 5680, nearest 5685
    notes = []
    picked = pick_legs(_linked_wing("1.00", "-5"), chain(missing={(D("5685"), "put")}),
                       notes=notes)  # fmt: skip
    assert picked[1].strike == D("5680")
    assert notes == ["leg 2: no put priced within 5 points of 5690; clamped to its offset "
                     "bound, using 5680 (bound 5685)"]  # fmt: skip
    lone = Chain(SPOT, {(D("5690"), "put"): Quote2(sym(D("5690"), "put"), D("2.45"), D("2.55"))})
    with pytest.raises(NoTrade, match="No put priced below 5690"):
        pick_legs(_linked_wing("1.00", "-5"), lone)


def test_a_strike_held_the_other_way_moves_one_strike_out():
    held = {sym(D("5910"), "call").replace(" ", ""): 1}  # long the 5910 call already
    notes = []
    picked = pick_legs(IC, chain(), held=held, move_conflicted=True, notes=notes)
    assert picked[0].strike == D("5915") and picked[0].symbol == sym(D("5915"), "call")
    assert picked[0].mid == mid_for(D("5915"), "call")
    assert notes == ["strike 5910 call is held the other way; moved to 5915"]
    # off: it stays (the broker is the backstop)
    assert pick_legs(IC, chain(), held=held)[0].strike == D("5910")
    # held the same way adds to it: no conflict
    short = {sym(D("5910"), "call").replace(" ", ""): -1}
    assert pick_legs(IC, chain(), held=short, move_conflicted=True)[0].strike == D("5910")


def test_a_strike_another_leg_already_uses_moves_one_strike_out():
    legs = (
        LegDef(0, Side.SELL, "call", pick="premium", target=D("2.70"), group=0),
        LegDef(1, Side.BUY, "call", pick="premium", target=D("2.70"), group=1),
    )
    with pytest.raises(Unsafe, match="same option"):
        pick_legs(legs, chain())
    picked = pick_legs(legs, chain(), move_conflicted=True)
    assert [p.strike for p in picked] == [D("5910"), D("5915")]


def test_a_conflicted_strike_never_moves_onto_a_taken_or_unpriced_one():
    held = {sym(D("5910"), "call").replace(" ", ""): 1}
    unpriced = chain()
    unpriced.quotes[(D("5915"), "call")] = Quote2(sym(D("5915"), "call"), None, None)
    picked = pick_legs(IC, unpriced, held=held, move_conflicted=True)
    assert picked[0].strike == D("5910")  # stays: the next one out has no price
    both = held | {sym(D("5915"), "call").replace(" ", ""): 2}
    assert pick_legs(IC, chain(), held=both, move_conflicted=True)[0].strike == D("5910")


# --- #206: a short placed by an offset may be in the money (Tracker2 c670b556) ---------


def _diagonal(points, short_right="call"):
    """Run #340's shape: a long at spot + `points`, a short 10 points further out of it."""
    out = D("10") if short_right == "call" else D("-10")
    return (
        LegDef(0, Side.BUY, short_right, pick="spot", target=D(points), group=0),
        LegDef(1, Side.SELL, short_right, pick="offset", target=out, link=0, group=0),
    )


def test_an_offset_short_in_the_money_is_the_setup_not_a_mistake():
    picked = pick_legs(_diagonal("-50"), delta_chain())
    assert [p.strike for p in picked] == [D("5750"), D("5760")]  # the short call is ITM
    assert picked[1].delta == D("0.580")


def test_an_offset_short_in_the_money_still_gets_the_stale_delta_check():
    with pytest.raises(Unsafe, match="delta of 0.98"):
        pick_legs(_diagonal("-250"), delta_chain())  # short call 5560, 0.98


def test_an_offset_short_in_the_money_with_no_delta_is_no_data():
    with pytest.raises(NoData, match="in the money and has no delta") as caught:
        pick_legs(_diagonal("-50"), chain())
    assert caught.value.kind == "deltas"


def test_an_out_of_the_money_offset_short_needs_no_delta():
    legs = (
        LegDef(0, Side.BUY, "call", pick="spot", target=D("50"), group=0),
        LegDef(1, Side.SELL, "call", pick="offset", target=D("10"), link=0, group=0),
    )
    assert [p.strike for p in pick_legs(legs, chain())] == [D("5850"), D("5860")]


def test_a_short_picked_by_premium_or_delta_is_still_refused_in_the_money():
    from dataclasses import replace

    from optionsjourney.bots.strikes import Picked, _sanity

    itm = Picked(0, sym(D("5760"), "call"), D("5760"), "call", Side.SELL, 1, 0, D("40"))
    with pytest.raises(Unsafe, match="The short call at 5760 is in the money"):
        _sanity((itm,), SPOT)
    _sanity((replace(itm, delta=D("0.58")),), SPOT, placed=frozenset({0}))  # by an offset


def test_offset_shorts_are_the_sold_spot_and_offset_legs():
    from optionsjourney.bots.strikes import offset_short

    assert offset_short(LegDef(0, Side.SELL, "put", pick="spot", target=D("-50"), group=0))
    assert offset_short(_diagonal("-50")[1])
    assert not offset_short(_diagonal("-50")[0])  # bought
    assert not offset_short(by_delta("put", "0.16"))
    assert not offset_short(IC[0])  # by premium


# --- #205: a live entry re-reads its legs' quotes before it is priced -----------------

from optionsjourney.bots.strikes import requote  # noqa: E402


def test_requote_reprices_every_leg_from_its_fresh_quote():
    picked = pick_legs(IC, chain())
    fresh = {p.symbol: (p.mid - D("0.05"), p.mid + D("0.05")) for p in picked}
    fresh[picked[2].symbol] = (D("2.95"), D("3.15"))  # the short put moved: mid 3.05
    got, notes = requote(picked, fresh)
    assert [p.mid for p in got] == [picked[0].mid, picked[1].mid, D("3.05"), picked[3].mid]
    assert [p.strike for p in got] == [p.strike for p in picked]  # strikes never move
    assert entry_price(got, D(0)) == entry_price(picked, D(0)) + D("0.55")
    assert notes == ["leg 3 requoted at entry: 2.95 x 3.15, mid 3.05 (the chain had 2.50)"]


def test_requote_keeps_the_deltas_the_strikes_were_picked_on():
    picked = pick_legs((by_delta("put", "0.16"),), delta_chain())
    got, _ = requote(picked, {picked[0].symbol: (D("1.00"), D("1.20"))})
    assert got[0].mid == D("1.10") and got[0].delta == picked[0].delta


@pytest.mark.parametrize(
    "bad",
    [None, (None, D("1")), (D("1"), None), (D("1.2"), D("1.1")), (D("0"), D("0")),
     (D("-0.05"), D("1"))],
)  # fmt: skip
def test_requote_keeps_the_chain_unless_every_leg_has_a_sane_two_sided_quote(bad):
    picked = pick_legs(IC, chain())
    fresh = {p.symbol: (D("1.00"), D("1.10")) for p in picked}
    if bad is None:
        del fresh[picked[1].symbol]
    else:
        fresh[picked[1].symbol] = bad
    assert requote(picked, fresh) == (picked, [])


def test_requote_with_the_same_prices_says_nothing():
    picked = pick_legs(IC, chain())
    same = {p.symbol: (p.mid - D("0.05"), p.mid + D("0.05")) for p in picked}
    got, notes = requote(picked, same)
    assert got == picked and notes == []
