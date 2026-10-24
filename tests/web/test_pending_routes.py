"""The Pending drawer's API (#292): GET /api/pending and its live mids."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.services.test_orders import FakeBroker

from optionsjourney.app import create_app
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.quotes import Quote
from optionsjourney.db.models import Account
from optionsjourney.services import pending_orders
from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus, Side
from optionsjourney.web import services

SHORT = "SPXW  261013P05800000"
LONG = "SPXW  261013P05790000"


@pytest.fixture
def app(settings, fake_credentials):
    legs = (BrokerLeg(SHORT, Side.SELL, True, 1, 1), BrokerLeg(LONG, Side.BUY, True, 1, 1))
    broker = FakeBroker(held={}, working=[
        BrokerOrder("777", OrderStatus.WORKING, legs, entered_at=datetime.now(UTC))])  # fmt: skip
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    app.config["TESTING"] = True
    with app.app_context(), services().session_factory() as session:
        session.add(Account(broker="tastytrade", number="5WX01234", nickname="Alpha",
                            updated_at=datetime.now(UTC)))  # fmt: skip
        session.commit()
    app.broker = broker
    return app


def test_pending_shape(app):
    got = app.test_client().get("/api/pending").get_json()
    assert got["count"] == 1 and got["errors"] == 0 and got["as_of"]
    entry = got["accounts"][0]
    assert entry["label"] == "Alpha" and entry["broker"] == "TASTYTRADE"
    assert isinstance(entry["account_id"], int) and entry["error"] is None
    assert entry["orders"][0]["order_id"] == "777"
    assert "5WX01234" not in str(got)


def test_pending_count_only(app):
    got = app.test_client().get("/api/pending?count=1").get_json()
    assert got == {"count": 1, "errors": 0}


def test_pending_broker_down_is_an_error_not_a_500(app):
    app.broker.errors["working"] = BrokerError("down")
    got = app.test_client().get("/api/pending")
    assert got.status_code == 200
    assert got.get_json()["errors"] == 1 and got.get_json()["accounts"][0]["orders"] == []


def test_pending_markets(app):
    with app.app_context():
        hub = services().quote_hub
        now = datetime.now(UTC)
        for key, mid in ((SHORT, "3.00"), (LONG, "1.00")):
            quote = Quote(
                key,
                Decimal(mid) - Decimal("0.05"),
                Decimal(mid) + Decimal("0.05"),
                None,
                "tastytrade",
                now,
            )
            hub._quotes.setdefault("tastytrade", {})[key] = quote
    client = app.test_client()
    client.get("/api/pending")  # the listing the marks are priced from
    reads = list(app.broker.reads)
    got = client.get("/api/pending/markets?ids=777,nope").get_json()
    assert got == {"marks": {"777": 2.0}}  # unknown ids are left out
    assert client.get("/api/pending/markets").get_json() == {"marks": {}}
    assert app.broker.reads == reads and app.broker.calls == []  # never asks a broker


def test_pending_markets_before_any_listing_is_empty(app):
    pending_orders._listed.clear()
    assert app.test_client().get("/api/pending/markets?ids=777").get_json() == {"marks": {}}


def test_pending_count_must_be_one(app):
    assert "accounts" in app.test_client().get("/api/pending?count=0").get_json()
