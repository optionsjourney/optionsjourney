"""A one-off DXLink candle history (#25): the charts' price history when
Schwab isn't connected (services/chart_market.py)."""

import json
import logging
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.candles import (
    Candle,
    dxlink_candle_symbol,
)

logger = logging.getLogger(__name__)

# How far back each interval reaches (Tracker2's choices)
HISTORY = {
    "1m": timedelta(days=4),
    "5m": timedelta(days=10),
    "15m": timedelta(days=14),
    "30m": timedelta(days=21),
    "1h": timedelta(days=45),
    "1d": timedelta(days=760),
}
FRESH_FOR = {"1d": 300}  # seconds a fetch is reused; intraday below
INTRADAY_FRESH_FOR = 10
SNAPSHOT_BEGIN, SNAPSHOT_END, SNAPSHOT_SNIP, REMOVE = 0x04, 0x08, 0x10, 0x02
CHANNEL = 3
FIELDS = [
    "eventType",
    "eventSymbol",
    "eventFlags",
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


def _number(value) -> Decimal | None:
    if value is None or value == "NaN":
        return None
    number = Decimal(str(value))
    return number if number.is_finite() else None


def read_dxlink_snapshot(
    socket, token: str, symbol: str, from_ms: int, deadline_s: float = 10
) -> list[Candle]:
    """One DXLink candle snapshot for `symbol` ("SPY{=5m}") from `from_ms`,
    oldest first. DXLink sends newest first and flags the last event of the
    snapshot with SNAPSHOT_END (or SNAPSHOT_SNIP when it cut the history)."""
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
    bars: dict[int, Candle] = {}
    finished_by = time.monotonic() + deadline_s
    while time.monotonic() < finished_by:
        try:
            message = json.loads(socket.recv(timeout=1))
        except TimeoutError:
            continue
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
                    "acceptAggregationPeriod": 10,
                    "acceptDataFormat": "COMPACT",
                    "acceptEventFields": {"Candle": FIELDS},
                }
            )
            send(
                {
                    "type": "FEED_SUBSCRIPTION",
                    "channel": CHANNEL,
                    "add": [{"type": "Candle", "symbol": symbol, "fromTime": from_ms}],
                }
            )
        elif kind == "FEED_DATA":
            values = message["data"][1] if len(message.get("data", [])) > 1 else []
            for start in range(0, len(values) - len(FIELDS) + 1, len(FIELDS)):
                event = dict(zip(FIELDS, values[start : start + len(FIELDS)], strict=True))
                flags = int(event.get("eventFlags") or 0)
                prices = [_number(event[k]) for k in ("open", "high", "low", "close")]
                if not flags & REMOVE and None not in prices:
                    at = datetime.fromtimestamp(int(event["time"]) / 1000, UTC)
                    bars[int(event["time"])] = Candle(
                        at, *prices, _number(event["volume"]) or Decimal("0")
                    )
                if flags & (SNAPSHOT_END | SNAPSHOT_SNIP):
                    return [bars[t] for t in sorted(bars)]
        elif kind == "ERROR":
            raise BrokerError(f"TastyTrade's candle stream refused: {message.get('message')}")
    return [bars[t] for t in sorted(bars)]


def tastytrade_candles(broker, symbol: str, interval: str, now: datetime) -> list[Candle]:
    from optionsjourney.services.streaming import _connect  # noqa: PLC0415

    token, url = broker.quote_token()
    from_ms = int((now - HISTORY[interval]).timestamp() * 1000)
    with _connect(url) as socket:
        return read_dxlink_snapshot(socket, token, dxlink_candle_symbol(symbol, interval), from_ms)
