"""In-app trade alerts (#178; Tracker2's transaction alerts): after a sync stores fills,
record each journal trade that opened, rolled, partly closed or closed, and hand back
the new ones for the pages' popups. Every trade the journal shows counts, a bot's or
your own. Built from the journal itself (core/trade_events), so it says what the
journal says. A problem here is logged and never fails the sync behind it."""

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from optionsjourney.core import trade_events
from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.db.models import Account, TradeEvent
from optionsjourney.services import journal, settings

logger = logging.getLogger(__name__)
EVENT = "trade_event"  # the Socket.IO event each new one is sent as
LOOKBACK = timedelta(days=1)  # older fills are history, not news: never recorded
POPUP_WINDOW = timedelta(minutes=10)  # a catch-up sync fills the list without a popup flood
_ONE_AT_A_TIME = threading.Lock()


@dataclass(frozen=True)
class Shown:
    """One event as the pages show it (the popup and the Alerts list)."""

    id: int
    kind: str
    label: str
    name: str
    bot_name: str | None
    account: str
    amount: str  # "$25.00 received", "$34.00 paid"
    occurred_at: str  # ISO, UTC
    when: str  # "10/19 14:30 ET"
    link: str
    popup: bool
    detail: str = ""  # a close's "Strikes: …" and "Rolled: …" lines (#212)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def popups_on(session_factory: sessionmaker) -> bool:
    return settings.get(session_factory, "trade_popups") != "off"


def _amount(amount: Decimal) -> str:
    words = "received" if amount >= 0 else "paid"
    return f"${abs(amount):,.2f} {words}"


def shown(row: TradeEvent, accounts: dict, now: datetime, popups: bool = True) -> Shown:
    account = accounts.get((row.broker, row.account_number))
    label = f"{account.display_name} {account.masked_number}" if account else "••" + row.account_number[-4:]
    return Shown(
        row.id, row.kind, trade_events.LABELS[row.kind], row.name, row.bot_name, label,
        _amount(row.amount), row.occurred_at.isoformat(),
        f"{row.occurred_at.astimezone(MARKET_TZ):%m/%d %H:%M} ET",
        f"/journal/trades/{quote(row.position_key, safe=':')}",
        popups and now - row.occurred_at <= POPUP_WINDOW, row.detail or "",
    )  # fmt: skip


def record(session_factory: sessionmaker, now: datetime, cache=None) -> list[Shown]:
    """Store the journal's trade events of the last day not stored yet; the new ones."""
    with _ONE_AT_A_TIME:
        view = journal.load_journal(session_factory, now=now, cache=cache)
        since = now - LOOKBACK
        with session_factory() as session:
            known = set(session.scalars(
                select(TradeEvent.dedupe_key).where(TradeEvent.occurred_at >= since - LOOKBACK)
            ))  # fmt: skip
            new = []
            for entry in view.entries:
                for fact in trade_events.events_of(entry.position):
                    if fact.at < since or fact.dedupe_key in known:
                        continue
                    known.add(fact.dedupe_key)
                    row = TradeEvent(
                        dedupe_key=fact.dedupe_key, kind=fact.kind,
                        position_key=fact.position_key, name=entry.name,
                        bot_name=entry.bot.name if entry.bot else None,
                        broker=entry.position.broker,
                        account_number=entry.position.account_number,
                        amount=fact.amount, occurred_at=fact.at, created_at=now,
                        detail=fact.detail,
                    )  # fmt: skip
                    session.add(row)
                    new.append(row)
            try:
                session.commit()
            except IntegrityError:  # another process stored them first
                session.rollback()
                return []
            new.sort(key=lambda r: r.occurred_at)
            popups = popups_on(session_factory)
            return [shown(r, view.accounts, now, popups) for r in new]


def accounts(session_factory: sessionmaker) -> dict:
    """(broker, number) -> BrokerAccount, for the account names events show."""
    with session_factory() as session:
        return {
            (a.broker, a.number): BrokerAccount(a.broker, a.number, a.nickname, a.account_type)
            for a in session.scalars(select(Account))
        }


def recent(session_factory: sessionmaker, kind: str = "", before_id: int | None = None,
           limit: int = 200) -> list[Shown]:  # fmt: skip
    """The Alerts page's list, newest first."""
    query = select(TradeEvent).order_by(TradeEvent.occurred_at.desc(), TradeEvent.id.desc())
    if kind in trade_events.KINDS:
        query = query.where(TradeEvent.kind == kind)
    if before_id:
        query = query.where(TradeEvent.id < before_id)
    now = datetime.now(UTC)
    with session_factory() as session:
        rows = session.scalars(query.limit(limit)).all()
    names = accounts(session_factory)
    return [shown(r, names, now, popups=False) for r in rows]
