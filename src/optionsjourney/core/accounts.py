"""Broker accounts and their balances."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class BrokerAccount:
    broker: str
    number: str
    nickname: str | None = None
    account_type: str | None = None

    @property
    def display_name(self) -> str:
        return self.nickname or self.account_type or "Account"

    @property
    def masked_number(self) -> str:
        return "••" + self.number[-4:]


def mask_number(text: str, number: str) -> str:
    """The text with every copy of the account number cut to its last four,
    for messages that are shown and logged (broker errors can quote it)."""
    return text.replace(number, "…" + number[-4:]) if number else text


@dataclass(frozen=True)
class Balance:
    net_liquidating_value: Decimal
    cash: Decimal
    buying_power: Decimal


@dataclass(frozen=True)
class DailyValue:
    """An account's value at the end of a day."""

    day: date
    net_liquidating_value: Decimal
    cash: Decimal


@dataclass(frozen=True)
class DailyClose:
    """An underlying's closing price on a trading day."""

    symbol: str
    day: date
    close: Decimal


# Cash-settled index underlyings; brokers look these up apart from stocks
INDEXES = {"SPX", "XSP", "NDX", "RUT", "VIX", "DJX", "OEX", "XEO"}


@dataclass(frozen=True)
class AccountSummary:
    """An account with its balance, or the reason the balance couldn't be read.
    A balance with an error too is an earlier one, read at `as_of`."""

    account: BrokerAccount
    balance: Balance | None = None
    error: str | None = None
    as_of: datetime | None = None  # when the balance was read


def total_net_liquidating_value(summaries: Iterable[AccountSummary]) -> Decimal:
    return sum(
        (s.balance.net_liquidating_value for s in summaries if s.balance is not None),
        Decimal("0"),
    )
