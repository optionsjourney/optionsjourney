"""Bots slice 21: one trade's own timed close and watch rules, with the fake broker
(Tracker2's timed_close_overrides.py and watch_overrides.py)."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.core import sessions
from optionsjourney.db.models import BotExit, BotRun, BotRunTimedClose
from optionsjourney.services import (
    bot_entry,
    bot_exits,
    bot_store,
    bot_timed,
    bot_trade_settings,
    bot_watch,
)
from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make

D = Decimal
DAY = date(2026, 10, 13)


def ny(hhmm, day=DAY, seconds=0):
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hours + 4, minutes, seconds, tzinfo=UTC)


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty],
        FakeHub(),
        FakeChain(),
        notify=lambda e: None,
        sleep=lambda s: None,
        now=lambda: OPEN_TIME,
        background=lambda fn, *args: fn(*args),
    )
    d.tasty = tasty
    d.seen = []
    d.notify = d.seen.append
    bot_watch.reset()
    yield d
    bot_exits.FIRING.clear()
    bot_watch.reset()
    sessions.use_fetched_calendar([], {})


def trade(deps, factory, **changes):
    """A live iron condor bot's filled trade (09:47); `changes` are set on the bot."""
    bot_id = make(factory, live=True)
    if changes:
        bot = bot_store.load(factory, bot_id)
        _, problems = bot_store.save(factory, replace(bot, **changes), OPEN_TIME)
        assert problems == {}
    return bot_entry.fire(deps, bot_id, "09:47")


def at(deps, hhmm, seconds=0):
    deps.now = lambda: ny(hhmm, seconds=seconds)


def rows(factory, kind=None):
    with factory() as session:
        query = select(BotExit).order_by(BotExit.id)
        if kind:
            query = query.where(BotExit.kind == kind)
        return list(session.scalars(query))


def quotes(deps, factory, run_id, when, mids=None, deltas=None):
    with factory() as session:
        legs = list(session.get(BotRun, run_id).legs)
    for leg in legs:
        default = "0.60" if leg["side"] == "sell" else "0.05"
        m = D((mids or {}).get(leg["strike"], default))
        deps.hub.set(leg["symbol"], max(m - D("0.05"), D(0)), m + D("0.05"), when)
        if deltas and leg["strike"] in deltas:
            deps.hub.set_delta(leg["symbol"], deltas[leg["strike"]], when)


def own_timed(factory):
    with factory() as session:
        return session.scalar(select(BotRunTimedClose))


# --- The timed close -------------------------------------------------------------------


def test_a_trades_own_timed_close_fires_when_the_bot_has_none(deps, factory):
    """timed_close.effective_settings: the run's own row wins; the pass reads it."""
    run_id = trade(deps, factory)
    at(deps, "10:00")
    saved = bot_trade_settings.save_timed(deps, run_id, "11:00", "0")
    assert saved.ok and saved.confirm is None, saved.message
    assert (own_timed(factory).close_time, own_timed(factory).close_dte) == ("11:00", 0)
    at(deps, "10:59")
    quotes(deps, factory, run_id, ny("10:59"))
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed") == []
    at(deps, "11:00")
    quotes(deps, factory, run_id, ny("11:00"))
    bot_timed.timed_pass(deps)
    assert sorted(r.group_position for r in rows(factory, "timed")) == [0, 1]
    assert "11:00, 0 DTE or fewer" in rows(factory, "timed")[0].log[0]["event"]


def test_a_blank_time_switches_the_bots_timed_close_off_for_the_trade(deps, factory):
    run_id = trade(deps, factory, close_time="15:45")
    at(deps, "10:00")
    assert bot_trade_settings.save_timed(deps, run_id, "", "").ok
    at(deps, "15:45")
    quotes(deps, factory, run_id, ny("15:45"))
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed") == []
    at(deps, "16:01")
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed") == []  # off: never "missed" either
    assert bot_trade_settings.revert_timed(deps, run_id).ok
    assert own_timed(factory) is None


def test_saving_a_timed_close_due_now_asks_first_and_saves_nothing(deps, factory):
    """timed_close_overrides.due_now: the route confirms before the next check closes it."""
    run_id = trade(deps, factory)
    at(deps, "11:05")
    asked = bot_trade_settings.save_timed(deps, run_id, "11:00", "0")
    assert not asked.ok and asked.confirm and "the next check" in asked.confirm
    assert own_timed(factory) is None
    done = bot_trade_settings.save_timed(deps, run_id, "11:00", "0", confirmed=True)
    assert done.ok and own_timed(factory).close_time == "11:00"


def test_a_bad_timed_close_is_refused_and_saves_nothing(deps, factory):
    run_id = trade(deps, factory)
    at(deps, "10:00")
    for typed in (("16:30", "0"), ("15:45", "-1"), ("", "2"), ("15:45", "x")):
        assert not bot_trade_settings.save_timed(deps, run_id, *typed).ok
    assert own_timed(factory) is None


def test_the_timed_close_view_says_what_is_in_force_and_when_it_next_runs(deps, factory):
    run_id = trade(deps, factory, close_time="15:45")
    at(deps, "10:00")
    bot = bot_store.load(factory, _bot_id(factory))
    view = bot_trade_settings.timed_view(factory, run_id, bot, deps.now())
    assert (view.time, view.dte, view.source) == ("15:45", 0, "bot")
    assert view.next_close == "today at 15:45" and view.editable
    assert bot_trade_settings.save_timed(deps, run_id, "14:00", "0").ok
    view = bot_trade_settings.timed_view(factory, run_id, bot, deps.now())
    assert (view.time, view.source, view.next_close) == ("14:00", "trade", "today at 14:00")


def _bot_id(factory):
    from optionsjourney.db.models import Bot

    with factory() as session:
        return session.scalar(select(Bot.id))


# --- The watch rules -----------------------------------------------------------------------

RATIO_20 = {"use_sl_ratio_exit": "on", "sl_ratio_above": "20"}


def watch_rows(factory):
    return rows(factory, "watch")


def passes(deps, factory, run_id, hhmm, *seconds, mids=None):
    for s in seconds:
        at(deps, hhmm, s)
        quotes(deps, factory, run_id, ny(hhmm, seconds=s), mids=mids)
        bot_watch.watch_pass(deps)


def test_the_trades_watch_rules_replace_the_bots(deps, factory):
    """watch_loop.config_for with an override: the trade's own S/L ratio rule fires on a
    trade whose bot has none."""
    run_id = trade(deps, factory)
    at(deps, "11:00")
    quotes(deps, factory, run_id, ny("11:00"))  # S/L ratio 1.20 / 0.10 = 12
    saved = bot_trade_settings.save_watch(deps, run_id, RATIO_20)
    assert saved.ok and saved.confirm is None, (saved.message, saved.confirm)
    passes(deps, factory, run_id, "11:00", 2, 4)
    assert watch_rows(factory) == []
    shorts = {"5910": "1.10", "5690": "1.10"}  # 2.20 / 0.10 = 22
    passes(deps, factory, run_id, "11:00", 6, 8, 36, mids=shorts)  # #201: held 30 s
    assert {r.group_position for r in watch_rows(factory)} == {0, 1}


def test_switching_the_rules_off_for_the_trade_stops_the_bots(deps, factory):
    run_id = trade(deps, factory, use_sl_ratio_exit=True, sl_ratio_above=D("10"))
    at(deps, "10:00")  # no quotes yet: the trade's rules (none) need no check
    saved = bot_trade_settings.save_watch(deps, run_id, {})
    assert saved.ok, saved.message
    passes(deps, factory, run_id, "11:00", 0, 2, 30, 32)  # the bot's rule would have fired at 12
    assert watch_rows(factory) == []


def test_a_rule_that_would_fire_at_once_or_cant_be_checked_asks_first(deps, factory):
    """watch_overrides.probe_save's breach and unverified lists; nothing is saved."""
    run_id = trade(deps, factory)
    at(deps, "11:00")
    stale = bot_trade_settings.save_watch(deps, run_id, RATIO_20)  # no quotes yet
    assert not stale.ok and "Can't be checked" in stale.confirm
    quotes(deps, factory, run_id, ny("11:00"))
    crossed = {"use_sl_ratio_exit": "on", "sl_ratio_above": "10"}
    asked = bot_trade_settings.save_watch(deps, run_id, crossed)
    assert not asked.ok and "Already crossed" in asked.confirm and "S/L ratio 12" in asked.confirm
    assert bot_trade_settings.all_watch(factory) == {}
    assert bot_trade_settings.save_watch(deps, run_id, crossed, confirmed=True).ok
    assert deps.tasty.calls.count(("cancel",)) == 0 and side_closes(deps) == []  # nothing sent


def side_closes(deps):
    return [c for c in deps.tasty.calls if c[0] == "place" and len(c[2]["legs"]) == 2
            and c[2]["legs"][0]["action"].endswith("Close")]  # fmt: skip


def test_watch_rules_are_refused_once_one_fired_or_when_bad(deps, factory):
    run_id = trade(deps, factory, use_sl_ratio_exit=True, sl_ratio_above=D("10"))
    at(deps, "11:00")
    bad = bot_trade_settings.save_watch(deps, run_id, {"use_delta_exit": "on"})
    assert not bad.ok and "nothing is set" in bad.message
    assert not bot_trade_settings.save_watch(deps, run_id, {"sl_ratio_above": "x"}).ok
    passes(deps, factory, run_id, "11:00", 0, 2, 30)  # the bot's rule fires (#201: 30 s)
    assert watch_rows(factory)
    refused = bot_trade_settings.save_watch(deps, run_id, RATIO_20, confirmed=True)
    assert not refused.ok and "already fired" in refused.message


def test_reverting_probes_the_bots_rules(deps, factory):
    run_id = trade(deps, factory, use_sl_ratio_exit=True, sl_ratio_above=D("10"))
    at(deps, "10:00")
    assert bot_trade_settings.save_watch(deps, run_id, {}).ok
    at(deps, "11:00")
    quotes(deps, factory, run_id, ny("11:00"))
    asked = bot_trade_settings.revert_watch(deps, run_id)
    assert not asked.ok and "Already crossed" in asked.confirm
    assert bot_trade_settings.revert_watch(deps, run_id, confirmed=True).ok
    assert bot_trade_settings.all_watch(factory) == {}


def test_a_trades_leg_delta_is_its_own_leg_by_position(deps, factory):
    run_id = trade(deps, factory)
    with factory() as session:
        legs = list(session.get(BotRun, run_id).legs)
    short_call = next(leg for leg in legs if leg["side"] == "sell" and leg["right"] == "call")
    form = {"use_delta_exit": "on", f"leg-{short_call['position']}-delta-below": "-40"}
    at(deps, "11:00")
    quotes(deps, factory, run_id, ny("11:00"), deltas={leg["strike"]: "0.10" for leg in legs})
    assert bot_trade_settings.save_watch(deps, run_id, form).ok
    tested = {short_call["strike"]: "0.45"}  # the short call at -45 (position-signed)
    for s in (2, 4, 32):  # #201: held 30 s
        at(deps, "11:00", s)
        quotes(deps, factory, run_id, ny("11:00", seconds=s), deltas=tested)
        bot_watch.watch_pass(deps)
    assert watch_rows(factory)
    assert any("Leg" in (r.log[0].get("rule") or "") for r in watch_rows(factory))
