"""The director: on the demo app, with the clock moved by skips (each one steps the
engine, and the engine steps the director) and the bots' own passes run as the loops
would run them."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from tests.demo.conftest import (  # noqa: F401 -- shared helpers
    _advance,
    _arm,
    _bot_id,
    _condor_run,
    _deps,
    _enter_condor,
    _Inline,
    _parts,
    _prime_quotes,
)

from optionsjourney.bots.day_plan import NO_RUN
from optionsjourney.core.market import market_date
from optionsjourney.core.positions import Status
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.demo.book import TASTY_ACCOUNT
from optionsjourney.demo.director import OVERSHOOT, TOUR_DRIFT, Director
from optionsjourney.demo.history import CALENDAR, CALENDAR_DAY, CONDOR
from optionsjourney.services import app_alerts, bot_loops, bot_store, journal
from optionsjourney.trading.orders import OrderStatus
from optionsjourney.web.routes import automation


def _exits(s, run_id, kind):
    with s.session_factory() as session:
        return list(
            session.scalars(select(BotExit).where(BotExit.run_id == run_id, BotExit.kind == kind))
        )


def _filled_stop(s, run_id):
    return next((x for x in _exits(s, run_id, "stop") if x.state == "filled"), None)


def test_the_demo_app_has_a_director(demo_app):
    s, demo = _parts(demo_app)
    assert isinstance(demo.director, Director)
    assert demo.engine.director is demo.director


def _filled_at(demo, order_id):
    """When the pretend broker filled the order."""
    stamp = demo.tasty.book.orders[order_id]["terminal-at"]
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _status(demo, order_id):
    return demo.tasty.get_order(TASTY_ACCOUNT, order_id).status


def _put_stop(s, run_id):
    return next(x for x in _exits(s, run_id, "stop") if "P" in x.anchor_symbol[12:13])


def test_condor_is_stopped_about_a_minute_after_entry(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    run = _enter_condor(s, deps)
    assert {x.state for x in _exits(s, run.id, "stop")} == {"working"}

    _advance(s, deps, 100)

    stop = _filled_stop(s, run.id)
    assert stop is not None, [(x.group_position, x.state) for x in _exits(s, run.id, "stop")]
    assert parse_occ(stop.anchor_symbol)[1].put_call == "CALL"  # always the call side: up
    assert _status(demo, stop.broker_order_id) is OrderStatus.FILLED
    took = _filled_at(demo, stop.broker_order_id) - run.filled_at
    assert timedelta(seconds=45) <= took <= timedelta(seconds=95), took
    # The stopped side is closed in the journal (a condor's sides are its own trades)
    key = f"tastytrade:{TASTY_ACCOUNT}:{run.broker_order_id}#call"
    view = journal.load_journal(s.session_factory, now=demo.clock.now())
    entry = next(e for e in view.entries if e.position.key == key)
    assert entry.position.status is Status.CLOSED
    # ... and the stop shows in the app's alerts
    assert any("stop" in a.title.lower() for a in app_alerts.recent(s.session_factory))


def test_the_condor_push_is_always_up_even_with_the_put_side_nearer(demo_app, monkeypatch):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    demo.engine.director = None  # the entry first, unstaged
    run = _enter_condor(s, deps)
    demo.market.steer(
        "SPX", demo.market.last("SPX") - 15, by=demo.market.now + timedelta(seconds=10)
    )
    demo.engine.step(demo.market.now + timedelta(seconds=11))  # now nearer the put short
    steers = _spy_steers(monkeypatch, demo.market)

    demo.director.step(demo.market.now)

    [(ticker, level, by)] = steers
    assert ticker == "SPX" and level > demo.market.last("SPX") + 20
    assert by >= run.filled_at + timedelta(seconds=60)


def test_director_follows_an_edited_stop(demo_app, monkeypatch):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    condor = bot_store.load(s.session_factory, _bot_id(s, CONDOR))
    groups = tuple(replace(g, stop_pct=Decimal(150)) for g in condor.groups)
    _, problems = bot_store.save(
        s.session_factory, replace(condor, groups=groups), demo.clock.now()
    )
    assert not problems
    steers = _spy_steers(monkeypatch, demo.market)
    run = _enter_condor(s, deps)
    stops = {x.group_position: x for x in _exits(s, run.id, "stop")}

    _advance(s, deps, 100)

    stop = _filled_stop(s, run.id)
    assert stop is not None and stop.log[0]["stop"] == "150"
    trigger = stops[stop.group_position].trigger
    # SPX was sent just past where the 150% stop's short reaches its trigger, once
    [(ticker, level, by)] = steers
    reached = level - OVERSHOOT
    mark = demo.market.option_mark_at(stop.anchor_symbol, reached, by,
                                      demo.market.vix_for_spx(reached))  # fmt: skip
    assert ticker == "SPX" and abs(mark - float(trigger)) < 0.5
    # A stop market fills at the mark of the tick that crossed its trigger
    assert trigger <= stop.fill_price <= trigger * Decimal("1.1")


def _spy_steers(monkeypatch, market):
    steers = []
    real = market.steer

    def steer(ticker, level, by):
        steers.append((ticker, level, by))
        real(ticker, level, by=by)

    monkeypatch.setattr(market, "steer", steer)
    return steers


def _calendar_target(s):
    with s.session_factory() as session:
        run_id = session.scalar(
            select(BotRun.id).where(
                BotRun.bot_id == _bot_id(s, CALENDAR),
                BotRun.trading_date == CALENDAR_DAY,
                BotRun.status == "filled",
            )
        )
    [target] = _exits(s, run_id, "target")
    return target


def _six_minutes_left(demo):
    left = demo.director.started_at + timedelta(minutes=6) - demo.clock.now()
    return int(left.total_seconds())


def test_calendar_hits_its_target_by_six_minutes(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    target = _calendar_target(s)
    assert target.state == "working"

    _advance(s, deps, _six_minutes_left(demo), every=5)

    assert _status(demo, target.broker_order_id) is OrderStatus.FILLED
    assert _calendar_target(s).state == "filled"


def test_the_condor_stop_leaves_the_calendar_short_of_its_target(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    run = _enter_condor(s, deps)

    _advance(s, deps, 100)

    assert _filled_stop(s, run.id) is not None
    assert _status(demo, _calendar_target(s).broker_order_id) is OrderStatus.WORKING


def test_the_calendar_scene_never_stops_the_condor_put_side(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    run = _enter_condor(s, deps)
    target = _calendar_target(s)

    _advance(s, deps, _six_minutes_left(demo))

    assert _status(demo, target.broker_order_id) is OrderStatus.FILLED
    put = _put_stop(s, run.id)
    assert put.state != "filled"  # still resting, or cancelled when its target filled
    assert _status(demo, put.broker_order_id) is not OrderStatus.FILLED


def test_tour_step_five_brings_the_calendar_forward(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    target = _calendar_target(s)
    _advance(s, deps, 60, every=5)
    assert _status(demo, target.broker_order_id) is OrderStatus.WORKING

    called = demo.clock.now()
    demo.director.on_tour_step(5)
    _advance(s, deps, 150, every=5)

    assert _status(demo, target.broker_order_id) is OrderStatus.FILLED
    # From the demo morning's level it's a longer way: no faster than a staged move may go
    took = _filled_at(demo, target.broker_order_id) - called
    assert took <= timedelta(seconds=150), took


def test_tour_step_five_after_the_condor_stop_fills_the_calendar_on_time(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    run = _enter_condor(s, deps)
    target = _calendar_target(s)
    _advance(s, deps, 100)
    assert _status(demo, target.broker_order_id) is OrderStatus.WORKING

    called = demo.clock.now()
    demo.director.on_tour_step(5)
    _advance(s, deps, 70)

    assert _status(demo, target.broker_order_id) is OrderStatus.FILLED
    took = _filled_at(demo, target.broker_order_id) - called
    assert took <= TOUR_DRIFT + timedelta(seconds=10), took
    assert demo.clock.now() < demo.director.started_at + timedelta(minutes=6)
    assert _put_stop(s, run.id).state != "filled"


def test_no_staged_minute_moves_spx_more_than_30_points(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    _enter_condor(s, deps)
    _advance(s, deps, _six_minutes_left(demo) + 60)

    since = int(demo.director.started_at.timestamp()) * 1000
    bars = [b for b in demo.market.candles("SPX", 1, 30) if b["time"] >= since]
    assert len(bars) >= 6
    widest = max(b["high"] - b["low"] for b in bars)
    assert widest <= 30, [(b["time"], round(b["high"] - b["low"], 1)) for b in bars]


def test_late_arm_is_harmless(demo_app, monkeypatch):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    _advance(s, deps, 400, every=10)  # the calendar's move is done
    steered = []
    monkeypatch.setattr(demo.market, "steer", lambda *a, **k: steered.append(a))
    demo.clock.to_close()
    _arm(s, CONDOR)

    bot_loops.clock_pass(deps, spawn=_Inline)
    bot_loops._monitor(deps)
    demo.engine.step(demo.clock.now())
    demo.director.step(demo.clock.now())

    assert _condor_run(s) is None
    with demo_app.app_context():
        plan = automation._schedule(market_date(demo.clock.now()), demo.clock.now())
    [row] = [r for r in plan.rows if r.bot_name == CONDOR]
    assert (row.state, row.note) == ("missed", NO_RUN)
    assert steered == []
