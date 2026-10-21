"""Bots slice 20: the list's search (Tracker2's automation.js matchesBotSearch)."""

from dataclasses import replace

from optionsjourney.bots import search
from optionsjourney.bots.definition import iron_condor_skeleton

BOT = replace(iron_condor_skeleton("Beta SPX IC"), id=1, account_number="5WX01234")


def test_every_word_must_match_name_account_or_underlying():
    assert search.matches(BOT, "")
    assert search.matches(BOT, "beta")
    assert search.matches(BOT, "BETA spx")
    assert search.matches(BOT, "1234")
    assert not search.matches(BOT, "beta qqq")
    assert search.matches(replace(BOT, name="Wings", underlying="QQQ"), "qqq")


def test_a_day_word_matches_the_entry_days_never_the_name():
    weekly = replace(BOT, name="Tuesday special", frequency="weekly", entry_days=(1, 3))
    assert search.matches(weekly, "mon")
    assert search.matches(weekly, "Wednesday")
    assert not search.matches(weekly, "tue")  # the name says Tuesday; its days don't
    assert search.matches(BOT, "fri")  # daily counts as Monday to Friday
    assert search.matches(weekly, "special weds")
