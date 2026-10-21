"""Slice 7: watch rules and profit actions, the pure part (Tracker2's watch_rules.py,
profit_actions.py, time_actions.close_contracts)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from optionsjourney.bots import stops, watch
from optionsjourney.bots.definition import GroupDef, ProfitActionDef, iron_condor_skeleton

D = Decimal
SPX = "SPXW  261013P05700000"


def leg(side, right, strike, bid="1.00", ask="1.10", delta=None, fresh=True, delta_fresh=True):
    return watch.LegView(
        side, right, D(strike), 1,
        None if bid is None else D(bid), None if ask is None else D(ask),
        None if delta is None else D(delta), fresh, delta_fresh,
    )  # fmt: skip


def condor(put_delta="-0.20", call_delta="0.15", **fresh):
    """Short put 5700 / long 5650, short call 5900 / long 5950, raw deltas."""
    return [
        leg("sell", "put", "5700", "1.00", "1.10", put_delta, **fresh),
        leg("buy", "put", "5650", "0.30", "0.40", "-0.05", **fresh),
        leg("sell", "call", "5900", "0.90", "1.00", call_delta, **fresh),
        leg("buy", "call", "5950", "0.20", "0.30", "0.04", **fresh),
    ]


def rules(**kw):
    return watch.Rules(**kw)


def by_rule(outcomes):
    return {o.rule: o for o in outcomes}


def test_signed_delta_reads_a_tested_short_put_positive_and_call_negative():
    assert watch.signed_delta(leg("sell", "put", "5700", delta="-0.66")) == D("0.66")
    assert watch.signed_delta(leg("sell", "call", "5900", delta="0.66")) == D("-0.66")
    assert watch.signed_delta(leg("buy", "put", "5650", delta="-0.10")) == D("-0.10")
    assert watch.signed_delta(leg("buy", "put", "5650", delta=None)) is None


def test_position_delta_fires_strictly():
    # +0.20 - 0.05 - 0.15 + 0.04 = +0.04, i.e. 4 in whole numbers
    legs = condor()
    out = by_rule(watch.evaluate(legs, rules(position_delta=(None, D("4"))), None, None))
    assert out["position_delta"].fired is False  # exactly 4 doesn't fire
    out = by_rule(watch.evaluate(legs, rules(position_delta=(None, D("3.9"))), None, None))
    assert out["position_delta"].fired and "> 3.9" in out["position_delta"].detail
    out = by_rule(watch.evaluate(legs, rules(position_delta=(D("5"), None)), None, None))
    assert out["position_delta"].fired and "< 5" in out["position_delta"].detail


def test_a_stale_or_missing_delta_never_fires():
    legs = condor(delta_fresh=False)
    out = by_rule(watch.evaluate(legs, rules(position_delta=(None, D("1"))), None, None))
    assert (out["position_delta"].fired, out["position_delta"].skipped) == (False, "stale")
    legs = condor(put_delta=None)
    out = by_rule(watch.evaluate(legs, rules(position_delta=(None, D("1"))), None, None))
    assert (out["position_delta"].fired, out["position_delta"].skipped) == (False, "stale")


def test_sl_ratio_allows_a_zero_bid_and_skips_a_crossed_quote():
    legs = condor()
    # shorts 1.05 + 0.95 = 2.00; longs 0.35 + 0.25 = 0.60; 3.333
    assert round(watch.sl_ratio(legs), 3) == D("3.333")
    out = by_rule(watch.evaluate(legs, rules(sl_ratio=(D("3.4"), None)), None, None))
    assert out["sl_ratio"].fired and "< 3.4" in out["sl_ratio"].detail
    out = by_rule(watch.evaluate(legs, rules(sl_ratio=(D("3.3"), D("3.4"))), None, None))
    assert out["sl_ratio"].fired is False
    zero_bid = [*legs[:3], leg("buy", "call", "5950", "0", "0.10")]
    assert watch.sl_ratio(zero_bid) is not None
    crossed = [*legs[:3], leg("buy", "call", "5950", "0.30", "0.20")]
    out = by_rule(watch.evaluate(crossed, rules(sl_ratio=(D("10"), None)), None, None))
    assert (out["sl_ratio"].fired, out["sl_ratio"].skipped) == (False, "bad_quote")


def test_stale_quotes_skip_the_ratio():
    out = by_rule(watch.evaluate(condor(fresh=False), rules(sl_ratio=(D("10"), None)), None, None))
    assert (out["sl_ratio"].fired, out["sl_ratio"].skipped) == (False, "stale")


def test_underlying_move_fires_inclusively_and_needs_an_entry_spot():
    r = rules(up_pct=D("1"), down_pct=D("1"))
    up = watch.SpotView(D("5858"), True)  # +1.00% from 5800
    out = by_rule(watch.evaluate(condor(), r, up, D("5800")))
    assert out["underlying_move"].fired and "+1.00%" in out["underlying_move"].detail
    flat = watch.SpotView(D("5810"), True)
    assert by_rule(watch.evaluate(condor(), r, flat, D("5800")))["underlying_move"].fired is False
    down = watch.SpotView(D("5742"), True)
    assert by_rule(watch.evaluate(condor(), r, down, D("5800")))["underlying_move"].fired
    out = by_rule(watch.evaluate(condor(), r, up, None))
    assert (out["underlying_move"].fired, out["underlying_move"].skipped) == (False, "no_entry")
    stale = watch.SpotView(D("5900"), False)
    out = by_rule(watch.evaluate(condor(), r, stale, D("5800")))
    assert (out["underlying_move"].fired, out["underlying_move"].skipped) == (False, "stale")


def test_short_tested_reads_the_nearest_short_inclusively():
    r = rules(put_pts=D("10"), call_pts=D("-5"))
    out = by_rule(watch.evaluate(condor(), r, watch.SpotView(D("5710"), True), None))
    assert out["short_put_tested"].fired  # 5710 - 5700 = 10, at the level
    assert "5700 is +10 pts" in out["short_put_tested"].detail
    assert out["short_call_tested"].fired is False  # 5900 - 5710 = 190
    out = by_rule(watch.evaluate(condor(), r, watch.SpotView(D("5905"), True), None))
    assert out["short_call_tested"].fired  # 5900 - 5905 = -5, past the strike
    # A side that's closed has no short of that right: nothing to watch
    only_call = condor()[2:]
    out = by_rule(watch.evaluate(only_call, r, watch.SpotView(D("5700"), True), None))
    assert "short_put_tested" not in out
    out = by_rule(watch.evaluate(condor(), r, None, None))
    assert out["short_put_tested"].skipped == "stale"


def test_rules_of_a_bot():
    bot = iron_condor_skeleton("IC")
    assert watch.rules_of(bot) is None
    on = replace(bot, use_sl_ratio_exit=True, sl_ratio_below=D("0.78"))
    assert watch.rules_of(on) == watch.Rules(sl_ratio=(D("0.78"), None))
    # Unticked keeps the numbers but the rule is off
    assert watch.rules_of(replace(on, use_sl_ratio_exit=False)) is None
    both = replace(bot, use_underlying_exit=True, short_put_tested_pts=D("5"),
                   use_delta_exit=True, position_delta_above=D("30"))  # fmt: skip
    assert watch.rules_of(both) == watch.Rules(position_delta=(None, D("30")), put_pts=D("5"))


def test_a_spreads_pnl_and_the_profit_level_in_pct_and_usd():
    short = leg("sell", "put", "5700", "0.40", "0.50")
    long = leg("buy", "put", "5650", "0.05", "0.15")
    # credit 1.20, closing costs 0.45 - 0.10 = 0.35: P&L 0.85
    assert watch.group_pnl_now(D("1.20"), [(short, 1), (long, 1)]) == (D("0.85"), None)
    pct = GroupDef(0, "Put side", target_pct=D("80"))
    action = ProfitActionDef(D("50"), 0, D("0"))
    out = watch.profit_outcome(pct, action, 1, D("1.20"), [(short, 1), (long, 1)])
    assert out.fired and "P/L 0.85 ≥ 0.60" in out.detail  # 50% of 1.20
    usd = GroupDef(0, "Put side", target_unit="usd", target_usd=D("1.00"))
    assert watch.profit_outcome(usd, ProfitActionDef(D("0.90"), 0, D("0")), 1, D("1.20"),
                                [(short, 1), (long, 1)]).fired is False  # fmt: skip
    stale = leg("sell", "put", "5700", "0.40", "0.50", fresh=False)
    out = watch.profit_outcome(pct, action, 1, D("1.20"), [(stale, 1), (long, 1)])
    assert (out.fired, out.skipped) == (False, "stale")


def test_contracts_for_rounds_down_with_at_least_one():
    assert watch.contracts_for(3, 33) == 1
    assert watch.contracts_for(3, 50) == 1
    assert watch.contracts_for(10, 25) == 2
    assert watch.contracts_for(1, 10) == 1
    assert watch.contracts_for(3, 0) == 0


def test_close_contracts_leaves_one_open():
    assert watch.close_contracts(50, 4, 4) == 2
    assert watch.close_contracts(99, 4, 4) == 3
    assert watch.close_contracts(50, 1, 1) == 0
    assert watch.close_contracts(50, 4, 2) == 1
    assert watch.close_contracts(None, 4, 4) == 0


def test_level_calc_is_the_credit_times_one_plus_the_loss():
    calc = stops.level_calc(SPX, D("1.50"), D("1.20"), D("0"))
    assert (calc.trigger, calc.problem) == (D("1.20"), None)
    assert stops.level_calc(SPX, D("1.50"), D("1.20"), D("-25")).trigger == D("0.90")
    assert stops.level_calc(SPX, D("1.50"), D("1.20"), D("50")).trigger == D("1.80")
    assert stops.level_calc(SPX, D("1.50"), D("1.20"), D("-100")).problem
    words = stops.describe(calc.as_log()["stop_calc"])
    assert words == ["credit 1.20 × (1 + 0%) = 1.20", "→ 1.20"]


# --- Final review fixes -------------------------------------------------------------


def test_the_opening_pause_is_the_first_minute_of_the_session():
    from datetime import datetime

    assert watch.in_opening_pause(datetime(2026, 10, 13, 9, 30, 10))
    assert watch.in_opening_pause(datetime(2026, 10, 13, 9, 30, 59))
    assert not watch.in_opening_pause(datetime(2026, 10, 13, 9, 31, 0))
    assert not watch.in_opening_pause(datetime(2026, 10, 13, 9, 29, 59))


def test_a_profit_action_is_known_by_what_it_says():
    assert ProfitActionDef(D("50"), 33, D("0")).sig == ProfitActionDef(D("50.0"), 33, D("0")).sig
    assert ProfitActionDef(D("50"), 33, D("0")).sig != ProfitActionDef(D("50"), 33, None).sig
    assert ProfitActionDef(D("50"), 33, None).sig == "50|33|"


def test_a_short_with_no_strike_is_stale_not_zero():
    no_strike = watch.LegView("sell", "put", None, 1, D("1"), D("1.1"), None, True, True)
    out = by_rule(watch.evaluate([no_strike], rules(put_pts=D("10")), watch.SpotView(D("5"), True),
                                 None))  # fmt: skip
    assert (out["short_put_tested"].fired, out["short_put_tested"].skipped) == (False, "stale")


# --- Slice 13: profit actions over any group ------------------------------------------


def test_group_pnl_now_over_a_debit_group():
    # A long call bought for 3.00 (entry -3.00), its mid now 4.10: closing receives 4.10.
    long_call = leg("buy", "call", "5800", "4.05", "4.15")
    assert watch.group_pnl_now(D("-3.00"), [(long_call, 1)]) == (D("1.10"), None)
    # A 1x2x1 fly bought for 1.00: wings 3.00 and 0.40, the body (sold 2) 1.20 each.
    fly = [(leg("buy", "call", "5750", "2.95", "3.05"), 1),
           (leg("sell", "call", "5800", "1.15", "1.25"), 2),
           (leg("buy", "call", "5850", "0.35", "0.45"), 1)]  # fmt: skip
    # value = 2 x 1.20 - 3.00 - 0.40 = -1.00: P&L -1.00 + 1.00 = 0.00
    assert watch.group_pnl_now(D("-1.00"), fly) == (D("0"), None)
    # A worthless long at 0/0 counts 0; a sold leg at 0/0 is unusable.
    assert watch.group_pnl_now(D("-3.00"), [(leg("buy", "call", "5800", "0", "0"), 1)]) == (
        D("-3.00"), None,
    )  # fmt: skip
    assert watch.group_pnl_now(D("1.00"), [(leg("sell", "put", "5700", "0", "0"), 1)]) == (
        None, "bad_quote",
    )  # fmt: skip
    stale = leg("buy", "call", "5800", "4.05", "4.15", fresh=False)
    assert watch.group_pnl_now(D("-3.00"), [(stale, 1)]) == (None, "stale")


def test_profit_levels_scale_by_the_entrys_size():
    pct = GroupDef(0, "Calls", target_pct=D("80"))
    assert watch.profit_level(pct, D("-3.00"), D("50")) == D("1.50")
    assert watch.profit_level(pct, D("1.20"), D("50")) == D("0.60")
    usd = GroupDef(0, "Calls", target_unit="usd", target_usd=D("2.00"))
    assert watch.profit_level(usd, D("-3.00"), D("0.90")) == D("0.90")
    # The outcome reads the group's legs: 1.10 gained on a 3.00 debit is short of 1.50.
    long_call = leg("buy", "call", "5800", "4.05", "4.15")
    out = watch.profit_outcome(pct, ProfitActionDef(D("50"), 0, D("0")), 1, D("-3.00"),
                               [(long_call, 1)])  # fmt: skip
    assert (out.fired, out.rule) == (False, "profit:0:1") and "P/L 1.10" in out.detail
    out = watch.profit_outcome(pct, ProfitActionDef(D("30"), 0, D("0")), 1, D("-3.00"),
                               [(long_call, 1)])  # fmt: skip
    assert out.fired and "P/L 1.10 ≥ 0.90" in out.detail


def test_the_messages_name_the_underlying():
    """Slice 16: SpotView carries the traded underlying's symbol; SPX by default."""
    r = rules(up_pct=D("1"), put_pts=D("10"))
    out = by_rule(watch.evaluate(condor(), r, watch.SpotView(D("5710"), True), D("5800")))
    assert "from SPX 5710.00" in out["short_put_tested"].detail
    assert out["underlying_move"].detail.startswith("SPX 5710.00 is")
    xsp = watch.SpotView(D("5710"), True, "XSP")
    out = by_rule(watch.evaluate(condor(), r, xsp, D("5800")))
    assert "from XSP 5710.00" in out["short_put_tested"].detail
    assert out["underlying_move"].detail.startswith("XSP 5710.00 is")
    stale = by_rule(watch.evaluate(condor(), r, watch.SpotView(None, False, "QQQ"), None))
    assert stale["short_put_tested"].detail == "short put: no fresh QQQ price"
    assert stale["underlying_move"].detail == "QQQ move: the entry price is unknown"


# --- Bots slice 19: leg delta exits --------------------------------------------------


def placed_condor(**changes):
    """condor() with each leg at its bot position (0 short put … 3 long call)."""
    return [replace(view, position=n) for n, view in enumerate(condor(**changes))]


def test_a_legs_delta_past_its_threshold_fires_strictly():
    # the short put's raw -0.45 reads +45 by position
    legs = placed_condor(put_delta="-0.45")
    out = by_rule(watch.evaluate(legs, rules(leg_delta=((0, None, D("40")),)), None, None))
    hit = out["leg_delta:0"]
    assert hit.fired and hit.detail == "leg 1 delta +45.0 > 40"
    assert hit.name == "Leg 1 delta exit"
    at = by_rule(watch.evaluate(placed_condor(put_delta="-0.40"),
                                rules(leg_delta=((0, None, D("40")),)), None, None))  # fmt: skip
    assert not at["leg_delta:0"].fired  # strict: at the threshold is not past it
    under = rules(leg_delta=((2, D("-25"), None),))
    below = by_rule(watch.evaluate(placed_condor(call_delta="0.30"), under, None, None))
    assert below["leg_delta:2"].fired  # the short call's -30 is below -25


def test_a_stale_leg_delta_is_skipped_and_a_leg_not_held_is_left_out():
    legs = placed_condor(put_delta="-0.45", delta_fresh=False)
    out = by_rule(watch.evaluate(legs, rules(leg_delta=((0, None, D("40")),)), None, None))
    assert out["leg_delta:0"].skipped == "stale" and not out["leg_delta:0"].fired
    unplaced = condor(put_delta="-0.45")  # no position: not this bot leg (a mismatch)
    assert watch.evaluate(unplaced, rules(leg_delta=((0, None, D("40")),)), None, None) == []


def test_rules_of_reads_leg_thresholds_only_while_use_delta_is_ticked():
    bot = iron_condor_skeleton("IC")
    legs = (replace(bot.legs[0], exit_delta_above=D("40")), *bot.legs[1:])
    assert watch.rules_of(replace(bot, legs=legs)) is None
    found = watch.rules_of(replace(bot, legs=legs, use_delta_exit=True))
    assert found.leg_delta == ((0, None, D("40")),) and found.position_delta is None


# --- Bots slice 21: a trade's own watch rules ----------------------------------------------


def test_a_trades_rules_replace_the_bots_delta_and_ratio_and_keep_its_underlying_rules():
    """watch_loop.config_for with an override row: it REPLACES the strategy's delta and
    ratio rules; the underlying move stays the strategy's (underlying_watch)."""
    from dataclasses import replace as swap

    from optionsjourney.bots.definition import iron_condor_skeleton

    bot = swap(iron_condor_skeleton("IC"), use_delta_exit=True, position_delta_above=D("30"),
               use_underlying_exit=True, underlying_up_pct=D("1"))  # fmt: skip
    mine = watch.TradeWatch(use_sl_ratio=True, sl_ratio=(None, D("4")))
    rules = watch.rules_for(bot, mine)
    assert rules.position_delta is None and rules.sl_ratio == (None, D("4"))
    assert rules.up_pct == D("1")
    legs = watch.TradeWatch(use_delta=True, leg_deltas=((2, None, D("40")),))
    assert watch.rules_for(bot, legs).leg_delta == ((2, None, D("40")),)
    assert watch.rules_for(bot, None) == watch.rules_of(bot)
    nothing = swap(bot, use_underlying_exit=False)
    assert watch.rules_for(nothing, watch.TradeWatch()) is None


def test_a_loss_level_fires_once_the_group_has_lost_that_much():
    """Decision 0055: a negative At fires at or below its (negative) level."""
    pct = GroupDef(0, "Puts", target_pct=D("80"))
    assert watch.profit_level(pct, D("2.00"), D("-50")) == D("-1.00")
    short_put = leg("sell", "put", "5700", "3.05", "3.15")  # costs 3.10: 1.10 lost on 2.00
    out = watch.profit_outcome(pct, ProfitActionDef(D("-50"), 50, None), 2, D("2.00"),
                               [(short_put, 1)])  # fmt: skip
    assert out.fired and "P/L -1.10 ≤ -1.00" in out.detail
    out = watch.profit_outcome(pct, ProfitActionDef(D("-60"), 50, None), 2, D("2.00"),
                               [(short_put, 1)])  # fmt: skip
    assert not out.fired


# --- #201: the delta and S/L ratio breaches must hold 30 s (Tracker2 16f11f50) ---------


def test_delta_and_ratio_rules_must_hold_but_the_others_need_passes_only():
    assert watch.must_hold("position_delta") and watch.must_hold("sl_ratio")
    assert watch.must_hold("leg_delta:0") and watch.must_hold("leg_delta:3")
    for rule in ("underlying_move", "short_put_tested", "short_call_tested", "profit:0:1"):
        assert not watch.must_hold(rule)


def test_a_held_rule_confirms_once_two_passes_have_run_30_seconds():
    since = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
    assert timedelta(seconds=30) == watch.HOLD
    assert not watch.confirmed("sl_ratio", 2, since, since + timedelta(seconds=2))
    assert not watch.confirmed("sl_ratio", 15, since, since + timedelta(seconds=29))
    assert watch.confirmed("sl_ratio", 16, since, since + timedelta(seconds=30))
    assert not watch.confirmed("sl_ratio", 1, since, since + timedelta(seconds=60))
    # $-like rules stay fast: two passes, any time apart
    assert watch.confirmed("short_put_tested", 2, since, since + timedelta(seconds=2))
    assert watch.confirmed("profit:0:1", 2, since, since + timedelta(seconds=2))
    assert not watch.confirmed("profit:0:1", 1, since, since)
