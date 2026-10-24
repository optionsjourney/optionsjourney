import re
from datetime import UTC, datetime, timedelta

import pytest

from optionsjourney.app import create_app
from optionsjourney.db.models import SyncState
from optionsjourney.web import services


@pytest.fixture
def app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app


def test_no_alerts(app):
    client = app.test_client()
    assert "Nothing needs your attention" in client.get("/alerts").get_data(as_text=True)
    badge = re.search(
        r"<span[^>]*data-alert-badge[^>]*>", client.get("/journal").get_data(as_text=True)
    )
    assert badge and "hidden" in badge.group(0)


def test_a_failed_sync_shows_in_the_nav_and_on_the_alerts_page(app):
    with app.app_context(), services().session_factory() as session:
        now = datetime.now(UTC)
        session.add(
            SyncState(
                broker="schwab",
                account_number="12349999",
                synced_through=now,
                last_attempt_at=now,
                last_error="Schwab needs you to log in again.",
            )
        )
        session.commit()
    client = app.test_client()
    nav = client.get("/journal").get_data(as_text=True)
    assert "data-alert-badge" in nav and ">1<" in nav
    html = client.get("/alerts").get_data(as_text=True)
    assert (
        "Schwab ••9999 didn" in html and "log in again" in html and 'href="/transactions"' in html
    )


ORIGIN = {"Origin": "http://localhost"}


def test_setting_and_handling_a_price_alert(app):
    from decimal import Decimal

    from optionsjourney.core.quotes import Quote
    from optionsjourney.services import user_alerts

    client = app.test_client()
    client.post(
        "/alerts/new",
        data={
            "kind": "price",
            "symbol": "spx",
            "direction": "below",
            "level": "7,700",
            "note": "hedge",
        },
        headers=ORIGIN,
    )
    page = client.get("/alerts").get_data(as_text=True)
    row = page.split('id="price-alerts"')[1].split("</table>")[0]
    assert "SPX" in row and "goes below 7,700.00" in row and "hedge" in row

    class Hub:
        def subscribe(self, keys):
            pass

        def latest(self, key):
            return Quote(
                key, Decimal("7600"), Decimal("7600"), Decimal("7600"), "schwab", datetime.now(UTC)
            )

    with app.app_context():
        user_alerts.check(
            services().session_factory, Hub(), open_pl=lambda k: None, notify=lambda e: None
        )
    page = client.get("/alerts").get_data(as_text=True)
    assert "SPX went below 7,700.00" in page and "Set again" in page
    assert 'class="nav__badge"' in client.get("/journal").get_data(as_text=True)
    [alert] = user_alerts.triggered(services_factory(app))
    client.post(f"/alerts/{alert.id}/dismiss", headers=ORIGIN)
    page = client.get("/alerts").get_data(as_text=True)
    assert "Set again" not in page  # off the list of things to handle
    assert "SPX went below" in page.split('id="recent-alerts"')[1]  # still in the history (#272)


def test_a_bad_alert_is_explained(app):
    html = (
        app.test_client()
        .post(
            "/alerts/new",
            data={"kind": "price", "symbol": "SPX", "direction": "below", "level": "soon"},
            headers=ORIGIN,
            follow_redirects=True,
        )
        .get_data(as_text=True)
    )
    assert "must be a number" in html


def services_factory(app):
    with app.app_context():
        return services().session_factory


def test_analyzer_alerts_api(app):
    client = app.test_client()
    origin = {"Origin": "http://localhost"}
    made = client.post(
        "/api/alerts",
        json={"kind": "underlying", "symbol": "spx", "direction": "above", "threshold": "8000"},
        headers=origin,
    )
    assert made.status_code == 201
    alert_id = made.get_json()["id"]
    spread = {
        "kind": "spread",
        "symbol": "SPX",
        "direction": "below",
        "threshold": "1.25",
        "contracts": [{"expiry": "2026-10-16", "strike": 5800, "right": "put", "qty": -1}],
    }
    assert client.post("/api/alerts", json=spread, headers=origin).status_code == 201
    listed = client.get("/api/alerts?state=armed,fired").get_json()["alerts"]
    assert {a["name"] for a in listed} == {"SPX went above 8,000.00", "SPX spread went below 1.25"}
    client.post(f"/api/alerts/{alert_id}/disable", headers=origin)
    assert len(client.get("/api/alerts?state=armed,fired").get_json()["alerts"]) == 1
    bad = client.post("/api/alerts", json={"kind": "position_pl"}, headers=origin)
    assert bad.status_code == 400 and bad.get_json()["message"]
    assert client.delete(f"/api/alerts/{alert_id}", headers=origin).status_code == 200


def _add_two(app):
    from optionsjourney.services import app_alerts

    with app.app_context():
        for n in (1, 2):
            app_alerts.add(
                services().session_factory,
                dedupe_key=f"k{n}",
                kind="pl_changed",
                title=f"P/L changed {n}",
                detail="detail",
                link="/journal",
                now=datetime.now(UTC),
            )


def test_the_badge_counts_unread_alerts(app):
    _add_two(app)
    html = app.test_client().get("/journal").get_data(as_text=True)
    badge = re.search(r"<span[^>]*data-alert-badge[^>]*>(.*?)</span>", html)
    assert badge and badge.group(1) == "2" and "hidden" not in badge.group(0)


def test_opening_alerts_shows_them_unread_once_then_marks_them_read(app):
    _add_two(app)
    client = app.test_client()
    first = client.get("/alerts").get_data(as_text=True)
    for n in (1, 2):
        assert re.search(
            rf'class="[^"]*alert--unread[^"]*"[^>]*>(?:(?!</li>).)*P/L changed {n}', first, re.S
        )
    second = client.get("/alerts").get_data(as_text=True)
    assert "P/L changed 1" in second and "alert--unread" not in second
    badge = re.search(r"<span[^>]*data-alert-badge[^>]*>", second)
    assert badge and "hidden" in badge.group(0)


def test_the_empty_badge_is_hidden_by_the_stylesheet(app):
    # .nav__badge sets display, which would beat the hidden attribute without this rule
    css = app.test_client().get("/static/css/nav.css").get_data(as_text=True)
    assert re.search(r"\.nav__badge\[hidden\]\s*\{\s*display:\s*none", css)


def test_opening_alerts_marks_only_the_ones_it_shows(app):
    """The page lists the newest 50: an older unread alert stays unread."""
    from optionsjourney.services import app_alerts

    with app.app_context():
        for n in range(51):
            app_alerts.add(
                services().session_factory,
                dedupe_key=f"k{n}",
                kind="settled",
                title=f"Settled {n}",
                detail="detail",
                link="/journal",
                now=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=n),
            )
    app.test_client().get("/alerts")
    with app.app_context():
        assert app_alerts.unread_count(services().session_factory) == 1


def test_set_again_and_dismiss_have_tips(app):
    from decimal import Decimal

    from optionsjourney.core.quotes import Quote
    from optionsjourney.services import user_alerts

    client = app.test_client()
    client.post("/alerts/new", data={"kind": "price", "symbol": "spx", "direction": "below",
                                     "level": "7,700"}, headers=ORIGIN)  # fmt: skip

    class Hub:
        def subscribe(self, keys):
            pass

        def latest(self, key):
            return Quote(key, Decimal("7600"), Decimal("7600"), Decimal("7600"), "schwab",
                         datetime.now(UTC))  # fmt: skip

    with app.app_context():
        user_alerts.check(services().session_factory, Hub(), open_pl=lambda k: None,
                          notify=lambda e: None)  # fmt: skip
    html = client.get("/alerts").get_data(as_text=True)
    assert re.search(r'aria-describedby="tip-alerts-set-again-\d+"', html)
    assert re.search(r'id="tip-alerts-dismiss-\d+"', html)


def _badge(client):
    html = client.get("/journal").get_data(as_text=True)
    return re.search(r"<span[^>]*data-alert-badge[^>]*>(.*?)</span>", html)


def _fire_spx_alert(app, client):
    from decimal import Decimal

    from optionsjourney.core.quotes import Quote
    from optionsjourney.services import user_alerts

    client.post(
        "/alerts/new",
        data={"kind": "price", "symbol": "spx", "direction": "below", "level": "7,700"},
        headers=ORIGIN,
    )

    class Hub:
        def subscribe(self, keys):
            pass

        def latest(self, key):
            return Quote(
                key, Decimal("7600"), Decimal("7600"), Decimal("7600"), "schwab", datetime.now(UTC)
            )

    with app.app_context():
        user_alerts.check(
            services().session_factory, Hub(), open_pl=lambda k: None, notify=lambda e: None
        )


def test_a_stale_sync_is_listed_but_doesnt_hold_the_badge(app):
    """#272: a note (worth a look) shows on the page and in the bell, but only
    problems keep the badge up until they're fixed."""
    with app.app_context(), services().session_factory() as session:
        then = datetime.now(UTC) - timedelta(days=4)
        session.add(
            SyncState(broker="schwab", account_number="12349999", synced_through=then,
                      last_attempt_at=then)
        )  # fmt: skip
        session.commit()
    client = app.test_client()
    assert "Schwab last synced 4 days ago" in client.get("/alerts").get_data(as_text=True)
    assert "hidden" in _badge(client).group(0)
    standing = client.get("/api/alerts/recent").get_json()["standing"]
    assert [(a["level"], a["title"]) for a in standing] == [
        ("warning", "Schwab last synced 4 days ago")
    ]


def test_a_fired_alert_counts_until_the_bell_shows_it(app):
    """#272: an alert that went off is new until seen; it stays on the page to set again."""
    client = app.test_client()
    _fire_spx_alert(app, client)
    assert _badge(client).group(1) == "1"
    data = client.get("/api/alerts/recent").get_json()
    [fired] = [a for a in data["recent"] if "SPX went below" in a["title"]]
    assert data["standing"] == []  # #275: listed once, as the new alert
    assert fired["unread"] is True and fired["link"]
    assert data["unread"] == 0
    assert "hidden" in _badge(client).group(0)
    again = client.get("/api/alerts/recent").get_json()["recent"]
    assert [a["unread"] for a in again] == [False]
    assert "Set again" in client.get("/alerts").get_data(as_text=True)


def test_the_bell_lists_problems_first_and_the_newest_alerts(app):
    from optionsjourney.services import app_alerts

    with app.app_context(), services().session_factory() as session:
        now = datetime.now(UTC)
        session.add(
            SyncState(broker="schwab", account_number="12349999", synced_through=now,
                      last_attempt_at=now, last_error="Log in.")
        )  # fmt: skip
        session.add(
            SyncState(broker="tastytrade", account_number="5WX0001",
                      synced_through=now - timedelta(days=5), last_attempt_at=now)
        )  # fmt: skip
        session.commit()
        for n in range(20):
            app_alerts.add(
                services().session_factory, dedupe_key=f"k{n}", kind="settled",
                title=f"Settled {n}", detail="d", link="/journal",
                now=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=n),
            )  # fmt: skip
    client = app.test_client()
    data = client.get("/api/alerts/recent").get_json()
    assert [a["level"] for a in data["standing"]] == ["problem", "warning"]
    assert data["standing"][0]["link"] == "/transactions"
    assert [a["title"] for a in data["recent"]][:2] == ["Settled 19", "Settled 18"]
    assert len(data["recent"]) == 15
    assert data["recent"][0]["created_at"].startswith("2026-10-01T")
    # only the 15 shown are marked read; the problem still holds the badge
    assert data["unread"] == 5
    assert _badge(client).group(1) == "6"


def test_the_page_calls_its_list_recent(app):
    _add_two(app)
    html = app.test_client().get("/alerts").get_data(as_text=True)
    assert ">Recent</h2>" in html and ">Unread</h2>" not in html


def test_the_page_has_price_alerts_and_trades_tabs(app):
    """#274: as Tracker2's Alerts page, with the price alerts first."""
    client = app.test_client()
    client.post(
        "/alerts/new",
        data={"kind": "price", "symbol": "spx", "direction": "below", "level": "7,700"},
        headers=ORIGIN,
    )
    html = client.get("/alerts").get_data(as_text=True)
    assert html.index('id="price"') < html.index('id="trades"')
    assert 'data-tab="price"' in html and 'data-tab="trades"' in html
    row = html.split('id="price-alerts"')[1].split("</table>")[0]
    assert "SPX" in row and "goes below 7,700.00" in row and "Armed" in row
    assert "Turn off" in row
    for show in ("active", "armed", "fired", "all"):
        assert f"show={show}" in html


def test_turning_an_alert_off_from_the_tab(app):
    from optionsjourney.services import user_alerts

    client = app.test_client()
    client.post(
        "/alerts/new",
        data={"kind": "price", "symbol": "spx", "direction": "below", "level": "7,700"},
        headers=ORIGIN,
    )
    [alert] = user_alerts.active(services_factory(app))
    client.post(f"/alerts/{alert.id}/disable", headers=ORIGIN)
    assert user_alerts.active(services_factory(app)) == []
    html = client.get("/alerts?show=all").get_data(as_text=True)
    assert "Off" in html.split('id="price-alerts"')[1].split("</table>")[0]
