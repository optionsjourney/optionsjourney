"""Symbols typed into the app's forms (charts, Analyze, alerts)."""

import re

SYMBOL = re.compile(r"^[A-Z0-9./$^]{1,20}$")


class ChartError(ValueError):
    """A request the charts page can't serve; the message says why."""


def clean_symbol(text: str) -> str:
    symbol = (text or "").strip().upper()
    if not SYMBOL.match(symbol):
        raise ChartError("Enter a symbol such as SPY or SPX.")
    return symbol
