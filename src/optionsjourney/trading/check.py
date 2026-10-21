"""The one check every order goes through (spec: "The check"; decision 0028).

services/orders.py is the only code that sends, cancels or replaces an order,
and it asks check() first. Pure: the service gathers the facts (the switches,
ticks, the bot's limits, what's open, what working orders already cover). The
rules, in order:

1. Freeze all orders refuses everything, previews and cancels included.
2. A valid order: on tick, futures through TastyTrade only, overnight only for
   Cboe index options at TastyTrade, no earlier unclear order on its legs, and
   never a bot's multi-leg order at market.
3. Opening: not while No new trades is on; bots and you open (managed trades
   don't); a bot within its limits, you within Settings → Your orders; never a
   market order.
4. Closing: each leg closes something open on the opposite side, for no more
   than what's open minus what working closing orders already cover.
5. Preview or live: a bot's preview setting, a managed trade's own setting, or
   live for a button the user clicked."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from optionsjourney.core.sessions import group_of
from optionsjourney.trading.orders import Order, OrderType, Side, TimeInForce, plain
from optionsjourney.trading.ticks import on_tick

ORIGINS = ("bot", "managed", "you")
COULDNT_CHECK = "Couldn't check"  # the alerts look for refusals starting with this


@dataclass(frozen=True)
class Switches:
    no_new_trades: bool = False
    freeze: bool = False


@dataclass(frozen=True)
class BotFacts:
    name: str
    live: bool  # False: preview mode
    max_per_trade: int  # contracts in the largest leg of one order
    # Contracts in the largest leg across the bot's orders today. None: no daily cap,
    # only for an entry you start yourself while the bot's limit is blank (decision 0058).
    max_per_day: int | None
    used_today: int  # the same count, for what the bot sent today


@dataclass(frozen=True)
class Facts:
    order: Order
    broker: str  # "tastytrade" or "schwab"
    origin: str  # one of ORIGINS
    switches: Switches
    price_tick: Decimal | None = None
    trigger_tick: Decimal | None = None
    bot: BotFacts | None = None  # required when origin is "bot"
    managed_live: bool = False
    open_contracts: Mapping[str, int] | None = None  # plain symbol -> signed; None: unknown
    working_closing: Mapping[str, int] | None = None  # plain symbol -> covered; None: unknown
    unclear_symbols: frozenset[str] = frozenset()  # plain symbols
    max_your_contracts: int = 0  # Settings → Your orders; 0 = not set (refuses)


@dataclass(frozen=True)
class Decision:
    action: str  # "send", "preview" or "refuse"
    reason: str


def _refuse(reason: str) -> Decision:
    return Decision("refuse", reason)


def check_cancel(switches: Switches) -> Decision:
    if switches.freeze:
        return _refuse("Orders are frozen: the app sends nothing to your broker.")
    return Decision("send", "Passed the checks.")


def check(facts: Facts) -> Decision:
    if facts.origin not in ORIGINS:
        raise ValueError(f"Unknown origin {facts.origin!r}.")
    if facts.origin == "bot" and facts.bot is None:
        raise ValueError("A bot's order needs the bot's facts.")
    order = facts.order
    if facts.switches.freeze:
        return _refuse("Orders are frozen: the app sends nothing to your broker.")
    problem = _validity(facts) or (_opening(facts) if order.opening else _closing(facts))
    if problem:
        return problem
    live = {
        "bot": facts.bot.live if facts.bot else False,
        "managed": facts.managed_live,
        "you": True,
    }[facts.origin]
    if not live:
        name = facts.bot.name if facts.origin == "bot" else "This trade"
        return Decision("preview", f"{name} is in preview mode: checked, not sent.")
    return Decision("send", "Passed the checks.")


def _validity(facts: Facts) -> Decision | None:
    order = facts.order
    if order.legs[0].futures and facts.broker != "tastytrade":
        return _refuse(
            "Schwab doesn't accept futures orders from apps like this one, only from"
            " its own website and thinkorswim. Choose a TastyTrade account to send this order."
        )
    if order.time_in_force is TimeInForce.GTC_OVERNIGHT and (
        facts.broker != "tastytrade"
        or any(group_of(leg.symbol) != "cboe_index" for leg in order.legs)
    ):
        return _refuse("Overnight orders are for Cboe index options at TastyTrade only.")
    if facts.origin == "bot" and order.order_type is OrderType.MARKET and len(order.legs) > 1:
        # Tracker2's exit_chase rule 1: only a single leg may ever go at market.
        return _refuse("A bot never sends a multi-leg order at market.")
    if order.price is not None and (
        facts.price_tick is None or not on_tick(order.price, facts.price_tick)
    ):
        return _refuse(f"${abs(order.price)} isn't on the ${facts.price_tick} tick.")
    if order.trigger is not None and (
        facts.trigger_tick is None or not on_tick(order.trigger, facts.trigger_tick)
    ):
        return _refuse(f"The ${order.trigger} trigger isn't on the ${facts.trigger_tick} tick.")
    blocked = sorted(
        leg.symbol.strip() for leg in order.legs if plain(leg.symbol) in facts.unclear_symbols
    )
    if blocked:
        return _refuse(
            f"An earlier order on {', '.join(blocked)} has an unclear outcome. Check your broker, "
            "then clear it on the order log."
        )
    return None


def _opening(facts: Facts) -> Decision | None:
    order, bot = facts.order, facts.bot
    if facts.switches.no_new_trades:
        return _refuse("No new trades is on: nothing new is opened.")
    if facts.origin == "managed":
        return _refuse("Only bots and you open trades.")
    if order.order_type is OrderType.MARKET:
        return _refuse("Opening orders can't be market orders.")
    largest = order.largest_leg
    if facts.origin == "you":
        if largest > facts.max_your_contracts:
            return _refuse(
                f"{largest} contracts in one leg is over your limit of "
                f"{facts.max_your_contracts} (Settings → Your orders)."
            )
        return None
    if bot.max_per_trade < 1 or (bot.max_per_day is not None and bot.max_per_day < 1):
        return _refuse(f"{bot.name} has no size limits set.")
    if largest > bot.max_per_trade:
        if all(leg.ratio == 1 for leg in order.legs):
            return _refuse(
                f"{largest} is over {bot.name}'s limit of {bot.max_per_trade} per trade."
            )
        return _refuse(
            f"{largest} contracts in the largest leg is over {bot.name}'s limit of "
            f"{bot.max_per_trade} per trade."
        )
    if bot.max_per_day is not None and bot.used_today + largest > bot.max_per_day:
        return _refuse(
            f"{bot.name} has ordered {bot.used_today} today; {largest} more is over "
            f"its limit of {bot.max_per_day} a day."
        )
    return None


def _closing(facts: Facts) -> Decision | None:
    if facts.open_contracts is None or facts.working_closing is None:
        return _refuse(
            f"{COULDNT_CHECK} your open positions and working orders at the broker, so the "
            "closing order wasn't sent."
        )
    order = facts.order
    for leg in order.legs:
        key, name = plain(leg.symbol), leg.symbol.strip()
        held = facts.open_contracts.get(key, 0)
        if held == 0 or (held > 0) != (leg.side is Side.SELL):
            return _refuse(f"{name} isn't open on that side, so this would open a new position.")
        available = abs(held) - facts.working_closing.get(key, 0)
        wanted = leg.ratio * order.quantity
        if wanted > available:
            return _refuse(
                f"Closing {wanted} of {name} is more than the {max(available, 0)} left to close: "
                "working orders already cover the rest."
            )
    return None
