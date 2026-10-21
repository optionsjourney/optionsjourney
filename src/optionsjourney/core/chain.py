"""The option chain's pure helpers, for the Trade page and the bots: which
tickers are valid, which expirations to list, futures series and symbols, and
what each account holds. Ported from Tracker2's services/analyzer_data.py."""

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal

from optionsjourney.core.symbols import normalize_underlying

WEEKLY_ROOTS = {"SPX": "SPXW", "RUT": "RUTW"}  # the root the chain prices, as Tracker2
TICKER = re.compile(r"^([A-Z][A-Z.]{0,9}|/(?![A-Z]{1,3}[FGHJKMNQUVXZ]\d{1,2}$)[A-Z][A-Z0-9]{0,3})$")
_DXLINK_OPTION = re.compile(r"^\.([A-Z]{1,6})(\d{6})([CP])(\d+(?:\.\d+)?)$")


def clean_ticker(raw) -> str | None:
    ticker = (raw or "").strip().upper() if isinstance(raw, str) else ""
    return ticker if TICKER.match(ticker) else None


def filter_expirations(days: Iterable[date], today: date) -> list[str]:
    """Today through December 31 five years out (drops bogus far listings)."""
    horizon = date(today.year + 5, 12, 31)
    return sorted({d.isoformat() for d in days if today <= d <= horizon})


# --- futures ---

_FUTURES_MONTH = re.compile(r"^/([A-Z][A-Z0-9]{0,2})[FGHJKMNQUVXZ]\d{1,2}$")


def chain_root(underlying: str) -> str:
    """A futures contract ("./ESZ6", "/ESZ6") as its root ("/ES"); anything
    else unchanged."""
    symbol = (underlying or "").strip().upper()
    if symbol.startswith("./"):
        symbol = symbol[1:]
    if not symbol.startswith("/"):
        return underlying
    found = _FUTURES_MONTH.match(symbol)
    return "/" + found.group(1) if found else symbol


# --- held legs: the chain's Pos column (#210, Tracker2's analyzer_held.py) ---


def holds_ticker(underlying: str, ticker: str) -> bool:
    """True when a leg's underlying belongs to the chain's ticker. A futures
    chain is a root (/GC) and its legs carry the contract month (/GCZ6), so a
    root matches every month of it; a weekly root (SPXW) is its index."""
    if ticker.startswith("/"):
        return chain_root(underlying or "") == ticker
    return normalize_underlying(underlying) == normalize_underlying(ticker)


def held_legs(positions, account_ids: Mapping[tuple[str, str], int], ticker: str) -> list[dict]:
    """What each account holds on the ticker: open option contracts netted per
    account, expiration, strike and call/put, signed (long +, short -), across
    positions. Zero nets, legs a roll closed, and accounts not in `account_ids`
    ((broker, number) -> id) are left out."""
    net: dict[tuple, Decimal] = defaultdict(Decimal)
    for position in positions:
        account_id = account_ids.get((position.broker, position.account_number))
        if account_id is None:
            continue
        for leg in position.live_legs:
            if leg.option is None or leg.remaining <= 0 or not holds_ticker(leg.underlying, ticker):
                continue
            key = (
                account_id,
                leg.option.expiration.isoformat(),
                float(leg.option.strike),
                leg.option.put_call.lower(),
            )
            net[key] += leg.sign * leg.remaining
    return [
        {
            "account_id": k[0],
            "expiration": k[1],
            "strike": k[2],
            "type": k[3],
            "qty": int(q) if q == q.to_integral_value() else float(q),
        }
        for k, q in sorted(net.items())
        if q
    ]


def pick_series(expirations: list[dict], today: date) -> dict[str, dict]:
    """One option series per date, today through five years out, from
    TastyTrade's nested futures chain. AM-settled series are left out; of the
    rest the one with the most strikes wins, ties to the lower root."""
    horizon = date(today.year + 5, 12, 31)
    best: dict[date, tuple] = {}
    for e in expirations or []:
        day = date.fromisoformat(e["expiration-date"])
        if not today <= day <= horizon or (e.get("settlement-type") or "").upper() == "AM":
            continue
        rank = (-len(e.get("strikes") or []), e.get("option-root-symbol") or "")
        if day not in best or rank < best[day][0]:
            best[day] = (rank, e)
    return {day.isoformat(): e for day, (_, e) in sorted(best.items())}


def series_info(e: dict) -> dict:
    """A series as the page wants it: option root, the contract it settles
    into, when it expires, and dollars per point."""
    multiplier = round(float(e["notional-value"]) * 100, 6)
    return {
        "root": e.get("option-root-symbol"),
        "underlying": e.get("underlying-symbol"),
        "expires_at": datetime.fromisoformat(e["expires-at"]).isoformat(),
        "multiplier": int(multiplier) if multiplier.is_integer() else multiplier,
    }


def _strike_text(strike) -> str:
    return (f"{float(strike):f}").rstrip("0").rstrip(".")


def symbol_format(e: dict) -> tuple[str, str] | None:
    """(root, exchange) such that every strike's DXLink name is root + C/P +
    strike + exchange ("./EW1X26", ":XCME"), or None when any name breaks the
    pattern. Lets the page name a leg outside the loaded strikes."""
    strikes = e.get("strikes") or []
    if not strikes:
        return None
    first = strikes[0]
    tag = "C" + _strike_text(first["strike-price"])
    call = first.get("call-streamer-symbol") or ""
    at = call.rfind(tag)
    if at < 0:
        return None
    root, exchange = call[:at], call[at + len(tag) :]
    for k in strikes:
        text = _strike_text(k["strike-price"])
        if (
            k.get("call-streamer-symbol") != f"{root}C{text}{exchange}"
            or k.get("put-streamer-symbol") != f"{root}P{text}{exchange}"
        ):
            return None
    return root, exchange


def occ_from_dxlink(symbol: str) -> str | None:
    """ ".SPXW261017C5800" -> "SPXW  261017C05800000" (the app's option key)."""
    found = _DXLINK_OPTION.match(symbol or "")
    if not found:
        return None
    root, yymmdd, right, strike = found.groups()
    return f"{root:<6}{yymmdd}{right}{int(round(float(strike) * 1000)):08d}"
