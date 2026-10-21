from decimal import Decimal

from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.holdings import StockPosition
from optionsjourney.services.holdings import broker_positions


class Good:
    broker, name = "schwab", "Schwab"

    def stock_positions(self):
        return [StockPosition("schwab:1", "AAPL", Decimal("1"), Decimal("2"))]


class Bad:
    broker, name = "schwab", "Other"

    def stock_positions(self):
        raise BrokerError("down")


def test_strict_returns_none_when_a_broker_fails():
    assert broker_positions([Good(), Bad()], strict=True) is None
    assert [p.ticker for p in broker_positions([Good(), Bad()])] == ["AAPL"]
    assert len(broker_positions([Good()], strict=True)) == 1


def test_strict_returns_none_when_credentials_are_unavailable():
    class NoKey(Bad):
        def stock_positions(self):
            raise CredentialsUnavailable("locked")

    assert broker_positions([Good(), NoKey()], strict=True) is None
