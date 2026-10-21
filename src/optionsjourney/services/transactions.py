"""Fills from the stored broker transactions, for the transactions page."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from optionsjourney.brokers import manual, schwab, tastytrade
from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.transactions import Fill
from optionsjourney.db.models import Account, BrokerTransaction, SyncState

logger = logging.getLogger(__name__)

PARSERS: dict[str, Callable[[dict, str], list[Fill]]] = {
    tastytrade.BROKER: tastytrade.parse_transaction,
    schwab.BROKER: schwab.parse_transaction,
    manual.BROKER: manual.parse_transaction,
}


@dataclass
class FillsView:
    fills: list[Fill] = field(default_factory=list)
    accounts: dict[tuple[str, str], BrokerAccount] = field(default_factory=dict)
    sync_states: list[SyncState] = field(default_factory=list)
    unreadable: int = 0  # stored transactions the parser couldn't read


def load_fills(
    session_factory: sessionmaker,
    *,
    since: datetime | None = None,
    account: tuple[str, str] | None = None,
) -> FillsView:
    """Fills executed since `since` (or all of them), newest first, optionally
    for one (broker, account number)."""
    view = FillsView()
    with session_factory() as session:
        view.accounts = {
            (a.broker, a.number): BrokerAccount(a.broker, a.number, a.nickname, a.account_type)
            for a in session.scalars(select(Account).order_by(Account.broker, Account.number))
        }
        view.sync_states = list(session.scalars(select(SyncState)))

        query = select(BrokerTransaction)
        if since is not None:
            query = query.where(BrokerTransaction.executed_at >= since)
        if account is not None:
            query = query.where(
                BrokerTransaction.broker == account[0],
                BrokerTransaction.account_number == account[1],
            )
        for row in session.scalars(query.order_by(BrokerTransaction.executed_at.desc())):
            parse = PARSERS.get(row.broker)
            try:
                view.fills.extend(parse(row.raw, row.account_number) if parse else [])
            except KeyError, ValueError, ArithmeticError:
                logger.exception("can't read %s transaction %s", row.broker, row.transaction_id)
                view.unreadable += 1
    return view


def order_fills(
    session_factory: sessionmaker,
    account: tuple[str, str],
    since: datetime,
    contracts: dict[str, int],
) -> dict[str, Decimal]:
    """Broker order id -> its net fill price a contract (+ credit, - debit), from the synced
    fills since `since`, for the orders in `contracts` (order id -> contracts filled). An
    order with no synced fill yet is left out."""
    view = load_fills(session_factory, since=since, account=account)
    cash: dict[str, Decimal] = {}
    for fill in view.fills:
        if fill.order_id in contracts:
            cash[fill.order_id] = cash.get(fill.order_id, Decimal(0)) + fill.value
    return {order: total / (100 * (contracts[order] or 1)) for order, total in cash.items()}
