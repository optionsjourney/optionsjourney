"""Which price an expiring option settles at (pure).

At 4:15 PM we settle from the day's last price, from whichever broker has it;
once the exchange publishes the official close we correct to that. Both pickers
return None rather than guess: a stale or missing price is never used."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from optionsjourney.core.accounts import INDEXES, DailyClose
from optionsjourney.core.market import market_date
from optionsjourney.core.quotes import Quote

PL_CHANGE_THRESHOLD = Decimal("1.00")


@dataclass(frozen=True)
class PriceRead:
    """A price, where it came from, and whether it is the official close."""

    price: Decimal
    source: str
    final: bool


def _decimal(value) -> Decimal | None:
    """A usable price: a finite number above zero (a glitchy 0 never settles a trade)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number > 0 else None


def _iso_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iso_on(value, day: date) -> bool:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return False
    if moment.tzinfo is None:
        return False
    return market_date(moment) == day


def _ms_on(value, day: date) -> bool:
    try:
        moment = datetime.fromtimestamp(float(value) / 1000, UTC)
    except TypeError, ValueError, OverflowError, OSError:
        return False
    return market_date(moment) == day


def _get(source: dict | None, *keys):
    for key in keys:
        if not isinstance(source, dict):
            return None
        source = source.get(key)
    return source


def official_close(
    day: date, read_on: date, tt_item: dict | None, schwab_closes: Iterable[DailyClose]
) -> PriceRead | None:
    """The official close for `day`, or None until a broker has it.

    Schwab's daily candle counts only once `read_on` is after `day`, since on
    the day itself it may still be the bar in progress. TastyTrade's final close
    and previous close are final whenever they are read."""
    if read_on > day:
        for close in schwab_closes:
            if close.day == day and _decimal(close.close) is not None:
                return PriceRead(close.close, "schwab daily close", True)
    item = tt_item if isinstance(tt_item, dict) else {}
    if item.get("close-price-type") == "Final" and _iso_day(item.get("summary-date")) == day:
        price = _decimal(item.get("close"))
        if price is not None:
            return PriceRead(price, "tastytrade close", True)
    if _iso_day(item.get("prev-close-date")) == day:
        price = _decimal(item.get("prev-close"))
        if price is not None:
            return PriceRead(price, "tastytrade prev-close", True)
    return None


def last_price(
    symbol: str,
    day: date,
    tt_item: dict | None,
    schwab_quote: dict | None,
    hub_quote: Quote | None,
) -> PriceRead | None:
    """The last price traded on `day` (not final), from either broker.

    Indexes trade only in the regular session, so either broker's last is fine
    if it is from `day`. For anything else the brokers' plain "last" can be an
    after-hours trade, so only the regular-session price counts."""
    if symbol in INDEXES:
        if isinstance(tt_item, dict) and _iso_on(tt_item.get("updated-at"), day):
            price = _decimal(tt_item.get("last"))
            if price is not None:
                return PriceRead(price, "tastytrade last", False)
        if _ms_on(_get(schwab_quote, "quote", "tradeTime"), day):
            price = _decimal(_get(schwab_quote, "quote", "lastPrice"))
            if price is not None:
                return PriceRead(price, "schwab last", False)
        return None
    if _ms_on(_get(schwab_quote, "regular", "regularMarketTradeTime"), day):
        price = _decimal(_get(schwab_quote, "regular", "regularMarketLastPrice"))
        if price is not None:
            return PriceRead(price, "schwab regular last", False)
    if (
        hub_quote is not None
        and hub_quote.source == "tastytrade"
        and _decimal(hub_quote.last) is not None
        and hub_quote.last_at is not None
        and hub_quote.last_at.tzinfo is not None
        and market_date(hub_quote.last_at) == day
    ):
        return PriceRead(hub_quote.last, "tastytrade stream last", False)
    return None


def pl_changed(old: Decimal, new: Decimal) -> bool:
    """Whether a corrected P/L moved by a dollar or more (worth an alert)."""
    return abs(new - old) >= PL_CHANGE_THRESHOLD
