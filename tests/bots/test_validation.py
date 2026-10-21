from dataclasses import replace
from decimal import Decimal

import pytest

from optionsjourney.bots.definition import (
    EXHAUST_MODES,
    REMAINDERS,
    DefaultAlertDef,
    EntryTimeDef,
    GroupDef,
    LegDef,
    MoveLimits,
    ProfitActionDef,
    TimeActionDef,
    iron_condor_skeleton,
    lone_shorts_rest_stops,
)
from optionsjourney.bots.validation import PAIR_RESTING, validate
from optionsjourney.trading.orders import Side

D = Decimal


def good():
    bot = iron_condor_skeleton("IC")
    targets = {0: D("2.70"), 1: D("75"), 2: D("2.90"), 3: D("-75")}
    legs = tuple(replace(leg, target=targets[leg.position]) for leg in bot.legs)
    groups = tuple(replace(g, stop_pct=D("95")) for g in bot.groups)
    return replace(bot, legs=legs, groups=groups, tag_id=1, account_number="5WX01234",
                   entry_times=(EntryTimeDef("09:47"), EntryTimeDef("12:03")))  # fmt: skip


def good_debit():
    """A bot whose groups stop on the entry price lost (they read the same on a debit fill)."""
    bot = good()
    groups = tuple(replace(g, stop_basis="group_entry") for g in bot.groups)
    return replace(bot, groups=groups)


def test_a_good_bot_passes():
    assert validate(good(), {1}) == {}


def test_problems_are_reported_by_field():
    bot = good()
    blank = replace(bot.legs[0], target=None)
    problems = validate(replace(bot, name="", legs=(blank, *bot.legs[1:])), {1})
    assert "name" in problems and "leg-0" in problems


def test_a_bot_trades_through_tastytrade_or_schwab():
    """Decision 0059: no switch for Schwab."""
    assert validate(replace(good(), broker="schwab"), {1}) == {}
    problems = validate(replace(good(), broker="schwab", account_number=""), {1})
    assert problems["broker"] == "Pick the Schwab account it trades in."
    assert "broker" in validate(replace(good(), broker="manual"), {1})


def test_contracts_and_limits():
    assert "contracts" in validate(replace(good(), contracts=0), {1})
    assert "contracts" in validate(replace(good(), contracts=101), {1})
    big = replace(good(), contracts=100, max_per_trade=100, max_per_day=100)
    assert validate(big, {1}) == {}
    assert "limits" in validate(replace(good(), contracts=3, max_per_trade=2), {1})
    assert "limits" in validate(replace(good(), max_per_day=0), {1})


def test_offset_legs_link_to_a_premium_leg_of_the_same_right():
    bot = good()
    wrong = replace(bot.legs[1], link=2)  # the call wing linked to the put
    assert "leg-1" in validate(replace(bot, legs=(bot.legs[0], wrong, *bot.legs[2:])), {1})


def test_each_side_has_one_short_and_one_long_of_one_right():
    bot = good()
    moved = replace(bot.legs[2], group=0)  # the short put joins the call side
    problems = validate(replace(bot, legs=(*bot.legs[:2], moved, bot.legs[3])), {1})
    assert "group-0" in problems


def test_exit_percentages_and_times():
    bot = good()
    ok = replace(bot.groups[0], target_pct=D("100"))
    assert validate(replace(bot, groups=(ok, bot.groups[1])), {1}) == {}
    over = replace(bot.groups[0], target_pct=D("1001"))
    assert "group-0" in validate(replace(bot, groups=(over, bot.groups[1])), {1})
    assert "times" in validate(replace(bot, entry_times=(EntryTimeDef("15:59"),)), {1})
    assert "times" in validate(replace(bot, entry_times=(EntryTimeDef("9:7"),)), {1})
    assert "times" in validate(replace(bot, entry_times=(EntryTimeDef("10:00", False),)), {1})
    assert "times" in validate(
        replace(bot, entry_times=(EntryTimeDef("10:00"), EntryTimeDef("10:00"))), {1}
    )


def test_a_dte_of_0_to_60_and_a_known_tag():
    bot = good()

    def with_dte(n):
        legs = tuple(replace(leg, dte=n) for leg in bot.legs)
        return validate(replace(bot, legs=legs), {1})

    assert with_dte(0) == {} and with_dte(60) == {}
    assert with_dte(1101)["leg-0"] == "Days to expiration is 0 to 1100."
    assert with_dte(-1)["leg-0"] == "Days to expiration is 0 to 1100."
    assert "tag" in validate(bot, {2})


def calendar(right="put", near=7, far=14, offset="0"):
    """A calendar: sell the near option, buy the far one on the same strike (or `offset`
    points away, a diagonal), one group, opening for a debit (bots slice 14)."""
    sell = LegDef(0, Side.SELL, right, dte=near, pick="premium", target=D("2.90"))
    buy = LegDef(1, Side.BUY, right, dte=far, pick="offset", target=D(offset), link=0)
    group = GroupDef(0, "Calendar", stop_pct=D("50"), stop_basis="group_entry")
    return replace(good(), legs=(sell, buy), groups=(group,), close_time="15:45")


def test_a_calendar_and_a_double_diagonal_are_valid():
    assert validate(calendar(), {1}) == {}
    assert validate(calendar(offset="-25"), {1}) == {}
    double = replace(
        good_debit(),
        legs=(
            LegDef(0, Side.SELL, "call", dte=7, target=D("2.70"), group=0),
            LegDef(1, Side.BUY, "call", dte=14, pick="offset", target=D("25"), link=0, group=0),
            LegDef(2, Side.SELL, "put", dte=7, target=D("2.90"), group=1),
            LegDef(3, Side.BUY, "put", dte=14, pick="offset", target=D("-25"), link=2, group=1),
        ),
        close_time="15:45",
    )
    assert validate(double, {1}) == {}


def test_a_sold_leg_expiring_after_a_bought_leg_of_its_group_is_refused():
    """Question 3: what's left of a group once its nearer legs expire is only bought
    options. A reverse calendar (buy near, sell far) is refused."""
    reverse = calendar(near=14, far=7)
    problems = validate(reverse, {1})
    assert problems == {
        "leg-0": "A sold leg can't expire after a bought leg of its group (a reverse "
        "calendar). Put it in its own group, or give it the nearer DTE."
    }
    # In different groups each expiration is managed on its own: allowed.
    sell, buy = reverse.legs
    groups = (reverse.groups[0], GroupDef(1, "Long", stop_pct=D("50"), stop_basis="group_entry"))
    split = replace(reverse, legs=(sell, replace(buy, group=1, pick="premium", target=D("3"),
                                                link=None)), groups=groups)  # fmt: skip
    assert validate(split, {1}) == {}


def test_an_offset_of_0_needs_another_dte():
    """A calendar's two legs share a strike; in one expiration that's the same option."""
    assert validate(calendar(), {1}) == {}
    same = calendar(near=7, far=7)
    assert validate(same, {1})["leg-1"] == (
        "Enter the offset in whole strike points (for example 75 or -75)."
    )


def test_a_schwab_bot_may_hold_overnight():
    """Decision 0060: Schwab bots were same-day only because every Schwab stop was watched
    by the app (question 6); its stops rest as TastyTrade's do now."""
    assert validate(replace(calendar(), broker="schwab"), {1}) == validate(calendar(), {1})


def test_the_stop_is_between_1_and_1000_percent():
    bot = good()
    for bad in (D("0"), D("0.5"), D("1001")):
        stopped = replace(bot.groups[0], stop_pct=bad)
        assert "group-0" in validate(replace(bot, groups=(stopped, bot.groups[1])), {1})
    for ok in (D("1"), D("1000")):
        stopped = replace(bot.groups[0], stop_pct=ok)
        assert validate(replace(bot, groups=(stopped, bot.groups[1])), {1}) == {}


def test_several_contracts_per_entry_are_allowed():
    assert validate(replace(good(), contracts=2, max_per_trade=2, max_per_day=2), {1}) == {}


def test_ratios_are_1_to_10():
    bot = replace(good(), max_per_trade=11, max_per_day=11)

    def with_ratio(n):
        return validate(replace(bot, legs=(bot.legs[0], replace(bot.legs[1], ratio=n),
                                           *bot.legs[2:])), {1})  # fmt: skip

    assert with_ratio(10) == {}
    for bad in (11, 0):
        assert with_ratio(bad) == {"leg-1": "The ratio is 1 to 10."}
    # Final review M5: a leg too far out says so first (a file names one problem a
    # leg): the app can't trade it at any ratio.
    leg = replace(bot.legs[1], ratio=11, dte=1101)
    problems = validate(replace(bot, legs=(bot.legs[0], leg, *bot.legs[2:])), {1})
    assert problems["leg-1"] == "Days to expiration is 0 to 1100."


def test_entry_time_quantity_and_label():
    bot = replace(good(), max_per_day=5)

    def one(**kw):
        return validate(replace(bot, entry_times=(EntryTimeDef("09:47", **kw),)), {1})

    assert one(quantity=5) == {} and one(quantity=None) == {} and one(label="a" * 50) == {}
    for bad in (0, 101, -1):
        assert one(quantity=bad)["times"] == (
            "entry time 09:47: quantity must be 1 to 100, or blank for the bot's contracts"
        )
    assert "times" in one(label="a" * 51)


def test_an_entry_time_may_have_seconds_on_a_15_second_step():
    bot = good()

    def one(text):
        return validate(replace(bot, entry_times=(EntryTimeDef(text),)), {1}).get("times")

    for fine in ("09:30:15", "09:30:30", "15:57:45"):
        assert one(fine) is None
    assert one("09:30:10") == "09:30:10: seconds go in 15-second steps, like 09:30:15."
    assert one("09:30:00") == "'09:30:00' isn't a time like 09:47 or 09:30:15."  # stored as 09:30
    assert one("09:29:45") == "Entry times are from 09:30 to 15:57 (New York)."


def test_an_entry_time_quantity_over_the_daily_limit_is_a_times_problem():
    bot = replace(good(), max_per_trade=2, max_per_day=3)
    times = (EntryTimeDef("09:47", quantity=4), EntryTimeDef("12:03", quantity=3))
    assert validate(replace(bot, entry_times=times), {1}) == {
        "times": "entry time 09:47: 4 contracts is more than the daily limit of 3"
    }
    at_limit = (EntryTimeDef("09:47", quantity=3),)  # above max_per_trade: clamped when it runs
    assert validate(replace(bot, entry_times=at_limit), {1}) == {}


def test_caps_are_blank_or_at_least_one():
    bot = good()
    assert validate(replace(bot, max_entries_per_day=2, max_open_trades=1), {1}) == {}
    assert "caps" in validate(replace(bot, max_entries_per_day=0), {1})
    assert "caps" in validate(replace(bot, max_open_trades=-1), {1})


def test_premium_bounds_are_checked_only_when_on():
    bot = good()
    assert validate(replace(bot, premium_min=D("-1")), {1}) == {}  # off: ignored
    on = replace(bot, use_premium_bounds=True)
    assert validate(replace(on, premium_min=D("1"), premium_max=D("2")), {1}) == {}
    assert validate(replace(on, premium_max=D("2")), {1}) == {}  # one bound is enough
    assert "premium" in validate(on, {1})  # no bound
    assert "premium" in validate(replace(on, premium_min=D("0")), {1})
    assert "premium" in validate(replace(on, premium_min=D("3"), premium_max=D("2")), {1})
    assert validate(replace(good_debit(), use_premium_bounds=True, premium_min=D("1")), {1}) == {}


def test_frequency_rules():
    bot = good()
    assert "frequency" in validate(replace(bot, frequency="weekly"), {1})
    assert validate(replace(bot, frequency="weekly", entry_days=(1, 3)), {1}) == {}
    assert "frequency" in validate(replace(bot, frequency="weekly", entry_days=(0,)), {1})
    assert "frequency" in validate(replace(bot, frequency="weekly", entry_days=(8,)), {1})
    monthly = replace(bot, frequency="monthly", monthly_week=5, monthly_weekday=5)
    assert validate(monthly, {1}) == {}
    assert "frequency" in validate(replace(monthly, monthly_week=None), {1})
    assert "frequency" in validate(replace(monthly, monthly_week=6), {1})
    assert "frequency" in validate(replace(monthly, monthly_weekday=6), {1})
    dates = replace(bot, frequency="specific_dates", specific_dates=("2026-10-14",))
    assert validate(dates, {1}) == {}
    assert "frequency" in validate(replace(dates, specific_dates=()), {1})
    assert "frequency" in validate(replace(dates, specific_dates=("2026-02-30",)), {1})
    assert "frequency" in validate(replace(bot, frequency="hourly"), {1})
    assert (
        validate(replace(bot, frequency="weekly"), {1})["frequency"]
        == "Weekly needs at least one day ticked."
    )


def test_blackout_days():
    bot = good()
    assert validate(replace(bot, blackout_days=("junk",)), {1}) == {}  # off: ignored
    on = replace(bot, use_blackout_days=True)
    assert validate(replace(on, blackout_days=("2026-12-25",)), {1}) == {}
    assert validate(on, {1})["blackout"] == "Blackout days is on but no date or list is set."
    assert "blackout" in validate(replace(on, blackout_days=("2026-13-01",)), {1})


def with_side(bot, **changes):
    return replace(bot, groups=(replace(bot.groups[0], **changes), bot.groups[1]))


def _fill(leg):
    if leg.pick == "premium":
        return D("2.00")
    return D("75") if leg.right == "call" else D("-75")


def shaped(key, **group):
    """A filled preset, with `group` changes on its first group."""
    from optionsjourney.bots.definition import shape

    bot = shape(key, iron_condor_skeleton("x"))
    legs = tuple(
        leg if leg.target is not None else replace(leg, target=_fill(leg)) for leg in bot.legs
    )
    return replace(bot, legs=legs, tag_id=1, account_number="5WX01234",
                   groups=(replace(bot.groups[0], **group), *bot.groups[1:]),
                   entry_times=(EntryTimeDef("09:47"),))  # fmt: skip


def test_stop_styles_and_their_amounts():
    bot = good()
    assert validate(with_side(bot, stop_basis="entry_premium"), {1}) == {}
    assert validate(with_side(bot, stop_basis="closing_order", stop_pct=D("0.20")), {1}) == {}
    assert "group-0" in validate(with_side(bot, stop_basis="closing_order", stop_pct=D("0")), {1})
    assert "group-0" in validate(with_side(bot, stop_basis="entry_premium", stop_pct=D("0.5")), {1})
    assert "group-0" in validate(with_side(bot, stop_basis="whole"), {1})


def test_dollar_stops_and_targets_are_above_zero():
    bot = good()
    usd = with_side(bot, stop_unit="usd", stop_usd=D("2.00"), stop_pct=None)
    assert validate(usd, {1}) == {}
    assert "group-0" in validate(with_side(bot, stop_unit="usd", stop_usd=D("-1")), {1})
    assert "group-0" in validate(with_side(bot, stop_unit="pts"), {1})
    target = with_side(bot, target_unit="usd", target_usd=D("1.00"))
    assert validate(target, {1}) == {}
    assert "group-0" in validate(with_side(bot, target_unit="usd", target_usd=D("0")), {1})
    assert "group-0" in validate(with_side(bot, target_unit="pips", target_pct=D("50")), {1})


def test_after_a_stop_choices_and_the_limit_price():
    bot = good()
    for mode in REMAINDERS:
        assert validate(with_side(bot, on_stop_remainder=mode), {1}) == {}, mode
    assert "group-0" in validate(with_side(bot, on_stop_remainder="bogus"), {1})
    priced = with_side(bot, on_stop_remainder="close_limit", remainder_limit=D("0.10"))
    assert validate(priced, {1}) == {}
    assert "group-0" in validate(with_side(bot, remainder_limit=D("0")), {1})


def test_the_closing_order_overlay_needs_an_amount_when_on():
    assert validate(replace(good(), stop_co_mode="tighter", stop_co_amount=D("0.15")), {1}) == {}
    assert validate(replace(good(), stop_co_mode="off"), {1}) == {}
    assert "closing_order" in validate(replace(good(), stop_co_mode="tighter"), {1})
    assert "closing_order" in validate(
        replace(good(), stop_co_mode="both", stop_co_amount=D("0")), {1}
    )
    assert "closing_order" in validate(
        replace(good(), stop_co_mode="sometimes", stop_co_amount=D("0.15")), {1}
    )


def test_exit_exhaust_mode_must_be_known():
    for mode in EXHAUST_MODES:
        assert (
            validate(replace(good(), exit_chase=replace(good().exit_chase, exhaust_mode=mode)), {1})
            == {}
        )
    odd = replace(good(), exit_chase=replace(good().exit_chase, exhaust_mode="keep_going"))
    assert "chase_exhaust" in validate(odd, {1})


def test_validation_accepts_keep_conceding():
    keep = replace(good(), exit_chase=replace(good().exit_chase, exhaust_mode="keep_conceding"))
    assert validate(keep, {1}) == {}


def test_natural_minutes_is_1_to_30():
    def with_minutes(n):
        chase = replace(good().exit_chase, exhaust_mode="natural_then_leg_out", natural_minutes=n)
        return validate(replace(good(), exit_chase=chase), {1})

    assert with_minutes(1) == {} and with_minutes(30) == {}
    assert "chase_exhaust" in with_minutes(0) and "chase_exhaust" in with_minutes(31)


def test_new_bots_get_natural_then_leg_out():
    assert iron_condor_skeleton("IC").exit_chase.exhaust_mode == "natural_then_leg_out"
    assert iron_condor_skeleton("IC").exit_chase.natural_minutes == 2


# Slice 5: market filters and date lists (Tracker2's validate_vix_level,
# _move_limit_errors, validate_vix9d_ratio, validate_technicals, _validate_date_sets)


def test_a_filter_that_is_off_is_not_checked():
    off = replace(good(), vix_min=D("-1"), sma_mode="sideways", vix9d_ratio_max=D("0"))
    assert validate(off, {1}) == {}


def test_vix_level():
    on = replace(good(), use_vix_level=True)
    assert validate(on, {1})["vix_level"].startswith("VIX level is on")
    assert validate(replace(on, vix_max=D("20")), {1}) == {}
    assert "vix_level" in validate(replace(on, vix_min=D("0")), {1})
    assert "vix_level" in validate(replace(on, vix_min=D("25"), vix_max=D("20")), {1})


def test_vix9d_ratio():
    on = replace(good(), use_vix9d_ratio=True)
    assert validate(on, {1})["vix9d_ratio"].startswith("VIX9D/VIX ratio is on")
    assert validate(replace(on, vix9d_ratio_min=D("0.8"), vix9d_ratio_max=D("1")), {1}) == {}
    assert "vix9d_ratio" in validate(replace(on, vix9d_ratio_max=D("-1")), {1})
    assert "vix9d_ratio" in validate(
        replace(on, vix9d_ratio_min=D("1.1"), vix9d_ratio_max=D("1")), {1}
    )


def test_move_fields():
    for toggle, field, key in (
        ("use_vix_overnight", "vix_overnight", "vix_overnight"),
        ("use_intraday_move", "intraday", "intraday_move"),
    ):
        on = replace(good(), **{toggle: True})
        assert "is on" in validate(on, {1})[key]
        assert validate(replace(on, **{field: MoveLimits(max_up=D("3"))}), {1}) == {}
        assert validate(replace(on, **{field: MoveLimits(min_up=D("0"))}), {1}) == {}
        assert key in validate(replace(on, **{field: MoveLimits(max_down=D("-1"))}), {1})
        bad = MoveLimits(min_down=D("2"), max_down=D("1"))
        assert key in validate(replace(on, **{field: bad}), {1})


def test_technicals():
    on = replace(good(), use_technicals=True)
    assert "neither" in validate(on, {1})["technicals"]
    assert validate(replace(on, sma_mode="above", sma_days_a=2), {1}) == {}
    assert "technicals" in validate(replace(on, sma_mode="above", sma_days_a=1), {1})
    assert "technicals" in validate(replace(on, sma_mode="above", sma_days_a=201), {1})
    assert "technicals" in validate(replace(on, sma_mode="above"), {1})
    assert validate(replace(on, sma_mode="compare", sma_days_a=1, sma_days_b=5), {1}) == {}
    assert "technicals" in validate(
        replace(on, sma_mode="compare", sma_days_a=5, sma_days_b=5), {1}
    )
    assert "technicals" in validate(replace(on, sma_mode="compare", sma_days_a=5), {1})
    assert validate(replace(on, ema_mode="below", ema_minutes_a=1), {1}) == {}
    assert "technicals" in validate(replace(on, ema_mode="below", ema_minutes_a=391), {1})
    assert "technicals" in validate(replace(on, ema_mode="sideways", ema_minutes_a=5), {1})
    both = replace(on, sma_mode="above", sma_days_a=5, ema_mode="compare", ema_minutes_a=5,
                   ema_minutes_b=20)  # fmt: skip
    assert validate(both, {1}) == {}


def test_date_lists():
    dates = replace(good(), frequency="specific_dates")
    assert "frequency" in validate(dates, {1})
    assert validate(replace(dates, specific_date_sets=("FOMC",)), {1}) == {}
    assert "frequency" in validate(replace(dates, specific_date_sets=("NFP",)), {1})
    on = replace(good(), use_blackout_days=True)
    assert validate(replace(on, blackout_date_sets=("CPI", "EOM")), {1}) == {}
    assert "blackout" in validate(replace(on, blackout_date_sets=("XYZ",)), {1})


def test_market_holiday_is_a_before_expiration_list_only():
    """#207 (Tracker2 04eaa8e4, ef862660): no session on a holiday, so it's never a day to
    trade on or an entry day; Before expiration alone is enough to turn blackout days on."""
    dates = replace(good(), frequency="specific_dates")
    assert "frequency" in validate(replace(dates, specific_date_sets=("HOLIDAY",)), {1})
    on = replace(good(), use_blackout_days=True)
    assert validate(replace(on, blackout_dte_sets=("HOLIDAY", "FOMC")), {1}) == {}
    assert "blackout" in validate(replace(on, blackout_date_sets=("HOLIDAY",)), {1})
    assert "blackout" in validate(replace(on, blackout_dte_sets=("NFP",)), {1})
    off = replace(good(), blackout_dte_sets=("NFP",))  # ignored while blackout days is off
    assert validate(off, {1}) == {}


# Slice 4: timed close and time actions


def test_close_time_is_blank_or_inside_the_session():
    assert "timed_close" not in validate(replace(good(), close_time=None), {1})
    assert "timed_close" not in validate(replace(good(), close_time="15:45"), {1})
    for bad in ("9:45", "16:00", "09:29", "noon"):
        assert "timed_close" in validate(replace(good(), close_time=bad), {1}), bad


def test_close_dte_is_zero_or_more():
    assert "timed_close" not in validate(replace(good(), close_time="15:45", close_dte=2), {1})
    assert "timed_close" in validate(replace(good(), close_time="15:45", close_dte=-1), {1})


def test_an_entry_time_at_or_after_the_timed_close_is_refused():
    problems = validate(replace(good(), close_time="12:03"), {1})
    assert "12:03 is at or after the timed close" in problems["timed_close"]
    paused = replace(good(), close_time="12:00",
                     entry_times=(EntryTimeDef("09:47"), EntryTimeDef("12:30", False)))  # fmt: skip
    assert "timed_close" in validate(paused, {1})


def test_entry_after_close_time_is_fine_when_the_trade_isnt_due_that_day():
    """Bots slice 14: the entry rule guards a trade that would be due the day it opens,
    which is only when its nearest DTE is within close_dte."""
    late = replace(calendar(), close_time="12:00", close_dte=0)  # entries at 09:47 and 12:03
    assert "timed_close" not in validate(late, {1})
    due = replace(late, close_dte=7)  # 7 DTE or fewer: due the day it opens
    assert "12:03 is at or after the timed close" in validate(due, {1})["timed_close"]


def row(**kw):
    values = dict(type="dte", days=0, at="14:00", stop_pct=None, target_pct=D("50"))
    return TimeActionDef(**(values | kw))


def test_time_actions_need_a_row_and_at_most_ten():
    assert "time_actions" in validate(replace(good(), use_time_actions=True), {1})
    many = replace(good(), use_time_actions=True, time_actions=(row(),) * 11)
    assert "at most 10" in validate(many, {1})["time_actions"]
    assert "time_actions" not in validate(
        replace(good(), use_time_actions=True, time_actions=(row(),)), {1}
    )


def test_a_time_action_row_is_checked():
    def problem(**kw):
        bot = replace(good(), use_time_actions=True, time_actions=(row(**kw),))
        return validate(bot, {1}).get("time_actions")

    assert problem() is None
    assert problem(type="mit")
    assert problem(days=-1)
    assert problem(at="16:00") and problem(at="9:00")
    assert problem(stop_pct=D("0")) and problem(stop_pct=D("1001"))
    assert problem(stop_pct=D("200")) is None
    assert problem(target_pct=D("100")) is None and problem(target_pct=D("0"))
    assert problem(target_pct=D("1001"))
    assert "a stop % or a target %" in problem(target_pct=None)


def test_time_actions_are_ignored_while_unticked():
    bot = replace(good(), use_time_actions=False, time_actions=(row(type="mit"),))
    assert "time_actions" not in validate(bot, {1})


def test_time_action_rows_are_known_by_what_they_say():
    assert row().sig == row().sig
    assert row(at="14:00").sig != row(at="14:05").sig
    assert row(target_pct=D("50")).sig == row(target_pct=D("50.0")).sig
    assert row(days=1, stop_pct=D("150")).label == "1 DTE @ 14:00: stop 150%, target 50%"


def test_too_many_tags_is_a_problem():
    from optionsjourney.bots.definition import MAX_TAGS

    assert validate(replace(good(), tags=tuple(f"t{n}" for n in range(MAX_TAGS))), {1}) == {}
    tags = tuple(f"t{n}" for n in range(MAX_TAGS + 1))
    assert "tags" in validate(replace(good(), tags=tags), {1})


# --- Final review fixes (slice 8): what a hostile file could slip past the form ------


def test_spx_or_a_typed_ticker_is_an_underlying():
    """Bots slice 16, the author's ruling: free text, as Tracker2's f-underlying."""
    assert validate(replace(good(), underlying="SPX"), {1}) == {}
    assert validate(replace(good(), underlying="TSLA"), {1}) == {}
    for underlying in ("spx", "", "X" * 200_000, "NDX"):
        problems = validate(replace(good(), underlying=underlying), {1})
        assert "underlying" in problems and "SPX" in problems["underlying"], underlying
    # Bots slice 17: a futures root is an underlying; a contract month isn't
    problems = validate(replace(good(), underlying="/ESZ6"), {1})
    assert problems["underlying"] == "Enter the root, /ES, not a contract month."


def test_one_to_four_legs_in_one_to_four_groups():
    bot = good()
    assert "legs" in validate(replace(bot, legs=(), groups=()), {1})
    extra = replace(bot.legs[1], position=4)
    assert "legs" in validate(replace(bot, legs=(*bot.legs, extra)), {1})
    one_group = replace(bot, legs=bot.legs[:2], groups=bot.groups[:1])
    assert validate(one_group, {1}) == {}
    five = tuple(GroupDef(n, f"G{n}") for n in range(5))
    assert "legs" in validate(replace(bot, groups=five), {1})
    empty = validate(replace(bot, groups=(*bot.groups, GroupDef(2, "Spare"))), {1})
    assert empty == {"group-2": "This group has no legs."}
    lost = replace(bot, groups=bot.groups[:1])
    assert validate(lost, {1}) == {"legs": "Every leg belongs to a group."}


def test_group_names_are_unique():
    bot = good()
    same = replace(bot, groups=(bot.groups[0], replace(bot.groups[1], name=bot.groups[0].name)))
    problems = validate(same, {1})
    assert list(problems) == ["group-1"] and "name" in problems["group-1"]


def test_a_spot_offset_of_zero_is_at_the_money():
    bot = good()

    def spot(index, target):
        leg = replace(bot.legs[index], pick="spot", link=None, target=target)
        legs = list(bot.legs)
        legs[index] = leg
        return validate(replace(bot, legs=tuple(legs)), {1})

    for index in range(4):
        assert spot(index, D("0")) == {}, index
    assert "leg-2" in spot(2, D("50"))  # a short put's points are below the price
    assert "leg-0" in spot(0, D("-50"))
    assert "leg-0" in spot(0, D("1.5"))


def test_a_watched_group_ignores_the_remainder_settings():
    bot = shaped("long_call", on_stop_remainder="bogus", remainder_limit=D("0"))
    assert validate(bot, {1}) == {}


@pytest.mark.parametrize(
    "phase, change",
    [
        ("entry_chase", {"max_tries": 61}),
        ("exit_chase", {"max_tries": 999_999_999}),
        ("entry_chase", {"interval_seconds": 61}),
        ("entry_chase", {"interval_seconds": 0}),
        ("entry_chase", {"step": D("0.000001")}),
        ("entry_chase", {"step": D("0.07")}),
        ("exit_chase", {"step": D("1.05")}),
        ("entry_chase", {"start_offset": D("500")}),
        ("entry_chase", {"start_offset": D("2.05")}),
        ("exit_chase", {"start_offset": D("-0.05")}),
        ("entry_chase", {"exhaust_mode": "keep_going"}),
        ("entry_chase", {"natural_minutes": 0}),
    ],
)
def test_chase_settings_are_bounded(phase, change):
    bot = good()
    chase = replace(getattr(bot, phase), **change)
    problems = validate(replace(bot, **{phase: chase}), {1})
    assert "chase" in problems or "chase_exhaust" in problems, change


def test_chase_settings_at_their_limits_pass():
    bot = good()
    edge = replace(bot.entry_chase, max_tries=60, interval_seconds=60, step=D("1.00"),
                   start_offset=D("2.00"))  # fmt: skip
    low = replace(bot.exit_chase, max_tries=1, interval_seconds=1, step=D("0.05"),
                  start_offset=D("0"))  # fmt: skip
    assert validate(replace(bot, entry_chase=edge, exit_chase=low), {1}) == {}


# --- Slice 7: watch rules, profit actions and a time action's close % ------------------


def test_a_time_action_can_close_part_and_keeps_its_old_sig_without_one():
    def problem(**kw):
        bot = replace(good(), use_time_actions=True, time_actions=(row(**kw),))
        return validate(bot, {1}).get("time_actions")

    assert problem(target_pct=None, close_pct=50) is None
    assert problem(close_pct=0) and problem(close_pct=100)
    assert problem(target_pct=None, close_pct=None)
    assert row(close_pct=50).sig == row().sig + "|close 50"
    assert row(close_pct=50).label == "0 DTE @ 14:00: target 50%, close 50%"


def test_a_time_action_without_a_close_keeps_its_old_sig():
    # What slice 4 stored as the sig of a fired row, so it never fires twice.
    assert row(stop_pct=D("150")).sig == "dte|0|14:00|150|50"


def test_watch_rules_are_checked_only_while_ticked():
    def problem(**kw):
        return validate(replace(good(), **kw), {1}).get("watch_rules")

    assert problem(position_delta_above=D("500")) is None  # unticked: ignored
    assert "nothing" in problem(use_delta_exit=True)
    assert problem(use_delta_exit=True, position_delta_above=D("101"))
    assert problem(use_delta_exit=True, position_delta_below=D("10"),
                   position_delta_above=D("5"))  # fmt: skip
    assert problem(use_delta_exit=True, position_delta_below=D("-30"),
                   position_delta_above=D("30")) is None  # fmt: skip
    assert "nothing" in problem(use_sl_ratio_exit=True)
    assert problem(use_sl_ratio_exit=True, sl_ratio_below=D("0"))
    assert problem(use_sl_ratio_exit=True, sl_ratio_below=D("0.78")) is None
    assert "nothing" in problem(use_underlying_exit=True)
    assert problem(use_underlying_exit=True, underlying_up_pct=D("0"))
    assert problem(use_underlying_exit=True, underlying_up_pct=D("1.5")) is None
    assert problem(use_underlying_exit=True, short_call_tested_pts=D("-5")) is None


EIGHTY = D("80")


def _with_actions(*actions, target_pct=EIGHTY, on=True):
    bot = good()
    side = replace(bot.groups[0], target_pct=target_pct, use_profit_actions=on,
                   profit_actions=actions)  # fmt: skip
    return validate(replace(bot, groups=(side, bot.groups[1])), {1}).get("profit-0")


def test_profit_actions_are_checked():
    from optionsjourney.bots.definition import ProfitActionDef as A

    assert _with_actions(A(D("50"), 33, D("0"))) is None
    assert _with_actions(A(D("500"), 33, None), on=False) is None  # unticked: ignored
    assert "add at least one" in _with_actions()
    assert "at most 5" in _with_actions(*(A(D(n), 0, D("0")) for n in range(10, 70, 10)))
    assert _with_actions(A(D("0"), 33, None))
    assert _with_actions(A(D("80"), 33, None))  # not below the side's 80% target
    assert _with_actions(A(D("100"), 33, None), target_pct=None) is None
    assert _with_actions(A(D("1000"), 33, None), target_pct=None)
    assert _with_actions(A(D("50"), 33, None), A(D("40"), 33, None))  # not increasing
    assert _with_actions(A(D("30"), 60, None), A(D("50"), 40, None))  # 100% in all
    assert _with_actions(A(D("30"), 100, None))
    assert "does nothing" in _with_actions(A(D("30"), 0, None))
    assert _with_actions(A(D("30"), 0, D("-30")))  # at or above the +30% it fires at
    assert _with_actions(A(D("30"), 0, D("-29"))) is None
    assert _with_actions(A(D("30"), 0, D("-100")))
    assert _with_actions(A(D("30"), 0, D("0")), A(D("50"), 0, D("10")))  # looser later
    assert _with_actions(A(D("30"), 0, D("0")), A(D("50"), 0, D("-20"))) is None


def test_alert_settings_are_checked():
    from optionsjourney.bots.definition import DefaultAlertDef as A

    assert validate(replace(good(), alert_name="x" * 40, alert_channel="telegram"), {1}) == {}
    assert "alert_name" in validate(replace(good(), alert_name="x" * 41), {1})
    assert "alert_channel" in validate(replace(good(), alert_channel="email"), {1})
    # default alerts only while ticked; ticked with none is refused (validate_default_alerts)
    assert validate(replace(good(), default_alerts=(A("pnl_pct", "sideways", D(5)),)), {1}) == {}
    on = replace(good(), use_default_alerts=True)
    assert "no alert rules" in validate(on, {1})["default_alerts"]
    for rule, words in [
        (A("pnl_pct", "sideways", D(5)), "above or below"),
        (A("pnl_pct", "above", D(0)), "non-zero"),
        (A("pnl_pct", "above", None), "non-zero"),
        (A("pnl_pct", "above", D(1001)), "non-zero"),
        (A("strike_offset", points=D(-1)), "0 to 1000"),
        (A("strike_offset"), "0 to 1000"),
        (A("email"), "P&L % or near a short strike"),
    ]:
        assert words in validate(replace(on, default_alerts=(rule,)), {1})["default_alerts"]
    twice = (A("pnl_pct", "above", D(15)), A("pnl_pct", "above", D("15.0")))
    assert "duplicate" in validate(replace(on, default_alerts=twice), {1})["default_alerts"]
    many = tuple(A("pnl_pct", "above", D(n)) for n in range(1, 12))
    assert "at most 10" in validate(replace(on, default_alerts=many), {1})["default_alerts"]
    fine = (A("pnl_pct", "above", D(15)), A("pnl_pct", "below", D(-50)),
            A("strike_offset", points=D(0)))  # fmt: skip
    assert validate(replace(on, default_alerts=fine), {1}) == {}


# --- Slice 10: delta and spot picks, the floating window --------------------------------


def with_leg(bot, position, **changes):
    legs = tuple(replace(leg, **changes) if leg.position == position else leg for leg in bot.legs)
    return replace(bot, legs=legs)


def test_a_delta_leg_takes_a_delta_from_001_to_099():
    bot = with_leg(good(), 0, pick="delta", target=D("0.16"))
    assert validate(bot, {1}) == {}  # and the call wing measures from it
    for bad in (None, D("0"), D("1"), D("16"), D("-0.16")):
        assert "leg-0" in validate(with_leg(good(), 0, pick="delta", target=bad), {1})


def test_a_spot_leg_is_whole_points_and_a_short_points_out_of_the_money():
    assert validate(with_leg(good(), 2, pick="spot", target=D("-50")), {1}) == {}
    assert validate(with_leg(good(), 0, pick="spot", target=D("50")), {1}) == {}
    for bad in (None, D("-50.5"), D("50")):
        assert "leg-2" in validate(with_leg(good(), 2, pick="spot", target=bad), {1})
    assert "leg-0" in validate(with_leg(good(), 0, pick="spot", target=D("-50")), {1})


def test_an_offset_can_measure_from_any_leg_that_isnt_an_offset():
    bot = with_leg(with_leg(good(), 2, pick="spot", target=D("-40")), 0, pick="delta",
                   target=D("0.10"))  # fmt: skip
    assert validate(bot, {1}) == {}
    chained = with_leg(good(), 3, link=1)  # the put wing from the call wing
    assert "leg-3" in validate(chained, {1})


def test_unknown_picks_are_refused():
    assert "leg-0" in validate(with_leg(good(), 0, pick="gamma"), {1})


def floating(start="11:30", end="11:45", **more):
    return replace(good(), use_floating_entry=True, floating_start=start, floating_end=end,
                   **more)  # fmt: skip


def test_a_floating_window_needs_two_times_in_order_inside_the_entry_hours():
    assert validate(floating(), {1}) == {}
    for start, end in (
        (None, "11:45"), ("11:30", None), ("11:45", "11:30"), ("11:30", "11:30"),
        ("09:29", "10:00"), ("15:00", "15:58"), ("9:30", "10:00"),
    ):  # fmt: skip
        assert "floating" in validate(floating(start, end), {1}), (start, end)
    assert validate(floating("09:30", "15:57"), {1}) == {}


def test_a_floating_bot_needs_no_entry_times_and_ignores_them():
    assert validate(floating(entry_times=()), {1}) == {}
    off = replace(good(), entry_times=())
    assert "times" in validate(off, {1})


def test_the_window_is_ignored_while_floating_is_off():
    bot = replace(good(), floating_start="nonsense", floating_end=None)
    assert validate(bot, {1}) == {}


def test_a_floating_window_ending_at_or_after_the_timed_close_is_refused():
    assert "timed_close" in validate(floating(close_time="11:45"), {1})
    assert validate(floating(close_time="11:46", entry_times=()), {1}) == {}


def test_the_delta_message_names_the_range_that_is_allowed():
    problem = validate(with_leg(good(), 0, pick="delta", target=D("1")), {1})["leg-0"]
    assert "0.01 to 0.99" in problem and "1 to 99" not in problem


def with_ratio_two(bot):
    return replace(bot, legs=(replace(bot.legs[0], ratio=2), *bot.legs[1:]))


def test_contracts_times_the_largest_ratio_must_fit_the_limits():
    bot = replace(with_ratio_two(good()), contracts=2, max_per_trade=3, max_per_day=4)
    assert validate(bot, {1})["limits"] == (
        "Contracts per entry × the largest leg's ratio (2) is more than the limits."
    )
    assert validate(replace(bot, max_per_trade=4), {1}) == {}
    assert "limits" in validate(replace(bot, max_per_trade=4, max_per_day=3), {1})
    # an entry-time quantity likewise, against the daily limit
    times = (EntryTimeDef("09:47", quantity=3),)
    fits = replace(bot, max_per_trade=6, max_per_day=6, entry_times=times)
    assert validate(fits, {1}) == {}
    refused = validate(replace(fits, max_per_day=5), {1})
    assert "times" in refused and "largest leg" in refused["times"]


ONE_LIMIT = (
    "A limit price works only when one leg is left after the stop; leave it blank to use "
    "each leg's bid."
)


def test_a_typed_limit_price_needs_exactly_one_leg_left_after_the_stop():
    # A credit put spread sold 2 to 1 with two bought puts: two legs are left after the
    # stop, so one typed price can't fit both. A vertical has one: it's allowed.
    from optionsjourney.bots.definition import LegDef, Side

    bot = shaped("put_credit_spread", on_stop_remainder="close_limit", remainder_limit=D("0.30"))
    assert validate(bot, {1}) == {}
    extra = LegDef(2, Side.BUY, "put", pick="offset", target=D("-100"), link=0)
    legs = (*bot.legs, extra)
    assert validate(replace(bot, legs=legs), {1}) == {"group-0": ONE_LIMIT}
    blank = replace(bot, legs=legs, groups=(replace(bot.groups[0], remainder_limit=None),))
    assert validate(blank, {1}) == {}


def test_time_and_profit_action_stops_say_group_and_entry_price():
    """Task 17 wording: a group's stop, measured on its entry price (credit or debit)."""
    from optionsjourney.bots.definition import ProfitActionDef, TimeActionDef
    from optionsjourney.bots.validation import _action_problem, _profit_actions

    row = TimeActionDef("dte", 0, "14:00", stop_pct=D("2000"))
    assert _action_problem(row) == (
        "the stop % is between 1 and 1000, measured like the group's own % stop."
    )
    group = GroupDef(0, "Put spread", use_profit_actions=True,
                     profit_actions=(ProfitActionDef(D("50"), 0, D("-200")),))  # fmt: skip
    assert _profit_actions(group) == (
        "profit action 1: the stop is a % of the entry price lost, -99 to 1000."
    )


def test_a_schwab_bot_may_turn_exact_dte_off():
    bot = replace(good(), use_exact_dte=False)
    assert validate(replace(bot, broker="schwab"), {1}) == validate(bot, {1})


def test_a_calendar_must_have_a_timed_close():
    """The author's rule: a multi-expiry group never reaches its near expiration."""
    problems = validate(replace(calendar(), close_time=None), {1})
    assert problems == {
        "timed_close": "A calendar or diagonal must close before its near legs expire: set "
        "a timed close."
    }
    # Legs held overnight in one expiration need none
    held = replace(good(), legs=tuple(replace(leg, dte=7) for leg in good().legs))
    assert validate(held, {1}) == {}


# --- Bots slice 16: other underlyings ---


def closing(bot, at="15:45"):
    return replace(bot, close_time=at)


@pytest.mark.parametrize("underlying", ["SPX", "XSP", "SPY", "QQQ", "IWM", "AAPL"])
def test_every_listed_underlying_saves(underlying):
    assert validate(closing(replace(good(), underlying=underlying)), {1}) == {}


def test_another_index_is_refused():
    problems = validate(replace(good(), underlying="RUT"), {1})
    assert problems["underlying"] == "Bots trade SPX and XSP among the indexes, not RUT."


def test_cent_chase_steps_on_a_penny_underlying_only():
    bot = closing(replace(good(), underlying="XSP"))
    cents = replace(bot.entry_chase, step=D("0.03"), start_offset=D("0.07"))
    assert validate(replace(bot, entry_chase=cents), {1}) == {}
    problems = validate(replace(bot, underlying="SPX", entry_chase=cents), {1})
    assert problems["chase"] == (
        "The entry chase: the step is $0.05 to $1.00, in $0.05 steps (SPX's tick)."
    )
    thin = replace(bot.exit_chase, step=D("0.005"))
    problems = validate(replace(bot, exit_chase=thin), {1})
    assert problems["chase"] == "The exit chase: the step is $0.01 to $1.00, in $0.01 steps."
    odd = replace(bot.exit_chase, start_offset=D("0.015"))
    problems = validate(replace(bot, exit_chase=odd), {1})
    assert problems["chase"] == "The exit chase: the start offset is $0 to $2.00, in $0.01 steps."


def test_a_spot_leg_names_the_underlying():
    bot = replace(good(), underlying="QQQ", close_time="15:45")
    leg = replace(bot.legs[0], pick="spot", target=D("2.5"))
    problems = validate(replace(bot, legs=(leg, *bot.legs[1:])), {1})
    assert problems["leg-0"].startswith("Enter the points from the QQQ price")


def test_an_american_seller_needs_no_timed_close():
    """The author's ruling (2026-10-13), as Tracker2: the 15:50 expiry alert warns."""
    for underlying in ("SPY", "AAPL"):
        assert "timed_close" not in validate(replace(good(), underlying=underlying), {1})


# --- Bots slice 17: futures bots (decision 0043) -----------------------------------------


def futures_put():
    """A /ES short put bot: a profit target only, no stop, no entry time (it enters only
    from the Trade page). Its leg's pick has no target: the ticket brings the strike."""
    leg = LegDef(0, Side.SELL, "put", pick="premium", target=None, dte=3)
    group = GroupDef(0, "Put", stop_pct=None, target_pct=D("50"))
    return replace(good(), underlying="/ES", legs=(leg,), groups=(group,), entry_times=())


def test_a_futures_bot_with_a_target_only_saves():
    assert validate(futures_put(), {1}) == {}


@pytest.mark.parametrize(
    ("change", "key", "words"),
    [
        (dict(broker="schwab"), "broker", "Futures options trade through TastyTrade only."),
        (
            dict(groups=(GroupDef(0, "Put", stop_pct=D("95"), target_pct=D("50")),)),
            "group-0",
            "Futures: profit target only, no stop. Leave the stop blank.",
        ),
        (
            dict(groups=(GroupDef(0, "Put", stop_pct=None, target_pct=None),)),
            "group-0",
            "Set a profit target on every group: it's a futures trade's only exit.",
        ),
        (dict(close_time="15:45"), "futures", "turn off the timed close"),
        (
            dict(use_time_actions=True, time_actions=(TimeActionDef("dte", 0, "14:00"),)),
            "futures",
            "turn off the time actions",
        ),
        (
            dict(use_delta_exit=True, position_delta_below=-30),
            "futures",
            "turn off the watch rules",
        ),
        (
            dict(stop_co_mode="both", stop_co_amount=D("0.50")),
            "futures",
            "turn off the closing order",
        ),
        (
            dict(use_floating_entry=True, floating_start="10:00", floating_end="11:00"),
            "futures",
            "turn off the entry window",
        ),
        (dict(use_vix_level=True, vix_max=D("30")), "futures", "turn off the market filters"),
    ],
)
def test_what_a_futures_bot_cant_have(change, key, words):
    problems = validate(replace(futures_put(), **change), {1})
    assert words in problems[key], problems


def test_a_futures_bots_profit_actions_are_refused():
    group = replace(futures_put().groups[0], use_profit_actions=True,
                    profit_actions=(ProfitActionDef(D("25"), 50, None),))  # fmt: skip
    problems = validate(replace(futures_put(), groups=(group,)), {1})
    assert "turn off the profit actions" in problems["futures"]


def test_a_futures_bots_default_alerts_are_refused():
    alerts = (DefaultAlertDef("pnl_pct", "above", D("25")),)
    problems = validate(replace(futures_put(), use_default_alerts=True, default_alerts=alerts), {1})
    assert "turn off the default alerts" in problems["futures"]


# --- Bots slice 19 ----------------------------------------------------------------------


def test_the_entry_chases_time_limit_is_1_to_390_minutes():
    bot = good()
    ok = replace(bot, entry_chase=replace(bot.entry_chase, window_minutes=3))
    assert validate(ok, {1}) == {}
    for bad in (0, 391):
        wrong = replace(bot, entry_chase=replace(bot.entry_chase, window_minutes=bad))
        assert "1 to 390" in validate(wrong, {1})["chase_exhaust"]


def test_leg_delta_thresholds_count_for_use_delta_and_are_checked():
    bot = good()
    legs = list(bot.legs)
    legs[0] = replace(legs[0], exit_delta_above=D("40"))
    assert validate(replace(bot, use_delta_exit=True, legs=tuple(legs)), {1}) == {}
    assert "nothing is set" in validate(replace(bot, use_delta_exit=True), {1})["watch_rules"]
    legs[0] = replace(legs[0], exit_delta_below=D("50"), exit_delta_above=D("40"))
    assert "Leg 1" in validate(replace(bot, use_delta_exit=True, legs=tuple(legs)),
                               {1})["watch_rules"]  # fmt: skip
    legs[0] = replace(legs[0], exit_delta_below=None, exit_delta_above=D("140"))
    assert "-100 and 100" in validate(replace(bot, use_delta_exit=True, legs=tuple(legs)),
                                      {1})["watch_rules"]  # fmt: skip
    # off, the numbers are kept and not read
    assert validate(replace(bot, legs=tuple(legs)), {1}) == {}


def test_a_profit_actions_dollar_stop_is_checked():
    bot = good()
    usd = replace(bot.groups[0], target_unit="usd", target_pct=None, target_usd=D("2"),
                  use_profit_actions=True)  # fmt: skip

    def check(*rows):
        return validate(replace(bot, groups=(replace(usd, profit_actions=rows), bot.groups[1])),
                        {1}).get("profit-0")  # fmt: skip

    assert check(ProfitActionDef(D("1"), 0, None, D("0.25"))) is None
    assert check(ProfitActionDef(D("1"), 0, None, D("-0.50"))) is None  # a loss is allowed
    assert "below its level" in check(ProfitActionDef(D("1"), 0, None, D("1")))
    assert "not both" in check(ProfitActionDef(D("1"), 0, D("0"), D("0.25")))
    pct = replace(bot.groups[0], target_pct=D("80"), use_profit_actions=True,
                  profit_actions=(ProfitActionDef(D("50"), 0, None, D("0.40")),))  # fmt: skip
    assert validate(replace(bot, groups=(pct, bot.groups[1])), {1}) == {}


def test_a_linked_premium_wing_needs_a_sold_leg_and_a_max_offset_away_from_the_money():
    bot = good()
    legs = list(bot.legs)  # 2 sells the put, 3 buys it

    def check(**leg3):
        legs[3] = replace(bot.legs[3], pick="premium", target=D("0.50"), link=2, **leg3)
        return validate(replace(bot, legs=tuple(legs)), {1}).get("leg-3")

    assert check(max_offset=D("-25")) is None
    assert "below its short" in check(max_offset=D("25"))
    assert "whole strike points" in check(max_offset=D("-2.5"))
    legs[3] = replace(bot.legs[3], pick="premium", target=D("0.50"), link=None,
                      max_offset=D("-25"))  # fmt: skip
    assert "sold leg of the same type" in validate(replace(bot, legs=tuple(legs)), {1})["leg-3"]
    legs[3] = replace(bot.legs[3], max_offset=D("-25"))  # an offset leg
    assert "picked by premium" in validate(replace(bot, legs=tuple(legs)), {1})["leg-3"]
    # a premium leg with a link but no max offset is a plain premium leg, as before
    legs[3] = replace(bot.legs[3], pick="premium", target=D("0.50"), link=2)
    assert validate(replace(bot, legs=tuple(legs)), {1}) == {}


def test_the_min_short_width_is_0_or_more():
    assert validate(replace(good(), min_short_width=D("20")), {1}) == {}
    assert validate(replace(good(), min_short_width=D("0")), {1}) == {}
    assert "0 or more" in validate(replace(good(), min_short_width=D("-5")), {1})["strikes"]


def test_an_offset_cant_be_measured_from_a_max_offset_wing():
    bot = good()
    legs = list(bot.legs)
    legs[3] = replace(bot.legs[3], pick="premium", target=D("0.50"), link=2, max_offset=D("-25"))
    legs[1] = replace(bot.legs[1], right="put", link=3)  # an offset from the linked wing
    problems = validate(replace(bot, legs=tuple(legs)), {1})
    assert "isn't an offset" in problems["leg-1"]


def test_a_trades_own_timed_close_is_checked_as_the_bots():
    """Bots slice 21 (timed_close_overrides.validate): a time from 09:30 to 15:59 or blank
    for off, a DTE of 0 or more, never a DTE without a time, and a calendar can't turn
    it off (question 1)."""
    from optionsjourney.bots.validation import trade_timed_problem

    assert trade_timed_problem("15:45", 0) is None
    assert trade_timed_problem(None, None) is None  # off
    assert "09:30 to 15:59" in trade_timed_problem("16:30", 0)
    assert "09:30 to 15:59" in trade_timed_problem("9:45", 0)
    assert "0 or more" in trade_timed_problem("15:45", -1)
    assert "needs a close time" in trade_timed_problem(None, 2)
    assert "calendar or diagonal" in trade_timed_problem(None, None, multi_expiry=True)


def test_a_trades_own_watch_rules_are_checked_as_the_bots():
    from optionsjourney.bots.validation import trade_watch_problem
    from optionsjourney.bots.watch import TradeWatch

    assert trade_watch_problem(TradeWatch(), (0, 1)) is None
    ok = TradeWatch(use_delta=True, position_delta=(D("-30"), D("30")),
                    leg_deltas=((1, None, D("40")),))  # fmt: skip
    assert trade_watch_problem(ok, (0, 1)) is None
    assert "nothing is set" in trade_watch_problem(TradeWatch(use_delta=True), (0, 1))
    wide = TradeWatch(use_delta=True, position_delta=(None, D("130")))
    assert "between -100 and 100" in trade_watch_problem(wide, (0, 1))
    crossed = TradeWatch(use_sl_ratio=True, sl_ratio=(D("4"), D("2")))
    assert "less than" in trade_watch_problem(crossed, (0, 1))
    stray = TradeWatch(use_delta=True, leg_deltas=((7, D("-50"), None),))
    assert "isn't a leg of this trade" in trade_watch_problem(stray, (0, 1))


# --- Bots slice 21: closing another group after a stop -------------------------------------


def paired(bot, **pairs):
    return replace(bot, groups=tuple(
        replace(g, close_on_stop=pairs.get(f"g{g.position}")) for g in bot.groups))  # fmt: skip


def split():
    """The iron condor as four groups: each short alone (it rests its stop, decision 0054)
    and each wing alone, with no stop."""
    bot = good()
    names = ("Short call", "Long call", "Short put", "Long put")
    legs = tuple(replace(leg, group=leg.position) for leg in bot.legs)
    groups = tuple(
        GroupDef(i, name, stop_pct=D("95") if i in (0, 2) else None) for i, name in enumerate(names)
    )
    return replace(bot, legs=legs, groups=groups)


def test_a_group_can_close_another_after_its_stop():
    """remainder._resolve_paired_target: another group of the same bot, no loop."""
    assert validate(paired(split(), g0=1, g2=3), {1}) == {}
    assert "itself" in validate(paired(split(), g0=0), {1})["group-0"]
    assert "doesn't exist" in validate(paired(split(), g0=7), {1})["group-0"]
    looped = validate(paired(split(), g0=2, g2=0), {1})
    assert "loop" in looped.get("group-0", "") + looped.get("group-2", "")


def test_a_spread_cannot_close_another_group_after_its_stop():
    """Decision 0054: a spread coming in rests its target, so its stop can't pair."""
    came_in = lone_shorts_rest_stops(paired(good(), g0=1))
    assert validate(came_in, {1})["group-0"] == PAIR_RESTING


def test_only_a_resting_stop_can_close_another_group():
    """Question 9: it fires only after a stop resting at the broker fills."""
    no_stop = replace(good(), groups=(replace(good().groups[0], stop_pct=None, close_on_stop=1),
                                      good().groups[1]))  # fmt: skip
    assert "no stop" in validate(no_stop, {1})["group-0"]
    watched = paired(split(), g0=1)
    watched = replace(watched, groups=(replace(watched.groups[0], broker_exit="target"),
                                       *watched.groups[1:]))  # fmt: skip
    assert validate(watched, {1}) == {"group-0": PAIR_RESTING}
    # Decision 0060: a Schwab group's stop rests at Schwab, so it can close another group
    schwab = replace(paired(split(), g0=1), broker="schwab")
    assert validate(schwab, {1}) == validate(paired(split(), g0=1), {1})


WATCHED_WORDS = ("This group has no single short to rest a stop on. Use $ loss per contract or "
                 "% of entry price lost.")  # fmt: skip


def test_a_group_with_no_single_sold_leg_keeps_todays_stop_styles():
    assert validate(shaped("long_call", stop_pct=D("50"), stop_basis="group_entry"), {1}) == {}
    for basis in ("leg_premium", "entry_premium", "closing_order"):
        found = validate(shaped("long_call", stop_pct=D("50"), stop_basis=basis), {1})
        assert found == {"group-0": WATCHED_WORDS}, basis
    straddle = shaped("short_straddle", stop_pct=D("50"), stop_basis="entry_premium")
    assert validate(straddle, {1}) == {"group-0": WATCHED_WORDS}
    usd = shaped("long_call", stop_unit="usd", stop_usd=D("1.50"), stop_basis="leg_premium")
    assert validate(usd, {1}) == {}


def test_a_group_with_one_sold_leg_takes_any_stop_style():
    for basis, amount in (("leg_premium", "95"), ("entry_premium", "200"),
                          ("closing_order", "0.20"), ("group_entry", "50")):  # fmt: skip
        for key in ("put_credit_spread", "call_debit_spread"):
            bot = shaped(key, stop_pct=D(amount), stop_basis=basis)
            assert validate(bot, {1}) == {}, (key, basis)


def test_the_closing_order_and_entry_credit_stops_save_on_any_bot():
    debit = good_debit()
    assert validate(with_side(debit, stop_basis="entry_premium", stop_pct=D("50")), {1}) == {}
    overlay = replace(debit, stop_co_mode="both", stop_co_amount=D("0.15"))
    assert validate(overlay, {1}) == {}


def test_targets_profit_and_time_actions_go_to_1000_percent_on_any_bot():
    from optionsjourney.bots.definition import ProfitActionDef as A

    for bot in (good(), good_debit()):
        assert validate(with_side(bot, target_pct=D("500")), {1}) == {}
        assert validate(with_side(bot, target_pct=D("1000")), {1}) == {}
        assert validate(with_side(bot, target_pct=D("1001")), {1}) == {
            "group-0": "The profit target is the % of the credit kept or gained on the debit, "
            "between 1 and 1000."
        }
        actions = with_side(bot, use_profit_actions=True, target_pct=None,
                            profit_actions=(A(D("200"), 33, None),))  # fmt: skip
        assert validate(actions, {1}) == {}
        timed = replace(bot, use_time_actions=True, time_actions=(row(target_pct=D("500")),))
        assert validate(timed, {1}) == {}
        too_far = replace(bot, use_time_actions=True, time_actions=(row(target_pct=D("1001")),))
        assert "time_actions" in validate(too_far, {1})


def test_dollar_sizing_saves_on_any_bot_and_needs_an_amount():
    for bot in (good(), good_debit()):
        assert validate(replace(bot, sizing="dollars", max_per_entry_usd=D("300")), {1}) == {}
        assert "above $0" in validate(replace(bot, sizing="dollars"), {1})["sizing"]
    zero = replace(good_debit(), sizing="dollars", max_per_entry_usd=D("0"))
    assert "above $0" in validate(zero, {1})["sizing"]
    assert "Unknown" in validate(replace(good(), sizing="shares"), {1})["sizing"]


def test_a_fixed_value_bot_ignores_its_hidden_contract_count():
    """Decision 0050: under a fixed value the form hides Contracts per entry, so it's
    never what a save is refused for; the limits still have to be at least 1."""
    dollars = replace(good_debit(), sizing="dollars", max_per_entry_usd=D("300"))
    assert validate(replace(dollars, contracts=0), {1}) == {}
    assert validate(replace(dollars, contracts=50, max_per_trade=1, max_per_day=1), {1}) == {}
    assert "at least 1" in validate(replace(dollars, max_per_day=0), {1})["limits"]
    assert "limits" in validate(replace(good(), contracts=50, max_per_trade=1), {1})


def test_rests_at_the_broker_is_stop_or_target():
    assert validate(with_side(good(), broker_exit="target"), {1}) == {}
    assert validate(with_side(good(), broker_exit="banana"), {1}) == {
        "group-0": "Pick whether the stop or the profit target rests at the broker."
    }


def test_two_sold_legs_cant_rest_a_stop_whatever_it_chose():
    legs = good().legs
    two_sold = replace(good(), legs=(legs[0], replace(legs[1], side=Side.SELL), *legs[2:]))
    two_sold = with_side(two_sold, stop_basis="group_entry")
    assert "watched by the app" in validate(paired(two_sold, g0=1), {1})["group-0"]


def test_an_action_can_fire_on_a_loss():
    """Decision 0055: a negative At is a loss level; loss rows go down, each deeper, and a
    new stop on one sits at a bigger loss than its level."""
    from optionsjourney.bots.definition import ProfitActionDef as A

    assert _with_actions(A(D("-50"), 50, None)) is None
    assert _with_actions(A(D("50"), 0, D("0")), A(D("-50"), 33, None)) is None
    assert _with_actions(A(D("-25"), 20, None), A(D("-50"), 20, None)) is None
    assert "deeper" in _with_actions(A(D("-50"), 20, None), A(D("-25"), 20, None))
    assert _with_actions(A(D("-50"), 0, D("75"))) is None  # out at a 75% loss
    assert "bigger loss" in _with_actions(A(D("-50"), 0, D("40")))  # 40% lost: at once
    assert _with_actions(A(D("-1000"), 33, None))
    assert _with_actions(A(D("-30"), 60, None), A(D("50"), 40, None))  # 100% in all


def test_an_entry_chase_cant_ignore_bidless_longs():
    odd = replace(good(), entry_chase=replace(good().entry_chase, ignore_bidless_longs=True))
    assert "chase_exhaust" in validate(odd, {1})
    ok = replace(good(), exit_chase=replace(good().exit_chase, ignore_bidless_longs=True))
    assert validate(ok, {1}) == {}
