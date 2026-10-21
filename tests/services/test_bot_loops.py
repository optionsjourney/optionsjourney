import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.core import sessions
from optionsjourney.db.models import Bot, BotRun
from optionsjourney.services import bot_entry, bot_loops, bot_store, economic_events
from optionsjourney.trading.orders import Leg, Order, OrderStatus, Side
from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make, stream_chain

LEG = {"symbol": "SPXW  261013C05910000", "ratio": 1, "side": "sell", "group": 0}
ORDER = {
    "order-type": "Limit",
    "legs": [{"symbol": LEG["symbol"], "quantity": 1, "action": "Sell to Open"}],
}


class Inline:
    """Runs a spawned thread's target straight away."""

    def __init__(self, target, args=(), kwargs=None, **_):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def deps_at(factory, when, tasty=None):
    tasty = tasty or FakeTasty()
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty],
        FakeHub(),
        FakeChain(),
        notify=lambda e: None,
        sleep=lambda s: None,
        now=lambda: when,
        background=lambda fn, *args: fn(*args),
    )
    d.tasty = tasty
    stream_chain(d.hub, when)  # a Paper bot's pretend fill reads the stream
    return d


def statuses(factory):
    with factory() as session:
        return [
            (r.entry_time, r.status) for r in session.scalars(select(BotRun).order_by(BotRun.id))
        ]


def running_run(factory, bot_id, key, order_id, created=OPEN_TIME, legs=None):
    with factory() as session:
        run = BotRun(
            bot_id=bot_id,
            key=key,
            trading_date=OPEN_TIME.date(),
            entry_time="09:47",
            status="running",
            live=True,
            legs=[LEG] if legs is None else legs,
            filled_legs=[],
            chase_log=[],
            contracts=0,
            broker_order_id=order_id,
            created_at=created,
            updated_at=created,
        )
        session.add(run)
        session.commit()
        return run.id


def test_clock_fires_a_due_time_once(factory, monkeypatch):
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: None)
    make(factory)
    d = deps_at(factory, OPEN_TIME)
    bot_loops.clock_pass(d, spawn=Inline)
    bot_loops.clock_pass(d, spawn=Inline)
    assert statuses(factory) == [("09:47", "filled")]


def test_clock_records_a_missed_time(factory):
    make(factory)
    bot_loops.clock_pass(deps_at(factory, OPEN_TIME + timedelta(minutes=10)), spawn=Inline)
    assert statuses(factory) == [("09:47", "skipped")]


def test_clock_ignores_days_the_market_is_shut(factory):
    make(factory)
    saturday = datetime(2026, 10, 17, 14, 0, tzinfo=UTC)
    bot_loops.clock_pass(deps_at(factory, saturday), spawn=Inline)
    assert statuses(factory) == []


def reshape(factory, bot_id, **changes):
    bot = bot_store.load(factory, bot_id)
    _, problems = bot_store.save(factory, replace(bot, **changes), OPEN_TIME)
    assert problems == {}


def test_a_weekly_bot_on_an_off_day_gets_no_run_at_all(factory):
    bot_id = make(factory)
    reshape(factory, bot_id, frequency="weekly", entry_days=(1,))  # OPEN_TIME is a Tuesday
    bot_loops.clock_pass(deps_at(factory, OPEN_TIME), spawn=Inline)
    bot_loops.clock_pass(deps_at(factory, OPEN_TIME + timedelta(minutes=10)), spawn=Inline)
    assert statuses(factory) == []


def test_a_specific_date_bot_fires_on_its_date(factory, monkeypatch):
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: None)
    bot_id = make(factory)
    reshape(factory, bot_id, frequency="specific_dates", specific_dates=("2026-10-13",))
    bot_loops.clock_pass(deps_at(factory, OPEN_TIME), spawn=Inline)
    assert statuses(factory) == [("09:47", "filled")]


def test_the_calendar_alert_goes_out_once_a_day(factory, monkeypatch):
    make(factory)
    monkeypatch.setattr(sessions, "calendar_covers", lambda day: False)
    monkeypatch.setattr(bot_loops, "_calendar_alerted", None)
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    bot_loops.clock_pass(d, spawn=Inline)
    bot_loops.clock_pass(d, spawn=Inline)
    assert [a.title for a in alerts if "calendar" in a.title] == ["Holiday calendar out of date"]
    assert "2026" in alerts[0].detail and alerts[0].link == "/automation"
    d.now = lambda: OPEN_TIME + timedelta(days=1)
    bot_loops.clock_pass(d, spawn=Inline)
    assert len([a for a in alerts if "calendar" in a.title]) == 2


def test_a_covered_calendar_sends_no_alert(factory, monkeypatch):
    monkeypatch.setattr(bot_loops, "_calendar_alerted", None)
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    bot_loops.clock_pass(d, spawn=Inline)
    assert alerts == []


def test_recovery_cancels_an_orphaned_entry_order(factory, monkeypatch):
    placed = []
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: placed.append(run_id))
    bot_id = make(factory, live=True)
    tasty = FakeTasty(entry="work")
    d = deps_at(factory, OPEN_TIME, tasty)
    # simulate a crash mid-chase: send the order, leave the run "running"
    tasty_id = tasty.place_order("5WX01234", ORDER)
    running_run(factory, bot_id, "bot-x", tasty_id)
    bot_loops.reconcile(d)
    assert ("cancel", tasty_id) in tasty.calls
    assert statuses(factory) == [("09:47", "missed")]
    assert placed == []


def test_recovery_hands_a_filled_orphan_to_exits(factory, monkeypatch):
    placed = []
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: placed.append(run_id))
    bot_id = make(factory, live=True)
    tasty = FakeTasty(entry="fill")
    d = deps_at(factory, OPEN_TIME, tasty)
    tasty_id = tasty.place_order("5WX01234", ORDER)
    run_id = running_run(factory, bot_id, "bot-y", tasty_id)
    bot_loops.reconcile(d)
    assert placed == [run_id] and statuses(factory) == [("09:47", "filled")]


def test_recovery_leaves_runs_being_chased_alone(factory):
    bot_id = make(factory, live=True)
    run_id = running_run(factory, bot_id, "bot-z", "999", legs=[])
    bot_entry.ACTIVE.add(run_id)
    try:
        bot_loops.reconcile(deps_at(factory, OPEN_TIME))  # would KeyError on order 999 if touched
    finally:
        bot_entry.ACTIVE.clear()
    assert statuses(factory) == [("09:47", "running")]


def test_recovery_only_alerts_about_a_stale_run(factory):
    bot_id = make(factory, live=True)
    running_run(factory, bot_id, "bot-old", "999", created=OPEN_TIME - timedelta(days=6))
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    bot_loops.reconcile(d)  # would KeyError on order 999 if touched
    assert len(alerts) == 1 and statuses(factory) == [("09:47", "running")]


def test_one_failing_run_does_not_stop_the_others(factory, monkeypatch):
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: None)
    bot_id = make(factory, live=True)
    tasty = FakeTasty(entry="fill")
    d = deps_at(factory, OPEN_TIME, tasty)
    running_run(factory, bot_id, "bot-bad", "999")  # the broker has no order 999
    good = tasty.place_order("5WX01234", ORDER)
    running_run(factory, bot_id, "bot-good", good)
    bot_loops.reconcile(d)
    assert statuses(factory) == [("09:47", "running"), ("09:47", "filled")]


def test_recovery_finds_an_unrecorded_order_by_its_log(factory, monkeypatch):
    from optionsjourney.db.models import OrderLog

    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: None)
    bot_id = make(factory, live=True)
    tasty = FakeTasty(entry="fill")
    d = deps_at(factory, OPEN_TIME, tasty)
    tasty_id = tasty.place_order("5WX01234", ORDER)
    run_id = running_run(factory, bot_id, "bot-nl", None)
    with factory() as session:
        columns = {c.name for c in OrderLog.__table__.columns}
        values = {
            "broker": "tastytrade",
            "account_number": "5WX01234",
            "origin": "bot",
            "bot_id": bot_id,
            "bot_run_id": run_id,
            "action": "send",
            "opening": True,
            "quantity": 1,
            "order_type": "Limit",
            "order": {},
            "ok": True,
            "result": "Placed",
            "broker_order_id": tasty_id,
            "created_at": OPEN_TIME,
        }
        session.add(OrderLog(**{k: v for k, v in values.items() if k in columns}))
        session.commit()
    bot_loops.reconcile(d)
    assert statuses(factory) == [("09:47", "filled")]


def test_settle_does_nothing_once_the_run_is_no_longer_running(factory):
    bot_id = make(factory, live=True)
    run_id = running_run(factory, bot_id, "bot-late", "999")
    d = deps_at(factory, OPEN_TIME)
    bot_entry.update_run(d, run_id, status="filled")  # settled after reconcile listed it
    bot_loops._settle(d, run_id, bot_id, "999", OPEN_TIME, OPEN_TIME)  # KeyError if touched
    assert statuses(factory) == [("09:47", "filled")]


def test_settle_leaves_an_active_run_alone(factory):
    bot_id = make(factory, live=True)
    run_id = running_run(factory, bot_id, "bot-act", "999")
    bot_entry.ACTIVE.add(run_id)
    try:
        bot_loops._settle(deps_at(factory, OPEN_TIME), run_id, bot_id, "999", OPEN_TIME, OPEN_TIME)
    finally:
        bot_entry.ACTIVE.clear()
    assert statuses(factory) == [("09:47", "running")]


def test_fire_keeps_the_run_active_until_it_has_settled(factory, monkeypatch):
    seen = []

    def protections(deps, run_id):
        seen.append(run_id in bot_entry.ACTIVE)

    monkeypatch.setattr(bot_entry, "place_protections", protections)
    bot_id = make(factory, live=True)
    d = deps_at(factory, OPEN_TIME, FakeTasty(entry="fill"))
    run_id = bot_entry.fire(d, bot_id, "09:47")
    assert seen == [True]
    assert run_id not in bot_entry.ACTIVE


def test_a_stale_run_alerts_only_once(factory):
    bot_id = make(factory, live=True)
    running_run(factory, bot_id, "bot-old2", "999", created=OPEN_TIME - timedelta(days=6))
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    bot_loops.reconcile(d)
    bot_loops.reconcile(d)
    assert len(alerts) == 1


def test_a_deleted_bot_alerts_once_and_the_run_is_left_alone(factory):
    from optionsjourney.db.models import Bot

    bot_id = make(factory, live=True)
    running_run(factory, bot_id, "bot-gone", "999")
    with factory() as session:
        session.get(Bot, bot_id).hidden = True  # hidden bots still load by id
        session.commit()
    assert bot_loops.bot_store.load(factory, bot_id) is not None
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    d_load = bot_loops.bot_store.load
    bot_loops.bot_store.load = lambda f, i: None  # the row is gone
    try:
        bot_loops.reconcile(d)
        bot_loops.reconcile(d)
    finally:
        bot_loops.bot_store.load = d_load
    assert len(alerts) == 1 and statuses(factory) == [("09:47", "running")]


def test_one_bot_failing_does_not_delay_the_others(factory, monkeypatch):
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: None)
    first = make(factory)
    second = make(factory)
    real = bot_entry.record_skip

    def skip(deps, bot_id, hhmm, reason):
        if bot_id == first:
            raise RuntimeError("boom")
        return real(deps, bot_id, hhmm, reason)

    monkeypatch.setattr(bot_entry, "record_skip", skip)
    late = OPEN_TIME + timedelta(minutes=10)
    bot_loops.clock_pass(deps_at(factory, late), spawn=Inline)
    with factory() as session:
        assert session.scalars(select(BotRun.bot_id)).all() == [second]


def log_row(factory, **values):
    from optionsjourney.db.models import OrderLog

    base = {
        "broker": "tastytrade", "account_number": "5WX01234", "origin": "bot", "action": "send",
        "opening": True, "quantity": 1, "order_type": "limit", "order": {}, "ok": True,
        "result": "Sent.", "created_at": OPEN_TIME,
    }  # fmt: skip
    with factory() as session:
        session.add(OrderLog(**(base | values)))
        session.commit()


def filled_run(factory, bot_id, key, created=OPEN_TIME):
    run_id = running_run(factory, bot_id, key, "555", created=created)
    with factory() as session:
        session.get(BotRun, run_id).status = "filled"
        session.commit()
    return run_id


def test_a_filled_run_with_no_exits_alerts_once(factory):
    bot_id = make(factory, live=True)
    run_id = filled_run(factory, bot_id, "bot-noexit")
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    bot_loops.check_exits(d)
    bot_loops.check_exits(d)
    assert len(alerts) == 1 and "exits may be missing" in alerts[0].detail
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith("Needs you")


def test_exit_rows_left_placing_are_marked_failed_with_an_alert(factory):
    from optionsjourney.db.models import BotExit

    bot_id = make(factory, live=True)
    run_id = filled_run(factory, bot_id, "bot-placing")
    with factory() as session:
        session.add(BotExit(
            run_id=run_id, group_position=0, kind="stop", state="placing",
            short_symbol=LEG["symbol"], long_symbol=LEG["symbol"], contracts=1, rearms=0,
            log=[], created_at=OPEN_TIME, updated_at=OPEN_TIME,
        ))  # fmt: skip
        session.commit()
    alerts = []
    d = deps_at(factory, OPEN_TIME + timedelta(minutes=1))
    d.notify = alerts.append
    bot_loops.check_exits(d)  # a minute old: may still be placing
    assert alerts == []
    d.now = lambda: OPEN_TIME + timedelta(minutes=3)
    bot_loops.check_exits(d)
    bot_loops.check_exits(d)
    with factory() as session:
        assert session.scalars(select(BotExit.state)).all() == ["failed"]
    assert len(alerts) == 1 and "exits may be missing" in alerts[0].detail
    assert not [c for c in d.tasty.calls if c[0] == "place"]  # never re-placed


def test_recovery_does_nothing_while_frozen(factory, monkeypatch):
    from optionsjourney.services import orders

    placed = []
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: placed.append(run_id))
    bot_id = make(factory, live=True)
    tasty = FakeTasty(entry="fill")
    d = deps_at(factory, OPEN_TIME, tasty)
    running_run(factory, bot_id, "bot-frozen", tasty.place_order("5WX01234", ORDER))
    orders.set_switch(factory, "freeze", True)
    bot_loops.reconcile(d)
    assert statuses(factory) == [("09:47", "running")] and placed == []
    orders.set_switch(factory, "freeze", False)
    bot_loops.reconcile(d)
    assert statuses(factory) == [("09:47", "filled")] and len(placed) == 1


def test_recovery_alerts_when_the_exits_fail_to_place(factory, monkeypatch):
    def broken(deps, run_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_entry, "place_protections", broken)
    bot_id = make(factory, live=True)
    tasty = FakeTasty(entry="fill")
    d = deps_at(factory, OPEN_TIME, tasty)
    alerts = []
    d.notify = alerts.append
    running_run(factory, bot_id, "bot-exitfail", tasty.place_order("5WX01234", ORDER))
    bot_loops.reconcile(d)  # doesn't raise
    assert statuses(factory) == [("09:47", "filled")]
    assert any("exits not placed" in a.title for a in alerts)


def test_recovery_waits_while_a_replace_of_the_run_is_unclear(factory, caplog):
    bot_id = make(factory, live=True)
    tasty = FakeTasty(entry="work")
    d = deps_at(factory, OPEN_TIME, tasty)
    old = tasty.place_order("5WX01234", ORDER)
    tasty.cancel_order("5WX01234", old)  # replaced at the broker...
    run_id = running_run(factory, bot_id, "bot-unclear", None)
    log_row(factory, bot_id=bot_id, bot_run_id=run_id, broker_order_id=old)
    # ...by a replacement whose answer never came back
    order = Order((Leg(LEG["symbol"], Side.SELL),), 1, True, price=Decimal("1.00")).to_dict()
    log_row(factory, bot_id=bot_id, bot_run_id=run_id, action="replace", ok=False,
            result="Unclear: timed out", replaces_id=old, order={"order": order})  # fmt: skip
    with caplog.at_level(logging.ERROR):
        bot_loops.reconcile(d)
    assert not caplog.records  # it waited; it didn't break
    assert statuses(factory) == [("09:47", "running")]  # not called missed
    assert not [c for c in tasty.calls if c[0] == "cancel" and c[1] != old]


def test_recovery_cancels_an_orphan_at_most_twice_then_needs_you(factory):
    bot_id = make(factory, live=True)
    tasty = FakeTasty(entry="work")
    tasty.cancel_order = lambda account, order_id: tasty.calls.append(("cancel", order_id))
    d = deps_at(factory, OPEN_TIME, tasty)
    alerts = []
    d.notify = alerts.append
    order_id = tasty.place_order("5WX01234", ORDER)
    running_run(factory, bot_id, "bot-stuck", order_id)
    for _ in range(4):
        bot_loops.reconcile(d)
    assert len([c for c in tasty.calls if c[0] == "cancel"]) == 2
    assert len(alerts) == 1 and "still working" in alerts[0].detail


def test_startup_recovery_runs_every_step(factory, monkeypatch):
    seen = []
    monkeypatch.setattr(bot_loops, "reconcile", lambda d: seen.append("runs"))
    monkeypatch.setattr(bot_loops, "check_exits", lambda d: 1 / 0)  # never stops the rest
    monkeypatch.setattr(bot_loops.bot_exits, "recover_targets", lambda d: seen.append("targets"))
    monkeypatch.setattr(bot_loops.bot_watched_stops, "recover", lambda d: seen.append("watched"))
    bot_loops.recover(deps_at(factory, OPEN_TIME))
    assert seen == ["runs", "targets", "watched"]


def test_recovery_settles_a_tranche_left_running_by_its_own_order(factory, monkeypatch):
    placed = []
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: placed.append(run_id))
    bot_id = make(factory, live=True)
    with factory() as session:
        bot = session.get(Bot, bot_id)
        bot.contracts, bot.max_per_trade, bot.max_per_day = 3, 3, 20
        session.commit()
    d = deps_at(factory, OPEN_TIME)
    d.tasty.fills = [1, 1]  # 1 of 3, then 1 of the 2 left
    d.tasty.stuck = {"102"}  # the tranche's cancel isn't confirmed: it's left running
    root_id = bot_entry.fire(d, bot_id, "09:47")
    with factory() as session:
        child_id = session.scalar(select(BotRun.id).where(BotRun.parent_run_id == root_id))
    assert placed == [root_id] and statuses(factory) == [("09:47", "filled"), ("09:47", "running")]
    d.tasty.stuck.clear()  # the broker takes the next cancel
    bot_loops.reconcile(d)
    with factory() as session:
        child = session.get(BotRun, child_id)
        assert (child.status, child.contracts, child.broker_order_id) == ("partial", 1, "102")
    assert placed == [root_id, child_id]
    assert statuses(factory)[0] == ("09:47", "filled")  # the root is left as it was


def test_recovery_never_records_legs_that_filled_unevenly(factory, monkeypatch):
    placed = []
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: placed.append(run_id))
    bot_id = make(factory, live=True)
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    put = "SPXW  261013P05690000"
    body = {
        "order-type": "Limit",
        "legs": [
            {"symbol": LEG["symbol"], "quantity": 2, "action": "Sell to Open"},
            {"symbol": put, "quantity": 2, "action": "Sell to Open"},
        ],
    }
    order_id = d.tasty.place_order("5WX01234", body)
    found = d.tasty.orders[order_id]  # cancelled with the call filled 1 and the put 0
    legs = (replace(found.legs[0], remaining=1, fill_price=Decimal("2.5")), found.legs[1])
    d.tasty.orders[order_id] = replace(found, status=OrderStatus.CANCELLED, legs=legs)
    put_leg = {"symbol": put, "ratio": 1, "side": "sell", "group": 1}
    run_id = running_run(factory, bot_id, "bot-uneven", order_id, legs=[LEG, put_leg])
    bot_loops.reconcile(d)
    bot_loops.reconcile(d)
    with factory() as session:
        run = session.get(BotRun, run_id)
        assert run.status == "running" and run.filled_legs == [] and run.contracts == 0
        assert run.reason.startswith(bot_loops.NEEDS_YOU) and "unevenly" in run.reason
    assert placed == [] and len([a for a in alerts if "needs you" in a.title]) == 1


def test_a_run_flagged_still_working_is_recorded_and_protected_once_it_fills(factory, monkeypatch):
    placed = []
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: placed.append(run_id))
    bot_id = make(factory, live=True)
    tasty = FakeTasty(entry="work")
    tasty.cancel_order = lambda account, order_id: tasty.calls.append(("cancel", order_id))
    d = deps_at(factory, OPEN_TIME, tasty)
    alerts = []
    d.notify = alerts.append
    order_id = tasty.place_order("5WX01234", ORDER)
    run_id = running_run(factory, bot_id, "bot-stuck-fills", order_id)
    for _ in range(3):
        bot_loops.reconcile(d)
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith(bot_loops.NEEDS_YOU)
    assert len(alerts) == 1 and "still working" in alerts[0].detail and placed == []
    tasty.fill(order_id, Decimal("2.5"))  # the order the app couldn't cancel fills after all
    bot_loops.reconcile(d)
    with factory() as session:
        run = session.get(BotRun, run_id)
        assert (run.status, run.contracts, run.broker_order_id) == ("filled", 1, order_id)
    assert placed == [run_id] and len(alerts) == 1  # protected, with no second alert


def test_a_bot_with_only_dollar_stops_still_counts_as_having_exits(factory):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    usd = tuple(replace(g, stop_pct=None, stop_unit="usd", stop_usd=Decimal("2.00"))
                for g in bot.groups)  # fmt: skip
    reshape(factory, bot_id, groups=usd)
    filled_run(factory, bot_id, "bot-usd-noexit")
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    bot_loops.check_exits(d)
    assert len(alerts) == 1 and "exits may be missing" in alerts[0].detail


# Slice 5: named date lists on the clock


def specific_fomc_bot(factory):
    bot_id = make(factory)
    bot = bot_store.load(factory, bot_id)
    _, problems = bot_store.save(
        factory,
        replace(bot, frequency="specific_dates", specific_date_sets=("FOMC",)),
        OPEN_TIME,
    )
    assert problems == {}
    return bot_id


def test_an_unavailable_specific_list_adds_no_day(factory):
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    bot_loops._lists_alerted.clear()
    specific_fomc_bot(factory)
    economic_events.FIRST_CHECK_DONE.clear()
    bot_loops.clock_pass(d, spawn=Inline)
    assert alerts == []  # the first calendar check hasn't finished: no alert yet
    economic_events.FIRST_CHECK_DONE.set()
    bot_loops.clock_pass(d, spawn=Inline)
    bot_loops.clock_pass(d, spawn=Inline)
    assert statuses(factory) == []  # not one of its days: no run at all
    titles = [a.title for a in alerts]
    assert titles == ["FOMC dates unavailable today"]
    assert "IC" in alerts[0].detail


def test_a_specific_list_day_fires(factory):
    from optionsjourney.db.models import EconomicEvent, EconomicEventSource

    with factory() as session:
        session.add(EconomicEvent(event_date=OPEN_TIME.date(), title="FOMC", impact="high",
                                  key="FOMC", source="fed"))  # fmt: skip
        session.add(EconomicEventSource(source="fed", refreshed_at=OPEN_TIME))
        session.commit()
    specific_fomc_bot(factory)
    bot_loops.clock_pass(deps_at(factory, OPEN_TIME), spawn=Inline)
    assert statuses(factory) == [("09:47", "filled")]


def test_a_fed_day_forexfactory_lacks_alerts_once(factory):
    from optionsjourney.db.models import EconomicEvent, EconomicEventSource

    with factory() as session:
        session.add(EconomicEvent(event_date=OPEN_TIME.date(), title="CPI m/m", impact="high",
                                  key="CPI", source="forexfactory"))  # fmt: skip
        session.add(EconomicEvent(event_date=OPEN_TIME.date(), title="FOMC", impact="high",
                                  key="FOMC", source="fed"))  # fmt: skip
        session.add(EconomicEventSource(source="fed", refreshed_at=OPEN_TIME))
        session.add(EconomicEventSource(source="forexfactory", refreshed_at=OPEN_TIME))
        session.commit()
    bot_id = make(factory)
    bot = bot_store.load(factory, bot_id)
    bot_store.save(factory, replace(bot, use_blackout_days=True, blackout_date_sets=("FOMC",)),
                   OPEN_TIME)  # fmt: skip
    alerts = []
    d = deps_at(factory, OPEN_TIME)
    d.notify = alerts.append
    bot_loops._lists_alerted.clear()
    economic_events.FIRST_CHECK_DONE.set()
    bot_loops.clock_pass(d, spawn=Inline)
    bot_loops.clock_pass(d, spawn=Inline)
    titles = [a.title for a in alerts if "ForexFactory" in a.title]
    assert titles == ["ForexFactory is missing an FOMC day"]
    assert statuses(factory) == [("09:47", "skipped")]  # the Fed's FOMC day still blacks out


def test_the_monitor_runs_each_step_on_its_own_and_ends_with_integrity(monkeypatch):
    ran = []

    def step(name, fails=False):
        def run(deps):
            ran.append(name)
            if fails:
                raise RuntimeError(name)

        return run

    monkeypatch.setattr(bot_loops, "reconcile", step("reconcile", fails=True))
    monkeypatch.setattr(bot_loops, "check_exits", step("check_exits"))
    monkeypatch.setattr(bot_loops.manual_attach, "pending_pass", step("manage with"))
    monkeypatch.setattr(bot_loops.bot_exits, "monitor_pass", step("monitor"))
    monkeypatch.setattr(bot_loops.bot_manage, "recover", step("exits off", fails=True))
    monkeypatch.setattr(bot_loops.bot_timed, "timed_pass", step("timed", fails=True))
    monkeypatch.setattr(bot_loops.bot_timed, "actions_pass", step("actions", fails=True))
    monkeypatch.setattr(bot_loops.bot_integrity, "check", step("integrity"))
    bot_loops._monitor(object())
    assert ran == [
        "reconcile",
        "check_exits",
        "manage with",
        "monitor",
        "exits off",
        "timed",
        "actions",
        "integrity",
    ]


def test_the_watch_loop_runs_the_targets_then_the_watch_rules_each_on_its_own(monkeypatch):
    ran = []

    def step(name, fails=False):
        def run(deps):
            ran.append(name)
            if fails:
                raise RuntimeError(name)

        return run

    monkeypatch.setattr(bot_loops.bot_exits, "watch_pass", step("targets", fails=True))
    monkeypatch.setattr(bot_loops.bot_watched_stops, "watch_pass", step("watched stops"))
    monkeypatch.setattr(bot_loops.bot_watch, "watch_pass", step("rules"))
    bot_loops._watch(object())
    assert ran == ["targets", "watched stops", "rules"]


# #204 (Tracker2 03669e16): a slow pass logs how long each of its steps took


def _steps_taking(monkeypatch, durations):
    """Every watch step a no-op; the loop's clock moves on by each step's duration."""
    now = [100.0]
    left = list(durations)

    def tick():
        return now[0]

    def step(deps):
        now[0] += left.pop(0)

    monkeypatch.setattr(bot_loops, "_clock", tick)
    for name in ("bot_exits", "bot_watched_stops", "bot_watch", "bot_paper_exits"):
        monkeypatch.setattr(getattr(bot_loops, name), "watch_pass", step)


def test_a_slow_watch_pass_logs_each_steps_time(monkeypatch, caplog):
    _steps_taking(monkeypatch, [0.2, 0.1, 3.6, 0.3])
    with caplog.at_level(logging.WARNING, logger=bot_loops.__name__):
        bot_loops._watch(object())
    [record] = [r for r in caplog.records if "took" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        "bot watch pass took 4.2s: targets=0.2s, watched stops=0.1s, watch rules=3.6s, paper=0.3s"
    )


def test_a_quick_pass_logs_nothing(monkeypatch, caplog):
    _steps_taking(monkeypatch, [0.2, 0.1, 3.0, 0.3])  # 3.6 s: under SLOW_PASS
    with caplog.at_level(logging.WARNING, logger=bot_loops.__name__):
        bot_loops._watch(object())
    assert not [r for r in caplog.records if "took" in r.getMessage()]
    assert bot_loops.SLOW_PASS == 4.0


def test_a_slow_monitor_pass_logs_too_and_counts_a_failing_step(monkeypatch, caplog):
    now = [0.0]
    monkeypatch.setattr(bot_loops, "_clock", lambda: now[0])

    def slow(deps):
        now[0] += 5.0
        raise RuntimeError("broker timed out")

    monkeypatch.setattr(bot_loops, "reconcile", slow)
    with caplog.at_level(logging.WARNING, logger=bot_loops.__name__):
        bot_loops._monitor(object())
    [record] = [r for r in caplog.records if "took" in r.getMessage()]
    assert record.getMessage().startswith("bot monitor pass took 5.0s: runs=5.0s, ")


# Slice 10: the floating entry window


class Spawned:
    """Records what the clock would start, without running it."""

    started: list = []

    def __init__(self, target, args=(), **_):
        self.call = (target.__name__, args[1:])

    def start(self):
        Spawned.started.append(self.call)


def floating_bot(factory, start="09:45", end="09:50", **more):
    bot_id = make(factory)
    reshape(factory, bot_id, use_floating_entry=True, floating_start=start, floating_end=end,
            **more)  # fmt: skip
    return bot_id


def test_the_clock_starts_a_floating_bot_once_a_minute_inside_its_window(factory, monkeypatch):
    monkeypatch.setattr(bot_loops, "_floating_started", {})
    Spawned.started = []
    bot_id = floating_bot(factory)
    base = OPEN_TIME.replace(minute=44, second=0)  # 09:44 New York
    for seconds in range(0, 8 * 60, 10):  # 09:44:00 to 09:51:50, every 10 s like the clock
        bot_loops.clock_pass(deps_at(factory, base + timedelta(seconds=seconds)), spawn=Spawned)
    minutes = ["09:45", "09:46", "09:47", "09:48", "09:49", "09:50"]
    assert Spawned.started == [("fire_floating", (bot_id, m)) for m in minutes]
    assert statuses(factory) == []  # never its entry time (09:47), never "missed its time"


def test_a_floating_bot_on_a_day_it_doesnt_run_is_never_started(factory, monkeypatch):
    monkeypatch.setattr(bot_loops, "_floating_started", {})
    Spawned.started = []
    floating_bot(factory, frequency="weekly", entry_days=(1,))  # OPEN_TIME is a Tuesday
    bot_loops.clock_pass(deps_at(factory, OPEN_TIME), spawn=Spawned)
    assert Spawned.started == []


def test_a_floating_bot_enters_through_the_clock(factory, monkeypatch):
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: None)
    monkeypatch.setattr(bot_loops, "_floating_started", {})
    floating_bot(factory)
    d = deps_at(factory, OPEN_TIME)  # 09:47:30
    bot_loops.clock_pass(d, spawn=Inline)
    bot_loops.clock_pass(d, spawn=Inline)
    assert statuses(factory) == [("09:47", "filled")]


def test_the_login_loop_reminds_about_schwabs_login(factory):
    """Bots slice 11: the reminders go through the bots' alert sender."""
    from types import SimpleNamespace

    seen = []
    login = SimpleNamespace(schwab_login_expires_at=lambda: OPEN_TIME + timedelta(hours=3))
    services = SimpleNamespace(session_factory=factory, credentials=login, settings=None)
    deps = SimpleNamespace(now=lambda: OPEN_TIME, notify=seen.append)
    bot_loops.login_pass(services, deps)
    assert [e.title for e in seen] == ["Schwab login needs you: it runs out in 3 hours"]
    assert "bots" not in seen[0].detail  # no Schwab bot, Schwab bots off (review M-1)


def test_a_bot_switched_off_by_a_migration_is_alerted_once_and_cleared_when_armed(factory):
    """Decision 0054: migration 0034's notice is alerted at startup, once, and shown on the
    bot's page until it's armed again."""
    bot_id = make(factory, live=True, enabled=False)
    with factory() as session:
        session.get(Bot, bot_id).notice = "Switched off: its stops are watched now."
        session.commit()
    seen = []
    deps = deps_at(factory, OPEN_TIME)
    deps.notify = seen.append
    assert bot_loops.send_notices(deps) == 1 and bot_loops.send_notices(deps) == 0
    assert [(e.title, e.detail, e.bot_id) for e in seen] == [
        ("IC was switched off", "Switched off: its stops are watched now.", bot_id)
    ]
    assert bot_store.notice_of(factory, bot_id) == "Switched off: its stops are watched now."
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    assert bot_store.notice_of(factory, bot_id) is None


def test_the_filters_read_tastytrade_then_schwab(factory):
    from types import SimpleNamespace

    from optionsjourney.services import market_feed

    services = SimpleNamespace(session_factory=factory, broker_clients=lambda: [],
                               quote_hub=None, chain=None)  # fmt: skip
    feed = bot_loops.deps_for(services).market.feed
    assert isinstance(feed, market_feed.FallbackFeed)
    assert [type(f) for f in feed.feeds] == [market_feed.TastyFeed, market_feed.SchwabFeed]
