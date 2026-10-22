import threading
from datetime import datetime, timedelta

from sqlalchemy import func, select
from tests.demo.conftest import _spread

from optionsjourney.core.positions import Status
from optionsjourney.db.models import BrokerTransaction
from optionsjourney.demo.book import TASTY_ACCOUNT
from optionsjourney.demo.clock import CLOSE, DEMO_DAY, NY
from optionsjourney.services import journal, trade_events
from optionsjourney.trading.orders import OrderStatus

PUT_5600_WEEKLY = "SPXW  261023P05600000"
CLOSE_AT = datetime.combine(DEMO_DAY, CLOSE, tzinfo=NY)


def _demo(app):
    s = app.extensions["optionsjourney"]
    return s, s.demo


def _transactions(s) -> int:
    with s.session_factory() as session:
        return session.scalar(select(func.count(BrokerTransaction.id)))


def test_a_fill_becomes_a_trade_event(demo_app):
    s, demo = _demo(demo_app)
    before = _transactions(s)  # the starting account's history
    oid = demo.tasty.place_order(TASTY_ACCOUNT, _spread())
    demo.engine.step(demo.market.now + timedelta(seconds=3))

    assert demo.tasty.get_order(TASTY_ACCOUNT, oid).status is OrderStatus.FILLED
    assert _transactions(s) == before + 2  # the sync ran because something filled
    new = trade_events.record(s.session_factory, demo.clock.now())
    assert [e.kind for e in new] == ["open"]


def test_a_step_without_a_fill_does_not_sync(demo_app, monkeypatch):
    s, demo = _demo(demo_app)
    from optionsjourney.demo import engine as engine_module

    calls = []
    monkeypatch.setattr(engine_module, "sync_all", lambda *a, **k: calls.append(a))
    demo.engine.step(demo.market.now + timedelta(seconds=5))
    assert calls == []


def test_a_step_moves_the_market_and_pushes_quotes(demo_app):
    s, demo = _demo(demo_app)
    s.quote_hub.subscribe({"SPX"})
    before = demo.market.now
    demo.engine.step(before + timedelta(seconds=10))
    assert demo.market.now == before + timedelta(seconds=10)
    quote = s.quote_hub.get("tastytrade", "SPX")
    assert quote is not None and abs(float(quote.last) - demo.market.last("SPX")) < 0.001


def test_skip_ahead_steps_the_engine_straight_away(demo_app):
    s, demo = _demo(demo_app)
    before = demo.market.now
    after = demo.clock.skip(15)
    assert after - before >= timedelta(minutes=15)
    assert abs((after - demo.market.now).total_seconds()) < 1  # whole seconds only


def test_at_the_close_day_orders_expire_and_gtc_orders_keep_working(demo_app):
    s, demo = _demo(demo_app)
    far = 50.0  # a credit the spread never reaches
    day = demo.tasty.place_order(TASTY_ACCOUNT, _spread("Limit", "Day", far))
    gtc = demo.tasty.place_order(
        TASTY_ACCOUNT, _spread("Limit", "GTC", far, PUT_5600_WEEKLY, "SPXW  261023P05590000")
    )
    after = demo.clock.to_close()
    assert after >= CLOSE_AT  # may be a few microseconds past 16:00
    assert demo.tasty.get_order(TASTY_ACCOUNT, day).status is OrderStatus.EXPIRED
    assert demo.tasty.get_order(TASTY_ACCOUNT, gtc).status is OrderStatus.WORKING


def test_a_0dte_open_at_1559_is_closed_in_the_journal_after_the_close(demo_app):
    s, demo = _demo(demo_app)
    oid = demo.tasty.place_order(TASTY_ACCOUNT, _spread())
    demo.engine.step(demo.market.now + timedelta(seconds=3))
    key = f"tastytrade:{TASTY_ACCOUNT}:{oid}"

    demo.clock.skip(379)  # 9:40 -> 15:59
    entry = _entry(s, key, demo.clock.now())
    assert entry.position.status is Status.OPEN

    demo.clock.to_close()
    entry = _entry(s, key, demo.clock.now())
    assert entry.position.status is Status.CLOSED
    assert demo.tasty.option_positions(TASTY_ACCOUNT) == {}


def _entry(s, key, now):
    view = journal.load_journal(s.session_factory, now=now)
    return next(e for e in view.entries if e.position.key == key)


def test_start_runs_the_engine_on_a_daemon_thread(demo_app, monkeypatch):
    s, demo = _demo(demo_app)
    stepped = threading.Event()
    monkeypatch.setattr(demo.engine, "step", lambda now: stepped.set())
    demo.engine.start()
    assert stepped.wait(3)
    thread = next(t for t in threading.enumerate() if t.name == "demo-engine")
    assert thread.daemon
