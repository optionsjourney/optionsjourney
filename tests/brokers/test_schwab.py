from datetime import date
from decimal import Decimal

import httpx
import pytest

from optionsjourney.brokers.http import BrokerError
from optionsjourney.brokers.schwab import SchwabClient

ACCOUNT_NUMBERS = [{"accountNumber": "12345678", "hashValue": "HASH-A"}]

ACCOUNTS = [{"securitiesAccount": {"accountNumber": "12345678", "type": "MARGIN"}}]

MARGIN_ACCOUNT = {
    "securitiesAccount": {
        "accountNumber": "12345678",
        "type": "MARGIN",
        "currentBalances": {
            "liquidationValue": 98765.43,
            "cashBalance": 1200.5,
            "buyingPower": 50000.0,
        },
    }
}

CASH_ACCOUNT = {
    "securitiesAccount": {
        "accountNumber": "12345678",
        "type": "CASH",
        "currentBalances": {"liquidationValue": 0, "totalCash": 3000.0},
    }
}


def _routes(account=MARGIN_ACCOUNT):
    return {
        "/trader/v1/accounts/accountNumbers": ACCOUNT_NUMBERS,
        "/trader/v1/accounts": ACCOUNTS,
        "/trader/v1/accounts/HASH-A": account,
    }


@pytest.fixture
def client(fake_credentials):
    def build(routes, seen=None):
        def handler(request):
            if seen is not None:
                seen.append(request)
            if request.url.path not in routes:
                return httpx.Response(404)
            return httpx.Response(200, json=routes[request.url.path])

        http = httpx.Client(transport=httpx.MockTransport(handler))
        return SchwabClient(fake_credentials, http=http)

    return build


def test_accounts(client):
    seen = []
    accounts = client(_routes(), seen).accounts()
    assert [(a.broker, a.number, a.display_name) for a in accounts] == [
        ("schwab", "12345678", "Margin")
    ]
    assert seen[0].headers["Authorization"] == "Bearer tt-token"


def test_balance_uses_the_account_hash(client):
    seen = []
    schwab = client(_routes(), seen)
    schwab.accounts()
    balance = schwab.balance("12345678")
    assert seen[-1].url.path == "/trader/v1/accounts/HASH-A"
    assert balance.net_liquidating_value == Decimal("98765.43")
    assert balance.cash == Decimal("1200.5")
    assert balance.buying_power == Decimal("50000.0")


def test_balance_after_accounts_reuses_the_accounts_answer(client):
    """/accounts already carries every balance: no call per account."""
    seen = []
    routes = _routes() | {"/trader/v1/accounts": [MARGIN_ACCOUNT]}
    schwab = client(routes, seen)
    schwab.accounts()
    calls = len(seen)
    assert schwab.balance("12345678").net_liquidating_value == Decimal("98765.43")
    assert len(seen) == calls
    # Only once: a later read asks Schwab again
    schwab.balance("12345678")
    assert seen[-1].url.path == "/trader/v1/accounts/HASH-A"


def test_balance_looks_up_the_hash_if_needed(client):
    assert client(_routes()).balance("12345678").net_liquidating_value == Decimal("98765.43")


def test_cash_account_falls_back_to_total_cash(client):
    balance = client(_routes(CASH_ACCOUNT)).balance("12345678")
    assert balance.net_liquidating_value == Decimal("3000.0")
    assert balance.cash == Decimal("3000.0")
    assert balance.buying_power == Decimal("0")


def test_unknown_account(client):
    with pytest.raises(BrokerError, match="doesn't list account 9999"):
        client(_routes()).balance("00009999")


def test_rejected_token(fake_credentials):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(BrokerError, match="Schwab rejected the access token"):
        SchwabClient(fake_credentials, http=http).accounts()


def test_daily_closes(make_credentials):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "candles": [
                    {
                        "datetime": 1791435600000,
                        "close": 7765.36,
                    },  # 2026-10-08 05:00 UTC, as Schwab sends it
                    {"datetime": 1791522000000, "close": 7811.54},  # 2026-10-09
                ]
            },
        )

    client = SchwabClient(
        make_credentials(), http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    closes = client.daily_closes("SPX", date(2026, 10, 8), date(2026, 10, 9))
    assert [(c.day, c.close) for c in closes] == [
        (date(2026, 10, 8), Decimal("7765.36")),
        (date(2026, 10, 9), Decimal("7811.54")),
    ]
    assert seen[0].url.path == "/marketdata/v1/pricehistory"
    assert seen[0].url.params["symbol"] == "$SPX"  # indexes are $-prefixed at Schwab


def test_streamer_info(make_credentials):
    body = {
        "streamerInfo": [
            {
                "streamerSocketUrl": "wss://streamer-api.schwab.com/ws",
                "schwabClientCustomerId": "c",
                "schwabClientCorrelId": "r",
                "schwabClientChannel": "N9",
                "schwabClientFunctionId": "APIAPP",
            }
        ]
    }
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    assert (
        SchwabClient(make_credentials(), http=http).streamer_info()["schwabClientChannel"] == "N9"
    )


def test_stock_positions_long_and_short_shares_only(client):
    positions = [
        {
            "instrument": {"assetType": "EQUITY", "symbol": "AAPL"},
            "longQuantity": 10.0,
            "shortQuantity": 0.0,
            "averagePrice": 250.5,
        },
        {
            "instrument": {"assetType": "COLLECTIVE_INVESTMENT", "symbol": "SPY"},
            "longQuantity": 0.0,
            "shortQuantity": 5.0,
            "averageShortPrice": 700.0,
        },
        {
            "instrument": {"assetType": "OPTION", "symbol": "SPXW  261016P05800000"},
            "longQuantity": 1.0,
            "shortQuantity": 0.0,
            "averagePrice": 3.0,
        },
    ]
    routes = _routes() | {
        "/trader/v1/accounts": [
            {"securitiesAccount": {"accountNumber": "12345678", "positions": positions}}
        ]
    }
    found = client(routes).stock_positions()
    assert [(p.account, p.ticker, p.quantity, p.cost_basis) for p in found] == [
        ("schwab:12345678", "AAPL", Decimal("10.0"), Decimal("250.5")),
        ("schwab:12345678", "SPY", Decimal("-5.0"), Decimal("700.0")),
    ]
