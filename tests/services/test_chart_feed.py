"""The chart grid's live candle feed over DXLink (#25)."""

import json
from datetime import UTC, datetime

import pytest

from optionsjourney.services.chart_feed import ChartFeed, _Ended, room
from optionsjourney.services.streaming import StreamClosed

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)
DAY = int(datetime(2026, 10, 9, tzinfo=UTC).timestamp() * 1000)
YESTERDAY = int(datetime(2026, 10, 8, tzinfo=UTC).timestamp() * 1000)


class FakeSocket:
    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self, timeout=None):
        if not self.script:
            raise StreamClosed("end of script")
        return json.dumps(self.script.pop(0))


def feed_with_emits():
    emitted = []
    feed = ChartFeed(lambda: None, lambda *a, **k: emitted.append((a, k)), autostart=False)
    return feed, emitted


def test_rejects_symbols_and_intervals_it_cannot_chart():
    feed, _ = feed_with_emits()
    assert not feed.add("s1", "/ES", "5m")  # a root has no candles: its contract does
    assert not feed.add("s1", "SPY", "2m")
    assert feed.add("s1", "spy", "1d")
    assert feed.wanted() == {("SPY", "1d")}


def test_session_subscribes_what_viewers_want_and_pushes_snapshot_then_updates():
    feed, emitted = feed_with_emits()
    feed.add("s1", "SPY", "1d")
    socket = FakeSocket(
        [
            {"type": "AUTH_STATE", "channel": 0, "state": "UNAUTHORIZED"},
            {"type": "AUTH_STATE", "channel": 0, "state": "AUTHORIZED"},
            {"type": "CHANNEL_OPENED", "channel": 5},
            {
                "type": "FEED_DATA",
                "channel": 5,
                "data": [
                    "Candle",
                    ["Candle", "SPY{=d}", 4, DAY, 2, 2, 2, 2, 10]
                    + ["Candle", "SPY{=d}", 8, YESTERDAY, 1, 1, 1, 1, 10],
                ],
            },
            {
                "type": "FEED_DATA",
                "channel": 5,
                "data": ["Candle", ["Candle", "SPY{=d}", 0, DAY, 2, 3, 2, 3, 12]],
            },
        ]
    )
    with pytest.raises(_Ended):
        feed.run_session(socket, "qt", now=lambda: NOW)
    assert socket.sent[1] == {"type": "AUTH", "channel": 0, "token": "qt"}
    sub = next(m for m in socket.sent if m["type"] == "FEED_SUBSCRIPTION")
    assert sub["add"][0]["symbol"] == "SPY{=1d}"
    names = [a[0] for a, _ in emitted]
    assert names == ["chart_candles_snapshot", "chart_candle_update"]
    assert emitted[0][1] == {"to": room("SPY", "1d")}
    assert len(emitted[0][0][1]["bars"]) == 2
    assert emitted[1][0][1]["bar"]["close"] == 3.0
    assert feed.snapshot("SPY", "1d")["bars"][-1]["close"] == 3.0


def test_a_viewer_leaving_unsubscribes_and_the_session_ends_when_idle():
    feed, _ = feed_with_emits()
    feed._idle_disconnect = 0
    feed.add("s1", "SPY", "5m")
    script = [
        {"type": "AUTH_STATE", "channel": 0, "state": "AUTHORIZED"},
        {"type": "CHANNEL_OPENED", "channel": 5},
    ]
    socket = FakeSocket(script)
    original_recv = socket.recv

    def recv(timeout=None):
        if not socket.script:
            feed.remove_sid("s1")
            raise TimeoutError
        return original_recv(timeout)

    socket.recv = recv
    feed.run_session(socket, "qt", now=lambda: NOW)  # returns: idle
    subs = [m for m in socket.sent if m["type"] == "FEED_SUBSCRIPTION"]
    assert subs[0]["add"][0]["symbol"] == "SPY{=5m}"
    assert subs[1]["remove"] == [{"type": "Candle", "symbol": "SPY{=5m}"}]
