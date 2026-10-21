from datetime import UTC, date, datetime
from decimal import Decimal

import httpx

from optionsjourney.brokers.tastytrade import TastyTradeClient, cash_flow, parse_transaction
from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import Status, build_positions
from optionsjourney.core.transactions import Effect, InstrumentType, Kind, Side

SELL_TO_OPEN = {
    "id": 401234567,
    "account-number": "5WX01234",
    "symbol": "SPXW  261009P05800000",
    "instrument-type": "Equity Option",
    "underlying-symbol": "SPXW",
    "transaction-type": "Trade",
    "transaction-sub-type": "Sell to Open",
    "description": "Sold 1 SPXW 10/09/26 Put 5800.00 @ 2.50",
    "action": "Sell to Open",
    "quantity": "1.0",
    "price": "2.5",
    "executed-at": "2026-10-09T13:32:04.123+00:00",
    "value": "250.0",
    "value-effect": "Credit",
    "net-value": "248.28",
    "net-value-effect": "Credit",
    "commission": "1.0",
    "commission-effect": "Debit",
    "clearing-fees": "0.1",
    "clearing-fees-effect": "Debit",
    "regulatory-fees": "0.02",
    "regulatory-fees-effect": "Debit",
    "proprietary-index-option-fees": "0.6",
    "proprietary-index-option-fees-effect": "Debit",
    "order-id": 98765,
    "leg-count": 2,
}

EXPIRATION = {
    "id": 401234999,
    "account-number": "5WX01234",
    "symbol": "SPXW  261009P05800000",
    "instrument-type": "Equity Option",
    "underlying-symbol": "SPXW",
    "transaction-type": "Receive Deliver",
    "transaction-sub-type": "Expiration",
    "description": "Removal of option due to expiration",
    "action": "Buy to Close",
    "quantity": "1.0",
    "price": None,
    "executed-at": "2026-10-09T21:00:00.000+00:00",
    "value": "0.0",
    "value-effect": "None",
}

FUTURES_OPTION = {
    "id": 401235000,
    "symbol": "./ESZ6 EW2V6 261009P5800",
    "instrument-type": "Future Option",
    "underlying-symbol": "/ESZ6",
    "transaction-type": "Trade",
    "description": "Sold 1 /ESZ6 EW2V6 10/09/26 Put 5800.00 @ 10.25",
    "action": "Sell to Open",
    "quantity": "1",
    "price": "10.25",
    "executed-at": "2026-10-09T14:00:00+00:00",
    "value": "512.5",
    "value-effect": "Credit",
}

DEPOSIT = {
    "id": 401235001,
    "transaction-type": "Money Movement",
    "transaction-sub-type": "Deposit",
    "executed-at": "2026-10-01T14:00:00+00:00",
    "value": "5000.0",
    "value-effect": "Credit",
}


def test_sell_to_open():
    [fill] = parse_transaction(SELL_TO_OPEN, "5WX01234")
    assert fill.transaction_id == "401234567"
    assert fill.executed_at == datetime(2026, 10, 9, 13, 32, 4, 123000, tzinfo=UTC)
    assert (fill.kind, fill.instrument_type) == (Kind.TRADE, InstrumentType.OPTION)
    assert fill.underlying == "SPX"
    assert fill.option.put_call == "PUT"
    assert fill.option.strike == Decimal("5800")
    assert fill.option.expiration == date(2026, 10, 9)
    assert (fill.side, fill.effect) == (Side.SELL, Effect.OPEN)
    assert fill.quantity == Decimal("1.0")
    assert fill.price == Decimal("2.5")
    assert fill.value == Decimal("250.0")
    assert fill.fees == Decimal("1.72")
    assert fill.order_id == "98765"


def test_expiration():
    [fill] = parse_transaction(EXPIRATION, "5WX01234")
    assert fill.kind == Kind.EXPIRATION
    assert fill.price is None
    assert fill.value == Decimal("0")
    assert fill.fees == Decimal("0")
    assert fill.action_label == "Expiration"


def test_futures_option():
    [fill] = parse_transaction(FUTURES_OPTION, "5WX01234")
    assert fill.instrument_type == InstrumentType.FUTURE_OPTION
    assert fill.underlying == "/ESZ6"
    assert fill.option.strike == Decimal("5800.00")
    assert fill.option.expiration == date(2026, 10, 9)


def test_money_movement_has_no_fills():
    assert parse_transaction(DEPOSIT, "5WX01234") == []


def test_debit_value_is_negative():
    bought = dict(SELL_TO_OPEN, action="Buy to Close", **{"value-effect": "Debit"})
    [fill] = parse_transaction(bought, "5WX01234")
    assert fill.value == Decimal("-250.0")
    assert (fill.side, fill.effect) == (Side.BUY, Effect.CLOSE)


def test_transactions_reads_every_page(fake_credentials):
    pages = {
        "0": {
            "data": {"items": [SELL_TO_OPEN]},
            "pagination": {"page-offset": 0, "total-pages": 2},
        },
        "1": {"data": {"items": [EXPIRATION]}, "pagination": {"page-offset": 1, "total-pages": 2}},
    }
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=pages[request.url.params["page-offset"]])

    client = TastyTradeClient(
        fake_credentials, http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    raw = client.transactions("5WX01234", date(2025, 10, 9), date(2026, 10, 9))

    assert [t.transaction_id for t in raw] == ["401234567", "401234999"]
    assert raw[0].transaction_type == "Trade"
    assert raw[0].data == SELL_TO_OPEN
    assert seen[0].url.path == "/accounts/5WX01234/transactions"
    assert seen[0].url.params["start-date"] == "2025-10-09"
    assert seen[0].url.params["end-date"] == "2026-10-09"
    assert len(seen) == 2


# A Treasury bill: bought, then matured (#3). Values as TastyTrade sends them.
TBILL_BUY = {
    "id": 501,
    "symbol": "912797VD6",
    "instrument-type": "Fixed Income Security",
    "underlying-symbol": "912797VD6",
    "transaction-type": "Trade",
    "transaction-sub-type": "Buy to Open",
    "description": "Bought 60 912797VD6 @ 996.79",
    "action": "Buy to Open",
    "quantity": "60.0",
    "price": "996.7919999",
    "value": "59807.52",
    "value-effect": "Debit",
    "executed-at": "2026-08-20T19:01:03.087+00:00",
    "order-id": 9001,
}
TBILL_MATURITY = {
    "id": 502,
    "symbol": "912797VD6",
    "instrument-type": "Fixed Income Security",
    "underlying-symbol": "912797VD6",
    "transaction-type": "Receive Deliver",
    "transaction-sub-type": "Maturity",
    "description": "Maturity 60.0 912797VD6 @ 1000",
    "action": "Sell to Close",
    "quantity": "60.0",
    "price": "1000.0",
    "value": "60000.0",
    "value-effect": "Credit",
    "executed-at": "2026-09-22T13:00:00.000+00:00",
}


def test_maturity():
    [fill] = parse_transaction(TBILL_MATURITY, "5WX01234")
    assert fill.kind == Kind.MATURITY
    assert fill.side == Side.SELL
    assert fill.effect == Effect.CLOSE
    assert fill.value == Decimal("60000.0")


def test_a_matured_treasury_bill_is_closed():
    fills = parse_transaction(TBILL_BUY, "5WX01234") + parse_transaction(TBILL_MATURITY, "5WX01234")
    book = build_positions(group_fills(fills))
    assert not book.unmatched
    [position] = book.positions
    assert position.status is Status.CLOSED
    assert position.close_reason == "Matured"
    assert position.realized_pl == Decimal("192.48")


def test_cash_flows_are_money_moved_in_or_out():
    assert cash_flow(DEPOSIT) == Decimal("5000.0")
    withdrawal = dict(DEPOSIT, **{"transaction-sub-type": "Withdrawal", "value-effect": "Debit"})
    assert cash_flow(withdrawal) == Decimal("-5000.0")
    transfer = dict(DEPOSIT, **{"transaction-sub-type": "Transfer", "value-effect": "Debit"})
    assert cash_flow(transfer) == Decimal("-5000.0")
    # Income and trades aren't money moved in or out
    assert cash_flow(dict(DEPOSIT, **{"transaction-sub-type": "Dividend"})) is None
    assert cash_flow(SELL_TO_OPEN) is None
