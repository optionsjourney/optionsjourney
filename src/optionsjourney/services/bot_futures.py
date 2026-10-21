"""A futures trade's series, read for a bot (spec: bots slice 17, section 2; Tracker2's
services/bots/futures_legs.py). The rules are bots/futures.py's; here the nested chain
comes through the chain's cache (the one the Trade page fills) and the
series' increments through the order path's tick tables (orders.tick_tables, which
fetches them from TastyTrade and keeps them in tick_sizes)."""

import logging
from collections.abc import Callable
from datetime import datetime

from optionsjourney.bots import futures
from optionsjourney.services import orders
from optionsjourney.trading import ticks
from optionsjourney.trading.orders import Leg, Order, Side

logger = logging.getLogger(__name__)
COULDNT_LOAD = "Couldn't load the {root} futures options from TastyTrade. Try again."


def _nested(chain, clients, root: str) -> dict:
    try:
        found = chain.futures_nested(clients, root)
    except Exception:  # noqa: BLE001 -- unknown: refused, never guessed
        logger.exception("couldn't read the %s futures chain", root)
        found = None
    if not found:
        raise futures.Unresolvable(COULDNT_LOAD.format(root=root))
    return found


def resolve_ticket(chain, clients, root: str, stream_symbols, now: datetime):
    """A Trade page ticket's legs by their DXLink names (futures_legs.resolve)."""
    by_stream, _ = futures.listed(_nested(chain, clients, root))
    return futures.resolve(root, stream_symbols, by_stream, now)


def resolve_held(chain, clients, root: str, symbols, now: datetime):
    """A journal trade's legs by their own TastyTrade symbols (futures_legs.resolve_held:
    across every listed series, not the page's one series a date)."""
    _, by_symbol = futures.listed(_nested(chain, clients, root))
    return futures.resolve(root, symbols, by_symbol, now)


def ticket_resolver(chain, clients, now: datetime) -> Callable:
    """resolve_ticket with the chain and the time in hand: the Trade page's review."""
    return lambda root, names: resolve_ticket(chain, clients, root, names, now)


def ensure_ticks(session_factory, clients, resolved: futures.Resolved, now: datetime) -> None:
    """Every series' single-leg increments loaded (futures_legs.ensure_ticks: "Couldn't
    load TastyTrade's option increments for EW3Z6 — try again"), all the same (legs in
    series with different steps are refused), and for a trade of
    two or more legs the contract's spread increments asked for too; without those the
    spreads use the series' own (_spread_ticks returns []). Raises Unresolvable."""
    found = {}
    for listed in {leg.series: leg for leg in resolved.legs}.values():
        probe = Order((Leg(listed.symbol, Side.SELL),), 1, True, price=1)
        try:
            tables = orders.tick_tables(session_factory, clients, probe, now)
        except Exception:  # noqa: BLE001 -- can't tell, so don't trade
            logger.exception("couldn't load the increments for %s", listed.series)
            tables = {}
        key = ticks.series_key(ticks.product_of(listed.contract), listed.series)
        if not tables.get(key):
            raise futures.Unresolvable(
                f"Couldn't load TastyTrade's option increments for {listed.series}. Try again."
            )
        found[listed.series] = tables[key]
    if len(set(found.values())) > 1:
        # One order is priced on its first leg's series (ticks.order_tick): legs in series
        # with other steps could rest off their tick. Refused (final review, slice 17).
        raise futures.Unresolvable(
            f"Its legs are in futures series with different price steps "
            f"({', '.join(sorted(found))}), so one order can't be priced for both. Trade "
            "them separately."
        )
    if len(resolved.legs) > 1:
        legs = tuple(Leg(leg.symbol, Side.SELL) for leg in resolved.legs)
        try:
            orders.tick_tables(session_factory, clients, Order(legs, 1, True, price=1), now)
        except Exception:  # noqa: BLE001 -- the series' own increments then price the spread
            logger.exception("couldn't load %s's spread increments", resolved.contract)
