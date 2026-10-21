"""Bots slice 20: a day's schedule per bot (Tracker2's automation_bp.api_schedule and
_run_outcome)."""

from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from types import SimpleNamespace

from optionsjourney.bots import day_plan, schedule
from optionsjourney.bots.definition import EntryTimeDef, iron_condor_skeleton

DAY = date(2026, 10, 14)  # a Wednesday
CLOSE = time(16, 0)


def bot(id=1, name="SPX IC", times=("10:00",), **kw):
    base = replace(
        iron_condor_skeleton(name), id=id, account_number="5WX01234", enabled=True, contracts=2,
        entry_times=tuple(EntryTimeDef(t) for t in times),
    )  # fmt: skip
    return replace(base, **kw)


def run(bot_id, hhmm, status, reason="", id=1, key=None, parent=None):
    return SimpleNamespace(
        id=id, bot_id=bot_id, status=status, reason=reason, parent_run_id=parent,
        key=key or schedule.entry_key(bot_id, hhmm, DAY), entry_time=hhmm,
    )  # fmt: skip


def plan(bots, runs=(), at="09:00", day=DAY, close=CLOSE, **kw):
    now = datetime.combine(DAY, time.fromisoformat(at))
    return day_plan.plan(bots, list(runs), day, now, close, **kw)


def states(p):
    return [(r.time, r.state, r.note) for r in p.rows]


def test_a_time_still_to_come_is_pending_and_one_past_with_no_run_is_missed():
    p = plan([bot(times=("09:45", "10:30"))], at="10:00")
    assert states(p) == [("09:45", "missed", "no run recorded"), ("10:30", "pending", None)]
    assert p.counts["pending"] == 1 and p.counts["missed"] == 1
    assert p.rows[1].size == "2 contracts" and p.rows[1].can_skip
    assert not p.rows[0].can_skip
    assert (p.rows[1].underlying, p.rows[1].account_number) == ("SPX", "5WX01234")


def test_a_run_says_how_the_time_went():
    runs = [run(1, "09:31", "filled", id=1), run(1, "09:32", "running", id=2),
            run(1, "09:33", "missed", id=3), run(1, "09:34", "skipped", "VIX 31; above max 30", 4),
            run(1, "09:35", "error", "Refused", 5), run(1, "09:36", "preview", id=6)]  # fmt: skip
    p = plan([bot(times=("09:31", "09:32", "09:33", "09:34", "09:35", "09:36"))], runs, "12:00")
    assert states(p) == [
        ("09:31", "done", "filled"), ("09:32", "running", "working"),
        ("09:33", "unfilled", "not filled"), ("09:34", "skipped", "skipped · VIX 31; above max 30"),
        ("09:35", "error", "error · Refused"), ("09:36", "done", "preview"),
    ]  # fmt: skip
    assert p.rows[0].run_id == 1


def test_only_a_root_run_counts_not_its_tranche():
    child = run(1, "10:00", "missed", id=9, key="bot-1-1000-20261014-t2", parent=1)
    p = plan([bot()], [run(1, "10:00", "filled", id=1), child], "11:00")
    assert states(p) == [("10:00", "done", "filled")]


def test_the_not_trading_count_says_how_many_are_live():
    p = plan([bot(1, live=True), bot(2, live=False), bot(3, live=False)], freeze=True)
    assert p.counts["blocked"] == 3 and p.counts["blocked_live"] == 1


def test_freeze_blocks_today_but_only_notes_another_day():
    p = plan([bot()], freeze=True)
    assert p.rows == [] and p.counts["blocked"] == 1
    assert "Freeze is on" in " ".join(p.notes)
    later = plan([bot()], day=date(2026, 10, 15), freeze=True)
    assert states(later) == [("10:00", "pending", None)]
    assert any("Freeze is on right now" in n for n in later.notes)
    assert p.warnings and later.warnings  # the drawer shows both as warnings


def test_no_new_trades_blocks_today_too():
    assert plan([bot()], no_new_trades=True).counts["blocked"] == 1


def test_a_closed_market_a_blackout_and_the_cut_off_block():
    assert plan([bot()], close=None).counts["blocked"] == 1
    assert plan([bot()], close=None).market_closed
    dark = bot(use_blackout_days=True, blackout_days=("2026-10-14",))
    assert plan([dark]).counts["blocked"] == 1
    early = plan([bot(times=("12:59", "10:00"))], close=time(13, 0))
    assert states(early) == [("10:00", "pending", None)]
    assert early.counts["blocked"] == 1
    assert any("Early close at 13:00" in n for n in early.notes)


def test_a_blackout_list_that_cant_tell_blocks_and_says_so():
    fomc = bot(use_blackout_days=True, blackout_date_sets=("FOMC",))
    p = plan([fomc], lists={"FOMC": None})
    assert p.counts["blocked"] == 1
    assert any("FOMC" in n for n in p.notes)


def test_a_list_day_before_expiration_blocks_and_one_that_cant_tell_says_so():
    """#207: a Before expiration list looks at the days the trade would be open."""
    legs = tuple(replace(leg, dte=7) for leg in bot().legs)
    weekly = bot(legs=legs, use_blackout_days=True, blackout_dte_sets=("FOMC",))
    window = schedule.expiry_window(weekly, DAY)
    fomc = {"FOMC": {d: d == date(2026, 10, 20) for d in window}}
    assert plan([weekly], lists={"FOMC": False}, ahead=fomc).counts["blocked"] == 1
    clear = {"FOMC": {d: False for d in window}}
    assert plan([weekly], lists={"FOMC": False}, ahead=clear).counts["blocked"] == 0
    unknown = {"FOMC": {d: None for d in window}}
    p = plan([weekly], lists={"FOMC": False}, ahead=unknown)
    assert p.counts["blocked"] == 1 and any("FOMC" in n for n in p.warnings)


def test_an_off_schedule_day_is_left_out():
    weekly = bot(frequency="weekly", entry_days=(1,))  # Mondays only
    p = plan([weekly])
    assert p.rows == [] and p.counts["blocked"] == 0


def test_a_stopped_clock_marks_pending_rows_not_scheduled():
    p = plan([bot(times=("09:45", "10:30"))], [run(1, "09:45", "filled")], "10:00",
             clock_ok=False)  # fmt: skip
    assert states(p) == [
        ("09:45", "done", "filled"), ("10:30", "unscheduled", "the clock has stopped: won't fire"),
    ]  # fmt: skip
    assert not p.rows[1].can_skip
    tomorrow = plan([bot()], day=date(2026, 10, 15), clock_ok=False)
    assert states(tomorrow) == [("10:00", "pending", None)]


def test_a_floating_window_watches_with_its_last_check():
    floater = bot(use_floating_entry=True, floating_start="10:00", floating_end="11:00")
    last = {1: ("10:31", "VIX 31.2; above max 30")}
    p = plan([floater], at="10:32", floating_last=last.get)
    assert states(p) == [("10:00–11:00", "watching", "last check 10:31 · VIX 31.2; above max 30")]
    assert p.rows[0].floating and p.rows[0].can_skip
    assert states(plan([floater], at="10:01"))[0][2] == "checking each minute"
    assert states(plan([floater], at="09:00"))[0][1] == "pending"
    assert states(plan([floater], at="11:30"))[0][1:] == ("missed", "no run recorded")


def test_a_floating_window_reads_its_newest_minute_and_retries_a_miss():
    floater = bot(use_floating_entry=True, floating_start="10:00", floating_end="11:00")
    tries = [run(1, "10:05", "missed", id=3, key="bot-1-float-20261014-1005"),
             run(1, "10:01", "error", id=2, key="bot-1-float-20261014-1001")]  # fmt: skip
    p = plan([floater], tries, at="10:20")
    assert states(p) == [("10:00–11:00", "unfilled", "not filled · retrying")]
    assert p.rows[0].run_id == 3


def test_a_past_day_has_no_pending_and_a_future_day_no_missed():
    past = plan([bot()], day=date(2026, 10, 13))
    assert states(past) == [("10:00", "missed", "no run recorded")]
    future = plan([bot()], day=date(2026, 10, 15))
    assert states(future) == [("10:00", "pending", None)] and future.rows[0].can_skip
    assert not past.rows[0].can_skip  # Tracker2: today or a later day can skip
    assert any("Scheduled, not guaranteed" in n for n in future.notes)


def test_rows_sort_by_time_then_name_and_dollar_sizes_read_as_dollars():
    a = bot(id=1, name="Zed", times=("10:00",))
    b = bot(id=2, name="Alpha", times=("10:00", "09:40"), sizing="dollars",
            max_per_entry_usd=Decimal("500"))  # fmt: skip
    p = plan([a, b])
    assert [(r.time, r.bot_name) for r in p.rows] == [
        ("09:40", "Alpha"), ("10:00", "Alpha"), ("10:00", "Zed"),
    ]  # fmt: skip
    assert p.rows[0].size == "$500 of buying power an entry"


def test_a_paused_entry_time_isnt_listed():
    paused = bot(entry_times=(EntryTimeDef("10:00", enabled=False), EntryTimeDef("11:00")))
    assert [r.time for r in plan([paused]).rows] == ["11:00"]


def test_a_time_within_the_clocks_grace_is_still_pending():
    assert states(plan([bot(times=("09:45",))], at="09:47"))[0][1] == "pending"
    assert states(plan([bot(times=("09:45",))], at="09:48:01"))[0][1] == "missed"


def test_a_future_monthly_bot_shows_only_on_its_day():
    monthly = bot(frequency="monthly", monthly_week=3, monthly_weekday=4)  # 3rd Thursday
    assert states(plan([monthly], day=date(2026, 10, 15))) == [("10:00", "pending", None)]
    fourth = plan([monthly], day=date(2026, 10, 22))
    assert fourth.rows == [] and fourth.counts["blocked"] == 0


def test_a_future_specific_dates_bot_shows_on_a_typed_or_listed_day():
    typed = bot(frequency="specific_dates", specific_dates=("2026-10-16",))
    assert states(plan([typed], day=date(2026, 10, 16))) == [("10:00", "pending", None)]
    assert plan([typed], day=date(2026, 10, 19)).rows == []
    listed = bot(frequency="specific_dates", specific_date_sets=("CPI",))
    assert plan([listed], day=date(2026, 10, 16), lists={"CPI": True}).rows
    assert not plan([listed], day=date(2026, 10, 16), lists={"CPI": None}).rows


def test_a_future_half_day_blocks_times_past_its_cut_off():
    half = plan([bot(times=("10:00", "12:59", "13:30"))], day=date(2026, 11, 27), close=time(13, 0))
    assert states(half) == [("10:00", "pending", None)]
    assert half.counts["blocked"] == 2
    assert any("Early close at 13:00" in n and "12:58" in n for n in half.notes)
