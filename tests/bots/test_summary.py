from dataclasses import replace
from decimal import Decimal

from optionsjourney.bots import schedule, summary
from optionsjourney.bots.definition import EntryTimeDef, iron_condor_skeleton

BOT = iron_condor_skeleton("x")


def test_entry_lists_every_time_with_its_size_and_the_days():
    bot = replace(
        BOT, frequency="weekly", entry_days=(1, 2, 3, 4, 5), contracts=1,
        entry_times=(EntryTimeDef("09:47"), EntryTimeDef("13:03", quantity=2)),
        use_blackout_days=True, blackout_date_sets=("FOMC",),
    )  # fmt: skip
    lines = summary.entry(bot)
    assert lines[0] == "09:47, 13:03 ×2 · Mon–Fri"
    assert lines[1] == "1–2 contracts" and "Use blackout days: FOMC (entry day)" in lines


def test_blackout_lists_say_which_checks_they_have():
    """#207: Tracker2's _blackout_set_labels."""
    bot = replace(BOT, use_blackout_days=True, blackout_date_sets=("FOMC",),
                  blackout_dte_sets=("HOLIDAY", "FOMC"))  # fmt: skip
    assert "Use blackout days: FOMC (entry day + before expiration), Market holiday " \
        "(before expiration)" in summary.entry(bot)
    assert "Use blackout days" in summary.entry(replace(bot, blackout_date_sets=(),
                                                        blackout_dte_sets=()))  # fmt: skip


def test_days_in_a_row_read_as_a_range():
    bot = replace(BOT, frequency="weekly", entry_days=(1, 2, 3, 5))
    assert schedule.summary(bot)[0] == "Mon–Wed, Fri"


def test_legs_groups_and_chase_in_a_line_each():
    assert all("DTE" in line for line in summary.legs(BOT)) and len(summary.legs(BOT)) == 4
    groups = tuple(replace(g, stop_pct=Decimal("90")) for g in BOT.groups)
    lines = summary.exit_lines(replace(BOT, groups=groups))
    if len(groups) > 1:
        assert lines[0] == f"Leg groups ({len(groups)})" and "SL 90%" in lines[1]
    else:
        assert "SL 90%" in lines[0]
    phases = summary.execution(BOT)
    assert [p for p, _ in phases] == ["Entry", "Exit"] and "tries" in phases[0][1][0]


def test_a_zero_starting_offset_is_left_out():
    chase = replace(BOT.entry_chase, start_offset=Decimal("0"))
    assert not any("offset" in line for line in summary.chase_lines(chase))
    assert summary.chase_lines(replace(chase, start_offset=Decimal("0.05")))[1] == "Starting offset 0.05"
