"""Live quotes for Analyze's legs and underlying (#26), pushed over Socket.IO
as Tracker2's analyzer expects ("subscribe_risk_quotes" {symbols} ->
"risk_quotes" {quotes: {SYMBOL: {bid, ask, last}}, ts}).

The page names symbols the DXLink way (".SPXW261017C5800", "SPX",
"/ESZ26:XCME"); they're read from the quote hub (TastyTrade first, Schwab as
backup). Each page gets only the quotes that changed, about once a second."""

import logging
import re
import threading
import time
from collections.abc import Callable

from optionsjourney.core.chain import occ_from_dxlink
from optionsjourney.core.quotes import DXLINK_KEY

logger = logging.getLogger(__name__)

MAX_SYMBOLS = 64
PUSH_EVERY = 1.0
FIELDS = ("bid", "ask", "last")
_SYMBOL = re.compile(
    r"^(\.[A-Z]{1,6}\d{6}[CP]\d+(\.\d+)?|[A-Z]{1,6}"
    r"|/[A-Z0-9]{2,8}:[A-Z]{3,4}|\./[A-Z0-9]{2,10}[CP]\d+(\.\d+)?:[A-Z]{3,4})$"
)


def hub_key(symbol: str) -> str:
    """The quote hub's key for a DXLink symbol."""
    if symbol.startswith("/") or symbol.startswith("./"):
        return DXLINK_KEY + symbol
    return occ_from_dxlink(symbol) or symbol


def _float(value):
    return None if value is None else float(value)


class RiskQuotes:
    def __init__(self, hub, emit: Callable[..., None], *, start_thread: bool = True):
        self._hub = hub
        self._emit = emit
        self._start_thread = start_thread
        self._lock = threading.Lock()
        self._watchers: dict[str, dict[str, tuple | None]] = {}  # sid -> symbol -> last sent
        self._thread: threading.Thread | None = None

    def subscribe(self, sid: str, symbols) -> None:
        wanted: list[str] = []
        for symbol in symbols or []:
            if isinstance(symbol, str) and _SYMBOL.match(symbol) and symbol not in wanted:
                wanted.append(symbol)
        wanted = wanted[:MAX_SYMBOLS]
        with self._lock:
            self._watchers[sid] = dict.fromkeys(wanted)
        if wanted:
            self._hub.subscribe({hub_key(s) for s in wanted})
        self.push([sid])
        self._ensure_thread()

    def unsubscribe(self, sid: str) -> None:
        with self._lock:
            self._watchers.pop(sid, None)

    def push(self, sids: list[str] | None = None) -> None:
        with self._lock:
            sids = list(self._watchers) if sids is None else sids
        for sid in sids:
            with self._lock:
                symbols = list(self._watchers.get(sid) or {})
            current = {}
            for symbol in symbols:
                quote = self._hub.latest(hub_key(symbol))
                if quote is not None:
                    current[symbol] = (_float(quote.bid), _float(quote.ask), _float(quote.last))
            changed = {}
            with self._lock:
                sent = self._watchers.get(sid)
                if sent is None:
                    continue
                for symbol, values in current.items():
                    if values != sent.get(symbol) and any(v is not None for v in values):
                        sent[symbol] = values
                        changed[symbol] = dict(zip(FIELDS, values, strict=True))
            if changed:
                try:
                    self._emit(
                        "risk_quotes", {"quotes": changed, "ts": int(time.time() * 1000)}, to=sid
                    )
                except Exception:  # noqa: BLE001 -- one bad client must not stop the rest
                    logger.exception("risk_quotes to %s failed", sid)

    def _ensure_thread(self) -> None:
        if not self._start_thread:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="risk-quotes", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            time.sleep(PUSH_EVERY)
            with self._lock:
                if not self._watchers:
                    self._thread = None
                    return
            try:
                self.push()
            except Exception:  # noqa: BLE001
                logger.exception("risk quotes push failed")
