from datetime import UTC, datetime, timedelta, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

import optionsjourney.db.models  # noqa: F401
from optionsjourney.db import Base, make_engine, make_session_factory, migrate
from optionsjourney.db.models import SyncState


def test_migrations_match_the_models(tmp_path):
    """A model change without a migration fails here."""
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_migrate_twice_is_harmless(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    migrate(engine)


def test_datetimes_come_back_in_utc(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    eastern = timezone(timedelta(hours=-4))
    with make_session_factory(engine)() as session:
        session.add(
            SyncState(
                broker="tastytrade",
                account_number="5WX01234",
                last_attempt_at=datetime(2026, 10, 9, 9, 30, tzinfo=eastern),
            )
        )
        session.commit()
    with make_session_factory(engine)() as session:
        state = session.get(SyncState, ("tastytrade", "5WX01234"))
        assert state.last_attempt_at == datetime(2026, 10, 9, 13, 30, tzinfo=UTC)
        assert state.last_attempt_at.tzinfo is UTC


def test_naive_datetimes_are_refused(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    with make_session_factory(engine)() as session:
        session.add(
            SyncState(
                broker="tastytrade",
                account_number="5WX01234",
                last_attempt_at=datetime(2026, 10, 9, 9, 30),
            )
        )
        with pytest.raises(Exception, match="timezone-aware"):
            session.commit()


def test_money_keeps_every_decimal_place(tmp_path):
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from optionsjourney.db.models import AccountValue

    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(
            AccountValue(
                broker="tastytrade",
                account_number="5WX01234",
                day=date(2026, 9, 1),
                net_liquidating_value=Decimal("25431.1734"),
                cash=Decimal("-0.01"),
                source="history",
                recorded_at=datetime.now(UTC),
            )
        )
        session.commit()
    with factory() as session:
        row = session.get(AccountValue, ("tastytrade", "5WX01234", date(2026, 9, 1)))
        assert row.net_liquidating_value == Decimal("25431.1734")
        assert row.cash == Decimal("-0.01")


def test_0008_tags_trades_with_their_strategy_and_adds_a_stocks_tag(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0007")
        connection.execute(text("INSERT INTO tags (name) VALUES ('wheel')"))
        connection.execute(
            text(
                "INSERT INTO trade_edits (position_key, strategy_id, updated_at) "
                "SELECT 'k1', id, '2026-10-10 00:00:00' FROM strategies WHERE name = 'Wheel'"
            )
        )
        connection.execute(
            text(
                "INSERT INTO trade_edits (position_key, strategy_id, updated_at) "
                "SELECT 'k2', id, '2026-10-10 00:00:00' FROM strategies WHERE name = '0DTE'"
            )
        )
    migrate(engine)
    with engine.connect() as connection:
        tagged = connection.execute(
            text(
                "SELECT trade_tags.position_key, tags.name FROM trade_tags "
                "JOIN tags ON tags.id = trade_tags.tag_id ORDER BY 1"
            )
        ).all()
        names = {row[0] for row in connection.execute(text("SELECT name FROM tags"))}
    assert tagged == [("k1", "wheel"), ("k2", "0DTE")]  # an existing tag is reused
    assert {"wheel", "0DTE", "Stocks"} <= names and "Wheel" not in names  # no duplicate


def test_0009_gives_every_strategy_a_tag(tmp_path):
    from sqlalchemy import text

    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    with engine.connect() as connection:
        tags = {row[0] for row in connection.execute(text("SELECT name FROM tags"))}
    assert tags == {"0DTE", "Calendars", "Credit Spreads", "Iron Condors", "Wheel", "Stocks"}


def test_0012_keeps_old_dashboards_on_open_and_closed(tmp_path):
    """Before #68 a dashboard with no status meant open and closed trades."""
    import json

    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0011")
        for sort_order, filters in enumerate(({"days": 30}, {"status": "closed"})):
            connection.execute(
                text(
                    "INSERT INTO dashboards (name, sort_order, filters, created_at) "
                    "VALUES (:name, :sort_order, :filters, '2026-10-10 00:00:00')"
                ),
                {
                    "name": f"d{sort_order}",
                    "sort_order": sort_order,
                    "filters": json.dumps(filters),
                },
            )
    migrate(engine)
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT filters FROM dashboards ORDER BY sort_order"))
        saved = [json.loads(row[0]) for row in rows]
    assert saved == [{"days": 30, "status": "all"}, {"status": "closed"}]


def test_0014_moves_bot_orders_into_the_order_log_and_stop_all_into_freeze(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0013")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, config, created_at) SELECT 1, 'b', id, "
                "'tastytrade', '5WX01234', 0, 1, 1, 1, '{}', '2026-10-11 00:00:00' "
                "FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                'INSERT INTO bot_orders (bot_id, created_at, action, quantity, price, "order", '
                "result, ok, broker_order_id) VALUES (1, '2026-10-11 14:00:00', 'send', 1, "
                "'1.5', '{}', 'Sent.', 1, '777')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO settings (key, value, updated_at) "
                "VALUES ('_bots_stopped', 'on', '2026-10-11 00:00:00')"
            )
        )
    migrate(engine)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT broker, account_number, origin, bot_id, action, opening, order_type, "
                "broker_order_id FROM order_log"
            )
        ).one()
        keys = {r[0] for r in connection.execute(text("SELECT key FROM settings"))}
        tables = {
            r[0]
            for r in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert tuple(row) == ("tastytrade", "5WX01234", "bot", 1, "send", 1, "limit", "777")
    assert "_freeze" in keys and "_bots_stopped" not in keys
    assert "bot_orders" not in tables and "tick_sizes" in tables


def test_0015_extends_bots_and_adds_the_bot_tables(tmp_path):
    from alembic import command
    from sqlalchemy import inspect, text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0014")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, config, created_at) SELECT 1, 'b', id, "
                "'tastytrade', '5WX01234', 0, 0, 1, 2, '{}', '2026-10-12 00:00:00' "
                "FROM strategies LIMIT 1"
            )
        )
    migrate(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("bots")}
    assert {"underlying", "contracts", "split_iron_condors", "hidden"} <= columns
    assert "config" not in columns
    tables = set(inspect(engine).get_table_names())
    assert {"bot_legs", "bot_leg_groups", "bot_entry_times", "bot_chase", "bot_runs",
            "bot_exits"} <= tables  # fmt: skip
    assert "bot_run_id" in {c["name"] for c in inspect(engine).get_columns("order_log")}
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT underlying, contracts, split_iron_condors FROM bots")
        ).one()
    assert tuple(row) == ("SPX", 1, 1)


def test_0016_adds_the_slice_1_columns_and_keeps_existing_bots(tmp_path):
    from alembic import command
    from sqlalchemy import inspect, text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0015")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, "
                "'tastytrade', '5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' "
                "FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text("INSERT INTO bot_entry_times (bot_id, time, enabled) VALUES (1, '09:47', 1)")
        )
        command.upgrade(config, "0032")  # Opens for (premium_side) is gone at 0033
        assert connection.execute(text("SELECT premium_side FROM bots")).scalar() == "credit"
    migrate(engine)
    inspector = inspect(engine)
    bot_columns = {c["name"] for c in inspector.get_columns("bots")}
    assert {
        "max_entries_per_day", "max_open_trades", "use_premium_bounds",
        "premium_min", "premium_max", "frequency", "entry_days", "monthly_week",
        "monthly_weekday", "specific_dates", "use_blackout_days", "blackout_days",
    } <= bot_columns  # fmt: skip
    assert {"quantity", "label"} <= {c["name"] for c in inspector.get_columns("bot_entry_times")}
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT name, live, enabled, max_per_trade, max_per_day, max_entries_per_day, "
                "max_open_trades, use_premium_bounds, premium_min, frequency, "
                "entry_days, specific_dates, blackout_days, use_blackout_days FROM bots"
            )
        ).one()
        entry = connection.execute(
            text("SELECT time, enabled, quantity, label FROM bot_entry_times")
        ).one()
    assert tuple(row) == ("b", 1, 1, 3, 4, None, None, 0, None, "daily", "[]", "[]", "[]", 0)
    assert tuple(entry) == ("09:47", 1, None, None)


def test_0017_keeps_existing_bots_stops_as_they_were(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0016")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, "
                "'tastytrade', '5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' "
                "FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_leg_groups (bot_id, position, name, stop_pct, target_pct) "
                "VALUES (1, 0, 'Call side', '95', '50')"
            )
        )
    migrate(engine)
    with engine.connect() as connection:
        bot = connection.execute(text("SELECT stop_co_mode, stop_co_amount FROM bots")).one()
        group = connection.execute(
            text(
                "SELECT stop_pct, target_pct, stop_basis, stop_unit, stop_usd, target_unit, "
                "target_usd, on_stop_remainder, on_stop_remainder_limit FROM bot_leg_groups"
            )
        ).one()
    assert tuple(bot) == ("off", None)
    assert tuple(group) == ("95", "50", "leg_premium", "pct", None, "pct", None,
                            "close_market", None)  # fmt: skip


def test_a_slice_1_bot_loads_unchanged_after_0017(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0016")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, "
                "'tastytrade', '5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' "
                "FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_leg_groups (bot_id, position, name, stop_pct, target_pct) "
                "VALUES (1, 0, 'Call side', '95', '50')"
            )
        )
    migrate(engine)
    bot = bot_store.load(make_session_factory(engine), 1)
    group = bot.groups[0]
    assert (group.stop_pct, group.target_pct) == (95, 50)
    assert (group.stop_basis, group.stop_unit, group.target_unit) == ("leg_premium", "pct", "pct")
    assert (group.on_stop_remainder, group.stop_usd, group.target_usd) == (
        "close_market",
        None,
        None,
    )
    assert (bot.stop_co_mode, bot.stop_co_amount) == ("off", None)


def test_0018_keeps_existing_bots_closes_as_they_were(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0017")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, "
                "'tastytrade', '5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' "
                "FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_chase (bot_id, phase, start_offset, step, interval_seconds, "
                "max_tries) VALUES (1, 'exit', '0', '0.05', 5, 6)"
            )
        )
    migrate(engine)
    with engine.connect() as connection:
        row = connection.execute(text("SELECT exhaust_mode, natural_minutes FROM bot_chase")).one()
        columns = [c[1] for c in connection.execute(text("PRAGMA table_info(bot_exits)"))]
    assert tuple(row) == ("give_up", 2)
    assert "cancel_requested_at" in columns
    exit_chase = bot_store.load(make_session_factory(engine), 1).exit_chase
    assert (exit_chase.exhaust_mode, exit_chase.natural_minutes) == ("give_up", 2)


def test_0019_leaves_existing_bots_without_filters_or_lists(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.bots.definition import MoveLimits
    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0018")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at, use_blackout_days, blackout_days) "
                "SELECT 1, 'b', id, 'tastytrade', '5WX01234', 1, 1, 3, 4, "
                "'2026-10-12 00:00:00', 1, '[\"2026-12-24\"]' FROM strategies LIMIT 1"
            )
        )
    migrate(engine)
    bot = bot_store.load(make_session_factory(engine), 1)
    assert not (bot.use_vix_level or bot.use_vix_overnight or bot.use_vix9d_ratio)
    assert not (bot.use_technicals or bot.use_intraday_move)
    assert bot.vix_overnight == MoveLimits() and bot.intraday == MoveLimits()
    assert (bot.sma_mode, bot.ema_mode) == (None, None)
    assert (bot.specific_date_sets, bot.blackout_date_sets) == ((), ())
    assert bot.use_blackout_days and bot.blackout_days == ("2026-12-24",)
    with engine.connect() as connection:
        tables = {r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))}
    assert {"economic_events", "economic_event_sources", "market_calendar"} <= tables
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0018")
        columns = [c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))]
        tables = {r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))}
    assert "use_vix_level" not in columns and "economic_events" not in tables


def test_0020_leaves_existing_bots_without_timed_close_or_time_actions(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0019")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
    migrate(engine)
    bot = bot_store.load(make_session_factory(engine), 1)
    assert (bot.close_time, bot.close_dte) == (None, 0)
    assert (bot.use_time_actions, bot.time_actions) == (False, ())
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0019")
        columns = [c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))]
    assert "close_time" not in columns and "time_actions" not in columns


def test_0021_leaves_existing_bots_untagged_unarchived_and_not_imported(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0020")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
    migrate(engine)
    factory = make_session_factory(engine)
    bot = bot_store.load(factory, 1)
    # 0053 (#278): a bot holds its main tag among its tags
    assert (bot.tags, bot.archived, bot.enabled, bot.live) == ((bot.tags[0],), False, True, True)
    assert bot_store.imported(factory, 1) == (None, None)
    assert [b.id for b in bot_store.armed(factory)] == [1]
    with engine.connect() as connection:
        tables = {r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))}
    assert "bot_integrity_alerts" in tables
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0020")
        columns = [c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))]
        tables = {r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))}
    assert "tags" not in columns and "archived_at" not in columns and "imported_at" not in columns
    assert "bot_integrity_alerts" not in tables


def test_0022_leaves_existing_bots_and_runs_unchanged(tmp_path):
    from alembic import command
    from sqlalchemy import select, text

    from optionsjourney.db import alembic_config
    from optionsjourney.db.models import BotTradeOverride
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0021")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "legs, filled_legs, contracts, chase_log, created_at, updated_at) VALUES (1, 1, "
                "'bot-1-0947-20261013', '2026-10-13', '09:47', 'filled', 1, '[]', '[]', 1, '[]', "
                "'2026-10-13 13:47:00', '2026-10-13 13:47:00')"
            )
        )
    migrate(engine)
    factory = make_session_factory(engine)
    bot = bot_store.load(factory, 1)
    assert (bot.enabled, bot.live) == (True, True)
    assert bot_store.run(factory, 1).status == "filled"
    with factory() as session:
        assert list(session.scalars(select(BotTradeOverride))) == []
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0021")
        tables = {r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))}
    assert "bot_trade_overrides" not in tables and "bot_runs" in tables


def test_0023_leaves_existing_bots_and_runs_unchanged(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0022")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at, time_actions, use_time_actions) "
                "SELECT 1, 'b', id, 'tastytrade', '5WX01234', 1, 1, 3, 4, "
                '\'2026-10-12 00:00:00\', \'[{"type": "dte", "days": 0, "at": "14:00", '
                '"stop_pct": "150", "target_pct": null}]\', 1 FROM strategies LIMIT 1'
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_leg_groups (bot_id, position, name, stop_pct) "
                "VALUES (1, 0, 'Call side', 95)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "legs, filled_legs, contracts, chase_log, created_at, updated_at) VALUES (1, 1, "
                "'bot-1-0947-20261013', '2026-10-13', '09:47', 'filled', 1, '[]', '[]', 1, '[]', "
                "'2026-10-13 13:47:00', '2026-10-13 13:47:00')"
            )
        )
    migrate(engine)
    factory = make_session_factory(engine)
    bot = bot_store.load(factory, 1)
    assert (bot.enabled, bot.live) == (True, True)
    assert (bot.use_delta_exit, bot.use_sl_ratio_exit, bot.use_underlying_exit) == (
        False, False, False,
    )  # fmt: skip
    assert bot.position_delta_above is None and bot.short_put_tested_pts is None
    [side] = bot.groups
    assert (side.use_profit_actions, side.profit_actions) == (False, ())
    # A time action saved before slice 7 keeps its sig, so it never fires twice.
    assert bot.time_actions[0].close_pct is None
    assert bot.time_actions[0].sig == "dte|0|14:00|150|"
    run = bot_store.run(factory, 1)
    assert (run.status, run.entry_spot) == ("filled", None)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0022")
        bots = [c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))]
        groups = [c[1] for c in connection.execute(text("PRAGMA table_info(bot_leg_groups)"))]
        runs = [c[1] for c in connection.execute(text("PRAGMA table_info(bot_runs)"))]
    assert "use_delta_exit" not in bots and "short_call_tested_pts" not in bots
    assert "profit_actions" not in groups and "entry_spot" not in runs


def test_0024_leaves_existing_bots_and_alerts_as_they_were(tmp_path):
    from alembic import command
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store, user_alerts

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0023")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO user_alerts (id, kind, target, label, direction, level, active, "
                "created_at, dismissed) VALUES (1, 'price', 'SPX', 'SPX', 'below', 5700, 1, "
                "'2026-10-12 00:00:00', 0)"
            )
        )
    migrate(engine)
    factory = make_session_factory(engine)
    bot = bot_store.load(factory, 1)
    assert (bot.enabled, bot.live, bot.alert_name, bot.alert_channel) == (True, True, None, "all")
    assert (bot.use_default_alerts, bot.default_alerts, bot.default_alerts_in_app_only) == (
        False, (), False,
    )  # fmt: skip
    [alert] = user_alerts.active(factory)
    assert (alert.kind, alert.bot_run_id, alert.rule_key, alert.quiet) == (
        "price", None, None, False,
    )  # fmt: skip
    with engine.begin() as connection:
        # One alert per entry and rule; the alerts you set (nulls) never clash.
        for n in (2, 3):
            connection.execute(text(
                "INSERT INTO user_alerts (id, kind, target, label, direction, level, active, "
                f"created_at, dismissed) VALUES ({n}, 'price', 'SPX', 'SPX', 'above', 5800, 1, "
                "'2026-10-12 00:00:00', 0)"
            ))  # fmt: skip
        insert = (
            "INSERT INTO user_alerts (kind, target, label, direction, level, active, created_at, "
            "dismissed, bot_run_id, rule_key, quiet) VALUES ('price', 'SPX', 'x', 'below', 5710, "
            "1, '2026-10-13 00:00:00', 0, 9, 'strike:P:5700:10', 0)"
        )
        connection.execute(text(insert))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text(insert))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0023")
        bots = [c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))]
        alerts = [c[1] for c in connection.execute(text("PRAGMA table_info(user_alerts)"))]
    assert "alert_channel" not in bots and "default_alerts" not in bots
    assert "bot_run_id" not in alerts and "quiet" not in alerts


def test_0025_leaves_existing_bots_on_their_entry_times(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0024")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text("INSERT INTO bot_entry_times (bot_id, time, enabled) VALUES (1, '09:47', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO bot_legs (bot_id, position, side, right, ratio, dte, pick, target, "
                "exact, group_position) VALUES (1, 0, 'sell', 'put', 1, 0, 'premium', 2.9, 1, 1)"
            )
        )
    migrate(engine)
    bot = bot_store.load(make_session_factory(engine), 1)
    assert (bot.enabled, bot.live) == (True, True)
    assert (bot.use_floating_entry, bot.floating_start, bot.floating_end) == (False, None, None)
    assert [t.time for t in bot.entry_times] == ["09:47"]
    assert [leg.pick for leg in bot.legs] == ["premium"] and not bot.market_picks
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0024")
        bots = [c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))]
    assert "use_floating_entry" not in bots and "floating_end" not in bots


def test_0026_gives_existing_runs_tastytrade(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.db.models import BotRun

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0025")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "account_number, legs, filled_legs, contracts, chase_log, created_at, updated_at) "
                "VALUES (1, 1, 'bot-1-0947-20261012', '2026-10-12', '09:47', 'filled', 1, "
                "'5WX01234', '[]', '[]', 1, '[]', '2026-10-12 13:47:00', '2026-10-12 13:47:00')"
            )
        )
    migrate(engine)
    with make_session_factory(engine)() as session:
        assert session.get(BotRun, 1).broker == "tastytrade"
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0025")
        runs = [c[1] for c in connection.execute(text("PRAGMA table_info(bot_runs)"))]
    assert "broker" not in runs


def test_0027_gives_existing_exits_their_legs_and_anchor(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.db.models import BotExit

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    S, L = "SPXW  261012P05000000", "SPXW  261012P04990000"
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0026")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, premium_side, created_at) SELECT 1, 'b', id, "
                "'tastytrade', '5WX01234', 1, 1, 3, 4, '', '2026-10-12 00:00:00' "
                "FROM strategies LIMIT 1"
            )
        )
        # Final review I2 (controller's ruling): before slice 13 every bot opened for a
        # credit, whatever its stored premium side; bounds off or on.
        for bot_id, bounds in ((2, 0), (3, 1)):
            connection.execute(
                text(
                    "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, "
                    "enabled, max_per_trade, max_per_day, premium_side, use_premium_bounds, "
                    f"created_at) SELECT {bot_id}, 'b{bot_id}', id, 'tastytrade', '5WX01234', "
                    f"1, 1, 3, 4, 'debit', {bounds}, '2026-10-12 00:00:00' "
                    "FROM strategies LIMIT 1"
                )
            )
        connection.execute(
            text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "account_number, legs, filled_legs, contracts, chase_log, created_at, updated_at) "
                "VALUES (1, 1, 'bot-1-0947-20261012', '2026-10-12', '09:47', 'filled', 1, "
                "'5WX01234', '[]', '[]', 1, '[]', '2026-10-12 13:47:00', '2026-10-12 13:47:00')"
            )
        )
        kinds = ["stop", "target", "wing", "timed", "action", "close", "watch", "profit"]
        for i, kind in enumerate(kinds, start=1):
            connection.execute(
                text(
                    "INSERT INTO bot_exits (id, run_id, group_position, kind, state, short_symbol, "
                    "long_symbol, contracts, rearms, log, created_at, updated_at) VALUES "
                    f"({i}, 1, 0, '{kind}', 'armed', '{S}', '{L}', 1, 0, '[]', "
                    "'2026-10-12 13:48:00', '2026-10-12 13:48:00')"
                )
            )
        command.upgrade(config, "0032")
        query = text("SELECT premium_side FROM bots ORDER BY id")
        sides = [r[0] for r in connection.execute(query)]
        assert sides == ["credit"] * 3  # final review I2: every bot before slice 13 is credit
    migrate(engine)
    pair = [
        {"symbol": S, "side": "sell", "ratio": 1},
        {"symbol": L, "side": "buy", "ratio": 1},
    ]
    with make_session_factory(engine)() as session:
        by_kind = {row.kind: row for row in session.query(BotExit)}
        for kind in ("stop", "target", "timed", "action", "close", "watch", "profit"):
            assert by_kind[kind].legs == pair, kind
        assert by_kind["wing"].legs == [{"symbol": L, "side": "buy", "ratio": 1}]
        for row in by_kind.values():
            assert row.anchor_symbol == S
            assert row.watched is False
            assert (row.short_symbol, row.long_symbol) == (S, L)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0026")
        columns = [c[1] for c in connection.execute(text("PRAGMA table_info(bot_exits)"))]
    assert not {"legs", "anchor_symbol", "watched"} & set(columns)


def test_0028_gives_existing_bots_exact_dte(tmp_path):
    """Bots slice 14: every existing bot is 0 DTE; exact 0 DTE is "today must be listed",
    what the entry did before."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0027")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_legs (bot_id, position, side, right, ratio, dte, pick, target, "
                "exact, group_position) VALUES (1, 0, 'sell', 'put', 1, 0, 'premium', 2.9, 1, 0)"
            )
        )
    migrate(engine)
    bot = bot_store.load(make_session_factory(engine), 1)
    assert bot.use_exact_dte is True
    assert (bot.enabled, bot.live) == (True, True)
    assert [leg.dte for leg in bot.legs] == [0]
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0027")
        bots = [c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))]
    assert "use_exact_dte" not in bots


def test_0029_keeps_existing_runs_as_they_were(tmp_path):
    """Bots slice 17: an existing run is 100 a point, on no futures contract, not profit
    target only, and was chased: what every run was before."""
    from decimal import Decimal

    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.db.models import BotRun
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0028")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "account_number, legs, filled_legs, contracts, chase_log, created_at, updated_at) "
                "VALUES (1, 1, 'bot-1-0947-20261012', '2026-10-12', '09:47', 'filled', 1, "
                "'5WX01234', '[]', '[]', 1, '[]', '2026-10-12 13:47:00', '2026-10-12 13:47:00')"
            )
        )
    migrate(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        run = session.get(BotRun, 1)
        assert (run.multiplier, run.contract, run.pt_only, run.entry_mode) == (
            Decimal(100), None, False, "chase")  # fmt: skip
        assert (run.status, run.broker) == ("filled", "tastytrade")
    bot = bot_store.load(factory, 1)
    assert (bot.enabled, bot.live, bot.underlying) == (True, True, "SPX")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0028")
        columns = [c[1] for c in connection.execute(text("PRAGMA table_info(bot_runs)"))]
    assert not {"multiplier", "contract", "pt_only", "entry_mode"} & set(columns)


def test_0030_gives_an_exits_off_row_both_switches(tmp_path):
    """Bots slice 18: slice 6's single exits-off switch becomes the stop's and the target's,
    both off for a trade that had exits off, both on for every other; downgrade puts the
    single switch back as both."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.db.models import BotTradeOverride

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0029")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "account_number, legs, filled_legs, contracts, chase_log, created_at, updated_at) "
                "VALUES (1, 1, 'bot-1-0947-20261012', '2026-10-12', '09:47', 'filled', 1, "
                "'5WX01234', '[]', '[]', 1, '[]', '2026-10-12 13:47:00', '2026-10-12 13:47:00')"
            )
        )
        for position, off in ((0, 1), (1, 0)):
            connection.execute(
                text(
                    "INSERT INTO bot_trade_overrides (run_id, group_position, exits_off, "
                    f"changed_at, log) VALUES (1, {position}, {off}, '2026-10-12 14:00:00', '[]')"
                )
            )
    migrate(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        rows = sorted(session.query(BotTradeOverride), key=lambda r: r.group_position)
        assert [(r.stop_off, r.target_off, r.exits_off) for r in rows] == [
            (True, True, True),
            (False, False, False),
        ]
        rows[1].stop_off = True  # only the stop off: not "exits off"
        session.commit()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0029")
        found = connection.execute(
            text("SELECT group_position, exits_off FROM bot_trade_overrides ORDER BY 1")
        ).all()
        columns = [c[1] for c in connection.execute(text("PRAGMA table_info(bot_trade_overrides)"))]
    assert [tuple(r) for r in found] == [(0, 1), (1, 0)]
    assert not {"stop_off", "target_off"} & set(columns)


def test_0031_leaves_an_existing_bot_as_it_was(tmp_path):
    """Bots slice 19: an existing bot is sized by contracts, its entry chase has no time
    limit, its closes don't go to market after the last try, its legs have no delta
    thresholds or max offset, and it has no min short width and doesn't move conflicted
    strikes: what every bot did before. Downgrade drops the columns."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0030")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_legs (bot_id, position, side, right, ratio, dte, pick, target, "
                "exact, group_position) VALUES (1, 0, 'sell', 'put', 1, 0, 'premium', 2.9, 1, 0)"
            )
        )
        for phase in ("entry", "exit"):
            connection.execute(
                text(
                    "INSERT INTO bot_chase (bot_id, phase, start_offset, step, interval_seconds, "
                    f"max_tries, exhaust_mode, natural_minutes) VALUES (1, '{phase}', 0, 0.05, "
                    "5, 6, 'give_up', 2)"
                )
            )
    migrate(engine)
    bot = bot_store.load(make_session_factory(engine), 1)
    assert (bot.sizing, bot.max_per_entry_usd) == ("contracts", None)
    assert (bot.min_short_width, bot.move_conflicted_strikes) == (None, False)
    assert (bot.entry_chase.window_minutes, bot.exit_chase.switch_to_market) == (None, False)
    leg = bot.legs[0]
    assert (leg.exit_delta_below, leg.exit_delta_above, leg.max_offset) == (None, None, None)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0030")
        columns = {
            table: {c[1] for c in connection.execute(text(f"PRAGMA table_info({table})"))}
            for table in ("bots", "bot_chase", "bot_legs")
        }
    gone = {"sizing", "max_per_entry_usd", "min_short_width", "move_conflicted_strikes"}
    assert not gone & columns["bots"]
    assert not {"window_minutes", "switch_to_market"} & columns["bot_chase"]
    assert not {"exit_delta_below", "exit_delta_above", "max_offset"} & columns["bot_legs"]


def test_0032_leaves_an_existing_bot_and_trade_as_they_were(tmp_path):
    """Bots slice 21: an existing bot's groups close nothing else after a stop, and an
    existing trade has no settings of its own (every new column null, no rows in the two
    new tables), so every pass reads what it read before. Downgrade drops it all."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.db.models import BotTradeOverride
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0031")
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 1, 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_legs (bot_id, position, side, right, ratio, dte, pick, target, "
                "exact, group_position) VALUES (1, 0, 'sell', 'put', 1, 0, 'premium', 2.9, 1, 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_leg_groups (bot_id, position, name, stop_pct, stop_basis, "
                "stop_unit, target_unit, on_stop_remainder, use_profit_actions, profit_actions) "
                "VALUES (1, 0, 'Put side', 95, 'leg_premium', 'pct', 'pct', 'close_market', 0, "
                "'[]')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "account_number, legs, filled_legs, contracts, chase_log, created_at, updated_at) "
                "VALUES (1, 1, 'bot-1-0947-20261012', '2026-10-12', '09:47', 'filled', 1, "
                "'5WX01234', '[]', '[]', 1, '[]', '2026-10-12 13:47:00', '2026-10-12 13:47:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_trade_overrides (run_id, group_position, stop_pct, stop_off, "
                "target_off, changed_at, log) VALUES (1, 0, 120, 0, 0, '2026-10-12 14:00:00', "
                "'[]')"
            )
        )
    migrate(engine)
    factory = make_session_factory(engine)
    bot = bot_store.load(factory, 1)
    assert bot.groups[0].close_on_stop is None
    with factory() as session:
        row = session.query(BotTradeOverride).one()
        assert row.stop_pct == 120
        assert (row.stop_unit, row.stop_usd, row.co_mode, row.co_amount) == (None,) * 4
        assert (row.on_stop_remainder, row.remainder_limit) == (None, None)
        assert (row.use_profit_actions, row.profit_actions) == (None, None)
    with engine.begin() as connection:
        for table in ("bot_run_timed_closes", "bot_run_watch_rules"):
            assert connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 0
        config.attributes["connection"] = connection
        command.downgrade(config, "0031")
        tables = {r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))}
        columns = {
            table: {c[1] for c in connection.execute(text(f"PRAGMA table_info({table})"))}
            for table in ("bot_trade_overrides", "bot_leg_groups")
        }
    assert not {"bot_run_timed_closes", "bot_run_watch_rules"} & tables
    assert "on_stop_close_group" not in columns["bot_leg_groups"]
    assert (
        not {"stop_unit", "co_mode", "on_stop_remainder", "profit_actions"}
        & columns["bot_trade_overrides"]
    )


def test_0033_rests_a_debit_bots_targets_and_leaves_open_trades_as_they_were(tmp_path):
    """Decision 0048: a debit bot's groups rest their target from the next trade, every
    other group its stop; an open trade's exit rows are as they were (no stop the app holds
    on a short). Opens for (bots.premium_side) goes; downgrade drops it all and gives it back
    ('debit' for a bot all of whose groups rest their target)."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.db.models import BotExit
    from optionsjourney.services import bot_store

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0032")
        for bot_id, side in ((1, "credit"), (2, "debit")):
            connection.execute(text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                f"max_per_trade, max_per_day, premium_side, created_at) SELECT {bot_id}, "
                f"'b{bot_id}', id, 'tastytrade', '5WX01234', 1, 1, 3, 4, '{side}', "
                "'2026-10-12 00:00:00' FROM strategies LIMIT 1"
            ))  # fmt: skip
            connection.execute(text(
                "INSERT INTO bot_legs (bot_id, position, side, right, ratio, dte, pick, target, "
                f"exact, group_position) VALUES ({bot_id}, 0, 'sell', 'put', 1, 0, 'premium', "
                "2.9, 1, 0)"
            ))  # fmt: skip
            connection.execute(text(
                "INSERT INTO bot_leg_groups (bot_id, position, name, stop_pct, stop_basis, "
                "stop_unit, target_unit, on_stop_remainder, use_profit_actions, profit_actions) "
                f"VALUES ({bot_id}, 0, 'Put side', 50, 'group_entry', 'pct', 'pct', "
                "'close_market', 0, '[]')"
            ))  # fmt: skip
        connection.execute(text(
            "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
            "account_number, legs, filled_legs, contracts, chase_log, created_at, updated_at) "
            "VALUES (1, 2, 'bot-2-0947-20261012', '2026-10-12', '09:47', 'filled', 1, "
            "'5WX01234', '[]', '[]', 1, '[]', '2026-10-12 13:47:00', '2026-10-12 13:47:00')"
        ))  # fmt: skip
        connection.execute(text(
            "INSERT INTO bot_exits (id, run_id, group_position, kind, state, short_symbol, "
            "long_symbol, contracts, rearms, log, created_at, updated_at, legs, watched) VALUES "
            "(1, 1, 0, 'target', 'armed', '', '', 1, 0, '[]', '2026-10-12 13:48:00', "
            "'2026-10-12 13:48:00', '[]', 1)"
        ))  # fmt: skip
    migrate(engine)
    factory = make_session_factory(engine)
    assert bot_store.load(factory, 1).groups[0].broker_exit == "stop"
    assert bot_store.load(factory, 2).groups[0].broker_exit == "target"
    with factory() as session:
        row = session.get(BotExit, 1)
        assert (row.state, row.app_held, row.watched) == ("armed", False, True)
    with engine.begin() as connection:
        bots = {c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))}
    assert "premium_side" not in bots
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0032")
        columns = {
            table: {c[1] for c in connection.execute(text(f"PRAGMA table_info({table})"))}
            for table in ("bot_leg_groups", "bot_exits", "bot_runs")
        }
        sides = dict(connection.execute(text("SELECT id, premium_side FROM bots")).all())
    assert sides == {1: "credit", 2: "debit"}
    assert "broker_exit" not in columns["bot_leg_groups"]
    assert "app_held" not in columns["bot_exits"] and "priced" not in columns["bot_runs"]


def test_0034_switches_off_bots_whose_spreads_no_longer_rest_their_stops(tmp_path):
    """Decision 0054: every group of more than one leg rests its target; an armed or live bot
    with one is switched off and back to preview, with a notice to alert. A bot of lone
    shorts, and one already off, keep their state; the off one gets no notice."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0033")
        # bot 1: armed, live, a put spread; 2: armed, live, a lone short put; 3: off, a spread
        for bot_id, on, legs in ((1, 1, 2), (2, 1, 1), (3, 0, 2)):
            connection.execute(text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                f"max_per_trade, max_per_day, created_at) SELECT {bot_id}, 'b{bot_id}', id, "
                f"'tastytrade', '5WX01234', {on}, {on}, 3, 4, '2026-10-12 00:00:00' "
                "FROM strategies LIMIT 1"
            ))  # fmt: skip
            for position, side in list(enumerate(("sell", "buy")))[:legs]:
                connection.execute(text(
                    "INSERT INTO bot_legs (bot_id, position, side, right, ratio, dte, pick, "
                    f"target, exact, group_position) VALUES ({bot_id}, {position}, '{side}', "
                    "'put', 1, 0, 'premium', 2.9, 1, 0)"
                ))  # fmt: skip
            connection.execute(text(
                "INSERT INTO bot_leg_groups (bot_id, position, name, stop_pct, stop_basis, "
                "stop_unit, target_unit, on_stop_remainder, use_profit_actions, profit_actions, "
                f"broker_exit) VALUES ({bot_id}, 0, 'Put side', 95, 'leg_premium', 'pct', 'pct', "
                "'close_market', 0, '[]', 'stop')"
            ))  # fmt: skip
    migrate(engine)
    with engine.begin() as connection:
        rows = {
            r[0]: r[1:]
            for r in connection.execute(text(
                "SELECT b.id, b.enabled, b.live, b.notice IS NOT NULL, b.notice_sent, "
                "g.broker_exit FROM bots b JOIN bot_leg_groups g ON g.bot_id = b.id"
            ))
        }  # fmt: skip
    assert rows[1] == (0, 0, 1, 0, "target")
    assert rows[2] == (1, 1, 0, 0, "stop")
    assert rows[3] == (0, 0, 0, 0, "target")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0033")
        bots = {c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))}
    assert "notice" not in bots and "notice_sent" not in bots


def test_0035_adds_the_link_a_bot_was_imported_from(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    config = alembic_config()
    with engine.begin() as connection:
        bots = {c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))}
        assert "imported_from_url" in bots
        config.attributes["connection"] = connection
        command.downgrade(config, "0034")
        bots = {c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))}
    assert "imported_from_url" not in bots


def test_0036_keeps_open_schwab_stops_watched_by_the_app(tmp_path):
    """Decision 0060: a Schwab trade opened before it keeps its stop watched by the app
    until it closes (never a second stop at Schwab): its stop rows on a short are marked
    app-held. A group watched on its P&L, a TastyTrade stop and a target are untouched."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config
    from optionsjourney.db.models import BotExit

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0035")
        connection.execute(text(
            "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
            "max_per_trade, max_per_day, created_at) SELECT 1, 'b1', id, 'schwab', '12345678', "
            "1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
        ))  # fmt: skip
        for run_id, broker in ((1, "schwab"), (2, "tastytrade")):
            connection.execute(text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "account_number, legs, filled_legs, contracts, chase_log, created_at, "
                f"updated_at, broker) VALUES ({run_id}, 1, 'bot-1-0947-{run_id}', '2026-10-12', "
                "'09:47', 'filled', 1, '12345678', '[]', '[]', 1, '[]', '2026-10-12 13:47:00', "
                f"'2026-10-12 13:47:00', '{broker}')"
            ))  # fmt: skip
        # (id, run, kind, state, watched): Schwab's short stop, its P&L-watched group, its
        # target, an old failed Schwab stop, and a TastyTrade run's resting stop
        for exit_id, run_id, kind, state, watched in (
            (1, 1, "stop", "watching", 0), (2, 1, "stop", "watching", 1),
            (3, 1, "target", "armed", 0), (4, 1, "stop", "failed", 0),
            (5, 2, "stop", "working", 0),
        ):  # fmt: skip
            connection.execute(text(
                "INSERT INTO bot_exits (id, run_id, group_position, kind, state, short_symbol, "
                "long_symbol, contracts, rearms, log, created_at, updated_at, legs, watched) "
                f"VALUES ({exit_id}, {run_id}, 0, '{kind}', '{state}', '', '', 1, 0, '[]', "
                f"'2026-10-12 13:48:00', '2026-10-12 13:48:00', '[]', {watched})"
            ))  # fmt: skip
    migrate(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        held = {i: session.get(BotExit, i).app_held for i in range(1, 6)}
        states = {i: session.get(BotExit, i).state for i in range(1, 6)}
    assert held == {1: True, 2: False, 3: False, 4: True, 5: False}
    assert states == {1: "watching", 2: "watching", 3: "armed", 4: "failed", 5: "working"}


def test_0039_adds_ignore_bidless_longs_off_and_downgrade_drops_it(tmp_path):
    """Bots: Ignore bid-less longs. Every existing chase row reads off; downgrade drops it."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0038")
        connection.execute(
            text("INSERT INTO strategies (name, created_at) VALUES ('s', '2026-10-12 00:00:00')")
        )
        connection.execute(
            text(
                "INSERT INTO bots (name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) SELECT 'b', id, 'tastytrade', "
                "'5WX01234', 1, 1, 3, 4, '2026-10-12 00:00:00' FROM strategies LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_chase (bot_id, phase, start_offset, step, interval_seconds, "
                "max_tries) SELECT id, 'exit', 0, 0.05, 5, 6 FROM bots"
            )
        )
        command.upgrade(config, "0039")
        found = connection.execute(text("SELECT ignore_bidless_longs FROM bot_chase")).all()
        assert [tuple(r) for r in found] == [(0,)]
        command.downgrade(config, "0038")
        columns = {c[1] for c in connection.execute(text("PRAGMA table_info(bot_chase)"))}
    assert "ignore_bidless_longs" not in columns


def test_0040_downgrade_drops_manual_bots(tmp_path):
    from alembic import command
    from sqlalchemy import inspect

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0039")
    columns = {c["name"] for c in inspect(engine).get_columns("bots")}
    assert "manual_bots" not in inspect(engine).get_table_names()
    assert not {"per_trade", "manual_bot_id"} & columns
    migrate(engine)
    assert "manual_bots" in inspect(engine).get_table_names()


def test_0040_downgrade_keeps_bots_and_their_runs(tmp_path):
    """Final review M3: dropping manual_bot_id (a REFERENCES column) in place works and
    never cascades: a bot pointing at a manual bot and its runs survive the downgrade."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    config = alembic_config()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO tags (name) VALUES ('s')"))
        connection.execute(
            text(
                "INSERT INTO manual_bots (id, name, target_unit, stop_unit, close_dte, alerts, "
                "created_at, updated_at) VALUES (1, 'm', '%', '%', 0, '[]', "
                "'2026-10-12 00:00:00', '2026-10-12 00:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bots (id, name, tag_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at, per_trade, manual_bot_id) SELECT 1, "
                "'b', id, 'tastytrade', '5WX01234', 1, 0, 3, 4, '2026-10-12 00:00:00', 1, 1 "
                "FROM tags LIMIT 1"
            )
        )
        connection.execute(
            text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "legs, filled_legs, contracts, chase_log, created_at, updated_at) VALUES (1, 1, "
                "'bot-1-0947-20261013', '2026-10-13', '09:47', 'filled', 1, '[]', '[]', 1, '[]', "
                "'2026-10-13 13:47:00', '2026-10-13 13:47:00')"
            )
        )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0039")
        bots = connection.execute(text("SELECT id FROM bots")).all()
        bot_runs = connection.execute(text("SELECT id, bot_id FROM bot_runs")).all()
        columns = {c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))}
    assert [tuple(r) for r in bots] == [(1,)] and [tuple(r) for r in bot_runs] == [(1, 1)]
    assert not {"per_trade", "manual_bot_id"} & columns


def _upgrade_to_0042_with_strategies(tmp_path):
    """A database at 0042 with a reused tag, a strategy-filed trade, a bot with a run,
    and dashboards filtering by strategy and by "no strategy"."""
    import json

    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "t.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0042")
        connection.execute(text("INSERT INTO tags (name) VALUES ('meic')"))
        connection.execute(
            text("INSERT INTO strategies (name, created_at) VALUES ('MEIC', '2026-10-12 00:00:00')")
        )
        ids = dict(connection.execute(text("SELECT name, id FROM strategies")).all())
        connection.execute(
            text(
                "INSERT INTO trade_edits (position_key, strategy_id, updated_at) "
                "VALUES ('k1', :s, '2026-10-12 00:00:00'), ('k2', :w, '2026-10-12 00:00:00'), "
                "('k3', NULL, '2026-10-12 00:00:00')"
            ),
            {"s": ids["MEIC"], "w": ids["Wheel"]},
        )
        connection.execute(
            text(
                "INSERT INTO bots (id, name, strategy_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at) VALUES (1, 'b', :s, 'tastytrade', "
                "'5WX01234', 1, 0, 3, 4, '2026-10-12 00:00:00')"
            ),
            {"s": ids["MEIC"]},
        )
        connection.execute(
            text(
                "INSERT INTO bot_runs (id, bot_id, key, trading_date, entry_time, status, live, "
                "legs, filled_legs, contracts, chase_log, created_at, updated_at) VALUES (1, 1, "
                "'bot-1-0947-20261013', '2026-10-13', '09:47', 'filled', 1, '[]', '[]', 1, '[]', "
                "'2026-10-13 13:47:00', '2026-10-13 13:47:00')"
            )
        )
        for order, filters in enumerate(
            (
                {"strategy": [str(ids["MEIC"])], "days": 30},
                {"strategy": ["none"]},
                {"tag": ["99"]},
            )
        ):
            connection.execute(
                text(
                    "INSERT INTO dashboards (name, sort_order, filters, created_at) "
                    "VALUES (:n, :o, :f, '2026-10-12 00:00:00')"
                ),
                {"n": f"d{order}", "o": order, "f": json.dumps(filters)},
            )
    return engine, config


def test_0043_turns_strategies_into_tags(tmp_path):
    import json

    from sqlalchemy import inspect, text

    engine, _ = _upgrade_to_0042_with_strategies(tmp_path)
    migrate(engine)
    with engine.connect() as connection:
        tags = dict(connection.execute(text("SELECT name, id FROM tags")).all())
        tagged = connection.execute(
            text(
                "SELECT trade_tags.position_key, tags.name FROM trade_tags "
                "JOIN tags ON tags.id = trade_tags.tag_id ORDER BY 1"
            )
        ).all()
        bot = connection.execute(text("SELECT tag_id FROM bots WHERE id = 1")).scalar()
        runs = connection.execute(text("SELECT id FROM bot_runs")).all()
        saved = [
            json.loads(r[0])
            for r in connection.execute(text("SELECT filters FROM dashboards ORDER BY sort_order"))
        ]
        integrity = connection.execute(text("PRAGMA integrity_check")).scalar()
        foreign = connection.execute(text("PRAGMA foreign_key_check")).all()
    assert "MEIC" not in tags and "meic" in tags  # an existing tag is reused, any case
    assert [tuple(r) for r in tagged] == [("k1", "meic"), ("k2", "Wheel")]
    assert bot == tags["meic"]
    assert [tuple(r) for r in runs] == [(1,)]  # the bot's runs survive (no table copy)
    assert saved == [{"days": 30, "tag": [str(tags["meic"])]}, {"tag": ["none"]}, {"tag": ["99"]}]
    assert integrity == "ok" and foreign == []
    inspector = inspect(engine)
    assert "strategies" not in inspector.get_table_names()
    assert "strategy_id" not in {c["name"] for c in inspector.get_columns("trade_edits")}
    bot_columns = {c["name"]: c for c in inspector.get_columns("bots")}
    assert "strategy_id" not in bot_columns and not bot_columns["tag_id"]["nullable"]


def test_0043_a_tag_a_bot_files_under_cant_be_deleted(tmp_path):
    from sqlalchemy import exc, text

    engine, _ = _upgrade_to_0042_with_strategies(tmp_path)
    migrate(engine)
    with pytest.raises(exc.IntegrityError), engine.begin() as connection:
        connection.execute(text("DELETE FROM tags WHERE name = 'meic'"))


def test_0043_downgrade_rebuilds_strategies_from_the_bots_tags(tmp_path):
    import json

    from alembic import command
    from sqlalchemy import text

    engine, config = _upgrade_to_0042_with_strategies(tmp_path)
    migrate(engine)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0042")
        strategies = dict(connection.execute(text("SELECT id, name FROM strategies")).all())
        bot = connection.execute(text("SELECT strategy_id FROM bots WHERE id = 1")).scalar()
        runs = connection.execute(text("SELECT id FROM bot_runs")).all()
        saved = json.loads(
            connection.execute(text("SELECT filters FROM dashboards WHERE name = 'd1'")).scalar()
        )
        columns = {c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))}
    assert list(strategies.values()) == ["meic"] and strategies[bot] == "meic"
    assert [tuple(r) for r in runs] == [(1,)] and "tag_id" not in columns
    assert saved == {"strategy": ["none"]}
    migrate(engine)  # and up again


def test_0045_marks_existing_settlements_as_checked_against_the_broker(tmp_path):
    """Settlements from before #186 never raise a broker-record alert after the upgrade."""
    from alembic import command
    from sqlalchemy import select, text

    from optionsjourney.db import alembic_config
    from optionsjourney.db.models import ExpirySettlement

    engine = make_engine(tmp_path / "t.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0044")
        connection.execute(
            text(
                "INSERT INTO expiry_settlements (broker, account_number, symbol, underlying, "
                "put_call, strike, expiration, sign, quantity, close_price, fee, created_at) "
                "VALUES ('tastytrade', '5WX01234', 'SPXW  261009P05800000', 'SPXW', 'PUT', "
                "'5800', '2026-10-09', -1, '1', '5850', '0', '2026-10-09 20:20:00')"
            )
        )
    migrate(engine)
    with make_session_factory(engine)() as session:
        row = session.scalars(select(ExpirySettlement)).one()
    assert row.broker_alerted_at is not None and row.broker_alerted_at.tzinfo is not None


def test_0046_gives_existing_bots_no_before_expiration_lists(tmp_path):
    """#207: every existing bot keeps blacking out exactly as before; downgrade drops it."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0045")
        connection.execute(text("INSERT INTO tags (name) VALUES ('t')"))
        connection.execute(
            text(
                "INSERT INTO bots (name, tag_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at, use_blackout_days, "
                "blackout_date_sets) SELECT 'b', id, 'tastytrade', '5WX01234', 0, 1, 3, 4, "
                "'2026-10-12 00:00:00', 1, '[\"FOMC\"]' FROM tags LIMIT 1"
            )
        )
        command.upgrade(config, "0046")
        found = connection.execute(
            text("SELECT blackout_date_sets, blackout_dte_sets FROM bots")
        ).all()
        assert [tuple(r) for r in found] == [('["FOMC"]', "[]")]
        command.downgrade(config, "0045")
        columns = {c[1] for c in connection.execute(text("PRAGMA table_info(bots)"))}
    assert "blackout_dte_sets" not in columns and "blackout_date_sets" in columns


def test_0053_makes_bot_labels_journal_tags_with_the_main_tag_first(tmp_path):
    """#278 (decision 0110): one set of tags. A label becomes a journal tag (an existing
    one's spelling wins), the main tag is held first, a state word is dropped."""
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0052")
        connection.execute(
            text("INSERT OR IGNORE INTO tags (name) VALUES ('Iron Condors'), ('0DTE')")
        )
        connection.execute(
            text(
                "INSERT INTO bots (name, tag_id, broker, account_number, live, enabled, "
                "max_per_trade, max_per_day, created_at, tags) SELECT 'b', id, 'tastytrade', "
                "'5WX01234', 0, 1, 3, 4, '2026-10-12 00:00:00', "
                "'[\"0dte\", \"FOMC\", \"iron condors\", \"Live\"]' FROM tags "
                "WHERE name = 'Iron Condors'"
            )
        )
        command.upgrade(config, "0053")
        held = connection.execute(text("SELECT tags FROM bots")).scalar_one()
        names = {r[0] for r in connection.execute(text("SELECT name FROM tags"))}
    assert held == '["Iron Condors", "0DTE", "FOMC"]' and "FOMC" in names


def test_migrations_have_exactly_one_head():
    """Two branches that each add a migration must not leave two heads (#236)."""
    from alembic.script import ScriptDirectory

    from optionsjourney.db import alembic_config

    assert len(ScriptDirectory.from_config(alembic_config()).get_heads()) == 1


def test_0055_never_reuses_a_deleted_ideas_id(tmp_path):
    """A new idea never takes the id of one just deleted, so an old link or a ticket
    still naming it can't land on a different idea (#292)."""
    from sqlalchemy import text

    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    insert = text(
        "INSERT INTO ideas (ticker, label, cards, created_at) "
        "VALUES ('SPX', 'x', '[]', '2026-10-23 14:00:00')"
    )
    with engine.begin() as connection:
        connection.execute(insert)
        connection.execute(insert)
        connection.execute(text("DELETE FROM ideas WHERE id = 2"))
        connection.execute(insert)
        ids = [r[0] for r in connection.execute(text("SELECT id FROM ideas ORDER BY id"))]
    assert ids == [1, 3]
