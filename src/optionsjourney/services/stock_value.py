"""The Stocks tab's portfolio value over time (#286), worked back from today's
shares through the synced stock fills:

- **Quantities:** today's from the brokers' own positions; earlier days undo
  each buy and sell after them (core/stock_history.py). Positions are read
  strictly: one broker that doesn't answer and nothing is drawn, rather than a
  chart missing an account. A broker that was never connected is skipped, and
  so are any fills it left behind.
- **Prices:** each ticker's daily closes, kept an hour. A ticker whose prices
  can't be loaded (an error or no candles) is left out of the value but still
  counted.
- **Today:** once the market has opened, today's quantities are priced live
  from the quote hub (last, else mark, as the holdings table). A ticker with
  no live quote yet keeps its latest close, so today never drops to nothing;
  one with no closes stays out today too, so the line doesn't step on its last
  day.
- **Nothing to draw:** no shares held and no stock fills since the first day
  is an empty chart with no note (the page then hides the card).

The days run from the first day of history (set when the app first synced) to
today, trading days only."""

from collections import defaultdict
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from optionsjourney.core.market import MARKET_TZ, market_date
from optionsjourney.core.sessions import REGULAR_OPEN, session_close
from optionsjourney.core.stock_history import (
    LOOKBACK_DAYS,
    ValuePoint,
    held_by_day,
    share_moves,
    value_series,
)
from optionsjourney.services import chart_market, holdings, transactions
from optionsjourney.services.chain import Cache
from optionsjourney.services.sync import history_starts

COULD_NOT_LOAD = (
    "Your stock chart couldn't load because a broker didn't answer. "
    "It will try again when you reload."
)
NO_PRICES = "Prices aren't available right now."

KEEP_FOR = 3600  # seconds
_cache = Cache()


@dataclass
class ValueChart:
    points: list[ValuePoint]
    note: str = ""


def daily_closes(clients: list, ticker: str, start: date, now: datetime) -> dict[date, Decimal]:
    """`ticker`'s closes by session date since a little before `start` (so the
    first day can fall back on an earlier close). Raises chart_market.MarketError."""

    def fetch():
        period = (market_date(now) - start).days + LOOKBACK_DAYS
        found = chart_market.price_history(clients, ticker, 1440, period, now)
        return {
            datetime.fromtimestamp(c["time"], UTC).date(): Decimal(str(c["close"]))
            for c in found["candles"]
        }

    return _cache.get((ticker, start), KEEP_FOR, fetch)


def first_day(session_factory, now: datetime) -> date:
    """The first day of history (sync.history_starts), read without saving it."""
    with session_factory() as session:  # no commit: reading must not set the start
        return history_starts(session, now)


def fills_since(day: date) -> datetime:
    """Midnight New York at the start of `day`, in UTC: where its fills begin."""
    return datetime.combine(day, time(0), MARKET_TZ).astimezone(UTC)


def portfolio_value(
    session_factory,
    clients: list,
    hub,
    accounts: Collection[str],
    now: datetime,
    *,
    positions: Callable = holdings.kept_positions,
    closes: Callable = daily_closes,
) -> ValueChart:
    """The value of the shares held in `accounts` (none: every account) at each
    trading day's close, today's from live prices once the market has opened."""
    not_connected: set[str] = set()
    held = positions(clients, strict=True, skipped=not_connected)
    if held is None:
        return ValueChart([], COULD_NOT_LOAD)
    if accounts:
        held = [p for p in held if p.account in accounts]
    today: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for p in held:
        today[(p.account, p.ticker)] += p.quantity

    start = first_day(session_factory, now)
    end = market_date(now)
    days = [
        start + timedelta(days=i)
        for i in range((end - start).days + 1)
        if session_close(start + timedelta(days=i)) is not None
    ]

    since = fills_since(start)
    fills = transactions.load_fills(session_factory, since=since).fills
    # A broker that isn't connected has no holdings to work back from: its old
    # fills, if any, would only skew the line
    fills = [f for f in fills if f.broker not in not_connected]
    moves = share_moves(fills, accounts)

    tickers = sorted({t for _, t in today} | {m.ticker for m in moves})
    if not tickers:
        return ValueChart([])
    prices: dict[str, dict[date, Decimal]] = {}
    for ticker in tickers:
        try:
            found = closes(clients, ticker, start, now)
        except chart_market.MarketError:
            continue
        if found:
            prices[ticker] = dict(found)  # a copy: the cached one stays as fetched
    if tickers and not prices:
        return ValueChart([], NO_PRICES)

    # days[-1] == end means today is a session day, so the open check below is
    # about today's own session.
    # Only tickers held today with closes: one with no closes on any earlier day
    # would otherwise appear on today's point alone.
    if days and days[-1] == end and _opened(now):
        live = sorted({t for _, t in today} & prices.keys())
        for ticker, price in _live_prices(hub, live).items():
            prices[ticker][end] = price
    return ValueChart(value_series(held_by_day(today, moves, days), prices, days))


def _opened(now: datetime) -> bool:
    """Whether today's session has begun (it may also have closed)."""
    return now.astimezone(MARKET_TZ).time() >= REGULAR_OPEN


def _live_prices(hub, tickers: list[str]) -> dict[str, Decimal]:
    """Each ticker's live price (last, else mark); tickers without a quote are left out."""
    hub.subscribe(set(tickers))
    found = {}
    for ticker in tickers:
        quote = hub.latest(ticker)
        price = None if quote is None else (quote.last if quote.last is not None else quote.mark)
        if price is not None:
            found[ticker] = price
    return found
