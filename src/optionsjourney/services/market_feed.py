"""A short, one-off read of TastyTrade's market data stream (DXLink) for the
bots' market filters (Tracker2's vix._read_dxlink, _read_dxlink_trades and
technicals._read_dxlink): the latest Trade of some symbols (price and time),
their Summary (the prior close and its day), and Candle histories. It reads
until the caller says it has what it needs, or until its deadline, then
closes. The live quote stream the exits use is never touched.

When TastyTrade can't give an answer, SchwabFeed reads the same things from
Schwab's REST market data in one pass, and FallbackFeed asks TastyTrade first,
then Schwab (#194)."""

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_time
from decimal import Decimal

from optionsjourney.bots.market_filters import previous_session
from optionsjourney.brokers.credentials.base import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import INDEXES
from optionsjourney.core.candles import Candle
from optionsjourney.core.market import MARKET_TZ, market_date
from optionsjourney.core.quotes import parse_dxlink_feed

logger = logging.getLogger(__name__)

CHANNEL = 3
REMOVE, SNAPSHOT_END, SNAPSHOT_SNIP = 0x02, 0x08, 0x10
FIELDS = {
    "Trade": ["eventType", "eventSymbol", "price", "time"],
    "Summary": ["eventType", "eventSymbol", "dayId", "prevDayClosePrice"],
    "Candle": ["eventType", "eventSymbol", "eventFlags", "time", "open", "high", "low", "close",
               "volume"],
}  # fmt: skip
_CANDLE = re.compile(r"^([^{]+)\{=(\d*)([a-zA-Z]+)")


class FeedUnavailable(Exception):
    """There's no market data to read: the broker isn't connected, or can't give it."""


def candle_key(symbol: str) -> tuple[str, str] | None:
    """("SPX", "1d") for "SPX{=1d}", "SPX{=d}" or "SPX{=d,tho=true}": DXLink echoes a
    candle symbol back in its own normal form (technicals._is_daily)."""
    found = _CANDLE.match(symbol or "")
    if not found:
        return None
    base, count, unit = found.groups()
    return base, f"{count or '1'}{unit.lower()}"


@dataclass(frozen=True)
class Want:
    trades: tuple[str, ...] = ()
    summaries: tuple[str, ...] = ()
    candles: tuple[tuple[str, int], ...] = ()  # (candle symbol like "SPX{=1d}", from ms)


@dataclass
class Got:
    trades: dict[str, tuple[Decimal, int]] = field(default_factory=dict)  # (price, ms)
    summaries: dict[str, tuple[int, Decimal | None]] = field(default_factory=dict)  # (dayId, prev)
    _bars: dict[tuple[str, str], dict[int, Candle]] = field(default_factory=dict)
    _complete: set[tuple[str, str]] = field(default_factory=set)
    source: str = "TastyTrade"  # the broker the prices came from, for the filters' reasons

    def bars(self, symbol: str) -> list[Candle]:
        """The bars read for a candle symbol, oldest first."""
        found = self._bars.get(candle_key(symbol), {})
        return [found[t] for t in sorted(found)]

    def is_complete(self, symbol: str) -> bool:
        """Whether that candle history's snapshot ended (all of it arrived)."""
        return candle_key(symbol) in self._complete

    def add_event(self, event: dict) -> None:
        kind, symbol = event.get("eventType"), event.get("eventSymbol")
        if kind == "Trade":
            price, at = event.get("price"), event.get("time")
            if price is None or Decimal(str(price)) <= 0 or not at:
                return
            seen = self.trades.get(symbol)
            if seen is None or int(at) >= seen[1]:
                self.trades[symbol] = (Decimal(str(price)), int(at))
        elif kind == "Summary":
            close = event.get("prevDayClosePrice")
            day = event.get("dayId")
            if day is not None:
                self.summaries[symbol] = (int(day), None if close is None else Decimal(str(close)))
        elif kind == "Candle":
            self._add_candle(event)

    def _add_candle(self, event: dict) -> None:
        key = candle_key(event.get("eventSymbol"))
        if key is None:
            return
        flags = int(event.get("eventFlags") or 0)
        prices = [event.get(k) for k in ("open", "high", "low", "close")]
        # vix.is_bar: a removal, or a price-less or zero open, is never a bar (DXLink
        # ends a snapshot with such a marker).
        if not flags & REMOVE and None not in prices and Decimal(str(prices[0])) > 0:
            at = int(event["time"])
            volume = event.get("volume")
            self._bars.setdefault(key, {})[at] = Candle(
                datetime.fromtimestamp(at / 1000, UTC),
                *(Decimal(str(p)) for p in prices),
                Decimal(str(volume)) if volume is not None else Decimal(0),
            )
        if flags & (SNAPSHOT_END | SNAPSHOT_SNIP):
            # A snip means DXLink cut the oldest bars; what arrived is all there is,
            # and a filter short of history fails closed ("not enough history").
            self._complete.add(key)


def read_dxlink(socket, token: str, want: Want, wait_s: float, ready: Callable[[Got], bool]) -> Got:
    """One DXLink session: subscribe to `want`, collect events until `ready(got)`
    or `wait_s` seconds have passed, and return what arrived. A refusal raises."""
    send = lambda message: socket.send(json.dumps(message))  # noqa: E731
    send({"type": "SETUP", "channel": 0, "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60,
          "version": "0.1-optionsjourney"})  # fmt: skip
    got = Got()
    deadline = time.monotonic() + wait_s
    while (left := deadline - time.monotonic()) > 0:
        try:
            message = json.loads(socket.recv(timeout=min(left, 0.5)))
        except TimeoutError:
            continue
        kind = message.get("type")
        if kind == "AUTH_STATE" and message.get("state") == "UNAUTHORIZED":
            send({"type": "AUTH", "channel": 0, "token": token})
        elif kind == "AUTH_STATE" and message.get("state") == "AUTHORIZED":
            send({"type": "CHANNEL_REQUEST", "channel": CHANNEL, "service": "FEED",
                  "parameters": {"contract": "AUTO"}})  # fmt: skip
        elif kind == "CHANNEL_OPENED" and message.get("channel") == CHANNEL:
            send({"type": "FEED_SETUP", "channel": CHANNEL, "acceptAggregationPeriod": 1,
                  "acceptDataFormat": "COMPACT", "acceptEventFields": FIELDS})  # fmt: skip
            add = [{"type": "Trade", "symbol": s} for s in want.trades]
            add += [{"type": "Summary", "symbol": s} for s in want.summaries]
            add += [{"type": "Candle", "symbol": s, "fromTime": t} for s, t in want.candles]
            send({"type": "FEED_SUBSCRIPTION", "channel": CHANNEL, "add": add})
        elif kind == "FEED_DATA":
            for event in parse_dxlink_feed(message.get("data") or [], FIELDS):
                got.add_event(event)
            if ready(got):
                return got
        elif kind == "ERROR":
            raise BrokerError(f"TastyTrade's market data refused: {message.get('message')}")
    return got


class TastyFeed:
    """Market data from TastyTrade's DXLink, one short connection per read."""

    def __init__(self, clients: Callable[[], list]):
        self._clients = clients

    def read(self, want: Want, wait_s: float, ready: Callable[[Got], bool]) -> Got:
        client = next((c for c in self._clients() if hasattr(c, "quote_token")), None)
        if client is None:
            raise FeedUnavailable("TastyTrade isn't connected, so there are no prices to read")
        from optionsjourney.services.streaming import _connect  # noqa: PLC0415

        token, url = client.quote_token()
        with _connect(url) as socket:
            return read_dxlink(socket, token, want, wait_s, ready)


# --- Schwab, when TastyTrade can't answer (#194) ---------------------------------------

_EPOCH = date(1970, 1, 1)
SUMMARY_LOOKBACK = timedelta(days=10)  # reaches the previous session past a long weekend
DAILY = {"periodType": "year", "frequencyType": "daily", "frequency": 1}
MINUTE = {"periodType": "day", "frequencyType": "minute", "frequency": 1}
MINUTE_HEADROOM_MS = 120_000  # a minute history is asked for from 2 minutes early


def _utcnow() -> datetime:
    return datetime.now(UTC)


def schwab_symbol(dx: str) -> str:
    """Schwab's name for a DXLink symbol: indexes are $-prefixed ("$VIX", "$VIX9D")."""
    return "$" + dx if dx in INDEXES | {"VIX9D"} else dx


def candle_from_schwab(row: dict, daily: bool) -> Candle:
    """One of Schwab's price history candles ({datetime ms, open, high, low, close,
    volume}). Schwab stamps a daily bar at New York midnight; here it's moved to
    midnight UTC of that New York day, DXLink's convention
    (market_filters.prior_daily_closes)."""
    at = datetime.fromtimestamp(row["datetime"] / 1000, UTC)
    if daily:
        at = datetime.combine(market_date(at), day_time(0), UTC)
    volume = row.get("volume")
    return Candle(
        at,
        *(Decimal(str(row[k])) for k in ("open", "high", "low", "close")),
        Decimal(str(volume)) if volume is not None else Decimal(0),
    )


class SchwabFeed:
    """The same reads from Schwab's REST market data: one pass, never waiting.
    Its answers carry source="Schwab"; anything it can't give is left out, so a
    filter short of it fails closed. A Schwab call that fails raises."""

    def __init__(self, clients: Callable[[], list], now: Callable[[], datetime] = _utcnow):
        self._clients, self._now = clients, now

    def read(self, want: Want, wait_s: float, ready: Callable[[Got], bool]) -> Got:
        client = next((c for c in self._clients() if hasattr(c, "price_history")), None)
        if client is None:
            raise FeedUnavailable("Schwab isn't connected, so there are no prices to read")
        bases = [
            *want.trades,
            *want.summaries,
            *((candle_key(s) or (s,))[0] for s, _ in want.candles),
        ]
        futures = sorted({s for s in bases if s.startswith("/")})
        if futures:
            raise FeedUnavailable(f"Schwab's market data isn't used for {', '.join(futures)}")
        now = self._now()
        now_ms = int(now.timestamp() * 1000)
        today = now.astimezone(MARKET_TZ).date()
        got = Got(source="Schwab")
        if want.trades:
            quotes = client.quotes([schwab_symbol(s) for s in want.trades]) or {}
            for symbol in want.trades:
                quote = (quotes.get(schwab_symbol(symbol)) or {}).get("quote") or {}
                got.add_event(
                    {
                        "eventType": "Trade",
                        "eventSymbol": symbol,
                        "price": quote.get("lastPrice"),
                        "time": quote.get("tradeTime"),
                    }
                )
        for symbol in want.summaries:
            closes = client.daily_closes(schwab_symbol(symbol), today - SUMMARY_LOOKBACK, today)
            before = [c for c in closes if c.day < today and c.close and c.close > 0]
            last = max(before, key=lambda c: c.day, default=None)
            # Only the previous session's close is today's prior close; an older one
            # would be the close from two sessions back.
            if last is not None and last.day == previous_session(today):
                got.add_event(
                    {
                        "eventType": "Summary",
                        "eventSymbol": symbol,
                        "dayId": (today - _EPOCH).days,
                        "prevDayClosePrice": last.close,
                    }
                )
        for symbol, from_ms in want.candles:
            key = candle_key(symbol)
            if key is None or key[1] not in ("1d", "1m"):
                continue  # nothing Schwab can give: left incomplete, the filter fails closed
            daily = key[1] == "1d"
            # Two minutes early, so a start Schwab takes as exclusive can't cost the
            # 09:30 bar; bars before 09:30 are ignored (first_rth_open,
            # completed_rth_closes). A 09:30 bar missing anyway fails the filter closed.
            start = from_ms if daily else from_ms - MINUTE_HEADROOM_MS
            params = {**(DAILY if daily else MINUTE), "startDate": start, "endDate": now_ms}
            if not daily:
                params["needExtendedHoursData"] = "false"
            for row in client.price_history(schwab_symbol(key[0]), params):
                if row.get("datetime") is None or None in (
                    row.get(k) for k in ("open", "high", "low", "close")
                ):
                    continue
                bar = candle_from_schwab(row, daily)
                got.add_event({"eventType": "Candle", "eventSymbol": symbol, "eventFlags": 0,
                               "time": int(bar.time.timestamp() * 1000), "open": bar.open,
                               "high": bar.high, "low": bar.low, "close": bar.close,
                               "volume": bar.volume})  # fmt: skip
            # The whole history came in one answer.
            got.add_event({"eventType": "Candle", "eventSymbol": symbol,
                           "eventFlags": SNAPSHOT_END | REMOVE, "time": 0})  # fmt: skip
        return got


class FallbackFeed:
    """Each feed in turn until one's answer is ready: TastyTrade, then Schwab. A feed
    that raises is logged and skipped. If none is ready, the last answer read is
    returned (the filter then fails closed on it); if every feed raised, that's
    FeedUnavailable with each one's reason."""

    def __init__(self, feeds: list):
        self.feeds = list(feeds)

    def read(self, want: Want, wait_s: float, ready: Callable[[Got], bool]) -> Got:
        last, reasons = None, []
        for feed in self.feeds:
            try:
                got = feed.read(want, wait_s, ready)
            except (FeedUnavailable, BrokerError, CredentialsUnavailable) as e:
                logger.info("market data from %s unavailable: %s", type(feed).__name__, e)
                reasons.append(str(e))
                continue
            except Exception as e:  # noqa: BLE001 -- one feed breaking never stops the next
                logger.exception("market data from %s broke", type(feed).__name__)
                reasons.append(str(e))
                continue
            if ready(got):
                return got
            last = got
        if last is None:
            raise FeedUnavailable("; ".join(reasons))
        return last
