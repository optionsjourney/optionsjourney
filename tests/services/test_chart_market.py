"""Market data the chart grid asks for by REST (#25)."""

from datetime import datetime
from decimal import Decimal

import pytest

from optionsjourney.core.market import MARKET_TZ
from optionsjourney.services.chart_market import (
    FuturesContracts,
    MarketError,
    futures_prev_close,
    history_end,
)


def et(*args):
    return datetime(*args, tzinfo=MARKET_TZ)


def test_history_ends_at_the_last_close_outside_the_session():
    assert history_end(et(2026, 10, 9, 11, 0)) == et(2026, 10, 9, 11, 0)  # Friday, open
    assert history_end(et(2026, 10, 10, 11, 0)) == et(2026, 10, 9, 16, 0)  # Saturday
    assert history_end(et(2026, 10, 11, 11, 0)) == et(2026, 10, 9, 16, 0)  # Sunday
    assert history_end(et(2026, 10, 13, 8, 0)) == et(2026, 10, 12, 16, 0)  # before the open


class FakeTasty:
    name = "TastyTrade"

    def __init__(self):
        self.calls = 0

    def futures(self, code):
        self.calls += 1
        assert code == "ES"
        return [
            {"symbol": "/ESH7", "active-month": False, "streamer-symbol": "/ESH27:XCME"},
            {"symbol": "/ESZ6", "active-month": True, "streamer-symbol": "/ESZ26:XCME"},
        ]

    def market_data(self, by_type):
        assert by_type == {"future": ["/ESZ6"]}
        return [{"symbol": "/ESZ6", "prev-close": "7816.25"}]


def test_front_contract_is_the_active_month_and_kept():
    tasty, contracts = FakeTasty(), FuturesContracts()
    assert contracts.front([tasty], "/es") == "/ESZ26:XCME"
    assert contracts.front([tasty], "/ES") == "/ESZ26:XCME"
    assert tasty.calls == 1
    assert contracts.front([tasty], "SPY") is None


def test_futures_prev_close_from_tastytrade_when_schwab_isnt_there():
    assert futures_prev_close([FakeTasty()], "/ES") == Decimal("7816.25")
    with pytest.raises(MarketError) as e:
        futures_prev_close([], "SPY")
    assert e.value.status == 400
