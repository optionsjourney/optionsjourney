"""Pending orders across every account (#292): the snapshot and the live mids."""

import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.quotes import Quote
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services import pending_orders
from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus, Side
from tests.services.test_orders import FakeBroker

NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
SHORT = "SPXW  261013P05800000"
LONG = "SPXW  261013P05790000"
FAILED = "Couldn't read this account's orders from your broker."


def _order(order_id, *legs):
    return BrokerOrder(order_id, OrderStatus.WORKING, legs, entered_at=NOW)


def _spread(order_id, qty=1):
    return _order(order_id, BrokerLeg(SHORT, Side.SELL, True, qty, qty),
                  BrokerLeg(LONG, Side.BUY, True, qty, qty))  # fmt: skip


def _account(account_id, broker, number, nickname=None):
    return SimpleNamespace(id=account_id, broker=broker, number=number, nickname=nickname)


def _factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


class SlowBroker(FakeBroker):
    def working_orders(self, account):
        time.sleep(0.5)
        return self.working


def test_snapshot_lists_every_account(tmp_path):
    tasty = FakeBroker(working=[_spread("1")])
    schwab = FakeBroker("schwab", working=[_spread("2"), _spread("3")])
    accounts = [_account(7, "tastytrade", "5WX01234", "Alpha"), _account(8, "schwab", "99991234")]
    got = pending_orders.snapshot(_factory(tmp_path), [tasty, schwab], accounts)
    assert [(a["account_id"], a["broker"], a["label"]) for a in got["accounts"]] == [
        (7, "TASTYTRADE", "Alpha"), (8, "SCHWAB", "…1234")]  # fmt: skip
    assert [len(a["orders"]) for a in got["accounts"]] == [1, 2]
    assert got["count"] == 3 and got["errors"] == 0
    assert all(a["error"] is None for a in got["accounts"])
    assert datetime.fromisoformat(got["as_of"])
    assert "5WX01234" not in str(got)


def test_snapshot_one_account_failing_keeps_others(tmp_path):
    bad = FakeBroker(working=[_spread("1")], errors={"working": BrokerError("down")})
    good = FakeBroker("schwab", working=[_spread("2")])
    accounts = [_account(1, "tastytrade", "5WX01234"), _account(2, "schwab", "99991234")]
    got = pending_orders.snapshot(_factory(tmp_path), [bad, good], accounts)
    first, second = got["accounts"]
    assert first["error"] == FAILED and first["orders"] == []
    assert second["error"] is None and len(second["orders"]) == 1
    assert got["errors"] == 1 and got["count"] == 1


def test_snapshot_timeout_marks_error(tmp_path):
    slow = SlowBroker(working=[_spread("1")])
    fast = FakeBroker("schwab", working=[_spread("2")])
    accounts = [_account(1, "tastytrade", "5WX01234"), _account(2, "schwab", "99991234")]
    started = time.monotonic()
    got = pending_orders.snapshot(_factory(tmp_path), [slow, fast], accounts, timeout_s=0.05)
    assert time.monotonic() - started < 0.4
    assert got["accounts"][0]["error"] == FAILED and got["accounts"][0]["orders"] == []
    assert len(got["accounts"][1]["orders"]) == 1 and got["errors"] == 1


class FakeHub:
    def __init__(self, marks):
        self.quotes = {
            key: Quote(
                key,
                Decimal(str(mid)) - Decimal("0.05"),
                Decimal(str(mid)) + Decimal("0.05"),
                None,
                "tastytrade",
                NOW,
            )
            for key, mid in marks.items()
        }
        self.subscribed = set()

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        return self.quotes.get(key)

    def good_mark(self, key):
        return None


def _row(order_id, *legs):
    return {
        "order_id": order_id,
        "legs": [{"symbol": s, "side": side, "quantity": q} for s, side, q in legs],
    }


def test_order_marks_signed_mid_and_missing():
    hub = FakeHub({SHORT: 3.0, LONG: 1.0})
    rows = [
        _row("credit", (SHORT, "sell", 1), (LONG, "buy", 1)),
        _row("debit", (SHORT, "buy", 1), (LONG, "sell", 1)),
        _row("ratio", (SHORT, "sell", 4), (LONG, "buy", 2)),  # per unit: 2 short, 1 long
        _row("gap", (SHORT, "sell", 1), ("SPXW  261013P05000000", "buy", 1)),
    ]
    got = pending_orders.order_marks(hub, rows)
    assert got == {"credit": 2.0, "debit": -2.0, "ratio": 5.0, "gap": None}
    assert hub.subscribed == {SHORT, LONG, "SPXW  261013P05000000"}


def test_snapshot_lists_the_demo_brokers_working_orders(tmp_path):
    from optionsjourney.demo.broker import TASTY_ACCOUNT, Book, PretendTasty  # noqa: PLC0415
    from optionsjourney.demo.market import PretendMarket  # noqa: PLC0415

    market = PretendMarket(seed=1, start=datetime(2026, 10, 21, 13, 40, tzinfo=UTC))
    tasty = PretendTasty(market, Book(TASTY_ACCOUNT))
    body = {
        "time-in-force": "Day", "order-type": "Limit", "price": "0.05", "price-effect": "Credit",
        "legs": [
            {"instrument-type": "Equity Option", "symbol": SHORT, "quantity": 1,
             "action": "Sell to Open"},
            {"instrument-type": "Equity Option", "symbol": LONG, "quantity": 1,
             "action": "Buy to Open"},
        ],
    }  # fmt: skip
    oid = tasty.place_order(TASTY_ACCOUNT, body)
    accounts = [_account(1, "tastytrade", TASTY_ACCOUNT, "Demo")]
    got = pending_orders.snapshot(_factory(tmp_path), [tasty], accounts)
    assert [o["order_id"] for o in got["accounts"][0]["orders"]] == [oid]
    assert got["errors"] == 0
