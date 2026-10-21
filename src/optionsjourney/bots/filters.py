"""Checks on a bot's entry before it is sent (Tracker2's guards.premium_bounds_ok).
Pure: the caller passes in the price."""

from decimal import Decimal

from optionsjourney.bots.definition import BotDef

CENT = Decimal("0.01")


def premium_problem(bot: BotDef, net: Decimal) -> str | None:
    """Why the entry's net premium (+ credit, - debit, before any chase offset) is outside
    the bot's bounds, or None if it is fine or bounds are off. Decision 0048: the bounds
    are on the size of the price, either side; the message names the side it priced on."""
    if not bot.use_premium_bounds:
        return None
    got = "credit" if net > 0 else "debit"
    size = abs(net).quantize(CENT)
    if bot.premium_min is not None and size < bot.premium_min:
        return f"net {got} {size} < min {bot.premium_min:.2f}"
    if bot.premium_max is not None and size > bot.premium_max:
        return f"net {got} {size} > max {bot.premium_max:.2f}"
    return None
