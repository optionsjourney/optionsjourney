import json
import logging
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from optionsjourney.bots.definition import MoveLimits, iron_condor_skeleton
from optionsjourney.brokers.credentials.base import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import DailyClose
from optionsjourney.services import bot_filters, market_feed
from optionsjourney.services.market_feed import Want, read_dxlink
from tests.services.market_fakes import day_id, et, ms

D = Decimal
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


HELLO = [
    {"type": "AUTH_STATE", "state": "UNAUTHORIZED"},
    {"type": "AUTH_STATE", "state": "AUTHORIZED"},
    {"type": "CHANNEL_OPENED", "channel": 3},
]


def feed(*data):
    return {"type": "FEED_DATA", "channel": 3, "data": list(data)}


def candle(symbol, flags, at, open_, close=None):
    close = open_ if close is None else close
    return ["Candle", symbol, flags, at, open_, open_, open_, close, 10]


def test_the_handshake_subscribes_to_what_is_wanted():
    socket = FakeSocket(HELLO)
    want = Want(trades=("VIX",), summaries=("VIX",), candles=(("VIX{=1m}", T),))
    read_dxlink(socket, "tok", want, wait_s=0.01, ready=lambda got: False)
    kinds = [m["type"] for m in socket.sent]
    assert kinds == ["SETUP", "AUTH", "CHANNEL_REQUEST", "FEED_SETUP", "FEED_SUBSCRIPTION"]
    assert socket.sent[1]["token"] == "tok"
    assert socket.sent[4]["add"] == [
        {"type": "Trade", "symbol": "VIX"},
        {"type": "Summary", "symbol": "VIX"},
        {"type": "Candle", "symbol": "VIX{=1m}", "fromTime": T},
    ]


def test_trades_keep_the_newest_print_and_ignore_empty_ones():
    socket = FakeSocket(
        [
            *HELLO,
            feed(
                "Trade",
                ["Trade", "VIX", 16.1, T + 2000, "Trade", "VIX", 16.0, T + 1000]
                + ["Trade", "VIX9D", "NaN", T + 3000, "Trade", "VIX9D", 0, T + 3000],
            ),
        ]
    )
    got = read_dxlink(socket, "tok", Want(trades=("VIX", "VIX9D")), 0.05, lambda got: False)
    assert got.trades == {"VIX": (D("16.1"), T + 2000)}


def test_summaries_and_candles_and_the_end_flag():
    socket = FakeSocket(
        [
            *HELLO,
            feed("Summary", ["Summary", "VIX", 20739, 15.25]),
            feed(
                "Candle",
                candle("VIX{=m}", 4, T + 60000, 15.5)  # newest first; begins the snapshot
                + candle("VIX{=m}", 0, T, 15.4)
                + candle("VIX{=m}", 2, T - 60000, 15.3)  # removed: not a bar
                + candle("VIX{=m}", 0, T - 120000, "NaN"),  # no price: not a bar
            ),
            feed("Candle", candle("VIX{=m}", 10, T - 180000, 0)),  # the end marker
            feed("Candle", candle("VIX{=m}", 0, T, 15.45)),  # a later update of a bar
        ]
    )
    want = Want(summaries=("VIX",), candles=(("VIX{=1m}", T - 600000),))
    got = read_dxlink(socket, "tok", want, 0.2, lambda got: False)
    assert got.summaries == {"VIX": (20739, D("15.25"))}
    bars = got.bars("VIX{=1m}")
    assert [(b.time.timestamp() * 1000, b.open) for b in bars] == [
        (T, D("15.45")),
        (T + 60000, D("15.5")),
    ]
    assert got.is_complete("VIX{=1m}")


def test_a_snapshot_without_its_end_is_not_complete():
    socket = FakeSocket([*HELLO, feed("Candle", candle("SPX{=d}", 4, T, 5800))])
    got = read_dxlink(socket, "tok", Want(candles=(("SPX{=1d}", T),)), 0.05, lambda got: False)
    assert len(got.bars("SPX{=1d}")) == 1 and not got.is_complete("SPX{=1d}")


def test_daily_and_minute_candles_of_one_symbol_are_kept_apart():
    socket = FakeSocket(
        [
            *HELLO,
            feed("Candle", candle("SPX{=d,tho=true}", 8, T, 5800)),
            feed("Candle", candle("SPX{=m}", 8, T, 5810)),
        ]
    )
    want = Want(candles=(("SPX{=1d}", T), ("SPX{=1m}", T)))
    got = read_dxlink(socket, "tok", want, 0.05, lambda got: False)
    assert [b.open for b in got.bars("SPX{=1d}")] == [D("5800")]
    assert [b.open for b in got.bars("SPX{=1m}")] == [D("5810")]
    assert got.is_complete("SPX{=1d}") and got.is_complete("SPX{=1m}")


def test_it_returns_as_soon_as_ready():
    socket = FakeSocket(
        [
            *HELLO,
            feed("Trade", ["Trade", "VIX", 16.1, T]),
            feed("Trade", ["Trade", "VIX", 17, T + 9]),
        ]
    )
    got = read_dxlink(socket, "tok", Want(trades=("VIX",)), 5, lambda got: "VIX" in got.trades)
    assert got.trades["VIX"] == (D("16.1"), T)
    assert len(socket.script) == 1  # the second message was never read


def test_a_feed_error_raises():
    socket = FakeSocket([*HELLO, {"type": "ERROR", "message": "bad symbol"}])
    with pytest.raises(BrokerError, match="bad symbol"):
        read_dxlink(socket, "tok", Want(trades=("VIX",)), 1, lambda got: False)


def test_no_tastytrade_connection_is_unavailable():
    feed_ = market_feed.TastyFeed(lambda: [object()])
    with pytest.raises(market_feed.FeedUnavailable, match="TastyTrade"):
        feed_.read(Want(trades=("VIX",)), 1, lambda got: True)


# --- Falling back to Schwab (#194) ----------------------------------------------------

DAY = date(2026, 10, 13)
NOW = et(DAY, 9, 47)


class FakeSchwab:
    def __init__(self, quotes=None, closes=(), history=None, error=None, exclusive_start=False):
        self._quotes, self._closes, self._history = quotes or {}, list(closes), history or {}
        self.error, self.calls = error, []
        self.exclusive_start = exclusive_start  # bars strictly after startDate only

    def quotes(self, symbols):
        self.calls.append(("quotes", list(symbols)))
        if self.error:
            raise self.error
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    def daily_closes(self, symbol, start, end):
        self.calls.append(("daily_closes", symbol, start, end))
        return [c for c in self._closes if start <= c.day <= end]

    def price_history(self, symbol, params):
        self.calls.append(("price_history", symbol, params))
        rows = self._history.get((symbol, params["frequencyType"]), [])
        if self.exclusive_start:
            rows = [r for r in rows if r["datetime"] > params["startDate"]]
        return rows


class Raising:
    def __init__(self, error):
        self.error, self.calls = error, 0

    def read(self, want, wait_s, ready):
        self.calls += 1
        raise self.error


class Answers:
    def __init__(self, got):
        self.got, self.calls = got, 0

    def read(self, want, wait_s, ready):
        self.calls += 1
        return self.got


def vix_trade(price="18.5", at=None):
    return {"$VIX": {"quote": {"lastPrice": float(price), "tradeTime": ms(at or NOW)}}}


def schwab_feed(client):
    return market_feed.SchwabFeed(lambda: [object(), client], now=lambda: NOW)


def is_fresh(got):
    return "VIX" in got.trades


def test_falls_back_to_schwab_when_tastytrade_isnt_connected():
    tasty = Raising(market_feed.FeedUnavailable("TastyTrade isn't connected"))
    feed_ = market_feed.FallbackFeed([tasty, schwab_feed(FakeSchwab(quotes=vix_trade()))])
    got = feed_.read(Want(trades=("VIX",)), 10, is_fresh)
    assert got.trades["VIX"] == (D("18.5"), ms(NOW))
    assert got.source == "Schwab"


def test_a_tastytrade_read_that_isnt_ready_asks_schwab():
    tasty = Answers(market_feed.Got())
    schwab = FakeSchwab(quotes=vix_trade())
    got = market_feed.FallbackFeed([tasty, schwab_feed(schwab)]).read(
        Want(trades=("VIX",)), 10, is_fresh
    )
    assert got.trades["VIX"][0] == D("18.5")
    assert schwab.calls == [("quotes", ["$VIX"])]


def test_a_ready_tastytrade_read_never_asks_schwab():
    ready = market_feed.Got()
    ready.trades["VIX"] = (D("18"), ms(NOW))
    schwab = FakeSchwab(quotes=vix_trade())
    got = market_feed.FallbackFeed([Answers(ready), schwab_feed(schwab)]).read(
        Want(trades=("VIX",)), 10, is_fresh
    )
    assert got is ready and got.source == "TastyTrade" and schwab.calls == []


def test_when_no_feed_is_ready_the_last_read_is_returned():
    schwab = FakeSchwab()  # answers, but without VIX
    got = market_feed.FallbackFeed([Answers(market_feed.Got()), schwab_feed(schwab)]).read(
        Want(trades=("VIX",)), 10, is_fresh
    )
    assert got.source == "Schwab" and got.trades == {}


def test_every_feed_failing_is_feed_unavailable():
    feeds = [
        Raising(market_feed.FeedUnavailable("TastyTrade isn't connected")),
        schwab_feed(FakeSchwab(error=BrokerError("Can't reach Schwab."))),
    ]
    with pytest.raises(market_feed.FeedUnavailable) as e:
        market_feed.FallbackFeed(feeds).read(Want(trades=("VIX",)), 10, is_fresh)
    assert str(e.value) == "TastyTrade isn't connected; Can't reach Schwab."


def test_a_feed_without_credentials_is_logged_quietly(caplog):
    # No token right now is expected, like a broker being down: info, no traceback.
    tasty = Raising(CredentialsUnavailable("TastyTrade isn't connected."))
    with caplog.at_level(logging.INFO, logger="optionsjourney.services.market_feed"):
        got = market_feed.FallbackFeed([tasty, schwab_feed(FakeSchwab(quotes=vix_trade()))]).read(
            Want(trades=("VIX",)), 10, is_fresh
        )
    assert got.source == "Schwab"
    assert [(r.levelno, r.exc_info) for r in caplog.records] == [(logging.INFO, None)]
    assert "TastyTrade isn't connected" in caplog.records[0].getMessage()


def test_schwab_feed_reads_trades_summaries_and_candles():
    stamp = datetime(2026, 10, 12, 5, tzinfo=UTC)  # Schwab's daily bar: New York midnight
    row = {"datetime": ms(stamp), "open": 5000, "high": 5010, "low": 4990, "close": 5005,
           "volume": 100}  # fmt: skip
    minute = {"datetime": ms(et(DAY, 9, 30)), "open": 17.6, "high": 17.7, "low": 17.5,
              "close": 17.65, "volume": 0}  # fmt: skip
    schwab = FakeSchwab(
        quotes=vix_trade(),
        closes=[
            DailyClose("$VIX", date(2026, 10, 9), D("17.2")),
            DailyClose("$VIX", date(2026, 10, 12), D("17.9")),
            DailyClose("$VIX", DAY, D("18.4")),
        ],  # fmt: skip
        history={("$SPX", "daily"): [row], ("$VIX", "minute"): [minute]},
    )
    from_ms = ms(et(DAY, 9, 30))
    want = Want(
        trades=("VIX",),
        summaries=("VIX",),
        candles=(("SPX{=1d}", 1), ("VIX{=1m}", from_ms)),
    )
    got = schwab_feed(schwab).read(want, 10, lambda g: False)
    assert got.source == "Schwab"
    assert got.trades["VIX"] == (D("18.5"), ms(NOW))
    assert got.summaries["VIX"] == (day_id(DAY), D("17.9"))
    assert got.is_complete("SPX{=1d}") and got.is_complete("VIX{=1m}")
    assert got.bars("SPX{=1d}")[0].time == datetime(2026, 10, 12, tzinfo=UTC)
    assert got.bars("SPX{=1d}")[0].close == D("5005")
    assert got.bars("VIX{=1m}")[0].time == et(DAY, 9, 30)
    daily = next(c for c in schwab.calls if c[0] == "price_history" and c[1] == "$SPX")[2]
    assert daily == {"periodType": "year", "frequencyType": "daily", "frequency": 1,
                     "startDate": 1, "endDate": ms(NOW)}  # fmt: skip
    minutes = next(c for c in schwab.calls if c[0] == "price_history" and c[1] == "$VIX")[2]
    assert minutes == {"periodType": "day", "frequencyType": "minute", "frequency": 1,
                       "startDate": from_ms - 120_000, "endDate": ms(NOW),
                       "needExtendedHoursData": "false"}  # fmt: skip


def test_a_schwab_summary_only_counts_for_the_previous_session():
    # The newest close before today is two sessions back: no prior close at all.
    schwab = FakeSchwab(closes=[DailyClose("$VIX", date(2026, 10, 9), D("17.2"))])
    got = schwab_feed(schwab).read(Want(summaries=("VIX",)), 10, lambda g: False)
    assert got.summaries == {}


def test_schwab_symbols():
    assert market_feed.schwab_symbol("VIX") == "$VIX"
    assert market_feed.schwab_symbol("VIX9D") == "$VIX9D"
    assert market_feed.schwab_symbol("SPX") == "$SPX"
    assert market_feed.schwab_symbol("SPY") == "SPY"


def test_schwab_feed_is_unavailable_without_schwab_or_for_futures():
    with pytest.raises(market_feed.FeedUnavailable, match="Schwab"):
        market_feed.SchwabFeed(lambda: [object()]).read(Want(trades=("VIX",)), 1, is_fresh)
    with pytest.raises(market_feed.FeedUnavailable, match="/ES"):
        schwab_feed(FakeSchwab()).read(Want(trades=("/ES",)), 1, is_fresh)
    with pytest.raises(market_feed.FeedUnavailable, match="/ES"):
        schwab_feed(FakeSchwab()).read(Want(candles=(("/ES{=1m}", 0),)), 1, is_fresh)


def test_a_schwab_daily_bar_is_dated_by_its_new_york_day():
    summer = {"datetime": ms(datetime(2026, 7, 6, 4, tzinfo=UTC)), "open": 1, "high": 1,
              "low": 1, "close": 1}  # fmt: skip
    bar = market_feed.candle_from_schwab(summer, daily=True)
    assert bar.time == datetime(2026, 7, 6, tzinfo=UTC) and bar.volume == 0
    minute = market_feed.candle_from_schwab(summer, daily=False)
    assert minute.time == datetime(2026, 7, 6, 4, tzinfo=UTC)
    assert timedelta(0) == minute.time - datetime.fromtimestamp(summer["datetime"] / 1000, UTC)


def vix_minutes(*rows):
    return [{"datetime": ms(at), "open": o, "high": o, "low": o, "close": o, "volume": 0}
            for at, o in rows]  # fmt: skip


def schwab_overnight(history, max_up="3"):
    """The overnight filter read from Schwab alone (TastyTrade down)."""
    schwab = FakeSchwab(
        closes=[DailyClose("$VIX", date(2026, 10, 12), D("15.00"))],
        history={("$VIX", "minute"): history},
        exclusive_start=True,  # a start time Schwab treats as exclusive
    )
    tasty = Raising(market_feed.FeedUnavailable("TastyTrade isn't connected"))
    market = bot_filters.Market(market_feed.FallbackFeed([tasty, schwab_feed(schwab)]))
    bot = replace(iron_condor_skeleton("IC"), id=7, use_vix_overnight=True,
                  vix_overnight=MoveLimits(max_up=D(max_up)))  # fmt: skip
    return bot_filters.check(market, bot, NOW)


def test_schwabs_overnight_open_is_the_0930_bar():
    found = schwab_overnight(
        vix_minutes((et(DAY, 9, 29), 15.1), (et(DAY, 9, 30), 15.6), (et(DAY, 9, 31), 99))
    )
    assert found.unavailable is None
    assert found.reason == (
        "VIX overnight move filter: VIX 15.00 -> 15.6 (+4.00%); outside max up 3.00%"
    )


def test_schwab_without_a_0930_bar_fails_closed():
    # Starts at 09:31: its open is never taken for 09:30's, however wide the limits.
    found = schwab_overnight(vix_minutes((et(DAY, 9, 31), 15.1), (et(DAY, 9, 32), 15.2)), "50")
    assert found.unavailable == "VIX overnight move"
    assert found.reason == (
        "VIX overnight move unavailable: no VIX 09:30 bar from Schwab. Not entered."
    )
