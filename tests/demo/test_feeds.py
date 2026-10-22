from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.core.chart_bars import HISTORY
from optionsjourney.demo.feeds import DemoStream, make_chart_feed, push_candles, push_quotes
from optionsjourney.demo.market import PretendMarket
from optionsjourney.services.streaming import QuoteHub

START = datetime(2026, 10, 21, 13, 40, tzinfo=UTC)
PUT = "SPXW  261021P05800000"


def _hub():
    stream = DemoStream()
    return QuoteHub([stream]), stream


def test_subscribed_keys_get_quotes():
    market = PretendMarket(start=START)
    hub, stream = _hub()
    hub.subscribe(["SPX", PUT])
    push_quotes(hub, stream, market)
    for key in ("SPX", PUT):
        q = hub.latest(key)
        assert q.bid < q.ask and q.source == "tastytrade"
    assert hub.status()["tastytrade"] == "streaming"


def test_option_keys_get_deltas():
    market = PretendMarket(start=START)
    hub, stream = _hub()
    hub.subscribe([PUT, "SPX"])
    push_quotes(hub, stream, market)
    delta, _ = hub.delta(PUT)
    assert Decimal("-0.6") < delta < Decimal("-0.4")
    assert hub.delta("SPX") is None


def test_candles_reach_the_feed():
    market = PretendMarket(start=START)
    emitted = []
    feed = make_chart_feed(lambda name, payload, to=None: emitted.append((name, payload)))
    assert feed.add("sid", "SPX", "5m")
    assert "5m" in HISTORY
    push_candles(feed, market)
    snap = feed.snapshot("SPX", "5m")
    assert snap and len(snap["bars"]) > 10
    assert emitted[0][0] == "chart_candles_snapshot"
    push_candles(feed, market)
    assert emitted[-1][0] == "chart_candle_update"
    assert len(feed.snapshot("SPX", "5m")["bars"]) == len(snap["bars"])


def test_every_chart_interval_gets_a_snapshot():
    market = PretendMarket(start=START)
    feed = make_chart_feed(lambda *a, **k: None)
    for interval in HISTORY:
        feed.add("sid", "SPX", interval)
    push_candles(feed, market)
    for interval in HISTORY:
        snap = feed.snapshot("SPX", interval)
        assert snap and snap["bars"], interval


def _bars(interval):
    market = PretendMarket(start=START)
    feed = make_chart_feed(lambda *a, **k: None)
    feed.add("sid", "SPX", interval)
    push_candles(feed, market)
    return market, feed.snapshot("SPX", interval)["bars"]


def test_daily_and_longer_charts_have_a_year_or_more():
    market, daily = _bars("1d")
    assert len(daily) >= 250 and daily[-1]["close"] == market.last("SPX")
    assert len(_bars("1w")[1]) >= 50
    assert len(_bars("1mo")[1]) >= 12


def test_daily_closes_move_like_a_normal_year():
    import math
    import statistics

    closes = [b["close"] for b in _bars("1d")[1]]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]
    assert 0.08 < statistics.stdev(rets) * math.sqrt(252) < 0.30


def test_stream_keys_are_bounded_oldest_first():
    from optionsjourney.demo.feeds import MAX_KEYS

    stream = DemoStream()
    for i in range(MAX_KEYS + 100):
        stream.subscribe([f"K{i}"])
    assert len(stream.keys) == MAX_KEYS
    assert "K99" not in stream.keys and "K100" in stream.keys

    stream.subscribe(["K100"])  # again: now the newest
    stream.subscribe(["NEW"])
    assert "K100" in stream.keys and "K101" not in stream.keys


def test_one_subscription_is_never_cut():
    """A page or bot subscribes a whole chain at once, as a set: which of its keys came
    "first" is only the set's hash order, so none of them is dropped for the others."""
    from optionsjourney.demo.feeds import MAX_KEYS

    stream = DemoStream()
    stream.subscribe(["OLD"])
    batch = {f"K{i}" for i in range(MAX_KEYS + 50)}
    stream.subscribe(batch)
    assert set(stream.keys) == batch


def test_a_whole_spx_chain_is_priced():
    """The 0DTE condor reads every strike of today's SPX chain from the hub
    (chain._tasty_rows subscribes them all in one set). Every one gets a quote, whatever
    the process's hash order, along with what was subscribed before it."""
    from optionsjourney.demo.book import TASTY_ACCOUNT, Book
    from optionsjourney.demo.broker import PretendTasty

    market = PretendMarket(start=START)
    [item] = PretendTasty(market, Book(TASTY_ACCOUNT)).option_chain("SPX")
    [today] = [e for e in item["expirations"] if e["expiration-date"] == "2026-10-21"]
    chain = {s[side] for s in today["strikes"] for side in ("call", "put")}
    hub, stream = _hub()
    hub.subscribe(["SPX", "VIX", PUT])  # the page's and the bots' own keys, earlier
    hub.subscribe(chain)
    push_quotes(hub, stream, market)
    missing = sorted(k for k in chain | {"SPX", "VIX"} if hub.latest(k) is None)
    assert not missing, f"{len(missing)} of {len(chain) + 2} keys unpriced, e.g. {missing[:3]}"
