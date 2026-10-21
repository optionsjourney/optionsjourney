"""Tick sizes: rounding a price to what the broker accepts (spec: "Ticks").

From Tracker2's services/bots/pricing.py, which was checked against live chains
and real rejections. Most options trade in pennies; only the big cash indexes
use nickels. A too-fine default only over-precises, while a too-coarse one
prices orders off the book, so the default is a penny. Pure: the per-stock and
futures tables (from TastyTrade, kept in tick_sizes) are passed in."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from optionsjourney.trading.orders import Order

DEFAULT_TICK = Decimal("0.01")
NICKEL, DIME = Decimal("0.05"), Decimal("0.10")
TIERED_ROOTS = frozenset({"SPX", "SPXW", "RUT", "RUTW", "NDX", "NDXP"})
TIER_BREAK = Decimal("3")
PENNY_INDEX_ROOTS = frozenset({"XSP", "XSPW"})


@dataclass(frozen=True)
class TickRow:
    threshold: Decimal | None  # prices below it use this row; the last row has none
    value: Decimal


TickTable = tuple[TickRow, ...]


def _positive(value) -> Decimal | None:
    """A finite number above zero, or None."""
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number > 0 else None


def table_from_rows(rows: list[dict]) -> TickTable:
    """From TastyTrade's [{"threshold": "3.0", "value": "0.05"}, {"value": "0.10"}].
    A row without a finite, positive value (or threshold) is skipped; if none
    is left, the table is empty and the default tick applies."""
    table = []
    for row in rows:
        threshold, value = row.get("threshold"), _positive(row.get("value"))
        limit = None if threshold in (None, "") else _positive(threshold)
        if value is None or (limit is None and threshold not in (None, "")):
            continue
        table.append(TickRow(limit, value))
    return tuple(table)


def table_to_rows(table: TickTable) -> list[dict]:
    return [
        {"threshold": None if r.threshold is None else str(r.threshold), "value": str(r.value)}
        for r in table
    ]


# A stock's single-leg increments as last loaded (Tracker2's pricing._OPTION_TICKS, set by
# option_ticks.ensure and load_all): the bots' exit math rounds a stop or a one-leg close
# without the tables in hand (bots slice 16), so the order path's tick_tables and the
# app's start keep this up to date. The one piece of state here.
_LEARNED: dict[str, TickTable] = {}


def learn(key: str, table: TickTable) -> None:
    if table:
        _LEARNED[key.upper()] = table


def learned(key: str) -> TickTable | None:
    return _LEARNED.get(key.upper())


def forget_learned() -> None:
    """Tests."""
    _LEARNED.clear()


def equity_key(root: str) -> str:
    return root.upper()


def series_key(product: str, series: str) -> str:
    return f"FOP:{product.upper()}:{series.upper()}"


def contract_key(contract: str) -> str:
    return f"FUT:{contract.upper()}"


def product_of(contract: str) -> str:
    """The futures product of a contract: '/ESZ6' -> 'ES' (month and year are the last two)."""
    return contract.lstrip("/")[:-2].upper()


def _from_table(table: TickTable | None, price: Decimal) -> Decimal | None:
    if not table:
        return None
    for row in table:
        if row.threshold is None or price < row.threshold:
            return row.value
    return table[-1].value


def _futures_keys(order: Order) -> tuple[str | None, str]:
    leg = order.legs[0]
    parts = leg.symbol.split()
    series = series_key(product_of(leg.root), parts[1]) if len(parts) >= 3 else None
    return series, contract_key(leg.root)


def keys_for(order: Order) -> set[str]:
    """The tables this order's tick can depend on; the service loads these."""
    leg = order.legs[0]
    if leg.futures:
        series, contract = _futures_keys(order)
        keys = {series} if series else set()
        if len(order.legs) >= 2:
            keys.add(contract)
        return keys
    root = leg.root.upper()
    if len(order.legs) == 1 and root not in TIERED_ROOTS | PENNY_INDEX_ROOTS:
        return {equity_key(root)}
    return set()


def order_tick(order: Order, price: Decimal, tables: Mapping[str, TickTable]) -> Decimal:
    """The increment for this order at this per-unit price."""
    price = abs(Decimal(str(price)))
    leg = order.legs[0]
    if leg.futures:
        series, contract = _futures_keys(order)
        if len(order.legs) >= 2:
            spread = _from_table(tables.get(contract), price)
            if spread is not None:
                return spread
        learned = _from_table(tables.get(series), price)
        return DEFAULT_TICK if learned is None else learned
    root = leg.root.upper()
    if root in TIERED_ROOTS:
        if len(order.legs) >= 2:
            return NICKEL
        return DIME if price >= TIER_BREAK else NICKEL
    if len(order.legs) == 1 and root not in PENNY_INDEX_ROOTS:
        key = equity_key(root)
        found = _from_table(tables.get(key) or _LEARNED.get(key), price)
        if found is not None:
            return found
    return DEFAULT_TICK


def round_price(price: Decimal, tick: Decimal) -> Decimal:
    """Round a signed price toward a fill: a credit down (ask less), a debit
    to a larger amount (pay more). On the signed number both are a floor."""
    return (price / tick).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick


def round_trigger(trigger: Decimal, tick: Decimal) -> Decimal:
    return (trigger / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick


def on_tick(value: Decimal, tick: Decimal) -> bool:
    return value % tick == 0


def rounded(order: Order, tables: Mapping[str, TickTable]) -> Order:
    """The order with its price and trigger on tick. Raises OrderError if the
    price rounds to zero."""
    changes = {}
    if order.price is not None:
        changes["price"] = round_price(order.price, order_tick(order, order.price, tables))
    if order.trigger is not None:
        changes["trigger"] = round_trigger(order.trigger, order_tick(order, order.trigger, tables))
    return replace(order, **changes) if changes else order
