"""#178: in-app trade alerts, recorded from the journal after a sync."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from tests.web.test_journal_pages import ORIGIN, FakeTastyTrade, _tasty

from optionsjourney.app import create_app
from optionsjourney.db.models import TradeEvent
from optionsjourney.services import settings as settings_service
from optionsjourney.services import trade_events

AT = datetime.now(UTC) - timedelta(hours=1)  # when the test's trade opened


def fill(*args, at=AT, **kwargs):
    return {**_tasty(*args, **kwargs), "executed-at": at.isoformat()}


PUT_SPREAD = [
    fill(1, "Sell to Open", "0.29", "Credit", "SPXW  261120P05800000", order_id=10),
    fill(2, "Buy to Open", "0.04", "Debit", "SPXW  261120P05780000", order_id=10),
]
STOPPED = [  # the short put's stop, then the wing sold on its own
    fill(3, "Buy to Close", "0.34", "Debit", "SPXW  261120P05800000", order_id=11,
         at=AT + timedelta(seconds=30)),
    fill(4, "Sell to Close", "0.03", "Credit", "SPXW  261120P05780000", order_id=12,
         at=AT + timedelta(seconds=40)),
]


@pytest.fixture
def app(settings, fake_credentials):
    broker = FakeTastyTrade(list(PUT_SPREAD))
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    app.config["TESTING"] = True
    app.broker = broker
    app.test_client().post("/sync", headers=ORIGIN)
    return app


def sf(app):
    return app.extensions["optionsjourney"].session_factory


def test_a_stop_then_its_wing_record_partly_closed_then_closed_once(app):
    now = datetime.now(UTC)
    opened = trade_events.record(sf(app), now)
    assert [e.kind for e in opened] == ["open"]
    assert opened[0].name == "SPX Put Credit Spread" and opened[0].amount == "$25.00 received"
    app.broker.rows.extend(STOPPED)
    app.test_client().post("/sync", headers=ORIGIN)
    closed = trade_events.record(sf(app), now)
    assert [e.kind for e in closed] == ["partial_close", "close"]
    assert [e.label for e in closed] == ["Partly closed", "Closed"]
    assert trade_events.record(sf(app), now) == []  # the same fills never alert twice
    with sf(app)() as session:
        assert len(session.scalars(select(TradeEvent)).all()) == 3


def _forget(app):
    with sf(app)() as session:
        session.query(TradeEvent).delete()
        session.commit()


def test_only_recent_fills_pop_up_and_the_setting_turns_popups_off(app):
    [late] = trade_events.record(sf(app), AT + timedelta(minutes=11))
    assert late.popup is False  # caught up later: listed on the Alerts page, no popup
    _forget(app)
    [fresh] = trade_events.record(sf(app), AT + timedelta(seconds=5))
    assert fresh.popup is True
    _forget(app)
    settings_service.save_section(sf(app), "notifications", {"trade_popups": "off"})
    [quiet] = trade_events.record(sf(app), AT + timedelta(seconds=5))
    assert quiet.popup is False


def test_the_alerts_page_lists_trade_events_and_filters_by_type(app):
    trade_events.record(sf(app), datetime.now(UTC))
    client = app.test_client()
    html = client.get("/alerts").get_data(as_text=True)
    assert "SPX Put Credit Spread" in html and "Opened" in html and "$25.00 received" in html
    html = client.get("/alerts?kind=close").get_data(as_text=True)
    assert "No trades yet." in html


def test_every_page_loads_the_popups(app):
    html = app.test_client().get("/alerts").get_data(as_text=True)
    assert "trade_toasts.js" in html and "socket.io" in html


def test_a_closed_trade_alert_lists_the_strikes_it_held(app):
    """#212: the close says which strikes the trade held; the Alerts page shows it too."""
    now = datetime.now(UTC)
    [opened] = trade_events.record(sf(app), now)
    assert opened.detail == ""
    app.broker.rows.extend(STOPPED)
    app.test_client().post("/sync", headers=ORIGIN)
    partial, closed = trade_events.record(sf(app), now)
    assert partial.detail == ""
    assert closed.detail == "Strikes: 5800/5780"
    assert closed.as_dict()["detail"] == "Strikes: 5800/5780"
    html = app.test_client().get("/alerts?kind=close").get_data(as_text=True)
    assert "Strikes: 5800/5780" in html
