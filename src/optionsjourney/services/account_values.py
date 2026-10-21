"""Account values over time (docs/decisions/0023): recording each account's
end-of-day value, and reading them back for the Account values page.

- TastyTrade keeps a daily history, so it's backfilled a year (the sync
  window) on the first sync and continued from the last stored day after.
  Until Settings exists, the start can't be chosen (#7).
- Schwab keeps none, so its value is recorded whenever the app reads it: on
  each sync and each visit to the Accounts page. The last read of a day wins.
- Deposits and withdrawals come from the synced transactions."""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from optionsjourney.brokers import schwab, tastytrade
from optionsjourney.core.account_values import (
    MonthRow,
    Performance,
    ValuePoint,
    by_month,
    combine,
    performance,
)
from optionsjourney.core.accounts import AccountSummary, Balance, BrokerAccount, DailyValue
from optionsjourney.core.market import market_date
from optionsjourney.db.models import Account, AccountValue, BrokerTransaction
from optionsjourney.services.sync import problem_text

logger = logging.getLogger(__name__)

HISTORY, DAILY = "history", "daily"
FIRST_LOOKBACK = timedelta(days=365)  # the same reach as the transaction sync
OVERLAP = timedelta(days=3)  # re-read a few recent days in case they changed
_CASH_FLOWS = {"tastytrade": tastytrade.cash_flow, "schwab": schwab.cash_flow}
MONEY_MOVEMENT_TYPES = {"Money Movement"} | schwab.CASH_FLOW_TYPES


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Recording ----------------------------------------------------------------


def sync_values(
    clients: Iterable, session_factory: sessionmaker, now: Callable[[], datetime] = _utcnow
) -> list[str]:
    """Record every account's value. Returns the problems, one line each."""
    problems = []
    for client in clients:
        keeps_history = hasattr(client, "balance_history")
        if not keeps_history and getattr(client, "broker", None) not in _NO_HISTORY:
            continue  # nothing to record from it
        try:
            accounts = client.accounts()
        except Exception as e:
            problems.append(f"{client.name}: {problem_text(client.name, e)}")
            continue
        for account in accounts:
            try:
                if keeps_history:
                    _sync_history(client, account, session_factory, now())
                else:
                    record_balance(session_factory, account, client.balance(account.number), now())
            except Exception as e:
                problems.append(
                    f"{client.name} {account.masked_number}: {problem_text(client.name, e)}"
                )
    return problems


def record_balances(
    session_factory: sessionmaker, summaries: Iterable[AccountSummary], now: datetime | None = None
) -> None:
    """Record the balances the Accounts page just read, for brokers that keep
    no history of their own."""
    for summary in summaries:
        if summary.balance is not None and summary.account.broker in _NO_HISTORY:
            record_balance(session_factory, summary.account, summary.balance, now or _utcnow())


_NO_HISTORY = {"schwab"}


def record_balance(
    session_factory: sessionmaker, account: BrokerAccount, balance: Balance, now: datetime
) -> None:
    """Today's value from a balance read now; a later read today replaces it."""
    value = DailyValue(market_date(now), balance.net_liquidating_value, balance.cash)
    with session_factory() as session:
        _save(session, account, value, DAILY, now)
        session.commit()


def _sync_history(client, account: BrokerAccount, session_factory: sessionmaker, now: datetime):
    today = market_date(now)
    with session_factory() as session:
        last = session.scalar(
            select(func.max(AccountValue.day)).where(
                AccountValue.broker == account.broker,
                AccountValue.account_number == account.number,
                AccountValue.source == HISTORY,
            )
        )
    start = last - OVERLAP if last else today - FIRST_LOOKBACK
    values = client.balance_history(account.number, start, today)
    with session_factory() as session:
        for value in values:
            _save(session, account, value, HISTORY, now)
        session.commit()
    logger.info("account values %s %s: %d days", client.name, account.masked_number, len(values))


def _save(session: Session, account: BrokerAccount, value: DailyValue, source: str, now: datetime):
    row = session.get(AccountValue, (account.broker, account.number, value.day)) or AccountValue(
        broker=account.broker, account_number=account.number, day=value.day
    )
    row.net_liquidating_value = value.net_liquidating_value
    row.cash = value.cash
    row.source = source
    row.recorded_at = now
    session.add(row)


# --- Reading --------------------------------------------------------------------


@dataclass
class ValuesView:
    points: list[ValuePoint] = field(default_factory=list)
    performance: Performance | None = None
    months: list[MonthRow] = field(default_factory=list)  # newest first
    accounts: list[Account] = field(default_factory=list)
    schwab_since: date | None = None  # when the first Schwab value was recorded

    def chart_data(self) -> list[dict]:
        return [
            {"day": p.day.isoformat(), "value": float(p.value), "flow": float(p.flow)}
            for p in self.points
        ]


def load_values(
    session_factory: sessionmaker,
    account: str = "",
    days: int = 0,
    now: datetime | None = None,
) -> ValuesView:
    """All accounts added up (or one, "broker:number"), over the last `days`
    days (0 = everything recorded)."""
    since = market_date(now or _utcnow()) - timedelta(days=days) if days else None
    broker, _, number = account.partition(":")
    with session_factory() as session:
        query = select(AccountValue).order_by(AccountValue.day)
        if account:
            query = query.where(
                AccountValue.broker == broker, AccountValue.account_number == number
            )
        if since:
            query = query.where(AccountValue.day >= since)
        rows = list(session.scalars(query))
        accounts = list(session.scalars(select(Account).order_by(Account.broker, Account.number)))
        schwab_since = session.scalar(
            select(func.min(AccountValue.day)).where(AccountValue.broker == "schwab")
        )
        flows = _cash_flows(session, account, since)

    series: dict[tuple[str, str], list[DailyValue]] = {}
    for row in rows:
        series.setdefault((row.broker, row.account_number), []).append(
            DailyValue(row.day, row.net_liquidating_value, row.cash)
        )
    points = combine(series, flows)
    return ValuesView(
        points=points,
        performance=performance(points),
        months=list(reversed(by_month(points))),
        accounts=accounts,
        schwab_since=schwab_since,
    )


def _cash_flows(session: Session, account: str, since: date | None) -> dict[date, Decimal]:
    """Money moved in or out per New York day."""
    # Only the kinds of record that can move money, not every trade
    query = select(BrokerTransaction).where(
        BrokerTransaction.transaction_type.in_(MONEY_MOVEMENT_TYPES)
    )
    if account:
        broker, _, number = account.partition(":")
        query = query.where(
            BrokerTransaction.broker == broker, BrokerTransaction.account_number == number
        )
    flows: dict[date, Decimal] = {}
    for row in session.scalars(query):
        parse = _CASH_FLOWS.get(row.broker)
        amount = parse(row.raw) if parse else None
        if amount:
            day = market_date(row.executed_at)
            if since is None or day >= since:
                flows[day] = flows.get(day, Decimal("0")) + amount
    return flows
