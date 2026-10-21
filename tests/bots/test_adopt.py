from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from optionsjourney.bots import adopt
from optionsjourney.bots.adopt import HeldLeg, Refused
from optionsjourney.bots.definition import iron_condor_skeleton
from optionsjourney.trading.orders import Side

D = Decimal
TODAY = date(2026, 10, 13)


def sym(strike, right):
    return f"SPXW  261013{right[0].upper()}{int(strike * 1000):08d}"


def leg(strike, right, side, price, qty=1, expiration=TODAY):
    return HeldLeg(sym(strike, right), right, D(strike), expiration, side, qty, D(price))


def condor(qty=1):
    return [
        leg(5900, "call", Side.SELL, "2.50", qty),
        leg(5975, "call", Side.BUY, "0.40", qty),
        leg(5700, "put", Side.SELL, "2.00", qty),
        leg(5625, "put", Side.BUY, "0.30", qty),
    ]


def bot(**changes):
    base = iron_condor_skeleton("IC")
    base = replace(
        base, id=7, account_number="5WX01234", live=True, max_per_trade=2,
        groups=tuple(replace(g, stop_pct=D("95"), target_pct=D("50")) for g in base.groups),
    )  # fmt: skip
    return replace(base, **changes)


def test_a_condor_matches_the_bots_sides():
    found = adopt.match(bot(), condor(2), today=TODAY)
    assert found.contracts == 2
    assert found.credit == D("3.80")  # 2.50 - 0.40 + 2.00 - 0.30
    by_position = {leg["position"]: leg for leg in found.legs}
    assert by_position[0]["symbol"] == sym(5900, "call") and by_position[0]["group"] == 0
    assert by_position[1]["symbol"] == sym(5975, "call") and by_position[1]["side"] == "buy"
    assert by_position[2]["symbol"] == sym(5700, "put") and by_position[2]["group"] == 1
    assert by_position[3]["symbol"] == sym(5625, "put")
    assert all(leg["ratio"] == 1 for leg in found.legs)
    fills = {f["symbol"]: f for f in found.filled_legs}
    assert fills[sym(5900, "call")] == {
        "symbol": sym(5900, "call"), "side": "sell", "fill_price": "2.50", "contracts": 2,
    }  # fmt: skip


@pytest.mark.parametrize(
    "legs, opened, words",
    [
        ([], TODAY, "up to 4 legs"),
        ([*condor(), leg(5500, "put", Side.BUY, "0.10")], TODAY, "up to 4 legs"),
        ([leg(5700, "put", Side.SELL, "2.00", qty=0)], TODAY, "contracts"),
        (condor(), date(2026, 10, 12), "opened today"),
    ],
)
def test_trade_problems(legs, opened, words):
    assert words in adopt.trade_problem(legs, opened_on=opened, today=TODAY)


def test_a_trade_opened_yesterday_is_refused():
    assert "today" in adopt.trade_problem(condor(), opened_on=date(2026, 10, 12), today=TODAY)


def test_a_good_trade_has_no_problem():
    assert adopt.trade_problem(condor(), opened_on=TODAY, today=TODAY) is None
    # Any shape of up to 4 option legs, in any expirations: the bot checks the rest.
    spread = [
        leg(5700, "put", Side.SELL, "2.00"),
        leg(5650, "put", Side.BUY, "1.00", expiration=date(2026, 10, 20)),
    ]
    assert adopt.trade_problem(spread, opened_on=TODAY, today=TODAY) is None


@pytest.mark.parametrize(
    "changes, words",
    [
        ({"live": False}, "Paper bot"),
        ({"underlying": "XSP"}, "XSP"),
        ({"max_per_trade": 1}, "per trade"),
        ({"archived": True}, "archived"),
    ],
)
def test_bot_problems(changes, words):
    problem = adopt.bot_problem(bot(**changes), condor(2), "SPX", today=TODAY)
    assert words in problem


def test_a_side_mixing_types_cannot_take_the_trade():
    b = bot()
    legs = list(b.legs)
    legs[1] = replace(legs[1], right="put")
    assert "doesn't match" in adopt.bot_problem(
        replace(b, legs=tuple(legs)), condor(), "SPX", today=TODAY
    )


def test_a_matching_bot_has_no_problem():
    assert adopt.bot_problem(bot(), condor(2), "SPX", today=TODAY) is None


def test_match_refuses_what_bot_problem_refuses():
    with pytest.raises(Refused):
        adopt.match(bot(max_per_trade=1), condor(2), today=TODAY)


def test_the_exits_are_worked_out_like_a_bot_fill():
    found = adopt.match(bot(), condor(), today=TODAY)
    sides = adopt.plan_exits(bot(), found)
    call, put = sides
    assert (call.position, call.short, call.long) == (0, sym(5900, "call"), sym(5975, "call"))
    # 2.50 x 1.95 = 4.875 -> 4.80 on the $0.10 tick (as test_bot_exits' first test)
    assert call.stop.trigger == D("4.80") and call.stop.problem is None
    assert call.target == D("1.05")  # keep 50% of 2.10
    assert put.stop.trigger == D("3.90") and put.target == D("0.85")


def test_a_side_with_no_stop_has_none():
    b = bot()
    b = replace(b, groups=(replace(b.groups[0], stop_pct=None), b.groups[1]))
    call, _ = adopt.plan_exits(b, adopt.match(b, condor(), today=TODAY))
    assert call.stop is None


def test_the_fingerprint_changes_with_any_exit():
    b = bot()
    found = adopt.match(b, condor(), today=TODAY)
    first = adopt.fingerprint(b.id, found, adopt.plan_exits(b, found))
    assert first == adopt.fingerprint(b.id, found, adopt.plan_exits(b, found))
    tighter = replace(b, groups=(replace(b.groups[0], stop_pct=D("90")), b.groups[1]))
    assert first != adopt.fingerprint(b.id, found, adopt.plan_exits(tighter, found))
    assert first != adopt.fingerprint(8, found, adopt.plan_exits(b, found))


def test_the_exits_are_worked_out_on_the_groups_entry():
    # Each side's target is a share of its own entry (stops.target_limit), its size the
    # trade's units.
    found = adopt.match(bot(), condor(2), today=TODAY)
    call, put = adopt.plan_exits(bot(), found)
    assert (call.target, put.target, call.contracts) == (D("1.05"), D("0.85"), 2)


# --- Slice 15: the matcher, widened (analyzer_shape.match) ---------------------------

from optionsjourney.bots.definition import GroupDef, LegDef, shape  # noqa: E402

NEAR, FAR = date(2026, 10, 20), date(2026, 10, 27)


def held(strike, right, side, price="1.00", qty=1, expiration=TODAY, root="SPXW"):
    symbol = f"{root:<6}{expiration:%y%m%d}{right[0].upper()}{int(strike * 1000):08d}"
    return HeldLeg(symbol, right, D(strike), expiration, side, qty, D(price))


def shaped(key, **changes):
    return replace(shape(key, bot()), **changes)


def calendar_bot(**changes):
    legs = (LegDef(0, Side.SELL, "put", dte=7), LegDef(1, Side.BUY, "put", dte=14, link=0,
                                                       pick="offset"))  # fmt: skip
    groups = (GroupDef(0, "Calendar", stop_pct=D("50"), stop_basis="group_entry",
                       target_pct=D("25")),)  # fmt: skip
    base = replace(bot(), legs=legs, groups=groups, close_time="15:45")
    return replace(base, **changes)


def calendar(qty=1, near=NEAR, far=FAR):
    return [held(5700, "put", Side.SELL, "4.00", qty, near),
            held(5700, "put", Side.BUY, "6.00", qty, far)]  # fmt: skip


def test_a_ratio_butterfly_pairs_by_ratio_and_strike():
    fly = shaped("long_call_butterfly", max_per_trade=10)
    legs = [held(5850, "call", Side.SELL, "3.00", 4), held(5800, "call", Side.BUY, "5.00", 2),
            held(5900, "call", Side.BUY, "1.80", 2)]  # fmt: skip
    units, pairs = adopt.pair(fly, legs)
    assert units == 2
    by_position = {b.position: h.strike for h, b in pairs}
    # The bot's two bought legs take the bought calls in strike order.
    assert by_position == {0: D("5850"), 1: D("5800"), 2: D("5900")}
    found = adopt.match(fly, legs, today=TODAY)
    assert found.contracts == 2
    assert found.credit == D("-0.80")  # -5.00 + 2 x 3.00 - 1.80: a debit
    assert {f["symbol"]: f["contracts"] for f in found.filled_legs}[legs[0].symbol] == 4


@pytest.mark.parametrize(
    "key, legs",
    [
        ("short_strangle", [held(5900, "call", Side.SELL), held(5700, "put", Side.SELL)]),
        ("put_credit_spread", [held(5700, "put", Side.SELL, "2.00"),
                               held(5650, "put", Side.BUY, "1.00")]),
    ],
)  # fmt: skip
def test_shapes_that_match(key, legs):
    assert adopt.bot_problem(shaped(key), legs, "SPX", today=TODAY) is None


def test_a_long_put_matches_no_short_put_bot():
    legs = [held(5700, "put", Side.BUY)]
    problem = adopt.bot_problem(shaped("short_put"), legs, "SPX", today=TODAY)
    assert problem == "1 long put doesn't match its legs."


def test_the_wrong_number_of_legs_doesnt_match():
    problem = adopt.bot_problem(bot(), condor()[:2], "SPX", today=TODAY)
    assert problem == "1 short call + 1 long call doesn't match a 4-leg bot."


def test_a_bots_ratios_must_be_in_lowest_terms():
    b = shaped("put_credit_spread", max_per_trade=10)
    b = replace(b, legs=tuple(replace(leg, ratio=2) for leg in b.legs))
    legs = [held(5700, "put", Side.SELL, "2.00", 2), held(5650, "put", Side.BUY, "1.00", 2)]
    assert "lowest terms" in adopt.bot_problem(b, legs, "SPX", today=TODAY)


def test_ratios_must_divide_evenly():
    fly = shaped("long_call_butterfly", max_per_trade=10)
    legs = [held(5850, "call", Side.SELL, "3.00", 3), held(5800, "call", Side.BUY, "5.00", 1),
            held(5900, "call", Side.BUY, "1.80", 1)]  # fmt: skip
    assert "doesn't match" in adopt.bot_problem(fly, legs, "SPX", today=TODAY)


def test_the_size_counts_the_largest_leg():
    fly = shaped("long_call_butterfly", max_per_trade=3)
    legs = [held(5850, "call", Side.SELL, "3.00", 4), held(5800, "call", Side.BUY, "5.00", 2),
            held(5900, "call", Side.BUY, "1.80", 2)]  # fmt: skip
    assert "4 contracts" in adopt.bot_problem(fly, legs, "SPX", today=TODAY)


@pytest.mark.parametrize(
    "b, legs, words",
    [
        # a reverse calendar: the sold leg expires after the bought one
        (calendar_bot(legs=(LegDef(0, Side.SELL, "put", dte=14),
                            LegDef(1, Side.BUY, "put", dte=7))),
         calendar(near=FAR, far=NEAR), "reverse calendar"),
        # a held trade into a same-day bot
        (shaped("put_credit_spread"),
         [held(5700, "put", Side.SELL, "2.00", expiration=NEAR),
          held(5650, "put", Side.BUY, "1.00", expiration=NEAR)], "same-day bot"),
        # a calendar needs the bot's timed close
        (calendar_bot(close_time=None), calendar(), "timed close"),
        # nothing past 1100 days
        (calendar_bot(), calendar(far=date(2029, 12, 21)), "1100 days"),
        # an expired leg
        (calendar_bot(), calendar(near=date(2026, 10, 12)), "expired"),
        # a monthly (AM-settled) SPX option
        (shaped("short_put"), [held(5700, "put", Side.SELL, root="SPX")], "monthly"),
    ],
)  # fmt: skip
def test_held_rules_on_the_trades_dates(b, legs, words):
    assert words in adopt.bot_problem(b, legs, "SPX", today=TODAY)


def test_a_schwab_calendar_bot_takes_a_calendar():
    """Decision 0060: a Schwab bot may hold overnight, as a TastyTrade one may."""
    assert adopt.bot_problem(calendar_bot(broker="schwab"), calendar(), "SPX",
                             today=TODAY) is None  # fmt: skip


def test_a_calendar_goes_to_a_calendar_bot():
    assert adopt.bot_problem(calendar_bot(), calendar(), "SPX",
                             today=TODAY) is None  # fmt: skip
    found = adopt.match(calendar_bot(), calendar(), today=TODAY)
    assert found.credit == D("-2.00")
    assert [(leg["expiration"], leg["dte"]) for leg in found.legs] == [
        ("2026-10-20", 7),
        ("2026-10-27", 14),
    ]


@pytest.mark.parametrize(
    "b, legs",
    [
        (calendar_bot(), [held(5700, "put", Side.SELL, "6.00", 1, NEAR),
                          held(5700, "put", Side.BUY, "4.00", 1, FAR)]),
        (shaped("put_credit_spread"), [held(5700, "put", Side.SELL, "1.00"),
                                       held(5650, "put", Side.BUY, "2.00")]),
    ],
)  # fmt: skip
def test_a_trade_of_either_side_is_taken(b, legs):
    assert adopt.match(b, legs, today=TODAY).credit != 0


def test_a_trade_priced_too_close_to_even_is_refused():
    legs = [held(5700, "put", Side.SELL, "1.00"), held(5650, "put", Side.BUY, "0.97")]
    with pytest.raises(Refused, match=r"Priced too close to even \(0\.03\)"):
        adopt.match(shaped("put_credit_spread"), legs, today=TODAY)


def test_the_fingerprint_changes_when_what_rests_changes():
    found = adopt.match(bot(), condor(), today=TODAY)
    stop = adopt.fingerprint(1, found, adopt.plan_exits(bot(), found))
    chose = replace(bot(), groups=tuple(replace(g, broker_exit="target") for g in bot().groups))
    assert adopt.fingerprint(1, found, adopt.plan_exits(chose, found)) != stop


def test_an_inverted_side_inside_a_credit_condor_is_refused():
    legs = [*condor()[:2], leg(5700, "put", Side.SELL, "0.30"), leg(5625, "put", Side.BUY, "0.50")]
    with pytest.raises(Refused, match="Inverted: the Put side"):
        adopt.match(bot(), legs, today=TODAY)


def test_a_watched_groups_exits():
    found = adopt.match(calendar_bot(), calendar(), today=TODAY)
    (side,) = adopt.plan_exits(calendar_bot(), found)
    assert side.watched and side.short == "" and side.entry == D("-2.00")
    assert side.stop.trigger == D("-1.00")  # 50% of the 2.00 debit lost, as a P&L level
    assert side.target == D("-2.50")  # 25% profit: close receiving 2.50
    assert side.rests == "target"  # a debit fill: the target rests, the stop watched
    assert [leg.symbol for leg in side.legs] == [calendar()[0].symbol, calendar()[1].symbol]


def test_exit_summary():
    assert adopt.exit_summary(bot()) == [
        "Call side: stop 95% · target 50%",
        "Put side: stop 95% · target 50%",
    ]
    b = shaped("short_put")
    assert adopt.exit_summary(b) == ["Short put: no stop · no target"]


# --- Final review: pairs you never traded are refused, ties by expiration ---------------


def two_spreads_bot():
    legs = (LegDef(0, Side.SELL, "put", group=0), LegDef(1, Side.BUY, "put", group=0),
            LegDef(2, Side.SELL, "put", group=1), LegDef(3, Side.BUY, "put", group=1))  # fmt: skip
    groups = (GroupDef(0, "First", stop_pct=D("95")), GroupDef(1, "Second", stop_pct=D("95")))
    return replace(bot(), legs=legs, groups=groups, max_per_trade=4)


def test_two_same_shape_groups_pair_by_lowest_strike_like_tracker2():
    # The author's ruling: Tracker2's analyzer_shape.match, even when the ticket could be
    # split another way; the picker and the check show the pairing first.
    legs = [held(5000, "put", Side.SELL, "5.00"), held(4950, "put", Side.BUY, "1.00"),
            held(4980, "put", Side.SELL, "4.00"), held(4975, "put", Side.BUY, "3.50")]  # fmt: skip
    _, pairs = adopt.pair(two_spreads_bot(), legs)
    assert {b.position: h.strike for h, b in pairs} == {
        0: D(4980),
        1: D(4950),
        2: D(5000),
        3: D(4975),
    }
    assert adopt.group_lines(two_spreads_bot(), pairs) == [
        "First: sell 4980P Oct 13 / buy 4950P Oct 13 · stop 95% · no target",
        "Second: sell 5000P Oct 13 / buy 4975P Oct 13 · stop 95% · no target",
    ]


def test_a_strike_tie_pairs_by_expiration_in_the_bots_dte_order():
    # One group: sell the near put, buy two puts on one strike, the bot's leg 1 the far one
    legs_def = (LegDef(0, Side.SELL, "put", dte=7), LegDef(1, Side.BUY, "put", dte=21),
                LegDef(2, Side.BUY, "put", dte=14))  # fmt: skip
    b = replace(calendar_bot(), legs=legs_def)
    far21 = date(2026, 11, 3)
    legs = [held(5700, "put", Side.SELL, "4.00", 1, NEAR),
            held(5700, "put", Side.BUY, "6.00", 1, FAR),
            held(5700, "put", Side.BUY, "7.00", 1, far21)]  # fmt: skip
    _, pairs = adopt.pair(b, legs)
    assert {bl.position: h.expiration for h, bl in pairs} == {0: NEAR, 1: far21, 2: FAR}


def test_a_condor_and_a_calendar_pass_straight_through_pair():
    units, pairs = adopt.pair(bot(), condor(2))
    assert units == 2
    assert {b.position: h.symbol for h, b in pairs} == {
        0: sym(5900, "call"),
        1: sym(5975, "call"),
        2: sym(5700, "put"),
        3: sym(5625, "put"),
    }
    units, pairs = adopt.pair(calendar_bot(), calendar(3))
    assert units == 3
    assert {b.position: h.expiration for h, b in pairs} == {0: NEAR, 1: FAR}


def test_an_adjusted_etf_option_is_refused():
    """Slice 16: hand-over and the Trade page take the bot's standard root only."""
    spy = replace(shaped("short_put"), underlying="SPY", close_time="15:45")
    adjusted = [held(570, "put", Side.SELL, root="SPY1")]
    words = adopt.bot_problem(spy, adjusted, "SPY", today=TODAY)
    assert "isn't a standard SPY option" in words
    standard = [held(570, "put", Side.SELL, root="SPY")]
    assert adopt.bot_problem(spy, standard, "SPY", today=TODAY) is None


# --- Bots slice 17: futures ------------------------------------------------------------

NOV20 = date(2026, 11, 20)


def fut(strike, right, side, price="12.50", qty=1, contract="ESZ6", series="EW3X6",
        expiration=NOV20):  # fmt: skip
    symbol = f"./{contract} {series} {expiration:%y%m%d}{right[0].upper()}{strike}"
    return HeldLeg(symbol, right, D(strike), expiration, side, qty, D(price))


def es_put_bot(**changes):
    from optionsjourney.bots.definition import GroupDef, LegDef

    base = bot(
        underlying="/ES", legs=(LegDef(0, Side.SELL, "put", dte=3),),
        groups=(GroupDef(0, "Put", stop_pct=None, target_pct=D("50")),), entry_times=(),
    )  # fmt: skip
    return replace(base, **changes)


def test_a_futures_put_goes_to_a_futures_bot_held_past_today():
    """A futures bot holds its trade until its target fills or it expires: the same-day
    rule (and the weekly root) don't apply; its contract must be a month of the root."""
    assert adopt.bot_problem(es_put_bot(), [fut(7000, "put", Side.SELL)], "/ES",
                             today=TODAY) is None  # fmt: skip
    found = adopt.match(es_put_bot(), [fut(7000, "put", Side.SELL, qty=2)], today=TODAY)
    assert found.contracts == 2 and found.credit == D("12.50")
    assert found.legs[0]["symbol"] == "./ESZ6 EW3X6 261120P7000"


@pytest.mark.parametrize(
    ("legs", "underlying", "words"),
    [
        (
            [fut(7000, "put", Side.SELL, contract="MESZ6", series="MW3X6")],
            "/ES",
            "/MESZ6 isn't a /ES contract.",
        ),
        ([fut(7000, "put", Side.SELL, expiration=date(2029, 12, 21))], "/ES",
         "more than 1100 days"),
        ([fut(7000, "put", Side.SELL)], "SPX", "It trades /ES, not SPX."),
    ],
)
def test_futures_held_rules(legs, underlying, words):
    problem = adopt.bot_problem(es_put_bot(), legs, underlying, today=TODAY)
    assert words in problem


def test_an_spx_bot_never_takes_futures():
    assert "It trades SPX, not /ES." in adopt.bot_problem(
        bot(), [fut(7000, "put", Side.SELL)], "/ES", today=TODAY
    )


def test_a_futures_bots_exit_lines_say_target_only():
    """analyzer_shape.exit_summary: "PT 50% · no stop (futures)"."""
    assert adopt.exit_summary(es_put_bot()) == ["Put: target 50% · no stop (futures)"]
    pairs = adopt.pair(es_put_bot(), [fut(7000, "put", Side.SELL)])[1]
    assert adopt.group_lines(es_put_bot(), pairs) == [
        "Put: sell 7000P Nov 20 · target 50% · no stop (futures)"
    ]


def test_a_futures_bots_plan_has_no_stop():
    found = adopt.match(es_put_bot(), [fut(7000, "put", Side.SELL)], today=TODAY)
    (side,) = adopt.plan_exits(es_put_bot(), found)
    assert side.stop is None and side.target == D("6.25")
