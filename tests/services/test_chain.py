from datetime import UTC, datetime
from decimal import Decimal

from optionsjourney.services import chain
from tests.services.bot_fakes import FakeHub

AT = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)


def test_a_hub_quote_carries_its_time_for_the_bots_freshness_check():
    hub = FakeHub()
    hub.set("SPXW  261013P05700000", 1.0, 1.2, AT)
    found = chain._side_quote(hub, "SPXW  261013P05700000", symbol="S")
    assert found == {"bid": 1.0, "ask": 1.2, "at": AT, "symbol": "S"}
    assert chain._side_quote(hub, "nothing") is None


def test_a_hub_quote_carries_the_streams_greeks_with_their_time():
    hub = FakeHub()
    hub.set("K", 1.0, 1.2, AT)
    hub.set_delta("K", "-0.21", AT)
    found = chain._side_quote(hub, "K", symbol="S")
    assert (found["stream_delta"], found["stream_delta_at"]) == (Decimal("-0.21"), AT)
