"""Connected accounts and their balances, for the accounts page.

The page shows the balances last read straight away (stored_overviews), then
asks the brokers for fresh ones (live_overviews), which are kept for next
time. A broker that can't be read leaves its last balances showing (#74)."""

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import AccountSummary, Balance, BrokerAccount
from optionsjourney.db.models import LatestBalance


class AccountsClient(Protocol):
    name: str
    broker: str

    def accounts(self) -> list[BrokerAccount]: ...

    def balance(self, account_number: str) -> Balance: ...


@dataclass(frozen=True)
class BrokerOverview:
    broker: str
    summaries: list[AccountSummary] = field(default_factory=list)
    problem: str | None = None

    @property
    def as_of(self) -> datetime | None:
        """When the oldest balance shown was read."""
        times = [s.as_of for s in self.summaries if s.balance is not None and s.as_of]
        return min(times) if times else None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def broker_overview(
    client: AccountsClient, now: Callable[[], datetime] = _utcnow
) -> BrokerOverview:
    """Every account at one broker with its balance. A failure reading one
    balance is shown on that account; it doesn't hide the others."""
    try:
        accounts = client.accounts()
    except (CredentialsUnavailable, BrokerError) as e:
        return BrokerOverview(broker=client.name, problem=str(e))

    summaries = []
    for account in accounts:
        try:
            balance = client.balance(account.number)
            summaries.append(AccountSummary(account, balance=balance, as_of=now()))
        except (CredentialsUnavailable, BrokerError) as e:
            summaries.append(AccountSummary(account, error=str(e)))
    return BrokerOverview(broker=client.name, summaries=summaries)


def stored_overviews(
    clients: Iterable[AccountsClient], session_factory: sessionmaker
) -> list[BrokerOverview]:
    """Each broker's balances as last read, without asking the broker."""
    stored = _load(session_factory)
    return [
        BrokerOverview(broker=c.name, summaries=list(stored.get(c.broker, {}).values()))
        for c in clients
    ]


def live_overviews(
    clients: Iterable[AccountsClient],
    session_factory: sessionmaker,
    now: Callable[[], datetime] = _utcnow,
) -> list[BrokerOverview]:
    """Each broker's balances read now, all brokers at once, and kept for
    next time. Where a read fails, the last balance stands in."""
    clients = list(clients)
    with ThreadPoolExecutor(max_workers=max(len(clients), 1)) as pool:
        overviews = list(pool.map(lambda c: broker_overview(c, now), clients))
    stored = _load(session_factory)
    result = []
    for client, overview in zip(clients, overviews, strict=True):
        last = stored.get(client.broker, {})
        if overview.problem:
            result.append(replace(overview, summaries=list(last.values())))
            continue
        _save(session_factory, client.broker, overview.summaries)
        result.append(replace(overview, summaries=[_or_last(s, last) for s in overview.summaries]))
    return result


def _or_last(summary: AccountSummary, last: dict[str, AccountSummary]) -> AccountSummary:
    earlier = last.get(summary.account.number)
    if summary.balance is not None or earlier is None:
        return summary
    return replace(summary, balance=earlier.balance, as_of=earlier.as_of)


def _load(session_factory: sessionmaker) -> dict[str, dict[str, AccountSummary]]:
    """Stored balances by broker, then account number."""
    with session_factory() as session:
        rows = session.scalars(
            select(LatestBalance).order_by(LatestBalance.broker, LatestBalance.account_number)
        )
        found: dict[str, dict[str, AccountSummary]] = {}
        for row in rows:
            account = BrokerAccount(row.broker, row.account_number, row.nickname, row.account_type)
            balance = Balance(row.net_liquidating_value, row.cash, row.buying_power)
            found.setdefault(row.broker, {})[row.account_number] = AccountSummary(
                account, balance=balance, as_of=row.read_at
            )
    return found


def _save(session_factory: sessionmaker, broker: str, summaries: list[AccountSummary]) -> None:
    """Keep the balances just read; forget accounts the broker no longer lists."""
    with session_factory() as session:
        session.execute(
            delete(LatestBalance).where(
                LatestBalance.broker == broker,
                LatestBalance.account_number.not_in([s.account.number for s in summaries]),
            )
        )
        for s in summaries:
            if s.balance is None:
                continue
            session.merge(
                LatestBalance(
                    broker=broker,
                    account_number=s.account.number,
                    nickname=s.account.nickname,
                    account_type=s.account.account_type,
                    net_liquidating_value=s.balance.net_liquidating_value,
                    cash=s.balance.cash,
                    buying_power=s.balance.buying_power,
                    read_at=s.as_of,
                )
            )
        session.commit()
