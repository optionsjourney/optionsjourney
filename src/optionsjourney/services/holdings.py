"""The Stocks tab (#58): the shares held, from the brokers' own positions,
priced live from the quote hub. Previous closes come from the brokers'
quotes (Schwab's regular-session change first, as the watchlist). Positions
and closes are kept a minute; prices are live."""

import logging
from decimal import Decimal

from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.holdings import Holding, StockPosition, holdings
from optionsjourney.services.chain import Cache
from optionsjourney.services.watchlist_quotes import snapshot_quotes

logger = logging.getLogger(__name__)

KEEP_FOR = 60  # seconds
_cache = Cache()


def broker_positions(clients: list, *, strict: bool = False) -> list[StockPosition] | None:
    """Every account's shares. A broker that can't be reached is left out,
    or with `strict` makes the whole answer None (the daily save needs all)."""
    found: list[StockPosition] = []
    for client in clients:
        if not hasattr(client, "stock_positions"):
            continue
        try:
            if client.broker == "schwab":
                found += client.stock_positions()
            else:
                for account in client.accounts():
                    found += client.stock_positions(account.number)
        except (BrokerError, CredentialsUnavailable, OSError) as e:
            logger.info("%s positions unavailable: %s", client.name, e)
            if strict:
                return None
    return found


def stock_holdings(clients: list, hub, account: str = "") -> list[Holding]:
    positions = _cache.get(("positions",), KEEP_FOR, lambda: broker_positions(clients)) or []
    if account:
        positions = [p for p in positions if p.account == account]
    tickers = sorted({p.ticker for p in positions if p.quantity})
    hub.subscribe(set(tickers))
    prices = {}
    for ticker in tickers:
        quote = hub.latest(ticker)
        price = None if quote is None else (quote.last if quote.last is not None else quote.mark)
        if price is not None:
            prices[ticker] = price

    def closes():
        return {
            t: Decimal(str(q["prev_close"]))
            for t, q in snapshot_quotes(clients, tickers).items()
            if q.get("prev_close") is not None
        }

    prev = (_cache.get(("prev", tuple(tickers)), KEEP_FOR, closes) or {}) if tickers else {}
    return holdings(positions, prices, prev)
