"""The demo tour's state machine: which step you are on and how it moves."""

from __future__ import annotations

from dataclasses import fields

from optionsjourney.demo import tour as tour_module
from optionsjourney.demo.tour import Tour, TourFacts


def facts(**kw) -> TourFacts:
    base = {f.name: (None if "id" in f.name else False) for f in fields(TourFacts)}
    base.update(kw)
    return TourFacts(**base)


def test_starts_at_step_one():
    tour = Tour()
    assert tour.current == 1
    assert tour.view(facts())["text"].startswith("Months of made-up trades")


def test_update_moves_on_when_the_steps_fact_is_true():
    tour = Tour()
    assert tour.update(facts(opened_automation=True)) == 2
    assert tour.current == 2


def test_reaching_a_step_already_done_moves_on():
    tour = Tour()
    tour.update(facts(opened_automation=True, condor_armed=True))
    assert tour.current == 3


def test_step_three_needs_both_the_stop_and_the_run_page():
    tour = Tour()
    tour.update(facts(opened_automation=True, condor_armed=True, condor_stopped=True))
    assert tour.current == 3
    tour.update(
        facts(
            opened_automation=True,
            condor_armed=True,
            condor_stopped=True,
            opened_condor_run=True,
        )
    )
    assert tour.current == 4


def test_never_goes_backwards():
    tour = Tour()
    tour.current = 4
    assert tour.update(facts()) is None
    assert tour.current == 4


def test_next_names_the_step_it_moves_from():
    tour = Tour()
    tour.current = 2
    tour.next(1)
    assert tour.current == 2
    tour.next(2)
    assert tour.current == 3


def test_skip_and_restart():
    tour = Tour()
    tour.current = 3
    tour.furthest = 3
    tour.skip()
    assert tour.view(facts())["skipped"]
    tour.restart(facts())
    assert tour.current == 1
    assert not tour.skipped
    assert tour.furthest == 3


def test_furthest_tracks_the_highest_step():
    tour = Tour()
    tour.update(
        facts(
            opened_automation=True,
            condor_armed=True,
            condor_stopped=True,
            opened_condor_run=True,
            spread_managed=True,
        )
    )
    assert tour.current == 5
    tour.restart(facts())
    assert tour.furthest == 5


def test_finished_after_step_seven():
    tour = Tour()
    everything = facts(
        opened_automation=True,
        condor_armed=True,
        condor_stopped=True,
        opened_condor_run=True,
        spread_managed=True,
        calendar_target_filled=True,
        watchlist_changed=True,
        own_bot_entered=True,
    )
    tour.update(everything)
    assert tour.current == 5  # the calendar's step waits to be seen, even when done
    tour.next(5)
    assert tour.update(everything) == 8
    view = tour.view(facts())
    assert tour.current == 8
    assert view["finished"]
    assert view["link"] == "https://optionsjourney.com/install"


_UP_TO_FOUR = {
    "opened_automation": True,
    "condor_armed": True,
    "condor_stopped": True,
    "opened_condor_run": True,
    "spread_managed": True,
}


def test_a_calendar_target_that_filled_earlier_still_gets_its_step():
    tour = Tour()
    tour.current = 4
    filled = facts(**_UP_TO_FOUR, calendar_target_filled=True)
    assert tour.update(filled) == 5
    assert tour.update(filled) is None  # stays until Next
    assert tour.view(filled)["text"].startswith("Your Weekly calendar reached its profit target")
    tour.next(5)
    assert tour.current == 6


def test_a_calendar_target_that_fills_while_you_watch_moves_on():
    tour = Tour()
    tour.current = 4
    assert tour.update(facts(**_UP_TO_FOUR)) == 5
    assert tour.view(facts(**_UP_TO_FOUR))["text"].startswith("Your Weekly calendar is near")
    assert tour.update(facts(**_UP_TO_FOUR, calendar_target_filled=True)) == 6


def test_a_filled_target_is_shown_even_after_the_close():
    tour = Tour()
    tour.current = 4
    filled = facts(**_UP_TO_FOUR, calendar_target_filled=True, day_over=True)
    tour.update(filled)
    assert tour.view(filled)["text"].startswith("Your Weekly calendar reached its profit target")


def test_step_three_links_the_condor_run():
    tour = Tour()
    tour.current = 3
    assert tour.view(facts(condor_run_id=12))["link"] == "/automation/runs/12"
    assert tour.view(facts())["link"] == "/automation"


def test_step_five_links_the_calendar_runs():
    tour = Tour()
    tour.current = 5
    assert tour.view(facts(calendar_bot_id=4))["link"] == "/automation/4/runs"
    assert tour.view(facts())["link"] == "/automation"


def test_after_the_close_the_box_points_at_a_new_day():
    tour = Tour()
    tour.current = 2
    view = tour.view(facts(day_over=True))
    assert view["text"].startswith("The demo's trading day is over")
    assert view["link"] is None
    assert view["day_over"]


def test_restart_holds_at_step_one_even_when_steps_are_done():
    tour = Tour()
    done = facts(opened_automation=True, condor_armed=True)
    tour.update(done)
    assert tour.current == 3
    tour.restart(done)
    assert tour.update(done) is None
    assert tour.current == 1
    assert tour.furthest == 3


def test_next_moves_past_a_held_step():
    tour = Tour()
    done = facts(opened_automation=True, condor_armed=True)
    tour.restart(done)
    tour.next(1)
    assert tour.update(done) is None  # step 2 is held too
    assert tour.current == 2
    tour.next(2)
    assert tour.current == 3


def test_a_held_step_does_not_stop_a_later_one_from_advancing():
    tour = Tour()
    tour.restart(facts(opened_automation=True))
    tour.next(1)
    assert tour.update(facts(opened_automation=True, condor_armed=True)) == 3


def test_step_two_and_five_words():
    from optionsjourney.demo.tour import STEPS

    assert STEPS[1].text == (
        "Open the SPX 0DTE condor (under Off) and arm it. It enters within a minute "
        "and a toast shows the trade."
    )
    assert STEPS[4].text == (
        "Your Weekly calendar is near its profit target. It takes about two minutes: "
        "watch it close and the alert arrive."
    )


def test_the_tour_points_at_the_seeded_condor():
    """tour.py stays light, so it can't import the seed's history: this keeps them equal."""
    from optionsjourney.demo import history  # noqa: PLC0415

    assert f'[data-bot-name="{history.CONDOR}"]' == tour_module._CONDOR


def test_counts_and_the_seats_use_the_tours_and_runners_numbers():
    from optionsjourney.demo import counts, seats  # noqa: PLC0415
    from optionsjourney.demo.__main__ import NEW_DAY  # noqa: PLC0415

    assert counts.FINISHED is tour_module.FINISHED == tour_module.TOTAL + 1
    assert seats.NEW_DAY == NEW_DAY
