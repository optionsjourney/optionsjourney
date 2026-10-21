from datetime import date

from optionsjourney.bots.expirations import resolve, resolve_legs

TODAY = date(2026, 10, 13)  # a Tuesday
LISTED = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-16", "2026-10-19", "2026-10-20",
          "2026-10-23", "2026-10-27"]  # fmt: skip


def test_exact_takes_only_the_date_that_many_days_away():
    """chain.resolve_expiration with exact=True."""
    assert resolve(LISTED, 0, TODAY, True) == (date(2026, 10, 13), None)
    assert resolve(LISTED, 7, TODAY, True) == (date(2026, 10, 20), None)
    found, reason = resolve(LISTED, 2, TODAY, True)
    assert found is None
    assert reason == "No expiration at exactly 2 DTE (the nearest is 2026-10-14, 1 DTE)."


def test_not_exact_takes_the_first_at_or_after_and_never_rounds_down():
    """resolve_expiration with exact=False: never earlier than the DTE (a 1 DTE leg on a
    Friday must not trade 0DTE), however far the next one is."""
    assert resolve(LISTED, 2, TODAY, False) == (date(2026, 10, 16), None)
    assert resolve(LISTED, 8, TODAY, False) == (date(2026, 10, 23), None)
    found, reason = resolve(LISTED, 30, TODAY, False)
    assert found is None
    assert reason == "No expiration at 30 DTE or later (the last listed is 2026-10-27, 14 DTE)."


def test_expired_dates_are_never_taken():
    """Expired dates can linger in a cached chain; never trade one."""
    found, reason = resolve(["2026-10-12"], 0, TODAY, False)
    assert found is None and reason == "No expiration listed from today on."
    assert resolve([], 0, TODAY, True) == (None, "No expirations listed.")
    assert resolve(["junk", "2026-10-13"], 0, TODAY, True) == (date(2026, 10, 13), None)


def test_resolve_legs_resolves_each_distinct_dte():
    found, problems, notes = resolve_legs([7, 0, 7, 14], LISTED, TODAY, True)
    assert found == {0: date(2026, 10, 13), 7: date(2026, 10, 20), 14: date(2026, 10, 27)}
    assert problems == [] and notes == []


def test_two_dtes_on_one_date_move_the_later_to_the_next_listed():
    """resolve_leg_expirations: without exact DTE, 2 and 3 DTE both land on Friday; a
    calendar on one expiration would buy and sell the same contract."""
    found, problems, notes = resolve_legs([2, 3], LISTED, TODAY, False)
    assert found == {2: date(2026, 10, 16), 3: date(2026, 10, 19)}
    assert problems == []
    assert notes == [
        "3 DTE resolved to 2026-10-16, the same as 2 DTE; moved to the next expiration, "
        "2026-10-19 (6 DTE)."
    ]


def test_a_collision_with_nothing_later_is_a_problem():
    found, problems, _ = resolve_legs([13, 14], ["2026-10-27"], TODAY, False)
    assert 14 not in found
    assert problems == [
        "14 DTE resolves to 2026-10-27, the same as 13 DTE, and no later expiration is listed."
    ]


def test_a_problem_for_one_dte_is_reported():
    found, problems, _ = resolve_legs([0, 2], LISTED, TODAY, True)
    assert found == {0: date(2026, 10, 13)}
    assert problems == ["No expiration at exactly 2 DTE (the nearest is 2026-10-14, 1 DTE)."]


def test_dte_0_is_always_exact():
    """Review 2: with exact off, a same-day leg never becomes tomorrow's."""
    found, reason = resolve(["2026-10-14"], 0, TODAY, False)
    assert found is None and reason.startswith("No expiration at exactly 0 DTE")
    found, problems, _ = resolve_legs([0], ["2026-10-14"], TODAY, False)
    assert found == {} and problems


def test_nothing_past_1100_dte_is_taken():
    """Review 1: the passes that watch held trades look back MAX_DTE + 10 days."""
    found, reason = resolve(["2029-11-11"], 1090, TODAY, False)  # 1125 DTE
    assert found is None
    assert reason == ("The first expiration at 1090 DTE or later is 2029-11-11, 1125 DTE: "
                      "more than 1100.")  # fmt: skip
    assert resolve(["2029-10-17"], 1090, TODAY, False) == (date(2029, 10, 17), None)  # 1100
    found, problems, _ = resolve_legs([1099, 1100], ["2029-10-17", "2029-10-19"], TODAY,
                                      False)  # fmt: skip
    assert 1100 not in found and "more than 1100 DTE" in problems[0]
