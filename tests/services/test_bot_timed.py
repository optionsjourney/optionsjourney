"""Slice 4: the timed close and time actions, with the fake broker."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import TimeActionDef
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core import sessions
from optionsjourney.db.models import BotExit
from optionsjourney.services import bot_entry, bot_exits, bot_store, bot_timed, orders
from optionsjourney.trading.orders import plain
from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make

D = Decimal
DAY = date(2026, 10, 13)


def ny(hhmm, day=DAY):
    """A New York wall-clock time on the test day, as the UTC moment (EDT: UTC-4)."""
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hours + 4, minutes, tzinfo=UTC)


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
    yield d
    bot_exits.FIRING.clear()
    sessions.use_fetched_calendar([], {})


def timed_run(deps, factory, close_time="15:45", target_pct="50", **changes):
    """A live iron condor bot with a timed close, entered at 09:47; the run's id."""
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, target_pct=D(target_pct)) for g in bot.groups)
    bot = replace(bot, groups=groups, close_time=close_time, **changes)
    _, problems = bot_store.save(factory, bot, OPEN_TIME)
    assert problems == {}
    return bot_entry.fire(deps, bot_id, "09:47")


def rows(factory, kind=None, side=None):
    with factory() as session:
        query = select(BotExit).order_by(BotExit.id)
        if kind:
            query = query.where(BotExit.kind == kind)
        if side is not None:
            query = query.where(BotExit.group_position == side)
        return list(session.scalars(query))


def at(deps, hhmm, day=DAY):
    deps.now = lambda: ny(hhmm, day)


def fresh_quotes(deps, factory, hhmm, short_mid="0.60", long_mid="0.05"):
    """Fresh quotes on every side's two legs at `hhmm` (bid and ask 0.05 either side)."""
    for stop in rows(factory, "stop"):
        for symbol, mid in ((stop.short_symbol, short_mid), (stop.long_symbol, long_mid)):
            m = D(mid)
            deps.hub.set(symbol, max(m - D("0.05"), D(0)), m + D("0.05"), ny(hhmm))


def side_closes(deps):
    return [
        c[2]
        for c in deps.tasty.calls
        if c[0] == "place"
        and len(c[2]["legs"]) == 2
        and c[2]["legs"][0]["action"].endswith("Close")
    ]


def titles(deps):
    return [e.title for e in deps.seen]


def test_a_timed_close_fires_at_its_time_and_closes_each_side_from_the_mid(deps, factory):
    timed_run(deps, factory)
    at(deps, "15:44")
    fresh_quotes(deps, factory, "15:44")
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed") == [] and side_closes(deps) == []
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    timed = rows(factory, "timed")
    assert [(t.group_position, t.state) for t in timed] == [(0, "filled"), (1, "filled")]
    assert {s.state for s in rows(factory, "stop")} == {"cancelled"}
    assert {t.state for t in rows(factory, "target")} == {"cancelled"}
    closes = side_closes(deps)
    # short mid 0.60 less the wing's mid 0.05: 0.55 to pay, from the mid (no start offset)
    assert [(c["price"], c["price-effect"]) for c in closes] == [("0.55", "Debit")] * 2
    assert "IC: timed close filled" in titles(deps)


def test_a_timed_close_waits_while_the_target_is_closing_the_side(deps, factory):
    timed_run(deps, factory)
    call_target = rows(factory, "target", 0)[0]
    with factory() as session:
        session.get(BotExit, call_target.id).state = "firing"  # its close is running
        session.commit()
    bot_exits.FIRING.add(call_target.id)
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed", 0) == []  # waits; nothing recorded
    assert [t.state for t in rows(factory, "timed", 1)] == ["filled"]
    assert len(side_closes(deps)) == 1


def test_a_target_cannot_fire_while_a_timed_close_runs(deps, factory):
    timed_run(deps, factory)
    jobs = []
    deps.background = lambda fn, *args: jobs.append((fn, args))  # the closes haven't run yet
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert [t.state for t in rows(factory, "timed")] == ["firing", "firing"]
    call_target = rows(factory, "target", 0)[0]
    assert call_target.state == "cancelled"
    bot_exits.fire_target(deps, call_target.id)  # the watch can't fire it
    bot_timed.timed_pass(deps)  # nor a second timed close
    assert len(jobs) == 2 and side_closes(deps) == []
    for fn, args in jobs:
        fn(*args)
    assert len(side_closes(deps)) == 2


def test_a_timed_close_that_gives_up_puts_the_stop_and_target_back_and_is_not_retried(
    deps, factory
):
    deps.tasty.close = "work"
    timed_run(deps, factory)
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert {t.state for t in rows(factory, "timed")} == {"failed"}
    stops = rows(factory, "stop")
    assert {s.state for s in stops} == {"working"} and {s.rearms for s in stops} == {1}
    assert {t.state for t in rows(factory, "target")} == {"armed"}
    didnt = [e for e in deps.seen if e.title == "IC: timed close didn't fill"]
    assert len(didnt) == 2 and "still open" in didnt[0].detail
    sent = len(side_closes(deps))
    at(deps, "15:50")
    fresh_quotes(deps, factory, "15:50")
    bot_timed.timed_pass(deps)
    assert len(side_closes(deps)) == sent and len(rows(factory, "timed")) == 2


def test_a_timed_close_fires_early_on_a_half_day(deps, factory):
    timed_run(deps, factory)
    # NYSE's fetched calendar makes the test day a half day closing at 13:00
    sessions.use_fetched_calendar([date(2026, 12, 25)], {DAY: time(13, 0)})
    at(deps, "12:44")
    fresh_quotes(deps, factory, "12:44")
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed") == []
    at(deps, "12:45")
    fresh_quotes(deps, factory, "12:45")
    bot_timed.timed_pass(deps)
    assert {t.state for t in rows(factory, "timed")} == {"filled"}


def test_a_timed_close_never_fires_on_a_holiday(deps, factory):
    timed_run(deps, factory)
    sessions.use_fetched_calendar([DAY], {})
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed") == [] and side_closes(deps) == []


def test_a_timed_close_the_session_ended_without_is_marked_missed_and_alerted_once(deps, factory):
    timed_run(deps, factory)
    at(deps, "16:05")
    bot_timed.timed_pass(deps)
    assert {t.state for t in rows(factory, "timed")} == {"missed"}
    assert titles(deps).count("IC: timed close missed") == 1
    bot_timed.timed_pass(deps)
    assert titles(deps).count("IC: timed close missed") == 1
    assert side_closes(deps) == []


def test_freeze_holds_a_timed_close_until_it_is_off(deps, factory):
    timed_run(deps, factory)
    orders.set_switch(factory, "freeze", True)
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed") == []
    orders.set_switch(factory, "freeze", False)
    bot_timed.timed_pass(deps)
    assert {t.state for t in rows(factory, "timed")} == {"filled"}


def test_a_timed_close_with_nothing_held_sends_nothing(deps, factory):
    timed_run(deps, factory)
    call_stop = rows(factory, "stop", 0)[0]
    for symbol in (call_stop.short_symbol, call_stop.long_symbol):
        deps.tasty.held.pop(plain(symbol))  # closed by hand at the broker
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert [t.state for t in rows(factory, "timed", 0)] == ["expired"]
    assert [t.state for t in rows(factory, "timed", 1)] == ["filled"]
    assert len(side_closes(deps)) == 1


def test_a_side_whose_stop_filled_is_left_alone(deps, factory):
    timed_run(deps, factory)
    call_stop = rows(factory, "stop", 0)[0]
    deps.tasty.fill(call_stop.broker_order_id, D("4.80"))
    bot_exits.monitor_pass(deps)
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed", 0) == []
    assert [t.state for t in rows(factory, "timed", 1)] == ["filled"]


def test_a_running_timed_close_can_be_cancelled(deps, factory):
    deps.tasty.close = "work"
    run_id = timed_run(deps, factory)
    jobs = []
    deps.background = lambda fn, *args: jobs.append((fn, args))
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    call = rows(factory, "timed", 0)[0]
    assert bot_exits.request_cancel(factory, run_id, call.id, ny("15:45"))
    jobs[0][0](*jobs[0][1])
    call = rows(factory, "timed", 0)[0]
    assert call.state == "cancelled"
    assert rows(factory, "stop", 0)[0].state == "working"
    assert rows(factory, "target", 0)[0].state == "armed"
    assert "IC: Close cancelled" in titles(deps)


def test_a_timed_close_cut_off_by_a_restart_is_recovered(deps, factory):
    timed_run(deps, factory)
    deps.background = lambda fn, *args: None  # the app dies before the close runs
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    bot_exits.FIRING.clear()  # the restart
    bot_exits.recover_targets(deps)
    assert {t.state for t in rows(factory, "timed")} == {"failed"}
    # the stops' cancels were never sent: they're still working; the targets are back
    assert {s.state for s in rows(factory, "stop")} == {"working"}
    assert {t.state for t in rows(factory, "target")} == {"armed"}
    assert "IC: timed close error" in titles(deps)
    # once a day: the next pass doesn't try again
    bot_timed.timed_pass(deps)
    assert len(rows(factory, "timed")) == 2


def test_no_timed_close_for_a_bot_without_one(deps, factory):
    timed_run(deps, factory, close_time=None)
    at(deps, "15:59")
    fresh_quotes(deps, factory, "15:59")
    bot_timed.timed_pass(deps)
    at(deps, "16:05")
    bot_timed.timed_pass(deps)
    assert rows(factory, "timed") == []


# Time actions


def action_run(deps, factory, *actions, target_pct="50"):
    return timed_run(deps, factory, close_time=None, target_pct=target_pct,
                     use_time_actions=True, time_actions=actions)  # fmt: skip


def stop_orders(deps):
    return [c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]


def test_a_time_action_moves_a_working_stop_after_its_cancel_is_confirmed(deps, factory):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    old = rows(factory, "stop", 0)[0]
    at(deps, "13:59")
    bot_timed.actions_pass(deps)
    assert rows(factory, "action") == []
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")  # the short asked 0.65: well below the new stop
    bot_timed.actions_pass(deps)
    stop = rows(factory, "stop", 0)[0]
    # the call short filled at 2.50: 2.50 x 2.5 = 6.25, rounded down to the $0.10 tick: 6.20
    assert stop.state == "working" and stop.trigger == D("6.20") and stop.rearms == 0
    assert stop.broker_order_id != old.broker_order_id
    calls = [c[:2] for c in deps.tasty.calls]
    new_place = next(i for i, c in enumerate(deps.tasty.calls)
                     if c[0] == "place" and c[1] == stop.broker_order_id)  # fmt: skip
    assert calls.index(("cancel", old.broker_order_id)) < new_place
    assert deps.tasty.orders[old.broker_order_id].status.name == "CANCELLED"
    assert {a.state for a in rows(factory, "action")} == {"filled"}
    assert deps.tasty.calls[new_place][2]["stop-trigger"] == "6.20"
    assert deps.seen == []  # applied quietly


def test_a_stop_whose_cancel_is_not_confirmed_is_not_moved_and_alerts(deps, factory):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    old = rows(factory, "stop", 0)[0]
    deps.tasty.stuck.add(old.broker_order_id)  # the broker ignores its cancel
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")
    bot_timed.actions_pass(deps)
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.trigger, stop.broker_order_id) == (
        "working",
        old.trigger,
        old.broker_order_id,
    )
    assert [a.state for a in rows(factory, "action", 0)] == ["failed"]
    assert [a.state for a in rows(factory, "action", 1)] == ["filled"]
    alert = next(e for e in deps.seen if e.title == "IC: time action 1 not fully applied")
    assert "wasn't confirmed" in alert.detail and "Call side" in alert.detail
    bot_timed.actions_pass(deps)  # never tried again
    assert len(rows(factory, "action")) == 2


def test_a_time_action_moves_an_armed_target(deps, factory):
    action_run(deps, factory, TimeActionDef("dit", 0, "14:00", target_pct=D("25")))
    before = rows(factory, "target", 0)[0]
    at(deps, "14:00")
    bot_timed.actions_pass(deps)
    target = rows(factory, "target", 0)[0]
    assert target.state == "armed" and target.id == before.id
    # keeping 25% of the side's credit closes at 75% of it, where 50% closed at half
    assert target.limit == before.limit * D("1.5")
    assert len(stop_orders(deps)) == 2  # the stops weren't touched


def test_a_side_with_no_target_gets_one(deps, factory):
    bot_id = make(factory, live=True)  # no targets
    bot = bot_store.load(factory, bot_id)
    bot = replace(bot, use_time_actions=True,
                  time_actions=(TimeActionDef("dte", 0, "14:00", target_pct=D("40")),))  # fmt: skip
    bot_store.save(factory, bot, OPEN_TIME)
    bot_entry.fire(deps, bot_id, "09:47")
    assert rows(factory, "target") == []
    at(deps, "14:00")
    bot_timed.actions_pass(deps)
    assert [t.state for t in rows(factory, "target")] == ["armed", "armed"]


def test_a_time_action_fires_once_per_side(deps, factory):
    first = TimeActionDef("dte", 0, "14:00", target_pct=D("25"))
    run_id = action_run(deps, factory, first)
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")
    bot_timed.actions_pass(deps)
    bot_timed.actions_pass(deps)
    assert len(rows(factory, "action")) == 2
    # a row added before it doesn't make it fire again: rows are known by what they say
    bot_id = bot_store.run(factory, run_id).bot_id
    bot = bot_store.load(factory, bot_id)
    earlier = TimeActionDef("dte", 0, "13:00", stop_pct=D("200"))
    bot_store.save(factory, replace(bot, time_actions=(earlier, first)), OPEN_TIME)
    bot_timed.actions_pass(deps)
    assert sorted(a.log[0]["rule"] for a in rows(factory, "action")) == sorted(
        [first.label] * 2 + [earlier.label] * 2
    )


def test_a_busy_side_waits_for_its_time_action(deps, factory):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", target_pct=D("25")))
    call_target = rows(factory, "target", 0)[0]
    with factory() as session:
        session.get(BotExit, call_target.id).state = "firing"
        session.commit()
    bot_exits.FIRING.add(call_target.id)
    at(deps, "14:00")
    bot_timed.actions_pass(deps)
    assert rows(factory, "action", 0) == []  # waits, nothing recorded
    assert [a.state for a in rows(factory, "action", 1)] == ["filled"]
    with factory() as session:
        session.get(BotExit, call_target.id).state = "failed"  # its close gave up
        session.commit()
    bot_timed.actions_pass(deps)
    assert [a.state for a in rows(factory, "action", 0)] == ["filled"]
    assert rows(factory, "target", 0)[-1].state == "armed"  # a new one: the old one failed


def test_a_late_time_action_alerts(deps, factory, monkeypatch):
    action_run(deps, factory, TimeActionDef("dit", 0, "10:00", target_pct=D("25")))
    monkeypatch.setattr(bot_timed.timing, "action_due", lambda *a: (True, True))
    at(deps, "14:00")
    bot_timed.actions_pass(deps)
    late = [e for e in deps.seen if e.title == "IC: time action 1 fired late"]
    assert len(late) == 1 and "missed its time" in late[0].detail


def test_a_moved_stop_that_would_fire_at_once_is_not_applied(deps, factory):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("1")))
    old = rows(factory, "stop", 0)[0]
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")
    bot_timed.actions_pass(deps)
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.trigger, stop.broker_order_id) == (
        "working",
        old.trigger,
        old.broker_order_id,
    )
    assert ("cancel", old.broker_order_id) not in [c[:2] for c in deps.tasty.calls]
    alert = next(e for e in deps.seen if e.title == "IC: time action 1 not fully applied")
    assert "would fire at once" in alert.detail


def test_no_time_actions_while_frozen(deps, factory):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    orders.set_switch(factory, "freeze", True)
    at(deps, "14:00")
    bot_timed.actions_pass(deps)
    assert rows(factory, "action") == []


def test_a_move_that_breaks_part_way_is_recorded_and_alerted_once(deps, factory, monkeypatch):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))

    def broken(*args):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(bot_exits, "_move_stop", broken)
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")
    bot_timed.actions_pass(deps)
    bot_timed.actions_pass(deps)
    assert {a.state for a in rows(factory, "action")} == {"failed"}
    alerts = [e for e in deps.seen if e.title == "IC: time action 1 not fully applied"]
    assert len(alerts) == 1 and "check the side's stop" in alerts[0].detail


def test_a_side_that_cant_be_read_is_retried_and_alerted_once(deps, factory, monkeypatch):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", target_pct=D("25")))
    real = bot_exits.apply_time_action

    def broken(*args):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(bot_exits, "apply_time_action", broken)
    monkeypatch.setattr(bot_timed, "_BROKEN", set())
    at(deps, "14:00")
    bot_timed.actions_pass(deps)
    bot_timed.actions_pass(deps)
    assert titles(deps).count("IC: time action 1 couldn't run") == 1
    monkeypatch.setattr(bot_exits, "apply_time_action", real)
    bot_timed.actions_pass(deps)
    assert {a.state for a in rows(factory, "action")} == {"filled"}


def test_a_timed_close_that_cant_start_alerts_once_a_day(deps, factory, monkeypatch):
    timed_run(deps, factory)

    def broken(*args):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(bot_exits, "fire_timed", broken)
    monkeypatch.setattr(bot_timed, "_FAILED", set())
    at(deps, "15:45")
    bot_timed.timed_pass(deps)
    bot_timed.timed_pass(deps)
    assert titles(deps).count("IC: timed close failed") == 1


# Final review fixes


def cancels_of(deps, order_id):
    return [c for c in deps.tasty.calls if c[:2] == ("cancel", order_id)]


def test_a_stop_move_that_would_fire_now_is_refused_and_the_old_stop_kept(deps, factory):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    old = rows(factory, "stop", 0)[0]
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00", short_mid="6.25")  # asked 6.30: above the new 6.20
    bot_timed.actions_pass(deps)
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.trigger, stop.broker_order_id) == (
        "working",
        old.trigger,
        old.broker_order_id,
    )
    assert cancels_of(deps, old.broker_order_id) == []
    alert = next(e for e in deps.seen if e.title == "IC: time action 1 not fully applied")
    assert "would fire" in alert.detail and "old stop" in alert.detail


def test_a_stop_move_with_no_fresh_quote_waits_and_is_retried(deps, factory, monkeypatch):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    monkeypatch.setattr(bot_timed, "_WAITING", set())
    old = rows(factory, "stop", 0)[0]
    at(deps, "14:00")
    bot_timed.actions_pass(deps)  # no quotes at all, even after the wait
    bot_timed.actions_pass(deps)
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.broker_order_id) == ("working", old.broker_order_id)
    assert cancels_of(deps, old.broker_order_id) == []
    assert rows(factory, "action") == []  # not recorded: tried again next pass
    assert titles(deps).count("IC: time action 1 waiting for a price") == 1
    fresh_quotes(deps, factory, "14:00")
    bot_timed.actions_pass(deps)
    assert {a.state for a in rows(factory, "action")} == {"filled"}
    assert rows(factory, "stop", 0)[0].trigger == D("6.20")


def test_a_stop_only_side_moves_once_its_quote_arrives_within_the_wait(deps, factory):
    bot_id = make(factory, live=True)  # stops, no targets: the watch never subscribes them
    bot = bot_store.load(factory, bot_id)
    bot = replace(bot, use_time_actions=True,
                  time_actions=(TimeActionDef("dte", 0, "14:00", stop_pct=D("150")),))  # fmt: skip
    bot_store.save(factory, bot, OPEN_TIME)
    bot_entry.fire(deps, bot_id, "09:47")
    shorts = {s.short_symbol for s in rows(factory, "stop")}
    assert shorts <= deps.hub.subscribed  # subscribed when the exits were placed
    at(deps, "14:00")
    deps.sleep = lambda seconds: fresh_quotes(deps, factory, "14:00")  # arrives while waiting
    bot_timed.actions_pass(deps)
    assert {a.state for a in rows(factory, "action")} == {"filled"}
    assert {s.trigger for s in rows(factory, "stop") if s.state == "working"} == {D("6.20")}


def test_a_refused_replace_after_the_cancel_waits_as_pending_and_alerts(deps, factory):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")
    deps.tasty.errors["place"] = BrokerError("rejected")
    bot_timed.actions_pass(deps)
    stop = rows(factory, "stop", 0)[0]
    assert (stop.state, stop.broker_order_id, stop.trigger) == ("pending", None, D("6.20"))
    assert any("no stop until" in e.detail for e in deps.seen)
    del deps.tasty.errors["place"]
    bot_exits.monitor_pass(deps)  # the next check places it
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.rearms == 0


class Crash(BaseException):
    """The app dying mid-step."""


def test_a_crash_between_the_cancel_and_the_replace_is_recovered(deps, factory, monkeypatch):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    old = rows(factory, "stop", 0)[0]
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")

    def crash(*args):
        raise Crash()

    monkeypatch.setattr(bot_exits, "read_after_cancel", crash)
    with pytest.raises(Crash):
        bot_timed.actions_pass(deps)
    assert cancels_of(deps, old.broker_order_id)  # the cancel went out, then the app died
    assert rows(factory, "stop", 0)[0].state == "cancelled"
    monkeypatch.undo()
    bot_exits.recover_targets(deps)  # startup
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "working" and stop.broker_order_id != old.broker_order_id
    assert stop.trigger == old.trigger  # put back as it was; the action runs again later
    assert any("stop" in e.title for e in deps.seen)


def test_a_move_that_breaks_after_the_cancel_says_the_side_has_no_stop(deps, factory, monkeypatch):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")
    real = bot_exits.rearm

    def broken(*args, **kwargs):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(bot_exits, "rearm", broken)
    bot_timed.actions_pass(deps)
    alert = next(e for e in deps.seen if e.title == "IC: time action 1 not fully applied")
    assert "old stop was cancelled" in alert.detail and "next check" in alert.detail
    assert rows(factory, "stop", 0)[0].state == "pending"
    monkeypatch.setattr(bot_exits, "rearm", real)
    bot_exits.monitor_pass(deps)
    assert rows(factory, "stop", 0)[0].state == "working"


def test_a_timed_close_that_finds_the_stop_filled_leaves_the_target_off(deps, factory):
    timed_run(deps, factory)
    call_stop = rows(factory, "stop", 0)[0]
    deps.tasty.fill(call_stop.broker_order_id, D("4.80"))  # filled; the monitor hasn't seen it
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert rows(factory, "target", 0)[0].state == "cancelled"
    assert rows(factory, "stop", 0)[0].state == "working"  # for the monitor to settle


def test_a_short_gone_with_its_wing_still_held_alerts(deps, factory):
    timed_run(deps, factory)
    call_stop = rows(factory, "stop", 0)[0]
    deps.tasty.held.pop(plain(call_stop.short_symbol))  # bought back by hand; wing kept
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    assert [t.state for t in rows(factory, "timed", 0)] == ["failed"]
    alert = next(e for e in deps.seen if e.title == "IC: timed close not done")
    assert call_stop.long_symbol.strip() in alert.detail
    assert len(side_closes(deps)) == 1  # only the put side


def test_the_timed_close_chase_stops_at_the_session_close(deps, factory):
    deps.tasty.close = "work"
    timed_run(deps, factory, close_time="15:59")
    clock = {"now": ny("15:59").replace(second=40)}
    deps.now = lambda: clock["now"]

    def sleep(seconds):
        clock["now"] += timedelta(seconds=seconds)

    deps.sleep = sleep
    fresh_quotes(deps, factory, "15:59")
    for symbol, quote in list(deps.hub.quotes.items()):
        deps.hub.set(symbol, quote.bid, quote.ask, clock["now"])
    bot_timed.timed_pass(deps)
    call = rows(factory, "timed", 0)[0]
    assert call.state == "failed" and "session" in call.log[-1]["event"]
    replaces = [c for c in deps.tasty.calls if c[0] == "replace"]
    assert len(replaces) < 5  # stopped before its last try


def test_a_move_that_breaks_after_the_stop_failed_says_so(deps, factory, monkeypatch):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")

    def broken(deps_, stop_id, first=False):
        with factory() as session:
            session.get(BotExit, stop_id).state = "failed"  # committed, then it broke
            session.commit()
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(bot_exits, "rearm", broken)
    bot_timed.actions_pass(deps)
    alert = next(e for e in deps.seen if e.title == "IC: time action 1 not fully applied")
    assert "next check" not in alert.detail and "has no stop" in alert.detail


def test_a_cut_off_move_whose_old_stop_cant_be_read_says_so(deps, factory, monkeypatch):
    action_run(deps, factory, TimeActionDef("dte", 0, "14:00", stop_pct=D("150")))
    at(deps, "14:00")
    fresh_quotes(deps, factory, "14:00")

    def crash(*args):
        raise Crash()

    monkeypatch.setattr(bot_exits, "read_after_cancel", crash)
    with pytest.raises(Crash):
        bot_timed.actions_pass(deps)
    monkeypatch.undo()
    deps.tasty.errors["get_order"] = BrokerError("down")
    bot_exits.recover_targets(deps)
    alert = next(e for e in deps.seen if "stop move" in e.title)
    assert "restart" not in alert.detail and "still in place" not in alert.detail
    assert "couldn't be confirmed" in alert.detail


def test_a_timed_close_that_filled_is_a_routine_alert_about_its_run(deps, factory):
    from optionsjourney.core.alert_format import is_safety

    timed_run(deps, factory)
    at(deps, "15:45")
    fresh_quotes(deps, factory, "15:45")
    bot_timed.timed_pass(deps)
    [event] = [e for e in deps.seen if e.title == "IC: timed close filled"][:1]
    run_id = rows(factory, "timed")[0].run_id
    assert (event.routine, event.run_id, event.bot_id) == (True, run_id, event.bot_id)
    assert event.bot_id is not None and not is_safety(event.title, event.detail)


# --- Slice 13: any group -----------------------------------------------------------------

from optionsjourney.bots import caps  # noqa: E402
from optionsjourney.core.symbols import parse_occ  # noqa: E402
from optionsjourney.db.models import Bot, BotRun  # noqa: E402
from tests.services.bot_fakes import SHAPE_MATRIX, make_shape, mid_for  # noqa: E402


def shape_run(deps, factory, key, contracts=None, **changes):
    """A live run of a preset shape with a 50% stop and target on every group and the
    bot `changes` (a close time, time actions); `contracts` sizes the run past
    validation's one contract."""
    bot_id = make_shape(factory, key, live=True, stop=D("50"), target=D("50"))
    bot = bot_store.load(factory, bot_id)
    assert bot_store.save(factory, replace(bot, **changes), OPEN_TIME)[1] == {}
    if contracts is not None:
        with factory() as session:
            row = session.get(Bot, bot_id)
            row.contracts, row.max_per_trade, row.max_per_day = contracts, 40, 40
            session.commit()
    return bot_entry.fire(deps, bot_id, "09:47")


def quote_all(deps, factory, run_id, hhmm):
    """Fresh quotes on every leg of the run at `hhmm`, 0.05 either side of its mid."""
    with factory() as session:
        legs = session.get(BotRun, run_id).legs
    for leg in legs:
        _, details = parse_occ(leg["symbol"])
        mid = mid_for(details.strike, details.put_call.lower())
        deps.hub.set(leg["symbol"], max(mid - D("0.05"), D(0)), mid + D("0.05"), ny(hhmm))


# Decision 0048: the matrix shapes whose stop rests at TastyTrade; every other group rests
# its profit target there instead (one GTC closing limit from the fill)
RESTING_STOPS = ("iron_condor", "put_credit_spread", "short_put")


def resting(sent):
    """The resting targets among closing orders sent (GTC), and the rest."""
    return ([b for b in sent if b["time-in-force"] == "GTC"],
            [b for b in sent if b["time-in-force"] != "GTC"])  # fmt: skip


def group_closes(deps):
    """Every closing order sent that isn't a stop."""
    return [
        c[2]
        for c in deps.tasty.calls
        if c[0] == "place"
        and c[2]["order-type"] != "Stop"
        and not c[2]["legs"][0]["action"].endswith("Open")
    ]


def held_trades(factory, now):
    """The run's open trades by the caps' count, as the entry reads it."""
    with factory() as session:
        runs = list(session.scalars(select(BotRun)))
        out = []
        for run in runs:
            exits = tuple(
                caps.ExitRow.of(row)
                for row in session.scalars(select(BotExit).where(BotExit.run_id == run.id))
            )
            out.append(caps.RunRow(run.id, run.id, run.status, tuple(run.legs), exits,
                                   bool(run.filled_legs)))  # fmt: skip
    return caps.open_trades(out, True, now)


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_is_closed_by_time(deps, factory, key):
    run_id = shape_run(deps, factory, key, close_time="15:45")
    with factory() as session:
        groups = sorted({leg["group"] for leg in session.get(BotRun, run_id).legs})
    assert held_trades(factory, ny("15:44")) == len(groups)
    at(deps, "15:45")
    quote_all(deps, factory, run_id, "15:45")
    bot_timed.timed_pass(deps)
    timed = rows(factory, "timed")
    assert [(t.group_position, t.state) for t in timed] == [(g, "filled") for g in groups]
    rested, closes = resting(group_closes(deps))
    assert len(closes) == len(groups)
    assert len(rested) == (0 if key in RESTING_STOPS else len(groups))
    assert len([c for c in deps.tasty.calls if c[0] == "cancel"]) >= len(rested)
    assert held_trades(factory, ny("15:46")) == 0


def test_a_time_action_closes_a_share_of_a_flys_units(deps, factory):
    # A long call butterfly of 4 units, its body sold 2 to a unit: closing 50% closes
    # 2 of its 4 units (2, 4 and 2 contracts), not 50% of the body's 8 contracts.
    action = TimeActionDef("dte", 0, "14:00", close_pct=50)
    run_id = shape_run(deps, factory, "long_call_butterfly", contracts=4,
                       use_time_actions=True, time_actions=(action,))  # fmt: skip
    at(deps, "14:00")
    quote_all(deps, factory, run_id, "14:00")
    bot_timed.actions_pass(deps)
    (close,) = rows(factory, "close")
    assert (close.state, close.contracts) == ("reduced", 2)
    # decision 0048: its target rests (4 units from the fill), is cancelled before the
    # close and rests again for the 2 left
    rested, (sent,) = resting(group_closes(deps))
    assert [sorted(int(leg["quantity"]) for leg in b["legs"]) for b in rested] == [
        [4, 4, 8],
        [2, 2, 4],
    ]
    assert sorted(int(leg["quantity"]) for leg in sent["legs"]) == [2, 2, 4]
    (row,) = rows(factory, "action")
    assert row.state == "filled" and "closing 2 of 4" in row.log[0]["notes"]
    assert row.contracts == 4 and len(row.legs) == 3 and row.watched


def test_a_time_action_moves_the_target_of_a_group_without_a_short_and_wing(deps, factory):
    # A long call (a debit): its target is re-priced on the group's entry, -2.50 a unit.
    # Keeping 50% closed it at -3.75; keeping 20% closes it at -3.00.
    action = TimeActionDef("dte", 0, "14:00", target_pct=D("20"))
    run_id = shape_run(deps, factory, "long_call", use_time_actions=True, time_actions=(action,))
    (before,) = rows(factory, "target")
    # decision 0048: it rests at TastyTrade, -3.75 rounded toward a fill on its tick
    assert (before.state, before.limit) == ("working", D("-3.70"))
    at(deps, "14:00")
    quote_all(deps, factory, run_id, "14:00")
    bot_timed.actions_pass(deps)
    # a resting target is changed by cancelling it and placing the new one (a new row)
    old, target = rows(factory, "target")
    assert old.id == before.id and old.state == "cancelled"
    assert (target.state, target.limit) == ("working", D("-3.00"))
    (row,) = rows(factory, "action")
    assert row.state == "filled", row.log


def test_a_group_with_no_target_gets_one_on_its_entry(deps, factory):
    # A naked short put (2.50 credit) with no target: keeping 40% closes at 1.50, and
    # the new target names the group's leg and the stop's anchor.
    bot_id = make_shape(factory, "short_put", live=True, stop=D("50"))
    bot = bot_store.load(factory, bot_id)
    action = TimeActionDef("dte", 0, "14:00", target_pct=D("40"))
    changed = replace(bot, use_time_actions=True, time_actions=(action,))
    assert bot_store.save(factory, changed, OPEN_TIME)[1] == {}
    bot_entry.fire(deps, bot_id, "09:47")
    assert rows(factory, "target") == []
    at(deps, "14:00")
    bot_timed.actions_pass(deps)
    (target,) = rows(factory, "target")
    (stop,) = rows(factory, "stop")
    assert (target.state, target.limit) == ("armed", D("1.50"))
    assert (target.legs, target.anchor_symbol, target.watched) == (stop.legs, stop.short_symbol,
                                                                   False)  # fmt: skip
    assert (target.short_symbol, target.long_symbol) == (stop.short_symbol, "")


@pytest.mark.parametrize("key", ["short_put", "long_call_butterfly"])
def test_a_missed_timed_close_records_the_groups_legs(deps, factory, key):
    shape_run(deps, factory, key, contracts=3, close_time="15:45")
    at(deps, "16:05")
    bot_timed.timed_pass(deps)
    (missed,) = rows(factory, "timed")
    (stop,) = rows(factory, "stop")
    assert missed.state == "missed" and missed.contracts == 3  # the group's units
    assert (missed.legs, missed.anchor_symbol, missed.watched) == (
        stop.legs, stop.anchor_symbol, stop.watched,
    )  # fmt: skip
    assert missed.short_symbol == (stop.anchor_symbol or "")
    assert f"{key}: timed close missed" in titles(deps)


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_counts_closed_for_max_open_trades_once_closed(deps, factory, key):
    # Each group is its own open trade; one closed by hand stops counting, the rest still do.
    run_id = shape_run(deps, factory, key)
    with factory() as session:
        groups = sorted({leg["group"] for leg in session.get(BotRun, run_id).legs})
    at(deps, "11:00")
    quote_all(deps, factory, run_id, "11:00")
    assert held_trades(factory, ny("11:00")) == len(groups)
    for done, group in enumerate(groups, start=1):
        assert bot_exits.fire_close(deps, run_id, group, 1, 1) is not None
        assert held_trades(factory, ny("11:01")) == len(groups) - done
    assert held_trades(factory, ny("11:02")) == 0


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_moves_its_stop_and_target_by_a_time_action(deps, factory, key):
    action = TimeActionDef("dte", 0, "14:00", stop_pct=D("150"), target_pct=D("25"))
    run_id = shape_run(deps, factory, key, use_time_actions=True, time_actions=(action,))
    stop, target = rows(factory, "stop", 0)[0], rows(factory, "target", 0)[0]
    sent_before = len(stop_orders(deps))
    at(deps, "14:00")
    quote_all(deps, factory, run_id, "14:00")
    bot_timed.actions_pass(deps)
    assert {a.state for a in rows(factory, "action")} == {"filled"}
    moved_stop, moved_target = rows(factory, "stop", 0)[0], rows(factory, "target", 0)[-1]
    if key in RESTING_STOPS:
        assert moved_target.id == target.id and moved_target.state == "armed"
    else:  # decision 0048: a resting target is cancelled and placed again, a new row
        assert rows(factory, "target", 0)[0].state == "cancelled"
        assert moved_target.id != target.id and moved_target.state == "working"
    assert moved_target.limit != target.limit
    if stop.watched:  # the level moves; nothing is sent to the broker
        assert moved_stop.state == bot_exits.WATCHING and moved_stop.watched
        assert moved_stop.trigger < stop.trigger < 0  # 150% lost is a deeper P&L level
        assert len(stop_orders(deps)) == sent_before
    else:  # cancelled first, then a new stop on the anchor at a new trigger, per contract
        assert moved_stop.state == "working" and moved_stop.trigger > stop.trigger
        assert moved_stop.broker_order_id != stop.broker_order_id
        calls = deps.tasty.calls
        new_at = next(i for i, c in enumerate(calls)
                      if c[0] == "place" and c[1] == moved_stop.broker_order_id)  # fmt: skip
        assert [c[:2] for c in calls].index(("cancel", stop.broker_order_id)) < new_at
        leg = calls[new_at][2]["legs"][0]
        assert leg["symbol"] == stop.anchor_symbol
        assert leg["quantity"] == stop.contracts * next(
            x["ratio"] for x in stop.legs if x["symbol"] == stop.anchor_symbol
        )
        assert D(str(calls[new_at][2]["stop-trigger"])) == moved_stop.trigger


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_closes_a_share_of_the_group_by_a_time_action(deps, factory, key):
    # 2 units held, close 50%: one unit, each leg its ratio, bought legs sold and sold
    # legs bought back.
    action = TimeActionDef("dte", 0, "14:00", close_pct=50)
    run_id = shape_run(deps, factory, key, contracts=2,
                       use_time_actions=True, time_actions=(action,))  # fmt: skip
    legs = bot_exits.exit_legs(rows(factory, "stop", 0)[0])
    at(deps, "14:00")
    quote_all(deps, factory, run_id, "14:00")
    bot_timed.actions_pass(deps)
    close = next(c for c in rows(factory, "close") if c.group_position == 0)
    assert (close.state, close.contracts) == ("reduced", 1)
    rested, closes = resting(group_closes(deps))
    # decision 0048: a resting target, cancelled before the close, rests again for the rest
    assert len(rested) == (0 if key in RESTING_STOPS else 2)
    (sent,) = [b for b in closes if {leg["symbol"] for leg in b["legs"]}
               == {leg.symbol for leg in legs}]  # fmt: skip
    want = [(leg.symbol, leg.ratio, "Buy to Close" if leg.side.value == "sell" else "Sell to Close")
            for leg in legs]  # fmt: skip
    assert sorted((b["symbol"], b["quantity"], b["action"]) for b in sent["legs"]) == sorted(want)
