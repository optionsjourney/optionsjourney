from dataclasses import replace
from decimal import Decimal

from optionsjourney.bots import stops
from optionsjourney.bots.definition import GroupDef
from optionsjourney.bots.stops import describe, stop_calc

D = Decimal
SHORT = "SPXW  261013C05910000"
FILL, CREDIT = D("2.50"), D("4.90")
SIDE = GroupDef(0, "Call side", stop_pct=D("95"))


def calc(group=SIDE, co_mode="off", co_amount=None, short_fill=FILL, credit=CREDIT):
    return stops.stop_calc(group, co_mode, co_amount, SHORT, short_fill, credit)


def test_no_stop_set_means_no_calc():
    assert calc(replace(SIDE, stop_pct=None)) is None


def test_the_three_styles_round_down_to_the_shorts_tick():
    assert calc().trigger == D("4.80")  # 2.50 x 1.95 = 4.875, $0.10 tick at $3 and up
    assert calc(replace(SIDE, stop_basis="entry_premium")).trigger == D("4.60")  # 4.655
    co = calc(replace(SIDE, stop_basis="closing_order", stop_pct=D("0.20")))
    assert (co.raw, co.trigger, co.problem) == (D("4.70"), D("4.70"), None)
    assert calc(replace(SIDE, stop_pct=D("15")), short_fill=D("1.40")).trigger == D("1.60")


def test_a_leg_premium_stop_is_the_shorts_fill_plus_the_percent():
    # exit_math.stop_trigger's old cases, now only here (stops.leg_trigger)
    assert calc(short_fill=D("2.70")).trigger == D("5.20")  # 5.265, $0.10 tick
    assert calc(short_fill=D("1.40")).trigger == D("2.70")  # 2.73, $0.05 tick


def test_a_dollar_stop_is_the_shorts_fill_plus_the_dollars_and_skips_the_overlay():
    usd = replace(SIDE, stop_unit="usd", stop_usd=D("2.00"), stop_pct=None)
    c = calc(usd, "both", D("0.50"))
    assert (c.basis, c.trigger, c.co_raw, c.problem) == ("usd", D("4.50"), None, None)


def test_every_side_takes_the_closing_order_even_when_looser():
    c = calc(replace(SIDE, stop_basis="entry_premium"), "both", D("0.01"))  # own 4.655, CO 4.89
    assert c.co_won and c.trigger == D("4.80")


def test_tighter_takes_the_lower_raw_trigger_and_a_tie_keeps_the_sides_own():
    won = calc(co_mode="tighter", co_amount=D("0.15"))  # own 4.875, CO 4.75
    assert won.co_won and won.trigger == D("4.70")
    lost = calc(co_mode="tighter", co_amount=D("0.01"))  # CO 4.89 is looser
    assert not lost.co_won and lost.trigger == D("4.80")
    tie = calc(co_mode="tighter", co_amount=D("0.025"))  # CO 4.875, the same as its own
    assert not tie.co_won and tie.trigger == D("4.80")
    # Per side, with different shorts on a 4.40 entry (CO 4.25): the call's CO wins and
    # rounds down once to 4.20; the put's own 3.90 is lower and stays.
    assert calc(co_mode="tighter", co_amount=D("0.15"), credit=D("4.40")).trigger == D("4.20")
    put = calc(co_mode="tighter", co_amount=D("0.15"), short_fill=D("2.00"), credit=D("4.40"))
    assert not put.co_won and put.trigger == D("3.90")


def test_refused_stops_say_why():
    big = calc(replace(SIDE, stop_basis="closing_order", stop_pct=D("5.00")))
    assert big.trigger <= 0 and "at or above the entry credit" in big.problem
    soon = calc(replace(SIDE, stop_basis="entry_premium", stop_pct=D("40")))  # 1.96
    assert soon.trigger == D("1.95") and "fire at once" in soon.problem
    overlay = calc(co_mode="both", co_amount=D("5.00"))
    assert overlay.co_won and "at or above the entry credit" in overlay.problem
    none = calc(replace(SIDE, stop_basis="entry_premium"), credit=None)
    assert none.trigger is None and "no entry credit" in none.problem
    assert calc(credit=None).problem is None  # the short's-fill style doesn't need it


def test_profit_target_levels():
    side = GroupDef(0, "Call side", target_pct=D("50"))
    assert stops.target_limit(side, D("2.45")) == (D("1.225"), None)
    usd = replace(side, target_unit="usd", target_usd=D("1.00"))
    assert stops.target_limit(usd, D("2.45")) == (D("1.45"), None)
    limit, problem = stops.target_limit(replace(usd, target_usd=D("2.45")), D("2.45"))
    assert limit == 0 and "at or above the group's credit" in problem
    assert stops.target_limit(replace(side, target_pct=None), D("2.45")) == (None, None)


def test_the_arithmetic_reads_as_one_line_per_stop():
    assert stops.describe(calc().as_log()["stop_calc"]) == [
        "short 2.50 × (1 + 95%) = 4.875",
        "→ 4.80 (rounded down to the 0.10 tick)",
    ]
    tighter = stops.describe(calc(co_mode="tighter", co_amount=D("0.15")).as_log()["stop_calc"])
    assert tighter == [
        "short 2.50 × (1 + 95%) = 4.875",
        "closing order 4.90 − 0.15 = 4.75",
        "lower wins: closing order",
        "→ 4.70 (rounded down to the 0.10 tick)",
    ]
    usd = replace(SIDE, stop_unit="usd", stop_usd=D("2.00"))
    assert stops.describe(calc(usd).as_log()["stop_calc"]) == [
        "short 2.50 + $2.00 = 4.50",
        "→ 4.50",
    ]
    refused = stops.describe(calc(replace(SIDE, stop_basis="entry_premium", stop_pct=D("40")))
                             .as_log()["stop_calc"])  # fmt: skip
    assert refused[-1].startswith("not placed: ")
    assert stops.describe(None) == []
    no_credit = calc(replace(SIDE, stop_basis="entry_premium"), credit=None).as_log()
    assert stops.describe(no_credit["stop_calc"]) == [
        f"not placed: {no_credit['stop_calc']['problem']}"
    ]
    assert stops.calc_of([{"at": "x"}, calc().as_log()])["trigger"] == "4.80"
    assert stops.calc_of([{"at": "x"}]) is None


def test_the_latest_stop_arithmetic_is_shown():
    first = calc().as_log()
    moved = calc(replace(SIDE, stop_pct=D("50"))).as_log()
    moved["stop_calc"]["moved_by"] = "time action 1"
    latest = stops.calc_of([first, {"at": "x"}, moved])
    assert latest["configured"] == "50"
    assert stops.describe(latest)[0] == "moved by time action 1"


# --- any-legs groups (bots slice 13) ---
def test_a_dollar_stop_on_a_ratio_2_anchor_divides_by_the_ratio():
    usd = replace(SIDE, stop_unit="usd", stop_usd=D("2.00"), stop_pct=None)
    c = stops.stop_calc(usd, "off", None, SHORT, D("1.20"), None, ratio=2)
    assert (c.trigger, c.problem) == (D("2.20"), None)
    assert stops.stop_calc(usd, "off", None, SHORT, D("1.20"), None).trigger == D("3.20")


def test_a_ratio_2_level_divides_by_the_ratio():
    assert stops.level_calc(SHORT, D("1.50"), D("2.40"), D("0"), ratio=2).trigger == D("1.20")


def test_watched_stops_are_a_pnl_level_per_unit():
    usd = GroupDef(0, "Fly", stop_unit="usd", stop_usd=D("1.50"))
    c = stops.watched_calc(usd, D("-3.00"))
    assert (c.basis, c.trigger, c.tick, c.short_fill, c.problem) == (
        "group_usd",
        D("-1.50"),
        None,
        None,
        None,
    )
    pct = GroupDef(0, "Fly", stop_pct=D("50"), stop_basis="group_entry")
    c = stops.watched_calc(pct, D("-3.00"))
    assert (c.basis, c.trigger) == ("group_entry", D("-1.50"))
    assert stops.watched_calc(pct, D("2.00")).trigger == D("-1.00")
    zero = stops.watched_calc(pct, D("0"))
    assert zero.trigger is None and zero.problem
    assert stops.watched_calc(replace(pct, stop_pct=None), D("2.00")) is None
    assert stops.describe(c.as_log()["stop_calc"]) == [
        "P&L −1.50 a contract: entry 3.00 debit × 50%"
    ]


def test_watched_level_is_the_loss_share_of_the_entry():
    c = stops.watched_level(D("2.00"), D("-25"), "profit action")
    assert (c.basis, c.trigger, c.problem) == ("group_level", D("0.50"), None)
    assert stops.watched_level(D("2.00"), D("0"), "x").trigger == D("0.00")
    assert stops.watched_level(D("0"), D("10"), "x").problem


def test_targets_on_a_debit_entry_close_for_a_credit():
    pct = GroupDef(0, "Fly", target_pct=D("50"))
    assert stops.target_limit(pct, D("-3.00")) == (D("-4.50"), None)
    usd = GroupDef(0, "Fly", target_unit="usd", target_usd=D("1.00"))
    assert stops.target_limit(usd, D("-3.00")) == (D("-4.00"), None)
    assert stops.target_limit(pct, D("2.00")) == (D("1.00"), None)
    big = GroupDef(0, "Fly", target_unit="usd", target_usd=D("2.00"))
    limit, problem = stops.target_limit(big, D("2.00"))
    assert limit == 0 and "at or above the group's credit" in problem


def test_a_ratio_2_anchor_prices_every_resting_basis_per_contract():
    entry_p = replace(SIDE, stop_basis="entry_premium")
    c = stops.stop_calc(entry_p, "off", None, SHORT, D("1.00"), D("4.90"), ratio=2)
    assert (c.raw, c.trigger) == (D("2.3275"), D("2.30"))  # 4.655 / 2, rounded down
    co = replace(SIDE, stop_basis="closing_order", stop_pct=D("0.20"))
    c = stops.stop_calc(co, "off", None, SHORT, D("1.00"), D("4.90"), ratio=2)
    assert (c.raw, c.trigger) == (D("2.35"), D("2.35"))  # (4.90 - 0.20) / 2
    c = stops.stop_calc(SIDE, "tighter", D("0.50"), SHORT, D("1.40"), D("4.90"), ratio=2)
    assert (c.co_raw, c.co_won) == (D("2.20"), True)  # (4.90 - 0.50) / 2 < 1.40 x 1.95
    assert c.trigger == D("2.20")
    leg = stops.stop_calc(SIDE, "off", None, SHORT, D("1.00"), D("4.90"), ratio=2)
    assert leg.trigger == D("1.95")  # leg_premium: not divided


def test_ratio_1_keeps_todays_numbers():
    entry_p = replace(SIDE, stop_basis="entry_premium")
    assert stops.stop_calc(entry_p, "off", None, SHORT, FILL, CREDIT, ratio=1).trigger == D("4.60")
    c = stops.stop_calc(SIDE, "tighter", D("0.50"), SHORT, FILL, CREDIT)
    assert (c.co_raw, c.trigger) == (D("4.40"), D("4.40"))


def test_describe_reads_a_moved_group_level_and_a_watched_problem():
    moved = stops.watched_level(D("2.00"), D("-25"), "profit action")
    assert stops.describe(moved.as_log()["stop_calc"]) == [
        "P&L +0.50 a contract: entry 2.00 credit × 25% kept"
    ]
    bad = stops.watched_calc(GroupDef(0, "F", stop_pct=D("0")), D("2.00"))
    assert stops.describe(bad.as_log()["stop_calc"])[-1].startswith("not placed:")
    none = stops.watched_calc(GroupDef(0, "F", stop_pct=D("50")), D("0"))
    assert stops.describe(none.as_log()["stop_calc"])[0].startswith("not placed:")


# --- Bots slice 19: a profit action's stop at a $ P&L level ------------------------------


def test_a_dollar_level_on_a_resting_stop_is_the_shorts_price_at_that_pnl():
    # 2.45 credit, keep 0.40 a contract: the short at 2.05 buys it back for that P&L
    calc = stops.level_usd_calc(SHORT, D("2.50"), D("2.45"), D("0.40"))
    assert (calc.basis, calc.trigger, calc.problem) == ("level_usd", D("2.05"), None)
    described = stops.describe(calc.as_log()["stop_calc"])
    assert described == ["credit 2.45 − P&L +0.40 = 2.05", "→ 2.05"]
    # a loss level: 2.45 + 1.00, rounded down to the dime a single SPX leg has above 3.00
    assert stops.level_usd_calc(SHORT, D("2.50"), D("2.45"), D("-1")).trigger == D("3.40")
    # a ratio-2 anchor: priced per contract, rounded down to the tick
    assert stops.level_usd_calc(SHORT, D("2.50"), D("2.45"), D("0.40"), 2).trigger == D("1.00")
    assert stops.level_usd_calc(SHORT, D("2.50"), D("2.45"), D("2.45")).problem


def test_a_dollar_level_on_a_watched_group_is_that_pnl():
    calc = stops.watched_level_usd(D("-1.20"), D("0.30"), "profit action 1")
    assert (calc.basis, calc.trigger, calc.problem) == ("group_level_usd", D("0.30"), None)
    assert stops.describe(calc.as_log()["stop_calc"]) == ["P&L +0.30 a contract: $ P&L level"]


# --- Decision 0048: "% of entry price lost" on a credit's short -------------------------

SHORT_PUT = "SPXW  261013P05690000"


def test_a_percent_of_the_entry_lost_rests_on_the_short_counting_the_wing_worthless():
    group = GroupDef(0, "Put side", stop_pct=Decimal("100"), stop_basis="group_entry")
    calc = stop_calc(group, "off", None, SHORT_PUT, Decimal("2.50"), Decimal("2.10"),
                     group_entry=Decimal("2.10"))  # fmt: skip
    assert (calc.basis, calc.entry_credit, calc.trigger) == ("entry_lost", Decimal("2.10"),
                                                             Decimal("4.20"))  # fmt: skip
    assert calc.problem is None
    assert describe(calc.as_log()["stop_calc"]) == [
        "group entry 2.10 × (1 + 100%) = 4.20", "→ 4.20",
    ]  # fmt: skip


def test_a_percent_of_the_entry_lost_below_the_shorts_fill_would_fire_at_once():
    group = GroupDef(0, "Put side", stop_pct=Decimal("10"), stop_basis="group_entry")
    calc = stop_calc(group, "off", None, SHORT_PUT, Decimal("2.50"), None,
                     group_entry=Decimal("2.10"))  # fmt: skip
    assert "would fire at once" in calc.problem
    none = stop_calc(group, "off", None, SHORT_PUT, Decimal("2.50"), None)
    assert none.problem == "the group has no entry price to base the stop on"


def test_the_closing_order_overlay_on_a_percent_of_the_entry_lost_keeps_the_trades_credit():
    group = GroupDef(0, "Put side", stop_pct=Decimal("100"), stop_basis="group_entry")
    calc = stop_calc(group, "tighter", Decimal("0.20"), SHORT_PUT, Decimal("2.50"),
                     Decimal("4.90"), group_entry=Decimal("2.10"))  # fmt: skip
    assert calc.trigger == Decimal("4.20")  # the side's own 4.20 is under 4.90 - 0.20
    log = calc.as_log()["stop_calc"]
    assert (log["entry_credit"], log["group_entry"]) == ("4.90", "2.10")
    assert describe(log) == [
        "group entry 2.10 × (1 + 100%) = 4.20",
        "closing order 4.90 − 0.20 = 4.70",
        "lower wins: the side's own",
        "→ 4.20",
    ]
