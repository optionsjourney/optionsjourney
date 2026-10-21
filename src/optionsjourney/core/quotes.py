"""Live quotes (#22): one symbol key across the app, and each broker's stream
read into the same Quote.

A symbol key is what the app already uses: the OCC symbol for an option
("SPXW  261016P05800000", as fills carry it) or the ticker for a stock or
index ("SPY", "SPX"). Each stream spells them its own way:
- TastyTrade's DXLink: ".SPXW261016P5800" for options, "SPX" for indexes.
- Schwab: the OCC symbol for options, "$SPX" for indexes.

Decisions 0021 and 0080: with both brokers, TastyTrade's stream comes first and
Schwab's is the backup, and it takes over when TastyTrade's stream is down or
its quote is more than STALE_GAP older than Schwab's."""

import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from decimal import Decimal

from optionsjourney.core.accounts import INDEXES
from optionsjourney.core.sessions import naive_et, session_close
from optionsjourney.core.symbols import OCC, parse_occ


@dataclass(frozen=True)
class Quote:
    key: str  # the app's symbol key
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    source: str  # "tastytrade" or "schwab"
    at: datetime  # when it arrived
    last_at: datetime | None = None  # when its last trade price arrived (bots slice 7)

    @property
    def mark(self) -> Decimal | None:
        """The mid of bid and ask, or the last trade if there's no market."""
        if self.bid is not None and self.ask is not None and self.ask >= self.bid > 0:
            return (self.bid + self.ask) / 2
        return self.last


def _is_option(key: str) -> bool:
    return parse_occ(key) is not None


# --- TastyTrade (DXLink) ---------------------------------------------------------


# A key that is already a DXLink symbol ("dx:/ESZ26:XCME"): Analyze's pages
# name futures and their options that way (#26). Only TastyTrade streams them.
DXLINK_KEY = "dx:"


def dxlink_symbol(key: str) -> str:
    if key.startswith(DXLINK_KEY):
        return key[len(DXLINK_KEY) :]
    match = OCC.match(key.strip())
    if not match:
        return key
    root, yymmdd, put_call, strike = match.groups()
    return f".{root}{yymmdd}{put_call}{format((Decimal(strike) / 1000).normalize(), 'f')}"


def parse_dxlink_feed(data: list, fields: dict[str, list[str]]) -> list[dict]:
    """FEED_DATA in COMPACT format: [type, [values of event 1..., event 2...]],
    each event's values in the order of the fields asked for in FEED_SETUP.
    "NaN" (no price) becomes None."""
    events = []
    for index in range(0, len(data) - 1, 2):
        event_type, values = data[index], data[index + 1]
        names = fields.get(event_type)
        if not names:
            continue
        for start in range(0, len(values) - len(names) + 1, len(names)):
            event = dict(zip(names, values[start : start + len(names)], strict=True))
            events.append(
                {
                    k: _number(v) if k not in ("eventType", "eventSymbol") else v
                    for k, v in event.items()
                }
            )
    return events


def _number(value):
    if value is None or value == "NaN" or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def quote_from_dxlink(key: str, event: dict, at: datetime) -> Quote:
    return Quote(
        key,
        _decimal(event.get("bidPrice")),
        _decimal(event.get("askPrice")),
        _decimal(event.get("price")),
        "tastytrade",
        at,
    )


# --- Schwab ---------------------------------------------------------------------------

# Field numbers in Schwab's LEVELONE services: (bid, ask, last)
_SCHWAB_FIELDS = {"LEVELONE_EQUITIES": ("1", "2", "3"), "LEVELONE_OPTIONS": ("2", "3", "4")}


def is_futures_option(key: str) -> bool:
    return key.startswith("./")


FUTURES_ROOT = re.compile(r"^\./([A-Z0-9]+?)[FGHJKMNQUVXZ]\d{1,2}\b")


def futures_root(key: str) -> str | None:
    """The product of a futures option key: "./ESZ6 EWX6  261130P7000" -> "ES"."""
    match = FUTURES_ROOT.match(key)
    return match.group(1) if match else None


def schwab_service_and_symbol(key: str) -> tuple[str, str] | None:
    """None for what Schwab's stream isn't asked for (futures options)."""
    if is_futures_option(key) or key.startswith(DXLINK_KEY):
        return None
    if _is_option(key):
        return "LEVELONE_OPTIONS", key
    return "LEVELONE_EQUITIES", f"${key}" if key in INDEXES else key


def update_from_schwab(
    previous: Quote | None, key: str, service: str, content: dict, at: datetime
) -> Quote:
    """Schwab sends a full quote first, then only the fields that changed."""
    bid_field, ask_field, last_field = _SCHWAB_FIELDS[service]
    quote = previous or Quote(key, None, None, None, "schwab", at)
    changes = {}
    for name, field in (("bid", bid_field), ("ask", ask_field), ("last", last_field)):
        if field in content:
            changes[name] = _decimal(content[field])
    if "last" in changes:
        changes["last_at"] = at
    return replace(quote, at=at, **changes)


# --- Choosing -----------------------------------------------------------------------------


# A TastyTrade quote this much older than Schwab's is stale: a stream can stay
# connected and stop sending (decision 0080). A quiet symbol is only a few
# seconds behind, so it stays on TastyTrade.
STALE_GAP = timedelta(seconds=10)


def pick_quote(tasty: Quote | None, schwab: Quote | None, *, tasty_up: bool) -> Quote | None:
    """TastyTrade's quote while its stream is up and it isn't stale next to
    Schwab's; otherwise Schwab's. A last-known quote beats none."""
    if tasty is not None and (schwab is None or (tasty_up and tasty.at >= schwab.at - STALE_GAP)):
        return tasty
    return schwab


# --- After hours (#241) -------------------------------------------------------------------

# After the bell, index weeklies lose their market: makers pull or widen their
# bids (0.00 x 20.40, 4.60 x 10.00), and the mid marks a leg far from its value.
# Outside the regular session such a quote is marked at the option's last real
# two-sided mark instead (Tracker2's leg_mark_snapshot.apply_closing_marks).
NO_MARKET_MIN_ASK = Decimal("0.50")  # under it, a bid-less quote is a worthless wing
SESSION_OPEN = time(9, 30)
OPTIONS_TRADE_AFTER_CLOSE = timedelta(minutes=15)  # SPX options trade to 16:15


def no_market(quote: Quote | None) -> bool:
    """An option's bid missing or under half a real ask: its market is closed or pulled."""
    if quote is None or not _is_option(quote.key) or quote.ask is None:
        return False
    return quote.ask >= NO_MARKET_MIN_ASK and (quote.bid is None or quote.bid < quote.ask / 2)


def quotes_are_the_market(now: datetime) -> bool:
    """09:30 until options stop trading after the close, on a trading day."""
    moment = naive_et(now)
    close = session_close(moment.date())
    if close is None:
        return False
    stop = datetime.combine(moment.date(), close) + OPTIONS_TRADE_AFTER_CLOSE
    return datetime.combine(moment.date(), SESSION_OPEN) <= moment < stop


def closing_mark(
    quote: Quote | None, saved: Decimal | None, now: datetime, *, strict: bool = False
) -> Decimal | None:
    """The price to mark a leg at: its quote's mark, except a no-market quote
    outside the regular session, which is marked at `saved` (its last real
    two-sided mark). With nothing saved it keeps its mark, or with `strict`
    (for the close-time snapshot, never stored from such a quote) has none."""
    if quote is None:
        return None
    if not no_market(quote) or quotes_are_the_market(now):
        return quote.mark
    if saved is not None:
        return saved
    return None if strict else quote.mark


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return None
    return number if number.is_finite() else None
