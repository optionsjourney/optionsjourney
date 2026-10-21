"""The quote streams and the hub that chooses between them (#22)."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from optionsjourney.app import create_app
from optionsjourney.brokers import guard
from optionsjourney.services.streaming import (
    QuoteHub,
    SchwabStream,
    StreamClosed,
    TastyStream,
    make_hub,
    marks,
)

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


class FakeSocket:
    """Plays the broker's side from a script; records what the app sends."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self, timeout=None):
        if not self.script:
            raise StreamClosed("end of script")
        item = self.script.pop(0)
        return item if isinstance(item, str) else json.dumps(item)

    def close(self):
        pass


class FakeTastyBroker:
    name = "TastyTrade"

    def quote_token(self):
        return "qt-1", "wss://dxlink.example/realtime"


def test_tastytrade_session_authorizes_subscribes_and_reads_quotes():
    hub = QuoteHub([], now=lambda: NOW)
    stream = TastyStream(FakeTastyBroker(), hub)
    stream.subscribe({"SPY", "SPXW  261016P05800000"})
    socket = FakeSocket(
        [
            {"type": "SETUP", "channel": 0},
            {"type": "AUTH_STATE", "channel": 0, "state": "UNAUTHORIZED"},
            {"type": "AUTH_STATE", "channel": 0, "state": "AUTHORIZED"},
            {"type": "CHANNEL_OPENED", "channel": 1},
            {"type": "FEED_CONFIG", "channel": 1},
            {
                "type": "FEED_DATA",
                "channel": 1,
                "data": [
                    "Quote",
                    ["Quote", "SPY", 778.5, 778.6, "Quote", ".SPXW261016P5800", 1.2, 1.4],
                ],
            },
        ]
    )
    stream.run_session(socket, "qt-1")
    sent_types = [m["type"] for m in socket.sent]
    assert sent_types[:4] == ["SETUP", "AUTH", "CHANNEL_REQUEST", "FEED_SETUP"]
    assert socket.sent[1]["token"] == "qt-1"
    subscription = next(m for m in socket.sent if m["type"] == "FEED_SUBSCRIPTION")
    assert {a["symbol"] for a in subscription["add"]} == {"SPY", ".SPXW261016P5800"}
    assert hub.latest("SPY").mark == Decimal("778.55")
    assert hub.latest("SPXW  261016P05800000").bid == Decimal("1.2")
    assert hub.latest("SPY").source == "tastytrade"


class FakeSchwabBroker:
    name = "Schwab"

    def streamer_info(self):
        return {
            "streamerSocketUrl": "wss://schwab.example/ws",
            "schwabClientCustomerId": "cust",
            "schwabClientCorrelId": "corr",
            "schwabClientChannel": "N9",
            "schwabClientFunctionId": "APIAPP",
        }


def test_schwab_session_logs_in_subscribes_and_merges_updates():
    hub = QuoteHub([], now=lambda: NOW)
    stream = SchwabStream(FakeSchwabBroker(), hub, token=lambda: "access-1")
    stream.subscribe({"SPX", "SPXW  261016P05800000"})
    socket = FakeSocket(
        [
            {
                "response": [
                    {"service": "ADMIN", "command": "LOGIN", "content": {"code": 0, "msg": "ok"}}
                ]
            },
            {
                "data": [
                    {
                        "service": "LEVELONE_EQUITIES",
                        "content": [{"key": "$SPX", "1": 7810, "2": 7812, "3": 7811}],
                    }
                ]
            },
            {"data": [{"service": "LEVELONE_EQUITIES", "content": [{"key": "$SPX", "2": 7813}]}]},
            {
                "data": [
                    {
                        "service": "LEVELONE_OPTIONS",
                        "content": [{"key": "SPXW  261016P05800000", "2": 1.2, "3": 1.4}],
                    }
                ]
            },
        ]
    )
    stream.run_session(socket, stream.login_info())
    login = socket.sent[0]["requests"][0]
    assert login["command"] == "LOGIN" and login["parameters"]["Authorization"] == "access-1"
    subs = [r for m in socket.sent[1:] for r in m["requests"]]
    assert {(r["service"], r["parameters"]["keys"]) for r in subs} == {
        ("LEVELONE_EQUITIES", "$SPX"),
        ("LEVELONE_OPTIONS", "SPXW  261016P05800000"),
    }
    spx = hub.latest("SPX")
    assert (spx.bid, spx.ask, spx.source) == (Decimal("7810"), Decimal("7813"), "schwab")
    assert hub.latest("SPXW  261016P05800000").ask == Decimal("1.4")


def test_a_refused_schwab_login_ends_the_session():
    hub = QuoteHub([], now=lambda: NOW)
    stream = SchwabStream(FakeSchwabBroker(), hub, token=lambda: "bad")
    socket = FakeSocket(
        [
            {
                "response": [
                    {
                        "service": "ADMIN",
                        "command": "LOGIN",
                        "content": {"code": 3, "msg": "Login denied"},
                    }
                ]
            }
        ]
    )
    try:
        stream.run_session(socket, stream.login_info())
        raise AssertionError("expected the session to end")
    except StreamClosed as e:
        assert "Login denied" in str(e)


class Stub:
    def __init__(self, name, up):
        self.name, self.up = name, up
        self.subscribed = set()

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def start(self):
        pass


def test_hub_uses_tastytrade_first_and_schwab_when_its_stream_is_down():
    tasty, schwab = Stub("tastytrade", True), Stub("schwab", True)
    hub = QuoteHub([tasty, schwab], now=lambda: NOW)
    hub.subscribe({"SPY"})
    assert tasty.subscribed == schwab.subscribed == {"SPY"}  # both, so the backup is warm
    hub.update("tastytrade", "SPY", bid=Decimal("1"), ask=Decimal("2"), last=None)
    hub.update("schwab", "SPY", bid=Decimal("1.1"), ask=Decimal("2.1"), last=None)
    assert hub.latest("SPY").source == "tastytrade"
    tasty.up = False
    assert hub.latest("SPY").source == "schwab"


def test_after_hours_the_hub_marks_a_pulled_market_at_its_last_real_mark():
    """#241: the hub remembers each option's last two-sided mark; a saved one
    only fills in for symbols it has seen nothing real for."""
    put, call = "SPXW  261023P06700000", "SPXW  261023C06900000"
    hub = QuoteHub([Stub("tastytrade", True)], now=lambda: NOW)
    hub.seed_good_marks({call: Decimal("2.00")})
    hub.update("tastytrade", put, bid=Decimal("7.00"), ask=Decimal("7.20"), last=None)
    hub.update("tastytrade", put, bid=Decimal("4.60"), ask=Decimal("10.00"), last=None)  # pulled
    hub.update("tastytrade", call, bid=Decimal("0"), ask=Decimal("3.00"), last=None)
    assert hub.good_mark(put) == Decimal("7.10")
    hub.seed_good_marks({put: Decimal("1")})  # never over a mark seen since the start
    evening = datetime(2026, 10, 9, 22, 0, tzinfo=UTC)
    assert marks(hub, {put, call}, evening) == {put: Decimal("7.10"), call: Decimal("2.00")}
    midday = datetime(2026, 10, 9, 16, 0, tzinfo=UTC)
    assert marks(hub, {put, call}, midday) == {put: Decimal("7.30")}  # live: the call has no mid


def test_hub_status():
    hub = QuoteHub([Stub("tastytrade", True), Stub("schwab", False)], now=lambda: NOW)
    assert hub.status() == {"tastytrade": "streaming", "schwab": "not connected"}


class FakeTastyWithFutures(FakeTastyBroker):
    def futures_option_streamer_symbols(self, root):
        assert root == "ES"
        return {"./ESZ6 EWX6  261130P7000": "./EWX26P7000:XCME"}


def test_tastytrade_subscribes_futures_options_by_their_stream_symbol():
    hub = QuoteHub([], now=lambda: NOW)
    stream = TastyStream(FakeTastyWithFutures(), hub)
    stream.subscribe({"./ESZ6 EWX6  261130P7000"})
    socket = FakeSocket(
        [
            {"type": "AUTH_STATE", "channel": 0, "state": "AUTHORIZED"},
            {"type": "CHANNEL_OPENED", "channel": 1},
            {
                "type": "FEED_DATA",
                "channel": 1,
                "data": ["Quote", ["Quote", "./EWX26P7000:XCME", 12.25, 12.75]],
            },
        ]
    )
    stream.run_session(socket, "qt")
    sub = next(m for m in socket.sent if m["type"] == "FEED_SUBSCRIPTION")
    assert {a["symbol"] for a in sub["add"]} == {"./EWX26P7000:XCME"}
    assert hub.latest("./ESZ6 EWX6  261130P7000").mark == Decimal("12.5")


def test_schwab_leaves_futures_options_to_tastytrade():
    hub = QuoteHub([], now=lambda: NOW)
    stream = SchwabStream(FakeSchwabBroker(), hub, token=lambda: "t")
    stream.subscribe({"./ESZ6 EWX6  261130P7000", "SPY"})
    socket = FakeSocket(
        [{"response": [{"service": "ADMIN", "command": "LOGIN", "content": {"code": 0}}]}]
    )
    stream.run_session(socket, stream.login_info())
    keys = [r["parameters"]["keys"] for m in socket.sent[1:] for r in m["requests"]]
    assert keys == ["SPY"]


def test_dxlink_keys_stream_as_they_are_on_tastytrade_only():
    hub = QuoteHub([], now=lambda: NOW)
    tasty = TastyStream(FakeTastyBroker(), hub)
    tasty.subscribe({"dx:/ESZ26:XCME"})
    socket = FakeSocket(
        [
            {"type": "AUTH_STATE", "channel": 0, "state": "AUTHORIZED"},
            {"type": "CHANNEL_OPENED", "channel": 1},
            {"type": "FEED_DATA", "channel": 1, "data": ["Quote", ["Quote", "/ESZ26:XCME", 1, 2]]},
        ]
    )
    tasty.run_session(socket, "qt")
    sub = next(m for m in socket.sent if m["type"] == "FEED_SUBSCRIPTION")
    assert {a["symbol"] for a in sub["add"]} == {"/ESZ26:XCME"}
    assert hub.latest("dx:/ESZ26:XCME").mark == Decimal("1.5")
    schwab = SchwabStream(FakeSchwabBroker(), hub, token=lambda: "t")
    assert schwab._subscriptions({}, {"dx:/ESZ26:XCME"}) == []


def test_a_broker_that_isnt_connected_is_reported_once_not_every_retry(monkeypatch, caplog):
    from optionsjourney.services import streaming

    class Stop(Exception):
        pass

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise Stop

    def not_connected():
        raise streaming.CredentialsUnavailable("Schwab isn't connected.")

    monkeypatch.setattr(streaming.time, "sleep", fake_sleep)
    stream = SchwabStream(object(), QuoteHub([]), token=lambda: "t")
    monkeypatch.setattr(stream, "_connect_and_run", not_connected)
    caplog.set_level("INFO", logger="optionsjourney.services.streaming")

    with pytest.raises(Stop):
        stream._loop()

    assert sleeps == [streaming.NOT_CONNECTED_PAUSE] * 3  # it keeps checking quietly
    assert [r.getMessage() for r in caplog.records] == ["schwab stream: Schwab isn't connected."]
    assert stream.state == "not connected"


def test_tastytrade_subscribes_greeks_for_options_and_keeps_the_delta_apart():
    """Watch rules (bots slice 7) read an option's delta (Tracker2's MarketStream
    subscribes DXLink's Greeks). A Greeks event never makes a quote look fresh."""
    from itertools import chain, repeat

    times = chain([NOW, NOW], repeat(NOW.replace(minute=5)))  # the quote's reads, then later
    hub = QuoteHub([], now=lambda: next(times))
    stream = TastyStream(FakeTastyBroker(), hub)
    stream.subscribe({"SPX", "SPXW  261016P05800000"})
    socket = FakeSocket(
        [
            {"type": "AUTH_STATE", "channel": 0, "state": "AUTHORIZED"},
            {"type": "CHANNEL_OPENED", "channel": 1},
            {
                "type": "FEED_DATA",
                "channel": 1,
                "data": ["Quote", ["Quote", ".SPXW261016P5800", 1.2, 1.4]],
            },
            {
                "type": "FEED_DATA",
                "channel": 1,
                "data": ["Greeks", ["Greeks", ".SPXW261016P5800", -0.21]],
            },
        ]
    )
    stream.run_session(socket, "qt-1")
    setup = next(m for m in socket.sent if m["type"] == "FEED_SETUP")
    assert setup["acceptEventFields"]["Greeks"] == ["eventType", "eventSymbol", "delta"]
    subscription = next(m for m in socket.sent if m["type"] == "FEED_SUBSCRIPTION")
    greeks = {a["symbol"] for a in subscription["add"] if a["type"] == "Greeks"}
    assert greeks == {".SPXW261016P5800"}  # options only, never SPX itself
    assert hub.delta("SPXW  261016P05800000") == (Decimal("-0.21"), NOW.replace(minute=5))
    assert hub.latest("SPXW  261016P05800000").at == NOW  # the quote's own time
    assert hub.delta("SPX") is None


def test_last_at_moves_only_with_a_last_price():
    clock = [NOW]
    hub = QuoteHub([], now=lambda: clock[0])
    hub.update("tastytrade", "SPX", last=Decimal("5800"))
    assert hub.latest("SPX").last_at == NOW
    clock[0] = NOW.replace(minute=1)
    hub.update("tastytrade", "SPX", bid=Decimal("5799"))
    quote = hub.latest("SPX")
    assert (quote.at, quote.last_at) == (NOW.replace(minute=1), NOW)


def test_a_non_finite_price_or_delta_is_never_stored():
    hub = QuoteHub([], now=lambda: NOW)
    stream = TastyStream(FakeTastyBroker(), hub)
    stream.subscribe({"SPXW  261016P05800000"})
    stream._subscription({"SPXW  261016P05800000"})
    stream._read(["Greeks", ["Greeks", ".SPXW261016P5800", "Infinity"]])
    stream._read(["Quote", ["Quote", ".SPXW261016P5800", "Infinity", 1.4]])
    assert hub.delta("SPXW  261016P05800000") is None
    assert hub.latest("SPXW  261016P05800000").bid is None


def test_a_gateway_hub_never_has_a_schwab_stream(fake_credentials):
    # Schwab allows one streamer session per login; in gateway mode the login
    # is the Tracker2 server's, so opening ours knocks its stream off.
    brokers = [FakeTastyBroker(), FakeSchwabBroker()]
    gateway = make_hub(brokers, fake_credentials, gateway=True)
    local = make_hub(brokers, fake_credentials, gateway=False)
    assert [s.name for s in gateway.streams] == ["tastytrade"]
    assert [s.name for s in local.streams] == ["tastytrade", "schwab"]


def test_the_app_builds_its_hub_without_schwab_in_gateway_mode(
    settings, gateway_settings, fake_credentials
):
    def stream_names(app_settings):
        app = create_app(app_settings, credentials=fake_credentials)
        return set(app.extensions["optionsjourney"].quote_hub.status())

    assert stream_names(gateway_settings) == {"tastytrade"}
    guard._uninstall_for_tests()
    assert stream_names(settings) == {"tastytrade", "schwab"}


def test_a_schwab_stream_never_connects_once_the_guard_is_on():
    class NeverAsked(FakeSchwabBroker):
        def streamer_info(self):
            raise AssertionError("asked Schwab for its streamer login")

    guard.install()
    stream = SchwabStream(NeverAsked(), QuoteHub([], now=lambda: NOW), token=lambda: "access-1")
    with pytest.raises(StreamClosed):
        stream._connect_and_run()


def test_schwab_stream_reports_account_activity_when_asked():
    hub = QuoteHub([], now=lambda: NOW)
    stream = SchwabStream(FakeSchwabBroker(), hub, token=lambda: "access-1")
    told = []
    stream.on_account_activity = told.append
    login_ok = {"response": [{"service": "ADMIN", "command": "LOGIN", "content": {"code": 0}}]}
    subscribed = {
        "data": [{"service": "ACCT_ACTIVITY", "content": [{"1": "acct", "2": "SUBSCRIBED"}]}]
    }
    filled = {
        "data": [
            {"service": "ACCT_ACTIVITY", "content": [{"1": "acct", "2": "OrderFillCompleted"}]}
        ]
    }
    socket = FakeSocket([login_ok, subscribed, filled])
    stream.run_session(socket, stream.login_info())
    requests = [r for m in socket.sent for r in m["requests"]]
    assert ("ACCT_ACTIVITY", "SUBS") in {(r["service"], r["command"]) for r in requests}
    assert told == ["schwab"]  # the fill, not the subscription's own message


def test_schwab_stream_asks_for_no_account_activity_unless_told_to():
    hub = QuoteHub([], now=lambda: NOW)
    stream = SchwabStream(FakeSchwabBroker(), hub, token=lambda: "access-1")
    login_ok = {"response": [{"service": "ADMIN", "command": "LOGIN", "content": {"code": 0}}]}
    socket = FakeSocket([login_ok])
    stream.run_session(socket, stream.login_info())
    assert all(r["service"] != "ACCT_ACTIVITY" for m in socket.sent for r in m["requests"])
