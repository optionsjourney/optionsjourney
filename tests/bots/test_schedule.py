from dataclasses import replace
from datetime import date, datetime, time, timedelta

from optionsjourney.bots import schedule
from optionsjourney.bots.definition import EntryTimeDef, iron_condor_skeleton

DAY = date(2026, 10, 13)  # a Tuesday


def at(h, m, s=0):
    return datetime(2026, 10, 13, h, m, s)


def test_keys():
    assert schedule.entry_key(7, "09:47", DAY) == "bot-7-0947-20261013"
    assert schedule.manual_key(7, at(10, 1, 2)).startswith("bot-7-now-20261013")


def test_due_fires_within_three_minutes_and_skips_after():
    times = (EntryTimeDef("09:47"), EntryTimeDef("12:03"), EntryTimeDef("13:00", False))
    assert schedule.due(times, at(9, 46), set(), 7) == []
    assert schedule.due(times, at(9, 49, 59), set(), 7) == [("09:47", "fire")]
    assert schedule.due(times, at(9, 51), set(), 7) == [("09:47", "skip")]
    done = {schedule.entry_key(7, "09:47", DAY)}
    assert schedule.due(times, at(12, 3), done, 7) == [("12:03", "fire")]


def test_entry_cutoff():
    assert schedule.entry_cutoff(DAY) == time(15, 58)
    assert schedule.entry_cutoff(date(2026, 10, 17)) is None  # Saturday


BOT = iron_condor_skeleton("x")


def test_daily_runs_every_day():
    assert schedule.runs_on(BOT, DAY) and schedule.runs_on(BOT, date(2026, 10, 17))


def test_weekly_runs_on_the_ticked_weekdays_monday_is_1():
    bot = replace(BOT, frequency="weekly", entry_days=(1, 5))
    assert schedule.runs_on(bot, date(2026, 10, 12))  # Monday
    assert not schedule.runs_on(bot, DAY)  # Tuesday
    assert schedule.runs_on(bot, date(2026, 10, 16))  # Friday


def test_monthly_is_the_nth_weekday_of_the_month():
    fifth_friday = replace(BOT, frequency="monthly", monthly_week=5, monthly_weekday=5)
    assert schedule.runs_on(fifth_friday, date(2026, 10, 30))  # October 2026 has 5 Fridays
    assert not schedule.runs_on(fifth_friday, date(2026, 10, 23))
    assert not any(  # November 2026 has only 4 Fridays
        schedule.runs_on(fifth_friday, date(2026, 11, d)) for d in range(1, 31)
    )
    assert schedule.is_nth_weekday(date(2026, 10, 7), 1, 3)  # first Wednesday
    assert not schedule.is_nth_weekday(date(2026, 10, 8), 1, 3)
    assert schedule.is_nth_weekday(date(2026, 10, 1), 1, 4)  # day 1 is week 1
    assert schedule.is_nth_weekday(date(2026, 10, 8), 2, 4)  # day 8 is week 2


def test_specific_dates():
    bot = replace(BOT, frequency="specific_dates", specific_dates=("2026-10-13",))
    assert schedule.runs_on(bot, DAY) and not schedule.runs_on(bot, date(2026, 10, 14))


def test_blackout_only_when_switched_on():
    bot = replace(BOT, blackout_days=("2026-10-13",))
    assert not schedule.is_blackout(bot, DAY)
    on = replace(bot, use_blackout_days=True)
    assert schedule.is_blackout(on, DAY) and not schedule.is_blackout(on, date(2026, 10, 14))


def test_parse_date_list_takes_pastes_with_commas_and_newlines():
    good, bad = schedule.parse_date_list("2026-10-14, 2026-10-13\n2026-10-14\t2026-12-25")
    assert good == ("2026-10-13", "2026-10-14", "2026-12-25") and bad == ()
    good, bad = schedule.parse_date_list("2026-10-13, 10/14/2026,\n2026-02-30  nope ,,")
    assert good == ("2026-10-13",) and bad == ("10/14/2026", "2026-02-30", "nope")
    assert schedule.parse_date_list("") == ((), ())


def test_skip_target_is_today_until_the_close_then_the_next_session():
    assert schedule.skip_target_day(at(8, 0)) == DAY  # before the open
    assert schedule.skip_target_day(at(15, 59)) == DAY
    assert schedule.skip_target_day(at(16, 0)) == date(2026, 10, 14)  # after the close
    friday = datetime(2026, 10, 16, 17, 0)
    assert schedule.skip_target_day(friday) == date(2026, 10, 19)  # Monday
    assert schedule.skip_target_day(datetime(2026, 10, 17, 10, 0)) == date(2026, 10, 19)
    # The day before a holiday (Thanksgiving, Thursday 2026-11-26) after the close.
    assert schedule.skip_target_day(datetime(2026, 11, 25, 16, 30)) == date(2026, 11, 27)
    assert schedule.skip_target_day(datetime(2026, 11, 27, 13, 0)) == date(2026, 11, 30)  # half day


def test_an_unknown_frequency_never_runs():
    assert not schedule.runs_on(replace(BOT, frequency="sometimes"), DAY)


# Slice 5: named date lists (guards._frequency_check, is_trading_day)


def test_a_specific_list_adds_its_days_and_an_unreadable_one_adds_none():
    bot = replace(BOT, frequency="specific_dates", specific_dates=("2026-10-20",),
                  specific_date_sets=("FOMC",))  # fmt: skip
    assert schedule.runs_on(bot, DAY, {"FOMC": True})
    assert not schedule.runs_on(bot, DAY, {"FOMC": False})
    assert not schedule.runs_on(bot, DAY, {"FOMC": None})
    assert not schedule.runs_on(bot, DAY)
    assert schedule.runs_on(bot, date(2026, 10, 20), {"FOMC": None})  # a typed date still runs


def test_a_blackout_list_hits_only_while_blackout_days_is_on():
    bot = replace(BOT, use_blackout_days=True, blackout_date_sets=("CPI", "EOM"))
    assert schedule.is_blackout(bot, DAY, {"CPI": True, "EOM": False})
    assert schedule.blackout_lists_hit(bot, DAY, {"CPI": True, "EOM": False}) == ["CPI"]
    assert not schedule.is_blackout(bot, DAY, {"CPI": False, "EOM": None})
    assert schedule.unavailable_blackout_lists(bot, DAY, {"CPI": False, "EOM": None}) == ["EOM"]
    off = replace(bot, use_blackout_days=False)
    assert not schedule.is_blackout(off, DAY, {"CPI": True})
    assert schedule.unavailable_blackout_lists(off, DAY, {"EOM": None}) == []


def test_the_lists_a_bot_uses():
    bot = replace(BOT, specific_date_sets=("FOMC",), blackout_date_sets=("CPI",))
    assert schedule.lists_in_use(bot) == ()
    assert schedule.lists_in_use(replace(bot, frequency="specific_dates")) == ("FOMC",)
    both = replace(bot, frequency="specific_dates", use_blackout_days=True)
    assert schedule.lists_in_use(both) == ("CPI", "FOMC")


def test_a_typed_time_is_read_like_tracker2():
    for typed in ("945", "0945", "9:45", " 09:45 "):
        assert schedule.normalize_time(typed) == "09:45"
    assert schedule.normalize_time("1314") == "13:14"
    assert schedule.normalize_time("130") == "01:30"  # padded, never guessed at
    for bad in ("", "9", "94500", "9:5", "24:00", "09:60", "nine", "9:45:15", "9:45 am"):
        assert schedule.normalize_time(bad) is None


def test_entry_times_may_carry_seconds_and_zero_seconds_are_dropped():
    for typed in ("9:30:15", "93015", "093015"):
        assert schedule.normalize_time(typed, seconds=True) == "09:30:15"
    assert schedule.normalize_time("09:30:00", seconds=True) == "09:30"
    assert schedule.normalize_time("093000", seconds=True) == "09:30"
    assert schedule.normalize_time("945", seconds=True) == "09:45"
    assert schedule.normalize_time("9:30:60", seconds=True) is None
    assert schedule.normalize_time("9:30:1", seconds=True) is None


def test_an_entry_time_with_seconds_fires_from_its_second():
    times = (EntryTimeDef("09:30:15"), EntryTimeDef("09:30"))
    assert schedule.due(times, at(9, 30, 10), set(), 7) == [("09:30", "fire")]
    done = {schedule.entry_key(7, "09:30", DAY)}
    assert schedule.due(times, at(9, 30, 20), done, 7) == [("09:30:15", "fire")]
    assert schedule.entry_key(7, "09:30:15", DAY) == "bot-7-093015-20261013"


def test_summary_says_the_days_and_times_in_a_few_words():
    bot = replace(
        iron_condor_skeleton("x"), frequency="weekly", entry_days=(3, 1),
        entry_times=(EntryTimeDef("09:32"), EntryTimeDef("10:00", enabled=False)),
    )  # fmt: skip
    assert schedule.summary(bot) == ("Mon, Wed", "09:32")
    assert schedule.summary(replace(bot, frequency="daily", use_floating_entry=True,
                                    floating_start="10:00", floating_end="11:00")) == (
        "Every day", "10:00–11:00")  # fmt: skip
    monthly = replace(bot, frequency="monthly", monthly_week=3, monthly_weekday=5)
    assert schedule.summary(monthly)[0] == "3rd Fri monthly"
    dated = replace(bot, frequency="specific_dates", specific_dates=("2026-10-14",),
                    specific_date_sets=("FOMC",))  # fmt: skip
    assert schedule.summary(dated)[0] == "FOMC, 1 date"


# Issue #207 (Tracker2 ef862660, 04eaa8e4): each blackout list has two checks
MON, FOMC_WED = date(2026, 10, 26), date(2026, 10, 28)


def weekly(dte, **kw):
    legs = tuple(replace(leg, dte=dte) for leg in BOT.legs)
    return replace(BOT, legs=legs, use_blackout_days=True, **kw)


def ahead(name, days, hits=()):
    return {name: {d: d in hits for d in days}}


def test_the_expiry_estimate_is_the_longest_leg_rolled_to_a_session():
    assert schedule.expiry_estimate(weekly(7), MON) == date(2026, 11, 2)
    # Fri 11/20 + 6 is Thanksgiving: the next session
    assert schedule.expiry_estimate(weekly(6), date(2026, 11, 20)) == date(2026, 11, 27)
    assert schedule.expiry_estimate(weekly(0), MON) == MON
    calendar = replace(BOT, legs=(replace(BOT.legs[0], dte=1), replace(BOT.legs[1], dte=4)))
    assert schedule.expiry_estimate(calendar, date(2026, 10, 16)) == date(2026, 10, 20)
    assert schedule.expiry_window(weekly(7), MON) == tuple(
        date(2026, 10, 27) + timedelta(days=i) for i in range(7))  # fmt: skip
    assert schedule.expiry_window(weekly(0), MON) == ()


def test_before_expiration_skips_an_event_after_the_entry_day():
    bot = weekly(7, blackout_dte_sets=("FOMC",))
    window = schedule.expiry_window(bot, MON)
    lists = {"FOMC": False}
    hit = schedule.blackout_lists_hit(bot, MON, lists, ahead("FOMC", window, {FOMC_WED}))
    assert hit == ["FOMC on 2026-10-28, before expiration"]
    assert schedule.is_blackout(bot, MON, lists, ahead("FOMC", window, {FOMC_WED}))
    assert not schedule.is_blackout(bot, MON, lists, ahead("FOMC", window))


def test_the_event_day_itself_trades_unless_entry_day_is_ticked_too():
    window = schedule.expiry_window(weekly(7), FOMC_WED)
    later = ahead("FOMC", window)  # no FOMC after the 28th
    only_dte = weekly(7, blackout_dte_sets=("FOMC",))
    assert not schedule.is_blackout(only_dte, FOMC_WED, {"FOMC": True}, later)
    both = replace(only_dte, blackout_date_sets=("FOMC",))
    assert schedule.blackout_lists_hit(both, FOMC_WED, {"FOMC": True}, later) == ["FOMC"]


def test_a_market_holiday_before_expiration_is_named():
    friday = date(2026, 11, 20)
    bot = weekly(7, blackout_dte_sets=("HOLIDAY",))
    window = schedule.expiry_window(bot, friday)
    thanksgiving = ahead("HOLIDAY", window, {date(2026, 11, 26)})
    hit = schedule.blackout_lists_hit(bot, friday, {"HOLIDAY": False}, thanksgiving)
    assert hit == ["Market holiday on 2026-11-26, before expiration"]
    # A holiday is never an entry-day check: one saved there counts as before expiration
    old = weekly(7, blackout_date_sets=("HOLIDAY",))
    assert schedule.blackout_entry_lists(old) == ()
    assert schedule.blackout_expiry_lists(old) == ("HOLIDAY",)
    assert schedule.blackout_lists_hit(old, friday, {"HOLIDAY": False}, thanksgiving) == hit


def test_before_expiration_does_nothing_while_blackout_days_is_off():
    bot = replace(weekly(7, blackout_dte_sets=("FOMC",)), use_blackout_days=False)
    window = schedule.expiry_window(bot, MON)
    assert schedule.blackout_expiry_lists(bot) == ()
    assert not schedule.is_blackout(bot, MON, {}, ahead("FOMC", window, {FOMC_WED}))
    assert schedule.unavailable_blackout_lists(bot, MON, {}, {"FOMC": {}}) == []


def test_a_day_before_expiration_that_cant_be_told_fails_closed():
    bot = weekly(7, blackout_dte_sets=("FOMC",))
    window = schedule.expiry_window(bot, MON)
    unknown = {"FOMC": {d: (None if d == window[-1] else False) for d in window}}
    assert schedule.unavailable_blackout_lists(bot, MON, {"FOMC": False}, unknown) == ["FOMC"]
    assert schedule.unavailable_blackout_lists(bot, MON, {"FOMC": False}, {}) == ["FOMC"]
    told = ahead("FOMC", window)
    assert schedule.unavailable_blackout_lists(bot, MON, {"FOMC": None}, told) == ["FOMC"]
    assert schedule.unavailable_blackout_lists(bot, MON, {"FOMC": False}, told) == []
    zero_dte = weekly(0, blackout_dte_sets=("FOMC",))  # nothing after the entry day
    assert schedule.unavailable_blackout_lists(zero_dte, MON, {"FOMC": False}) == []


def test_the_lists_a_bot_uses_include_before_expiration():
    bot = weekly(7, blackout_date_sets=("CPI",), blackout_dte_sets=("HOLIDAY", "FOMC"))
    assert schedule.lists_in_use(bot) == ("CPI", "FOMC", "HOLIDAY")
    assert schedule.blackout_entry_lists(bot) == ("CPI",)
    assert schedule.blackout_expiry_lists(bot) == ("FOMC", "HOLIDAY")
