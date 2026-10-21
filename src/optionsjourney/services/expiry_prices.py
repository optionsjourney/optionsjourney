"""Reading expiry-day prices from the brokers (decision 0021 style: either
broker may answer, one failing never stops the other). The picking rules live in
core/expiry_prices.py; this only asks the brokers and hands the answers over."""

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime

from optionsjourney.core.accounts import INDEXES, DailyClose
from optionsjourney.core.expiry_prices import PriceRead, last_price, official_close
from optionsjourney.core.market import market_date

logger = logging.getLogger(__name__)

Key = tuple[str, date]


def _schwab_symbol(symbol: str) -> str:
    return f"${symbol}" if symbol in INDEXES else symbol


def _tastytrade_items(clients: list, symbols: set[str]) -> dict[str, dict]:
    items: dict[str, dict] = {}
    by_type = {
        "index": sorted(s for s in symbols if s in INDEXES),
        "equity": sorted(s for s in symbols if s not in INDEXES),
    }
    for client in clients:
        if not hasattr(client, "market_data"):
            continue
        try:
            for item in client.market_data(by_type):
                if isinstance(item, dict) and item.get("symbol") in symbols:
                    items.setdefault(item["symbol"], item)
        except Exception as e:
            logger.info("tastytrade market data unavailable: %s", e)
    return items


def _schwab_closes(
    clients: list,
    wanted: set[Key],
    read_on: date,
    *,
    budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, list[DailyClose]]:
    """Schwab's history per symbol. With a budget, no new symbol request starts
    once that many seconds have passed; the rest are left for the next try."""
    closes: dict[str, list[DailyClose]] = {}
    start = clock()
    for client in clients:
        if not hasattr(client, "daily_closes"):
            continue
        for symbol in sorted({s for s, _ in wanted}):
            days = [d for s, d in wanted if s == symbol and d < read_on]
            if not days:
                continue
            if budget_s is not None and clock() - start > budget_s:
                logger.info("schwab history out of time; %s left for the next try", symbol)
                return closes
            try:
                closes.setdefault(symbol, []).extend(
                    client.daily_closes(symbol, min(days), max(days))
                )
            except Exception as e:
                logger.info("schwab history for %s unavailable: %s", symbol, e)
    return closes


def _schwab_quotes(clients: list, symbols: set[str]) -> dict[str, dict]:
    quotes: dict[str, dict] = {}
    for client in clients:
        if not hasattr(client, "quotes"):
            continue
        try:
            got = client.quotes([_schwab_symbol(s) for s in sorted(symbols)])
            for symbol in symbols:
                quote = got.get(_schwab_symbol(symbol))
                if isinstance(quote, dict):
                    quotes.setdefault(symbol, quote)
        except Exception as e:
            logger.info("schwab quotes unavailable: %s", e)
    return quotes


def _hub_quotes(hub, symbols: set[str]):
    if hub is None:
        return {}
    try:
        hub.subscribe(set(symbols))
        return {s: hub.latest(s) for s in symbols}
    except Exception as e:
        logger.info("quote hub unavailable: %s", e)
        return {}


def official_closes(
    clients: list,
    wanted: set[Key],
    *,
    now: datetime | None = None,
    tastytrade_first: bool = False,
    budget_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[Key, PriceRead]:
    """The official close for each (underlying, day) a broker already has.

    With `tastytrade_first`, Schwab's history is asked only for the keys
    TastyTrade's items didn't price. With `budget_s`, Schwab's history stops
    starting new requests once that many seconds have passed."""
    read_on = market_date(now or datetime.now(UTC))
    symbols = {s for s, _ in wanted}
    items = _tastytrade_items(clients, symbols)
    ask_schwab = wanted
    if tastytrade_first:
        ask_schwab = {
            (symbol, day)
            for symbol, day in wanted
            if official_close(day, read_on, items.get(symbol), []) is None
        }
    closes = _schwab_closes(clients, ask_schwab, read_on, budget_s=budget_s, clock=clock)
    found: dict[Key, PriceRead] = {}
    for symbol, day in wanted:
        read = official_close(day, read_on, items.get(symbol), closes.get(symbol, []))
        if read is not None:
            found[(symbol, day)] = read
    return found


def prelim_prices(
    clients: list, hub, wanted: set[Key], *, now: datetime | None = None
) -> dict[Key, PriceRead]:
    """The official close where there is one, else the day's last price."""
    read_on = market_date(now or datetime.now(UTC))
    symbols = {s for s, _ in wanted}
    items = _tastytrade_items(clients, symbols)
    closes = _schwab_closes(clients, wanted, read_on)
    quotes = _schwab_quotes(clients, symbols)
    hub_quotes = _hub_quotes(hub, symbols)
    found: dict[Key, PriceRead] = {}
    for symbol, day in wanted:
        read = official_close(day, read_on, items.get(symbol), closes.get(symbol, []))
        if read is None:
            read = last_price(
                symbol, day, items.get(symbol), quotes.get(symbol), hub_quotes.get(symbol)
            )
        if read is not None:
            found[(symbol, day)] = read
    return found
