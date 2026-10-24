"""Trade Ideas (#292): save a ticket, follow its P/L, freeze it at settlement.

Live legs are priced from the quote hub (streaming.marks). An expired leg is priced at
its underlying's official close (expiry_prices.official_closes), asked for at most every
300 s per idea. Once every leg is expired and has a final close, final_pnl and final_at
are written once and the idea is never priced again. A close that isn't final is shown
but never stored. Nothing here raises for a broker or hub failure: the card just has no
P/L."""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.core import ideas as rules
from optionsjourney.core.quotes import dxlink_symbol
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import Idea
from optionsjourney.services import expiry_prices, streaming

logger = logging.getLogger(__name__)

ASK_EVERY_S = 300
_asked: dict[int, float] = {}  # idea id -> when the brokers were last asked
_last_reads: dict[int, dict] = {}  # idea id -> the reads that ask returned, reused meanwhile


def _now(now):
    return now or datetime.now(UTC)


def create(sf, hub, ticker, label, note, cards, now=None) -> dict:
    ticker = (ticker or "").strip().upper()
    cleaned = rules.validate(ticker, label, cards)
    spot = None
    try:
        hub.subscribe([ticker])
        spot = streaming.marks(hub, [ticker], _now(now)).get(ticker)
    except Exception:  # noqa: BLE001 -- no spot is fine
        logger.info("No spot for %s when saving an idea", ticker)
    with sf() as session:
        idea = Idea(
            ticker=ticker,
            label=label.strip()[: rules.LABEL_MAX],
            note=(note or "").strip() or None,
            cards=cleaned,
            spot_at_entry=spot,
            created_at=_now(now),
        )
        session.add(idea)
        session.commit()
        return {"id": idea.id, "spot_at_entry": float(spot) if spot is not None else None}


def _live_marks(hub, symbols: list[str], now) -> dict[str, Decimal]:
    if not symbols:
        return {}
    try:
        hub.subscribe(symbols)
        return streaming.marks(hub, symbols, now)
    except Exception:  # noqa: BLE001 -- no marks: no P/L
        logger.info("Quote hub unavailable for ideas")
        return {}


def _closes(idea_id, clients, wanted, now, clock):
    """Reads for the expired (underlying, day) keys, unless asked within ASK_EVERY_S."""
    last = _asked.get(idea_id)
    if last is not None and clock() - last < ASK_EVERY_S:
        return _last_reads.get(idea_id, {})
    _asked[idea_id] = clock()
    try:
        _last_reads[idea_id] = expiry_prices.official_closes(clients, wanted, now=now)
    except Exception:  # noqa: BLE001 -- try again later
        logger.info("Closes for idea %s unavailable", idea_id)
        _last_reads.pop(idea_id, None)
    return _last_reads.get(idea_id, {})


def _evaluate(idea: Idea, hub, clients, now, clock, session) -> dict:
    cards = idea.cards
    state = rules.status(cards, now)
    if idea.final_pnl is not None:
        card = cards[0]
        unit = Decimal(str(card["entry_unit"])) - idea.final_pnl / (
            card["qty"] * card["multiplier"]
        )
        return _result(state, False, [(unit, idea.final_pnl)], cards)

    legs = [(leg, parse_occ(leg["symbol"])) for card in cards for leg in card["legs"]]
    gone = [(leg, p) for leg, p in legs if rules.has_expired(p[1].expiration, now)]
    live = [leg["symbol"] for leg, p in legs if not rules.has_expired(p[1].expiration, now)]
    prices = _live_marks(hub, live, now) if live else {}

    reads = {}
    if gone:
        wanted = {(u, opt.expiration) for _, (u, opt) in gone}
        reads = _closes(idea.id, clients, wanted, now, clock)
    closes = {key: read.price for key, read in reads.items()}
    expired_symbols = {leg["symbol"] for leg, _ in gone}
    na = any((u, opt.expiration) not in closes for _, (u, opt) in gone)

    rows = []
    for card in cards:
        parts = {
            True: {**card, "legs": [g for g in card["legs"] if g["symbol"] in expired_symbols]},
            False: {
                **card,
                "legs": [g for g in card["legs"] if g["symbol"] not in expired_symbols],
            },
        }
        settled = rules.settled_unit(parts[True], closes)
        current = rules.card_unit(parts[False], prices)
        unit = None if settled is None or current is None else settled + current
        rows.append((unit, None if unit is None else rules.card_pnl(card, unit)))

    if len(gone) == len(legs) and not na and all(r.final for r in reads.values()):
        total = sum(pnl for _, pnl in rows)
        idea.final_pnl = total
        idea.final_at = now
        session.commit()
        _last_reads.pop(idea.id, None)
        _asked.pop(idea.id, None)
    return _result(state, na, rows, cards)


def _result(state, na, rows, cards) -> dict:
    pnls = [pnl for _, pnl in rows]
    total = None if any(p is None for p in pnls) else sum(pnls, Decimal(0))
    pct = None if total is None else rules.pnl_percent(total, cards[0])
    return {
        "status": state,
        "settlement_na": na,
        "pnl": None if total is None else float(round(total, 2)),
        "pnl_pct": None if pct is None else round(pct, 1),
        "cards": [
            {
                "now": None if unit is None else float(unit),
                "pnl": None if pnl is None else float(round(pnl, 2)),
            }
            for unit, pnl in rows
        ],
    }


def listing(sf, hub, clients, now=None, clock: Callable[[], float] = time.monotonic) -> list[dict]:
    now = _now(now)
    out = []
    with sf() as session:
        for idea in list(session.query(Idea).order_by(Idea.created_at.desc(), Idea.id.desc())):
            result = _evaluate(idea, hub, clients, now, clock, session)
            out.append(
                {
                    "id": idea.id,
                    "ticker": idea.ticker,
                    "label": idea.label,
                    "note": idea.note,
                    "status": result["status"],
                    "settlement_na": result["settlement_na"],
                    "created_at": idea.created_at.isoformat(),
                    "spot_at_entry": None
                    if idea.spot_at_entry is None
                    else float(idea.spot_at_entry),
                    "pnl": result["pnl"],
                    "pnl_pct": result["pnl_pct"],
                    "cards": [
                        {
                            "label": card["label"],
                            "qty": card["qty"],
                            "entry": card["entry_unit"],
                            "locked": card["locked"],
                            **priced,
                        }
                        for card, priced in zip(idea.cards, result["cards"], strict=True)
                    ],
                }
            )
    return out


def markets(sf, hub, clients, now=None, clock: Callable[[], float] = time.monotonic) -> dict:
    return {
        row["id"]: {
            "pnl": row["pnl"],
            "pnl_pct": row["pnl_pct"],
            "cards": [{"now": c["now"], "pnl": c["pnl"]} for c in row["cards"]],
        }
        for row in listing(sf, hub, clients, now, clock)
    }


def edit(sf, idea_id, label=None, note=None) -> bool:
    with sf() as session:
        idea = session.get(Idea, idea_id)
        if idea is None:
            return False
        if label is not None:
            if not label.strip():
                raise rules.IdeaRefused("Give the idea a name.")
            idea.label = label.strip()[: rules.LABEL_MAX]
        if note is not None:
            idea.note = note.strip() or None
        session.commit()
        return True


def delete(sf, idea_id) -> bool:
    with sf() as session:
        idea = session.get(Idea, idea_id)
        if idea is None:
            return False
        session.delete(idea)
        session.commit()
    _asked.pop(idea_id, None)
    _last_reads.pop(idea_id, None)
    return True


def retire(sf, idea_id) -> bool:
    """Remove an idea that has become a real trade. Logs and never raises."""
    try:
        return delete(sf, idea_id)
    except Exception:  # noqa: BLE001 -- retiring is best effort
        logger.exception("Could not retire idea %s", idea_id)
        return False


def _cell(hub, symbol: str, side: str, qty: int) -> dict:
    """A leg as the Trade page's chain tray holds it (demo/tour_trade._cell's shape), with
    the hub's quote when there is one; the page's stream fills it in otherwise."""
    _, option = parse_occ(symbol)
    try:
        quote = hub.latest(symbol)
        found = hub.delta(symbol)
    except Exception:  # noqa: BLE001 -- no quote: the stream brings one
        quote, found = None, None
    whole = option.strike == option.strike.to_integral()
    return {
        "expiration": option.expiration.isoformat(),
        "strike": int(option.strike) if whole else float(option.strike),
        "type": option.put_call.lower(),
        "side": side,
        "qty": qty,
        "bid": None if quote is None or quote.bid is None else float(quote.bid),
        "ask": None if quote is None or quote.ask is None else float(quote.ask),
        "stream_symbol": dxlink_symbol(symbol),
        "delta": None if found is None else float(found[0]),
    }


def for_ticket(sf, hub, idea_id, now=None) -> dict | None:
    """The idea as the Trade page loads it (Trade it), or None when it is missing or a leg
    has expired: its legs as tray cells (card qty x ratio), and its entry as the limit."""
    with sf() as session:
        idea = session.get(Idea, idea_id)
        if idea is None or rules.status(idea.cards, _now(now)) != "live":
            return None
        card = idea.cards[0]
        return {
            "id": idea.id,
            "label": idea.label,
            "created_at": idea.created_at.isoformat(),
            "ticker": idea.ticker,
            "tray": [
                _cell(hub, leg["symbol"], leg["side"], card["qty"] * leg["ratio"])
                for leg in card["legs"]
            ],
            "price": abs(card["entry_unit"]),
            "effect": "debit" if card["entry_unit"] < 0 else "credit",
        }


def count(sf) -> int:
    with sf() as session:
        return session.query(Idea).count()
