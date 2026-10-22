import copy
import json
from decimal import Decimal

import pytest

from optionsjourney.bots import bot_file, tracker2_file
from optionsjourney.bots.bot_file import FileError
from optionsjourney.bots.definition import ChaseDef, MoveLimits
from optionsjourney.bots.validation import validate
from optionsjourney.trading.orders import Side

D = Decimal


def _chase(**changes):
    row = {"entry_window_minutes": None, "exhaust_mode": "natural_then_leg_out",
           "ignore_bidless_longs": False, "interval_seconds": 2, "max_attempts": 20,
           "natural_minutes": 2, "price_increment": 0.05, "starting_offset": 0.0,
           "switch_to_market_after_max_attempts": True}  # fmt: skip
    return row | changes


def _group(name, sort, stop=None, close=None, remainder="close_market"):
    return {"broker_exit": "stop", "name": name, "on_stop_close_group_index": close,
            "on_stop_remainder": remainder, "on_stop_remainder_limit": None,
            "profit_actions": [], "profit_target_pct": None, "profit_target_unit": "pct",
            "profit_target_usd": None, "sort_order": sort, "stop_loss_basis": "leg_premium",
            "stop_loss_pct": stop, "stop_loss_unit": "pct", "stop_loss_usd": None,
            "use_profit_actions": False}  # fmt: skip


def _leg(sort, action, right, group, target, link=None, max_offset=None):
    return {"action": action, "dte": 0, "exact_strike_offset": True, "exit_delta_above": None,
            "exit_delta_below": None, "group_index": group, "linked_leg_index": link,
            "max_strike_offset": max_offset, "option_type": right, "quantity": 1,
            "selection_mode": "fixed_premium", "sort_order": sort,
            "target_value": target}  # fmt: skip


def six_entry_ic() -> dict:
    """A Tracker2 share shaped like a real one: an iron condor entered six times a day,
    each short its own group whose stop also closes its wing's group."""
    times = ("09:47", "12:23", "13:03", "13:43", "14:23", "15:03")
    bot = {
        "allocation_type": "quantity", "blackout_date_sets": ["FOMC"], "blackout_days": [],
        "broker_exit": None, "default_alerts": [
            {"direction": "above", "type": "pnl_pct", "value": 75.0}],
        "default_alerts_in_app_only": False, "description": None, "ema_minutes_a": None,
        "ema_minutes_b": None, "ema_mode": None, "entry_days": [1, 2, 3, 4, 5],
        "entry_source": "scheduled",
        "entry_times": [{"entry_time": t, "is_enabled": True, "label": None,
                         "quantity_override": None} for t in times],
        "execution_configs": {
            "entry": _chase(entry_window_minutes=3, interval_seconds=3, max_attempts=10,
                            switch_to_market_after_max_attempts=False),
            "exit": _chase()},
        "exit_mode": "hybrid", "exit_time_in_force": "GTC", "floating_end": None,
        "floating_start": None, "frequency": "daily", "intraday_max_down_pct": None,
        "intraday_max_up_pct": None, "intraday_min_down_pct": None,
        "intraday_min_up_pct": None,
        "leg_groups": [_group("Short Puts", 0, 90.0, close=2),
                       _group("Short Calls", 1, 90.0, close=3),
                       _group("Long Puts", 2, remainder="close_chase"),
                       _group("Long Calls", 3, remainder="close_chase")],
        "legs": [_leg(0, "sell", "put", 0, 2.9), _leg(1, "buy", "put", 2, 0.05, 0, -75.0),
                 _leg(2, "sell", "call", 1, 2.7), _leg(3, "buy", "call", 3, 0.05, 2, 75.0)],
        "max_allocation_per_trade": None, "max_contracts_per_trade": None,
        "max_entries_per_day": None, "max_open_positions": None, "min_short_width": 0.0,
        "monthly_week": 1, "monthly_weekday": 1, "move_conflicted_strikes": True,
        "name": "Six-entry IC", "poll_interval_seconds": 10, "position_delta_above": None,
        "position_delta_below": None, "premium_max": None, "premium_min": None,
        "premium_side": "credit", "profit_actions": [], "profit_target_pct": None,
        "profit_target_unit": "pct", "profit_target_usd": None, "quantity": 1,
        "scheduled_close_dte": None, "scheduled_close_time": None,
        "short_call_tested_pts": None, "short_put_tested_pts": None, "sl_ratio_above": None,
        "sl_ratio_below": None, "sma_days_a": None, "sma_days_b": None, "sma_mode": None,
        "specific_date_sets": [], "specific_dates": [], "stop_loss_co_amount": 0.2,
        "stop_loss_co_mode": "tighter", "stop_loss_pct": None, "stop_loss_unit": "pct",
        "stop_loss_usd": None, "time_actions": [], "underlying": "SPX",
        "underlying_down_pct": None, "underlying_up_pct": None, "use_blackout_days": True,
        "use_default_alerts": True, "use_delta_exit": False, "use_exact_dte": True,
        "use_floating_entry": False, "use_intraday_move": False, "use_leg_groups": True,
        "use_premium_bounds": False, "use_profit_actions": False,
        "use_short_call_tested": False, "use_short_put_tested": False,
        "use_sl_ratio_exit": False, "use_technicals": False, "use_time_actions": False,
        "use_underlying_exit": False, "use_vix9d_ratio": False, "use_vix_level": False,
        "use_vix_overnight": False, "vix9d_ratio_max": None, "vix9d_ratio_min": None,
        "vix_max": None, "vix_min": None, "vix_on_max_down_pct": None,
        "vix_on_max_up_pct": None, "vix_on_min_down_pct": None, "vix_on_min_up_pct": None,
    }  # fmt: skip
    return {"bot": bot, "format": "tracker2.bot", "schema_version": 1,
            "shared_at": "2026-10-16T01:27:17Z", "updated_at": "2026-10-16T01:27:17Z"}  # fmt: skip


def read(data: dict) -> bot_file.Imported:
    return bot_file.parse(json.dumps(data))


def test_before_expiration_lists_carry_over():
    """#207 (Tracker2 ef862660): blackout_dte_sets comes over as it is, and a market
    holiday saved as an entry-day list (Tracker2's earlier form) is read, as Tracker2
    reads it, as a Before expiration one."""
    data = six_entry_ic()
    data["bot"] |= {"blackout_date_sets": ["FOMC", "HOLIDAY"], "blackout_dte_sets": ["CPI"]}
    bot = read(data).bot
    assert (bot.blackout_date_sets, bot.blackout_dte_sets) == (("FOMC",), ("CPI", "HOLIDAY"))
    del data["bot"]["blackout_dte_sets"]  # a share from before ef862660
    data["bot"]["blackout_date_sets"] = ["FOMC"]
    assert read(data).bot.blackout_dte_sets == ()


def test_a_tracker2_bot_file_imports_as_it_was():
    imported = read(six_entry_ic())
    bot = imported.bot
    assert imported.source == "tracker2" and imported.notes == ()
    assert (bot.name, bot.underlying, bot.frequency) == ("Six-entry IC", "SPX", "daily")
    assert bot.entry_days == (1, 2, 3, 4, 5)
    assert [(t.time, t.enabled, t.quantity) for t in bot.entry_times] == [
        (t, True, None) for t in ("09:47", "12:23", "13:03", "13:43", "14:23", "15:03")
    ]
    assert (bot.use_blackout_days, bot.blackout_date_sets) == (True, ("FOMC",))
    assert (bot.stop_co_mode, bot.stop_co_amount) == ("tighter", D("0.2"))
    assert (bot.use_default_alerts, bot.default_alerts[0].value) == (True, D("75.0"))
    assert bot.move_conflicted_strikes and bot.use_exact_dte and bot.min_short_width is None
    # Legs: the shorts by premium, each wing by premium no further than 75 points out
    sp, lp, sc, lc = bot.legs
    assert (sp.side, sp.right, sp.pick, sp.target, sp.group) == (
        Side.SELL,
        "put",
        "premium",
        D("2.9"),
        0,
    )
    assert (lp.side, lp.link, lp.max_offset, lp.target, lp.group) == (
        Side.BUY,
        0,
        D("-75.0"),
        D("0.05"),
        2,
    )
    assert (sc.target, sc.group, lc.link, lc.max_offset, lc.group) == (D("2.7"), 1, 2, D("75.0"), 3)
    # Groups: each short's stop rests and also closes its wing's group
    names = [g.name for g in bot.groups]
    assert names == ["Short Puts", "Short Calls", "Long Puts", "Long Calls"]
    short_puts, short_calls, long_puts, long_calls = bot.groups
    assert (short_puts.stop_pct, short_puts.stop_basis, short_puts.broker_exit) == (
        D("90.0"),
        "leg_premium",
        "stop",
    )
    assert (short_puts.close_on_stop, short_calls.close_on_stop) == (2, 3)
    assert long_puts.on_stop_remainder == "close_chase" and long_puts.stop_pct is None
    assert long_calls.broker_exit == "target"  # a lone bought leg rests its target (0054)
    # Chase
    assert bot.entry_chase == ChaseDef(D("0"), D("0.05"), 3, 10, "natural_then_leg_out", 2, 3,
                                       False)  # fmt: skip
    assert bot.exit_chase == ChaseDef(D("0"), D("0.05"), 2, 20, "natural_then_leg_out", 2, None,
                                      True)  # fmt: skip
    # Never the sizes, the account or anything private
    assert (bot.contracts, bot.max_per_trade, bot.tag_id, bot.account_number) == (
        1,
        None,
        None,
        "",
    )
    assert not bot.live and not bot.enabled
    assert imported.exported_at.isoformat() == "2026-10-16T01:27:17+00:00"


def test_the_imported_tracker2_bot_validates_once_the_importer_fills_in():
    bot = read(six_entry_ic()).bot
    from dataclasses import replace

    trial = replace(bot, tag_id=1, account_number="5WX01234", max_per_day=6)
    assert validate(trial, {1}) == {}


def test_filters_schedule_and_exits_carry_over():
    data = six_entry_ic()
    data["bot"] |= {
        "use_vix_overnight": True, "vix_on_min_up_pct": 1.0, "vix_on_max_up_pct": 5.0,
        "vix_on_min_down_pct": 0.5, "vix_on_max_down_pct": 4.0,
        "use_intraday_move": True, "intraday_max_up_pct": 1.2, "intraday_max_down_pct": 0.8,
        "use_vix_level": True, "vix_min": 12.0, "vix_max": 30.0,
        "use_technicals": True, "sma_mode": "compare", "sma_days_a": 20, "sma_days_b": 50,
        "max_open_positions": 3, "max_entries_per_day": 4,
        "scheduled_close_time": "15:45", "scheduled_close_dte": None,
        "use_short_put_tested": True, "short_put_tested_pts": -5.0,
        "use_short_call_tested": False, "short_call_tested_pts": 10.0,
        "frequency": "monthly", "monthly_week": 2, "monthly_weekday": 3,
        "use_premium_bounds": True, "premium_min": 1.5, "premium_max": 9.0,
    }  # fmt: skip
    bot = read(data).bot
    assert bot.vix_overnight == MoveLimits(D("1.0"), D("5.0"), D("0.5"), D("4.0"))
    assert bot.intraday == MoveLimits(None, D("1.2"), None, D("0.8"))
    assert (bot.vix_min, bot.vix_max, bot.sma_mode, bot.sma_days_b) == (
        D("12.0"),
        D("30.0"),
        "compare",
        50,
    )
    assert (bot.max_open_trades, bot.max_entries_per_day) == (3, 4)
    assert (bot.close_time, bot.close_dte) == ("15:45", 0)
    # A tested-short row that's off is blank, as the form saves it
    assert (bot.short_put_tested_pts, bot.short_call_tested_pts) == (D("-5.0"), None)
    assert (bot.frequency, bot.monthly_week, bot.monthly_weekday) == ("monthly", 2, 3)
    assert (bot.premium_min, bot.premium_max) == (D("1.5"), D("9.0"))


def test_profit_and_time_actions_carry_over():
    data = six_entry_ic()
    group = data["bot"]["leg_groups"][0]
    group |= {
        "use_profit_actions": True,
        "profit_target_pct": 80.0,
        "profit_actions": [
            {"at": 25.0, "close_pct": 50.0, "adjust_sl_pct": 0.0, "adjust_sl_usd": None},
            {"at": 50.0, "close_pct": 0, "adjust_sl_pct": None, "adjust_sl_usd": 0.5},
        ],
    }
    data["bot"] |= {"use_time_actions": True, "time_actions": [
        {"type": "dte", "days": 0, "at": "14:00", "close_pct": 50, "adjust_sl_pct": None,
         "adjust_pt_pct": 25.0}]}  # fmt: skip
    imported = read(data)
    actions = imported.bot.groups[0].profit_actions
    assert [(a.at, a.close_pct, a.stop_pct, a.stop_usd) for a in actions] == [
        (D("25.0"), 50, D("0.0"), None),
        (D("50.0"), 0, None, D("0.5")),
    ]
    action = imported.bot.time_actions[0]
    assert (action.type, action.days, action.at, action.close_pct, action.target_pct,
            action.stop_pct) == ("dte", 0, "14:00", 50, D("25.0"), None)  # fmt: skip
    assert imported.notes == ()


def test_a_time_actions_stop_move_is_noted():
    data = six_entry_ic()
    data["bot"] |= {"use_time_actions": True, "time_actions": [
        {"type": "dit", "days": 1, "at": "10:00", "close_pct": None, "adjust_sl_pct": 50.0,
         "adjust_pt_pct": None}]}  # fmt: skip
    imported = read(data)
    assert imported.bot.time_actions[0].stop_pct == D("50.0")
    assert any("own stop style" in note for note in imported.notes)


def test_delta_spot_and_offset_picks_carry_over():
    data = six_entry_ic()
    legs = data["bot"]["legs"]
    legs[0] |= {"selection_mode": "delta", "target_value": 16}
    legs[2] |= {"selection_mode": "spot_offset", "target_value": 40.0}
    legs[1] |= {
        "selection_mode": "parent_offset",
        "target_value": -50.0,
        "max_strike_offset": None,
        "exact_strike_offset": False,
    }
    bot = read(data).bot
    assert (bot.legs[0].pick, bot.legs[0].target) == ("delta", D("0.16"))
    assert (bot.legs[2].pick, bot.legs[2].target) == ("spot", D("40.0"))
    assert (bot.legs[1].pick, bot.legs[1].target, bot.legs[1].exact) == (
        "offset",
        D("-50.0"),
        False,
    )


def test_a_bot_without_leg_groups_becomes_one_group_with_the_bots_exits():
    data = six_entry_ic()
    for leg in data["bot"]["legs"]:
        leg["group_index"] = None
    data["bot"] |= {"use_leg_groups": False, "leg_groups": [], "stop_loss_pct": 100.0,
                    "profit_target_pct": 50.0, "broker_exit": "target"}  # fmt: skip
    imported = read(data)
    (group,) = imported.bot.groups
    assert (group.name, group.stop_pct, group.target_pct) == ("Whole trade", D("100.0"), D("50.0"))
    assert (group.stop_basis, group.broker_exit) == ("group_entry", "target")
    assert all(leg.group == 0 for leg in imported.bot.legs)
    assert any("one group" in note for note in imported.notes)


def test_a_one_short_whole_trade_stops_on_its_short():
    data = six_entry_ic()
    data["bot"]["legs"] = [_leg(0, "sell", "put", None, 2.9)]
    data["bot"] |= {"use_leg_groups": False, "leg_groups": [], "stop_loss_pct": 100.0}
    (group,) = read(data).bot.groups
    assert (group.stop_basis, group.broker_exit) == ("leg_premium", "stop")


def test_a_watched_groups_leg_stop_basis_becomes_the_groups_entry_and_is_noted():
    """Both shorts in one group, both wings in another: neither has one sold leg, so the
    app watches their stops as a % of the group's entry. A spread keeps its basis."""
    data = six_entry_ic()
    for leg in data["bot"]["legs"]:
        leg["group_index"] = 0 if leg["action"] == "sell" else 1
    data["bot"]["leg_groups"] = [_group("Shorts", 0, 200.0), _group("Wings", 1)]
    imported = read(data)
    assert [g.stop_basis for g in imported.bot.groups] == ["group_entry", "group_entry"]
    assert [g.broker_exit for g in imported.bot.groups] == ["target", "target"]
    assert [n for n in imported.notes if "entry" in n] == [
        "Shorts: its stop is a % of the group's entry here: the app watches it, as the "
        "group isn't one sold leg."
    ]
    for leg in data["bot"]["legs"]:
        leg["group_index"] = 0 if leg["option_type"] == "put" else 1
    data["bot"]["leg_groups"] = [_group("Puts", 0, 200.0), _group("Calls", 1, 200.0)]
    imported = read(data)
    assert [g.stop_basis for g in imported.bot.groups] == ["leg_premium", "leg_premium"]
    assert imported.notes == ()


def test_chase_endings_this_app_doesnt_have_give_up_and_are_noted():
    data = six_entry_ic()
    configs = data["bot"]["execution_configs"]
    configs["entry"] |= {"exhaust_mode": "leave_alert"}
    imported = read(data)
    assert imported.bot.entry_chase.exhaust_mode == "give_up"
    notes = " ".join(imported.notes)
    assert "Leave working" in notes and "bid-less" not in notes


def test_tracker2_ignore_bidless_longs_imports_without_a_note():
    data = six_entry_ic()
    data["bot"]["execution_configs"]["exit"] |= {"ignore_bidless_longs": True}
    imported = read(data)
    assert imported.bot.exit_chase.ignore_bidless_longs
    assert not imported.bot.entry_chase.ignore_bidless_longs
    assert "bid-less" not in " ".join(imported.notes)


def test_tracker2_keep_conceding_imports_without_a_note():
    data = six_entry_ic()
    data["bot"]["execution_configs"]["exit"] |= {"exhaust_mode": "keep_conceding"}
    imported = read(data)
    assert imported.bot.exit_chase.exhaust_mode == "keep_conceding"
    assert "Keep conceding" not in " ".join(imported.notes)


def test_a_debit_bot_without_broker_exits_rests_its_targets():
    data = six_entry_ic()
    data["bot"]["premium_side"] = "debit"
    for group in data["bot"]["leg_groups"]:
        group["broker_exit"] = None
    assert all(g.broker_exit == "target" for g in read(data).bot.groups)


def test_other_tracker2_settings_are_noted_only_when_they_matter():
    data = six_entry_ic()
    data["bot"] |= {"exit_mode": "poll", "entry_source": "analyzer"}
    notes = " ".join(read(data).notes)
    assert "exit mode" in notes and "by hand" in notes
    assert "Tracker2" not in notes  # its name stays off the screen


def test_private_settings_in_a_tracker2_file_are_ignored():
    data = six_entry_ic()
    data["bot"] |= {
        "quantity": 7,
        "max_contracts_per_trade": 9,
        "account_id": 3,
        "alert_name": "Joint",
        "tags": ["Mine"],
        "mode": "live",
        "is_active": True,
    }
    data["bot"]["entry_times"][0]["quantity_override"] = 5
    bot = read(data).bot
    assert (bot.contracts, bot.max_per_trade, bot.alert_name, bot.tags) == (1, None, None, ())
    assert not bot.live and not bot.enabled and bot.entry_times[0].quantity is None


@pytest.mark.parametrize("change, words", [
    ({"schema_version": 2}, "newer version of the app that made it"),
    ({"schema_version": None}, "no version"),
    ({"bot": []}, "no bot"),
    ({"bot": {"name": " "}}, "no name"),
])  # fmt: skip
def test_a_tracker2_file_that_cant_be_read_is_refused(change, words):
    with pytest.raises(FileError, match=words):
        read(six_entry_ic() | change)


@pytest.mark.parametrize("key, value", [
    ("legs", {"0": {}}), ("legs", [1]), ("leg_groups", "x"), ("entry_times", [None]),
    ("execution_configs", []), ("execution_configs", {"exit": 3}), ("time_actions", {}),
])  # fmt: skip
def test_malformed_tracker2_containers_are_refused_not_crashed_on(key, value):
    data = copy.deepcopy(six_entry_ic())
    data["bot"][key] = value
    with pytest.raises(FileError):
        read(data)


def test_a_wrong_value_in_a_tracker2_file_is_refused_with_its_place():
    data = six_entry_ic()
    data["bot"]["legs"][1]["target_value"] = "lots"
    with pytest.raises(FileError, match="leg 2"):
        read(data)


def test_an_optionsjourney_file_has_no_notes():
    from tests.bots.test_bot_file import NOW, good

    imported = bot_file.parse(bot_file.dumps(good(), NOW))
    assert (imported.source, imported.notes) == ("optionsjourney", ())
    assert tracker2_file.FORMAT == "tracker2.bot"


# --- Analyzer bots become manual bots (#158) ---------------------------------------


def _analyzer(**changes):
    bot = {"name": "Closer", "entry_source": "analyzer", "underlying": "SPX",
           "legs": [_leg(0, "sell", "put", 0, 3.0)], "execution_configs": {"exit": _chase()},
           "profit_target_pct": 50.0, "profit_target_unit": "pct", "profit_target_usd": None,
           "stop_loss_pct": None, "stop_loss_unit": "pct", "stop_loss_usd": 120.0,
           "scheduled_close_time": None, "scheduled_close_dte": None}  # fmt: skip
    return {"format": "tracker2.bot", "schema_version": 1, "bot": bot | changes}


def test_analyzer_bot_becomes_manual():
    rules, notes = tracker2_file.manual_of(_analyzer(stop_loss_unit="usd"))
    assert rules.name == "Closer"
    assert (rules.target_value, rules.target_unit) == (D("50"), "pct")
    assert (rules.stop_value, rules.stop_unit) == (D("120"), "usd")
    assert rules.close_time is None
    text = " ".join(notes).lower()
    for word in ("legs", "chase", "account", "ticker"):
        assert word in text


def test_scheduled_close_maps_to_exit_time():
    rules, _ = tracker2_file.manual_of(
        _analyzer(scheduled_close_time="15:45", scheduled_close_dte=1)
    )
    assert (rules.close_dte, rules.close_time) == (1, "15:45")


def test_place_only_is_an_analyzer_bot_with_no_exits():
    plain = _analyzer(profit_target_pct=None, stop_loss_usd=None)
    assert tracker2_file.is_place_only(plain)
    assert tracker2_file.manual_of(plain)[0] is None
    assert not tracker2_file.is_place_only(_analyzer())
    assert not tracker2_file.is_place_only(
        _analyzer(profit_target_pct=None, stop_loss_usd=None, scheduled_close_time="15:00")
    )
    assert not tracker2_file.is_place_only(
        _analyzer(profit_target_pct=None, stop_loss_usd=None, entry_source="scheduled")
    )


def test_a_bad_analyzer_number_is_refused():
    with pytest.raises(FileError):
        tracker2_file.manual_of(_analyzer(profit_target_pct="lots"))
    with pytest.raises(FileError):
        tracker2_file.manual_of(_analyzer(profit_target_pct=-5))


_ALERTS = [{"type": "pnl_pct", "direction": "below", "value": 30.0},
           {"type": "strike_offset", "points": 5}]  # fmt: skip


def test_default_alerts_carry_over():
    from optionsjourney.bots.definition import DefaultAlertDef

    rules, notes = tracker2_file.manual_of(
        _analyzer(use_default_alerts=True, default_alerts=_ALERTS)
    )
    assert rules.alerts == (DefaultAlertDef("pnl_pct", "below", D("30")),
                            DefaultAlertDef("strike_offset", points=D("5")))  # fmt: skip
    assert not any("alerts" in n for n in notes)


def test_alerts_switched_off_are_ignored():
    rules, _ = tracker2_file.manual_of(_analyzer(use_default_alerts=False, default_alerts=_ALERTS))
    assert rules.alerts == ()


def test_alerts_only_is_not_place_only_and_in_app_only_is_noted():
    only = _analyzer(profit_target_pct=None, stop_loss_usd=None, use_default_alerts=True,
                     default_alerts=_ALERTS, default_alerts_in_app_only=True)  # fmt: skip
    assert not tracker2_file.is_place_only(only)
    rules, notes = tracker2_file.manual_of(only)
    assert len(rules.alerts) == 2 and any("in that app only" in n for n in notes)


def test_dollar_amounts_on_a_ratio_group_keep_their_size():
    """#218: Tracker2 prices a group's $ amounts per full ratio (exits.usd_stop_trigger
    divides by the leg's quantity); here a unit is the ratios over their gcd (decision
    0084). A $2.00 stop on a x3 short is $0.67 a contract here, as it was there."""
    data = six_entry_ic()
    data["bot"]["legs"][0]["quantity"] = 3
    data["bot"]["leg_groups"][0] |= {
        "stop_loss_unit": "usd", "stop_loss_usd": 2.0, "stop_loss_pct": None,
        "profit_target_unit": "usd", "profit_target_usd": 0.9,
        "use_profit_actions": True,
        "profit_actions": [{"at": 0.3, "close_pct": 50.0, "adjust_sl_pct": None,
                            "adjust_sl_usd": 0.6}],
    }  # fmt: skip
    imported = read(data)
    puts, calls = imported.bot.groups[0], imported.bot.groups[1]
    assert (puts.stop_usd, puts.target_usd) == (D("0.67"), D("0.30"))
    assert [(a.at, a.stop_usd) for a in puts.profit_actions] == [(D("0.10"), D("0.20"))]
    assert calls.stop_pct == D("90.0")  # a % is the same either way
    assert any("Short Puts" in note and "÷ 3" in note for note in imported.notes)


def test_a_percent_profit_action_level_is_left_alone():
    data = six_entry_ic()
    data["bot"]["legs"][0]["quantity"] = 2
    data["bot"]["leg_groups"][0] |= {
        "use_profit_actions": True, "profit_target_pct": 80.0,
        "profit_actions": [{"at": 25.0, "close_pct": 50.0, "adjust_sl_pct": None,
                            "adjust_sl_usd": 0.5}],
    }  # fmt: skip
    action = read(data).bot.groups[0].profit_actions[0]
    assert (action.at, action.stop_usd) == (D("25.0"), D("0.25"))


def test_a_whole_trade_bots_dollars_divide_by_the_trades_gcd():
    data = six_entry_ic()
    bot = data["bot"]
    bot |= {"use_leg_groups": False, "stop_loss_pct": None,
            "stop_loss_unit": "usd", "stop_loss_usd": 3.0,
            "profit_target_unit": "usd", "profit_target_usd": 1.0}  # fmt: skip
    for leg in bot["legs"]:
        leg["quantity"] = 2
    group = read(data).bot.groups[0]
    assert (group.stop_usd, group.target_usd) == (D("1.50"), D("0.50"))
