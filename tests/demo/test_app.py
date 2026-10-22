from dataclasses import replace

import pytest
from flask import render_template_string

from optionsjourney.app import create_app
from optionsjourney.brokers import guard
from optionsjourney.demo.feeds import DemoStream
from optionsjourney.services import (
    app_alerts,
    background,
    bot_entry,
    bot_loops,
    economic_events,
)
from optionsjourney.services.bot_loops import deps_for


def _quiet(services):
    """The bot's deps without queuing a phone notification."""
    return replace(deps_for(services), notify=lambda event: None)


def test_demo_app_has_pretend_clients_and_no_network(demo_app):
    s = demo_app.extensions["optionsjourney"]
    assert {c.name for c in s.broker_clients()} == {"TastyTrade", "Schwab"}
    assert s.settings.is_demo and s.demo is not None
    assert [type(st) for st in s.quote_hub.streams] == [DemoStream]
    assert guard.is_installed()  # the trading guard too, as in gateway mode


def test_demo_app_keeps_its_parts_together(demo_app):
    s = demo_app.extensions["optionsjourney"]
    d = s.demo
    assert d.tasty in s.broker_clients() and d.schwab in s.broker_clients()
    assert d.tasty.market is d.market and d.schwab.market is d.market
    assert d.stream in s.quote_hub.streams
    assert d.engine is not None and d.clock is not None


def test_demo_chart_feed_never_connects(demo_app):
    feed = demo_app.extensions["optionsjourney"].chart_feed
    assert feed is not None and feed._autostart is False


def test_pages_know_demo_mode(demo_app, app):
    with demo_app.test_request_context("/"):
        assert render_template_string("{{ demo_mode }}") == "True"
    with app.test_request_context("/"):
        assert render_template_string("{{ demo_mode }}") == "False"


def test_bot_alerts_show_in_the_app_in_demo_mode(demo_app):
    s = demo_app.extensions["optionsjourney"]
    deps = _quiet(s)
    assert deps.in_app_alerts is True
    bot_entry.alert(deps, "Stopped out", "detail", "/automation/1", run_id=1)
    assert app_alerts.unread_count(s.session_factory) == 1
    [row] = app_alerts.recent(s.session_factory)
    assert (row.kind, row.title, row.detail, row.link) == (
        "bot", "Stopped out", "detail", "/automation/1",
    )  # fmt: skip
    assert row.dedupe_key == "bot:1:Stopped out"


@pytest.fixture
def app(settings, fake_credentials):
    # Its own folder: a demo app's folder holds the demo database, which local mode refuses
    settings = replace(settings, data_dir=settings.data_dir / "local")
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app


def test_bot_alerts_stay_phone_only_outside_demo(app):
    s = app.extensions["optionsjourney"]
    deps = _quiet(s)
    assert deps.in_app_alerts is False
    bot_entry.alert(deps, "Stopped out", "detail", "/automation/1", run_id=1)
    assert app_alerts.unread_count(s.session_factory) == 0
    assert s.demo is None


def _record_starts(monkeypatch):
    started = []
    for module, name in (
        (background.backups, "start_scheduler"),
        (background.pl_day, "start"),
        (background.stock_history, "start"),
        (background.expiry_settle, "start"),
        (background.bot_loops, "start"),
        (background.auto_sync, "start"),
        (background.fill_sync, "start"),
    ):
        label = f"{module.__name__.rsplit('.', 1)[-1]}.{name}"
        monkeypatch.setattr(module, name, lambda *a, _l=label, **k: started.append(_l))
    monkeypatch.setattr(
        background, "start_alert_checker", lambda *a, **k: started.append("alert_checker")
    )
    return started


def test_start_all_skips_backups_and_fill_sync_in_demo(monkeypatch, demo_app):
    s = demo_app.extensions["optionsjourney"]
    started = _record_starts(monkeypatch)
    monkeypatch.setattr(s.demo.engine, "start", lambda: started.append("demo.engine"))
    background.start_all(s.settings.database_path, s)
    assert sorted(started) == sorted([
        "alert_checker", "pl_day.start", "stock_history.start", "expiry_settle.start",
        "bot_loops.start", "auto_sync.start", "demo.engine",
    ])  # fmt: skip


def test_start_all_is_unchanged_outside_demo(monkeypatch, app):
    s = app.extensions["optionsjourney"]
    started = _record_starts(monkeypatch)
    background.start_all(s.settings.database_path, s)
    assert "backups.start_scheduler" in started and "fill_sync.start" in started
    assert "demo.engine" not in started


def _loops_started(monkeypatch, services):
    names = []
    monkeypatch.setattr(bot_loops, "_loop", lambda name, seconds, body: names.append(name))
    monkeypatch.setattr(bot_loops, "recover", lambda deps: None)
    bot_loops.start(services)
    return names


def test_bot_loops_in_demo_have_no_calendar_or_schwab_login(monkeypatch, demo_app):
    economic_events.FIRST_CHECK_DONE.clear()
    names = _loops_started(monkeypatch, demo_app.extensions["optionsjourney"])
    assert "market-calendar" not in names and "schwab-login" not in names
    assert {"bot-clock", "bot-watch", "bot-monitor", "bot-audit"} <= set(names)
    # no calendar fetch will ever run, so the stored calendar counts as the first check
    assert economic_events.FIRST_CHECK_DONE.is_set()


def test_bot_loops_outside_demo_are_unchanged(monkeypatch, app):
    economic_events.FIRST_CHECK_DONE.clear()
    names = _loops_started(monkeypatch, app.extensions["optionsjourney"])
    assert "market-calendar" in names and "schwab-login" in names
    assert not economic_events.FIRST_CHECK_DONE.is_set()


def test_bot_filters_read_the_pretend_market(demo_app, caplog):
    """Final review I-3: the bots' feed in demo mode is the pretend Schwab's market data
    (no TastyTrade socket at all), and the VIX level, overnight move and VIX9D/VIX ratio
    filters read prices from it, during a clock pass too, with nothing blocked."""
    import logging
    from decimal import Decimal

    from sqlalchemy import select

    from optionsjourney.bots.definition import MoveLimits
    from optionsjourney.db.models import Bot
    from optionsjourney.services import bot_filters, bot_store, market_feed

    s = demo_app.extensions["optionsjourney"]
    deps = _quiet(s)
    feeds = deps.market.feed.feeds
    assert [type(f) for f in feeds] == [market_feed.SchwabFeed]
    now = deps.now()
    got = deps.market.read(market_feed.Want(trades=("VIX", "VIX9D"), summaries=("VIX",)), 1,
                           lambda g: True)  # fmt: skip
    assert got.source == "Schwab"
    assert set(got.trades) == {"VIX", "VIX9D"} and got.summaries["VIX"][1] > 0
    vix, vix9d = got.trades["VIX"][0], got.trades["VIX9D"][0]
    assert Decimal("0.85") < vix9d / vix < Decimal("1.0")

    with s.session_factory() as session:
        bot_id = session.scalar(select(Bot.id).where(Bot.name == "SPX 0DTE condor"))
    bot = replace(
        bot_store.load(s.session_factory, bot_id),
        use_vix_level=True, vix_min=Decimal(1), vix_max=Decimal(90),
        use_vix9d_ratio=True, vix9d_ratio_min=Decimal("0.5"), vix9d_ratio_max=Decimal(2),
        use_vix_overnight=True, vix_overnight=MoveLimits(max_up=Decimal(50), max_down=Decimal(50)),
    )  # fmt: skip
    caplog.set_level(logging.WARNING)
    assert bot_filters.check(deps.market, bot, now) is None
    bot_loops.clock_pass(deps, spawn=lambda *a, **k: _NoThread())
    blocked = [r for r in caplog.records if r.name == "optionsjourney.demo.offline"]
    assert blocked == [] and not [r for r in caplog.records if r.levelno >= logging.ERROR]


class _NoThread:
    def start(self):
        pass


def test_bots_read_tastytrade_first_outside_demo(app):
    from optionsjourney.services import market_feed

    feeds = _quiet(app.extensions["optionsjourney"]).market.feed.feeds
    assert [type(f) for f in feeds] == [market_feed.TastyFeed, market_feed.SchwabFeed]
