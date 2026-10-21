"""How many units a bot sized by buying power buys (bots slice 19; decision 0050). Pure.

A bot sized by buying power divides its budget by the buying power ONE unit needs on its
own, as the bot's account's broker reports it for an order at the WORST price the entry
chase could reach (its last try), so no fill the chase can reach needs more. A credit or a
debit alike. The broker's figures are read here; asking for them is bot_entry's job:
- TastyTrade's dry run: the order's isolated margin requirement (not the change in buying
  power, which nets against what the account holds), less a credit received or plus a
  debit paid, plus its fees.
- Schwab's preview has no isolated figure: the drop in projectedAvailableFund from one
  unit to two is what one more unit needs."""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal

from optionsjourney.bots.definition import MAX_CONTRACTS, ChaseDef
from optionsjourney.trading.ticks import round_price

MULTIPLIER = 100  # sizing.CONTRACT_MULTIPLIER: a bot's own entries are equity options
CENT = Decimal("0.01")


def worst_price(
    first: Decimal,
    plan: ChaseDef,
    step_to: Callable[[Decimal, Decimal], Decimal | None],
    tick_of: Callable[[Decimal], Decimal],
) -> Decimal:
    """The price the entry chase sends on its last try (sizing.worst_entry_price): from
    `first` (the mids less the start offset), `plan.max_tries - 1` steps, each by the
    chase's step or the order's tick at that price if bigger (bot_chase.chase_entry, as
    execution.chase_entry's accumulator), stopping where `step_to` (strikes.next_price)
    stops. Rounded onto the tick toward a fill, as the order path rounds what it sends."""
    price = first
    for _ in range(plan.max_tries - 1):
        lower = step_to(price, max(plan.step, tick_of(price)))
        if lower is None:
            break
        price = lower
    return round_price(price, tick_of(price))


@dataclass(frozen=True)
class Sized:
    """`contracts` (units) to send; 0 with a `problem` when the entry mustn't go out.
    `too_small`: the dollars don't buy one unit (Tracker2's allocation_too_small)."""

    contracts: int
    asked: int = 0
    problem: str | None = None
    too_small: bool = False
    record: dict = field(default_factory=dict)


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENT):f}"


def _signed(fields: dict, key: str) -> Decimal | None:
    """TastyTrade's unsigned amount with its "-effect": a Debit uses buying power (+), a
    Credit frees it (-), None is nothing. None when the amount is missing."""
    amount = fields.get(key)
    if amount is None:
        return None
    value = Decimal(str(amount))
    return -value if fields.get(f"{key}-effect") == "Credit" else value


def tastytrade_unit_cost(reply: dict, price: Decimal, multiplier: int = MULTIPLIER):
    """The buying power one unit needs on its own, from a dry run of one unit at `price`
    (signed, credit positive); None when the reply doesn't say."""
    isolated = _signed(reply.get("buying-power-effect") or {}, "isolated-order-margin-requirement")
    if isolated is None:
        return None
    fees = _signed(reply.get("fee-calculation") or {}, "total-fees") or Decimal(0)
    return isolated - price * multiplier + fees


def schwab_unit_cost(one: dict, two: dict):
    """What one more unit needs: projectedAvailableFund for one unit less that for two
    (previews of the same order); None when either doesn't say."""

    def fund(reply):
        value = ((reply.get("orderStrategy") or {}).get("orderBalance") or {}).get(
            "projectedAvailableFund"
        )
        return None if value is None else Decimal(str(value))

    first, second = fund(one), fund(two)
    return None if first is None or second is None else first - second


def buying_power_size(
    budget: Decimal, unit_cost: Decimal | None, worst: Decimal, *, max_per_trade: int,
    ratio: int, broker: str,
) -> Sized:  # fmt: skip
    """⌊ budget ÷ the buying power one unit needs ⌋ units, never more than Max per trade
    counted in the largest leg (definition.contracts_for) or MAX_CONTRACTS. A cost the
    broker didn't give, or of $0 or less, refuses the entry; so does a budget that buys
    nothing (`too_small`, alerted like Tracker2's allocation_too_small)."""
    if unit_cost is None or unit_cost <= 0:
        return Sized(0, problem=f"{broker}'s preview didn't say what buying power one "
                     "contract needs.")  # fmt: skip
    asked = int((budget / unit_cost).to_integral_value(rounding=ROUND_FLOOR))
    contracts = min(asked, max_per_trade // max(ratio, 1), MAX_CONTRACTS)
    record = {
        "sizing": "buying_power", "broker": broker, "budget": _money(budget),
        "worst": _money(worst), "unit_cost": _money(unit_cost), "asked": asked,
        "contracts": max(contracts, 0),
    }  # fmt: skip
    if contracts < 1 and asked >= 1:  # validation keeps a unit within Max per trade
        return Sized(0, asked, "Max per trade is less than one unit of this bot.", False, record)
    if contracts < 1:
        problem = (
            f"One contract needs ${_money(unit_cost)} of buying power at the worst price "
            f"{_money(worst)}, more than the ${_money(budget)} budget."
        )
        return Sized(0, asked, problem, True, record)
    return Sized(contracts, asked, None, False, record)
