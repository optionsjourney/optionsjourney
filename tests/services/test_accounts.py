from datetime import UTC, datetime
from decimal import Decimal

import pytest

from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import Balance, BrokerAccount
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services.accounts import live_overviews, stored_overviews

EARLIER = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)

ALPHA = BrokerAccount("tastytrade", "5WX01234", "Alpha")
ROTH = BrokerAccount("tastytrade", "5WY05678", None, "Roth IRA")


def _balance(value):
    return Balance(Decimal(value), Decimal("100"), Decimal("200"))


class FakeBroker:
    def __init__(self, name="TastyTrade", broker="tastytrade", accounts=(), balances=None):
        self.name = name
        self.broker = broker
        self._accounts = list(accounts)
        self.balances = balances or {}
        self.fail_accounts = None

    def accounts(self):
        if self.fail_accounts:
            raise BrokerError(self.fail_accounts)
        return self._accounts

    def balance(self, number):
        if number not in self.balances:
            raise BrokerError("TastyTrade answered 502.")
        return self.balances[number]


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    return make_session_factory(engine)


def test_nothing_stored_yet(session_factory):
    [overview] = stored_overviews([FakeBroker(accounts=[ALPHA])], session_factory)
    assert overview.broker == "TastyTrade"
    assert overview.summaries == []


def test_live_read_is_stored_for_next_time(session_factory):
    tasty = FakeBroker(accounts=[ALPHA, ROTH], balances={"5WX01234": _balance("25000")})
    [live] = live_overviews([tasty], session_factory, now=lambda: NOW)
    assert [s.account.number for s in live.summaries] == ["5WX01234", "5WY05678"]
    assert live.summaries[0].balance == _balance("25000")
    assert live.summaries[0].as_of == NOW
    assert live.summaries[1].error == "TastyTrade answered 502."

    [stored] = stored_overviews([tasty], session_factory)
    [summary] = stored.summaries  # the account whose balance failed has none
    assert summary.account == ALPHA  # nickname kept for the name shown
    assert summary.balance == _balance("25000")
    assert summary.as_of == NOW
    assert stored.as_of == NOW


def test_a_failed_balance_falls_back_to_the_stored_one(session_factory):
    tasty = FakeBroker(accounts=[ALPHA], balances={"5WX01234": _balance("25000")})
    live_overviews([tasty], session_factory, now=lambda: EARLIER)
    tasty.balances = {}

    [live] = live_overviews([tasty], session_factory, now=lambda: NOW)
    [summary] = live.summaries
    assert summary.balance == _balance("25000")
    assert summary.as_of == EARLIER
    assert summary.error == "TastyTrade answered 502."


def test_a_broker_problem_keeps_the_stored_balances(session_factory):
    tasty = FakeBroker(accounts=[ALPHA], balances={"5WX01234": _balance("25000")})
    live_overviews([tasty], session_factory, now=lambda: EARLIER)
    tasty.fail_accounts = "TastyTrade rejected the access token."

    [live] = live_overviews([tasty], session_factory, now=lambda: NOW)
    assert live.problem == "TastyTrade rejected the access token."
    assert [s.balance for s in live.summaries] == [_balance("25000")]
    assert live.as_of == EARLIER


def test_a_closed_account_is_forgotten(session_factory):
    tasty = FakeBroker(
        accounts=[ALPHA, ROTH],
        balances={"5WX01234": _balance("25000"), "5WY05678": _balance("900")},
    )
    live_overviews([tasty], session_factory, now=lambda: EARLIER)
    tasty._accounts = [ALPHA]
    live_overviews([tasty], session_factory, now=lambda: NOW)

    [stored] = stored_overviews([tasty], session_factory)
    assert [s.account.number for s in stored.summaries] == ["5WX01234"]


def test_each_broker_sees_only_its_own(session_factory):
    tasty = FakeBroker(accounts=[ALPHA], balances={"5WX01234": _balance("25000")})
    schwab = FakeBroker(
        "Schwab",
        "schwab",
        [BrokerAccount("schwab", "12349999", None, "Margin")],
        {"12349999": _balance("1000")},
    )
    live_overviews([tasty, schwab], session_factory, now=lambda: NOW)

    stored = stored_overviews([tasty, schwab], session_factory)
    assert [[s.account.number for s in o.summaries] for o in stored] == [
        ["5WX01234"],
        ["12349999"],
    ]
