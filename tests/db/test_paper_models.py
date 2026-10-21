"""Paper trading's data model: a run's Paper marker and its pretend fills."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from tests.services.bot_fakes import make

from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import BotRun, PaperFill

NOW = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def add_run(factory, **changes):
    bot_id = make(factory)
    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key="bot-paper-1", trading_date=NOW.date(), entry_time="09:47",
            status="filled", live=False, legs=[], filled_legs=[], created_at=NOW,
            updated_at=NOW, **changes,
        )  # fmt: skip
        session.add(run)
        session.commit()
        return run.id


def test_paper_fill_round_trip(factory):
    run_id = add_run(factory, paper=True)
    with factory() as session:
        session.add(
            PaperFill(
                run_id=run_id, kind="entry", price="5.025", contracts=1, reason="entry",
                legs=[{"symbol": "SPXW  261016P05000000", "mid": "5.025"}], at=NOW,
            )
        )  # fmt: skip
        session.commit()
    with factory() as session:
        run = session.get(BotRun, run_id)
        fills = session.scalars(select(PaperFill).where(PaperFill.run_id == run_id)).all()
    assert run.paper is True
    assert len(fills) == 1
    assert Decimal(fills[0].price) == Decimal("5.025")
    assert fills[0].group is None and fills[0].at == NOW


def test_paper_defaults_false(factory):
    run_id = add_run(factory)
    with factory() as session:
        assert session.get(BotRun, run_id).paper is False


def test_0037_downgrade_drops_the_marker_and_the_fills(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from optionsjourney.db import alembic_config

    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0036")
        runs = {c[1] for c in connection.execute(text("PRAGMA table_info(bot_runs)"))}
        tables = {r[0] for r in connection.execute(text("SELECT name FROM sqlite_master"))}
    assert "paper" not in runs and "paper_fills" not in tables
