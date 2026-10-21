from datetime import date, datetime, time
from decimal import Decimal

from optionsjourney.bots import timing
from optionsjourney.bots.definition import TimeActionDef

D = Decimal
CALL = "SPXW  261013C05910000"


def at(day, hhmm):
    """A naive New York wall-clock moment."""
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(hhmm))


def test_effective_time_on_a_half_day_is_fifteen_minutes_before_the_close():
    assert timing.effective_time(time(15, 45), time(16, 0)) == time(15, 45)
    # 2026-11-27 closes at 13:00: a 15:45 close fires at 12:45
    assert timing.effective_time(time(15, 45), time(13, 0)) == time(12, 45)
    assert timing.effective_time(time(12, 50), time(13, 0)) == time(12, 50)
    assert timing.effective_time(time(13, 0), time(13, 0)) == time(12, 45)


def test_dte_is_calendar_days_to_the_nearest_expiration():
    symbols = [CALL, "SPXW  261016P05690000"]
    assert timing.nearest_expiration(symbols) == date(2026, 10, 13)
    assert timing.nearest_expiration(["not an option"]) is None


def test_timed_close_is_due_from_its_time_on_a_session_day():
    expiry = date(2026, 10, 13)  # a Tuesday
    assert timing.timed_close_due("15:45", 0, expiry, at("2026-10-13", "15:44")) is None
    assert timing.timed_close_due("15:45", 0, expiry, at("2026-10-13", "15:45")) == "due"
    assert timing.timed_close_due("15:45", 0, expiry, at("2026-10-13", "16:00")) == "over"
    # one day out: only with a DTE of 1 or more
    assert timing.timed_close_due("15:45", 0, expiry, at("2026-10-12", "15:50")) is None
    assert timing.timed_close_due("15:45", 1, expiry, at("2026-10-12", "15:50")) == "due"
    # a weekend never fires
    assert timing.timed_close_due("15:45", 5, expiry, at("2026-10-10", "15:50")) is None


def test_a_timed_close_on_a_half_day():
    expiry = date(2026, 11, 27)
    assert timing.timed_close_due("15:45", 0, expiry, at("2026-11-27", "12:44")) is None
    assert timing.timed_close_due("15:45", 0, expiry, at("2026-11-27", "12:45")) == "due"
    assert timing.timed_close_due("15:45", 0, expiry, at("2026-11-27", "13:00")) == "over"


def action(kind="dte", days=0, hhmm="14:00"):
    return TimeActionDef(kind, days, hhmm, target_pct=D("50"))


def test_a_dte_row_steps_back_over_a_weekend_and_never_before_the_fill():
    monday = date(2026, 10, 19)
    # 1 DTE on a Monday expiry is the Friday before, not the Sunday
    assert timing.action_due_day(action(days=1), monday, date(2026, 10, 14)) == date(2026, 10, 16)
    # never before the fill
    assert timing.action_due_day(action(days=5), monday, date(2026, 10, 16)) == date(2026, 10, 16)
    assert timing.action_due_day(action(), None, date(2026, 10, 16)) is None


def test_dit_counts_sessions_after_the_fill():
    friday = date(2026, 10, 16)
    assert timing.action_due_day(action("dit", 0), None, friday) == friday
    assert timing.action_due_day(action("dit", 1), None, friday) == date(2026, 10, 19)
    # Thanksgiving (Thursday 2026-11-26) is skipped
    wednesday = date(2026, 11, 25)
    assert timing.action_due_day(action("dit", 1), None, wednesday) == date(2026, 11, 27)


def test_a_row_is_due_from_its_time_and_never_outside_the_session():
    expiry, fill = date(2026, 10, 13), date(2026, 10, 13)
    row = action(hhmm="14:00")
    assert timing.action_due(row, expiry, fill, at("2026-10-13", "13:59")) == (False, False)
    assert timing.action_due(row, expiry, fill, at("2026-10-13", "14:00")) == (True, False)
    assert timing.action_due(row, expiry, fill, at("2026-10-13", "16:00")) == (False, False)


def test_a_row_on_a_later_day_is_late_and_due_from_the_open():
    fill, expiry = date(2026, 10, 13), date(2026, 10, 16)
    row = action("dit", 0, "15:00")
    assert timing.action_due(row, expiry, fill, at("2026-10-14", "09:29")) == (False, True)
    assert timing.action_due(row, expiry, fill, at("2026-10-14", "09:30")) == (True, True)


def test_side_state():
    assert timing.side_state([]) == "open"
    assert timing.side_state([("stop", "working"), ("target", "armed")]) == "open"
    assert timing.side_state([("stop", "cancelled"), ("target", "firing")]) == "busy"
    assert timing.side_state([("stop", "placing")]) == "busy"
    assert timing.side_state([("stop", "cancelled"), ("timed", "firing")]) == "busy"
    for closed in (("target", "filled"), ("timed", "filled"), ("timed", "expired"),
                   ("stop", "filled"), ("stop", "expired")):  # fmt: skip
        assert timing.side_state([closed, ("target", "firing")]) == "closed", closed
    assert timing.side_state([("timed", "failed"), ("stop", "working")]) == "open"


def test_a_filled_or_expired_close_closes_the_side_and_a_reduced_one_does_not():
    # Slice 6: a close you asked for. "reduced": it filled, part of the side is still open.
    assert timing.side_state([("close", "filled"), ("stop", "cancelled")]) == "closed"
    assert timing.side_state([("close", "expired")]) == "closed"
    assert timing.side_state([("close", "reduced"), ("stop", "working")]) == "open"
    assert timing.side_state([("close", "firing"), ("stop", "cancelled")]) == "busy"


# --- Bots slice 21: the next timed close (timed_close.next_close) -------------------------


def test_the_next_close_is_today_at_its_time_and_due_once_it_passed():
    today = date(2026, 10, 13)
    later = timing.next_close("15:45", 0, today, at("2026-10-13", "10:00"), fired_today=False)
    assert (later.day, later.at, later.due_now, later.dte) == (today, "15:45", False, 0)
    due = timing.next_close("11:00", 0, today, at("2026-10-13", "11:05"), fired_today=False)
    assert due.due_now and due.at == "11:00"


def test_the_next_close_skips_today_once_it_fired_or_the_session_closed():
    expiry = date(2026, 10, 16)  # a Friday
    fired = timing.next_close("15:45", 3, expiry, at("2026-10-13", "15:50"), fired_today=True)
    assert fired.day == date(2026, 10, 14) and not fired.due_now
    after = timing.next_close("15:45", 3, expiry, at("2026-10-13", "16:30"), fired_today=False)
    assert after.day == date(2026, 10, 14)
    # nothing is left before the nearest expiration
    assert timing.next_close("15:45", 0, date(2026, 10, 13), at("2026-10-13", "16:30"),
                             fired_today=False) is None  # fmt: skip


def test_the_next_close_waits_for_its_dte_and_steps_over_a_weekend():
    expiry = date(2026, 10, 26)  # a Monday
    found = timing.next_close("15:45", 2, expiry, at("2026-10-13", "10:00"), fired_today=False)
    # 2 calendar days before is Saturday: the first session from then is the Monday
    assert (found.day, found.dte) == (date(2026, 10, 26), 0)
    one = timing.next_close("15:45", 3, expiry, at("2026-10-13", "10:00"), fired_today=False)
    assert (one.day, one.dte) == (date(2026, 10, 23), 3)


def test_the_next_close_on_a_half_day_is_pulled_in():
    expiry = date(2026, 11, 27)
    found = timing.next_close("15:45", 0, expiry, at("2026-11-27", "09:40"), fired_today=False)
    assert (found.at, found.due_now) == ("12:45", False)
    assert timing.next_close("", 0, expiry, at("2026-11-27", "09:40"), False) is None
