from decimal import Decimal

from optionsjourney.brokers.credentials import CredentialsUnavailable, NotConnected
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


def test_strict_skips_a_broker_that_was_never_connected():
    class Off:
        broker, name = "tastytrade", "TastyTrade"

        def accounts(self):
            raise NotConnected("TastyTrade isn't connected")

        def stock_positions(self, number):
            raise AssertionError("no accounts to read")

    skipped = set()
    found = broker_positions([Good(), Off()], strict=True, skipped=skipped)
    assert [p.ticker for p in found] == ["AAPL"]
    assert skipped == {"tastytrade"}
    assert broker_positions([Good(), Off()], strict=True) is not None


def test_kept_positions_asks_the_brokers_once_for_the_page_and_its_chart():
    # #304: the value chart reuses the Stocks page's minute-old positions
    from optionsjourney.services.holdings import kept_positions

    class Counting(Good):
        asked = 0

        def stock_positions(self):
            Counting.asked += 1
            return super().stock_positions()

    clients = [Counting()]
    assert len(kept_positions(clients)) == 1  # the page
    assert len(kept_positions(clients, strict=True)) == 1  # its chart
    assert Counting.asked == 1


def test_kept_positions_strict_is_none_while_a_broker_is_down():
    from optionsjourney.services.holdings import kept_positions

    clients = [Good(), Bad()]
    assert len(kept_positions(clients)) == 1  # the page shows what it has
    assert kept_positions(clients, strict=True) is None  # the chart needs every broker
