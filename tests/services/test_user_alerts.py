"""Alerts the user sets (#27): prices and trades' open P/L."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from optionsjourney.core.quotes import Quote
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services import user_alerts
from optionsjourney.services.user_alerts import AlertError

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


class Hub:
    def __init__(self, marks):
        self.marks, self.subscribed = marks, set()

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        m = self.marks.get(key)
        return (
            None if m is None else Quote(key, Decimal(m), Decimal(m), Decimal(m), "tastytrade", NOW)
        )


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def test_a_price_alert_goes_off_once_when_crossed(factory):
    user_alerts.create_price(factory, "spx", "below", "7700", note="add hedges")
    sent = []
    assert (
        user_alerts.check(
            factory, Hub({"SPX": "7750"}), open_pl=lambda key: None, notify=sent.append, now=NOW
        )
        == []
    )
    [fired] = user_alerts.check(
        factory, Hub({"SPX": "7690"}), open_pl=lambda key: None, notify=sent.append, now=NOW
    )
    assert (
        fired.title == "SPX went below 7,700.00"
        and "now 7,690.00" in fired.detail
        and "add hedges" in fired.detail
    )
    assert sent == [fired]
    # one-shot: it doesn't go off again until set again
    assert (
        user_alerts.check(
            factory, Hub({"SPX": "7600"}), open_pl=lambda key: None, notify=sent.append, now=NOW
        )
        == []
    )
    [shown] = user_alerts.triggered(factory)
    user_alerts.rearm(factory, shown.id)
    assert (
        len(
            user_alerts.check(
                factory, Hub({"SPX": "7600"}), open_pl=lambda key: None, notify=sent.append, now=NOW
            )
        )
        == 1
    )


def test_a_trade_open_pl_alert(factory):
    user_alerts.create_trade(factory, "tastytrade:5WX01234:1", "SPX Iron Condor", "below", "-500")
    fired = user_alerts.check(
        factory, Hub({}), open_pl=lambda key: Decimal("-650"), notify=lambda a: None, now=NOW
    )
    assert fired[0].title == "SPX Iron Condor open P/L went below -$500"
    assert "now -$650" in fired[0].detail


def test_no_price_no_alert(factory):
    user_alerts.create_price(factory, "SPY", "above", "800")
    assert (
        user_alerts.check(
            factory, Hub({}), open_pl=lambda key: None, notify=lambda a: None, now=NOW
        )
        == []
    )


def test_bad_alerts_are_refused(factory):
    with pytest.raises(AlertError, match="number"):
        user_alerts.create_price(factory, "SPY", "above", "lots")
    with pytest.raises(AlertError, match="above or below"):
        user_alerts.create_price(factory, "SPY", "sideways", "800")
    with pytest.raises(AlertError, match="symbol"):
        user_alerts.create_price(factory, "", "above", "800")


def test_dismissing_and_deleting(factory):
    alert_id = user_alerts.create_price(factory, "SPY", "above", "700")
    user_alerts.check(
        factory, Hub({"SPY": "778"}), open_pl=lambda key: None, notify=lambda a: None, now=NOW
    )
    user_alerts.dismiss(factory, alert_id)
    assert user_alerts.triggered(factory) == []
    user_alerts.delete(factory, alert_id)
    assert user_alerts.active(factory) == [] and user_alerts.triggered(factory) == []


SPREAD = [
    {"expiry": "2026-10-16", "strike": 5800, "right": "put", "qty": -1},
    {"expiry": "2026-10-16", "strike": 5790, "right": "put", "qty": 1},
]


def test_a_spread_alert_watches_the_net_mid_of_its_legs(factory):
    alert_id = user_alerts.create_spread(factory, "SPX", SPREAD, "below", "0.90")
    hub = Hub({"SPXW  261016P05800000": "3.00", "SPXW  261016P05790000": "2.00"})
    sent = []
    assert user_alerts.check(factory, hub, lambda key: None, sent.append, now=NOW) == []
    assert hub.subscribed == {"SPXW  261016P05800000", "SPXW  261016P05790000"}
    hub.marks["SPXW  261016P05800000"] = "2.80"  # the 1.00 credit spread is now 0.80
    fired = user_alerts.check(factory, hub, lambda key: None, sent.append, now=NOW)
    assert [f.id for f in fired] == [alert_id]
    assert fired[0].title == "SPX spread went below 0.90" and "now 0.80" in fired[0].detail


def test_spread_alerts_are_checked(factory):
    with pytest.raises(AlertError):
        user_alerts.create_spread(factory, "SPX", [], "below", "1")
    with pytest.raises(AlertError):
        user_alerts.create_spread(factory, "/ES", SPREAD, "below", "1")
    with pytest.raises(AlertError):
        user_alerts.create_spread(factory, "SPX", [dict(SPREAD[0], right="x")], "below", "1")


def test_states_for_the_analyzer(factory):
    armed = user_alerts.create_price(factory, "SPX", "above", "1")
    off = user_alerts.create_price(factory, "SPY", "above", "1")
    user_alerts.disable(factory, off)
    states = {a["id"]: a["state"] for a in user_alerts.listing(factory)}
    assert states == {armed: "armed", off: "off"}
    assert [a["id"] for a in user_alerts.listing(factory, {"armed"})] == [armed]
