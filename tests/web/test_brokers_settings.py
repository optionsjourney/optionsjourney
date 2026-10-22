"""Settings -> Brokers: connecting a broker in local mode (#16, #17)."""

import httpx

from optionsjourney.app import create_app
from optionsjourney.brokers.credentials.local import LocalProvider
from optionsjourney.brokers.credentials.store import MemoryStore

ORIGIN = {"Origin": "http://localhost"}


def _client(settings, accept=True):
    def handler(request):
        if accept:
            return httpx.Response(200, json={"access_token": "a", "expires_in": 900})
        return httpx.Response(401, json={"error": "invalid_grant"})

    provider = LocalProvider(
        MemoryStore(), http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    app = create_app(settings, credentials=provider, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app.test_client(), provider


def test_brokers_section_shows_connect_forms(settings):
    client, _ = _client(settings)
    html = client.get("/settings").get_data(as_text=True)
    assert "Brokers" in html and "Connect TastyTrade" in html
    assert "Not connected" in html


def test_connecting_tastytrade(settings):
    client, provider = _client(settings)
    response = client.post(
        "/settings/brokers/tastytrade",
        data={"client_secret": "s", "refresh_token": "r", "risk": "1"},
        headers=ORIGIN,
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert "TastyTrade connected" in html
    assert provider.connected("tastytrade")
    assert "Disconnect" in html


def test_a_refused_login_is_explained_and_nothing_kept(settings):
    client, provider = _client(settings, accept=False)
    response = client.post(
        "/settings/brokers/tastytrade",
        data={"client_secret": "s", "refresh_token": "r", "risk": "1"},
        headers=ORIGIN,
        follow_redirects=True,
    )
    assert "didn&#39;t accept" in response.get_data(as_text=True)
    assert not provider.connected("tastytrade")


def test_disconnecting(settings):
    client, provider = _client(settings)
    client.post(
        "/settings/brokers/tastytrade",
        data={"client_secret": "s", "refresh_token": "r", "risk": "1"},
        headers=ORIGIN,
    )
    client.post("/settings/brokers/tastytrade/disconnect", headers=ORIGIN)
    assert not provider.connected("tastytrade")


def test_gateway_mode_explains_instead(gateway_settings, fake_credentials):
    app = create_app(gateway_settings, credentials=fake_credentials, broker_clients=lambda: [])
    html = app.test_client().get("/settings").get_data(as_text=True)
    assert "Tracker2 server" in html and "Connect TastyTrade" not in html


def _schwab_client(settings):
    def handler(request):
        return httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r", "expires_in": 1800}
        )

    provider = LocalProvider(
        MemoryStore(), http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    app = create_app(settings, credentials=provider, broker_clients=lambda: [])
    return app.test_client(), provider


def test_connecting_schwab_in_two_steps(settings):
    client, provider = _schwab_client(settings)
    start = client.post(
        "/settings/brokers/schwab/start",
        data={
            "app_key": "key-1",
            "app_secret": "secret-1",
            "callback_url": "https://127.0.0.1",
            "risk": "1",
        },
        headers=ORIGIN,
    )
    assert start.status_code == 302
    assert start.location.startswith("https://api.schwabapi.com/v1/oauth/authorize?client_id=key-1")
    waiting = client.get("/settings").get_data(as_text=True)
    assert "Paste the address" in waiting  # the second step is waiting
    done = client.post(
        "/settings/brokers/schwab/finish",
        data={"redirected_url": "https://127.0.0.1/?code=abc"},
        headers=ORIGIN,
        follow_redirects=True,
    ).get_data(as_text=True)
    assert "Schwab connected" in done and "Reconnect Schwab" in done
    assert provider.connected("schwab")


def test_schwab_login_running_out_is_an_alert(settings):
    from datetime import UTC, datetime, timedelta

    client, provider = _schwab_client(settings)
    six_days_ago = (datetime.now(UTC) - timedelta(days=6, hours=12)).isoformat()
    provider.store.set(
        "schwab",
        {
            "app_key": "k",
            "app_secret": "s",
            "callback_url": "https://127.0.0.1",
            "refresh_token": "r",
            "logged_in_at": six_days_ago,
        },
    )
    html = client.get("/alerts").get_data(as_text=True)
    assert "Schwab login runs out" in html and 'href="/settings#brokers"' in html


def test_the_app_details_can_be_changed_before_logging_in(settings):
    # e.g. the callback URL was entered without its port
    client, provider = _schwab_client(settings)
    client.post(
        "/settings/brokers/schwab/start",
        data={
            "app_key": "key-1",
            "app_secret": "secret-1",
            "callback_url": "https://127.0.0.1",
            "risk": "1",
        },
        headers=ORIGIN,
    )
    page = client.get("/settings").get_data(as_text=True)
    assert "Change the app" in page and 'value="https://127.0.0.1"' in page
    again = client.post(
        "/settings/brokers/schwab/start",
        data={"app_key": "key-1", "app_secret": "", "callback_url": "https://127.0.0.1:8080"},
        headers=ORIGIN,
    )
    assert "redirect_uri=https%3A%2F%2F127.0.0.1%3A8080" in again.location
    saved = provider.store.get("schwab")
    assert saved["callback_url"] == "https://127.0.0.1:8080" and saved["app_secret"] == "secret-1"


def test_starting_over_clears_the_app_details(settings):
    client, provider = _schwab_client(settings)
    client.post(
        "/settings/brokers/schwab/start",
        data={
            "app_key": "key-1",
            "app_secret": "secret-1",
            "callback_url": "https://127.0.0.1",
            "risk": "1",
        },
        headers=ORIGIN,
    )
    assert "Start over" in client.get("/settings").get_data(as_text=True)
    client.post("/settings/brokers/schwab/disconnect", headers=ORIGIN)
    assert provider.store.get("schwab") is None


def test_a_refused_schwab_code_is_explained_in_the_brokers_section(settings):
    def handler(request):
        return httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "Authorization code expired"}
        )

    provider = LocalProvider(
        MemoryStore(), http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    client = create_app(settings, credentials=provider, broker_clients=lambda: []).test_client()
    client.post(
        "/settings/brokers/schwab/start",
        data={
            "app_key": "k",
            "app_secret": "s",
            "callback_url": "https://127.0.0.1:8080",
            "risk": "1",
        },
        headers=ORIGIN,
    )
    html = client.post(
        "/settings/brokers/schwab/finish",
        data={"redirected_url": "https://127.0.0.1:8080/?code=old"},
        headers=ORIGIN,
        follow_redirects=True,
    ).get_data(as_text=True)
    brokers_card = html.split('id="brokers"')[1].split("</section>")[0]
    assert (
        "Authorization code expired" in brokers_card
    )  # Schwab's own reason, where the user is looking


def test_broker_status_pills(settings):
    """Connected / Not connected / Log in again soon, each explained (#73)."""
    from datetime import UTC, datetime, timedelta

    client, provider = _schwab_client(settings)
    html = client.get("/settings").get_data(as_text=True)
    assert "Not connected" in html and 'aria-describedby="tip-tastytrade-status"' in html

    six_days_ago = (datetime.now(UTC) - timedelta(days=6, hours=12)).isoformat()
    provider.store.set(
        "schwab",
        {
            "app_key": "k",
            "app_secret": "s",
            "callback_url": "https://127.0.0.1",
            "refresh_token": "r",
            "logged_in_at": six_days_ago,
        },
    )
    html = client.get("/settings").get_data(as_text=True)
    assert "Log in again soon" in html and "pill--warn" in html
    assert 'aria-describedby="tip-schwab-reconnect"' in html


def test_connecting_a_broker_asks_the_risk_box_once(settings):
    client, provider = _client(settings)
    assert 'name="risk"' in client.get("/settings").get_data(as_text=True)
    unticked = client.post(
        "/settings/brokers/tastytrade",
        data={"client_secret": "s", "refresh_token": "r"},
        headers=ORIGIN,
        follow_redirects=True,
    )
    assert "Tick the box" in unticked.get_data(as_text=True)
    assert not provider.connected("tastytrade")
    client.post(
        "/settings/brokers/tastytrade",
        data={"client_secret": "s", "refresh_token": "r", "risk": "1"},
        headers=ORIGIN,
    )
    assert provider.connected("tastytrade")
    # Ticked once: the Schwab form doesn't ask again
    page = client.get("/settings").get_data(as_text=True)
    assert 'name="risk"' not in page
    start = client.post(
        "/settings/brokers/schwab/start",
        data={"app_key": "k", "app_secret": "s", "callback_url": "https://127.0.0.1"},
        headers=ORIGIN,
    )
    assert start.location.startswith("https://api.schwabapi.com/")


def test_schwab_login_needs_the_risk_box(settings):
    client, provider = _schwab_client(settings)
    start = client.post(
        "/settings/brokers/schwab/start",
        data={"app_key": "k", "app_secret": "s", "callback_url": "https://127.0.0.1"},
        headers=ORIGIN,
    )
    assert not start.location.startswith("https://")
    assert provider.store.get("schwab") is None


def test_reconnecting_a_login_from_before_the_box_doesnt_ask(settings):
    from datetime import UTC, datetime

    client, provider = _schwab_client(settings)
    provider.store.set(
        "schwab",
        {
            "app_key": "k",
            "app_secret": "s",
            "callback_url": "https://127.0.0.1",
            "refresh_token": "r",
            "logged_in_at": datetime.now(UTC).isoformat(),
        },
    )
    start = client.post(
        "/settings/brokers/schwab/start",
        data={"app_key": "", "app_secret": "", "callback_url": ""},
        headers=ORIGIN,
    )
    assert start.location.startswith("https://api.schwabapi.com/")
