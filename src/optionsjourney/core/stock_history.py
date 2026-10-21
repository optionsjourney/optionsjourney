"""P/L against cost over time for stocks, and buy/sell marks for a chart (#236).

Pure maths on saved daily rows and on fills."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from optionsjourney.core.market import market_date
from optionsjourney.core.transactions import Fill, InstrumentType, Kind, Side

# Shares put or called away by an option are real buys and sells at the strike.
_SHARE_KINDS = (Kind.TRADE, Kind.ASSIGNMENT, Kind.EXERCISE)

RANGES: dict[str, int | None] = {"1M": 30, "3M": 91, "1Y": 365, "All": None}


@dataclass(frozen=True)
class StockRow:
    """One stock holding as saved at one day's close. `close` is None when unpriced."""

    day: date
    account: str  # "broker:number"
    ticker: str
    quantity: Decimal  # negative for a short
    cost_basis: Decimal  # per share
    close: Decimal | None


@dataclass(frozen=True)
class DayPL:
    day: date
    pl: Decimal
    cost: Decimal
    pct: Decimal | None  # None when there is no cost
    priced: int
    total: int


@dataclass(frozen=True)
class Mark:
    day: date
    side: str  # "buy" | "sell"
    quantity: Decimal
    price: Decimal


def portfolio_series(rows: Iterable[StockRow], account: str = "", ticker: str = "") -> list[DayPL]:
    """Per-day P/L and cost over the matching rows, sorted by day. Rows without a
    close are left out of the sums but still counted in `total`."""
    pl: dict[date, Decimal] = defaultdict(Decimal)
    cost: dict[date, Decimal] = defaultdict(Decimal)
    priced: dict[date, int] = defaultdict(int)
    total: dict[date, int] = defaultdict(int)
    for r in rows:
        if (account and r.account != account) or (ticker and r.ticker != ticker):
            continue
        total[r.day] += 1
        if r.close is None:
            continue
        priced[r.day] += 1
        pl[r.day] += (r.close - r.cost_basis) * r.quantity
        cost[r.day] += abs(r.quantity) * r.cost_basis
    return [
        DayPL(d, pl[d], cost[d], pl[d] / cost[d] * 100 if cost[d] else None, priced[d], total[d])
        for d in sorted(total)
    ]


def range_start(key: str, today: date) -> date | None:
    """First day of a chart range, or None for no limit (also for an unknown key)."""
    days = RANGES.get(key)
    return None if days is None else today - timedelta(days=days)


def trade_marks(fills: Iterable[Fill], ticker: str, account: str = "") -> list[Mark]:
    """Stock buys and sells of `ticker`, shares from assignment or exercise included,
    merged per day and side (quantity-weighted price)."""
    merged: dict[tuple[date, str], list[Decimal]] = {}
    for f in fills:
        if f.kind not in _SHARE_KINDS or f.instrument_type is not InstrumentType.STOCK:
            continue
        if ticker not in (f.underlying, f.symbol) or f.side is None or f.price is None:
            continue
        if account and f"{f.broker}:{f.account_number}" != account:
            continue
        key = (market_date(f.executed_at), "buy" if f.side is Side.BUY else "sell")
        qty, notional = merged.setdefault(key, [Decimal(0), Decimal(0)])
        merged[key] = [qty + f.quantity, notional + f.quantity * f.price]
    return [Mark(d, side, q, n / q) for (d, side), (q, n) in sorted(merged.items()) if q]
