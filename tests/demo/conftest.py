from dataclasses import replace

import pytest
from sqlalchemy import select

from optionsjourney.app import create_app
from optionsjourney.brokers import guard
from optionsjourney.config import load_settings
from optionsjourney.core.market import market_date
from optionsjourney.db.models import Bot, BotRun
from optionsjourney.demo import offline
from optionsjourney.demo.clock import DemoClock
from optionsjourney.demo.history import CONDOR
from optionsjourney.services import bot_loops, bot_store
from optionsjourney.services.bot_loops import deps_for

PUT_5700 = "SPXW  261021P05700000"  # the demo day's 0DTE
PUT_5690 = "SPXW  261021P05690000"


@pytest.fixture(autouse=True)
def _real_clock_afterwards():
    yield
    DemoClock.stop()


@pytest.fixture
def demo_settings(tmp_path):
    return load_settings({"BROKER_CREDENTIALS": "demo", "OPTIONSJOURNEY_DATA_DIR": str(tmp_path)})


@pytest.fixture
def demo_app(demo_settings):
    """A demo app with no background threads (TESTING): tests drive engine.step."""
    app = create_app(demo_settings)
    app.config["TESTING"] = True
    yield app
    # create_app installed both guards for good; put the network back for other tests.
    # The trading guard first: it wraps the offline guard's send.
    guard._uninstall_for_tests()
    offline._uninstall_for_tests()


@pytest.fixture(autouse=True)
def _new_clock_memory(monkeypatch):
    """The clock's memory of the minutes it started is per process: each demo is new."""
    monkeypatch.setattr(bot_loops, "_floating_started", {})


class _Inline:
    """A thread that runs its target as it starts: the clock's entry runs in the test."""

    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def _parts(app):
    s = app.extensions["optionsjourney"]
    return s, s.demo


def _deps(s):
    """The loops' deps, with the bots' waits as skips of the demo clock (each skip steps
    the engine) and no phone notification."""
    return replace(
        deps_for(s),
        notify=lambda event: None,
        sleep=lambda seconds: s.demo.clock.skip(seconds / 60),
        background=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    )


def _bot_id(s, name):
    with s.session_factory() as session:
        return session.scalar(select(Bot.id).where(Bot.name == name))


def _prime_quotes(s):
    """The 0DTE chain priced in the quote hub, as the engine's loop keeps it."""
    demo = s.demo
    day = market_date(demo.clock.now()).isoformat()
    keys = {"SPX"}
    for item in demo.tasty.option_chain("SPX"):
        for expiration in item["expirations"]:
            if expiration["expiration-date"] == day:
                for strike in expiration["strikes"]:
                    keys |= {strike["call"], strike["put"]}
    s.quote_hub.subscribe(keys)
    demo.engine.step(demo.clock.now())


def _arm(s, name):
    bot_store.set_armed(s.session_factory, _bot_id(s, name), True, s.demo.clock.now())


def _enter_condor(s, deps):
    """Arm the condor and run the bots' clock until its entry fills and its exits rest."""
    _arm(s, CONDOR)
    _prime_quotes(s)
    bot_loops.clock_pass(deps, spawn=_Inline)
    bot_loops._monitor(deps)
    run = _condor_run(s)
    assert run is not None and run.status == "filled", run and (run.status, run.reason)
    return run


def _condor_run(s):
    with s.session_factory() as session:
        return session.scalar(
            select(BotRun)
            .where(
                BotRun.bot_id == _bot_id(s, CONDOR),
                BotRun.trading_date == market_date(s.demo.clock.now()),
                BotRun.status == "filled",
            )
            .order_by(BotRun.id.desc())
        )


def _advance(s, deps, seconds, every=2):
    """Demo time moves on in skips of `every` seconds; the watch pass runs each skip and
    the monitor every 30 s, as their loops would."""
    since_monitor = 0
    for _ in range(seconds // every):
        s.demo.clock.skip(every / 60)
        bot_loops._watch(deps)
        since_monitor += every
        if since_monitor >= 30:
            bot_loops._monitor(deps)
            since_monitor = 0
    bot_loops._monitor(deps)


def _spread(order_type="Market", tif="Day", price=None, first=PUT_5700, second=PUT_5690):
    body = {
        "time-in-force": tif,
        "order-type": order_type,
        "legs": [
            {"instrument-type": "Equity Option", "symbol": first, "quantity": 1,
             "action": "Sell to Open"},
            {"instrument-type": "Equity Option", "symbol": second, "quantity": 1,
             "action": "Buy to Open"},
        ],
    }  # fmt: skip
    if price is not None:
        body["price"], body["price-effect"] = f"{price:.2f}", "Credit"
    return body
