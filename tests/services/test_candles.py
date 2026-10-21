import json
from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.services.candles import read_dxlink_snapshot

T = 1791552600000  # 2026-10-09 13:30 UTC (09:30 New York), in ms


class FakeSocket:
    def __init__(self, script):
        self.script, self.sent = list(script), []

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self, timeout=None):
        if not self.script:
            raise TimeoutError
        return json.dumps(self.script.pop(0))


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


def test_dxlink_snapshot_is_read_until_its_end_flag():
    socket = FakeSocket(
        [
            {"type": "AUTH_STATE", "state": "UNAUTHORIZED"},
            {"type": "AUTH_STATE", "state": "AUTHORIZED"},
            {"type": "CHANNEL_OPENED", "channel": 3},
            {
                "type": "FEED_DATA",
                "channel": 3,
                "data": [
                    "Candle",
                    [
                        "Candle",
                        "SPY{=5m}",
                        4,
                        T + 300000,
                        2,
                        3,
                        1,
                        2.5,
                        50,  # newest first, snapshot begins
                        "Candle",
                        "SPY{=5m}",
                        0,
                        T,
                        1,
                        2,
                        0.5,
                        1.5,
                        100,
                        "Candle",
                        "SPY{=5m}",
                        8,
                        T - 300000,
                        "NaN",
                        "NaN",
                        "NaN",
                        "NaN",
                        "NaN",  # end; no prices
                    ],
                ],
            },
            {
                "type": "FEED_DATA",
                "channel": 3,
                "data": ["Candle", ["Candle", "SPY{=5m}", 0, T + 600000, 9, 9, 9, 9, 9]],
            },
        ]
    )
    candles = read_dxlink_snapshot(socket, "token", "SPY{=5m}", T - 3600000, deadline_s=5)
    assert [(c.time, c.close) for c in candles] == [
        (datetime(2026, 10, 9, 13, 30, tzinfo=UTC), Decimal("1.5")),
        (datetime(2026, 10, 9, 13, 35, tzinfo=UTC), Decimal("2.5")),
    ]  # oldest first; nothing after the end flag
    sub = next(m for m in socket.sent if m["type"] == "FEED_SUBSCRIPTION")
    assert sub["add"] == [{"type": "Candle", "symbol": "SPY{=5m}", "fromTime": T - 3600000}]


class FakeSchwab:
    def __init__(self):
        self.asked = []

    def price_history(self, symbol, params):
        self.asked.append((symbol, params))
        return [
            {"datetime": T, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
            {"datetime": T + 1800000, "open": 1.5, "high": 3, "low": 1, "close": 2, "volume": 50},
        ]
