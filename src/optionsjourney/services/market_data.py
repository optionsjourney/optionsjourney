"""Prices from the user's brokers (decision 0021): TastyTrade first, Schwab
as the backup. For now, daily closes, used to estimate expired trades (#23);
live quotes and streaming come with #22.

The closes come from the one official-close reader
(`expiry_prices.official_closes`), so the journal and the 4:15 settlement agree.
Only official closes are kept, never a last price. Past closes never change, so
each one is fetched once per run of the app. A close no broker has yet is asked
for again only after MISS_RETRY, and Schwab gets at most JOURNAL_BUDGET_S of a
page's time, so a slow or down broker never holds the journal up."""

import threading
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from optionsjourney.services.expiry_prices import official_closes

MISS_RETRY = timedelta(minutes=5)  # how long a close nobody had is left alone
JOURNAL_BUDGET_S = 3.0  # most of a page's time Schwab's history may take

Key = tuple[str, date]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CloseCache:
    """Closing prices by (underlying, day), kept for the life of the app."""

    def __init__(self, now: Callable[[], datetime] = _utcnow):
        self._now = now
        self._lock = threading.Lock()
        self._closes: dict[Key, Decimal] = {}
        self._missed: dict[Key, datetime] = {}

    def closes(self, clients: Iterable, wanted: set[Key]) -> dict[Key, Decimal]:
        now = self._now()
        with self._lock:
            ask = {
                key
                for key in wanted
                if key not in self._closes
                and (key not in self._missed or now - self._missed[key] >= MISS_RETRY)
            }
        if ask:
            reads = official_closes(
                list(clients), ask, now=now, tastytrade_first=True, budget_s=JOURNAL_BUDGET_S
            )  # no lock held: this talks to the brokers
            with self._lock:
                for key in ask:
                    read = reads.get(key)
                    if read is not None and read.final:
                        self._closes[key] = read.price
                        self._missed.pop(key, None)
                    else:
                        self._missed[key] = now
        with self._lock:
            return {key: self._closes[key] for key in wanted if key in self._closes}
