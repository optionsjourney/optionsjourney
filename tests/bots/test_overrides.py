"""Bots slice 21: what one trade's group reads, its own copy laid over the bot's group."""

from decimal import Decimal

from optionsjourney.bots import overrides
from optionsjourney.bots.definition import GroupDef, ProfitActionDef, iron_condor_skeleton
from optionsjourney.bots.overrides import TradeGroup

D = Decimal
GROUP = GroupDef(
    0, "Put side", stop_pct=D("95"), target_pct=D("50"), on_stop_remainder="close_market",
    use_profit_actions=True, profit_actions=(ProfitActionDef(D("20"), 25),),
)  # fmt: skip


def test_no_copy_is_the_bots_group():
    assert overrides.effective_group(GROUP, None) is GROUP
    assert overrides.effective_group(GROUP, TradeGroup()) == GROUP


def test_each_setting_is_laid_over_only_when_set():
    mine = TradeGroup(on_stop_remainder="leave")
    got = overrides.effective_group(GROUP, mine)
    assert got.on_stop_remainder == "leave"
    assert got.remainder_limit is None
    assert got.profit_actions == GROUP.profit_actions and got.stop_pct == D("95")


def test_a_limit_and_the_profit_actions_replace_the_groups():
    actions = (ProfitActionDef(D("30"), 0, D("0")),)
    got = overrides.effective_group(GROUP, TradeGroup(
        on_stop_remainder="close_limit", remainder_limit=D("0.10"),
        use_profit_actions=False, profit_actions=actions,
    ))  # fmt: skip
    assert (got.on_stop_remainder, got.remainder_limit) == ("close_limit", D("0.10"))
    assert got.use_profit_actions is False and got.profit_actions == actions


def test_a_dollar_stop_switches_the_unit_and_a_pct_keeps_the_style():
    dollars = overrides.effective_group(GROUP, TradeGroup(stop_unit="usd", stop_usd=D("1.50")))
    assert (dollars.stop_unit, dollars.stop_value) == ("usd", D("1.50"))
    pct = overrides.effective_group(GROUP, TradeGroup(stop_unit="pct", stop_pct=D("120")))
    assert (pct.stop_unit, pct.stop_pct, pct.stop_basis) == ("pct", D("120"), "leg_premium")


def test_a_trades_timed_close_replaces_the_bots_wholesale():
    from dataclasses import replace

    bot = replace(iron_condor_skeleton("IC"), close_time="15:45", close_dte=1)
    assert overrides.effective_timed(bot, None) == ("15:45", 1, "bot")
    mine = overrides.TradeTimed("11:00", None)
    assert overrides.effective_timed(bot, mine) == ("11:00", 0, "trade")
    assert overrides.effective_timed(bot, overrides.TradeTimed(None, None)) == (None, 0, "trade")


def test_a_trades_target_pct_never_switches_a_dollar_groups_unit():
    """Review I1: a $ group's profit actions are in $; a slice 6 target % on the trade
    must not turn them into % (a $1.50 action would read as 1.50%)."""
    actions = (ProfitActionDef(D("1.50"), 0, D("0")),)
    usd = GroupDef(0, "Put side", stop_pct=D("95"), target_unit="usd", target_usd=D("2.00"),
                   use_profit_actions=True, profit_actions=actions)  # fmt: skip
    got = overrides.effective_group(usd, TradeGroup(target_pct=D("25")))
    assert (got.target_unit, got.target_usd) == ("usd", D("2.00"))
    pct = overrides.effective_group(GROUP, TradeGroup(target_pct=D("25")))
    assert (pct.target_unit, pct.target_pct) == ("pct", D("25"))
