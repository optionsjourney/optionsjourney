"""The pretend accounts' state, and what a fill does to it.

Orders are kept as raw TastyTrade order dicts (so ``trading.tastytrade.broker_order``
reads them like the real ones) and fills are written as raw TastyTrade transactions
(so ``brokers.tastytrade.parse_transaction`` and the sync read them like the real
ones). Made-up ids and account numbers only. No prices come from here: the pretend
broker decides when and at what an order fills.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from optionsjourney.core.accounts import DailyValue
from optionsjourney.core.market import market_date
from optionsjourney.core.symbols import parse_occ
from optionsjourney.core.transactions import RawTransaction
from optionsjourney.trading.orders import plain

TASTY_ACCOUNT = "5WX00001"
SCHWAB_ACCOUNT = "D0000001"
START_CASH = Decimal("100000")
MULTIPLIER = 100
CENT = Decimal("0.01")

COMMISSION = Decimal("1.00")  # per contract
CLEARING = Decimal("0.10")
REGULATORY = Decimal("0.02")
INDEX_OPTION_FEE = Decimal("0.60")  # proprietary-index-option-fees, SPX/SPXW only
_INDEX_ROOTS = {"SPX", "SPXW"}

LIVE = "Live"  # how TastyTrade calls a working order


@dataclass
class Book:
    """One account at the pretend broker: cash, what it holds, its orders, its history."""

    account: str
    cash: Decimal = START_CASH
    nickname: str | None = None
    account_type: str | None = "Individual"
    options: dict[str, int] = field(default_factory=dict)  # plain OCC -> signed contracts
    stocks: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)  # (shares, avg)
    orders: dict[str, dict] = field(default_factory=dict)  # id -> raw TastyTrade order
    transactions: list[RawTransaction] = field(default_factory=list)
    history: list[DailyValue] = field(default_factory=list)  # end-of-day values
    triggered: set[str] = field(default_factory=set)  # stop-limit orders now limits
    next_order_id: int = 700001
    next_transaction_id: int = 900000001

    def new_order_id(self) -> str:
        oid = self.next_order_id
        self.next_order_id += 1
        return str(oid)

    def new_transaction_id(self) -> int:
        tid = self.next_transaction_id
        self.next_transaction_id += 1
        return tid

    def held(self, symbol: str) -> int:
        return self.options.get(plain(symbol), 0)

    def fill(self, raw: dict, prices: list[Decimal], at: datetime) -> None:
        """Fill every leg of a working order at `prices` (one per leg, per contract):
        positions, cash, the order's own record and one transaction per leg."""
        stamp = at.isoformat()
        legs = raw["legs"]
        for leg, price in zip(legs, prices, strict=True):
            quantity = int(leg["quantity"])
            buy = leg["action"].startswith("Buy")
            key = plain(leg["symbol"])
            held = self.options.get(key, 0) + (quantity if buy else -quantity)
            if held:
                self.options[key] = held
            else:
                self.options.pop(key, None)
            value = price * quantity * MULTIPLIER
            fees = option_fees(leg["symbol"], quantity)
            net = (-value if buy else value) - sum(fees.values(), Decimal(0))
            self.cash += net
            self.transactions.append(_trade(self, raw, leg, price, value, fees, net, at, len(legs)))
            leg["remaining-quantity"] = 0
            leg["fills"] = [
                {
                    "fill-id": f"{raw['id']}-{len(self.transactions)}",
                    "quantity": str(quantity),
                    "fill-price": _text(price),
                    "filled-at": stamp,
                }
            ]
        raw["status"] = "Filled"
        raw["terminal-at"] = raw["updated-at"] = stamp
        raw["cancellable"] = raw["editable"] = False

    def settle(self, key: str, intrinsic: Decimal, at: datetime) -> None:
        """An option held into its expiry (`key`, plain OCC) leaves the account, settled
        in cash at `intrinsic` per contract, written as TastyTrade's Receive Deliver
        record: an Expiration when it's worth nothing, else a cash-settled assignment
        (short) or exercise (long)."""
        held = self.options.pop(key)
        quantity, short = abs(held), held < 0
        value = intrinsic * quantity * MULTIPLIER
        self.cash += -value if short else value
        symbol = f"{key[:-15]:<6}{key[-15:]}"  # padded, as the trades wrote it
        if not value:
            sub_type, effect = "Expiration", "None"
        elif short:
            sub_type, effect = "Cash Settled Assignment", "Debit"
        else:
            sub_type, effect = "Cash Settled Exercise", "Credit"
        tid = self.new_transaction_id()
        data = {
            "id": tid,
            "account-number": self.account,
            "symbol": symbol,
            "instrument-type": "Equity Option",
            "underlying-symbol": key[:-15],
            "transaction-type": "Receive Deliver",
            "transaction-sub-type": sub_type,
            "description": "Removal of option due to expiration"
            if not value
            else f"Cash settlement of {quantity} {symbol.strip()} @ {_text(intrinsic)}",
            "action": "Buy to Close" if short else "Sell to Close",
            "quantity": str(quantity),
            "price": _text(intrinsic) if value else None,
            "executed-at": at.isoformat(),
            "value": _text(value),
            "value-effect": effect,
            "net-value": _text(value),
            "net-value-effect": effect,
        }
        self.transactions.append(
            RawTransaction(
                transaction_id=str(tid), executed_at=at, transaction_type="Receive Deliver",
                data=data,
            )
        )  # fmt: skip

    def transactions_between(self, start: date, end: date) -> list[RawTransaction]:
        return [t for t in self.transactions if start <= market_date(t.executed_at) <= end]


def option_fees(symbol: str, contracts: int) -> dict[str, Decimal]:
    """The usual TastyTrade charges for `contracts` of one option, by field."""
    root = symbol[:6].strip().upper()
    fees = {
        "commission": COMMISSION * contracts,
        "clearing-fees": CLEARING * contracts,
        "regulatory-fees": REGULATORY * contracts,
    }
    if root in _INDEX_ROOTS:
        fees["proprietary-index-option-fees"] = INDEX_OPTION_FEE * contracts
    return fees


def leg_prices(legs: list[tuple[bool, int]], marks: list[float], cost: Decimal) -> list[Decimal]:
    """Per-contract prices for each leg ((buy, ratio)) that come to `cost` per unit
    (debit positive): each leg at its mark to the cent, and the difference taken up
    by the dearest leg that stays at zero or above."""
    prices = [Decimal(str(round(max(m, 0.0), 2))) for m in marks]
    if len(legs) == 1:
        return [abs(cost) / legs[0][1]]
    at_marks = sum(
        (p * ratio if buy else -p * ratio for (buy, ratio), p in zip(legs, prices, strict=True)),
        Decimal(0),
    )
    diff = cost - at_marks
    for i in sorted(range(len(legs)), key=lambda i: prices[i], reverse=True):
        buy, ratio = legs[i]
        moved = prices[i] + (diff / ratio if buy else -diff / ratio)
        if moved >= 0:
            prices[i] = moved.quantize(Decimal("0.0001")).normalize()
            break
    return prices


def max_loss(held: dict[str, int]) -> Decimal:
    """What these option contracts (plain OCC -> signed) can lose at expiry, in
    dollars, premium aside: per underlying, the lowest value they reach at a strike,
    at zero or 20% above the highest strike (the stand-in for an uncovered call)."""
    groups: dict[str, list[tuple[str, Decimal, int]]] = {}
    for symbol, quantity in held.items():
        parsed = parse_occ(symbol)
        if parsed is None or not quantity:
            continue
        underlying, d = parsed
        groups.setdefault(underlying, []).append((d.put_call, d.strike, quantity))
    total = Decimal(0)
    for legs in groups.values():
        strikes = {strike for _, strike, _ in legs}
        points = {Decimal(0), max(strikes) * Decimal("1.2"), *strikes}
        lowest = min(
            sum(
                (
                    q * (max(s - k, Decimal(0)) if pc == "CALL" else max(k - s, Decimal(0)))
                    for pc, k, q in legs
                ),
                Decimal(0),
            )
            for s in points
        )
        total += max(Decimal(0), -lowest) * MULTIPLIER
    return total


def _text(amount: Decimal) -> str:
    return format(amount.normalize(), "f") if amount else "0.0"


def _trade(book, raw, leg, price, value, fees, net, at, leg_count) -> RawTransaction:
    symbol, action, quantity = leg["symbol"], leg["action"], int(leg["quantity"])
    buy = action.startswith("Buy")
    root = symbol[:6].strip()
    parsed = parse_occ(symbol)
    described = symbol
    if parsed is not None:
        d = parsed[1]
        described = (
            f"{root} {d.expiration:%m/%d/%y} {d.put_call.title()} {d.strike:.2f} @ {price:.2f}"
        )
    tid = book.new_transaction_id()
    data = {
        "id": tid,
        "account-number": book.account,
        "symbol": symbol,
        "instrument-type": "Equity Option",
        "underlying-symbol": root,
        "transaction-type": "Trade",
        "transaction-sub-type": action,
        "description": f"{'Bought' if buy else 'Sold'} {quantity} {described}",
        "action": action,
        "quantity": str(quantity),
        "price": _text(price),
        "executed-at": at.isoformat(),
        "value": _text(value),
        "value-effect": "Debit" if buy else "Credit",
        "net-value": _text(abs(net)),
        "net-value-effect": "Debit" if net < 0 else "Credit",
        "order-id": int(raw["id"]),
        "leg-count": leg_count,
    }
    for name, amount in fees.items():
        data[name] = _text(amount)
        data[f"{name}-effect"] = "Debit"
    return RawTransaction(
        transaction_id=str(tid), executed_at=at, transaction_type="Trade", data=data
    )
