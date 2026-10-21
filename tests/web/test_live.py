"""Bots slice 20: the journal's chips pushed live (Tracker2's override_tags.notify_changed)."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from optionsjourney.app import create_app
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import BotExit, BotRun, BotTradeOverride, BrokerTransaction
from optionsjourney.web import live

NOW = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def a_run(factory) -> int:
    from tests.services.test_bot_store import good

    from optionsjourney.services import bot_store

    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    with factory() as session:
        run = BotRun(bot_id=bot_id, key="k", trading_date=date(2026, 10, 14), entry_time="09:47",
                     status="filled", live=True, legs=[], chase_log=[], contracts=1,
                     created_at=NOW, updated_at=NOW)  # fmt: skip
        session.add(run)
        session.commit()
        return run.id


def exit_row(run_id, state="working"):
    return BotExit(run_id=run_id, group_position=0, kind="stop", state=state, short_symbol="",
                   long_symbol="", contracts=1, rearms=0, log=[], created_at=NOW,
                   updated_at=NOW)  # fmt: skip


@pytest.fixture
def pushed(factory):
    sent = []
    live.watch_chips(factory, lambda run_id: sent.append(run_id))
    return sent


def test_a_committed_override_change_pushes_its_run_once(factory, pushed):
    run_id = a_run(factory)
    pushed.clear()
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=0, log=[], changed_at=NOW))
        session.add(BotTradeOverride(run_id=run_id, group_position=1, log=[], changed_at=NOW))
        session.flush()
        assert pushed == []  # not before the commit
        session.commit()
    assert pushed == [run_id]


def test_an_exit_state_change_pushes_but_a_log_only_change_doesnt(factory, pushed):
    run_id = a_run(factory)
    with factory() as session:
        session.add(exit_row(run_id))
        session.commit()
    assert pushed == [run_id]
    pushed.clear()
    with factory() as session:
        row = session.scalar(select(BotExit))
        row.log = [{"at": "x", "event": "checked"}]
        session.commit()
    assert pushed == []
    with factory() as session:
        session.scalar(select(BotExit)).state = "cancelled"
        session.commit()
    assert pushed == [run_id]


def test_a_rollback_pushes_nothing(factory, pushed):
    run_id = a_run(factory)
    pushed.clear()
    with factory() as session:
        session.add(exit_row(run_id))
        session.flush()
        session.rollback()
        session.commit()  # nothing left to commit
    assert pushed == []


def test_an_emit_that_breaks_never_fails_the_commit(factory):
    def broken(run_id):
        raise RuntimeError("no socket")

    live.watch_chips(factory, broken)
    run_id = a_run(factory)
    with factory() as session:
        session.add(exit_row(run_id))
        session.commit()  # no exception
    with factory() as session:
        assert session.scalar(select(BotExit)) is not None


def test_the_app_pushes_bot_tags_changed_over_its_socket(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    s = app.extensions["optionsjourney"]
    client = s.socketio.test_client(app)
    run_id = a_run(s.session_factory)
    client.get_received()
    with s.session_factory() as session:
        session.add(exit_row(run_id))
        session.commit()
    received = [m for m in client.get_received() if m["name"] == "bot_tags_changed"]
    assert received == [{"name": "bot_tags_changed", "args": [{"run_id": run_id}],
                         "namespace": "/"}]  # fmt: skip


def test_a_savepoint_rollback_keeps_what_the_outer_change_noted(factory, pushed):
    run_id = a_run(factory)
    pushed.clear()
    with factory() as session:
        session.add(exit_row(run_id))
        session.flush()  # noted
        nested = session.begin_nested()
        session.add(BotTradeOverride(run_id=run_id, group_position=0, log=[], changed_at=NOW))
        session.flush()
        nested.rollback()
        session.commit()
    assert pushed == [run_id]


def a_transaction(transaction_id="t1"):
    return BrokerTransaction(broker="tastytrade", account_number="A1", transaction_id=transaction_id,
                             transaction_type="Trade", executed_at=NOW, raw={}, first_seen_at=NOW,
                             updated_at=NOW)  # fmt: skip


def test_a_synced_fill_pushes_journal_changed_once_per_commit(factory):
    sent = []
    live.watch_journal(factory, lambda: sent.append(1))
    with factory() as session:
        session.add(a_transaction("t1"))
        session.add(a_transaction("t2"))
        session.flush()
        assert sent == []  # not before the commit
        session.commit()
    assert sent == [1]
    with factory() as session:
        session.add(BotTradeOverride(run_id=a_run(factory), group_position=0, log=[], changed_at=NOW))
        session.commit()
    assert sent == [1]  # other changes don't reload the journal


def test_a_rolled_back_fill_pushes_nothing(factory):
    sent = []
    live.watch_journal(factory, lambda: sent.append(1))
    with factory() as session:
        session.add(a_transaction())
        session.flush()
        session.rollback()
        session.commit()
    assert sent == []


def test_a_paper_fill_pushes_paper_journal_changed(factory):
    """#183: the Paper trades view reloads itself when a pretend fill is saved."""
    from decimal import Decimal

    from optionsjourney.db.models import PaperFill

    sent = []
    live.watch_paper(factory, lambda: sent.append(1))
    run_id = a_run(factory)
    with factory() as session:
        session.add(PaperFill(run_id=run_id, kind="exit", group=0, price=Decimal("-1"),
                              contracts=1, reason="stop", legs=[], at=NOW))  # fmt: skip
        session.commit()
    assert sent == [1]
    with factory() as session:
        session.add(a_transaction("t9"))
        session.commit()
    assert sent == [1]  # a broker fill reloads the real journal, not this one
