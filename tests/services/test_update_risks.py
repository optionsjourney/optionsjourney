from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import EntryTimeDef, iron_condor_skeleton
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import BotExit, BotRun, Tag
from optionsjourney.services import bot_store, update_risks

D = Decimal
# 11:00 New York on a Tuesday, and 18:00 the same day (EDT is UTC-4).
OPEN = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
CLOSED = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def arm_bot(factory, enabled=True, name="IC"):
    with factory() as session:
        tag = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    bot = iron_condor_skeleton(name)
    targets = {0: D("2.70"), 1: D("75"), 2: D("2.90"), 3: D("-75")}
    bot = replace(
        bot, tag_id=tag, account_number="5WX01234",
        legs=tuple(replace(leg, target=targets[leg.position]) for leg in bot.legs),
        groups=tuple(replace(g, stop_pct=D("95"), target_pct=D("50")) for g in bot.groups),
        entry_times=(EntryTimeDef("09:47"),), live=True, enabled=enabled,
    )  # fmt: skip
    bot_id = bot_store.save(factory, bot, OPEN)[0]
    if enabled:
        assert bot_store.set_armed(factory, bot_id, True, OPEN)
    return bot_id


def add_run(session, bot_id, key, status, live=True):
    run = BotRun(
        bot_id=bot_id, key=key, trading_date=OPEN.date(), entry_time="09:47",
        status=status, live=live, legs=[], filled_legs=[], chase_log=[], contracts=1,
        created_at=OPEN, updated_at=OPEN,
    )  # fmt: skip
    session.add(run)
    session.flush()
    return run


def add_exit(session, run, kind, state, watched=True):
    session.add(BotExit(
        run_id=run.id, group_position=0, kind=kind, state=state, short_symbol="S",
        long_symbol="L", contracts=1, watched=watched, created_at=OPEN, updated_at=OPEN,
    ))  # fmt: skip


def test_all_four_risks_in_order_with_counts(factory):
    bot_id = arm_bot(factory)
    with factory() as session:
        filled = add_run(session, bot_id, "k1", "filled")
        add_exit(session, filled, "stop", "watching")
        add_exit(session, filled, "stop", "armed", watched=True)  # not pending/watching
        add_run(session, bot_id, "k2", "running")
        session.commit()
    risks = update_risks.live_risks(factory, OPEN)
    assert [r.key for r in risks] == [
        "market_open",
        "armed_bots",
        "watched_stops",
        "working_orders",
    ]
    assert risks[0].title == "The market is open."
    assert risks[1].title == "1 bot armed"
    assert risks[2].title.startswith("1 open trade has a stop the app watches")
    assert risks[3].title == "1 bot order still working"


def test_nothing_live_gives_no_risks(factory):
    assert update_risks.live_risks(factory, CLOSED) == []


def test_paper_runs_and_exit_orders_count_correctly(factory):
    bot_id = arm_bot(factory, enabled=False)
    with factory() as session:
        paper = add_run(session, bot_id, "p1", "running", live=False)
        add_exit(session, paper, "stop", "watching")
        live = add_run(session, bot_id, "p2", "filled")
        add_exit(session, live, "target", "firing")
        add_exit(session, live, "stop", "watching", watched=False)  # the broker holds it
        session.commit()
    keys = [r.key for r in update_risks.live_risks(factory, CLOSED)]
    assert keys == ["working_orders"]
