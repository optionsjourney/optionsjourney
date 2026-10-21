from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

from optionsjourney.brokers.schwab import SchwabClient, cash_flow, parse_transaction
from optionsjourney.core.transactions import Effect, InstrumentType, Kind, Side

SELL_TO_OPEN = {
    "activityId": 91234567890,
    "time": "2026-10-09T13:32:04+0000",
    "type": "TRADE",
    "status": "VALID",
    "subAccount": "MARGIN",
    "orderId": 1004567890123,
    "positionId": 2233445566,
    "netAmount": 248.33,
    "transferItems": [
        {
            "instrument": {
                "assetType": "OPTION",
                "symbol": "SPXW  261009P05800000",
                "underlyingSymbol": "$SPX",
                "putCall": "PUT",
                "strikePrice": 5800,
                "expirationDate": "2026-10-09T20:00:00.000+00:00",
                "optionPremiumMultiplier": 100,
            },
            "amount": -1.0,
            "cost": 250.0,
            "price": 2.5,
            "positionEffect": "OPENING",
        },
        {
            "instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
            "feeType": "COMMISSION",
            "amount": -0.65,
        },
        {
            "instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
            "feeType": "INDEX_OPTION_FEE",
            "amount": -0.97,
        },
        {
            "instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
            "feeType": "OPT_REG_FEE",
            "amount": -0.05,
        },
    ],
}

STOCK_BUY = {
    "activityId": 91234567891,
    "time": "2026-10-08T15:00:00+0000",
    "type": "TRADE",
    "transferItems": [
        {
            "instrument": {"assetType": "EQUITY", "symbol": "AAPL"},
            "amount": 10.0,
            "price": 230.5,
            "positionEffect": "OPENING",
        }
    ],
}

DIVIDEND = {
    "activityId": 91234567892,
    "time": "2026-10-01T12:00:00+0000",
    "type": "DIVIDEND_OR_INTEREST",
    "transferItems": [],
}


def test_sell_to_open():
    [fill] = parse_transaction(SELL_TO_OPEN, "12345678")
    assert fill.transaction_id == "91234567890"
    assert fill.executed_at == datetime(2026, 10, 9, 13, 32, 4, tzinfo=UTC)
    assert (fill.kind, fill.instrument_type) == (Kind.TRADE, InstrumentType.OPTION)
    assert fill.underlying == "SPX"
    assert fill.option.strike == Decimal("5800")
    assert fill.option.expiration == date(2026, 10, 9)
    assert (fill.side, fill.effect) == (Side.SELL, Effect.OPEN)
    assert fill.quantity == Decimal("1.0")
    assert fill.value == Decimal("250.0")
    assert fill.fees == Decimal("1.67")
    assert fill.order_id == "1004567890123"


def test_stock_without_cost_computes_value():
    [fill] = parse_transaction(STOCK_BUY, "12345678")
    assert fill.instrument_type == InstrumentType.STOCK
    assert fill.option is None
    assert fill.side == Side.BUY
    assert fill.value == Decimal("-2305.0")
    assert fill.instrument_label == "AAPL"


def test_dividend_has_no_fills():
    assert parse_transaction(DIVIDEND, "12345678") == []


def test_expiration_from_description():
    expired = dict(SELL_TO_OPEN, type="RECEIVE_AND_DELIVER", description="OPTION EXPIRATION")
    [fill] = parse_transaction(expired, "12345678")
    assert fill.kind == Kind.EXPIRATION


def test_transactions_uses_hash_and_windows(fake_credentials):
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/accountNumbers"):
            return httpx.Response(200, json=[{"accountNumber": "12345678", "hashValue": "HASH-A"}])
        if request.url.path == "/trader/v1/accounts":
            return httpx.Response(200, json=[])
        if request.url.params["startDate"].startswith("2026-08"):
            return httpx.Response(200, json=[SELL_TO_OPEN])
        return httpx.Response(200, json=[])

    client = SchwabClient(
        fake_credentials, http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    raw = client.transactions("12345678", date(2026, 6, 1), date(2026, 10, 9))

    windows = [r for r in seen if r.url.path.endswith("/transactions")]
    assert {r.url.path for r in windows} == {"/trader/v1/accounts/HASH-A/transactions"}
    assert [(r.url.params["startDate"], r.url.params["endDate"]) for r in windows] == [
        ("2026-06-01T00:00:00.000Z", "2026-08-01T00:00:00.000Z"),
        ("2026-08-01T00:00:00.000Z", "2026-10-01T00:00:00.000Z"),
        ("2026-10-01T00:00:00.000Z", "2026-10-10T00:00:00.000Z"),
    ]
    assert "TRADE" in windows[0].url.params["types"]
    assert [t.transaction_id for t in raw] == ["91234567890"]


# Schwab leaves the symbol out on some legs of contracts it has since disabled
# (#3). The details are still there, so the symbol is rebuilt from them.
NO_SYMBOL_CLOSE = {
    "activityId": 91234567892,
    "time": "2025-10-13T15:17:10+0000",
    "type": "TRADE",
    "orderId": 1004343494670,
    "transferItems": [
        {
            "instrument": {
                "assetType": "OPTION",
                "status": "DISABLED",
                "description": "S & P 500 INDEX 10/24/2025 $6600 Put",
                "expirationDate": "2025-10-24T04:00:00+0000",
                "optionDeliverables": [{"rootSymbol": "SPXW", "deliverableUnits": 100.0}],
                "optionPremiumMultiplier": 100,
                "putCall": "PUT",
                "strikePrice": 6600.0,
                "underlyingSymbol": "SPXW",
            },
            "amount": 5.0,
            "cost": -500.0,
            "price": 1.0,
            "positionEffect": "CLOSING",
        },
    ],
}


def test_missing_option_symbol_is_rebuilt():
    [fill] = parse_transaction(NO_SYMBOL_CLOSE, "12345678")
    assert fill.symbol == "SPXW  251024P06600000"
    assert fill.underlying == "SPX"
    assert fill.option.strike == Decimal("6600")


def test_cash_flows_are_money_moved_in_or_out():
    deposit = {
        "activityId": 1,
        "time": "2026-10-01T14:00:00+0000",
        "type": "ACH_RECEIPT",
        "netAmount": 2500.0,
    }
    withdrawal = dict(deposit, type="ACH_DISBURSEMENT", netAmount=-1000.0)
    wire = dict(deposit, type="WIRE_IN", netAmount=7000.0)
    assert cash_flow(deposit) == Decimal("2500.0")
    assert cash_flow(withdrawal) == Decimal("-1000.0")
    assert cash_flow(wire) == Decimal("7000.0")
    assert cash_flow(dict(deposit, type="DIVIDEND_OR_INTEREST")) is None
    assert cash_flow(SELL_TO_OPEN) is None
