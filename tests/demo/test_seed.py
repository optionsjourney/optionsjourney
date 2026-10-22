from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.app import create_app
from optionsjourney.brokers import guard
from optionsjourney.config import ConfigError
from optionsjourney.core.market import market_date
from optionsjourney.core.metrics import pl_by_period
from optionsjourney.core.positions import Status
from optionsjourney.db import Base, make_engine, make_session_factory, migrate
from optionsjourney.db.models import (
    AccountValue,
    Bot,
    BotExit,
    BotRun,
    EconomicEvent,
    PaperFill,
    UserAlert,
    WatchlistGroup,
)
from optionsjourney.demo import offline, seed
from optionsjourney.demo.book import TASTY_ACCOUNT
from optionsjourney.demo.clock import DEMO_DAY
from optionsjourney.services import bot_store, economic_events, journal, manual_bots
from optionsjourney.services import setup as setup_service

NAMES = {"SPX 0DTE condor", "Weekly calendar", "QQQ put spread"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One seed, read by the tests that only look at it (a build takes a few seconds)."""
    folder = tmp_path_factory.mktemp("seed")
    books = seed.build(folder)
    engine = make_engine(folder / "optionsjourney.db")
    yield books, make_session_factory(engine), folder
    engine.dispose()


def _bots(factory) -> dict[str, Bot]:
    with factory() as session:
        return {b.name: b for b in session.scalars(select(Bot))}


def test_seed_has_the_three_bots_by_name(built):
    _, factory, _ = built
    bots = _bots(factory)
    assert set(bots) == NAMES
    condor = bot_store.load(factory, bots["SPX 0DTE condor"].id)
    assert (condor.live, condor.enabled) == (True, False)
    assert (condor.use_floating_entry, condor.floating_start, condor.floating_end) == (
        True, "09:40", "15:00",
    )  # fmt: skip
    assert {(g.target_pct, g.stop_pct, g.broker_exit) for g in condor.groups} == {
        (Decimal("50"), Decimal("200"), "stop")
    }
    assert sorted(abs(leg.target) for leg in condor.legs if leg.pick == "spot") == [60, 60]
    calendar = bot_store.load(factory, bots["Weekly calendar"].id)
    assert (calendar.live, calendar.enabled) == (True, True)
    assert [g.target_pct for g in calendar.groups] == [Decimal("25")]
    qqq = bot_store.load(factory, bots["QQQ put spread"].id)
    assert (qqq.live, qqq.enabled, qqq.underlying) == (False, True, "QQQ")


def test_seed_history_is_realistic(built):
    _, factory, _ = built
    view = journal.load_journal(factory, now=datetime.combine(DEMO_DAY, datetime.min.time(), UTC))
    closed = [e for e in view.entries if e.position.status is Status.CLOSED]
    opened = sorted(market_date(e.position.opened_at) for e in closed)
    assert timedelta(weeks=12) <= opened[-1] - opened[0] <= timedelta(weeks=14)
    assert opened[-1] < DEMO_DAY
    assert {e.position.broker for e in closed} == {"tastytrade", "schwab"}
    metrics = journal.load_metrics(view, "week")
    assert Decimal("60") <= metrics.trades.win_rate <= Decimal("72")
    weeks = pl_by_period(journal.pl_by_day(view), "week")
    assert sum(1 for pl in weeks.values() if pl < 0) >= 2
    tags = {t.name for e in closed for t in e.tags}
    assert {"Iron Condors", "Credit Spreads", "Calendars", "Earnings"} <= tags
    assert sum(1 for e in closed if e.notes) >= 3
    # Schwab: stock trades and covered calls
    schwab = [e for e in closed if e.position.broker == "schwab"]
    assert any(e.position.underlying == "AAPL" and "Covered Calls" in {t.name for t in e.tags}
               for e in schwab)  # fmt: skip
    with factory() as session:
        values = list(session.scalars(select(AccountValue)))
    assert {v.broker for v in values} == {"tastytrade", "schwab"}
    assert max(v.day for v in values) < DEMO_DAY and len(values) > 100


def test_seed_past_runs_are_real_runs_linked_to_their_trades(built):
    _, factory, _ = built
    bots = _bots(factory)
    view = journal.load_journal(factory, now=datetime.combine(DEMO_DAY, datetime.min.time(), UTC))
    by_run = {e.bot.run_id for e in view.entries if e.bot is not None}
    with factory() as session:
        # The condor trades Mondays, Wednesdays and Fridays; the calendar Mondays and Thursdays
        for name, (low, high) in (("SPX 0DTE condor", (34, 40)), ("Weekly calendar", (17, 24))):
            runs = list(session.scalars(select(BotRun).where(BotRun.bot_id == bots[name].id)))
            past = [r for r in runs if r.trading_date < date(2026, 10, 19)]
            assert low <= len(past) <= high, name
            assert all(r.status == "filled" and r.live and r.broker_order_id for r in past)
            assert {r.id for r in past} <= by_run  # each opens its journal trade
            exits = list(session.scalars(
                select(BotExit).where(BotExit.run_id.in_([r.id for r in past]))
            ))  # fmt: skip
            kinds = {(x.kind, x.state) for x in exits}
            assert ("target", "filled") in kinds
            assert any(k in kinds for k in (("stop", "filled"), ("timed", "filled")))
        paper = list(session.scalars(
            select(BotRun).where(BotRun.bot_id == bots["QQQ put spread"].id)
        ))  # fmt: skip
        assert paper and all(r.paper and not r.live for r in paper)
        fills = list(session.scalars(select(PaperFill)))
        assert {f.kind for f in fills} >= {"entry", "exit"}


def test_seed_has_the_open_calendar_and_its_target(built):
    books, factory, _ = built
    held = books.tasty.options
    assert len(held) == 2 and sorted(held.values()) == [-1, 1]
    assert all(k.startswith("SPXW") and k[-9] == "P" for k in held)
    calendar = _bots(factory)["Weekly calendar"]
    with factory() as session:
        [run] = session.scalars(
            select(BotRun).where(BotRun.bot_id == calendar.id,
                                 BotRun.trading_date == DEMO_DAY - timedelta(days=2))
        )  # fmt: skip
        [target] = session.scalars(
            select(BotExit).where(BotExit.run_id == run.id, BotExit.kind == "target")
        )
    assert run.status == "filled" and run.fill_credit < 0  # a debit
    assert target.state == "working" and target.broker_order_id
    order = books.tasty.orders[target.broker_order_id]
    assert order["status"] == "Live" and order["time-in-force"] == "GTC"
    # The exit math signs a value to close at like a fill: a calendar's target is a credit
    assert Decimal(order["price"]) == -target.limit and order["price-effect"] == "Credit"
    assert abs(-target.limit - -run.fill_credit * Decimal("1.25")) <= Decimal("0.05")
    view = journal.load_journal(factory, now=datetime.combine(DEMO_DAY, datetime.min.time(), UTC))
    [entry] = [e for e in view.entries if e.position.status is Status.OPEN and e.bot]
    assert entry.bot is not None and entry.bot.run_id == run.id
    assert entry.position.account_number == TASTY_ACCOUNT


def test_seed_is_identical_each_time(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    books_a, books_b = seed.build(first), seed.build(second)
    assert _dump(first) == _dump(second)
    assert books_a.tasty.orders == books_b.tasty.orders
    assert books_a.tasty.transactions == books_b.tasty.transactions
    assert books_a.schwab.transactions == books_b.schwab.transactions


def _dump(folder) -> dict:
    engine = make_engine(folder / "optionsjourney.db")
    try:
        with engine.connect() as connection:
            return {
                table.name: [tuple(row) for row in connection.execute(
                    table.select().order_by(*table.primary_key.columns)
                )]
                for table in Base.metadata.sorted_tables
            }  # fmt: skip
    finally:
        engine.dispose()


def test_seed_calendar_has_nothing_on_the_demo_day(built):
    _, factory, _ = built
    with factory() as session:
        rows = list(session.scalars(select(EconomicEvent)))
    assert not [r for r in rows if r.event_date == DEMO_DAY]
    in_season = [r for r in rows if date(2026, 10, 1) <= r.event_date <= date(2026, 11, 30)]
    keys = {r.key for r in in_season}
    assert {"FOMC", "CPI"} <= keys
    assert any("claims" in r.title.lower() for r in in_season)
    # The monthly jobs report (payrolls), on its first Friday
    assert [(r.event_date, r.key) for r in in_season if "Non-Farm" in r.title] == [
        (date(2026, 11, 6), None)
    ]
    now = datetime.combine(DEMO_DAY, seed.SEEDED_AT, seed.NY)
    # The bots' date lists can tell, offline: the demo day is on neither
    assert economic_events.lists_for(factory, ["FOMC", "CPI"], DEMO_DAY, now) == {
        "FOMC": False, "CPI": False,
    }  # fmt: skip


def test_seed_has_a_watchlist_alerts_and_setup_done(built):
    _, factory, _ = built
    with factory() as session:
        [group] = session.scalars(select(WatchlistGroup))
        alerts = list(session.scalars(select(UserAlert)))
    assert group.name == "Demo watchlist"
    assert group.symbols == ["SPX", "QQQ", "IWM", "AAPL", "MSFT", "NVDA"]
    assert len(alerts) == 2
    assert setup_service.is_complete(factory) and setup_service.risk_accepted(factory)


def test_seed_skips_setup(demo_app):
    client = demo_app.test_client()
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].endswith("/journal")


def test_seed_pages_render(demo_app):
    s = demo_app.extensions["optionsjourney"]
    client = demo_app.test_client()
    assert client.get("/journal").status_code == 200
    with s.session_factory() as session:
        runs = list(session.scalars(select(BotRun)))
    for bot_id in {r.bot_id for r in runs}:
        assert client.get(f"/automation/{bot_id}/runs").status_code == 200
    for run in runs[:3] + runs[-3:]:
        assert client.get(f"/automation/runs/{run.id}").status_code == 200, run.id


def test_demo_app_starts_the_brokers_from_the_seed(demo_app):
    d = demo_app.extensions["optionsjourney"].demo
    assert len(d.tasty.book.options) == 2
    assert d.tasty.book.transactions and d.schwab.book.transactions
    assert any(raw["status"] == "Live" for raw in d.tasty.book.orders.values())


@pytest.fixture
def guards_off_afterwards():
    yield
    guard._uninstall_for_tests()  # first: it wraps the offline guard's send
    offline._uninstall_for_tests()


def test_demo_refuses_a_real_database_and_leaves_it_alone(demo_settings, guards_off_afterwards):
    engine = make_engine(demo_settings.database_path)
    migrate(engine)
    engine.dispose()
    before = demo_settings.database_path.read_bytes()
    with pytest.raises(ConfigError, match="demo"):
        create_app(demo_settings)
    assert demo_settings.database_path.read_bytes() == before


def test_demo_starts_again_on_its_own_database(demo_settings, guards_off_afterwards):
    create_app(demo_settings)
    app = create_app(demo_settings)  # the same folder: a demo database, so accepted
    d = app.extensions["optionsjourney"].demo
    assert len(d.tasty.book.options) == 2


def test_the_command_says_why_it_wont_start_on_a_real_database(
    demo_settings, guards_off_afterwards, monkeypatch
):
    from optionsjourney import __main__ as entry  # noqa: PLC0415

    engine = make_engine(demo_settings.database_path)
    migrate(engine)
    engine.dispose()
    monkeypatch.setattr(entry, "load_settings", lambda: demo_settings)
    monkeypatch.setattr(entry.logs, "start_console", lambda: None)  # no handlers left behind
    monkeypatch.setattr(entry.logs, "start_file", lambda folder: None)
    with pytest.raises(SystemExit, match="Settings problem: .*isn't a demo database"):
        entry.main()


def _rows(factory) -> tuple:
    with factory() as session:
        runs = [(r.id, r.status, r.reason, r.updated_at) for r in session.scalars(select(BotRun))]
        exits = [(x.id, x.state, x.broker_order_id, x.updated_at)
                 for x in session.scalars(select(BotExit))]  # fmt: skip
    return runs, exits


def test_startup_recovery_and_the_monitor_find_nothing_wrong_with_the_seed(demo_app):
    """#266: the seeded closed calendars aren't held trades; nothing needs a look."""
    from dataclasses import replace  # noqa: PLC0415

    from optionsjourney.db.models import AppAlert  # noqa: PLC0415
    from optionsjourney.services import bot_loops  # noqa: PLC0415

    s = demo_app.extensions["optionsjourney"]
    sent = []
    deps = replace(bot_loops.deps_for(s), notify=sent.append)
    before = _rows(s.session_factory)
    bot_loops.recover(deps)
    bot_loops._monitor(deps)  # 09:40: the pre-open check of the held calendar runs too
    with s.session_factory() as session:
        stored = [a.title for a in session.scalars(select(AppAlert))]
    # Only the routine all-clear of the held trades' check (the open calendar)
    assert [e.title for e in sent] == ["Held trades checked"]
    assert stored == ["Held trades checked"]
    assert _rows(s.session_factory) == before


def test_a_demo_folder_never_opens_as_a_real_app(tmp_path, settings, fake_credentials):
    """Its risk boxes are ticked and a Live bot is armed: local (or gateway) mode refuses
    it before writing anything."""
    seed.build(settings.data_dir)
    before = settings.database_path.read_bytes()
    with pytest.raises(ConfigError, match="demo database"):
        create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    assert settings.database_path.read_bytes() == before
    assert sorted(p.name for p in settings.data_dir.iterdir()) == ["optionsjourney.db"]


def test_the_command_refuses_a_demo_folder_outside_demo_mode(settings, monkeypatch):
    import flask  # noqa: PLC0415

    from optionsjourney import __main__ as entry  # noqa: PLC0415
    from optionsjourney.services import background  # noqa: PLC0415

    seed.build(settings.data_dir)
    monkeypatch.setattr(background, "start_all", lambda *a: None)
    monkeypatch.setattr(flask.Flask, "run", lambda *a, **k: pytest.fail("it started"))
    monkeypatch.setattr(entry, "load_settings", lambda: settings)
    monkeypatch.setattr(entry.logs, "start_console", lambda: None)
    monkeypatch.setattr(entry.logs, "start_file", lambda folder: None)
    with pytest.raises(SystemExit, match="Settings problem: .*demo database"):
        entry.main()


BOASTFUL = Decimal("2500")  # more than this over thirteen weeks would read as an advert


def test_seed_showcase_bots_end_modestly_ahead(built):
    """Final review I-4b: the two Live bots the tour shows each end a little ahead over the
    history (one losing bot would be a poor first impression; a big win, an advert)."""
    _, factory, _ = built
    bots = _bots(factory)
    view = journal.load_journal(factory, now=datetime.combine(DEMO_DAY, datetime.min.time(), UTC))
    with factory() as session:
        runs = list(session.scalars(select(BotRun)))
    for name in ("SPX 0DTE condor", "Weekly calendar"):
        tag = "Iron Condors" if name == "SPX 0DTE condor" else "Calendars"
        closed = [e for e in view.entries if e.position.status is Status.CLOSED
                  and tag in {t.name for t in e.tags}]  # fmt: skip
        total = sum((e.position.realized_pl for e in closed), Decimal(0))
        assert Decimal(0) < total < BOASTFUL, (name, total)
        assert any(r.bot_id == bots[name].id for r in runs)


def test_seed_condor_trades_on_the_days_its_schedule_says(built):
    _, factory, _ = built
    condor_id = _bots(factory)["SPX 0DTE condor"].id
    condor = bot_store.load(factory, condor_id)
    with factory() as session:
        days = {r.trading_date for r in session.scalars(select(BotRun)) if r.bot_id == condor_id}
    from optionsjourney.bots import schedule
    from optionsjourney.demo import history

    expected = {d for d in history.trading_days() if schedule.runs_on(condor, d)}
    assert days - {DEMO_DAY} == expected
    assert schedule.runs_on(condor, DEMO_DAY)  # the tour arms it on the demo day


def test_seed_has_the_manual_exits(built):
    _, factory, folder = built
    assert seed.MANUAL_EXITS == "Put spread exits"
    assert "Put spread exits" in manual_bots.names(factory).values()
    [(_, rules)] = manual_bots.listing(factory)
    assert (rules.target_value, rules.target_unit) == (Decimal(50), "pct")
    assert (rules.stop_value, rules.stop_unit) == (Decimal(200), "pct")
