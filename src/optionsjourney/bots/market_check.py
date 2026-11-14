"""Can today's entries read the market data they need? (#327, decision 0130; Tracker2's
market_data_check.) Pure: services/bot_market_check.py asks the brokers, this decides.

For each underlying an armed bot trades today, an entry reads the listed expirations
(TastyTrade's list first, Schwab's as the backup) and, for the expiration it trades, an
option chain (Schwab's snapshot first, TastyTrade's live quotes as the backup), with
deltas when a leg picks by delta. Each underlying's answer is:

- **blocked**: its entries will not be placed (nothing lists expirations, no chain, or
  a delta bot's chain has no deltas);
- **degraded**: they will, from the backup broker (or there's no backup today);
- neither: all is well, and nothing is said.

A broker that isn't connected is never a problem: a friend may use one broker only, and
the demo's pretend Schwab has no expiration list. Only a connected broker that fails, or
lists nothing, counts."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date

from optionsjourney.bots import floating, schedule, underlyings
from optionsjourney.bots.definition import BotDef
from optionsjourney.bots.strikes import reads_deltas

OPEN = "09:30"  # the regular session: every underlying's options quote from here
ERROR_LENGTH = 120


@dataclass(frozen=True)
class Listing:
    """One broker's expiration list: not connected, the dates it lists, or why it failed."""

    connected: bool
    days: tuple[date, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.connected and bool(self.days) and self.error is None

    @property
    def why(self) -> str:
        return _short(self.error) if self.error else "none listed"


@dataclass
class Result:
    symbol: str
    blocked: list[str] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blocked and not self.degraded


def _short(text: str) -> str:
    words = " ".join(str(text).split())
    return words if len(words) <= ERROR_LENGTH else words[: ERROR_LENGTH - 1] + "…"


def expirations(symbol: str, tasty: Listing, schwab: Listing) -> tuple[Result, tuple[date, ...]]:
    """The underlying's answer from each broker's expiration list, and the dates its
    entries will use (TastyTrade's, else Schwab's; none when blocked)."""
    result = Result(symbol)
    if tasty.ok:
        if schwab.connected and not schwab.ok:
            result.degraded.append(
                f"Schwab couldn't list expirations ({schwab.why}): there's no backup if "
                "TastyTrade fails today"
            )
        return result, tasty.days
    if schwab.ok:
        if tasty.connected:
            result.degraded.append(
                f"TastyTrade couldn't list expirations ({tasty.why}); entries will use "
                "Schwab's list"
            )
        return result, schwab.days
    asked = [f"{name} ({found.why})" for name, found in (("TastyTrade", tasty), ("Schwab", schwab))
             if found.connected]  # fmt: skip
    if not asked:
        result.blocked.append("no broker is connected to list expirations")
    else:
        result.blocked.append(f"no expirations from {' or '.join(asked)}: entries need them")
    return result, ()


def nearest(days: Iterable[date], today: date) -> date | None:
    """The first listed expiration today or later: the chain the check reads."""
    return min((d for d in days if d >= today), default=None)


def chain(
    result: Result, expiration: date | None, *, found: bool, backup: bool, deltas: bool | None
) -> None:
    """Add the chain's answer to `result`. `found`: a chain came back; `backup`: from the
    backup broker (TastyTrade's live quotes while Schwab is connected); `deltas`: whether
    it has them, None when no bot on it picks by delta (so they weren't checked)."""
    if expiration is None:
        result.blocked.append("no expiration today or later is listed")
        return
    when = f"{expiration:%m-%d}"
    if not found:
        result.blocked.append(f"no {when} option chain from your brokers")
    elif deltas is False:
        result.blocked.append(
            f"the {when} option chain has no deltas: entries that pick strikes by delta "
            "won't be placed"
        )
    elif backup:
        result.degraded.append(
            "Schwab's option chain failed; entries will price from TastyTrade's live quotes"
        )


def _entries_ahead(bot: BotDef, hhmm: str) -> bool:
    """An entry time still to come today (or its window not over)."""
    if bot.use_floating_entry:
        window = floating.window(bot)
        return window is not None and hhmm <= window[1]
    return any(t.enabled and t.time >= hhmm for t in bot.entry_times)


def due(
    bots: Iterable[BotDef], day: date, hhmm: str, lists: Mapping | None = None
) -> dict[str, bool]:
    """{underlying: whether a bot on it picks by delta} for the armed bots (Live or Paper)
    that trade on `day` (their frequency, not a blackout day: Tracker2's is_trading_day)
    with an entry still to come at `hhmm` (New York). Futures are left out: their options
    come from TastyTrade alone (decision 0080), with no backup to check."""
    found: dict[str, bool] = {}
    for bot in bots:
        symbol = (bot.underlying or "").upper()
        if not symbol or underlyings.futures(symbol) or not bot.enabled or bot.archived:
            continue
        if not schedule.runs_on(bot, day, lists) or schedule.is_blackout(bot, day, lists):
            continue
        if not _entries_ahead(bot, hhmm):
            continue
        found[symbol] = found.get(symbol, False) or reads_deltas(bot.legs)
    return found


def chain_due(symbol: str, hhmm: str) -> bool:
    """Whether a chain can be checked now: SPX and XSP quote overnight, so before the
    open; every other underlying only once the regular session has opened."""
    return symbol in underlyings.CASH or hhmm >= OPEN


def alert(results: Iterable[Result]) -> tuple[str, str] | None:
    """(title, detail) for the one alert, or None when every underlying is fine."""
    results = list(results)
    wrong = [r for r in results if not r.ok]
    if not wrong:
        return None
    detail = " ".join(f"{r.symbol}: {'; '.join(r.blocked + r.degraded)}." for r in wrong)
    blocked = [r.symbol for r in wrong if r.blocked]
    if blocked:
        title = f"Market data check: {', '.join(blocked)} entries will NOT be placed"
        return title, detail + " Check your broker connections on the Settings page."
    return "Market data degraded: entries will use a backup source", detail
