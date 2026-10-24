"""Buy/sell marks for the stock chart (#236).

Also the portfolio value over time, worked back from today's quantities through the
share moves in the fills, and the stock transaction list (#286).

Pure maths on fills."""

import logging
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from optionsjourney.core.market import market_date
from optionsjourney.core.transactions import Fill, InstrumentType, Kind, Side

logger = logging.getLogger(__name__)

# Shares put or called away by an option are real buys and sells at the strike.
_SHARE_KINDS = (Kind.TRADE, Kind.ASSIGNMENT, Kind.EXERCISE)

RANGES: dict[str, int | None] = {"1M": 30, "3M": 91, "1Y": 365, "All": None}

# How far back a ticker's latest close may stand in for a day with no close of its own.
LOOKBACK_DAYS = 10


@dataclass(frozen=True)
class Mark:
    day: date
    side: str  # "buy" | "sell"
    quantity: Decimal
    price: Decimal


def range_start(key: str, today: date) -> date | None:
    """First day of a chart range, or None for no limit (also for an unknown key)."""
    days = RANGES.get(key)
    return None if days is None else today - timedelta(days=days)


def trade_marks(fills: Iterable[Fill], ticker: str, accounts: Collection[str] = ()) -> list[Mark]:
    """Stock buys and sells of `ticker`, shares from assignment or exercise included,
    merged per day and side (quantity-weighted price)."""
    merged: dict[tuple[date, str], list[Decimal]] = {}
    for f in fills:
        if f.kind not in _SHARE_KINDS or f.instrument_type is not InstrumentType.STOCK:
            continue
        if ticker not in (f.underlying, f.symbol) or f.side is None or f.price is None:
            continue
        if accounts and f"{f.broker}:{f.account_number}" not in accounts:
            continue
        key = (market_date(f.executed_at), "buy" if f.side is Side.BUY else "sell")
        qty, notional = merged.setdefault(key, [Decimal(0), Decimal(0)])
        merged[key] = [qty + f.quantity, notional + f.quantity * f.price]
    return [Mark(d, side, q, n / q) for (d, side), (q, n) in sorted(merged.items()) if q]


@dataclass(frozen=True)
class ShareMove:
    day: date
    account: str  # "broker:number"
    ticker: str
    quantity: Decimal  # signed: + bought, - sold


@dataclass(frozen=True)
class ValuePoint:
    day: date
    value: Decimal
    priced: int  # tickers with a price that day
    total: int  # tickers held that day


@dataclass(frozen=True)
class StockTransaction:
    executed_at: datetime
    account: str  # "broker:number"
    ticker: str
    action: str  # "Buy" | "Sell" | "Assigned" | "Exercised"
    quantity: Decimal  # unsigned
    price: Decimal | None
    value: Decimal  # cash effect: + received, - paid
    fees: Decimal


def _share_fills(fills: Iterable[Fill], accounts: Collection[str]) -> Iterable[Fill]:
    """The fills that move shares, the same ones `trade_marks` keeps, in the given accounts."""
    for f in fills:
        if f.kind not in _SHARE_KINDS or f.instrument_type is not InstrumentType.STOCK:
            continue
        if f.side is None:
            continue
        if accounts and f"{f.broker}:{f.account_number}" not in accounts:
            continue
        yield f


def share_moves(fills: Iterable[Fill], accounts: Collection[str] = ()) -> list[ShareMove]:
    """Signed share changes from stock fills, assignment and exercise included."""
    return [
        ShareMove(
            market_date(f.executed_at),
            f"{f.broker}:{f.account_number}",
            f.underlying or f.symbol,
            f.quantity if f.side is Side.BUY else -f.quantity,
        )
        for f in _share_fills(fills, accounts)
    ]


def held_by_day(
    today: Mapping[tuple[str, str], Decimal], moves: Iterable[ShareMove], days: Sequence[date]
) -> dict[date, dict[str, Decimal]]:
    """Each ticker's quantity at each day's close, summed across accounts, worked back
    from `today` (keyed (account, ticker)). Zero quantities are left out.

    A long never shows below zero and a short never above it: fills that don't add up
    to today's quantity (a transfer, a split) would otherwise show shares that were
    never held."""
    by_key: dict[tuple[str, str], list[ShareMove]] = defaultdict(list)
    for m in moves:
        by_key[(m.account, m.ticker)].append(m)
    newest_first = sorted(days, reverse=True)
    held: dict[date, dict[str, Decimal]] = {d: {} for d in days}
    for key in set(today) | set(by_key):
        now = today.get(key, Decimal(0))
        pending = sorted(by_key[key], key=lambda m: m.day, reverse=True)
        after = Decimal(0)  # sum of this key's moves after the day being looked at
        i = 0
        clamped = False
        for d in newest_first:
            while i < len(pending) and pending[i].day > d:
                after += pending[i].quantity
                i += 1
            q = now - after
            fixed = max(q, Decimal(0)) if now >= 0 else min(q, Decimal(0))
            clamped = clamped or fixed != q
            if fixed:
                held[d][key[1]] = held[d].get(key[1], Decimal(0)) + fixed
        if clamped:
            logger.info(
                "Share history for %s in %s doesn't add up to today; clamped", key[1], key[0]
            )
    for d in days:
        held[d] = {t: q for t, q in held[d].items() if q}
    return held


def value_series(
    held: Mapping[date, Mapping[str, Decimal]],
    closes: Mapping[str, Mapping[date, Decimal]],
    days: Sequence[date],
) -> list[ValuePoint]:
    """Portfolio value per day: quantity x close over the tickers held. A ticker with no
    close that day uses its latest close in the previous LOOKBACK_DAYS days, else it is
    left out of the value (but still counted in `total`). Days before the first holding
    are dropped."""
    days_of = {t: sorted(c) for t, c in closes.items()}
    points: list[ValuePoint] = []
    for d in days:
        tickers = held.get(d, {})
        if not tickers and not points:
            continue
        value = Decimal(0)
        priced = 0
        for ticker, qty in tickers.items():
            series = closes.get(ticker, {})
            price = series.get(d)
            if price is None:
                known = days_of.get(ticker, [])
                at = bisect_left(known, d)  # closes before d end at known[at - 1]
                if at and known[at - 1] >= d - timedelta(days=LOOKBACK_DAYS):
                    price = series[known[at - 1]]
            if price is None:
                continue
            value += qty * price
            priced += 1
        points.append(ValuePoint(d, value, priced, len(tickers)))
    return points


_ACTIONS = {Kind.ASSIGNMENT: "Assigned", Kind.EXERCISE: "Exercised"}


def stock_transactions(
    fills: Iterable[Fill], accounts: Collection[str] = ()
) -> list[StockTransaction]:
    """The fills behind `share_moves` as a list, newest first."""
    rows = [
        StockTransaction(
            f.executed_at,
            f"{f.broker}:{f.account_number}",
            f.underlying or f.symbol,
            _ACTIONS.get(f.kind) or ("Buy" if f.side is Side.BUY else "Sell"),
            f.quantity,
            f.price,
            f.value,
            f.fees,
        )
        for f in _share_fills(fills, accounts)
    ]
    return sorted(rows, key=lambda r: r.executed_at, reverse=True)
