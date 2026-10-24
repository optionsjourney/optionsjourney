from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.transactions import RawTransaction
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import Account, BrokerTransaction, Setting, SyncState
from optionsjourney.services.sync import sync_all, sync_broker

NOW = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)  # 4pm ET
ALPHA = BrokerAccount("tastytrade", "5WX01234", "Alpha")
ROTH = BrokerAccount("tastytrade", "5WY05678", None, "Roth IRA")


def _raw(transaction_id, fees="1.0", at=datetime(2026, 10, 9, 13, 32, tzinfo=UTC)):
    return RawTransaction(
        transaction_id=transaction_id,
        executed_at=at,
        transaction_type="Trade",
        data={"id": transaction_id, "commission": fees},
    )


class FakeBroker:
    name = "TastyTrade"
    broker = "tastytrade"

    def __init__(self, accounts, transactions=None, failing=()):
        self._accounts = accounts
        self._transactions = transactions or {}
        self._failing = set(failing)
        self.windows = []

    def accounts(self):
        return self._accounts

    def transactions(self, number, start, end):
        self.windows.append((number, start, end))
        if number in self._failing:
            raise BrokerError("TastyTrade answered 502.")
        return self._transactions.get(number, [])


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    return make_session_factory(engine)


def _count(session_factory, model):
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_first_sync_starts_history_today(session_factory):
    broker = FakeBroker([ALPHA], {"5WX01234": [_raw("1"), _raw("2")]})
    result = sync_broker(broker, session_factory, now=lambda: NOW)

    assert result.ok
    assert (result.accounts, result.new, result.updated) == (1, 2, 0)
    assert broker.windows == [("5WX01234", date(2026, 10, 9), date(2026, 10, 9))]
    assert _count(session_factory, BrokerTransaction) == 2
    with session_factory() as session:
        assert session.scalar(select(Account.nickname)) == "Alpha"
        assert session.get(SyncState, ("tastytrade", "5WX01234")).synced_through == NOW
        assert session.get(Setting, "history_starts").value == "2026-10-09"


def test_next_sync_overlaps_a_week_and_updates_changed_records(session_factory):
    with session_factory() as session:
        session.add(Setting(key="history_starts", value="2026-09-01", updated_at=NOW))
        session.commit()
    sync_broker(
        FakeBroker([ALPHA], {"5WX01234": [_raw("1", fees="1.0")]}), session_factory, now=lambda: NOW
    )
    later = NOW + timedelta(days=1)
    broker = FakeBroker([ALPHA], {"5WX01234": [_raw("1", fees="1.15"), _raw("3")]})
    result = sync_broker(broker, session_factory, now=lambda: later)

    assert (result.new, result.updated) == (1, 1)
    assert broker.windows == [("5WX01234", date(2026, 10, 2), date(2026, 10, 10))]
    with session_factory() as session:
        row = session.scalar(
            select(BrokerTransaction).where(BrokerTransaction.transaction_id == "1")
        )
        assert row.raw["commission"] == "1.15"
        assert row.first_seen_at == NOW
        assert row.updated_at == later


def test_unchanged_records_are_left_alone(session_factory):
    broker = FakeBroker([ALPHA], {"5WX01234": [_raw("1")]})
    sync_broker(broker, session_factory, now=lambda: NOW)
    result = sync_broker(broker, session_factory, now=lambda: NOW + timedelta(hours=1))
    assert (result.new, result.updated) == (0, 0)


def test_repeated_transaction_in_one_fetch(session_factory):
    broker = FakeBroker([ALPHA], {"5WX01234": [_raw("1"), _raw("1")]})
    assert sync_broker(broker, session_factory, now=lambda: NOW).new == 1


def test_one_failing_account_does_not_stop_the_others(session_factory):
    broker = FakeBroker([ROTH, ALPHA], {"5WX01234": [_raw("1")]}, failing={"5WY05678"})
    result = sync_broker(broker, session_factory, now=lambda: NOW)

    assert not result.ok
    assert result.problems == ["Roth IRA ••5678: TastyTrade answered 502."]
    assert result.new == 1
    with session_factory() as session:
        failed = session.get(SyncState, ("tastytrade", "5WY05678"))
        assert failed.synced_through is None
        assert failed.last_error == "TastyTrade answered 502."


def test_broker_unavailable(session_factory):
    class Unavailable(FakeBroker):
        def accounts(self):
            raise BrokerError("Can't reach TastyTrade.")

    result = sync_broker(Unavailable([]), session_factory, now=lambda: NOW)
    assert result.problems == ["Can't reach TastyTrade."]
    assert result.accounts == 0


def test_nothing_before_history_starts_is_added_but_stored_rows_still_update(session_factory):
    old = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
    with session_factory() as session:
        session.add(Setting(key="history_starts", value="2026-10-05", updated_at=NOW))
        session.add(
            BrokerTransaction(
                broker="tastytrade",
                account_number="5WX01234",
                transaction_id="kept",
                transaction_type="Trade",
                executed_at=old,
                raw={"id": "kept", "commission": "1.0"},
                first_seen_at=NOW,
                updated_at=NOW,
            )
        )
        session.commit()
    broker = FakeBroker(
        [ALPHA],
        {"5WX01234": [_raw("kept", fees="2.0", at=old), _raw("gone", at=old), _raw("new")]},
    )
    result = sync_broker(broker, session_factory, now=lambda: NOW)

    assert (result.new, result.updated) == (1, 0)
    assert broker.windows[0][1] == date(2026, 10, 9)
    with session_factory() as session:
        ids = set(session.scalars(select(BrokerTransaction.transaction_id)))
    assert ids == {"kept", "new"}


def test_an_unexpected_error_from_one_broker_doesnt_stop_the_next(session_factory):
    class Schwab(FakeBroker):
        name = "Schwab"
        broker = "schwab"

        def accounts(self):
            raise KeyError("accounts")

    tasty = FakeBroker([ALPHA], {"5WX01234": [_raw("1")]})
    results = sync_all([Schwab([]), tasty], session_factory, now=lambda: NOW)

    assert [r.broker for r in results] == ["Schwab", "TastyTrade"]
    assert results[0].problems == ["Schwab sent a reply the app couldn't read."]
    assert results[1].ok and results[1].new == 1


def test_an_unexpected_error_in_one_account_is_recorded_on_its_sync_state(session_factory):
    class Odd(FakeBroker):
        def transactions(self, number, start, end):
            if number == "5WX01234":
                raise KeyError("id")
            return super().transactions(number, start, end)

    broker = Odd([ALPHA, ROTH], {"5WY05678": [_raw("2")]})
    result = sync_broker(broker, session_factory, now=lambda: NOW)

    assert len(result.problems) == 1 and "Alpha" in result.problems[0]
    assert result.new == 1
    with session_factory() as session:
        state = session.get(SyncState, ("tastytrade", "5WX01234"))
        assert state.synced_through is None
        assert state.last_error == "TastyTrade sent a reply the app couldn't read."
        assert session.get(SyncState, ("tastytrade", "5WY05678")).synced_through == NOW


def test_a_sync_with_new_trades_matches_them_to_bots(session_factory, monkeypatch):
    """#276: and a failing auto-match never fails the sync."""
    from optionsjourney.services import bot_match

    calls = []

    def boom(factory):
        calls.append(factory)
        raise RuntimeError("no")

    monkeypatch.setattr(bot_match, "auto_match", boom)
    tasty = FakeBroker([ALPHA], {"5WX01234": [_raw("1")]})
    [result] = sync_all([tasty], session_factory, now=lambda: NOW)
    assert result.ok and calls == [session_factory]
    sync_all([tasty], session_factory, now=lambda: NOW)  # nothing new: no match
    assert len(calls) == 1
