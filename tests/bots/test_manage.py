"""Slice 6: the chips for a trade's changes and the checks on what you type."""

from decimal import Decimal

from optionsjourney.bots import manage
from optionsjourney.bots.definition import GroupDef
from optionsjourney.bots.manage import Override

D = Decimal
SIDE = GroupDef(0, "Call side", stop_pct=D("150"), target_pct=D("50"))


def labels(chips):
    return [c.label for c in chips]


def test_no_chips_when_nothing_differs():
    assert manage.chips(SIDE, None) == []
    assert manage.chips(SIDE, Override()) == []
    # Your numbers, the same as the bot's: no chip (typing them again is "back to the bot").
    assert manage.chips(SIDE, Override(stop_pct=D("150.0"), target_pct=D("50"))) == []


def test_a_changed_stop_shows_the_bots_stop_in_its_own_style():
    [chip] = manage.chips(SIDE, Override(stop_pct=D("200")))
    assert (chip.kind, chip.label) == ("stop", "Stop 200%")
    assert chip.title == "Stop changed for this trade (bot: 150%)"
    entry = GroupDef(0, "", stop_pct=D("150"), stop_basis="entry_premium")
    [chip] = manage.chips(entry, Override(stop_pct=D("2")))
    assert "bot: 150% of the entry credit" in chip.title
    usd = GroupDef(0, "", stop_unit="usd", stop_usd=D("2"))
    # A $ stop moved by % always differs, even at the same number.
    [chip] = manage.chips(usd, Override(stop_pct=D("2")))
    assert chip.title.endswith("(bot: $2.00 a contract)")
    co = GroupDef(0, "", stop_pct=D("0.8"), stop_basis="closing_order")
    assert "closing order $0.80" in manage.chips(co, Override(stop_pct=D("0.8")))[0].title
    # The bot's closing-order overlay: your % has none, so it differs.
    [chip] = manage.chips(SIDE, Override(stop_pct=D("150")), co_mode="both", co_amount=D("1.2"))
    assert "with the closing order $1.20 (both)" in chip.title
    assert manage.stop_text(None) == "none"


def test_a_changed_target_chip():
    [chip] = manage.chips(SIDE, Override(target_pct=D("25")))
    assert (chip.kind, chip.label, chip.title) == (
        "target",
        "Target 25%",
        "Target changed for this trade (bot: 50%)",
    )
    none = GroupDef(0, "", stop_pct=D("150"))
    assert manage.chips(none, Override(target_pct=D("25")))[0].title.endswith("(bot: none)")
    usd = GroupDef(0, "", target_unit="usd", target_usd=D("1"))
    assert manage.chips(usd, Override(target_pct=D("1")))[0].title.endswith("$1.00 a contract)")


def test_exits_off_chip_is_a_warning():
    [chip] = manage.chips(SIDE, Override(stop_pct=D("200"), exits_off=True))
    assert chip.kind == "off" and chip.warning and chip.label == "Exits off: not protected"
    assert "no stop and no target" in chip.title


def test_a_part_closed_side_shows_how_much():
    assert labels(manage.chips(SIDE, None, closed=1, size=3)) == ["Closed 1 of 3"]
    assert labels(manage.chips(SIDE, Override(target_pct=D("25")), closed=2, size=3)) == [
        "Target 25%",
        "Closed 2 of 3",
    ]


def test_a_quantity_is_a_whole_number_up_to_what_is_held():
    assert manage.quantity_problem("2", 3) == (2, None)
    assert manage.quantity_problem(" 3 ", 3) == (3, None)
    assert manage.quantity_problem("4", 3) == (None, "Only 3 of this group are held.")
    assert manage.quantity_problem("1", 0) == (None, "Nothing of this group is held.")
    assert manage.quantity_problem("0", 3)[1] == "The quantity must be 1 or more."
    assert manage.quantity_problem("1.5", 3)[1] == "The quantity must be a whole number."


def test_a_percent_is_read_within_its_range():
    assert manage.read_pct("200", manage.STOP_RANGE, "stop") == (D("200"), None)
    assert manage.read_pct("25%", manage.TARGET_RANGE, "target") == (D("25"), None)
    assert manage.read_pct("100", manage.TARGET_RANGE, "target")[1] == (
        "The target must be from 1% to 99%."
    )
    assert manage.read_pct("abc", manage.STOP_RANGE, "stop")[1] == "The stop must be a number."
    assert manage.read_pct("NaN", manage.STOP_RANGE, "stop")[0] is None


def test_a_targets_range_follows_the_trades_fill():
    """Decision 0048: 1-1000% on a debit fill, 1-99% on a credit (or none yet)."""
    assert manage.target_range(D("-1.20")) == (D(1), D(1000))
    assert manage.target_range(D("1.20")) == manage.TARGET_RANGE == (D(1), D(99))
    assert manage.target_range(None) == manage.TARGET_RANGE


def test_a_watched_groups_stop_in_words():
    entry = GroupDef(0, "", stop_pct=D("50"), stop_basis="group_entry")
    assert manage.stop_text(entry) == "50% of the entry price"
    usd = GroupDef(0, "", stop_unit="usd", stop_usd=D("1.5"))
    assert manage.stop_text(usd, watched=True) == "$1.50 a contract (watched by the app)"
    assert manage.stop_text(usd) == "$1.50 a contract"
    # Your % on a watched group is "% of the entry price lost": the same number is no chip.
    assert manage.chips(entry, Override(stop_pct=D("50")), watched=True) == []
    [chip] = manage.chips(usd, Override(stop_pct=D("50")), watched=True)
    assert chip.title.endswith("(bot: $1.50 a contract (watched by the app))")


def test_each_switch_has_its_own_chip():
    """Bots slice 18: the stop and the target switch off separately (Tracker2's "SL off"
    and "PT off"); both off is slice 6's "Exits off"."""
    [chip] = manage.chips(SIDE, Override(stop_pct=D("200"), stop_off=True))
    assert (chip.kind, chip.label, chip.warning) == ("off", "Stop off: no stop", True)
    chips = manage.chips(SIDE, Override(stop_pct=D("200"), target_off=True))
    assert labels(chips) == ["Target off", "Stop 200%"] and not chips[0].warning
    both = manage.chips(SIDE, Override(stop_off=True, target_off=True))
    assert labels(both) == ["Exits off: not protected"]


# --- Bots slice 21: a group's own settings on this trade --------------------------------


def test_a_wing_rule_of_the_trades_has_a_chip_unless_it_is_the_groups():
    assert manage.chips(SIDE, Override(on_stop_remainder="close_market")) == []
    [chip] = manage.chips(SIDE, Override(on_stop_remainder="leave"))
    assert chip.label == "After the stop: leave open"
    assert "(bot: sell now)" in chip.title
    [limit] = manage.chips(SIDE, Override(on_stop_remainder="close_limit",
                                          remainder_limit=D("0.15")))  # fmt: skip
    assert limit.label == "After the stop: sell at a limit at 0.15"


def test_a_dollar_stop_and_a_closing_order_have_chips():
    [usd] = manage.chips(SIDE, Override(stop_unit="usd", stop_usd=D("1.5")))
    assert usd.label == "Stop $1.50 a contract" and "(bot: 150%)" in usd.title
    [co] = manage.chips(SIDE, Override(stop_pct=D("150"), co_mode="tighter",
                                       co_amount=D("0.8")))  # fmt: skip
    assert co.label == "Closing order $0.80 (tighter)"
    [off] = manage.chips(SIDE, Override(co_mode="off"), co_mode="both", co_amount=D("1.2"))
    assert off.label == "Closing order off"
    assert manage.chips(SIDE, Override(co_mode="both", co_amount=D("1.2")), co_mode="both",
                        co_amount=D("1.2")) == []  # fmt: skip


def test_the_trades_own_profit_actions_have_a_chip():
    [chip] = manage.chips(SIDE, Override(own_profit_actions=True))
    assert chip.label == "Profit actions: this trade's"
