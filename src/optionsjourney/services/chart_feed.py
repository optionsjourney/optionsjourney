"""Live chart candles from TastyTrade's DXLink, pushed to the chart pages over
Socket.IO (#25). Tracker2's chart feed, on the app's own DXLink connection code.

A page subscribes ("subscribe_candles" {symbol, interval}) and gets the whole
history once ("chart_candles_snapshot") and then each change to the forming bar
("chart_candle_update"). Bars are in core/chart_bars.py's format.

Its own DXLink connection, separate from the quote stream. It connects for
the first viewer and disconnects IDLE_DISCONNECT seconds after the last one
leaves. Without TastyTrade nothing connects: the pages then ask
/api/chart/pricehistory (Schwab) after ten seconds."""

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.core.chart_bars import (
    HISTORY,
    CandleBook,
    dxlink_period,
    parse_event_symbol,
    valid_feed_symbol,
)

logger = logging.getLogger(__name__)

CHANNEL = 5
FIELDS = ["eventType", "eventSymbol", "eventFlags", "time", "open", "high", "low", "close"]
FIELDS += ["volume"]
IDLE_DISCONNECT = 60  # seconds
KEEPALIVE_EVERY = 30
BACKOFF = (1, 2, 5, 10, 30)
NOT_CONNECTED_PAUSE = 60


def room(symbol: str, interval: str) -> str:
    return f"candles:{symbol}:{interval}"


class _Ended(Exception):
    """The connection closed."""


class ChartFeed:
    def __init__(
        self,
        broker: Callable[[], object | None],
        emit: Callable[..., None],
        *,
        connect: Callable | None = None,
        autostart: bool = True,
        idle_disconnect: float = IDLE_DISCONNECT,
    ):
        self._broker = broker
        self._emit = emit
        self._connect = connect
        self._autostart = autostart
        self._idle_disconnect = idle_disconnect
        self._lock = threading.Lock()
        self._viewers: dict[tuple[str, str], set[str]] = {}
        self._books: dict[tuple[str, str], CandleBook] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # --- who is watching (any thread) ---

    def add(self, sid: str, symbol: str, interval: str) -> bool:
        symbol = (symbol or "").upper()
        if interval not in HISTORY or not valid_feed_symbol(symbol):
            return False
        with self._lock:
            self._viewers.setdefault((symbol, interval), set()).add(sid)
        if self._autostart:
            self._ensure_running()
        return True

    def remove(self, sid: str, symbol: str, interval: str) -> None:
        key = ((symbol or "").upper(), interval)
        with self._lock:
            sids = self._viewers.get(key)
            if sids is not None:
                sids.discard(sid)
                if not sids:
                    del self._viewers[key]

    def remove_sid(self, sid: str) -> None:
        with self._lock:
            for key in list(self._viewers):
                self._viewers[key].discard(sid)
                if not self._viewers[key]:
                    del self._viewers[key]

    def wanted(self) -> set[tuple[str, str]]:
        with self._lock:
            return set(self._viewers)

    def snapshot(self, symbol: str, interval: str) -> dict | None:
        key = ((symbol or "").upper(), interval)
        with self._lock:
            book = self._books.get(key)
            if book is None or not book.ready:
                return None
            return {"symbol": key[0], "interval": interval, "bars": book.bars()}

    def status(self) -> dict:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "viewers": {f"{s}:{i}": len(v) for (s, i), v in self._viewers.items()},
                "ready": sorted(f"{s}:{i}" for (s, i), b in self._books.items() if b.ready),
            }

    # --- events ---

    def on_candle(self, event: dict) -> None:
        parsed = parse_event_symbol(event.get("eventSymbol"))
        if parsed is None or parsed[1] not in HISTORY:
            return
        symbol, interval = parsed
        payload = None
        with self._lock:
            book = self._books.get(parsed)
            if book is None:
                book = self._books[parsed] = CandleBook(
                    interval, full_session=symbol.startswith("/")
                )
            what = book.apply(
                event.get("eventFlags"),
                event.get("time") or 0,
                event.get("open"),
                event.get("high"),
                event.get("low"),
                event.get("close"),
                event.get("volume"),
            )
            if what == "snapshot":
                payload = {"symbol": symbol, "interval": interval, "bars": book.bars()}
            elif what == "update":
                payload = {"symbol": symbol, "interval": interval, "bar": dict(book.last_changed)}
        if payload is None:
            return
        name = "chart_candles_snapshot" if what == "snapshot" else "chart_candle_update"
        try:
            self._emit(name, payload, to=room(symbol, interval))
        except Exception:  # noqa: BLE001 -- one bad emit must not stop the feed
            logger.exception("chart feed: emit %s failed", name)

    # --- the connection ---

    def _ensure_running(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="chart-feed", daemon=True)
            self._thread.start()

    def _exit_if_idle(self) -> bool:
        """Under the lock, so an add() racing the exit either keeps this
        thread or starts a new one."""
        with self._lock:
            if self._viewers and not self._stop.is_set():
                return False
            self._thread = None
            self._books.clear()
            return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        failures = 0
        told_not_connected = False  # say it once, then keep checking quietly
        while not self._exit_if_idle():
            pause = 0.0
            try:
                broker = self._broker()
                if broker is None:
                    raise CredentialsUnavailable("TastyTrade isn't connected")
                token, url = broker.quote_token()
                with self._open(url) as socket:
                    self.run_session(socket, token)
                failures = 0
                told_not_connected = False
            except CredentialsUnavailable as e:
                if not told_not_connected:
                    logger.info("chart feed: %s", e)
                    told_not_connected = True
                pause = NOT_CONNECTED_PAUSE
            except Exception as e:  # noqa: BLE001 -- reconnect after anything
                logger.warning("chart feed dropped: %s", e)
                told_not_connected = False
                pause = BACKOFF[min(failures, len(BACKOFF) - 1)]
                failures += 1
            until = time.monotonic() + pause
            while time.monotonic() < until and self.wanted() and not self._stop.is_set():
                time.sleep(0.2)

    def _open(self, url: str):
        if self._connect is not None:
            return self._connect(url)
        from optionsjourney.services.streaming import _connect  # noqa: PLC0415

        return _connect(url)

    def run_session(self, socket, token: str, now: Callable[[], datetime] | None = None) -> None:
        """One connection: until it closes, the feed stops, or nobody has
        watched for IDLE_DISCONNECT seconds."""
        now = now or (lambda: datetime.now(UTC))
        send = lambda message: socket.send(json.dumps(message))  # noqa: E731
        send(
            {
                "type": "SETUP",
                "channel": 0,
                "keepaliveTimeout": 60,
                "acceptKeepaliveTimeout": 60,
                "version": "0.1-optionsjourney",
            }
        )
        subscribed: set[tuple[str, str]] = set()
        channel_open, last_sent, idle_since = False, time.monotonic(), None
        while not self._stop.is_set():
            message = _receive(socket)
            if message is not None:
                kind = message.get("type")
                if kind == "AUTH_STATE" and message.get("state") == "UNAUTHORIZED":
                    send({"type": "AUTH", "channel": 0, "token": token})
                elif kind == "AUTH_STATE" and message.get("state") == "AUTHORIZED":
                    send(
                        {
                            "type": "CHANNEL_REQUEST",
                            "channel": CHANNEL,
                            "service": "FEED",
                            "parameters": {"contract": "AUTO"},
                        }
                    )
                elif kind == "CHANNEL_OPENED" and message.get("channel") == CHANNEL:
                    send(
                        {
                            "type": "FEED_SETUP",
                            "channel": CHANNEL,
                            "acceptAggregationPeriod": 1,
                            "acceptDataFormat": "COMPACT",
                            "acceptEventFields": {"Candle": FIELDS},
                        }
                    )
                    channel_open = True
                elif kind == "FEED_DATA":
                    for event in _events(message.get("data") or []):
                        self.on_candle(event)
                elif kind == "ERROR":
                    raise ConnectionError(f"DXLink refused: {message.get('message')}")
            wanted = self.wanted()
            if channel_open and wanted != subscribed:
                add = [
                    {
                        "type": "Candle",
                        "symbol": f"{s}{{={dxlink_period(i)}}}",
                        "fromTime": int((now() - HISTORY[i]).timestamp() * 1000),
                    }
                    for s, i in sorted(wanted - subscribed)
                ]
                remove = [
                    {"type": "Candle", "symbol": f"{s}{{={dxlink_period(i)}}}"}
                    for s, i in sorted(subscribed - wanted)
                ]
                with self._lock:
                    for key in subscribed - wanted:
                        self._books.pop(key, None)
                message = {"type": "FEED_SUBSCRIPTION", "channel": CHANNEL}
                send(
                    message | ({"add": add} if add else {}) | ({"remove": remove} if remove else {})
                )
                logger.info("chart feed: candles %s", sorted(f"{s}:{i}" for s, i in wanted))
                subscribed, last_sent = wanted, time.monotonic()
            idle_since = (idle_since or time.monotonic()) if not wanted else None
            if idle_since and time.monotonic() - idle_since >= self._idle_disconnect:
                return
            if time.monotonic() - last_sent > KEEPALIVE_EVERY:
                send({"type": "KEEPALIVE", "channel": 0})
                last_sent = time.monotonic()


def _receive(socket) -> dict | None:
    from optionsjourney.services.streaming import _closed_errors  # noqa: PLC0415

    try:
        text = socket.recv(timeout=1)
    except TimeoutError:
        return None
    except _closed_errors() as e:
        raise _Ended from e
    return json.loads(text)


def _events(data: list) -> list[dict]:
    """COMPACT feed data: ["Candle", [v1, v2, ...]] with FIELDS repeating."""
    if len(data) < 2 or not isinstance(data[1], list):
        return []
    values, size = data[1], len(FIELDS)
    return [
        dict(zip(FIELDS, values[i : i + size], strict=True))
        for i in range(0, len(values) - size + 1, size)
    ]
