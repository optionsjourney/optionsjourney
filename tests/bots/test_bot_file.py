import dataclasses
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from optionsjourney.bots import bot_file
from optionsjourney.bots.definition import (
    DEFAULT_ENTRY_CHASE,
    DEFAULT_EXIT_CHASE,
    BotDef,
    DefaultAlertDef,
    EntryTimeDef,
    MoveLimits,
    ProfitActionDef,
    TimeActionDef,
    iron_condor_skeleton,
    shape,
)
from optionsjourney.bots.validation import validate

D = Decimal
NOW = datetime(2026, 10, 13, 18, 0, tzinfo=UTC)  # 14:00 New York


def good() -> BotDef:
    bot = iron_condor_skeleton("SPX IC")
    targets = {0: D("2.70"), 1: D("75"), 2: D("2.90"), 3: D("-75")}
    return replace(
        bot, id=7, tag_id=3, account_number="5WX01234", contracts=2, max_per_trade=4,
        max_per_day=6, live=True, enabled=True, tags=("Mine",),
        legs=tuple(replace(leg, target=targets[leg.position],
                           fallback_target=D("2.10") if leg.position in (0, 2) else None)
                   for leg in bot.legs),
        min_short_width=D("20"), keep_trying_minutes=2,  # #314
        use_keep_trying=False,  # #315
        groups=tuple(replace(g, stop_pct=D("95"), target_pct=D("50"), broker_exit="target")
                     for g in bot.groups[:1])
        + (replace(bot.groups[1], stop_pct=D("95"), target_pct=D("50"), use_profit_actions=True,
                   broker_exit="target",
                   profit_actions=(ProfitActionDef(D("25"), 33, D("0")),)),),
        entry_times=(EntryTimeDef("09:47", True, 3, "open"), EntryTimeDef("12:03", False)),
        max_entries_per_day=2, max_open_trades=3, use_blackout_days=True,
        blackout_days=("2026-10-01", "2026-12-24"), blackout_date_sets=("FOMC",),
        blackout_dte_sets=("FOMC", "HOLIDAY"),
        use_vix_overnight=True, vix_overnight=MoveLimits(max_up=D("5")),
        close_time="15:45", use_time_actions=True,
        time_actions=(TimeActionDef("dte", 0, "14:00", D("150"), None, 50),),
        use_sl_ratio_exit=True, sl_ratio_below=D("0.78"), use_underlying_exit=True,
        underlying_up_pct=D("1.5"), short_put_tested_pts=D("-5"),
        alert_name="IC Joint 6789", alert_channel="telegram", use_default_alerts=True,
        default_alerts=(DefaultAlertDef("pnl_pct", "above", D("15")),
                        DefaultAlertDef("strike_offset", points=D("10"))),
        default_alerts_in_app_only=True,
    )  # fmt: skip


def round_trip(bot: BotDef) -> BotDef:
    return bot_file.parse(json.dumps(bot_file.to_file(bot, NOW))).bot


def test_a_bot_round_trips_through_its_file():
    bot = good()
    back = round_trip(bot)
    private = {"id", "tag_id", "broker", "account_number", "contracts", "live", "enabled",
               "max_per_trade", "max_per_day", "tags", "archived", "entry_times",
               "blackout_days", "alert_name", "alert_channel"}  # fmt: skip
    for field in dataclasses.fields(BotDef):
        if field.name not in private:
            assert getattr(back, field.name) == getattr(bot, field.name), field.name
    # Each entry time keeps its time, on/off and label, never its quantity
    assert back.entry_times == (EntryTimeDef("09:47", True, None, "open"),
                                EntryTimeDef("12:03", False))  # fmt: skip
    assert back.blackout_days == ("2026-12-24",)


def test_an_imported_bot_starts_with_no_account_tag_or_sizes_and_safe():
    back = round_trip(good())
    assert (back.id, back.tag_id, back.account_number) == (None, None, "")
    assert (back.live, back.enabled, back.tags, back.archived) == (False, False, (), False)
    assert (back.contracts, back.max_per_trade, back.max_per_day) == (1, None, None)


def test_every_bot_field_is_shared_or_private():
    """A new BotDef field fails here until someone decides whether it's shared."""
    names = {f.name for f in dataclasses.fields(BotDef)}
    assert names == bot_file.SHARED | bot_file.PRIVATE
    assert not bot_file.SHARED & bot_file.PRIVATE
    assert {f.name for f in dataclasses.fields(EntryTimeDef)} == (
        bot_file.ENTRY_TIME_SHARED | {"quantity"}
    )


def test_private_fields_are_never_written():
    data = bot_file.to_file(good(), NOW)
    text = json.dumps(data)
    assert "5WX01234" not in text and "Mine" not in text and "IC Joint" not in text
    for name in bot_file.PRIVATE:
        assert name not in data["bot"], name
    assert all("quantity" not in t for t in data["bot"]["entry_times"])
    assert (data["format"], data["version"]) == ("optionsjourney.bot", 3)
    assert data["exported_at"] == "2026-10-13T18:00:00Z"


def test_money_is_written_as_text():
    data = bot_file.to_file(good(), NOW)
    assert data["bot"]["legs"][0]["target"] == "2.70"
    assert data["bot"]["sides"][0]["stop_pct"] == "95"
    assert data["bot"]["entry_chase"]["step"] == "0.05"
    assert data["bot"]["vix_overnight"] == {"min_up": None, "max_up": "5", "min_down": None,
                                           "max_down": None}  # fmt: skip


def test_past_blackout_days_are_dropped():
    data = bot_file.to_file(good(), NOW)
    assert data["bot"]["blackout_days"] == ["2026-12-24"] and data["bot"]["use_blackout_days"]
    only_past = replace(good(), blackout_days=("2026-10-01",), blackout_date_sets=(),
                        blackout_dte_sets=())  # fmt: skip
    data = bot_file.to_file(only_past, NOW)
    assert data["bot"]["blackout_days"] == [] and data["bot"]["use_blackout_days"] is False
    # #207: a Before expiration list alone still blacks out
    data = bot_file.to_file(replace(only_past, blackout_dte_sets=("HOLIDAY",)), NOW)
    assert data["bot"]["blackout_dte_sets"] == ["HOLIDAY"] and data["bot"]["use_blackout_days"]
    # A file from before #207 has no Before expiration lists
    del data["bot"]["blackout_dte_sets"]
    assert bot_file.from_file(data).bot.blackout_dte_sets == ()


def test_a_newer_file_version_is_refused():
    data = bot_file.to_file(good(), NOW) | {"version": 4}
    with pytest.raises(bot_file.FileError, match="newer OptionsJourney .file version 4.*Update"):
        bot_file.from_file(data)
    for version in (None, "1", 0, True, 1.0):
        with pytest.raises(bot_file.FileError, match="version"):
            bot_file.from_file(bot_file.to_file(good(), NOW) | {"version": version})


def test_a_file_that_is_not_a_bot_is_refused():
    with pytest.raises(bot_file.FileError, match="isn't JSON"):
        bot_file.parse(b"{not json")
    with pytest.raises(bot_file.FileError, match="isn't an OptionsJourney bot file"):
        bot_file.parse(json.dumps({"format": "other.bot", "version": 1, "bot": {}}))
    with pytest.raises(bot_file.FileError, match="isn't an OptionsJourney bot file"):
        bot_file.parse("[1, 2]")
    with pytest.raises(bot_file.FileError, match="too large"):
        bot_file.parse(b" " * (bot_file.MAX_BYTES + 1))
    with pytest.raises(bot_file.FileError, match="no bot"):
        bot_file.from_file({"format": "optionsjourney.bot", "version": 1, "bot": {"name": " "}})


def _bad(path, value):
    data = bot_file.to_file(good(), NOW)
    target = data["bot"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return data


@pytest.mark.parametrize(
    "path, value, words",
    [
        (("legs",), "four", "legs"),
        (("legs",), [1, 2], "legs"),
        (("legs", 0, "target"), "lots", "target"),
        (("legs", 0, "target"), "NaN", "target"),
        (("legs", 0, "target"), True, "target"),
        (("legs", 0, "target"), "1e999", "target"),
        (("legs", 0, "ratio"), "1", "ratio"),
        (("legs", 0, "ratio"), True, "ratio"),
        (("legs", 0, "ratio"), 10**12, "ratio"),
        (("legs", 0, "side"), "short", "side"),
        (("legs", 0, "position"), 5, "position"),
        (("sides", 0, "name"), "x" * 61, "name"),
        (("sides", 0, "stop_pct"), [95], "stop_pct"),
        (("entry_times", 0, "enabled"), "yes", "enabled"),
        (("entry_chase",), [], "entry_chase"),
        (("entry_days",), "mon", "entry_days"),
        (("time_actions", 0), "14:00", "time_actions"),
        (("vix_overnight",), {"max_up": "x"}, "max_up"),
        (("name",), 7, "name"),
        (("frequency",), None, "frequency"),
    ],
)
def test_malformed_files_are_refused_not_crashed_on(path, value, words):
    with pytest.raises(bot_file.FileError, match=words):
        bot_file.from_file(_bad(path, value))


def test_a_bot_bigger_than_the_editor_is_refused():
    data = bot_file.to_file(good(), NOW)
    data["bot"]["legs"].append(dict(data["bot"]["legs"][0], position=4))
    with pytest.raises(bot_file.FileError, match="4 legs"):
        bot_file.from_file(data)
    data = bot_file.to_file(good(), NOW)
    side = data["bot"]["sides"][0]
    data["bot"]["sides"] += [dict(side, position=n) for n in (2, 3, 4)]
    with pytest.raises(bot_file.FileError, match="4 sides"):
        bot_file.from_file(data)
    data = bot_file.to_file(good(), NOW)
    data["bot"]["entry_times"] = [{"time": "10:00", "enabled": True, "label": None}] * 51
    with pytest.raises(bot_file.FileError, match="50 entry times"):
        bot_file.from_file(data)


def test_private_fields_in_a_file_are_ignored():
    data = bot_file.to_file(good(), NOW)
    data["bot"] |= {"account_number": "9ZZ99999", "live": True, "enabled": True, "id": 1,
                    "tag_id": 1, "contracts": 50, "max_per_day": 99, "tags": ["x"],
                    "archived": True, "broker": "schwab", "surprise": {"a": 1}}  # fmt: skip
    data["bot"]["entry_times"][0]["quantity"] = 40
    back = bot_file.from_file(data).bot
    assert (back.account_number, back.live, back.enabled, back.id) == ("", False, False, None)
    assert (back.contracts, back.max_per_day, back.tags, back.broker) == (1, None, (), "tastytrade")
    assert back.entry_times[0].quantity is None and not back.archived


def test_missing_settings_take_new_bot_defaults():
    data = bot_file.to_file(good(), NOW)
    bot = data["bot"]
    data["bot"] = {k: bot[k] for k in ("name", "legs", "sides", "entry_times")}
    back = bot_file.from_file(data).bot
    assert (back.frequency, back.use_vix_overnight, back.close_time) == ("daily", False, None)
    assert (back.entry_chase, back.exit_chase) == (DEFAULT_ENTRY_CHASE, DEFAULT_EXIT_CHASE)
    assert back.max_entries_per_day is None and back.time_actions == ()


def test_a_file_from_a_valid_bot_validates_once_the_importer_fills_in():
    back = round_trip(good())
    filled = replace(back, tag_id=3, account_number="5WX01234", max_per_day=2)
    assert validate(filled, {3}) == {}


def test_the_exported_at_is_read_back():
    assert bot_file.parse(json.dumps(bot_file.to_file(good(), NOW))).exported_at == NOW


def test_the_file_name_is_the_bot_name_in_kebab_case():
    assert bot_file.file_name("SPX IC (copy 2)") == "spx-ic-copy-2.json"
    assert bot_file.file_name("***") == "bot.json"


def test_a_file_from_before_slice_7_imports_with_no_watch_rules_or_profit_actions():
    data = bot_file.to_file(good(), NOW)
    for key in ("use_sl_ratio_exit", "sl_ratio_below", "use_underlying_exit",
                "underlying_up_pct", "short_put_tested_pts"):  # fmt: skip
        del data["bot"][key]
    for side in data["bot"]["sides"]:
        side.pop("use_profit_actions"), side.pop("profit_actions")
    del data["bot"]["time_actions"][0]["close_pct"]
    bot = bot_file.from_file(data).bot
    assert (bot.use_sl_ratio_exit, bot.use_underlying_exit) == (False, False)
    assert all(not g.use_profit_actions and g.profit_actions == () for g in bot.groups)
    assert bot.time_actions[0].close_pct is None


def test_a_profit_action_in_a_file_is_type_checked():
    data = bot_file.to_file(good(), NOW)
    data["bot"]["sides"][1]["profit_actions"][0]["close_pct"] = "33"
    with pytest.raises(bot_file.FileError, match="close_pct"):
        bot_file.from_file(data)


def test_default_alerts_are_shared_and_type_checked():
    data = bot_file.to_file(good(), NOW)
    assert data["bot"]["default_alerts"] == [
        {"type": "pnl_pct", "direction": "above", "value": "15", "points": None},
        {"type": "strike_offset", "direction": None, "value": None, "points": "10"},
    ]
    back = round_trip(good())
    assert (back.alert_name, back.alert_channel) == (None, "all")  # yours, never shared
    data["bot"]["default_alerts"][1]["points"] = [10]
    with pytest.raises(bot_file.FileError, match="points"):
        bot_file.from_file(data)


def test_a_file_from_before_slice_9_imports_with_no_default_alerts():
    data = bot_file.to_file(good(), NOW)
    for key in ("use_default_alerts", "default_alerts", "default_alerts_in_app_only"):
        del data["bot"][key]
    bot = bot_file.from_file(data).bot
    assert (bot.use_default_alerts, bot.default_alerts, bot.default_alerts_in_app_only) == (
        False, (), False,
    )  # fmt: skip


def test_delta_and_spot_picks_and_the_floating_window_are_shared():
    bot = good()
    legs = (replace(bot.legs[0], pick="delta", target=D("0.16")), bot.legs[1],
            replace(bot.legs[2], pick="spot", target=D("-50")), bot.legs[3])  # fmt: skip
    bot = replace(bot, legs=legs, use_floating_entry=True, floating_start="11:30",
                  floating_end="11:45", close_time=None)  # fmt: skip
    back = round_trip(bot)
    assert back.legs == bot.legs
    assert (back.use_floating_entry, back.floating_start, back.floating_end) == (
        True, "11:30", "11:45",
    )  # fmt: skip


def test_a_file_from_before_slice_10_imports_with_floating_off():
    data = bot_file.to_file(good(), NOW)
    for key in ("use_floating_entry", "floating_start", "floating_end"):
        del data["bot"][key]
    bot = bot_file.from_file(data).bot
    assert (bot.use_floating_entry, bot.floating_start, bot.floating_end) == (False, None, None)


def test_a_delta_in_delta_points_in_a_file_is_read_as_the_form_reads_it():
    data = bot_file.to_file(good(), NOW)
    data["bot"]["legs"][0] |= {"pick": "delta", "target": "16"}
    assert bot_file.from_file(data).bot.legs[0].target == D("0.16")
    data["bot"]["legs"][0]["target"] = "0.16"
    assert bot_file.from_file(data).bot.legs[0].target == D("0.16")


def test_a_long_call_bot_round_trips_as_version_3():
    bot = shape("long_call", iron_condor_skeleton("Long call"))
    leg = replace(bot.legs[0], target=D("3.10"))
    group = replace(bot.groups[0], stop_basis="group_entry", stop_pct=D("50"), target_pct=D("80"),
                    broker_exit="target")  # fmt: skip
    bot = replace(bot, legs=(leg,), groups=(group,), contracts=2, max_per_trade=2, max_per_day=4)
    data = bot_file.to_file(bot, NOW)
    assert data["version"] == 3 and "opens_for" not in data["bot"]
    assert data["bot"]["sides"][0]["rests"] == "target"
    assert "broker_exit" not in data["bot"]["sides"][0]
    back = round_trip(bot)
    assert back.legs == bot.legs and back.groups == bot.groups


@pytest.mark.parametrize(("version", "opens_for", "rests"),
                         [(1, None, "stop"), (2, "credit", "stop"), (2, "debit", "target"),
                          (2, "banana", "stop")])  # fmt: skip
def test_versions_1_and_2_set_each_groups_rests_from_what_they_opened_for(version, opens_for,
                                                                         rests):  # fmt: skip
    data = bot_file.to_file(shape("short_put", good()), NOW)  # a lone short: it may rest a stop
    data["version"] = version
    for side in data["bot"]["sides"]:
        side.pop("rests")
    if opens_for is not None:
        data["bot"]["opens_for"] = opens_for
    bot = bot_file.from_file(data).bot
    assert [g.broker_exit for g in bot.groups] == [rests] * len(bot.groups)


def test_ratios_above_10_in_a_file_are_refused_by_validation_after_import():
    bot = shape("long_call", iron_condor_skeleton("Long call"))
    bot = replace(
        bot,
        legs=(replace(bot.legs[0], ratio=11, target=D("3.10")),),
        groups=(replace(bot.groups[0], stop_basis="group_entry", stop_pct=D("50")),),
        tag_id=3,
        account_number="5WX01234",
        max_per_day=20,
        max_per_trade=20,
    )
    back = round_trip(bot)
    assert back.legs[0].ratio == 11
    assert validate(back, {3})["leg-0"] == "The ratio is 1 to 10."


def test_a_version_3_side_with_an_unknown_rests_reads_and_validation_refuses_it():
    data = bot_file.to_file(good(), NOW)
    data["bot"]["sides"][0]["rests"] = "banana"
    bot = bot_file.from_file(data).bot
    filled = replace(bot, tag_id=3, account_number="5WX01234", max_per_day=2)
    assert "rests at the broker" in validate(filled, {3})["group-0"]


def test_leg_dtes_and_exact_dte_are_shared():
    """Bots slice 14: each leg's DTE (already in version 2) and use_exact_dte; a file
    without it imports with exact DTE on."""
    bot = good()
    legs = (bot.legs[0], replace(bot.legs[1], dte=14), bot.legs[2], replace(bot.legs[3], dte=7))
    bot = replace(bot, legs=legs, use_exact_dte=False)
    back = round_trip(bot)
    assert [leg.dte for leg in back.legs] == [0, 14, 0, 7]
    assert back.use_exact_dte is False
    data = bot_file.to_file(good(), NOW)
    del data["bot"]["use_exact_dte"]
    assert bot_file.from_file(data).bot.use_exact_dte is True


def test_slice_19_settings_are_shared_but_the_sizing_stays_private():
    """The chase's window and market switch, the leg deltas and max offset, the min short
    width, moving conflicted strikes and a $ profit-action stop go in the file; dollar
    sizing never does (share_format.build_snapshot writes it as quantity)."""
    bot = good()
    legs = list(bot.legs)
    legs[0] = replace(legs[0], exit_delta_above=D("40"))
    legs[3] = replace(legs[3], pick="premium", target=D("0.5"), link=2, max_offset=D("-25"))
    side = replace(bot.groups[1], profit_actions=(ProfitActionDef(D("25"), 0, None, D("0.4")),))
    bot = replace(
        bot, legs=tuple(legs), groups=(bot.groups[0], side), use_delta_exit=True,
        min_short_width=D("20"), move_conflicted_strikes=True,
        entry_chase=replace(bot.entry_chase, window_minutes=3),
        exit_chase=replace(bot.exit_chase, switch_to_market=True),
        sizing="dollars", max_per_entry_usd=D("300"),
    )  # fmt: skip
    data = bot_file.to_file(bot, NOW)
    assert "sizing" not in data["bot"] and "max_per_entry_usd" not in data["bot"]
    back = bot_file.from_file(json.loads(json.dumps(data))).bot
    assert back.legs == bot.legs and back.groups[1].profit_actions == side.profit_actions
    assert (back.min_short_width, back.move_conflicted_strikes) == (D("20"), True)
    assert back.entry_chase.window_minutes == 3 and back.exit_chase.switch_to_market
    assert (back.sizing, back.max_per_entry_usd) == ("contracts", None)


def test_a_file_from_before_slice_19_imports_as_before():
    data = bot_file.to_file(good(), NOW)
    for leg in data["bot"]["legs"]:
        for key in ("exit_delta_below", "exit_delta_above", "max_offset"):
            del leg[key]
    for key in ("min_short_width", "move_conflicted_strikes"):
        del data["bot"][key]
    for chase in ("entry_chase", "exit_chase"):
        del data["bot"][chase]["window_minutes"], data["bot"][chase]["switch_to_market"]
    back = bot_file.from_file(data).bot
    assert all(leg.max_offset is None and leg.exit_delta_above is None for leg in back.legs)
    assert (back.min_short_width, back.move_conflicted_strikes) == (None, False)
    assert back.entry_chase.window_minutes is None and not back.exit_chase.switch_to_market


def test_a_groups_pairing_is_in_its_file():
    """Bots slice 21: "when its stop fills, also close" travels with the bot."""
    bot = good()
    bot = replace(bot, groups=(replace(bot.groups[0], close_on_stop=1), bot.groups[1]))
    assert round_trip(bot).groups[0].close_on_stop == 1
    assert round_trip(good()).groups[0].close_on_stop is None


def test_a_file_with_a_spread_set_to_rest_its_stop_rests_its_target():
    """Decision 0054: whatever the file says, a group of more than one leg rests its target."""
    data = bot_file.to_file(good(), NOW)
    for side in data["bot"]["sides"]:
        side["rests"] = "stop"
    assert {g.broker_exit for g in bot_file.from_file(data).bot.groups} == {"target"}


def test_ignore_bidless_longs_round_trips_through_a_bot_file():
    bot = good()
    bot = replace(bot, exit_chase=replace(bot.exit_chase, ignore_bidless_longs=True))
    data = bot_file.to_file(bot, NOW)
    assert data["bot"]["exit_chase"]["ignore_bidless_longs"] is True
    assert bot_file.from_file(json.loads(json.dumps(data))).bot.exit_chase.ignore_bidless_longs
    del data["bot"]["exit_chase"]["ignore_bidless_longs"]  # a file from before it existed
    assert not bot_file.from_file(data).bot.exit_chase.ignore_bidless_longs


def test_a_file_from_before_the_switches_keeps_its_min_width_on():
    raw = bot_file.to_file(good(), NOW)
    del raw["bot"]["use_min_short_width"]
    back = bot_file.parse(json.dumps(raw)).bot
    assert back.use_min_short_width is True and back.min_short_width == D("20")
