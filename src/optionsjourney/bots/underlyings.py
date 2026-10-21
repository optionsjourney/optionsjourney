"""What a bot can trade, and how each underlying differs (spec: bots slice 16, section 1;
decision 0042). Pure.

Like Tracker2 (templates/automation_strategy.html f-underlying), the underlying is free
text: SPX, XSP, or any stock or ETF (the author's ruling, 2026-10-13). SPX, XSP and the
big ETFs SPY, QQQ and IWM are suggested on the form; another index (RUT, NDX, VIX) isn't
traded. A stock's single-leg ticks come from TastyTrade (option_ticks.ensure,
trading/ticks.learn).

- **root**: the option root the bot trades, the one the chain prices
  (core/analyzer.WEEKLY_ROOTS, Tracker2's chain.weekly_root): never an AM-settled
  monthly SPX, never an adjusted contract such as SPY1.
- **settles**: "cash" (European, cash-settled at the close, expiry_service.PM_CASH_SETTLED)
  or "shares" (American, physically settled: assigned at any time, and exercised or
  assigned at expiry when in the money).
- **watch symbol** and **divisor**: the price the watch rules and the market filters
  read, and what turns it into the traded underlying's points (technicals.DX_SYMBOLS,
  SPOT_DIVISORS: XSP reads SPX ÷ 10, because SPX prints reliably on DXLink).
- **grid**: the chase's step and start offset (SPX's $0.05 tick, else a cent).

Every contract is 100 of the underlying (tickers.run_multiplier), except a futures
option's: bots slice 17 adds futures roots (/ES, /MES, /GC), whose options trade through
their series (bots/futures.py) at each series' own multiplier, recorded on the run."""

import re
from dataclasses import dataclass
from decimal import Decimal

from optionsjourney.core.accounts import INDEXES
from optionsjourney.core.chain import WEEKLY_ROOTS, chain_root

NICKEL, CENT = Decimal("0.05"), Decimal("0.01")


@dataclass(frozen=True)
class Underlying:
    symbol: str
    settles: str  # "cash", "shares" or "futures" (bots slice 17)
    watch_symbol: str
    divisor: Decimal
    grid: Decimal
    about: str  # one line for the form

    @property
    def root(self) -> str:
        return WEEKLY_ROOTS.get(self.symbol, self.symbol)

    @property
    def american(self) -> bool:
        return self.settles == "shares"


_ROWS = (
    Underlying("SPX", "cash", "SPX", Decimal(1), NICKEL,
               "S&P 500 index, settles in cash"),
    Underlying("XSP", "cash", "SPX", Decimal(10), CENT,
               "Mini-SPX, a tenth of SPX, settles in cash"),
    Underlying("SPY", "shares", "SPY", Decimal(1), CENT,
               "S&P 500 ETF, settles in shares"),
    Underlying("QQQ", "shares", "QQQ", Decimal(1), CENT,
               "Nasdaq-100 ETF, settles in shares"),
    Underlying("IWM", "shares", "IWM", Decimal(1), CENT,
               "Russell 2000 ETF, settles in shares"),
)  # fmt: skip
UNDERLYINGS = {row.symbol: row for row in _ROWS}
SYMBOLS = tuple(UNDERLYINGS)  # the form's suggestions
CASH = frozenset(u.symbol for u in _ROWS if not u.american)  # SPX, XSP
# The author's ruling (2026-10-13): any stock or ETF, typed as in Tracker2's f-underlying.
# Other cash-settled indexes (RUT, NDX, VIX, ...) have AM roots and tiers of their own.
# Their option roots too, which would otherwise pass as tickers (Tracker2's
# expiry_service.PM_CASH_SETTLED and AM_SETTLED_ALWAYS, and Cboe's other index roots).
INDEX_ROOTS = frozenset({
    "SPXW", "XSPW", "RUTW", "NDXP", "MRUT", "DJX", "DJXW", "VIXW", "XND", "OEX", "XEO",
})  # fmt: skip
OTHER_INDEXES = (frozenset(INDEXES) | INDEX_ROOTS) - CASH
WEEKLY_OF = {"SPXW": "SPX", "XSPW": "XSP"}  # typed as its weekly root
# A stock's (analyzer.clean_ticker without futures). No dot: a class share like BRK.B lists
# its options under another root (BRKB), which a bot can't match to its ticker yet.
TICKER = re.compile(r"^[A-Z]{1,10}$")
# Bots slice 17: a futures root (Tracker2's analyzer_bp._TICKER_RE), never a contract month
FUTURES_ROOT = re.compile(r"^/[A-Z][A-Z0-9]{0,3}$")
SUGGESTED = (*SYMBOLS, "/ES", "/MES")  # the form's suggestions


def _futures_root(text: str) -> bool:
    return bool(FUTURES_ROOT.match(text)) and chain_root(text) == text


def futures(symbol: str | None) -> bool:
    """A futures root (/ES): its options trade through TastyTrade only, at a profit
    target only (decision 0043)."""
    return _futures_root((symbol or "").strip())


def get(symbol: str | None) -> Underlying | None:
    """The listed five, or any other ticker as a stock or ETF: American, settled in shares,
    its own root and price, a cent chase grid. None for a blank, a futures symbol or
    another index."""
    text = (symbol or "").strip().upper()
    found = UNDERLYINGS.get(text)
    if found is not None:
        return found
    if _futures_root(text):
        return Underlying(text, "futures", text, Decimal(1), CENT,
                          "futures options, through TastyTrade only")  # fmt: skip
    if not TICKER.match(text) or text in OTHER_INDEXES:
        return None
    return Underlying(text, "shares", text, Decimal(1), CENT, "a stock or ETF, settles in shares")


def problem(symbol: str | None) -> str | None:
    """Why a bot can't trade this underlying (pure: whether the broker lists options on
    it is checked when it's saved, bot_store.save)."""
    text = (symbol or "").strip().upper()
    if text in WEEKLY_OF:
        return f"Enter {WEEKLY_OF[text]}: the bot trades its weekly ({text}) options itself."
    if text in OTHER_INDEXES:
        return f"Bots trade SPX and XSP among the indexes, not {text}."
    if FUTURES_ROOT.match(text) and chain_root(text) != text:
        return f"Enter the root, {chain_root(text)}, not a contract month."
    if "." in text:
        return (
            f"{text} can't be traded by a bot yet: a share class's options trade under "
            "another symbol (BRK.B's are BRKB), which the bot can't match to it."
        )
    if get(text) is None or text != symbol:
        return "Enter a stock or ETF ticker like SPY or AAPL, or SPX or XSP."
    return None


def root(symbol: str | None) -> str:
    """The option root the bot trades; an unknown symbol is its own root."""
    found = get(symbol)
    return found.root if found else (symbol or "").strip().upper()


def american(symbol: str | None) -> bool:
    found = get(symbol)
    return found is not None and found.american


def watch_symbol(symbol: str | None) -> str:
    """The quote the watch rules, the entry and the market filters read
    (technicals.dx_symbol): SPX for XSP, else the symbol."""
    found = get(symbol)
    return found.watch_symbol if found else (symbol or "").strip().upper()


def divisor(symbol: str | None) -> Decimal:
    """watch_symbol's price ÷ this = the traded underlying's points (spot_divisor)."""
    found = get(symbol)
    return found.divisor if found else Decimal(1)


def in_points(symbol: str | None, price: Decimal) -> Decimal:
    return price / divisor(symbol)


def grid(symbol: str | None) -> Decimal:
    """The chase step and start offset's grid: SPX's tick, else a cent."""
    found = get(symbol)
    return found.grid if found else NICKEL
