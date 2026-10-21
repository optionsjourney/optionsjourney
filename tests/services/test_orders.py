"""The one path an order takes to a broker (spec: the order path)."""

import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DivisionByZero

import pytest
from sqlalchemy import select

from optionsjourney.brokers.http import BrokerError, RequestUnclear
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import Bot, BotRun, OrderLog, Setting, Tag, TickSize
from optionsjourney.services import orders
from optionsjourney.trading.orders import (
    BrokerLeg,
    BrokerOrder,
    Leg,
    Order,
    OrderError,
    OrderStatus,
    Side,
    plain,
)

NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
SHORT = "SPXW  261013P05800000"
LONG = "SPXW  261013P05790000"
OPEN = Order((Leg(SHORT, Side.SELL), Leg(LONG, Side.BUY)), 1, True, price=Decimal("1.50"))
CLOSE = Order((Leg(SHORT, Side.BUY), Leg(LONG, Side.SELL)), 1, False, price=Decimal("-0.50"))


class FakeBroker:
    """A broker client with every order call; records calls (and reads), raises on request."""

    def __init__(self, broker="tastytrade", held=None, working=(), recent=(), errors=None,
                 preview_delay=0.0):  # fmt: skip
        self.broker = broker
        self.name = "TastyTrade" if broker == "tastytrade" else "Schwab"
        self.calls = []
        self.reads = []
        self.preview_delay = preview_delay
        self.held = {plain(SHORT): -1, plain(LONG): 1} if held is None else held
        self.working, self.recent, self.errors = list(working), list(recent), errors or {}
        self.ticks = {}

    def _maybe_raise(self, name):
        if name in self.errors:
            raise self.errors[name]

    def preview_order(self, account, body):
        self.calls.append(("preview", account))
        time.sleep(self.preview_delay)
        self._maybe_raise("preview")
        return {"buying-power-effect": {"change-in-buying-power": "850"}}

    def place_order(self, account, body):
        self.calls.append(("place", account, body))
        self._maybe_raise("place")
        return "777"

    def replace_order(self, account, order_id, body):
        self.calls.append(("replace", account, order_id))
        self._maybe_raise("replace")
        return "778"

    def cancel_order(self, account, order_id):
        self.calls.append(("cancel", account, order_id))
        self._maybe_raise("cancel")

    def get_order(self, account, order_id):
        self.reads.append("get_order")
        self._maybe_raise("get_order")
        return next(o for o in self.recent + self.working if o.broker_order_id == order_id)

    def recent_orders(self, account, since=None):
        self.reads.append("recent")
        self._maybe_raise("recent")
        return self.recent

    def working_orders(self, account):
        self.reads.append("working")
        self._maybe_raise("working")
        return self.working

    def option_positions(self, account):
        self.reads.append("positions")
        self._maybe_raise("positions")
        return self.held

    def equity_tick_sizes(self, symbol):
        self.calls.append(("ticks", symbol))
        self._maybe_raise("ticks")
        return self.ticks.get(symbol, [])

    def future_tick_sizes(self, contract):
        self.reads.append("ticks")
        return []

    def futures_option_tick_sizes(self, product):
        self.reads.append("ticks")
        return {}


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def make_bot(factory, live=True, max_per_trade=2, max_per_day=3, broker="tastytrade",
             account="5WX01234"):  # fmt: skip
    with factory() as session:
        tag = session.scalar(select(Tag).where(Tag.name == "0DTE"))
        bot = Bot(name="SPX put spread", tag_id=tag.id, broker=broker,
                  account_number=account, live=live, enabled=True, max_per_trade=max_per_trade,
                  max_per_day=max_per_day, created_at=NOW)  # fmt: skip
        session.add(bot)
        session.commit()
        return bot.id


def log(factory):
    with factory() as session:
        return list(session.scalars(select(OrderLog).order_by(OrderLog.id)))


def test_switches_start_off_and_can_be_set(factory):
    assert orders.switches(factory) == orders.Switches()
    orders.set_switch(factory, "no_new_trades", True, NOW)
    orders.set_switch(factory, "freeze", True, NOW)
    assert orders.switches(factory) == orders.Switches(no_new_trades=True, freeze=True)
    orders.set_switch(factory, "freeze", False, NOW)
    assert not orders.switches(factory).freeze
    with pytest.raises(KeyError):
        orders.set_switch(factory, "stop_everything", True, NOW)


def test_switches_fail_closed(factory):
    with factory() as session:
        session.add(Setting(key="_no_new_trades", value="yes", updated_at=NOW))
        session.commit()
    assert orders.switches(factory) == orders.Switches()  # only "on" is on

    def broken():
        raise RuntimeError("database is locked")

    assert orders.switches(broken).freeze


def test_tick_tables_are_fetched_once_and_kept(factory):
    tasty = FakeBroker()
    tasty.ticks["AAPL"] = [{"threshold": "3.0", "value": "0.05"}, {"value": "0.10"}]
    aapl = Order((Leg("AAPL  261016C00250000", Side.SELL),), 1, True, price=Decimal("2.50"))
    first = orders.tick_tables(factory, [tasty], aapl, NOW)
    second = orders.tick_tables(factory, [tasty], aapl, NOW + timedelta(days=1))
    assert first == second and "AAPL" in first
    assert tasty.calls.count(("ticks", "AAPL")) == 1
    orders.tick_tables(factory, [tasty], aapl, NOW + timedelta(days=8))  # stale: fetched again
    assert tasty.calls.count(("ticks", "AAPL")) == 2


def test_tick_tables_fall_back_when_fetching_fails(factory):
    tasty = FakeBroker(errors={"ticks": BrokerError("TastyTrade answered 500")})
    aapl = Order((Leg("AAPL  261016C00250000", Side.SELL),), 1, True, price=Decimal("2.50"))
    assert orders.tick_tables(factory, [tasty], aapl, NOW) == {}
    with factory() as session:
        assert session.scalar(select(TickSize)) is None


def test_a_failed_tick_fetch_waits_five_minutes_before_asking_again(factory):
    tasty = FakeBroker(errors={"ticks": BrokerError("TastyTrade answered 500")})
    aapl = Order((Leg("AAPL  261016C00250000", Side.SELL),), 1, True, price=Decimal("2.50"))
    orders.tick_tables(factory, [tasty], aapl, NOW)
    orders.tick_tables(factory, [tasty], aapl, NOW + timedelta(minutes=4))
    assert tasty.calls.count(("ticks", "AAPL")) == 1
    orders.tick_tables(factory, [tasty], aapl, NOW + timedelta(minutes=6))
    assert tasty.calls.count(("ticks", "AAPL")) == 2


def test_a_stale_table_is_used_while_tastytrade_is_down(factory):
    tasty = FakeBroker()
    tasty.ticks["AAPL"] = [{"threshold": "3.0", "value": "0.05"}, {"value": "0.10"}]
    aapl = Order((Leg("AAPL  261016C00250000", Side.SELL),), 1, True, price=Decimal("2.50"))
    orders.tick_tables(factory, [tasty], aapl, NOW)
    tasty.errors["ticks"] = BrokerError("TastyTrade answered 500")
    assert "AAPL" in orders.tick_tables(factory, [tasty], aapl, NOW + timedelta(days=8))


def bot_request(factory, order=OPEN, **bot):
    return orders.Request("tastytrade", "5WX01234", "bot", order, bot_id=make_bot(factory, **bot))


def you_close(order=CLOSE, broker="tastytrade", account="5WX01234"):
    return orders.Request(broker, account, "you", order)


def test_preview_mode_only_previews(factory):
    tasty = FakeBroker()
    result = orders.submit(factory, [tasty], bot_request(factory, live=False), now=NOW)
    assert result.action == "preview" and result.ok and "850" in result.message
    assert [c[0] for c in tasty.calls] == ["preview"]
    assert log(factory)[0].action == "preview"


def test_a_live_order_is_previewed_then_sent_once_and_logged(factory):
    tasty = FakeBroker()
    result = orders.submit(factory, [tasty], bot_request(factory), now=NOW)
    assert result.action == "send" and result.ok and result.broker_order_id == "777"
    assert [c[0] for c in tasty.calls] == ["preview", "place"]
    row = log(factory)[0]
    assert (row.origin, row.opening, row.broker_order_id, row.order_type) == (
        "bot",
        True,
        "777",
        "limit",
    )
    assert row.order["order"]["price"] == "1.50" and row.order["body"]["price-effect"] == "Credit"
    assert row.tag and tasty.calls[1][2]["external-identifier"] == row.tag


def test_a_failed_preview_stops_a_live_order(factory):
    tasty = FakeBroker(
        errors={"preview": BrokerError("TastyTrade refused it: Insufficient buying power")}
    )
    result = orders.submit(factory, [tasty], bot_request(factory), now=NOW)
    assert not result.ok and "Insufficient" in result.message
    assert [c[0] for c in tasty.calls] == ["preview"]


def test_a_clean_rejection_is_logged_and_not_retried(factory):
    tasty = FakeBroker(errors={"place": BrokerError("TastyTrade refused it (422): market closed")})
    result = orders.submit(factory, [tasty], bot_request(factory), now=NOW)
    assert not result.ok and result.message.startswith("Not sent")
    assert [c[0] for c in tasty.calls].count("place") == 1


def test_an_unclear_send_is_logged_alerted_counted_and_not_retried(factory):
    alerts = []
    tasty = FakeBroker(errors={"place": RequestUnclear("Lost the connection to TastyTrade")})
    request = bot_request(factory)
    result = orders.submit(factory, [tasty], request, now=NOW, notify=alerts.append)
    assert not result.ok and result.message.startswith(orders.UNCLEAR)
    assert [c[0] for c in tasty.calls].count("place") == 1
    assert orders.used_today(factory, request.bot_id, NOW) == 1
    assert alerts and "unclear" in alerts[0].title


def test_no_new_trades_refuses_an_opening_order_without_any_broker_read(factory):
    tasty = FakeBroker()
    orders.set_switch(factory, "no_new_trades", True, NOW)
    result = orders.submit(factory, [tasty], bot_request(factory), now=NOW)
    assert result.action == "refuse" and "No new trades" in result.message
    assert tasty.calls == [] and tasty.reads == [] and log(factory)[0].action == "refuse"


def test_freeze_is_checked_before_any_broker_read(factory):
    unclear_send(factory)  # an unclear row: clearing it up would read the broker's orders
    orders.set_switch(factory, "freeze", True, NOW)
    tasty = FakeBroker()
    result = orders.submit(factory, [tasty], you_close(), now=NOW + timedelta(minutes=2))
    assert result.action == "refuse" and "frozen" in result.message
    assert tasty.calls == [] and tasty.reads == []
    assert log(factory)[-1].action == "refuse"


def test_a_closing_order_from_a_button_is_checked_against_the_broker(factory):
    tasty = FakeBroker()
    assert orders.submit(factory, [tasty], you_close(), now=NOW).ok
    over = you_close(Order(CLOSE.legs, 2, False, price=Decimal("-0.50")))
    assert orders.submit(factory, [FakeBroker()], over, now=NOW).action == "refuse"


def test_a_closing_order_is_refused_and_alerted_when_working_orders_are_unknown(factory):
    alerts = []
    tasty = FakeBroker(errors={"working": BrokerError("TastyTrade answered 500")})
    result = orders.submit(factory, [tasty], you_close(), now=NOW, notify=alerts.append)
    assert result.action == "refuse" and result.message.startswith("Couldn't check")
    assert alerts and "wasn't sent" in alerts[0].title


def test_a_price_that_rounds_to_zero_is_refused(factory):
    cheap = Order(CLOSE.legs, 1, False, price=Decimal("-0.03"))
    result = orders.submit(factory, [FakeBroker()], you_close(cheap), now=NOW)
    # a debit rounds to a larger amount (-0.05), so it is on tick and goes through
    assert result.ok
    credit = Order(OPEN.legs, 1, True, price=Decimal("0.03"))
    result = orders.submit(factory, [FakeBroker()], bot_request(factory, credit), now=NOW)
    assert result.action == "refuse" and "zero" in result.message


def test_a_bot_order_must_use_the_bots_account(factory):
    request = orders.Request("tastytrade", "5WY05678", "bot", OPEN, bot_id=make_bot(factory))
    assert orders.submit(factory, [FakeBroker()], request, now=NOW).action == "refuse"


def test_schwab_orders_go_through_schwab_without_a_tag(factory):
    schwab = FakeBroker("schwab")
    request = you_close(broker="schwab", account="12345678")
    result = orders.submit(factory, [schwab], request, now=NOW)
    assert result.ok and schwab.calls[-1][2]["orderType"] == "NET_DEBIT"
    assert log(factory)[0].tag is None


def test_a_broker_that_isnt_connected(factory):
    result = orders.submit(factory, [], you_close(), now=NOW)
    assert result.action == "refuse" and "isn't connected" in result.message


def test_replace_leaves_the_replaced_order_out_of_the_count(factory):
    resting = BrokerOrder("500", OrderStatus.WORKING, (BrokerLeg(SHORT, Side.BUY, False, 1, 1),))
    tasty = FakeBroker(working=[resting])
    single = Order((Leg(SHORT, Side.BUY),), 1, False, price=Decimal("-0.60"))
    result = orders.replace(factory, [tasty], you_close(single), "500", now=NOW)
    assert result.ok and result.action == "replace" and result.broker_order_id == "778"
    assert log(factory)[0].replaces_id == "500"


def test_cancel_and_freeze(factory):
    tasty = FakeBroker()
    assert orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "500", "you", now=NOW).ok
    orders.set_switch(factory, "freeze", True, NOW)
    frozen = orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "501", "you", now=NOW)
    assert frozen.action == "refuse" and "frozen" in frozen.message
    assert [c for c in tasty.calls if c[0] == "cancel"] == [("cancel", "5WX01234", "500")]


def test_an_unreadable_answer_to_a_send_is_treated_as_unclear(factory):
    alerts = []
    tasty = FakeBroker(errors={"place": ValueError("not json")})
    request = bot_request(factory)
    result = orders.submit(factory, [tasty], request, now=NOW, notify=alerts.append)
    assert not result.ok and result.message.startswith(orders.UNCLEAR)
    assert log(factory)[0].result.startswith(orders.UNCLEAR)
    assert orders.used_today(factory, request.bot_id, NOW) == 1
    assert alerts


def test_an_unreadable_answer_to_a_cancel_is_treated_as_unclear(factory):
    alerts = []
    tasty = FakeBroker(errors={"cancel": AttributeError("no data")})
    result = orders.cancel(
        factory, [tasty], "tastytrade", "5WX01234", "500", "you", now=NOW, notify=alerts.append
    )
    assert not result.ok and result.message.startswith(orders.UNCLEAR)
    assert log(factory)[0].result.startswith(orders.UNCLEAR) and alerts


def unclear_send(factory, broker=None):
    tasty = broker or FakeBroker(errors={"place": RequestUnclear("Lost the connection")})
    orders.submit(factory, [tasty], you_close(), now=NOW, notify=lambda e: None)
    return log(factory)[-1]


def found_like(row, order_id="900", minutes=0, tag="use-row"):
    return BrokerOrder(
        order_id,
        OrderStatus.WORKING,
        (BrokerLeg(SHORT, Side.BUY, False, 1, 1), BrokerLeg(LONG, Side.SELL, False, 1, 1)),
        tag=row.tag if tag == "use-row" else tag,
        entered_at=NOW + timedelta(minutes=minutes),
    )


def test_an_unclear_send_blocks_its_legs(factory):
    unclear_send(factory)
    tasty = FakeBroker(errors={"recent": BrokerError("TastyTrade answered 500")})
    result = orders.submit(factory, [tasty], you_close(), now=NOW + timedelta(seconds=10))
    assert result.action == "refuse" and "unclear" in result.message


def test_an_unclear_send_found_at_the_broker_is_confirmed(factory):
    row = unclear_send(factory)
    tasty = FakeBroker(recent=[found_like(row)])
    assert orders.resolve_unclear(factory, [tasty], "tastytrade", "5WX01234", NOW) == 0
    confirmed = log(factory)[0]
    assert confirmed.cleared_at and confirmed.ok and confirmed.broker_order_id == "900"


def test_a_different_tag_is_not_ours(factory):
    row = unclear_send(factory)
    tasty = FakeBroker(recent=[found_like(row, tag="someone-else")])
    assert orders.resolve_unclear(factory, [tasty], "tastytrade", "5WX01234", NOW) == 1


def test_an_unclear_send_not_at_the_broker_a_minute_later_was_not_placed(factory):
    unclear_send(factory)
    tasty = FakeBroker(recent=[])
    assert (
        orders.resolve_unclear(
            factory, [tasty], "tastytrade", "5WX01234", NOW + timedelta(seconds=30)
        )
        == 1
    )
    assert (
        orders.resolve_unclear(
            factory, [tasty], "tastytrade", "5WX01234", NOW + timedelta(minutes=2)
        )
        == 0
    )
    row = log(factory)[0]
    assert row.cleared_at and not row.ok and "wasn't placed" in row.result


def test_schwab_unclear_sends_match_on_legs_and_time(factory):
    schwab = FakeBroker("schwab", errors={"place": RequestUnclear("Lost the connection")})
    orders.submit(factory, [schwab], you_close(broker="schwab", account="12345678"), now=NOW,
                  notify=lambda e: None)  # fmt: skip
    row = log(factory)[0]
    late = FakeBroker("schwab", recent=[found_like(row, minutes=20, tag=None)])
    assert (
        orders.resolve_unclear(factory, [late], "schwab", "12345678", NOW + timedelta(seconds=5))
        == 1
    )
    near = FakeBroker("schwab", recent=[found_like(row, minutes=1, tag=None)])
    assert (
        orders.resolve_unclear(factory, [near], "schwab", "12345678", NOW + timedelta(seconds=5))
        == 0
    )


def test_you_can_clear_an_unclear_row(factory):
    row = unclear_send(factory)
    assert orders.clear(factory, row.id, NOW)
    assert not orders.clear(factory, row.id, NOW)  # already cleared
    assert "Cleared by you" in log(factory)[0].result
    assert orders.submit(factory, [FakeBroker()], you_close(), now=NOW).ok


def test_the_schwab_app_tag_is_learned_from_the_first_order(factory):
    placed = BrokerOrder("777", OrderStatus.WORKING, (), tag="TA_optionsjourney")
    schwab = FakeBroker("schwab", recent=[placed])
    orders.submit(factory, [schwab], you_close(broker="schwab", account="12345678"), now=NOW)
    with factory() as session:
        assert session.get(Setting, "_schwab_tag:12345678").value == "TA_optionsjourney"


def test_a_failure_learning_the_schwab_tag_cannot_lose_the_send_log(factory):
    schwab = FakeBroker("schwab", errors={"get_order": RuntimeError("boom")})
    result = orders.submit(
        factory, [schwab], you_close(broker="schwab", account="12345678"), now=NOW
    )
    assert result.ok and result.broker_order_id == "777"
    sent = [r for r in log(factory) if r.action == "send"]
    assert len(sent) == 1 and sent[0].ok and sent[0].result == "Sent."


def test_an_unclear_schwab_replace_is_not_confirmed_against_the_order_it_replaces(factory):
    schwab = FakeBroker("schwab", errors={"replace": RequestUnclear("Lost the connection")})
    orders.replace(
        factory, [schwab], you_close(broker="schwab", account="12345678"), "A1", now=NOW,
        notify=lambda e: None,
    )  # fmt: skip
    row = log(factory)[0]
    original = found_like(row, order_id="A1", minutes=-1, tag=None)
    client = FakeBroker("schwab", recent=[original])
    assert orders.resolve_unclear(factory, [client], "schwab", "12345678", NOW) == 1
    assert log(factory)[0].cleared_at is None and not log(factory)[0].ok


def test_two_unclear_schwab_sends_are_not_both_confirmed_by_one_order(factory):
    schwab = FakeBroker("schwab", errors={"place": RequestUnclear("Lost the connection")})
    request = you_close(broker="schwab", account="12345678")
    orders.submit(factory, [schwab], request, now=NOW, notify=lambda e: None)
    with factory() as session:  # the second, kept apart from the first by the block
        first = session.scalars(select(OrderLog)).one()
        session.add(
            OrderLog(
                created_at=NOW + timedelta(seconds=20), broker="schwab",
                account_number="12345678", origin="you", action="send", opening=False,
                quantity=1, price=first.price, order_type=first.order_type, order=first.order,
                result=first.result, ok=False,
            )
        )  # fmt: skip
        session.commit()
    row = log(factory)[0]
    client = FakeBroker("schwab", recent=[found_like(row, tag=None)])
    assert orders.resolve_unclear(factory, [client], "schwab", "12345678", NOW) == 1
    assert sum(1 for r in log(factory) if r.ok) == 1


def test_an_unclear_tastytrade_send_is_not_called_unplaced_the_next_day(factory):
    unclear_send(factory)
    tasty = FakeBroker(recent=[])
    later = NOW + timedelta(days=1)
    assert orders.resolve_unclear(factory, [tasty], "tastytrade", "5WX01234", later) == 1
    assert log(factory)[0].cleared_at is None


def test_system_alerts_show_unclear_orders_until_cleared(factory):
    from optionsjourney.services.alerts import system_alerts

    row = unclear_send(factory)
    assert any("unclear" in a.title for a in system_alerts(factory))
    orders.clear(factory, row.id)
    assert not any("unclear" in a.title for a in system_alerts(factory))


def test_two_closes_at_once_send_only_one(factory):
    tasty = FakeBroker(preview_delay=0.05)
    results = []

    def close():
        results.append(orders.submit(factory, [tasty], you_close(), now=NOW))

    threads = [threading.Thread(target=close) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [c[0] for c in tasty.calls].count("place") == 1
    assert sorted(r.action for r in results) == ["refuse", "send"]


def test_a_close_right_after_a_logged_close_is_refused_while_the_broker_catches_up(factory):
    assert orders.submit(factory, [FakeBroker()], you_close(), now=NOW).ok
    lagging = FakeBroker()  # its working list doesn't show order 777 yet
    again = orders.submit(factory, [lagging], you_close(), now=NOW + timedelta(minutes=1))
    assert again.action == "refuse" and "more than" in again.message
    assert "place" not in [c[0] for c in lagging.calls]
    shown = BrokerOrder(
        "777", OrderStatus.WORKING,
        (BrokerLeg(SHORT, Side.BUY, False, 1, 1), BrokerLeg(LONG, Side.SELL, False, 1, 1)),
    )  # fmt: skip
    listed = orders.submit(factory, [FakeBroker(working=[shown])], you_close(), now=NOW)
    assert listed.action == "refuse"  # counted once, from the broker's list
    later = orders.submit(factory, [FakeBroker()], you_close(), now=NOW + timedelta(minutes=6))
    assert later.ok  # after 5 minutes the broker's list is trusted alone


def test_replacing_your_own_recent_close_is_not_counted_against_itself(factory):
    assert orders.submit(factory, [FakeBroker()], you_close(), now=NOW).ok
    result = orders.replace(
        factory, [FakeBroker()], you_close(), "777", now=NOW + timedelta(seconds=30)
    )
    assert result.ok and result.action == "replace"
    # the replacement (778) now covers the position; 777 is gone
    third = orders.submit(factory, [FakeBroker()], you_close(), now=NOW + timedelta(minutes=1))
    assert third.action == "refuse" and "more than" in third.message


def test_a_tick_fetch_that_breaks_refuses_and_logs(factory):
    tasty = FakeBroker(errors={"ticks": RuntimeError("boom")})
    aapl = Order((Leg("AAPL  261016C00250000", Side.SELL),), 1, True, price=Decimal("2.50"))
    result = orders.submit(factory, [tasty], bot_request(factory, aapl), now=NOW)
    assert result.action == "refuse" and not result.ok and "RuntimeError" in result.message
    assert "place" not in [c[0] for c in tasty.calls] and log(factory)[-1].action == "refuse"


def test_positions_that_break_refuse_a_closing_order_and_alert(factory):
    alerts = []
    tasty = FakeBroker(errors={"positions": ValueError("time data 'x' does not match")})
    result = orders.submit(factory, [tasty], you_close(), now=NOW, notify=alerts.append)
    assert result.action == "refuse" and result.message.startswith(orders.COULDNT_CHECK)
    assert log(factory)[-1].result.startswith(orders.COULDNT_CHECK)
    assert alerts and "wasn't sent" in alerts[0].title and tasty.calls == []


def test_a_translator_error_cannot_hide_a_couldnt_check_refusal(factory, monkeypatch):
    def broken(*args):
        raise OrderError("can't format")

    monkeypatch.setattr(orders, "_body", broken)
    alerts = []
    tasty = FakeBroker(errors={"working": BrokerError("TastyTrade answered 500")})
    result = orders.submit(factory, [tasty], you_close(), now=NOW, notify=alerts.append)
    assert result.message.startswith(orders.COULDNT_CHECK) and alerts


def test_a_translator_error_on_a_passing_order_is_a_logged_refusal(factory, monkeypatch):
    def broken(*args):
        raise IndexError("string index out of range")

    monkeypatch.setattr(orders, "_body", broken)
    tasty = FakeBroker()
    result = orders.submit(factory, [tasty], you_close(), now=NOW, notify=lambda e: None)
    assert result.action == "refuse" and not result.ok and tasty.calls == []
    assert log(factory)[-1].action == "refuse"


def test_a_rounding_error_is_a_logged_refusal(factory, monkeypatch):
    def broken(order, tables):
        raise DivisionByZero()

    monkeypatch.setattr(orders.ticks, "rounded", broken)
    result = orders.submit(factory, [FakeBroker()], bot_request(factory), now=NOW)
    assert result.action == "refuse" and log(factory)[-1].action == "refuse"


def test_a_legacy_unclear_row_does_not_break_later_orders(factory):
    with factory() as session:
        session.add(
            OrderLog(
                created_at=NOW - timedelta(days=2), broker="tastytrade",
                account_number="5WX01234", origin="bot", action="send", opening=True,
                quantity=1, price=Decimal("1.50"), order_type="limit",
                order={"order-type": "Limit"}, result="Unclear: lost the connection", ok=False,
            )
        )  # fmt: skip
        session.commit()
    result = orders.submit(factory, [FakeBroker()], you_close(), now=NOW)
    assert result.ok and result.action == "send"
    assert log(factory)[0].cleared_at is None  # left for you to clear


def test_resolving_survives_odd_broker_answers(factory):
    orders.cancel(
        factory, [FakeBroker(errors={"cancel": RequestUnclear("Lost the connection")})],
        "tastytrade", "5WX01234", "500", "you", now=NOW, notify=lambda e: None,
    )  # fmt: skip
    odd = FakeBroker(errors={"get_order": RuntimeError("boom")})
    assert orders.resolve_unclear(factory, [odd], "tastytrade", "5WX01234", NOW) == 1
    broken = FakeBroker(errors={"recent": ValueError("not json")})
    assert orders.resolve_unclear(factory, [broken], "tastytrade", "5WX01234", NOW) == 1


def test_a_missing_bot_is_a_logged_refusal(factory):
    request = orders.Request("tastytrade", "5WX01234", "bot", OPEN, bot_id=999)
    result = orders.submit(factory, [FakeBroker()], request, now=NOW)
    assert result.action == "refuse" and "999" in result.message
    assert log(factory)[-1].action == "refuse"


def test_an_unknown_origin_is_a_logged_refusal(factory):
    request = orders.Request("tastytrade", "5WX01234", "robot", CLOSE)
    result = orders.submit(factory, [FakeBroker()], request, now=NOW, notify=lambda e: None)
    assert result.action == "refuse" and log(factory)[-1].action == "refuse"


def test_a_preview_that_breaks_is_logged_and_not_sent(factory):
    tasty = FakeBroker(errors={"preview": RuntimeError("boom")})
    result = orders.submit(factory, [tasty], bot_request(factory), now=NOW)
    assert not result.ok and "place" not in [c[0] for c in tasty.calls]
    assert log(factory)[-1].ok is False


def test_an_unclear_tastytrade_replace_is_logged_alerted_and_blocks_its_legs(factory):
    alerts = []
    resting = BrokerOrder("500", OrderStatus.WORKING, (BrokerLeg(SHORT, Side.BUY, False, 1, 1),))
    tasty = FakeBroker(working=[resting], errors={"replace": RequestUnclear("Lost the connection")})
    single = Order((Leg(SHORT, Side.BUY),), 1, False, price=Decimal("-0.60"))
    result = orders.replace(factory, [tasty], you_close(single), "500", now=NOW,
                            notify=alerts.append)  # fmt: skip
    assert not result.ok and result.action == "replace"
    row = log(factory)[-1]
    assert row.result.startswith(orders.UNCLEAR) and row.replaces_id == "500"
    assert alerts and "unclear" in alerts[0].title
    blocked = FakeBroker(errors={"recent": BrokerError("TastyTrade answered 500")})
    again = orders.submit(factory, [blocked], you_close(), now=NOW + timedelta(seconds=10))
    assert again.action == "refuse" and "unclear" in again.message


def test_preview_only_never_sends_even_for_a_live_bot(factory):
    tasty = FakeBroker()
    request = orders.Request(
        "tastytrade", "5WX01234", "bot", OPEN, bot_id=make_bot(factory, live=True),
        preview_only=True,
    )  # fmt: skip
    result = orders.submit(factory, [tasty], request, now=NOW)
    assert result.action == "preview" and result.ok
    assert [c[0] for c in tasty.calls] == ["preview"]


def test_a_filled_recent_close_no_longer_covers(factory):
    done = BrokerOrder("777", OrderStatus.FILLED, (BrokerLeg(SHORT, Side.BUY, False, 1, 0),))
    first = FakeBroker(held={plain(SHORT): -2, plain(LONG): 2})
    close_one = Order(CLOSE.legs, 1, False, price=Decimal("-0.50"))
    assert orders.submit(factory, [first], you_close(close_one), now=NOW).ok
    later = FakeBroker(held={plain(SHORT): -1, plain(LONG): 1}, recent=[done])
    later.place_order = lambda account, body: "778"
    result = orders.submit(factory, [later], you_close(close_one), now=NOW + timedelta(minutes=1))
    assert result.ok, result.message


def test_switches_are_read_again_inside_the_lock(factory, monkeypatch):
    tasty = FakeBroker()
    reads = iter([orders.Switches(), orders.Switches(freeze=True)])
    monkeypatch.setattr(orders, "switches", lambda sf: next(reads))
    result = orders.submit(factory, [tasty], you_close(), now=NOW)
    assert result.action == "refuse" and "frozen" in result.message
    assert [c for c in tasty.calls if c[0] in ("place", "preview")] == []


def test_a_bot_can_replace_its_working_opening_order_at_its_daily_limit(factory):
    request = bot_request(factory, max_per_trade=1, max_per_day=1)
    tasty = FakeBroker()
    assert orders.submit(factory, [tasty], request, now=NOW).ok
    resting = BrokerOrder("777", OrderStatus.WORKING, (BrokerLeg(SHORT, Side.SELL, True, 1, 1),))
    tasty.working = [resting]
    assert orders.replace(factory, [tasty], request, "777", now=NOW).ok
    assert not orders.submit(factory, [tasty], request, now=NOW).ok  # a new order is still over


def make_run(factory, bot_id, live, account="5WX01234"):
    with factory() as session:
        run = BotRun(bot_id=bot_id, key=f"bot-{bot_id}-run-{live}", trading_date=NOW.date(),
                     entry_time="09:47", status="filled", live=live, legs=[], filled_legs=[],
                     chase_log=[], contracts=1, account_number=account, created_at=NOW,
                     updated_at=NOW)  # fmt: skip
        session.add(run)
        session.commit()
        return run.id


def bot_close(factory, run_live, order=CLOSE, run_id=None, **bot):
    """A bot's exit order for one of its runs; the run was recorded live or not."""
    bot_id = make_bot(factory, **bot)
    run_id = make_run(factory, bot_id, run_live) if run_id is None else run_id
    return orders.Request("tastytrade", "5WX01234", "bot", order, bot_id=bot_id,
                          bot_run_id=run_id, run_live=run_live)  # fmt: skip


def test_a_runs_live_setting_overrides_the_bots_for_its_exit_orders(factory):
    tasty = FakeBroker()
    sent = orders.submit(factory, [tasty], bot_close(factory, True, live=False), now=NOW)
    assert sent.action == "send" and sent.ok  # the run was live: its exits stay live
    later = NOW + timedelta(minutes=6)  # the first close no longer counts as covering
    previewed = orders.submit(
        factory, [FakeBroker()], bot_close(factory, False, live=True), now=later
    )
    assert previewed.action == "preview"  # and a preview run's exits stay preview


def test_the_runs_stored_setting_wins_over_the_request(factory):
    request = bot_close(factory, False, live=False)  # the run was recorded preview
    claimed = replace(request, run_live=True)  # a caller claiming it was live
    result = orders.submit(factory, [FakeBroker()], claimed, now=NOW)
    assert result.action == "preview"


def test_run_live_is_ignored_for_opening_orders(factory):
    request = bot_close(factory, True, order=OPEN, live=False)
    result = orders.submit(factory, [FakeBroker()], request, now=NOW)
    assert result.action == "preview"  # an entry follows the bot's own setting


def test_run_live_needs_a_run_of_that_bot(factory):
    no_run = replace(bot_close(factory, True, live=False), bot_run_id=None)
    assert orders.submit(factory, [FakeBroker()], no_run, now=NOW).action == "preview"
    other_bot = make_bot(factory, live=True)
    other_run = make_run(factory, other_bot, True)
    foreign = bot_close(factory, True, run_id=other_run, live=False)
    assert orders.submit(factory, [FakeBroker()], foreign, now=NOW).action == "preview"


def test_a_bots_run_is_checked_against_the_broker_it_fired_at(factory):
    """Exits follow the run (bots slice 11): a bot moved to Schwab after its trade
    filled still closes that trade at TastyTrade, and nowhere else."""
    request = bot_close(factory, True)
    with factory() as session:
        session.get(Bot, request.bot_id).broker = "schwab"
        session.commit()
    assert orders.submit(factory, [FakeBroker()], request, now=NOW).ok
    elsewhere = replace(request, broker="schwab")
    refused = orders.submit(factory, [FakeBroker(broker="schwab")], elsewhere, now=NOW)
    assert refused.action == "refuse" and "another account" in refused.message


def test_run_live_still_goes_through_the_central_check(factory):
    orders.set_switch(factory, "freeze", True, NOW)
    frozen = orders.submit(factory, [FakeBroker()], bot_close(factory, True), now=NOW)
    assert frozen.action == "refuse" and "frozen" in frozen.message
    orders.set_switch(factory, "freeze", False, NOW)
    nothing_open = FakeBroker(held={})
    refused = orders.submit(factory, [nothing_open], bot_close(factory, True), now=NOW)
    assert refused.action == "refuse"  # the closing rule still applies


def test_the_daily_limit_counts_only_opening_orders(factory):
    request = bot_request(factory, max_per_trade=1, max_per_day=2)
    assert orders.submit(factory, [FakeBroker()], request, now=NOW).ok
    close = orders.Request("tastytrade", "5WX01234", "bot", CLOSE, bot_id=request.bot_id)
    assert orders.submit(factory, [FakeBroker()], close, now=NOW).ok
    assert orders.used_today(factory, request.bot_id, NOW) == 1


def test_a_close_that_fills_between_the_reads_still_counts_as_covering(factory):
    # Our own close (777) is working. It fills while the order path reads the
    # broker: the positions come back from just before the fill.
    assert orders.submit(factory, [FakeBroker()], you_close(), now=NOW).ok
    legs = (BrokerLeg(SHORT, Side.BUY, False, 1, 1), BrokerLeg(LONG, Side.SELL, False, 1, 1))
    racing = FakeBroker(working=[BrokerOrder("777", OrderStatus.WORKING, legs)])
    stale = dict(racing.held)

    def positions(account):
        racing.reads.append("positions")
        filled = BrokerOrder("777", OrderStatus.FILLED, tuple(
            BrokerLeg(leg.symbol, leg.side, False, 1, 0, Decimal("0.50")) for leg in legs
        ))  # fmt: skip
        racing.working, racing.recent = [], [filled]
        return stale

    racing.option_positions = positions
    racing.recent = list(racing.working)
    again = orders.submit(factory, [racing], you_close(), now=NOW + timedelta(seconds=30))
    assert again.action == "refuse", again.message
    assert "place" not in [c[0] for c in racing.calls]


def test_your_opening_order_previews_within_the_setting(factory):
    tasty = FakeBroker(held={})
    request = orders.Request("tastytrade", "5WX01234", "you", OPEN, preview_only=True)
    result = orders.submit(factory, [tasty], request, now=NOW)
    assert result.action == "preview" and result.ok and "850" in result.message
    assert [c[0] for c in tasty.calls if c[0] != "ticks"] == ["preview"]


def test_your_opening_order_follows_a_saved_setting(factory):
    with factory() as session:
        session.add(Setting(key="max_order_contracts", value="1", updated_at=NOW))
        session.commit()
    big = Order(OPEN.legs, 2, True, price=Decimal("1.50"))
    request = orders.Request("tastytrade", "5WX01234", "you", big, preview_only=True)
    result = orders.submit(factory, [FakeBroker(held={})], request, now=NOW)
    assert result.action == "refuse" and "over your limit of 1" in result.message


def test_your_opening_order_is_refused_when_the_size_setting_cant_be_read(factory, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(orders.settings_service, "get", broken)
    request = orders.Request("tastytrade", "5WX01234", "you", OPEN, preview_only=True)
    tasty = FakeBroker(held={})
    result = orders.submit(factory, [tasty], request, now=NOW)
    assert result.action == "refuse" and not result.ok
    assert "over your limit of 0" in result.message
    assert "preview" not in [c[0] for c in tasty.calls]


LEAKY = BrokerError("TastyTrade refused POST /accounts/5WX01234/orders (403)")


@pytest.mark.parametrize("step", ["preview", "place", "cancel", "unclear"])
def test_messages_never_show_the_full_account_number(factory, step):
    error = RequestUnclear("Lost POST /accounts/5WX01234/orders") if step == "unclear" else LEAKY
    alerts = []
    tasty = FakeBroker(errors={"place" if step == "unclear" else step: error})
    if step == "cancel":
        result = orders.cancel(factory, [tasty], "tastytrade", "5WX01234", "500", "you", now=NOW)
    else:
        result = orders.submit(factory, [tasty], bot_request(factory), now=NOW,
                               notify=alerts.append)  # fmt: skip
    assert not result.ok and "…1234" in result.message
    assert "5WX01234" not in result.message
    assert "5WX01234" not in log(factory)[-1].result
    assert all("5WX01234" not in a.detail for a in alerts)


BODY = "SPXW  261013P05795000"
FLY = Order(
    (Leg(LONG, Side.BUY), Leg(BODY, Side.SELL, 2), Leg(SHORT, Side.BUY)),
    2, True, price=Decimal("-0.50"),
)  # fmt: skip


def test_used_today_counts_each_sends_largest_leg(factory):
    request = bot_request(factory, FLY, max_per_trade=4, max_per_day=8)
    assert orders.submit(factory, [FakeBroker()], request, now=NOW).ok
    assert orders.used_today(factory, request.bot_id, NOW) == 4  # 2 units x ratio 2
    assert orders.used_today(factory, request.bot_id, NOW + timedelta(days=1)) == 0


def test_a_tranche_of_a_ratio_order_doesnt_count_twice(factory):
    first = bot_request(factory, FLY, max_per_trade=4, max_per_day=4)
    assert orders.submit(factory, [FakeBroker()], first, now=NOW).ok
    parent = make_run(factory, first.bot_id, True)
    with factory() as session:
        child = BotRun(bot_id=first.bot_id, key="tranche", trading_date=NOW.date(),
                       entry_time="09:47", status="filled", live=True, legs=[], filled_legs=[],
                       chase_log=[], contracts=1, account_number="5WX01234", created_at=NOW,
                       updated_at=NOW, parent_run_id=parent)  # fmt: skip
        session.add(child)
        session.commit()
        child_id = child.id
    rest = replace(first, order=replace(FLY, quantity=1), bot_run_id=child_id)
    result = orders.submit(factory, [FakeBroker()], rest, now=NOW)
    assert result.ok, result.message  # 4 used - 2 for the rest = 2; 2 + 2 fits 4 a day
    assert orders.used_today(factory, first.bot_id, NOW) == 4


def test_blank_limits_are_worked_out_for_the_bots_own_entries(factory):
    """Decision 0058: a bot of 1 contract with no entry times on, limits blank (stored 0):
    1 per trade and 1 a day for what it sends by itself, so a misfire is still stopped."""
    tasty = FakeBroker()
    two = Order(OPEN.legs, 2, True, price=OPEN.price)
    refused = orders.submit(factory, [tasty], bot_request(factory, two, max_per_trade=0,
                                                         max_per_day=0), now=NOW)  # fmt: skip
    assert not refused.ok and "limit of 1 per trade" in refused.message
    request = bot_request(factory, max_per_trade=0, max_per_day=0)
    assert orders.submit(factory, [tasty], request, now=NOW).ok
    again = orders.submit(factory, [tasty], request, now=NOW)
    assert not again.ok and "limit of 1 a day" in again.message


def test_blank_limits_dont_hold_an_entry_you_start_yourself(factory):
    """A Trade page entry (its run's key) keeps its size and has no worked-out day; nor
    does it use up the bot's own day."""
    tasty = FakeBroker()
    bot_id = make_bot(factory, max_per_trade=0, max_per_day=0)
    with factory() as session:
        run = BotRun(bot_id=bot_id, key=f"ticket-{bot_id}-tok", trading_date=NOW.date(),
                     entry_time="09:47", status="pending", live=True, legs=[], filled_legs=[],
                     chase_log=[], contracts=2, account_number="5WX01234", created_at=NOW,
                     updated_at=NOW)  # fmt: skip
        session.add(run)
        session.commit()
        run_id = run.id
    two = Order(OPEN.legs, 2, True, price=OPEN.price)
    by_hand = orders.Request("tastytrade", "5WX01234", "bot", two, bot_id=bot_id,
                             bot_run_id=run_id)  # fmt: skip
    assert orders.submit(factory, [tasty], by_hand, now=NOW).ok
    assert orders.used_today(factory, bot_id, NOW) == 2
    assert orders.used_today(factory, bot_id, NOW, own_only=True) == 0
    own = orders.Request("tastytrade", "5WX01234", "bot", OPEN, bot_id=bot_id)
    assert orders.submit(factory, [tasty], own, now=NOW).ok


def test_preview_costs_from_tastytrades_dry_run():
    preview = {
        "order-costs": {"estimated-trade-cost": "300.00", "estimated-trade-cost-effect": "Credit"},
        "fee-calculation": {"commission": "1.0", "total-fees": "2.3"},
        "buying-power-effect": {
            "change-in-buying-power": "202.3", "change-in-buying-power-effect": "Debit",
            "new-buying-power": "9797.7", "new-buying-power-effect": "Credit",
        },
    }  # fmt: skip
    assert orders.preview_costs("tastytrade", preview) == {
        "value": 300.0, "commission": 1.0, "fees": 1.3, "bp_change": -202.3, "bp_after": 9797.7,
    }  # fmt: skip


def test_preview_costs_from_schwabs_preview_even_when_refused():
    preview = {
        "orderStrategy": {"orderType": "LIMIT", "orderBalance": {
            "orderValue": 120.0, "projectedBuyingPower": -50.0}},
        "commissionAndFee": {
            "commission": {"commissionLegs": [
                {"commissionValues": [{"value": 0.0, "type": "BASE_CHARGE"}, {"value": 0.5, "type": "COMMISSION"}]},
                {"commissionValues": [{"value": 0.5, "type": "COMMISSION"}]}]},
            "fee": {"feeLegs": [{"feeValues": [{"value": 0.01}, {"value": 0.56}]},
                                {"feeValues": [{"value": 0.01}, {"value": 0.56}]}]},
        },
        "orderValidationResult": {"rejects": [{"message": "Not enough buying power."}]},
    }  # fmt: skip
    assert orders.preview_costs("schwab", preview, credit=False) == {
        "value": -120.0, "commission": 1.0, "fees": 1.14, "bp_change": None, "bp_after": -50.0,
    }  # fmt: skip
    assert orders.preview_costs("schwab", None) is None
