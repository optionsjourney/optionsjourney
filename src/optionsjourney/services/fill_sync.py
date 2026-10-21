"""Fills start a sync (#172), as Tracker2's account_stream.py does: a broker's account stream
reports a fill and the journal syncs within seconds. A trigger only: nothing here writes a
trade; the sync reads the broker's transaction history as Sync now does. The timed sync
(auto_sync, #171) stays as the backstop for anything a stream misses.

- TastyAccountStream: TastyTrade's account streamer (wss://streamer.tastyworks.com), listen
  only, in both modes. It has no one-session limit: Tracker2 runs its own on the same login.
- Schwab: ACCT_ACTIVITY on the Schwab price stream (services/streaming.py), local mode only.

Runs in the app process (__main__), never in tests."""

import json
import logging
import threading
import time
from collections.abc import Callable

from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError

logger = logging.getLogger(__name__)

SETTLE = 3.0  # seconds after the last fill notice: fills come in bursts, one sync for them
GAP = 10.0  # seconds between fill syncs at the least


class FillSync:
    """Turns fill notices into syncs: one sync SETTLE seconds after the last notice, and at
    most one every GAP seconds. `enabled` is the setting, read at each notice."""

    def __init__(
        self,
        run_sync: Callable[[], object],
        enabled: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._run_sync = run_sync
        self._enabled = enabled
        self._clock = clock
        self._lock = threading.Lock()
        self._noticed: float | None = None  # the last notice not yet synced
        self._last_run: float | None = None

    def notify(self, broker: str) -> None:
        if not self._enabled():
            return
        logger.info("fill reported by %s: syncing shortly", broker)
        with self._lock:
            self._noticed = self._clock()

    def step(self) -> bool:
        """Sync if a notice is waiting and settled; whether it did."""
        now = self._clock()
        with self._lock:
            if self._noticed is None or now - self._noticed < SETTLE:
                return False
            if self._last_run is not None and now - self._last_run < GAP:
                return False
            self._noticed, self._last_run = None, now
        self._run_sync()
        return True

    def start(self) -> threading.Thread:
        def loop():
            while True:
                try:
                    self.step()
                except Exception:  # noqa: BLE001 -- never let the loop die
                    logger.exception("fill sync crashed")
                time.sleep(0.5)

        thread = threading.Thread(target=loop, name="fill-sync", daemon=True)
        thread.start()
        return thread


def is_fill(message: dict) -> bool:
    """Whether a TastyTrade account streamer message reports a fill: an order (or complex
    order) that is filled, or still live with fills on a leg (a partial fill)."""
    if message.get("type") not in ("Order", "ComplexOrder"):
        return False
    data = message.get("data") or {}
    orders = [data, *(data.get("orders") or [])]
    for order in orders:
        if str(order.get("status", "")).lower() == "filled":
            return True
        if any(leg.get("fills") for leg in order.get("legs") or []):
            return True
    return False


class TastyAccountStream:
    """TastyTrade's account streamer, listen only. Reconnects after a drop (5 s, doubling to a
    minute) and starts afresh every REBUILD seconds: a quiet stream and a dead one look alike."""

    URL = "wss://streamer.tastyworks.com"
    TEST_URL = "wss://streamer.cert.tastyworks.com"
    HEARTBEAT = 20  # seconds
    REBUILD = 30 * 60  # seconds

    def __init__(self, client, credentials, on_fill: Callable[[str], None]):
        self.client = client
        self.credentials = credentials
        self.on_fill = on_fill
        self.state = "off"

    def run_once(self, connect) -> None:
        """One session: connect, subscribe every account, heartbeat, report fills, until
        REBUILD seconds pass or the socket drops (raises)."""
        token = self.credentials.tastytrade_token()
        accounts = [a.number for a in self.client.accounts()]
        if not accounts:
            raise BrokerError("No TastyTrade accounts to listen to.")
        url = self.TEST_URL if token.is_test else self.URL
        with connect(url) as socket:
            request_id = 0

            def send(action, value=None):
                nonlocal request_id
                request_id += 1
                message = {
                    "auth-token": f"Bearer {token.value}",
                    "action": action,
                    "request-id": request_id,
                }
                if value is not None:
                    message["value"] = value
                socket.send(json.dumps(message))

            send("connect", accounts)
            started = beat = time.monotonic()
            self.state = "listening"
            while time.monotonic() - started < self.REBUILD:
                if time.monotonic() - beat >= self.HEARTBEAT:
                    send("heartbeat")
                    beat = time.monotonic()
                try:
                    raw = socket.recv(timeout=5)
                except TimeoutError:
                    continue
                message = json.loads(raw)
                if message.get("action") == "connect" and message.get("status") != "ok":
                    raise BrokerError(f"TastyTrade's account stream refused: {message}")
                if is_fill(message):
                    self.on_fill("tastytrade")

    def start(self) -> threading.Thread:
        from optionsjourney.services.streaming import _connect  # noqa: PLC0415

        def loop():
            wait = 5
            while True:
                try:
                    self.run_once(_connect)
                    wait = 5
                except (BrokerError, CredentialsUnavailable) as e:
                    self.state = "off"
                    logger.info("TastyTrade account stream: %s", e)
                    wait = 60
                except Exception as e:  # noqa: BLE001 -- a drop: reconnect after a wait
                    self.state = "reconnecting"
                    logger.info("TastyTrade account stream dropped: %s", e)
                    wait = min(wait * 2, 60)
                time.sleep(wait)

        thread = threading.Thread(target=loop, name="tasty-account-stream", daemon=True)
        thread.start()
        return thread


def start(services) -> FillSync:
    """The fill sync and TastyTrade's account stream; the Schwab stream reports its account
    activity to the same FillSync (services.fill_sync, set here)."""
    from optionsjourney.services import settings  # noqa: PLC0415
    from optionsjourney.services.sync import sync_all  # noqa: PLC0415

    fills = FillSync(
        lambda: sync_all(services.broker_clients(), services.session_factory),
        enabled=lambda: settings.get(services.session_factory, "sync_on_fill") == "on",
    )
    fills.start()
    services.fill_sync = fills
    for stream in getattr(services.quote_hub, "streams", []):
        if getattr(stream, "name", None) == "schwab":
            stream.on_account_activity = fills.notify
    for client in services.broker_clients():
        if getattr(client, "broker", None) == "tastytrade":
            TastyAccountStream(client, services.credentials, fills.notify).start()
    return fills
