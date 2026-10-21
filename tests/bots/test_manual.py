from decimal import Decimal

from optionsjourney.bots import manual
from optionsjourney.bots.definition import DefaultAlertDef


def test_reads_pct_and_dollar_rules():
    rules, problems = manual.read_form({"name": "Half", "target_value": "50", "target_unit": "pct",
                                         "stop_value": "300", "stop_unit": "usd"})  # fmt: skip
    assert problems == {}
    assert (rules.target_value, rules.target_unit) == (Decimal("50"), "pct")
    assert (rules.stop_value, rules.stop_unit) == (Decimal("300"), "usd")
    assert rules.close_time is None


def test_exit_time_needs_valid_clock_and_days():
    _, problems = manual.read_form({"name": "X", "close_time": "25:00", "close_dte": "-1"})
    assert set(problems) == {"close_time", "close_dte"}


def test_exit_time_typed_short_is_stored_as_clock():
    rules, problems = manual.read_form({"name": "X", "close_time": "1545", "close_dte": "1"})
    assert problems == {} and (rules.close_time, rules.close_dte) == ("15:45", 1)


def test_name_required_and_values_positive():
    _, problems = manual.read_form({"name": "", "target_value": "0", "target_unit": "pct"})
    assert set(problems) == {"name", "target_value"}


def test_unknown_unit_and_unreadable_number_are_problems():
    _, problems = manual.read_form({"name": "X", "target_value": "abc", "stop_value": "5",
                                    "stop_unit": "lots"})  # fmt: skip
    assert set(problems) == {"target_value", "stop_unit"}


def test_rules_with_nothing_is_allowed_but_has_no_exits():
    rules, problems = manual.read_form({"name": "Alerts only"})
    assert problems == {} and manual.has_exits(rules) is False


def test_alerts_read_like_a_bots_default_alerts():
    rules, problems = manual.read_form({
        "name": "A", "alerts": "2", "da-0-type": "pnl_pct", "da-0-direction": "below",
        "da-0-value": "-50", "da-1-type": "strike_offset", "da-1-points": "10",
    })  # fmt: skip
    assert problems == {}
    assert rules.alerts == (DefaultAlertDef("pnl_pct", "below", Decimal("-50")),
                            DefaultAlertDef("strike_offset", points=Decimal("10")))  # fmt: skip
    assert manual.has_exits(rules) is False
