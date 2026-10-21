from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from optionsjourney.core.accounts import Balance, BrokerAccount, DailyValue
from optionsjourney.core.transactions import RawTransaction
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import AccountValue
from optionsjourney.services import account_values
from optionsjourney.services.sync import sync_broker

NOW = datetime(2026, 10, 9, 21, 0, tzinfo=UTC)  # Fri Oct 9, 17:00 ET


class FakeTasty:
    name = "TastyTrade"
    broker = "tastytrade"

    def __init__(self, deposited=True):
        self.asked = []
        self.deposited = deposited

    def accounts(self):
        return [BrokerAccount("tastytrade", "5WX01234", "Alpha")]

    def balance_history(self, number, start, end):
        self.asked.append((start, end))
        days = [end - timedelta(days=n) for n in range(3)]
        # $1,000 plus the day of the month, and the $500 deposited on Oct 8
        deposit = Decimal("500") if self.deposited else Decimal("0")
        return [
            DailyValue(
                d,
                Decimal("1000") + d.day + (deposit if d >= date(2026, 10, 8) else 0),
                Decimal("10"),
            )
            for d in sorted(days)
        ]

    def transactions(self, number, start, end):
        deposit = {
            "id": 1,
            "transaction-type": "Money Movement",
            "transaction-sub-type": "Deposit",
            "executed-at": "2026-10-08T14:00:00+00:00",
            "value": "500.0",
            "value-effect": "Credit",
        }
        return [
            RawTransaction(
                "1", datetime.fromisoformat(deposit["executed-at"]), "Money Movement", deposit
            )
        ]


class FakeSchwab:
    name = "Schwab"
    broker = "schwab"

    def __init__(self, value="2000"):
        self.value = Decimal(value)

    def accounts(self):
        return [BrokerAccount("schwab", "12345678", "Beta")]

    def balance(self, number):
        return Balance(self.value, Decimal("0"), Decimal("0"))


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    return make_session_factory(engine)


def _rows(factory):
    with factory() as session:
        return {(r.broker, r.day): r for r in session.query(AccountValue)}


def test_tastytrade_history_is_backfilled_a_year(session_factory):
    tasty = FakeTasty()
    account_values.sync_values([tasty], session_factory, now=lambda: NOW)
    assert tasty.asked == [(date(2025, 10, 9), date(2026, 10, 9))]
    rows = _rows(session_factory)
    assert {day for _, day in rows} == {date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9)}
    assert rows[("tastytrade", date(2026, 10, 9))].source == "history"


def test_tastytrade_history_then_continues_from_the_last_day(session_factory):
    tasty = FakeTasty()
    account_values.sync_values([tasty], session_factory, now=lambda: NOW)
    account_values.sync_values([tasty], session_factory, now=lambda: NOW + timedelta(days=3))
    # A few days back from the last stored day, in case the broker revised them
    assert tasty.asked[1] == (date(2026, 10, 6), date(2026, 10, 12))


def test_schwab_records_one_value_a_day_and_keeps_the_latest(session_factory):
    account_values.sync_values([FakeSchwab("2000")], session_factory, now=lambda: NOW)
    account_values.sync_values(
        [FakeSchwab("2100")], session_factory, now=lambda: NOW + timedelta(minutes=5)
    )
    rows = _rows(session_factory)
    assert list(rows) == [("schwab", date(2026, 10, 9))]
    assert rows[("schwab", date(2026, 10, 9))].net_liquidating_value == Decimal("2100")
    assert rows[("schwab", date(2026, 10, 9))].source == "daily"


def test_a_broker_problem_is_reported_not_raised(session_factory):
    class Broken(FakeSchwab):
        def accounts(self):
            from optionsjourney.brokers.http import BrokerError

            raise BrokerError("Schwab is down")

    problems = account_values.sync_values([Broken()], session_factory, now=lambda: NOW)
    assert problems == ["Schwab: Schwab is down"]


def test_an_unexpected_error_from_one_broker_is_a_problem_not_a_crash(session_factory):
    class Odd(FakeTasty):
        def accounts(self):
            raise KeyError("accounts")

    problems = account_values.sync_values([Odd()], session_factory, now=lambda: NOW)
    assert problems == ["TastyTrade: TastyTrade sent a reply the app couldn't read."]


def test_load_values_adds_up_accounts_and_takes_deposits_out(session_factory):
    tasty = FakeTasty()
    sync_broker(tasty, session_factory, now=lambda: NOW)  # brings in the deposit
    account_values.sync_values([tasty, FakeSchwab("2000")], session_factory, now=lambda: NOW)
    view = account_values.load_values(session_factory, now=NOW)
    assert [p.day for p in view.points] == [date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9)]
    assert view.points[-1].value == Decimal("1509") + Decimal("2000")
    assert view.points[1].flow == Decimal("500")
    # 1007 -> 3509: +500 deposited, +2000 Schwab joining, so a gain of 2
    assert view.performance.gain == Decimal("2")
    assert view.schwab_since == date(2026, 10, 9)


def test_load_values_for_one_account(session_factory):
    account_values.sync_values([FakeTasty(), FakeSchwab()], session_factory, now=lambda: NOW)
    view = account_values.load_values(session_factory, account="schwab:12345678", now=NOW)
    assert [p.value for p in view.points] == [Decimal("2000")]
