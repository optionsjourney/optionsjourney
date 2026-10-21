"""Trades entered by hand (docs/decisions/0023): manual accounts, storing
and deleting entries, and pre-filling a close. Entries are stored as broker
transactions under the "manual" broker (brokers/manual.py), so they flow
through the same pipeline as synced trades.

Only manual entries can be deleted: for synced accounts the broker is the
source of truth."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from optionsjourney.brokers import manual
from optionsjourney.brokers.manual import ManualEntryError
from optionsjourney.core.positions import Position
from optionsjourney.core.transactions import InstrumentType
from optionsjourney.db.models import Account, BrokerTransaction

ACCOUNT_TYPE = "Entered by hand"


def accounts(session_factory: sessionmaker) -> list[Account]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(Account).where(Account.broker == manual.BROKER).order_by(Account.number)
            )
        )


def create_account(session_factory: sessionmaker, name: str) -> str:
    """A new manual account; returns its number ("M001", "M002", ...)."""
    name = " ".join((name or "").split())
    if not name:
        raise ManualEntryError("Give the account a name.")
    if len(name) > 100:
        raise ManualEntryError("Keep the account name under 100 characters.")
    existing = accounts(session_factory)
    if any((a.nickname or "").lower() == name.lower() for a in existing):
        raise ManualEntryError(f"There's already an account called {name}.")
    number = f"M{len(existing) + 1:03d}"
    with session_factory() as session:
        session.add(
            Account(
                broker=manual.BROKER,
                number=number,
                nickname=name,
                account_type=ACCOUNT_TYPE,
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()
    return number


def add_entry(session_factory: sessionmaker, account_number: str, entry: dict) -> str:
    """Store an entry made by manual.build_entry. Returns the key the trade
    gets if the entry opens one."""
    with session_factory() as session:
        account = session.scalar(
            select(Account).where(Account.broker == manual.BROKER, Account.number == account_number)
        )
        if account is None:
            raise ManualEntryError("Choose one of your manual accounts.")
        now = datetime.now(UTC)
        session.add(
            BrokerTransaction(
                broker=manual.BROKER,
                account_number=account_number,
                transaction_id=entry["order_id"],
                transaction_type=manual.TRANSACTION_TYPE,
                executed_at=datetime.fromisoformat(entry["executed_at"]),
                raw=entry,
                first_seen_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return f"{manual.BROKER}:{account_number}:{entry['order_id']}"


def delete_entry(session_factory: sessionmaker, entry_id: str) -> None:
    with session_factory() as session:
        row = session.scalar(
            select(BrokerTransaction).where(BrokerTransaction.transaction_id == entry_id)
        )
        if row is None or row.broker != manual.BROKER:
            raise ManualEntryError("Only trades entered by hand can be deleted.")
        session.delete(row)
        session.commit()


def entry_ids(position: Position) -> list[str]:
    """The manual entries that make up a trade, oldest first."""
    if position.broker != manual.BROKER:
        return []
    ids = []
    for event in position.events:
        for order in event.orders:
            if order.order_id and order.order_id not in ids:
                ids.append(order.order_id)
    return ids


def closing_legs(position: Position) -> list[dict]:
    """Legs that would close what's left of a trade, for pre-filling the form."""
    legs = []
    for leg in position.live_legs:
        if leg.remaining <= 0:
            continue
        side = "buy" if leg.sign < 0 else "sell"
        if leg.instrument_type is InstrumentType.STOCK or leg.option is None:
            legs.append(
                {
                    "type": "shares",
                    "side": side,
                    "quantity": _plain(leg.remaining),
                    "underlying": leg.underlying,
                    "price": "",
                }
            )
        else:
            legs.append(
                {
                    "type": leg.option.put_call.lower(),
                    "side": side,
                    "quantity": _plain(leg.remaining),
                    "underlying": _root(leg.symbol, leg.underlying),
                    "strike": _plain(leg.option.strike),
                    "expiration": leg.option.expiration.isoformat(),
                    "price": "",
                }
            )
    return legs


def _plain(value) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _root(symbol: str, underlying: str) -> str:
    """The option root from the symbol ("SPXW  261016P..." -> SPXW), so the
    closing legs rebuild the same symbol."""
    root = symbol[:6].strip()
    return root or underlying
