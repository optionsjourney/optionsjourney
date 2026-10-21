"""Which exit rests and which the app watches, read against the fill (decision 0048;
spec: trades take either side, sections 2 and 4)."""

from decimal import Decimal

import pytest

from optionsjourney.bots import exit_plan
from optionsjourney.bots.definition import BROKER_EXITS, GroupDef
from optionsjourney.trading.orders import Side

D = Decimal
ONE = (Side.SELL, Side.BUY)  # a spread: one sold leg
TWO = (Side.SELL, Side.SELL, Side.BUY, Side.BUY)  # an iron fly kept as one group
NONE = (Side.BUY,)  # a long call


def group(rests="stop", stop="50", target="50", **more):
    return GroupDef(0, "G", stop_pct=None if stop is None else D(stop),
                    target_pct=None if target is None else D(target), broker_exit=rests,
                    **more)  # fmt: skip


def test_a_group_rests_its_stop_unless_it_says_otherwise():
    assert GroupDef(0, "G").broker_exit == "stop"
    assert BROKER_EXITS == ("stop", "target")


@pytest.mark.parametrize(
    ("sides", "credit", "rests", "want"),
    [
        # (what rests at the broker, how the stop is held, the debit fall-back)
        (ONE, True, "stop", ("stop", "broker", False)),
        (ONE, True, "target", ("target", "short", False)),
        (TWO, True, "stop", ("target", "pnl", False)),
        (TWO, True, "target", ("target", "pnl", False)),
        (NONE, True, "stop", ("target", "pnl", False)),
        (ONE, False, "stop", ("target", "pnl", True)),
        (TWO, False, "stop", ("target", "pnl", True)),
        (ONE, False, "target", ("target", "pnl", False)),
        (TWO, False, "target", ("target", "pnl", False)),
    ],
)
def test_the_table_row_by_row(sides, credit, rests, want):
    plan = exit_plan.plan_group(group(rests, stop_basis="group_entry"), sides, credit,
                                "tastytrade")  # fmt: skip
    assert (plan.rests, plan.stop_on, plan.fallback) == want
    assert plan.on_short == (credit and sides == ONE)


def test_a_group_with_no_target_rests_its_stop_if_it_can_and_one_with_no_stop_its_target():
    no_target = exit_plan.plan_group(group("target", target=None), ONE, True, "tastytrade")
    assert (no_target.rests, no_target.stop_on) == ("stop", "broker")
    no_stop = exit_plan.plan_group(group("stop", stop=None), ONE, False, "tastytrade")
    assert (no_stop.rests, no_stop.stop_on, no_stop.fallback) == ("target", None, False)
    neither = exit_plan.plan_group(group(stop=None, target=None), TWO, True, "tastytrade")
    assert (neither.rests, neither.stop_on) == (None, None)


def test_a_schwab_group_plans_exactly_as_a_tastytrade_one():
    """Decision 0060: the same table on both brokers."""
    for rests in BROKER_EXITS:
        for credit, basis in ((True, "leg_premium"), (False, "group_entry")):
            for sides in (ONE, TWO, NONE):
                g = group(rests, stop_basis=basis)
                assert exit_plan.plan_group(g, sides, credit, "schwab") == (
                    exit_plan.plan_group(g, sides, credit, "tastytrade")
                )
    credit = exit_plan.plan_group(group("stop"), ONE, True, "schwab")
    assert (credit.rests, credit.stop_on) == ("stop", "broker")


def test_a_credit_fill_drops_a_target_it_cant_keep():
    read, notes = exit_plan.read_against_fill(group(target="150"), True)
    assert read.target_pct is None
    assert notes == ("Opened for a credit; a 150% target can't be kept on a credit. "
                     "No target set.",)  # fmt: skip
    assert exit_plan.read_against_fill(group(target="99"), True) == (group(target="99"), ())
    usd = group(target=None, target_unit="usd", target_usd=D("150"))
    assert exit_plan.read_against_fill(usd, True) == (usd, ())
    assert exit_plan.read_against_fill(group(target="150"), False)[0].target_pct == D("150")


def test_a_debit_fill_reads_each_stop_style_against_the_debit():
    read, notes = exit_plan.read_against_fill(group(stop="200", stop_basis="entry_premium"), False)
    assert (read.stop_basis, read.stop_pct, notes) == ("group_entry", D("100"), ())
    read, notes = exit_plan.read_against_fill(group(stop="100", stop_basis="entry_premium"),
                                              False)  # fmt: skip
    assert read.stop_pct is None and "is no loss on a debit. No stop set." in notes[0]
    for basis, words in (("leg_premium", "a % above the short's fill"),
                         ("closing_order", "$ off the entry credit")):  # fmt: skip
        read, notes = exit_plan.read_against_fill(group(stop_basis=basis), False)
        assert read.stop_pct is None
        assert notes == (f"Opened for a debit; a stop measured as {words} only works on a "
                         "credit. No stop set.",)  # fmt: skip
    kept = group(stop_basis="group_entry")
    assert exit_plan.read_against_fill(kept, False) == (kept, ())
    usd = group(stop=None, stop_unit="usd", stop_usd=D("1.00"), stop_basis="leg_premium")
    assert exit_plan.read_against_fill(usd, False) == (usd, ())


def test_a_debit_fill_ignores_the_closing_order_and_says_so():
    _, notes = exit_plan.read_against_fill(group(stop_basis="group_entry"), False, "both")
    assert notes == ("Opened for a debit; the closing order works only on a credit trade's "
                     "stop on its short, so it's ignored.",)  # fmt: skip
    assert exit_plan.read_against_fill(group(), True, "both")[1] == ()


def test_the_side_of_a_fill():
    assert exit_plan.credit_fill(D("1.20")) and not exit_plan.credit_fill(D("-1.85"))
    assert not exit_plan.credit_fill(None, D("-0.40")) and exit_plan.credit_fill(None, None)


from optionsjourney.bots.exit_math import GroupLeg  # noqa: E402

S, L = "SPXW  261013P05690000", "SPXW  261013P05615000"
C1, C2 = "SPXW  261013C05840000", "SPXW  261013C05915000"
PUT_SPREAD = (GroupLeg(S, Side.SELL, 1), GroupLeg(L, Side.BUY, 1))
CALL_SPREAD = (GroupLeg(C1, Side.BUY, 1), GroupLeg(C2, Side.SELL, 1))


def test_a_resting_stop_is_worked_out_on_the_short():
    plan = exit_plan.plan_group(group("stop", stop="95"), ONE, True, "tastytrade")
    fills = {S: D("2.50"), L: D("0.40")}
    calc = exit_plan.stop_for(plan, PUT_SPREAD, fills, D("2.10"), "off", None)
    assert (calc.basis, calc.trigger) == ("leg_premium", D("4.80"))  # 2.50 x 1.95, the tick
    assert exit_plan.target_for(plan, PUT_SPREAD, fills) == (D("1.05"), None)


def test_a_stop_the_app_holds_on_the_short_is_the_one_it_would_have_rested():
    plan = exit_plan.plan_group(group("target", stop="95"), ONE, True, "tastytrade")
    calc = exit_plan.stop_for(plan, PUT_SPREAD, {S: D("2.50"), L: D("0.40")}, D("2.10"),
                              "off", None)  # fmt: skip
    assert plan.stop_on == "short" and calc.trigger == D("4.80")


def test_a_debit_groups_stop_is_a_pnl_level_and_its_target_a_credit_to_close_at():
    plan = exit_plan.plan_group(group("target", stop_basis="group_entry"), ONE, False,
                                "tastytrade")  # fmt: skip
    fills = {C1: D("2.50"), C2: D("0.70")}  # a debit of 1.80
    calc = exit_plan.stop_for(plan, CALL_SPREAD, fills, D("-1.80"), "off", None)
    assert (calc.basis, calc.trigger) == ("group_entry", D("-0.90"))
    assert exit_plan.target_for(plan, CALL_SPREAD, fills) == (D("-2.70"), None)


def test_no_stop_or_target_or_fill_works_out_to_nothing():
    plan = exit_plan.plan_group(group(stop=None, target=None), ONE, True, "tastytrade")
    assert exit_plan.stop_for(plan, PUT_SPREAD, {}, None, "off", None) is None
    assert exit_plan.target_for(plan, PUT_SPREAD, {}) == (None, None)


from dataclasses import replace  # noqa: E402

from optionsjourney.bots import stops  # noqa: E402
from optionsjourney.bots.definition import iron_condor_skeleton, shape  # noqa: E402


def call_spread_bot(rests="target"):
    bot = shape("call_debit_spread", iron_condor_skeleton("Fly"))
    group = replace(bot.groups[0], stop_pct=D("50"), stop_basis="group_entry",
                    target_pct=D("50"), broker_exit=rests)  # fmt: skip
    return replace(bot, groups=(group,))


CALL_LEGS = [{"symbol": C1, "side": "buy", "ratio": 1, "group": 0},
             {"symbol": C2, "side": "sell", "ratio": 1, "group": 0}]  # fmt: skip


def test_the_preview_says_the_side_and_what_rests_where():
    text = exit_plan.preview(call_spread_bot(), CALL_LEGS, {C1: D("2.50"), C2: D("0.70")},
                             D("-1.80"))  # fmt: skip
    assert text == ("Priced as a debit of 1.80. Call spread: target 2.70 rests at TastyTrade; "
                    "stop watched by the bot at P&L −0.90.")  # fmt: skip


def test_the_preview_of_a_credit_rests_the_stop_first():
    bot = shape("put_credit_spread", iron_condor_skeleton("PCS"))
    # set to rest its stop, as bots made before decision 0054 were (the runtime still can)
    bot = replace(bot, groups=(replace(bot.groups[0], stop_pct=D("95"), target_pct=D("50"),
                                       broker_exit="stop"),))  # fmt: skip
    legs = [{"symbol": S, "side": "sell", "ratio": 1, "group": 0},
            {"symbol": L, "side": "buy", "ratio": 1, "group": 0}]  # fmt: skip
    text = exit_plan.preview(bot, legs, {S: D("2.50"), L: D("0.40")}, D("2.10"))
    assert text == ("Priced as a credit of 2.10. Put spread: stop 4.80 rests at TastyTrade; "
                    "target 1.05 watched by the bot.")  # fmt: skip


def test_a_debit_fill_on_a_group_that_chose_stop_alerts_once_with_its_levels():
    plan = exit_plan.plan_group(group("stop", stop_basis="group_entry"), ONE, False,
                                "tastytrade")  # fmt: skip
    calc = stops.watched_calc(plan.group, D("-1.80"))
    groups = [("Call spread", plan, calc, D("-2.70"))]
    title, detail = exit_plan.entry_alert("Fly", D("-1.80"), groups, several=False)
    assert title == "Fly: opened for a debit, so its stop is watched by the bot."
    assert detail == (
        "It opened for a debit of 1.80, and a stop can't rest at the broker on a debit spread. "
        "The bot watches the stop (P&L −0.90) while the app is running and the computer is "
        "awake. The profit target (2.70) rests at TastyTrade."
    )


def test_a_debit_fallback_with_no_target_does_not_claim_one_rests():
    plan = exit_plan.plan_group(group("stop", target=None, stop_basis="group_entry"), ONE, False,
                                "tastytrade")  # fmt: skip
    assert (plan.fallback, plan.rests) == (True, None)
    calc = stops.watched_calc(plan.group, D("-1.80"))
    _, detail = exit_plan.entry_alert("Fly", D("-1.80"), [("Call spread", plan, calc, None)],
                                      several=False)  # fmt: skip
    assert "watches the stop (P&L −0.90)" in detail
    assert "target" not in detail


def test_notes_alone_alert_and_nothing_to_say_is_silent():
    plan = exit_plan.plan_group(group("stop", target="150"), ONE, True, "tastytrade")
    title, detail = exit_plan.entry_alert("PCS", D("2.10"), [("Put side", plan, None, None),
                                                             ("Call side", plan, None, None)],
                                          several=True)  # fmt: skip
    assert title == "PCS: opened for a credit, so some exits changed"
    assert detail.startswith("Put side: Opened for a credit; a 150% target can't be kept")
    quiet = exit_plan.plan_group(group("stop"), ONE, True, "tastytrade")
    assert exit_plan.entry_alert("PCS", D("2.10"), [("Put side", quiet, None, None)],
                                 several=False) is None  # fmt: skip


def test_a_pairing_whose_stop_ended_up_watched_is_said():
    paired = replace(group("target"), close_on_stop=1)
    plan = exit_plan.plan_group(paired, ONE, True, "tastytrade")
    said = exit_plan.with_pairing(plan, paired, {0: "Call side", 1: "Put side"})
    assert said.notes == ("Its stop is watched by the bot, so closing Put side after it "
                          "doesn't run on this trade.",)  # fmt: skip
    resting = exit_plan.plan_group(replace(paired, broker_exit="stop"), ONE, True, "tastytrade")
    assert exit_plan.with_pairing(resting, paired, {1: "Put side"}) == resting
