from datetime import UTC, datetime

import httpx
import pytest

from optionsjourney.brokers.http import BrokerError
from optionsjourney.brokers.schwab import SchwabClient

BASE = "/trader/v1/accounts/HASH-A"
NUMBERS = [{"accountNumber": "12345678", "hashValue": "HASH-A"}]
ORDER = {
    "orderId": 1001, "status": "WORKING", "enteredTime": "2026-10-13T14:00:00+0000",
    "quantity": 1, "filledQuantity": 0,
    "orderLegCollection": [{"instruction": "BUY_TO_CLOSE", "quantity": 1,
                            "instrument": {"symbol": "SPXW  261013P05800000"}}],
}  # fmt: skip


@pytest.fixture
def schwab(fake_credentials):
    def build(handler):
        def route(request):
            if request.url.path == "/trader/v1/accounts/accountNumbers":
                return httpx.Response(200, json=NUMBERS)
            if request.url.path == "/trader/v1/accounts":  # accounts() also reads the types
                return httpx.Response(200, json=[])
            return handler(request)

        return SchwabClient(
            fake_credentials, http=httpx.Client(transport=httpx.MockTransport(route))
        )

    return build


def test_preview_place_replace_and_cancel(schwab):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/previewOrder"):
            return httpx.Response(200, json={"orderValidationResult": {"rejects": []}})
        if request.method in ("POST", "PUT"):
            return httpx.Response(
                201, headers={"Location": f"https://api.schwabapi.com{BASE}/orders/2002"}
            )
        return httpx.Response(200)

    client = schwab(handler)
    body = {"orderType": "NET_CREDIT"}
    client.preview_order("12345678", body)
    assert client.place_order("12345678", body) == "2002"
    assert client.replace_order("12345678", "2002", body) == "2002"
    client.cancel_order("12345678", "2002")
    assert seen == [
        ("POST", f"{BASE}/previewOrder"),
        ("POST", f"{BASE}/orders"),
        ("PUT", f"{BASE}/orders/2002"),
        ("DELETE", f"{BASE}/orders/2002"),
    ]


def test_a_preview_with_rejects_is_a_refusal(schwab):
    rejects = {"orderValidationResult": {"rejects": [{"message": "Insufficient funds"}]}}
    client = schwab(lambda request: httpx.Response(200, json=rejects))
    with pytest.raises(BrokerError, match="Insufficient funds"):
        client.preview_order("12345678", {})


def test_place_without_a_location_returns_no_id(schwab):
    client = schwab(lambda request: httpx.Response(201))
    assert client.place_order("12345678", {}) == ""


def test_orders_read_back(schwab):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == f"{BASE}/orders":
            return httpx.Response(200, json=[ORDER, {**ORDER, "orderId": 1002, "status": "FILLED"}])
        return httpx.Response(200, json=ORDER)

    client = schwab(handler)
    since = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)
    assert [o.broker_order_id for o in client.recent_orders("12345678", since)] == ["1001", "1002"]
    assert seen[0].url.params["fromEnteredTime"] == "2026-10-13T13:00:00.000Z"
    assert [o.broker_order_id for o in client.working_orders("12345678")] == ["1001"]
    assert client.get_order("12345678", "1001").legs[0].side.value == "buy"


def test_option_positions(schwab):
    account = {"securitiesAccount": {"positions": [
        {"instrument": {"assetType": "OPTION", "symbol": "SPXW  261013P05800000"},
         "longQuantity": 0, "shortQuantity": 2},
        {"instrument": {"assetType": "EQUITY", "symbol": "AAPL"}, "longQuantity": 10,
         "shortQuantity": 0},
    ]}}  # fmt: skip
    client = schwab(lambda request: httpx.Response(200, json=account))
    assert client.option_positions("12345678") == {"SPXW261013P05800000": -2}


def test_an_order_in_a_status_the_app_doesnt_know_counts_as_working(schwab):
    items = [{**ORDER, "status": "UNKNOWN"}, {**ORDER, "orderId": 1002, "status": "CANCELED"}]
    client = schwab(lambda request: httpx.Response(200, json=items))
    assert [o.broker_order_id for o in client.working_orders("12345678")] == ["1001"]
