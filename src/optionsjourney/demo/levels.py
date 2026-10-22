"""Starting levels and volatilities for the pretend market."""

from __future__ import annotations

START: dict[str, float] = {
    "SPX": 5800.0,
    "SPY": 579.0,
    "QQQ": 500.0,
    "IWM": 225.0,
    "AAPL": 235.0,
    "MSFT": 420.0,
    "NVDA": 135.0,
    "VIX": 16.5,
    "VIX9D": 15.675,  # VIX x VIX9D_RATIO: it moves with VIX (the bots' VIX9D/VIX filter)
}

# VIX9D has no path of its own: it is always VIX times this.
VIX9D_RATIO = 0.95

# Annual volatility: drives the path and the option prices. VIX is not here;
# it follows the index rather than having a path of its own.
VOL: dict[str, float] = {
    "SPX": 0.15,
    "SPY": 0.15,
    "QQQ": 0.19,
    "IWM": 0.22,
    "AAPL": 0.28,
    "MSFT": 0.25,
    "NVDA": 0.45,
}

# How closely each ticker follows the market's shared moves (1.0 = identical).
CORRELATION: dict[str, float] = {
    "SPX": 1.0,
    "SPY": 1.0,
    "QQQ": 0.9,
    "IWM": 0.8,
    "AAPL": 0.6,
    "MSFT": 0.6,
    "NVDA": 0.6,
}

VIX_BASE = 16.5
