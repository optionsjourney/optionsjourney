"""The Stocks tab's holdings (#58): the shares held, one row per ticker across
accounts, with cost basis, the current price, P/L and today's P/L.

Built from the brokers' own positions, not the journal: the journal only knows
shares bought within the synced history, and misses older purchases and
transfers in.

- Cost basis is the broker's average price for the shares held. Short
  shares count as negative quantities.
- Today's P/L is measured from the previous close.
- The summary cards add up the priced holdings only; unpriced rows are left out."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True)
class StockPosition:
    """Shares in one account, as the broker reports them."""

    account: str  # "broker:number"
    ticker: str
    quantity: Decimal  # negative when short
    cost_basis: Decimal  # average price per share
    prev_close: Decimal | None = None  # the broker's own, when it gives one


@dataclass(frozen=True)
class Holding:
    ticker: str
    quantity: Decimal
    cost_basis: Decimal
    price: Decimal | None
    today_pl: Decimal | None

    @property
    def cost(self) -> Decimal:
        return abs(self.quantity) * self.cost_basis

    @property
    def market_value(self) -> Decimal | None:
        return None if self.price is None else self.quantity * self.price

    @property
    def pl(self) -> Decimal | None:
        return None if self.price is None else self.quantity * (self.price - self.cost_basis)

    @property
    def pl_percent(self) -> Decimal | None:
        return None if self.pl is None or not self.cost else self.pl / self.cost * 100


def holdings(
    positions: Iterable[StockPosition],
    prices: Mapping[str, Decimal],
    prev_closes: Mapping[str, Decimal],
) -> list[Holding]:
    """Combined by ticker, A to Z. `prev_closes` wins over a broker's own."""
    by_ticker: dict[str, list[StockPosition]] = {}
    for position in positions:
        if position.quantity:
            by_ticker.setdefault(position.ticker, []).append(position)
    rows = []
    for ticker, lots in sorted(by_ticker.items()):
        quantity = sum((p.quantity for p in lots), ZERO)
        if not quantity:
            continue
        held = sum((abs(p.quantity) for p in lots), ZERO)
        basis = sum((abs(p.quantity) * p.cost_basis for p in lots), ZERO) / held
        price = prices.get(ticker)
        prev = prev_closes.get(ticker) or next((p.prev_close for p in lots if p.prev_close), None)
        today_pl = None if price is None or prev is None else quantity * (price - prev)
        rows.append(Holding(ticker, quantity, basis, price, today_pl))
    return rows


@dataclass(frozen=True)
class StockSummary:
    total_pl: Decimal
    total_pl_percent: Decimal | None  # of the cost of the priced holdings
    market_value: Decimal
    day_pl: Decimal
    day_pl_percent: Decimal | None  # of yesterday's value (market value less today's P/L)


def stock_summary(rows: Iterable[Holding]) -> StockSummary:
    """Totals over the priced holdings; a row with no price is left out."""
    priced = [r for r in rows if r.price is not None]
    total_pl = sum((r.pl for r in priced), ZERO)
    cost = sum((r.cost for r in priced), ZERO)
    market_value = sum((r.market_value for r in priced), ZERO)
    day_pl = sum((r.today_pl for r in priced if r.today_pl is not None), ZERO)
    before = market_value - day_pl
    return StockSummary(
        total_pl,
        total_pl / cost * 100 if cost else None,
        market_value,
        day_pl,
        day_pl / before * 100 if before else None,
    )
