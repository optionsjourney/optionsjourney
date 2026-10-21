import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from optionsjourney.brokers.http import BrokerError
from optionsjourney.services import fill_sync
from optionsjourney.services.fill_sync import FillSync, TastyAccountStream, is_fill


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_one_sync_for_a_burst_of_fills_after_they_settle():
    clock, runs = Clock(), []
    fills = FillSync(lambda: runs.append(clock.t), clock=clock)
    assert not fills.step()  # nothing reported
    fills.notify("tastytrade")
    clock.t += 1
    fills.notify("tastytrade")  # a second leg's fill
    clock.t += 2
    assert not fills.step()  # still settling after the last one
    clock.t += fill_sync.SETTLE
    assert fills.step() and len(runs) == 1
    assert not fills.step()  # nothing new


def test_at_most_one_fill_sync_per_gap():
    clock, runs = Clock(), []
    fills = FillSync(lambda: runs.append(clock.t), clock=clock)
    fills.notify("schwab")
    clock.t += fill_sync.SETTLE
    assert fills.step()
    fills.notify("schwab")
    clock.t += fill_sync.SETTLE
    assert not fills.step()  # too soon after the last
    clock.t += fill_sync.GAP
    assert fills.step() and len(runs) == 2


def test_the_setting_off_ignores_fills():
    clock, runs = Clock(), []
    fills = FillSync(lambda: runs.append(1), enabled=lambda: False, clock=clock)
    fills.notify("tastytrade")
    clock.t += 60
    assert not fills.step() and runs == []


@pytest.mark.parametrize(
    "message, filled",
    [
        ({"type": "Order", "data": {"status": "Filled"}}, True),
        (
            {"type": "Order", "data": {"status": "Live", "legs": [{"fills": [{"quantity": 1}]}]}},
            True,
        ),
        ({"type": "Order", "data": {"status": "Live", "legs": [{"fills": []}]}}, False),
        ({"type": "ComplexOrder", "data": {"orders": [{"status": "Filled"}]}}, True),
        ({"type": "AccountBalance", "data": {"status": "Filled"}}, False),
        ({"action": "heartbeat", "status": "ok"}, False),
    ],
)
def test_is_fill(message, filled):
    assert is_fill(message) is filled


class Socket:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self, timeout=None):
        if not self.replies:
            raise ConnectionError("dropped")
        return json.dumps(self.replies.pop(0))


def _stream(replies, told):
    socket = Socket(replies)

    @contextmanager
    def connect(url):
        socket.url = url
        yield socket

    client = SimpleNamespace(
        accounts=lambda: [SimpleNamespace(number="5WX1"), SimpleNamespace(number="5WX2")]
    )
    credentials = SimpleNamespace(
        tastytrade_token=lambda: SimpleNamespace(value="tok", is_test=False)
    )
    return TastyAccountStream(client, credentials, told.append), socket, connect


def test_tasty_account_stream_subscribes_every_account_and_reports_fills():
    told = []
    stream, socket, connect = _stream(
        [
            {"action": "connect", "status": "ok"},
            {"type": "Order", "data": {"status": "Live"}},
            {"type": "Order", "data": {"status": "Filled"}},
        ],
        told,
    )
    with pytest.raises(ConnectionError):
        stream.run_once(connect)
    assert socket.url == "wss://streamer.tastyworks.com"
    assert socket.sent[0] == {
        "auth-token": "Bearer tok", "action": "connect", "value": ["5WX1", "5WX2"], "request-id": 1,
    }  # fmt: skip
    assert told == ["tastytrade"]


def test_tasty_account_stream_says_when_it_is_refused():
    stream, _socket, connect = _stream([{"action": "connect", "status": "error"}], [])
    with pytest.raises(BrokerError, match="refused"):
        stream.run_once(connect)
