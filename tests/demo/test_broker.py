from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from optionsjourney.brokers.http import BrokerError
from optionsjourney.brokers.tastytrade import parse_transaction
from optionsjourney.core.accounts import Balance, BrokerAccount
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.demo.broker import (
    SCHWAB_ACCOUNT,
    TASTY_ACCOUNT,
    Book,
    PretendSchwab,
    PretendTasty,
)
from optionsjourney.demo.clock import DEMO_DAY
from optionsjourney.demo.market import PretendMarket
from optionsjourney.services import journal
from optionsjourney.services.sync import sync_all
from optionsjourney.trading.orders import BrokerOrder, OrderStatus, plain

T0 = datetime(2026, 10, 21, 13, 40, tzinfo=UTC)  # 9:40 New York
PUT_5700 = "SPXW  261021P05700000"
PUT_5690 = "SPXW  261021P05690000"
PUT_5650 = "SPXW  261021P05650000"


def _setup(seed=1):
    market = PretendMarket(seed=seed, start=T0)
    return market, PretendTasty(market, Book(TASTY_ACCOUNT))


def _spread_body(price, effect="Credit", order_type="Limit", trigger=None, opening=True):
    sell, buy = ("Sell to Open", "Buy to Open") if opening else ("Buy to Close", "Sell to Close")
    body = {
        "time-in-force": "Day",
        "order-type": order_type,
        "legs": [
            {"instrument-type": "Equity Option", "symbol": PUT_5700, "quantity": 1,
             "action": sell},
            {"instrument-type": "Equity Option", "symbol": PUT_5690, "quantity": 1,
             "action": buy},
        ],
    }  # fmt: skip
    if price is not None:
        body["price"] = f"{price:.2f}"
        body["price-effect"] = effect
    if trigger is not None:
        body["stop-trigger"] = f"{trigger:.2f}"
    return body


def _stop_body(symbol, trigger, quantity=1):
    return {
        "time-in-force": "GTC",
        "order-type": "Stop",
        "stop-trigger": f"{trigger:.2f}",
        "legs": [
            {"instrument-type": "Equity Option", "symbol": symbol, "quantity": quantity,
             "action": "Buy to Close"},
        ],
    }  # fmt: skip


def _credit(market, at=None, spot=None):
    if spot is None:
        return market.option_mark(PUT_5700) - market.option_mark(PUT_5690)
    return market.option_mark_at(PUT_5700, spot, at) - market.option_mark_at(PUT_5690, spot, at)


def test_a_limit_spread_fills_when_the_path_crosses_it():
    market, tasty = _setup()
    limit = round(_credit(market) * 1.3, 2)
    oid = tasty.place_order(TASTY_ACCOUNT, _spread_body(limit))
    assert tasty.get_order(TASTY_ACCOUNT, oid).status is OrderStatus.WORKING

    market.steer("SPX", 5700, by=T0 + timedelta(seconds=60))
    filled = tasty.match(market.advance_to(T0 + timedelta(seconds=90)))

    assert filled == [oid]
    order = tasty.get_order(TASTY_ACCOUNT, oid)
    assert order.status is OrderStatus.FILLED
    prices = {leg.symbol: leg.fill_price for leg in order.legs}
    assert prices[PUT_5700] - prices[PUT_5690] == Decimal(f"{limit:.2f}")
    held = tasty.option_positions(TASTY_ACCOUNT)
    assert held == {plain(PUT_5700): -1, plain(PUT_5690): 1}


def test_a_limit_that_is_not_reached_keeps_working():
    market, tasty = _setup()
    limit = round(_credit(market) * 3, 2)
    oid = tasty.place_order(TASTY_ACCOUNT, _spread_body(limit))
    assert tasty.match(market.advance_to(T0 + timedelta(seconds=30))) == []
    assert [o.broker_order_id for o in tasty.working_orders(TASTY_ACCOUNT)] == [oid]


def test_a_market_order_fills_on_the_next_tick():
    market, tasty = _setup()
    oid = tasty.place_order(TASTY_ACCOUNT, _spread_body(None, order_type="Market"))
    assert tasty.match(market.advance_to(T0 + timedelta(seconds=1))) == [oid]


def test_a_stop_fills_when_triggered():
    market, tasty = _setup()
    tasty.book.options[plain(PUT_5700)] = -1
    trigger = round(market.option_mark(PUT_5700) * 1.5, 2)
    oid = tasty.place_order(TASTY_ACCOUNT, _stop_body(PUT_5700, trigger))
    assert tasty.match(market.advance_to(T0 + timedelta(seconds=5))) == []

    market.steer("SPX", 5720, by=T0 + timedelta(seconds=60))
    assert tasty.match(market.advance_to(T0 + timedelta(seconds=90))) == [oid]

    order = tasty.get_order(TASTY_ACCOUNT, oid)
    assert order.status is OrderStatus.FILLED
    assert order.legs[0].fill_price >= Decimal(f"{trigger:.2f}")
    assert tasty.option_positions(TASTY_ACCOUNT) == {}


def test_jump_fills_orders_crossed_on_the_way():
    end = T0 + timedelta(minutes=15)
    twin = PretendMarket(seed=5, start=T0)
    marks = [
        (t.at, twin.option_mark_at(PUT_5700, t.price, t.at))
        for t in twin.advance_to(end)
        if t.ticker == "SPX"
    ]
    peak_at, peak = max(marks, key=lambda m: m[1])
    final = marks[-1][1]
    assert peak > final * 1.02 and peak_at < end  # the path goes there and comes back
    trigger = round((peak + final) / 2, 2)

    market, tasty = _setup(seed=5)
    tasty.book.options[plain(PUT_5700)] = -1
    oid = tasty.place_order(TASTY_ACCOUNT, _stop_body(PUT_5700, trigger))
    assert tasty.match(market.advance_to(end)) == [oid]  # one call, the whole jump

    [raw] = tasty.transactions(TASTY_ACCOUNT, DEMO_DAY, DEMO_DAY)
    assert raw.executed_at < end
    assert market.option_mark(PUT_5700) < trigger  # it wasn't there at the end


def test_fills_parse_as_tastytrade_transactions():
    market, tasty = _setup()
    tasty.place_order(TASTY_ACCOUNT, _spread_body(None, order_type="Market"))
    tasty.match(market.advance_to(T0 + timedelta(seconds=2)))

    raws = tasty.transactions(TASTY_ACCOUNT, DEMO_DAY, DEMO_DAY)
    assert len(raws) == 2
    for raw in raws:
        [fill] = parse_transaction(raw.data, TASTY_ACCOUNT)
        assert fill.fees == Decimal("1.72")  # 1.00 + 0.10 + 0.02 + 0.60
    cash = tasty.balance(TASTY_ACCOUNT).cash
    values = sum(parse_transaction(r.data, TASTY_ACCOUNT)[0].value for r in raws)
    assert cash == Book(TASTY_ACCOUNT).cash + values - Decimal("3.44")


def test_sync_all_brings_fills_into_the_journal(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    session_factory = make_session_factory(engine)
    market, tasty = _setup()
    oid = tasty.place_order(TASTY_ACCOUNT, _spread_body(None, order_type="Market"))
    tasty.match(market.advance_to(T0 + timedelta(seconds=2)))

    now = T0 + timedelta(minutes=1)
    [result] = sync_all([tasty], session_factory, now=lambda: now)
    assert result.ok and result.new == 2

    view = journal.load_journal(session_factory, now=now)
    keys = [e.position.key for e in view.entries]
    assert f"tastytrade:{TASTY_ACCOUNT}:{oid}" in keys


def test_preview_and_order_shapes_match_the_real_client():
    market, tasty = _setup()
    body = _spread_body(2.0)
    preview = tasty.preview_order(TASTY_ACCOUNT, body)
    assert "order" in preview and "buying-power-effect" in preview
    bp = preview["buying-power-effect"]
    assert Decimal(bp["isolated-order-margin-requirement"]) == Decimal("1000")
    assert preview["fee-calculation"]["total-fees-effect"] == "Debit"
    assert tasty.working_orders(TASTY_ACCOUNT) == []  # a preview places nothing

    oid = tasty.place_order(TASTY_ACCOUNT, {**body, "external-identifier": "bot-7"})
    order = tasty.get_order(TASTY_ACCOUNT, oid)
    assert isinstance(order, BrokerOrder) and len(order.legs) == 2
    assert order.tag == "bot-7" and order.entered_at == T0

    new = tasty.replace_order(TASTY_ACCOUNT, oid, _spread_body(2.1))
    old = tasty.get_order(TASTY_ACCOUNT, oid)
    assert old.status is OrderStatus.CANCELLED and old.replaced_by == new
    tasty.cancel_order(TASTY_ACCOUNT, new)
    assert tasty.get_order(TASTY_ACCOUNT, new).status is OrderStatus.CANCELLED
    assert {o.broker_order_id for o in tasty.recent_orders(TASTY_ACCOUNT)} == {oid, new}
    with pytest.raises(BrokerError):
        tasty.cancel_order(TASTY_ACCOUNT, new)
    with pytest.raises(BrokerError):
        tasty.get_order(TASTY_ACCOUNT, "999")


def test_closing_more_than_is_held_is_refused():
    _, tasty = _setup()
    with pytest.raises(BrokerError):
        tasty.place_order(TASTY_ACCOUNT, _stop_body(PUT_5700, 5.0))


def test_accounts_balance_and_tick_sizes():
    _, tasty = _setup()
    assert tasty.name == "TastyTrade" and tasty.broker == "tastytrade"
    assert tasty.quote_token() == ("demo", "demo://")
    [account] = tasty.accounts()
    assert isinstance(account, BrokerAccount) and account.number == "5WX00001"
    balance = tasty.balance(TASTY_ACCOUNT)
    assert isinstance(balance, Balance) and balance.net_liquidating_value == balance.cash
    assert tasty.equity_tick_sizes("SPX")[0] == {"threshold": "3.0", "value": "0.05"}
    assert tasty.equity_tick_sizes("QQQ") == [{"value": "0.01"}]


def test_chain_lists_the_0dte_and_weeklies():
    market, tasty = _setup()
    [spx] = tasty.option_chain("SPX")
    assert spx["root-symbol"] == "SPXW"
    days = [e["expiration-date"] for e in spx["expirations"]]
    assert days == [DEMO_DAY.isoformat(), "2026-10-23", "2026-10-30", "2026-11-06", "2026-11-13",
                    "2026-11-20"]  # fmt: skip
    strikes = spx["expirations"][0]["strikes"]
    prices = [Decimal(s["strike-price"]) for s in strikes]
    spot = Decimal(str(market.prev_close("SPX")))
    assert spot * Decimal("0.9") <= prices[0] < spot * Decimal("0.9") + 5
    assert spot * Decimal("1.1") - 5 < prices[-1] <= spot * Decimal("1.1")
    assert all(b - a == 5 for a, b in zip(prices, prices[1:], strict=False))
    row = next(s for s in strikes if Decimal(s["strike-price"]) == 5700)
    assert row["put"] == PUT_5700 and row["call"] == "SPXW  261021C05700000"

    [qqq] = tasty.option_chain("QQQ")
    assert [e["expiration-date"] for e in qqq["expirations"]][0] == "2026-10-23"
    qprices = [Decimal(s["strike-price"]) for s in qqq["expirations"][0]["strikes"]]
    assert qprices[1] - qprices[0] == 1
    assert tasty.option_chain("NOPE") == []


def test_market_data_and_metrics():
    market, tasty = _setup()
    items = tasty.market_data({"index": ["SPX"], "equity-option": [PUT_5700]})
    by = {i["symbol"]: i for i in items}
    assert float(by["SPX"]["last"]) == pytest.approx(market.last("SPX"))
    assert float(by[PUT_5700]["bid"]) < float(by[PUT_5700]["ask"])
    assert by["SPX"]["prev-close"] is not None
    [metrics] = tasty.market_metrics(["SPY"])
    assert metrics["symbol"] == "SPY" and "earnings" not in metrics


def test_schwab_holds_stocks_and_charts_but_no_chains():
    market = PretendMarket(seed=1, start=T0)
    book = Book(SCHWAB_ACCOUNT)
    book.stocks["AAPL"] = (Decimal("10"), Decimal("200"))
    schwab = PretendSchwab(market, book)
    assert schwab.name == "Schwab" and schwab.broker == "schwab"
    assert not hasattr(schwab, "option_expirations") and not hasattr(schwab, "streamer_info")
    assert schwab.accounts()[0].number == "D0000001"
    [held] = schwab.stock_positions()
    assert held.ticker == "AAPL" and held.account == "schwab:D0000001"
    assert schwab.option_positions(SCHWAB_ACCOUNT) == {}
    assert schwab.working_orders(SCHWAB_ACCOUNT) == []
    assert schwab.recent_orders(SCHWAB_ACCOUNT, T0) == []
    quote = schwab.quotes(["$SPX", "AAPL"])
    assert quote["$SPX"]["quote"]["lastPrice"] == pytest.approx(market.last("SPX"))
    assert "AAPL" in quote
    start = int((T0 - timedelta(days=1)).timestamp() * 1000)
    rows = schwab.price_history(
        "$SPX", {"frequencyType": "minute", "frequency": 5, "startDate": start}
    )
    assert rows and {"datetime", "open", "high", "low", "close", "volume"} <= set(rows[0])
    assert all(r["datetime"] >= start for r in rows)


def test_the_close_settles_todays_options_in_cash(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    session_factory = make_session_factory(engine)
    market, tasty = _setup()
    oid = tasty.place_order(TASTY_ACCOUNT, _spread_body(None, order_type="Market"))
    tasty.match(market.advance_to(T0 + timedelta(seconds=2)))
    cash = tasty.book.cash

    close = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    market.steer("SPX", 5650, by=close - timedelta(hours=1))
    market.advance_to(close)
    spot = Decimal(f"{market.last('SPX'):.2f}")
    assert tasty.close_the_day(market.now)
    assert tasty.book.options == {}
    # short 5700 assigned, long 5690 exercised: the spread's width, both in the money
    assert spot < 5690
    assert tasty.book.cash == cash - (Decimal(5700) - spot) * 100 + (Decimal(5690) - spot) * 100
    settled = [parse_transaction(t.data, TASTY_ACCOUNT)[0] for t in tasty.book.transactions[2:]]
    assert {f.kind.name for f in settled} == {"ASSIGNMENT", "EXERCISE"}

    now = close + timedelta(minutes=1)
    sync_all([tasty], session_factory, now=lambda: now)
    view = journal.load_journal(session_factory, now=now)
    [entry] = [e for e in view.entries if e.position.key == f"tastytrade:{TASTY_ACCOUNT}:{oid}"]
    assert entry.position.status.value == "closed"
    assert not tasty.close_the_day(market.now)  # nothing left to do


def test_the_close_expires_worthless_options_and_day_orders():
    market, tasty = _setup()
    tasty.place_order(TASTY_ACCOUNT, _spread_body(None, order_type="Market"))
    tasty.match(market.advance_to(T0 + timedelta(seconds=2)))
    day = tasty.place_order(TASTY_ACCOUNT, _spread_body(50.0))
    stop = tasty.place_order(TASTY_ACCOUNT, _stop_body(PUT_5700, 40.0))  # GTC, on a 0DTE
    weekly = _spread_body(50.0)
    weekly["time-in-force"] = "GTC"
    for leg, strike in zip(weekly["legs"], ("05700", "05690"), strict=True):
        leg["symbol"] = f"SPXW  261023P{strike}000"
    gtc = tasty.place_order(TASTY_ACCOUNT, weekly)
    market.advance_to(datetime(2026, 10, 21, 20, 0, tzinfo=UTC))
    assert market.last("SPX") > 5700  # both legs out of the money
    cash = tasty.book.cash

    assert tasty.close_the_day(market.now)
    assert tasty.get_order(TASTY_ACCOUNT, day).status is OrderStatus.EXPIRED
    assert tasty.get_order(TASTY_ACCOUNT, stop).status is OrderStatus.EXPIRED
    assert tasty.get_order(TASTY_ACCOUNT, gtc).status is OrderStatus.WORKING
    assert tasty.book.cash == cash and tasty.book.options == {}
    expired = [t.data for t in tasty.book.transactions[2:]]
    assert [t["transaction-sub-type"] for t in expired] == ["Expiration", "Expiration"]
    assert {t["symbol"] for t in expired} == {PUT_5700, PUT_5690}


def test_schwab_daily_closes_has_the_real_clients_shape():
    """Final review I-3: SchwabClient.daily_closes(symbol, start, end) -> [DailyClose],
    for the bots' prior-close filters and the expiry prices."""
    from datetime import date

    from optionsjourney.core.accounts import DailyClose

    market = PretendMarket(seed=1, start=T0)
    schwab = PretendSchwab(market, Book(SCHWAB_ACCOUNT))
    closes = schwab.daily_closes("$VIX", date(2026, 10, 19), date(2026, 10, 21))
    assert all(isinstance(c, DailyClose) and c.symbol == "$VIX" for c in closes)
    assert [c.day for c in closes] == [date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)]
    assert closes[1].close == Decimal(str(round(market.prev_close("VIX"), 2)))
    assert schwab.daily_closes("SPX", date(2026, 10, 20), date(2026, 10, 20))[0].day == date(
        2026, 10, 20
    )
    assert schwab.daily_closes("ZZZZ", date(2026, 10, 1), date(2026, 10, 21)) == []
