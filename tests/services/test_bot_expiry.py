"""Bots slice 16, question 3: American-style legs held into expiration day's last
minutes are alerted, once (services/bot_expiry.py)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import bot_entry, bot_exits, bot_expiry
from tests.services.bot_fakes import (
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeTasty,
    make,
    on_underlying,
)

D = Decimal
DAY = date(2026, 10, 13)


def ny(hhmm):
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime(DAY.year, DAY.month, DAY.day, hours + 4, minutes, tzinfo=UTC)


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: OPEN_TIME, background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    d.tasty = tasty
    d.seen = []
    d.notify = d.seen.append
    bot_expiry.reset()
    yield d
    bot_exits.FIRING.clear()
    bot_expiry.reset()


def entered(deps, factory, underlying="SPY", live=True):
    bot_id = make(factory, live=live, close_time="15:55", **on_underlying(deps, underlying))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        assert session.get(BotRun, run_id).status in ("filled", "preview")
    return run_id


def pass_at(deps, hhmm):
    deps.now = lambda: ny(hhmm)
    bot_expiry.warn_pass(deps)


def warnings(deps):
    return [e for e in deps.seen if "held into expiration" in e.title]


def test_an_spy_leg_held_into_expiry_is_warned_once(deps, factory):
    run_id = entered(deps, factory)
    deps.hub.set_last("SPY", "5685", ny("15:49"))
    pass_at(deps, "15:49")
    assert warnings(deps) == []  # not before 10 minutes from the close
    pass_at(deps, "15:50")
    (alert,) = warnings(deps)
    assert alert.title == "IC: URGENT: SPY options held into expiration"
    assert not alert.routine and alert.run_id == run_id
    assert alert.detail == (
        f"Run {run_id} still holds options that expire at today's close: sold SPY 5910 call "
        "(out of the money by 225.00), bought SPY 5985 call (out of the money by 300.00), "
        "sold SPY 5690 put (in the money by 5.00), bought SPY 5615 put (out of the money by "
        "70.00). Its timed close is at 15:55. SPY options are American-style: in the money "
        "at the close, a sold option is assigned (you'll hold shares) and a bought one is "
        "exercised. Close them, or check your broker."
    )
    assert "SPY" in deps.hub.subscribed
    pass_at(deps, "15:51")
    bot_expiry.reset()  # a restart: the run's log still says it was said
    pass_at(deps, "15:52")
    assert len(warnings(deps)) == 1
    with factory() as session:
        log = session.get(BotRun, run_id).chase_log
    assert log[-1]["event"] == "expiry_warned" and log[-1]["day"] == "2026-10-13"


def test_the_window_ends_five_minutes_after_the_close(deps, factory):
    entered(deps, factory)
    pass_at(deps, "16:05")
    assert warnings(deps) == []
    pass_at(deps, "16:04")
    assert len(warnings(deps)) == 1


def test_a_stocks_legs_are_warned_too(deps, factory):
    """The author's ruling: single stocks settle in shares like the ETFs."""
    entered(deps, factory, "AAPL")
    pass_at(deps, "15:50")
    (alert,) = warnings(deps)
    assert alert.title == "IC: URGENT: AAPL options held into expiration"
    assert "AAPL options are American-style" in alert.detail


def test_a_closed_group_and_an_unknown_price(deps, factory):
    run_id = entered(deps, factory)
    with factory() as session:
        stop = session.scalars(
            select(BotExit).where(
                BotExit.run_id == run_id, BotExit.group_position == 0, BotExit.kind == "stop"
            )  # fmt: skip
        ).one()
        stop.state = "filled"
        session.commit()
    pass_at(deps, "15:50")
    (alert,) = warnings(deps)
    assert "call" not in alert.detail
    assert "sold SPY 5690 put (price unknown), bought SPY 5615 put (price unknown)." in alert.detail


@pytest.mark.parametrize("underlying", ["SPX", "XSP"])
def test_cash_settled_underlyings_are_never_warned(deps, factory, underlying):
    entered(deps, factory, underlying)
    pass_at(deps, "15:50")
    assert warnings(deps) == []


def test_a_preview_or_closed_run_is_never_warned(deps, factory):
    bot_id = make(factory, live=False, close_time="15:55", **on_underlying(deps, "SPY"))
    preview = bot_entry.fire(deps, bot_id, None, manual=True)  # Preview now: check only
    with factory() as session:
        assert session.get(BotRun, preview).status == "preview"
    run_id = entered(deps, factory)
    with factory() as session:
        session.get(BotRun, run_id).status = "closed"
        session.commit()
    pass_at(deps, "15:50")
    assert warnings(deps) == []


def test_a_working_close_is_mentioned(deps, factory):
    run_id = entered(deps, factory)
    with factory() as session:
        for row in session.scalars(select(BotExit).where(BotExit.run_id == run_id)):
            if row.kind == "stop":
                row.state = "firing"
        session.commit()
    pass_at(deps, "15:50")
    (alert,) = warnings(deps)
    assert "A close is working on it." in alert.detail


def test_a_half_day_warns_ten_minutes_before_its_early_close(deps, factory):
    from optionsjourney.core import sessions

    sessions.use_fetched_calendar([date(2026, 1, 1)], {DAY: datetime(1, 1, 1, 13, 0).time()})
    try:
        entered(deps, factory)
        pass_at(deps, "12:49")
        assert warnings(deps) == []
        pass_at(deps, "12:50")
        assert len(warnings(deps)) == 1
    finally:
        sessions.use_fetched_calendar([], {})
