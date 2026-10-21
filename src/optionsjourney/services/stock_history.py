"""The daily save of stock holdings (#236): after each trading day's close
(plus 15 minutes) every account's shares and the day's closing prices are
stored, so the Stocks tab can chart P/L against cost over time.

A day is saved only when every connected broker answered; otherwise nothing is
written and the next check (every 10 minutes) tries again. Started from
__main__ and the menu bar app, like P/L Day's snapshots."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.core.market import MARKET_TZ, market_date
from optionsjourney.core.sessions import session_close
from optionsjourney.core.stock_history import StockRow
from optionsjourney.db.models import StockSnapshot
from optionsjourney.services.expiry_prices import prelim_prices
from optionsjourney.services.expiry_settle import GIVE_UP_AT
from optionsjourney.services.holdings import broker_positions
from optionsjourney.services.pl_day import SNAPSHOT_AFTER

logger = logging.getLogger(__name__)

CHECK_EVERY = 600  # seconds


def take_stock_snapshot(
    session_factory,
    clients: list,
    hub,
    now: datetime,
    *,
    positions: Callable = broker_positions,
    prices: Callable = prelim_prices,
) -> int | None:
    """Save today's holdings once the session has been closed SNAPSHOT_AFTER.
    None when it isn't time, today is already saved, or a broker didn't answer;
    otherwise the number of rows written (0 with no holdings)."""
    today = market_date(now)
    close = session_close(today)
    if close is None:
        return None
    if now < datetime.combine(today, close, MARKET_TZ) + SNAPSHOT_AFTER:
        return None
    with session_factory() as session:
        if session.scalar(select(StockSnapshot.day).where(StockSnapshot.day == today).limit(1)):
            return None
    held = positions(clients, strict=True)
    if held is None:
        return None
    held = _combined([p for p in held if p.quantity])
    if not held:
        return 0
    wanted = {(p.ticker, today) for p in held}
    reads = prices(clients, hub, wanted, now=now)
    closes = {}
    for ticker, _ in wanted:
        read = reads.get((ticker, today))
        if read is not None and read.price is not None and read.price > 0:
            closes[ticker] = Decimal(str(read.price))
    giving_up = now >= datetime.combine(today, GIVE_UP_AT, MARKET_TZ)
    if len(closes) < len({p.ticker for p in held}) and not giving_up:
        return None  # a price is missing: try again at the next check
    with session_factory() as session:
        for p in held:
            session.merge(
                StockSnapshot(
                    day=today,
                    account=p.account,
                    ticker=p.ticker,
                    quantity=p.quantity,
                    cost_basis=p.cost_basis,
                    close=closes.get(p.ticker),
                    taken_at=now,
                )
            )
        session.commit()
    logger.info("Stocks: saved %d holdings for %s", len(held), today)
    return len(held)


def _combined(positions: list) -> list:
    """One position per (account, ticker): quantities summed, cost averaged by quantity."""
    by_key: dict = {}
    for p in positions:
        by_key.setdefault((p.account, p.ticker), []).append(p)
    out = []
    for lots in by_key.values():
        if len(lots) == 1:
            out.append(lots[0])
            continue
        total = sum((x.quantity for x in lots), Decimal(0))
        cost = sum((x.quantity * x.cost_basis for x in lots), Decimal(0))
        out.append(
            replace(lots[0], quantity=total, cost_basis=cost / total if total else Decimal(0))
        )
    return [p for p in out if p.quantity]


def load_rows(session_factory, since: date | None = None) -> list[StockRow]:
    """Saved rows, oldest day first (from `since` on, when given)."""
    query = select(StockSnapshot).order_by(
        StockSnapshot.day, StockSnapshot.account, StockSnapshot.ticker
    )
    if since is not None:
        query = query.where(StockSnapshot.day >= since)
    with session_factory() as session:
        return [
            StockRow(r.day, r.account, r.ticker, r.quantity, r.cost_basis, r.close)
            for r in session.scalars(query)
        ]


def has_any(session_factory) -> bool:
    """Whether any day has been saved yet."""
    with session_factory() as session:
        return session.scalar(select(StockSnapshot.day).limit(1)) is not None


def start(services) -> threading.Thread:
    """Check every 10 minutes whether today's holdings are due to be saved."""

    def loop():
        while True:
            try:
                take_stock_snapshot(
                    services.session_factory,
                    services.broker_clients(),
                    services.quote_hub,
                    datetime.now(UTC),
                )
            except Exception:  # noqa: BLE001 -- keep checking
                logger.exception("Stock snapshot failed")
            time.sleep(CHECK_EVERY)

    thread = threading.Thread(target=loop, name="stock-snapshots", daemon=True)
    thread.start()
    return thread
