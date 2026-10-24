from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from optionsjourney.core.holdings import StockPosition
from optionsjourney.core.transactions import Fill, InstrumentType, Kind, Side
from optionsjourney.db.models import Setting
from optionsjourney.services import chart_market, stock_value, transactions
from optionsjourney.services.transactions import FillsView

NOW = datetime(2026, 10, 22, 18, 0, tzinfo=UTC)  # Thursday 14:00 New York, market open
TODAY = date(2026, 10, 22)
START = date(2026, 10, 16)  # a Friday
DAYS = [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21), TODAY]


class Quote:
    def __init__(self, last=None, mark=None):
        self.last = None if last is None else Decimal(str(last))
        self.mark = None if mark is None else Decimal(str(mark))


class Hub:
    """Live quotes by ticker; remembers what was subscribed."""

    def __init__(self, **quotes):
        self.quotes = quotes
        self.subscribed = set()

    def subscribe(self, symbols):
        self.subscribed |= set(symbols)

    def latest(self, symbol):
        return self.quotes.get(symbol)


@pytest.fixture
def sf(factory):
    with factory() as session:
        session.add(Setting(key="history_starts", value=START.isoformat(), updated_at=NOW))
        session.commit()
    return factory


class _Fills(list):
    def __init__(self):
        super().__init__()
        self.asked = {}


@pytest.fixture
def fills(monkeypatch):
    """The fills load_fills hands back, and the `since` it was asked for."""
    found = _Fills()
    asked = found.asked

    def load(session_factory, *, since=None, account=None):
        asked["since"] = since
        return FillsView(fills=list(found))

    monkeypatch.setattr(transactions, "load_fills", load)
    return found


@pytest.fixture(autouse=True)
def no_card_rows(monkeypatch):
    """Today is priced from the strict quantities, never the holdings table's rows."""

    def refuse(*a, **k):
        raise AssertionError("stock_holdings must not be called")

    monkeypatch.setattr(stock_value.holdings, "stock_holdings", refuse)


def stock_fill(account, side, qty, day, ticker="AAPL"):
    broker, number = account.split(":")
    return Fill(
        broker=broker,
        account_number=number,
        transaction_id=f"{account}-{day}-{side}",
        leg=0,
        executed_at=datetime(day.year, day.month, day.day, 15, 0, tzinfo=UTC),
        kind=Kind.TRADE,
        instrument_type=InstrumentType.STOCK,
        symbol=ticker,
        underlying=ticker,
        option=None,
        side=side,
        effect=None,
        quantity=Decimal(qty),
        price=Decimal(100),
        value=Decimal(0),
        fees=Decimal(0),
    )


def flat(*a):
    return {d: Decimal(100) for d in DAYS}


def at(*positions):
    return lambda clients, strict, skipped: list(positions)


def test_broker_down_draws_nothing(sf, fills):
    down = lambda c, strict, skipped: None  # noqa: E731
    chart = stock_value.portfolio_value(sf, [], Hub(), (), NOW, positions=down)
    assert chart.points == [] and chart.note == stock_value.COULD_NOT_LOAD


def test_one_account_ignores_the_other_accounts_fills(sf, fills):
    # schwab:2 sold 50 yesterday; counted, it would show 100 of its shares before then
    fills.append(stock_fill("schwab:2", Side.SELL, 50, date(2026, 10, 21)))
    chart = stock_value.portfolio_value(
        sf,
        [],
        Hub(),
        ("tt:1",),
        NOW,
        positions=at(
            StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90)),
            StockPosition("schwab:2", "AAPL", Decimal(50), Decimal(90)),
        ),
        closes=flat,
    )
    assert chart.note == ""
    assert {p.value for p in chart.points if p.day < TODAY} == {Decimal(1000)}


def test_a_broker_not_connected_is_left_out(sf, fills):
    # Its old fills would otherwise be undone against no shares at all
    fills.append(stock_fill("schwab:2", Side.SELL, 50, date(2026, 10, 21)))

    def positions(clients, strict, skipped):
        skipped.add("schwab")
        return [StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90))]

    chart = stock_value.portfolio_value(sf, [], Hub(), (), NOW, positions=positions, closes=flat)
    assert chart.note == ""
    assert {p.value for p in chart.points if p.day < TODAY} == {Decimal(1000)}


def test_fills_rebuild_earlier_days(sf, fills):
    fills.append(stock_fill("tt:1", Side.BUY, 4, date(2026, 10, 20)))
    chart = stock_value.portfolio_value(
        sf,
        [],
        Hub(),
        (),
        NOW,
        positions=at(StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90))),
        closes=flat,
    )
    by_day = {p.day: p.value for p in chart.points}
    assert by_day[date(2026, 10, 19)] == Decimal(600)
    assert by_day[date(2026, 10, 20)] == Decimal(1000)


def test_days_are_trading_days_from_history_start(sf, fills):
    chart = stock_value.portfolio_value(
        sf,
        [],
        Hub(),
        (),
        NOW,
        positions=at(StockPosition("tt:1", "AAPL", Decimal(1), Decimal(90))),
        closes=flat,
    )
    assert [p.day for p in chart.points] == DAYS  # no weekend
    assert fills.asked["since"] == datetime(2026, 10, 16, 4, 0, tzinfo=UTC)  # 00:00 EDT


def test_no_prices_at_all(sf, fills):
    def fail(*a):
        raise chart_market.MarketError("down")

    chart = stock_value.portfolio_value(
        sf,
        [],
        Hub(),
        (),
        NOW,
        positions=at(StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90))),
        closes=fail,
    )
    assert chart.points == [] and chart.note == stock_value.NO_PRICES


def test_one_ticker_without_prices_leaves_the_others(sf, fills):
    def closes(clients, ticker, start, now):
        if ticker == "MSFT":
            raise chart_market.MarketError("down")
        return flat()

    chart = stock_value.portfolio_value(
        sf,
        [],
        Hub(),
        (),
        NOW,
        positions=at(
            StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90)),
            StockPosition("tt:1", "MSFT", Decimal(5), Decimal(90)),
        ),
        closes=closes,
    )
    first = chart.points[0]
    assert chart.note == ""
    assert (first.value, first.priced, first.total) == (Decimal(1000), 1, 2)


def test_tickers_sold_before_today_are_priced_too(sf, fills):
    fills.append(stock_fill("tt:1", Side.SELL, 3, date(2026, 10, 20), ticker="MSFT"))
    asked = []

    def closes(clients, ticker, start, now):
        asked.append((ticker, start))
        return flat()

    stock_value.portfolio_value(
        sf,
        [],
        Hub(),
        (),
        NOW,
        positions=at(StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90))),
        closes=closes,
    )
    assert sorted(asked) == [("AAPL", START), ("MSFT", START)]


def test_duplicate_positions_are_summed(sf, fills):
    chart = stock_value.portfolio_value(
        sf,
        [],
        Hub(),
        (),
        NOW,
        positions=at(
            StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90)),
            StockPosition("tt:1", "AAPL", Decimal(5), Decimal(80)),
        ),
        closes=flat,
    )
    assert chart.points[0].value == Decimal(1500)


def two_tickers():
    return at(
        StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90)),
        StockPosition("tt:1", "MSFT", Decimal(1), Decimal(90)),
    )


def before_today(*a):
    return {d: Decimal(100) for d in DAYS if d < TODAY}


def test_today_uses_live_prices_while_open(sf, fills):
    hub = Hub(AAPL=Quote(last="123.5"), MSFT=Quote(mark=50))
    chart = stock_value.portfolio_value(
        sf, [], hub, (), NOW, positions=two_tickers(), closes=before_today
    )
    assert [p.day for p in chart.points] == DAYS
    assert hub.subscribed == {"AAPL", "MSFT"}
    last = chart.points[-1]
    assert (last.value, last.priced, last.total) == (Decimal(1285), 2, 2)  # 1235 + 50
    assert chart.points[-2].value == Decimal(1100)


def test_no_live_quote_keeps_the_latest_close(sf, fills):
    chart = stock_value.portfolio_value(
        sf, [], Hub(), (), NOW, positions=two_tickers(), closes=before_today
    )
    last = chart.points[-1]
    assert (last.value, last.priced, last.total) == (Decimal(1100), 2, 2)  # no cliff


def test_one_ticker_live_the_other_from_its_close(sf, fills):
    hub = Hub(AAPL=Quote(last=120))
    chart = stock_value.portfolio_value(
        sf, [], hub, (), NOW, positions=two_tickers(), closes=before_today
    )
    last = chart.points[-1]
    assert (last.value, last.priced, last.total) == (Decimal(1300), 2, 2)  # 1200 + 100


def test_before_the_open_today_ignores_live_quotes(sf, fills):
    early = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)  # 08:00 New York
    hub = Hub(AAPL=Quote(last="123.5"))
    chart = stock_value.portfolio_value(
        sf,
        [],
        hub,
        (),
        early,
        positions=at(StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90))),
        closes=before_today,
    )
    assert chart.points[-1].value == Decimal(1000)
    assert hub.subscribed == set()


def test_cached_closes_are_not_changed_by_the_live_price(sf, fills):
    kept = before_today()
    stock_value.portfolio_value(
        sf,
        [],
        Hub(AAPL=Quote(last=120)),
        (),
        NOW,
        positions=at(StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90))),
        closes=lambda *a: kept,
    )
    assert TODAY not in kept


def test_no_candles_for_any_ticker_is_no_prices(sf, fills):
    chart = stock_value.portfolio_value(
        sf, [], Hub(AAPL=Quote(last=120)), (), NOW, positions=two_tickers(), closes=lambda *a: {}
    )
    assert chart.points == [] and chart.note == stock_value.NO_PRICES


def test_never_synced_database_prices_today_live(factory, fills):
    # History starts today; the closes reach back before it.
    chart = stock_value.portfolio_value(
        factory,
        [],
        Hub(AAPL=Quote(last=120)),
        (),
        NOW,
        positions=at(StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90))),
        closes=lambda *a: {date(2026, 10, 21): Decimal(100)},
    )
    assert [(p.day, p.value) for p in chart.points] == [(TODAY, Decimal(1200))]


def test_history_starts_is_not_written(factory, fills):
    stock_value.portfolio_value(
        factory,
        [],
        Hub(),
        (),
        NOW,
        positions=at(StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90))),
        closes=flat,
    )
    with factory() as s:
        assert s.get(Setting, "history_starts") is None


def test_daily_closes_by_session_date_and_kept(monkeypatch):
    calls = []

    def history(clients, ticker, frequency, period, now):
        calls.append((ticker, frequency, period))
        day = int(datetime(2026, 10, 21, tzinfo=UTC).timestamp())
        return {"candles": [{"time": day, "close": 101.25}], "symbol": ticker}

    monkeypatch.setattr(stock_value, "_cache", stock_value.Cache())
    monkeypatch.setattr(chart_market, "price_history", history)
    start = TODAY - timedelta(days=30)
    got = stock_value.daily_closes([], "AAPL", start, NOW)
    assert got == {date(2026, 10, 21): Decimal("101.25")}
    assert stock_value.daily_closes([], "AAPL", start, NOW) == got
    assert len(calls) == 1
    ticker, frequency, period = calls[0]
    assert (ticker, frequency) == ("AAPL", 1440) and period >= 30


def test_a_ticker_without_closes_is_left_out_of_today_too(sf, fills):
    # MSFT has no closes but a live quote: counting it today only would be a step
    def closes(clients, ticker, start, now):
        return {} if ticker == "MSFT" else before_today()

    hub = Hub(AAPL=Quote(last=120), MSFT=Quote(last=50))
    chart = stock_value.portfolio_value(
        sf, [], hub, (), NOW, positions=two_tickers(), closes=closes
    )
    last = chart.points[-1]
    assert (last.value, last.priced, last.total) == (Decimal(1200), 1, 2)
    assert "MSFT" not in hub.subscribed


def test_only_tickers_held_today_are_priced_live(sf, fills):
    fills.append(stock_fill("tt:1", Side.SELL, 3, date(2026, 10, 20), ticker="MSFT"))
    hub = Hub(AAPL=Quote(last=120), MSFT=Quote(last=50))
    stock_value.portfolio_value(
        sf,
        [],
        hub,
        (),
        NOW,
        positions=at(StockPosition("tt:1", "AAPL", Decimal(10), Decimal(90))),
        closes=flat,
    )
    assert hub.subscribed == {"AAPL"}


def test_nothing_held_and_no_fills_draws_nothing_quietly(sf, fills):
    def refuse(*a):
        raise AssertionError("closes must not be fetched")

    hub = Hub()
    chart = stock_value.portfolio_value(sf, [], hub, (), NOW, positions=at(), closes=refuse)
    assert chart.points == [] and chart.note == ""
    assert hub.subscribed == set()


def test_fills_since_is_the_first_day_at_midnight_new_york(sf):
    assert stock_value.first_day(sf, NOW) == START
    assert stock_value.fills_since(START) == datetime(2026, 10, 16, 4, 0, tzinfo=UTC)


def test_first_day_is_not_written(factory):
    assert stock_value.first_day(factory, NOW) == TODAY
    with factory() as s:
        assert s.get(Setting, "history_starts") is None
