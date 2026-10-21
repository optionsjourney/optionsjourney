"""A futures option's series (spec: bots slice 17, section 2; Tracker2's
services/bots/futures_legs.py, spec 2026-10-03-analyzer-futures-bot-trade-design.md).
Pure: the service (services/bot_futures.py) reads TastyTrade's nested chain.

A futures option's order symbol ("./ESZ6 EW3Z6 261218P7000") can't be built from the
root: the contract month and the series root vary per expiration. So every leg is
looked up in the series TastyTrade lists ("/futures-option-chains/ES/nested"), by its
DXLink streamer symbol (a Trade page ticket, futures_legs.resolve) or by its own order
symbol (a journal trade, resolve_held), and the run keeps what was found: the symbol,
the series' expiry time, the contract it settles into and dollars a point."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from optionsjourney.core.chain import chain_root
from optionsjourney.core.transactions import OptionDetails

# "./ESZ6 EW3Z6 261218P7000": the contract, the series, then yymmdd, C or P and the strike
_SYMBOL = re.compile(r"^\./(\S+)\s+(\S+)\s+(\d{2})(\d{2})(\d{2})([CP])(\d+(?:\.\d+)?)$")


class Unresolvable(ValueError):
    """Why a futures trade can't be traded by a bot (futures_legs.Unresolvable)."""


@dataclass(frozen=True)
class Listed:
    """One listed futures option."""

    symbol: str  # TastyTrade's order symbol
    stream_symbol: str  # DXLink's
    right: str  # "call" or "put"
    strike: Decimal
    expiration: date
    expires_at: datetime | None  # the series' trading cutoff (aware UTC)
    contract: str  # the futures contract it settles into, "/ESZ6"
    series: str  # TastyTrade's option contract symbol, "EW3Z6"
    multiplier: Decimal  # dollars a point: notional value x 100 (/ES 50)


@dataclass(frozen=True)
class Resolved:
    legs: tuple[Listed, ...]  # in the order asked for
    contract: str
    multiplier: Decimal


def is_futures_symbol(symbol: str | None) -> bool:
    return (symbol or "").lstrip().startswith("./")


def _parts(symbol: str | None):
    return _SYMBOL.match((symbol or "").strip())


def option_of(symbol: str | None) -> OptionDetails | None:
    """The option a futures symbol names (broker._occ_parts: "./ESZ6 EW3Z6 261218P7000"
    is the 7000 put expiring 2026-12-18), or None."""
    found = _parts(symbol)
    if found is None:
        return None
    _, _, yy, mm, dd, right, strike = found.groups()
    try:
        expiration = date(2000 + int(yy), int(mm), int(dd))
    except ValueError:
        return None
    return OptionDetails("CALL" if right == "C" else "PUT", Decimal(strike), expiration)


def contract_of(symbol: str | None) -> str | None:
    found = _parts(symbol)
    return f"/{found.group(1)}" if found else None


def series_of(symbol: str | None) -> str | None:
    found = _parts(symbol)
    return found.group(2) if found else None


def multiplier_of(series: dict) -> Decimal:
    """Dollars a point: the series' notional value x 100 (analyzer.series_info)."""
    value = Decimal(str(series["notional-value"])) * 100
    whole = value.to_integral_value()
    return whole.quantize(Decimal(1)) if value == whole else value.normalize()


def _when(text) -> datetime | None:
    if not text:
        return None
    found = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return found if found.tzinfo else found.replace(tzinfo=UTC)


def listed(nested: dict | None) -> tuple[dict[str, Listed], dict[str, Listed]]:
    """Every option of every listed series in TastyTrade's nested chain, by streamer
    symbol and by order symbol (futures_legs.resolve_held: "across EVERY listed
    series"). A series that can't be read is left out."""
    by_stream: dict[str, Listed] = {}
    by_symbol: dict[str, Listed] = {}
    for chain in (nested or {}).get("option-chains", []) or []:
        for e in chain.get("expirations", []) or []:
            try:
                expiration = date.fromisoformat(e["expiration-date"])
                expires_at = _when(e.get("expires-at"))
                multiplier = multiplier_of(e)
                contract, code = e["underlying-symbol"], e["option-contract-symbol"]
            except KeyError, TypeError, ValueError, ArithmeticError:
                continue
            for k in e.get("strikes", []) or []:
                for right in ("call", "put"):
                    symbol, stream = k.get(right), k.get(f"{right}-streamer-symbol")
                    if not symbol or not stream:
                        continue
                    option = Listed(
                        symbol, stream, right, Decimal(str(k["strike-price"])).normalize(),
                        expiration, expires_at, contract, code, multiplier,
                    )  # fmt: skip
                    by_stream[stream] = option
                    by_symbol[_plain(symbol)] = option
    return by_stream, by_symbol


def _plain(symbol: str) -> str:
    return "".join(symbol.split())


def find(by: dict[str, Listed], name: str) -> Listed | None:
    """`name` in either index: a streamer symbol as is, an order symbol without spaces."""
    return by.get(name) or by.get(_plain(name))


def resolve(root: str, wanted, by: dict[str, Listed], now: datetime) -> Resolved:
    """The listed option for each name in `wanted` (futures_legs.resolve and
    resolve_held): every leg listed, none past its series' expiry, all on one contract.
    Raises Unresolvable."""
    names = list(wanted)
    if not names:
        raise Unresolvable("There are no legs to trade.")
    out = []
    for name in names:
        found = find(by, name)
        if found is None:
            raise Unresolvable(f"{name.strip()} isn't listed in TastyTrade's {root} options.")
        if found.expires_at is not None and found.expires_at <= now:  # futures_legs._expired
            raise Unresolvable(f"{root} {found.expiration.isoformat()} has already expired.")
        out.append(found)
    if len({leg.contract for leg in out}) != 1:  # _resolved
        raise Unresolvable("A bot's futures trade must be on one futures contract.")
    if len({leg.multiplier for leg in out}) != 1:
        raise Unresolvable("The legs have different contract sizes.")
    return Resolved(tuple(out), out[0].contract, out[0].multiplier)


def contract_problem(root: str, contract: str) -> str | None:
    """A contract must be a month of the bot's root (/ESZ6 for /ES, never /MESZ6)."""
    plain = contract.strip().lstrip(".")
    if chain_root(plain) != root:
        return f"{plain} isn't a {root} contract."
    return None


def dollars(points: Decimal | None, multiplier: Decimal | None) -> str:
    """A price in points as dollars a contract at the run's multiplier (Tracker2's
    run_multiplier: "${level * run_multiplier(run):+,.0f}/ct"): 6.25 at x50 is $312.50."""
    if points is None:
        return "—"
    return f"${abs(Decimal(points) * Decimal(multiplier or 100)):,.2f}"


def past_expiry(symbol: str, run_legs, now: datetime) -> bool:
    """A futures leg is over once its series' own expires_at has passed, as the run
    recorded it (futures_legs._expired: /GC at 13:30 New York); without one, the end of its
    expiration day's session (exit_math.after_expiry)."""
    from optionsjourney.bots.exit_math import after_expiry

    plain = _plain(symbol)
    for leg in run_legs or ():
        if _plain(leg.get("symbol", "")) == plain and leg.get("expires_at"):
            when = _when(leg["expires_at"])
            if when is not None:
                return when <= now
    return after_expiry(symbol, now)


def contract_stream(nested: dict | None, contract: str) -> str | None:
    """A futures contract's DXLink symbol in TastyTrade's nested chain ("/ESZ6" ->
    "/ESZ26:XCME"), or None when the chain doesn't list it."""
    return next(
        (f.get("streamer-symbol") for f in (nested or {}).get("futures", [])
         if f.get("symbol") == contract),
        None,
    )  # fmt: skip
