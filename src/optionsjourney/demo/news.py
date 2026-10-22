"""Made-up headlines for the News panel in demo mode.

Generic on purpose: no directional claims, no real companies' news."""

from __future__ import annotations

import random
import zlib
from datetime import datetime, timedelta

SOURCE = "Demo headlines"

_TEMPLATES = [
    (
        "Options volume picks up ahead of Friday's expiration",
        "Traders adjust positions before the weekly expiry.",
    ),
    (
        "Analysts weigh the quarter ahead",
        "Notes from several desks look at what the next report may bring.",
    ),
    ("{s} in focus as the session gets going", "Early trading draws attention to {s}."),
    (
        "What traders are watching in {s} this week",
        "A look at the levels and events on the calendar.",
    ),
    (
        "Implied volatility holds steady for {s}",
        "Option prices show no big change in expected movement.",
    ),
    ("A quiet stretch for the calendar", "Few scheduled reports are due before the close."),
    (
        "Weekly expiration brings fresh strikes to {s}",
        "New contracts begin trading as the old ones near expiry.",
    ),
    ("Market participants review their risk", "Position sizes and stop levels are on many minds."),
    (
        "How options traders plan around events",
        "A primer on timing trades near scheduled announcements.",
    ),
    (
        "Trading volume in {s} compared with last week",
        "Volume figures are close to the recent average.",
    ),
]


def headlines(symbol: str, now: datetime) -> dict:
    sym = (symbol or "").strip().upper()
    rng = random.Random(zlib.crc32(sym.encode()))
    count = rng.randint(6, 10)
    chosen = rng.sample(_TEMPLATES, k=count) if count <= len(_TEMPLATES) else _TEMPLATES
    items = []
    for n, (head, summary) in enumerate(chosen):
        when = now - timedelta(minutes=25 + n * 70 + rng.randint(0, 30))
        items.append(
            {
                "headline": head.format(s=sym),
                "source": SOURCE,
                "url": "",
                "summary": summary.format(s=sym),
                "datetime": int(when.timestamp()),
            }
        )
    return {"symbol": sym, "news": items}
