"""Live quotes for the chart grid's watchlist drawer (#25), pushed over
Socket.IO as Tracker2's drawer expects ("watchlist_quotes" {quotes: {SYMBOL:
{last, net_chg, net_pct, mark, mark_pct}}, source, ts}).

Prices come from the quote hub (TastyTrade first, Schwab as backup). The
previous close the changes are measured from, and any symbol the streams
haven't priced yet (futures roots like /ES), come from the brokers' quote
snapshots, at most every REST_EVERY seconds. Each client gets only the rows
that changed, about once a second."""

import logging
import re
import threading
import time
from collections.abc import Callable

from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import INDEXES

logger = logging.getLogger(__name__)

MAX_SYMBOLS = 128
PUSH_EVERY = 1.0
REST_EVERY = 20.0
FIELDS = ("last", "net_chg", "net_pct", "mark", "mark_pct")
_SYMBOL = re.compile(r"^(?:[A-Z]{1,6}|/[A-Z0-9]{1,4})$")


def _float(value) -> float | None:
    try:
        return None if value is None else float(value)
    except TypeError, ValueError:
        return None


def row(last, prev_close, mark=None) -> dict:
    """A watchlist row. Without a mark (indexes) Mark shows Last, as ToS does."""
    last, prev_close, mark = _float(last), _float(prev_close), _float(mark)
    mark = mark or last

    def change(price):
        if price is None or not prev_close:
            return None, None
        return round(price - prev_close, 4), round((price - prev_close) / prev_close * 100, 4)

    net_chg, net_pct = change(last)
    return {
        "last": last,
        "net_chg": net_chg,
        "net_pct": net_pct,
        "mark": mark,
        "mark_pct": change(mark)[1],
    }


def snapshot_quotes(clients: list, symbols: list[str]) -> dict[str, dict]:
    """{symbol: {"last", "mark", "prev_close"}} from the brokers' quote
    snapshots: Schwab's, then TastyTrade's for what Schwab didn't answer."""
    found: dict[str, dict] = {}
    # Schwab first, as Tracker2: TastyTrade's index prev-close moves to the
    # latest session at the weekend, which zeroes the day's change
    for client in sorted(clients, key=lambda c: not hasattr(c, "quotes")):
        missing = [s for s in symbols if s not in found]
        if not missing:
            break
        try:
            if hasattr(client, "quotes"):
                by_schwab = {f"${s}" if s in INDEXES else s: s for s in missing}
                for key, body in (client.quotes(list(by_schwab)) or {}).items():
                    body = body or {}
                    product = ((body.get("reference") or {}).get("product") or "").upper()
                    symbol = by_schwab.get(key) or by_schwab.get(product)
                    quote = body.get("quote") or {}
                    if symbol is None or quote.get("lastPrice") is None:
                        continue
                    # Stocks: the regular session's change, as Tracker2 shows it. At
                    # the weekend closePrice is already Friday's; last minus the
                    # session's net change is the close it was measured from.
                    regular = body.get("regular") or {}
                    last, prev_close = quote["lastPrice"], quote.get("closePrice")
                    reg_last = regular.get("regularMarketLastPrice")
                    reg_change = regular.get("regularMarketNetChange")
                    if body.get("assetMainType") != "FUTURE" and reg_last is not None:
                        last = reg_last
                        if reg_change is not None:
                            prev_close = reg_last - reg_change
                    found[symbol] = {
                        "last": last,
                        "mark": quote.get("mark"),
                        "prev_close": prev_close,
                    }
            elif hasattr(client, "market_data"):
                stocks = [s for s in missing if not s.startswith("/")]
                by_type = {
                    "index": [s for s in stocks if s in INDEXES],
                    "equity": [s for s in stocks if s not in INDEXES],
                }
                for item in client.market_data(by_type):
                    if item.get("symbol") in missing:
                        found[item["symbol"]] = {
                            "last": item.get("last"),
                            "mark": item.get("mark"),
                            "prev_close": item.get("prev-close"),
                        }
        except (BrokerError, CredentialsUnavailable, OSError) as e:
            logger.info("watchlist quotes from %s unavailable: %s", client.name, e)
    return found


class WatchlistQuotes:
    def __init__(
        self,
        hub,
        clients: Callable[[], list],
        emit: Callable[..., None],
        *,
        clock: Callable[[], float] = time.monotonic,
        start_thread: bool = True,
    ):
        self._hub = hub
        self._clients = clients
        self._emit = emit
        self._clock = clock
        self._start_thread = start_thread
        self._lock = threading.Lock()
        self._watchers: dict[str, dict] = {}  # sid -> {"sent": {symbol: row tuple}, "source"}
        self._snapshot: dict[str, dict] = {}
        self._snapshot_at: float | None = None
        self._snapshot_tried: set[str] = set()
        self._snapshot_failed = False
        self._thread: threading.Thread | None = None

    def subscribe(self, sid: str, symbols) -> None:
        wanted: list[str] = []
        for symbol in symbols or []:
            symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
            if _SYMBOL.match(symbol) and symbol not in wanted:
                wanted.append(symbol)
        wanted = wanted[:MAX_SYMBOLS]
        with self._lock:
            self._watchers[sid] = {"sent": dict.fromkeys(wanted), "source": None}
        stocks = {s for s in wanted if not s.startswith("/")}
        if stocks:
            self._hub.subscribe(stocks)
        self.push([sid])
        self._ensure_thread()

    def unsubscribe(self, sid: str) -> None:
        with self._lock:
            self._watchers.pop(sid, None)

    def _refresh_snapshot(self, symbols: list[str]) -> None:
        now = self._clock()
        due = self._snapshot_at is None or now - self._snapshot_at >= REST_EVERY
        new = [s for s in symbols if s not in self._snapshot_tried]
        if not (due or new):
            return
        if due:
            self._snapshot_tried = set()
        self._snapshot_tried.update(symbols)
        self._snapshot_at = now
        try:
            self._snapshot.update(snapshot_quotes(self._clients(), symbols))
            self._snapshot_failed = False
        except Exception:  # noqa: BLE001 -- keep the last rows; tried again later
            self._snapshot_failed = True
            logger.warning("watchlist snapshot quotes failed", exc_info=True)

    def _rows(self, symbols: list[str]) -> dict[str, tuple[dict, str]]:
        self._refresh_snapshot(symbols)
        out = {}
        for symbol in symbols:
            snap = self._snapshot.get(symbol) or {}
            quote = None if symbol.startswith("/") else self._hub.latest(symbol)
            last = quote and (quote.last if quote.last is not None else quote.mark)
            if last is not None:
                mark = quote.mark if quote.bid and quote.ask else None
                out[symbol] = (row(last, snap.get("prev_close"), mark), "live")
            elif snap.get("last") is not None:
                out[symbol] = (row(snap["last"], snap.get("prev_close"), snap.get("mark")), "rest")
        return out

    def push(self, sids: list[str] | None = None) -> None:
        with self._lock:
            sids = list(self._watchers) if sids is None else sids
            symbols = sorted(
                {s for sid in sids if sid in self._watchers for s in self._watchers[sid]["sent"]}
            )
        rows = self._rows(symbols)
        for sid in sids:
            changed = {}
            with self._lock:
                watcher = self._watchers.get(sid)
                if watcher is None:
                    continue
                sources = {rows[s][1] for s in watcher["sent"] if s in rows}
                if "rest" in sources:
                    source = "stale" if self._snapshot_failed else "rest"
                else:
                    source = "live" if sources else "none"
                for symbol in watcher["sent"]:
                    if symbol not in rows:
                        continue
                    current = tuple(rows[symbol][0].get(f) for f in FIELDS)
                    if current != watcher["sent"][symbol]:
                        watcher["sent"][symbol] = current
                        changed[symbol] = dict(zip(FIELDS, current, strict=True))
                source_changed = source != watcher["source"]
                watcher["source"] = source
            if changed or source_changed:
                try:
                    self._emit(
                        "watchlist_quotes",
                        {"quotes": changed, "source": source, "ts": int(time.time() * 1000)},
                        to=sid,
                    )
                except Exception:  # noqa: BLE001 -- one bad client must not stop the rest
                    logger.exception("watchlist_quotes to %s failed", sid)

    def _ensure_thread(self) -> None:
        if not self._start_thread:
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="watchlist-quotes", daemon=True)
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
                logger.exception("watchlist quotes push failed")
