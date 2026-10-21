import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from optionsjourney.brokers.http import BrokerError
from optionsjourney.brokers.tastytrade import TastyTradeClient

ACCOUNTS = {
    "data": {
        "items": [
            {
                "account": {
                    "account-number": "5WX01234",
                    "nickname": "Individual",
                    "account-type-name": "Individual",
                    "margin-or-cash": "Margin",
                },
                "authority-level": "owner",
            },
            {
                "account": {
                    "account-number": "5WY05678",
                    "nickname": "",
                    "account-type-name": "Roth IRA",
                },
                "authority-level": "owner",
            },
        ]
    }
}

BALANCES = {
    "data": {
        "account-number": "5WX01234",
        "net-liquidating-value": "25431.17",
        "cash-balance": "10250.5",
        "derivative-buying-power": "18000.0",
    }
}


@pytest.fixture
def client(make_credentials):
    def build(handler, is_test=False):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        return TastyTradeClient(make_credentials(is_test=is_test), http=http)

    return build


def test_accounts(client):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=ACCOUNTS)

    accounts = client(handler).accounts()

    assert [a.number for a in accounts] == ["5WX01234", "5WY05678"]
    assert accounts[0].display_name == "Individual"
    assert accounts[1].nickname is None
    assert accounts[1].display_name == "Roth IRA"
    assert seen[0].url == "https://api.tastyworks.com/customers/me/accounts"
    assert seen[0].headers["Authorization"] == "Bearer tt-token"


def test_balance(client):
    balance = client(lambda r: httpx.Response(200, json=BALANCES)).balance("5WX01234")
    assert balance.net_liquidating_value == Decimal("25431.17")
    assert balance.cash == Decimal("10250.5")
    assert balance.buying_power == Decimal("18000.0")


def test_sandbox_token_uses_sandbox_url(client):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=ACCOUNTS)

    client(handler, is_test=True).accounts()
    assert seen[0].url.host == "api.cert.tastyworks.com"


def test_rejected_token(client):
    with pytest.raises(BrokerError, match="rejected the access token"):
        client(lambda r: httpx.Response(401)).accounts()


def test_server_error(client):
    with pytest.raises(BrokerError, match="answered 502 for /accounts/5WX01234/balances"):
        client(lambda r: httpx.Response(502)).balance("5WX01234")


def _snapshot(day, value, cash):
    return {
        "snapshot-date": day,
        "net-liquidating-value": value,
        "cash-balance": cash,
        "time-of-day": "EOD",
    }


def test_balance_history(client):
    pages = {
        "0": {
            "data": {"items": [_snapshot("2026-09-01", "25000.5", "10000")]},
            "pagination": {"page-offset": 0, "total-pages": 2},
        },
        "1": {
            "data": {"items": [_snapshot("2026-09-02", "25100", "10050")]},
            "pagination": {"page-offset": 1, "total-pages": 2},
        },
    }
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=pages[request.url.params["page-offset"]])

    history = client(handler).balance_history("5WX01234", date(2026, 9, 1), date(2026, 9, 2))
    assert [(v.day, v.net_liquidating_value, v.cash) for v in history] == [
        (date(2026, 9, 1), Decimal("25000.5"), Decimal("10000")),
        (date(2026, 9, 2), Decimal("25100"), Decimal("10050")),
    ]
    assert seen[0].url.path == "/accounts/5WX01234/balance-snapshots"
    assert seen[0].url.params["start-date"] == "2026-09-01"
    assert seen[0].url.params["time-of-day"] == "EOD"


def test_quote_token(client):
    body = {
        "data": {
            "token": "qt",
            "dxlink-url": "wss://tasty-openapi-ws.dxfeed.com/realtime",
            "level": "api",
        }
    }
    assert client(lambda r: httpx.Response(200, json=body)).quote_token() == (
        "qt",
        "wss://tasty-openapi-ws.dxfeed.com/realtime",
    )


def test_stock_positions(client):
    def handler(request):
        assert request.url.path == "/accounts/5WX01234/positions"
        items = [
            {"instrument-type": "Equity", "symbol": "IRM", "quantity": "40.5",
             "quantity-direction": "Long", "average-open-price": "52.25",
             "close-price": "114.51"},
            {"instrument-type": "Equity", "symbol": "TSLA", "quantity": "10",
             "quantity-direction": "Short", "average-open-price": "400", "close-price": "0"},
            {"instrument-type": "Equity Option", "symbol": "SPXW  261016P05800000",
             "quantity": "1", "quantity-direction": "Long", "average-open-price": "3"},
        ]  # fmt: skip
        return httpx.Response(200, json={"data": {"items": items}})

    found = client(handler).stock_positions("5WX01234")
    assert [(p.account, p.ticker, p.quantity, p.cost_basis, p.prev_close) for p in found] == [
        ("tastytrade:5WX01234", "IRM", Decimal("40.5"), Decimal("52.25"),
         Decimal("114.51")),
        ("tastytrade:5WX01234", "TSLA", Decimal("-10"), Decimal("400"), None),
    ]  # fmt: skip


def test_preview_place_replace_and_cancel(client):
    seen = []

    def handler(request):
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path.endswith("/dry-run"):
            effect = {"change-in-buying-power": "1700"}
            return httpx.Response(201, json={"data": {"buying-power-effect": effect}})
        if request.method == "PUT":
            return httpx.Response(200, json={"data": {"id": 778, "status": "Received"}})
        if request.method == "DELETE":
            return httpx.Response(200, json={"data": {"id": 778, "status": "Cancel Requested"}})
        return httpx.Response(201, json={"data": {"order": {"id": 777, "status": "Received"}}})

    body = {"order-type": "Limit"}
    tasty = client(handler)
    assert tasty.preview_order("5WX01234", body)["buying-power-effect"]
    assert tasty.place_order("5WX01234", body) == "777"
    assert tasty.replace_order("5WX01234", "777", body) == "778"
    tasty.cancel_order("5WX01234", "778")
    assert [(m, p) for m, p, _ in seen] == [
        ("POST", "/accounts/5WX01234/orders/dry-run"),
        ("POST", "/accounts/5WX01234/orders"),
        ("PUT", "/accounts/5WX01234/orders/777"),
        ("DELETE", "/accounts/5WX01234/orders/778"),
    ]
    assert seen[0][2] == body


def test_a_refused_order_says_why(client):
    def handler(request):
        error = {
            "code": "preflight_check_failure",
            "message": "One or more preflight checks failed",
            "errors": [{"message": "Insufficient buying power"}],
        }
        return httpx.Response(422, json={"error": error})

    with pytest.raises(BrokerError, match="Insufficient buying power"):
        client(handler).preview_order("5WX01234", {})


LIVE_ORDERS = {
    "data": {
        "items": [
            {"id": 1, "status": "Live", "legs": [{"symbol": "SPXW  261013P05800000",
             "action": "Buy to Close", "quantity": "1", "remaining-quantity": "1"}]},
            {"id": 2, "status": "Filled", "legs": []},
        ]
    }
}  # fmt: skip


def test_orders_read_back(client):
    def handler(request):
        if request.url.path.endswith("/orders/live"):
            return httpx.Response(200, json=LIVE_ORDERS)
        return httpx.Response(200, json={"data": LIVE_ORDERS["data"]["items"][0]})

    tasty = client(handler)
    assert [o.broker_order_id for o in tasty.recent_orders("5WX01234")] == ["1", "2"]
    assert [o.broker_order_id for o in tasty.working_orders("5WX01234")] == ["1"]
    assert tasty.get_order("5WX01234", "1").legs[0].remaining == 1


def test_option_positions_are_signed_by_plain_symbol(client):
    positions = {"data": {"items": [
        {"instrument-type": "Equity Option", "symbol": "SPXW  261013P05800000", "quantity": "2",
         "quantity-direction": "Short"},
        {"instrument-type": "Future Option", "symbol": "./ESZ6 EW2Z6 261218P7000",
         "quantity": "1", "quantity-direction": "Long"},
        {"instrument-type": "Equity", "symbol": "AAPL", "quantity": "10",
         "quantity-direction": "Long"},
    ]}}  # fmt: skip
    tasty = client(lambda request: httpx.Response(200, json=positions))
    assert tasty.option_positions("5WX01234") == {
        "SPXW261013P05800000": -2,
        "./ESZ6EW2Z6261218P7000": 1,
    }


def test_tick_sizes(client):
    rows = [{"threshold": "3.0", "value": "0.05"}, {"value": "0.10"}]

    def handler(request):
        path = request.url.path
        if path == "/instruments/equities/AAPL":
            return httpx.Response(200, json={"data": {"option-tick-sizes": rows}})
        if path == "/instruments/futures/ESZ6":
            return httpx.Response(200, json={"data": {"spread-tick-sizes": [{"value": "0.05"}]}})
        if path == "/futures-option-chains/ES/nested":
            chain = {"expirations": [{"option-contract-symbol": "EW2Z6", "tick-sizes": rows}]}
            return httpx.Response(200, json={"data": {"option-chains": [chain]}})
        return httpx.Response(404)

    tasty = client(handler)
    assert tasty.equity_tick_sizes("AAPL") == rows
    assert tasty.future_tick_sizes("/ESZ6") == [{"value": "0.05"}]
    assert tasty.futures_option_tick_sizes("ES") == {"EW2Z6": rows}


def test_an_order_in_a_status_the_app_doesnt_know_counts_as_working(client):
    odd = {"data": {"items": [
        {"id": 3, "status": "Some New Status", "legs": []},
        {"id": 4, "status": "Cancelled", "legs": []},
    ]}}  # fmt: skip
    tasty = client(lambda request: httpx.Response(200, json=odd))
    assert [o.broker_order_id for o in tasty.working_orders("5WX01234")] == ["3"]
