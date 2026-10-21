from dataclasses import replace
from datetime import date

from optionsjourney.bots import floating
from optionsjourney.bots.definition import iron_condor_skeleton

DAY = date(2026, 10, 13)


def bot(on=True, start="11:50", end="12:10"):
    return replace(iron_condor_skeleton("IC"), use_floating_entry=on, floating_start=start,
                   floating_end=end)  # fmt: skip


def test_the_window_is_both_ends_inclusive_and_crosses_the_hour():
    assert floating.window(bot()) == ("11:50", "12:10")
    minutes = ("11:49", "11:50", "12:00", "12:10", "12:11")
    assert [floating.in_window(bot(), t) for t in minutes] == [False, True, True, True, False]
    assert floating.is_final_minute(bot(), "12:10") and not floating.is_final_minute(bot(), "12:09")
    assert floating.label(bot()) == "11:50–12:10"


def test_no_window_while_off_or_unset():
    for b in (bot(on=False), bot(start=None), bot(end="")):
        assert floating.window(b) is None and not floating.in_window(b, "12:00")
        assert not floating.is_final_minute(b, "12:10")


def test_keys():
    assert floating.minute_key(7, DAY, "11:50") == "bot-7-float-20261013-1150"
    assert floating.close_key(7, DAY) == "bot-7-float-20261013-close"
    assert floating.minute_key(7, DAY, "11:50").startswith(floating.prefix(7, DAY))
    assert floating.close_key(7, DAY).startswith(floating.prefix(7, DAY))
