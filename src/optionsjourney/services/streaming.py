"""Live quotes from the brokers' streams (#22, decision 0021).

- TastyStream: TastyTrade's DXLink WebSocket (a quote token from
  /api-quote-tokens, then SETUP / AUTH / CHANNEL_REQUEST / FEED_SETUP /
  FEED_SUBSCRIPTION, COMPACT data, a KEEPALIVE every 30 seconds).
- SchwabStream: Schwab's streamer (its address from /userPreference, an
  ADMIN LOGIN with the access token, then LEVELONE_EQUITIES / _OPTIONS).
- QuoteHub: what the app asks. It subscribes both streams (so the backup is
  warm) and answers with TastyTrade's quote while its stream is up and
  its quote isn't more than 10 seconds older than Schwab's (decision 0080),
  otherwise Schwab's.

Streams run in background threads, start on the first subscription, and
reconnect by themselves with growing pauses. Nothing here places orders."""

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.brokers import guard
from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.core.quotes import (
    Quote,
    _decimal,  # a finite number or None: "Infinity" or NaN is never stored
    closing_mark,
    dxlink_symbol,
    futures_root,
    is_futures_option,
    no_market,
    parse_dxlink_feed,
    pick_quote,
    schwab_service_and_symbol,
    update_from_schwab,
)
from optionsjourney.core.symbols import parse_occ

logger = logging.getLogger(__name__)

RECV_WAIT = 1.0  # seconds; how often the loop gets to send subscriptions and keepalives
KEEPALIVE_EVERY = 30
RETRY_PAUSES = (5, 10, 30, 60)  # seconds before reconnecting, growing
NOT_CONNECTED_PAUSE = 120  # when the broker isn't connected at all


class StreamClosed(Exception):
    """The stream ended or the broker refused it. The message says why."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _connect(url: str):
    from websockets.sync.client import connect  # noqa: PLC0415 -- only when streaming

    return connect(url, open_timeout=15, close_timeout=5, max_size=2**23)


def _closed_errors() -> tuple[type[BaseException], ...]:
    from websockets.exceptions import ConnectionClosed  # noqa: PLC0415

    return (StreamClosed, ConnectionClosed)


class QuoteHub:
    def __init__(self, streams: list, now: Callable[[], datetime] = _utcnow):
        self.streams = streams
        self._now = now
        self._quotes: dict[str, dict[str, Quote]] = {}
        self._deltas: dict[str, tuple[Decimal, datetime]] = {}  # TastyTrade's Greeks only
        self._good: dict[str, Decimal] = {}  # each symbol's last real two-sided mark (#241)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        return self._now()

    def subscribe(self, keys: Iterable[str]) -> None:
        keys = set(keys)
        for stream in self.streams:
            stream.subscribe(keys)
            stream.start()

    def get(self, source: str, key: str) -> Quote | None:
        with self._lock:
            return self._quotes.get(source, {}).get(key)

    def put(self, quote: Quote) -> None:
        mark = quote.mark
        with self._lock:
            self._quotes.setdefault(quote.source, {})[quote.key] = quote
            if mark is not None and not no_market(quote):
                self._good[quote.key] = mark

    def good_mark(self, key: str) -> Decimal | None:
        """The symbol's last mark from a real two-sided quote (#241), or None."""
        with self._lock:
            return self._good.get(key)

    def good_marks(self, keys: Iterable[str]) -> dict[str, Decimal]:
        with self._lock:
            return {k: self._good[k] for k in keys if k in self._good}

    def seed_good_marks(self, marks: dict[str, Decimal]) -> None:
        """Marks saved at an earlier close, for symbols with none since the app started."""
        with self._lock:
            for key, mark in marks.items():
                self._good.setdefault(key, mark)

    def update(self, source: str, key: str, **prices: Decimal | None) -> None:
        """Merge new prices into the source's quote; missing ones are kept."""
        previous = self.get(source, key) or Quote(key, None, None, None, source, self._now())
        changes = {name: value for name, value in prices.items() if value is not None}
        now = self._now()
        if "last" in changes:
            changes["last_at"] = now  # a quote's time alone never freshens an old last
        self.put(replace(previous, at=now, **changes))

    def put_delta(self, key: str, delta: Decimal) -> None:
        """An option's delta from the stream's Greeks, with its own time: it never
        touches the option's quote (bots slice 7)."""
        with self._lock:
            self._deltas[key] = (delta, self._now())

    def delta(self, key: str) -> tuple[Decimal, datetime] | None:
        """(the option's latest delta, when it arrived), or None."""
        with self._lock:
            return self._deltas.get(key)

    def latest(self, key: str) -> Quote | None:
        tasty_up = any(s.name == "tastytrade" and s.up for s in self.streams)
        return pick_quote(self.get("tastytrade", key), self.get("schwab", key), tasty_up=tasty_up)

    def status(self) -> dict[str, str]:
        return {
            s.name: "streaming" if s.up else getattr(s, "state", "not connected")
            for s in self.streams
        }


def marks(hub, keys: Iterable[str], now: datetime | None = None, *, strict: bool = False):
    """The journal's marks for `keys` (#241): each quote's mark, but outside the
    regular session an option whose market is pulled is marked at its last real
    one (core.quotes.closing_mark). Keys with no mark are left out."""
    now = now or _utcnow()
    found = {}
    for key in keys:
        quote = hub.latest(key)
        saved = hub.good_mark(key) if no_market(quote) else None
        mark = closing_mark(quote, saved, now, strict=strict)
        if mark is not None:
            found[key] = mark
    return found


class _Stream:
    """What both streams share: the symbols wanted, the background thread and
    reconnecting."""

    name = ""

    def __init__(self, hub: QuoteHub):
        self.hub = hub
        self.up = False
        self.state = "not started"
        self._wanted: set[str] = set()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def subscribe(self, keys: Iterable[str]) -> None:
        with self._lock:
            new = set(keys) - self._wanted
            self._wanted |= new
            self._pending |= new

    def _take_pending(self) -> set[str]:
        with self._lock:
            pending, self._pending = self._pending, set()
            return pending

    def _resubscribe_all(self) -> None:
        with self._lock:
            self._pending = set(self._wanted)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._loop, name=f"{self.name}-stream", daemon=True
            )
        self._thread.start()

    def _loop(self) -> None:
        failures = 0
        told_not_connected = False  # say it once, then keep checking quietly
        while True:
            try:
                self.state = "connecting"
                self._resubscribe_all()
                self._connect_and_run()
                failures = 0
                self.state = "reconnecting"
            except CredentialsUnavailable as e:
                self.up, self.state = False, "not connected"
                if not told_not_connected:
                    logger.info("%s stream: %s", self.name, e)
                    told_not_connected = True
                time.sleep(NOT_CONNECTED_PAUSE)
                continue
            except Exception as e:  # noqa: BLE001 -- reconnect after anything
                self.up, self.state = False, f"reconnecting ({e})"
                logger.warning("%s stream dropped: %s", self.name, e)
            told_not_connected = False
            self.up = False
            time.sleep(RETRY_PAUSES[min(failures, len(RETRY_PAUSES) - 1)])
            failures += 1

    def _recv(self, socket) -> dict | None:
        """The next message, None if none came in time. Raises StreamClosed
        when the socket has closed."""
        try:
            text = socket.recv(timeout=RECV_WAIT)
        except TimeoutError:
            return None
        except _closed_errors() as e:
            raise _Ended from e
        return json.loads(text)


class _Ended(Exception):
    """The connection closed: the session is over, not an error."""


class TastyStream(_Stream):
    name = "tastytrade"
    CHANNEL = 1
    FIELDS = {
        "Quote": ["eventType", "eventSymbol", "bidPrice", "askPrice"],
        "Trade": ["eventType", "eventSymbol", "price"],
        # Options' deltas, for the bots' watch rules (Tracker2's MarketStream)
        "Greeks": ["eventType", "eventSymbol", "delta"],
    }

    def __init__(self, broker, hub: QuoteHub):
        super().__init__(hub)
        self.broker = broker
        self._keys_by_symbol: dict[str, str] = {}
        self._futures_symbols: dict[str, dict[str, str]] = {}

    def _connect_and_run(self) -> None:
        token, url = self.broker.quote_token()
        with _connect(url) as socket:
            self.run_session(socket, token)

    def run_session(self, socket, token: str) -> None:
        """One connection, until it closes."""
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
        last_sent, channel_open = time.monotonic(), False
        try:
            while True:
                message = self._recv(socket)
                if message is not None:
                    kind = message.get("type")
                    if kind == "AUTH_STATE" and message.get("state") == "UNAUTHORIZED":
                        send({"type": "AUTH", "channel": 0, "token": token})
                    elif kind == "AUTH_STATE" and message.get("state") == "AUTHORIZED":
                        send(
                            {
                                "type": "CHANNEL_REQUEST",
                                "channel": self.CHANNEL,
                                "service": "FEED",
                                "parameters": {"contract": "AUTO"},
                            }
                        )
                    elif kind == "CHANNEL_OPENED" and message.get("channel") == self.CHANNEL:
                        send(
                            {
                                "type": "FEED_SETUP",
                                "channel": self.CHANNEL,
                                "acceptAggregationPeriod": 1,
                                "acceptDataFormat": "COMPACT",
                                "acceptEventFields": self.FIELDS,
                            }
                        )
                        channel_open = True
                        self.up, self.state = True, "streaming"
                    elif kind == "FEED_DATA":
                        self._read(message.get("data") or [])
                    elif kind == "ERROR":
                        raise StreamClosed(f"TastyTrade's stream refused: {message.get('message')}")
                if channel_open:
                    pending = self._take_pending()
                    if pending:
                        send(self._subscription(pending))
                        last_sent = time.monotonic()
                if time.monotonic() - last_sent > KEEPALIVE_EVERY:
                    send({"type": "KEEPALIVE", "channel": 0})
                    last_sent = time.monotonic()
        except _Ended:
            self.up = False

    def _stream_symbol(self, key: str) -> str | None:
        """DXLink's name for a key. Futures options need TastyTrade's own
        streamer symbol from the product's option chain (fetched once)."""
        if not is_futures_option(key):
            return dxlink_symbol(key)
        root = futures_root(key)
        if root is None or not hasattr(self.broker, "futures_option_streamer_symbols"):
            return None
        if root not in self._futures_symbols:
            try:
                self._futures_symbols[root] = self.broker.futures_option_streamer_symbols(root)
            except Exception as e:  # noqa: BLE001 -- one product's chain must not stop the stream
                logger.warning("no %s futures option chain: %s", root, e)
                self._futures_symbols[root] = {}
        return self._futures_symbols[root].get(key)

    def _subscription(self, keys: set[str]) -> dict:
        add = []
        for key in sorted(keys):
            symbol = self._stream_symbol(key)
            if symbol is None:
                continue
            self._keys_by_symbol[symbol] = key
            add += [{"type": "Quote", "symbol": symbol}, {"type": "Trade", "symbol": symbol}]
            if parse_occ(key) is not None:
                add.append({"type": "Greeks", "symbol": symbol})
        return {"type": "FEED_SUBSCRIPTION", "channel": self.CHANNEL, "add": add}

    def _read(self, data: list) -> None:
        for event in parse_dxlink_feed(data, self.FIELDS):
            key = self._keys_by_symbol.get(event.get("eventSymbol"))
            if key is None:
                continue
            if event.get("eventType") == "Greeks":
                delta = _decimal(event.get("delta"))
                if delta is not None:
                    self.hub.put_delta(key, delta)
                continue
            self.hub.update(
                self.name,
                key,
                bid=_decimal(event.get("bidPrice")),
                ask=_decimal(event.get("askPrice")),
                last=_decimal(event.get("price")),
            )


class SchwabStream(_Stream):
    name = "schwab"
    FIELDS = {"LEVELONE_EQUITIES": "0,1,2,3", "LEVELONE_OPTIONS": "0,2,3,4"}

    def __init__(self, broker, hub: QuoteHub, token: Callable[[], str]):
        super().__init__(hub)
        self.broker = broker
        self._token = token
        self._keys_by_symbol: dict[str, str] = {}
        self._request_id = 0
        self._subscribed_services: set[str] = set()
        # #172: told of account activity (fills) while the stream is open; set by fill_sync
        self.on_account_activity: Callable[[str], None] | None = None

    def login_info(self) -> dict:
        return self.broker.streamer_info()

    def _connect_and_run(self) -> None:
        if guard.is_installed():  # gateway mode: the login is the server's
            raise StreamClosed("Schwab's stream is never opened in gateway mode")
        info = self.login_info()
        with _connect(info["streamerSocketUrl"]) as socket:
            self.run_session(socket, info)

    def _request(self, info: dict, service: str, command: str, parameters: dict) -> dict:
        self._request_id += 1
        return {
            "service": service,
            "requestid": str(self._request_id),
            "command": command,
            "SchwabClientCustomerId": info.get("schwabClientCustomerId"),
            "SchwabClientCorrelId": info.get("schwabClientCorrelId"),
            "parameters": parameters,
        }

    def run_session(self, socket, info: dict) -> None:
        send = lambda requests: socket.send(json.dumps({"requests": requests}))  # noqa: E731
        self._subscribed_services = set()
        send(
            [
                self._request(
                    info,
                    "ADMIN",
                    "LOGIN",
                    {
                        "Authorization": self._token(),
                        "SchwabClientChannel": info.get("schwabClientChannel"),
                        "SchwabClientFunctionId": info.get("schwabClientFunctionId"),
                    },
                )
            ]
        )
        logged_in = False
        try:
            while True:
                message = self._recv(socket)
                if message is not None:
                    for response in message.get("response", []):
                        if response.get("command") == "LOGIN":
                            content = response.get("content", {})
                            if content.get("code") != 0:
                                raise StreamClosed(
                                    f"Schwab's stream refused the login: {content.get('msg')}"
                                )
                            logged_in = True
                            self.up, self.state = True, "streaming"
                            if self.on_account_activity is not None:
                                send([self._account_activity(info)])
                    for item in message.get("data", []):
                        self._read(item)
                if logged_in:
                    pending = self._take_pending()
                    if pending:
                        send(self._subscriptions(info, pending))
        except _Ended:
            self.up = False

    def _subscriptions(self, info: dict, keys: set[str]) -> list[dict]:
        by_service: dict[str, list[str]] = {}
        for key in sorted(keys):
            found = schwab_service_and_symbol(key)
            if found is None:
                continue
            service, symbol = found
            self._keys_by_symbol[symbol] = key
            by_service.setdefault(service, []).append(symbol)
        requests = []
        for service, symbols in by_service.items():
            command = "ADD" if service in self._subscribed_services else "SUBS"
            self._subscribed_services.add(service)
            requests.append(
                self._request(
                    info,
                    service,
                    command,
                    {"keys": ",".join(symbols), "fields": self.FIELDS[service]},
                )
            )
        return requests

    def _account_activity(self, info: dict) -> dict:
        return self._request(
            info, "ACCT_ACTIVITY", "SUBS", {"keys": "Account Activity", "fields": "0,1,2,3"}
        )

    def _read(self, item: dict) -> None:
        service = item.get("service")
        if service == "ACCT_ACTIVITY":
            # Any account message but the subscription's own: a sync is cheap, a missed fill isn't.
            kinds = [str(c.get("2", "")) for c in item.get("content", [])]
            if self.on_account_activity is not None and any(k != "SUBSCRIBED" for k in kinds):
                self.on_account_activity("schwab")
            return
        if service not in self.FIELDS:
            return
        for content in item.get("content", []):
            key = self._keys_by_symbol.get(content.get("key"))
            if key is None:
                continue
            previous = self.hub.get(self.name, key)
            self.hub.put(update_from_schwab(previous, key, service, content, self.hub.now()))


def make_hub(clients: list, credentials, *, gateway: bool) -> QuoteHub:
    """A hub with a stream for each broker that has one. Nothing connects
    until something subscribes.

    In gateway mode there is no Schwab stream: Schwab allows one streamer
    session per login, and the login is the Tracker2 server's, so ours would
    knock its stream off. Quotes come from TastyTrade's DXLink, which allows
    several sessions."""
    hub = QuoteHub([])
    for client in clients:
        if hasattr(client, "quote_token"):
            hub.streams.append(TastyStream(client, hub))
        elif hasattr(client, "streamer_info") and not gateway:
            hub.streams.append(
                SchwabStream(client, hub, token=lambda: credentials.schwab_token().value)
            )
    hub.streams.sort(key=lambda s: s.name != "tastytrade")  # TastyTrade first
    return hub
