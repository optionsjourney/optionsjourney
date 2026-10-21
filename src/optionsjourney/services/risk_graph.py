"""Journal trades for the risk graph: legs (core.risk_graph), live marks from
the quote hub and the underlying's price. A broker or stream failure leaves a
mark or the spot empty; the graph still draws at entry prices."""

import logging

from optionsjourney.core.live import multipliers_from_fills
from optionsjourney.core.risk_graph import graph_legs, graph_symbol
from optionsjourney.services import journal

logger = logging.getLogger(__name__)


def _marks(hub, keys: set[str]) -> dict:
    marks = {}
    try:
        hub.subscribe(keys)
        for key in keys:
            quote = hub.latest(key)
            if quote is not None and quote.mark is not None:
                marks[key] = quote.mark
    except Exception as e:  # noqa: BLE001 -- no stream: graph at entry prices
        logger.warning("risk graph: no live marks: %s", e)
    return marks


def _spot(chain, clients, hub, symbol: str) -> float | None:
    """The underlying's price. A futures contract ("/ESH7") is priced itself:
    its root's front month can be a different contract."""
    try:
        if symbol.startswith("/"):
            return chain.futures_last(clients, hub, symbol)
        return chain.underlying_last(clients, hub, symbol)
    except Exception as e:  # noqa: BLE001 -- no spot: the graph centres on the strikes
        logger.warning("risk graph: no price for %s: %s", symbol, e)
        return None


def trade_graphs(view, keys: list[str], hub, chain, clients) -> list[dict]:
    """Each trade in keys' order, shaped as an item of Tracker2's
    _mountRiskGraphGroup, or {key, error} when it has nothing open to graph.
    Raises LookupError for a key that isn't in the journal."""
    entries = []
    for key in keys:
        entry = journal.find_entry(view, key)
        if entry is None:
            raise LookupError(key)
        entries.append(entry)
    open_keys = {
        leg.symbol
        for e in entries
        for leg in e.position.live_legs
        if leg.option is not None and leg.remaining > 0
    }
    marks = _marks(hub, open_keys) if open_keys else {}
    multipliers = multipliers_from_fills(
        fill
        for e in entries
        for event in e.position.events
        for order in event.orders
        for fill in order.fills
    )
    spots: dict[str, float | None] = {}
    items = []
    for entry in entries:
        legs = graph_legs(entry.position, multipliers, marks)
        if not legs or not journal.analyzable(entry):
            items.append(
                {"key": entry.position.key, "error": f"{entry.name} has no open options to graph."}
            )
            continue
        symbol = graph_symbol(entry.position)
        if symbol not in spots:
            spots[symbol] = _spot(chain, clients, hub, symbol)
        items.append(
            {
                "key": entry.position.key,
                "label": entry.name,
                "symbol": symbol,
                "legs": legs,
                "realized_pl": float(entry.position.realized_pl),
                "spot": spots[symbol],
            }
        )
    return items
