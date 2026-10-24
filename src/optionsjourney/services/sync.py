"""Copy broker transactions into the local database.

Each account syncs from where it left off, re-reading the last few days so
late changes (TastyTrade finalizes estimated fees the next day) are picked up.
History starts on the day of the app's first sync (decision 0064): that day is
stored as `history_starts`, and nothing dated before it is fetched or added.
Transactions already stored from before it (a kept trade's opening, after
trimming a development database to a later date, #148) still update. A sync that
brings in or changes trades then matches them to their bots (#276, bot_match.auto_match)."""

import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.market import market_date
from optionsjourney.core.transactions import RawTransaction
from optionsjourney.db.models import Account, BrokerTransaction, Setting, SyncState

logger = logging.getLogger(__name__)

OVERLAP = timedelta(days=7)
HISTORY_STARTS = "history_starts"  # settings row, YYYY-MM-DD New York date


class SyncClient(Protocol):
    name: str
    broker: str

    def accounts(self) -> list[BrokerAccount]: ...

    def transactions(self, account_number: str, start: date, end: date) -> list[RawTransaction]: ...


@dataclass
class SyncResult:
    broker: str
    accounts: int = 0
    new: int = 0
    updated: int = 0
    problems: list[str] | None = None

    @property
    def ok(self) -> bool:
        return not self.problems


UNREADABLE = "{broker} sent a reply the app couldn't read."


def problem_text(broker: str, error: Exception) -> str:
    """What to show for a failed read: a refusal's own words, or, for anything
    unexpected (a reply of the wrong shape), a plain line, with the cause logged."""
    if isinstance(error, (BrokerError, CredentialsUnavailable)):
        return str(error)
    logger.error("Unexpected error reading %s", broker, exc_info=error)
    return UNREADABLE.format(broker=broker)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Sync now and the background sync (auto_sync, #171) take turns: never two at once.
_ONE_AT_A_TIME = threading.Lock()


def sync_all(
    clients: Iterable[SyncClient],
    session_factory: sessionmaker,
    now: Callable[[], datetime] = _utcnow,
) -> list[SyncResult]:
    with _ONE_AT_A_TIME:
        results = []
        for client in clients:
            try:
                results.append(sync_broker(client, session_factory, now))
            except Exception as e:  # one broker's failure never stops the next
                name = getattr(client, "name", "?")
                results.append(SyncResult(broker=name, problems=[problem_text(name, e)]))
        if any(r.new or r.updated for r in results):
            _auto_match(session_factory)
        return results


def _auto_match(session_factory: sessionmaker) -> None:
    """#276: new trades go to their bot, as Match all bots would tick them. A problem
    here never fails the sync; Match all bots is still there."""
    from optionsjourney.services import bot_match  # bot_match reads the journal

    try:
        bot_match.auto_match(session_factory)
    except Exception:  # noqa: BLE001
        logger.exception("auto-match after sync failed")


def sync_broker(
    client: SyncClient,
    session_factory: sessionmaker,
    now: Callable[[], datetime] = _utcnow,
) -> SyncResult:
    result = SyncResult(broker=client.name, problems=[])
    try:
        accounts = client.accounts()
    except Exception as e:
        result.problems.append(problem_text(client.name, e))
        return result

    for account in accounts:
        result.accounts += 1
        with session_factory() as session:
            started = now()
            _save_account(session, account, started)
            state = session.get(SyncState, (account.broker, account.number)) or SyncState(
                broker=account.broker, account_number=account.number
            )
            state.last_attempt_at = started
            floor = history_starts(session, started)
            start, end = max(_window(state.synced_through, started), floor), market_date(started)
            try:
                raw = client.transactions(account.number, start, end)
                raw = [t for t in raw if market_date(t.executed_at) >= floor]
                new, updated = _store(session, account, raw, started)
            except Exception as e:  # this account's problem never stops the others
                text = problem_text(client.name, e)
                session.rollback()
                _save_account(session, account, started)
                history_starts(session, started)
                state = session.get(SyncState, (account.broker, account.number)) or SyncState(
                    broker=account.broker, account_number=account.number
                )
                state.last_attempt_at = started
                state.last_error = text
                session.add(state)
                session.commit()
                result.problems.append(f"{account.display_name} {account.masked_number}: {text}")
                continue
            state.synced_through = started
            state.last_error = None
            session.add(state)
            session.commit()
        result.new += new
        result.updated += updated
        logger.info(
            "synced %s %s: %d new, %d updated", client.name, account.masked_number, new, updated
        )
    return result


def _window(synced_through: datetime | None, now: datetime) -> date:
    """Where the fetch starts, before the history_starts floor."""
    if synced_through is None:
        return market_date(now)
    return market_date(synced_through - OVERLAP)


def history_starts(session: Session, now: datetime) -> date:
    """The first day of history, set to today the first time it's asked for."""
    row = session.get(Setting, HISTORY_STARTS)
    if row is None or not row.value:
        row = row or Setting(key=HISTORY_STARTS)
        row.value = market_date(now).isoformat()
        row.updated_at = now
        session.add(row)
    return date.fromisoformat(row.value)


def _save_account(session: Session, account: BrokerAccount, now: datetime) -> None:
    row = session.scalar(
        select(Account).where(Account.broker == account.broker, Account.number == account.number)
    ) or Account(broker=account.broker, number=account.number)
    row.nickname = account.nickname
    row.account_type = account.account_type
    row.updated_at = now
    session.add(row)


def _store(
    session: Session, account: BrokerAccount, raw: list[RawTransaction], now: datetime
) -> tuple[int, int]:
    """Insert new transactions; replace ones the broker has changed."""
    if not raw:
        return 0, 0
    existing = {
        row.transaction_id: row
        for row in session.scalars(
            select(BrokerTransaction).where(
                BrokerTransaction.broker == account.broker,
                BrokerTransaction.transaction_id.in_([t.transaction_id for t in raw]),
            )
        )
    }
    new = updated = 0
    for transaction in raw:
        row = existing.get(transaction.transaction_id)
        if row is None:
            row = BrokerTransaction(
                broker=account.broker,
                account_number=account.number,
                transaction_id=transaction.transaction_id,
                transaction_type=transaction.transaction_type,
                executed_at=transaction.executed_at,
                raw=transaction.data,
                first_seen_at=now,
                updated_at=now,
            )
            session.add(row)
            existing[transaction.transaction_id] = row  # a fetch can repeat a transaction
            new += 1
        elif row.raw != transaction.data:
            row.raw = transaction.data
            row.transaction_type = transaction.transaction_type
            row.executed_at = transaction.executed_at
            row.updated_at = now
            updated += 1
    return new, updated
