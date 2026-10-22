"""The setup wizard (#15, docs/decisions/0007)."""

import httpx
import pytest

from optionsjourney.app import create_app
from optionsjourney.brokers.credentials.local import LocalProvider
from optionsjourney.brokers.credentials.store import MemoryStore
from optionsjourney.services import journal, setup
from optionsjourney.web import services

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture
def fresh_app(settings):
    """A first start: the risk box on the welcome step not yet ticked."""

    def handler(request):
        return httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r", "expires_in": 900}
        )

    provider = LocalProvider(
        MemoryStore(), http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    app = create_app(settings, credentials=provider, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app


@pytest.fixture
def app(fresh_app):
    with fresh_app.app_context():
        setup.accept_risk(services().session_factory)
    return fresh_app


def test_first_start_opens_the_wizard(app):
    response = app.test_client().get("/")
    assert response.status_code == 302 and response.location.endswith("/setup")


def test_gateway_mode_never_opens_the_wizard(gateway_settings, fake_credentials):
    app = create_app(gateway_settings, credentials=fake_credentials, broker_clients=lambda: [])
    assert app.test_client().get("/").location.endswith("/journal")


def test_welcome(app):
    html = app.test_client().get("/setup").get_data(as_text=True)
    assert "Welcome" in html and "only on this computer" in html
    assert "checks GitHub for a new version" in html and "Settings → This computer" in html


def test_choosing_brokers_decides_the_next_steps(app):
    client = app.test_client()
    response = client.post("/setup/brokers", data={"brokers": ["tastytrade"]}, headers=ORIGIN)
    assert response.location.endswith("/setup/tastytrade")
    response = client.post("/setup/brokers", data={"brokers": []}, headers=ORIGIN)  # manual only
    assert response.location.endswith("/setup/style")


def test_connecting_tastytrade_in_the_wizard_comes_back_to_it(app):
    client = app.test_client()
    client.post("/setup/brokers", data={"brokers": ["tastytrade", "schwab"]}, headers=ORIGIN)
    page = client.get("/setup/tastytrade").get_data(as_text=True)
    assert "Connect TastyTrade" in page
    response = client.post(
        "/settings/brokers/tastytrade",
        data={"client_secret": "s", "refresh_token": "r", "next": "/setup/schwab"},
        headers=ORIGIN,
    )
    assert response.location.endswith("/setup/schwab")


def test_schwab_can_be_finished_later(app):
    client = app.test_client()
    client.post("/setup/brokers", data={"brokers": ["schwab"]}, headers=ORIGIN)
    html = client.get("/setup/schwab").get_data(as_text=True)
    assert "Finish later" in html and "few days" in html


def test_redirects_after_connecting_stay_in_the_app(app):
    response = app.test_client().post(
        "/settings/brokers/tastytrade",
        data={"client_secret": "s", "refresh_token": "r", "next": "https://evil.example/"},
        headers=ORIGIN,
    )
    assert "evil" not in response.location


def test_trading_style_sets_the_starter_tags(app):
    client = app.test_client()
    client.post(
        "/setup/style", data={"styles": ["Wheel", "Strangles", "Covered Calls"]}, headers=ORIGIN
    )
    with app.app_context():
        names = {t.name for t in journal.load_journal(services().session_factory).tags}
    assert {"Wheel", "Strangles", "Covered Calls"} <= names
    assert "0DTE" not in names  # an unused starter tag that wasn't picked goes
    assert "Stocks" in names  # not a style: it stays


def test_backups_step_saves_the_backup_settings(app, tmp_path):
    client = app.test_client()
    client.post(
        "/setup/backups",
        data={"backup_folder": str(tmp_path / "b"), "backup_frequency": "weekly"},
        headers=ORIGIN,
    )
    assert 'value="weekly" selected' in client.get("/settings").get_data(as_text=True)


def test_finishing_ends_the_wizard(app):
    client = app.test_client()
    html = client.get("/setup/done").get_data(as_text=True)
    assert "Sync now" in html
    client.post("/setup/finish", headers=ORIGIN)
    assert client.get("/").location.endswith("/journal")


def test_the_wizard_can_be_run_again_from_settings(app):
    client = app.test_client()
    client.post("/setup/finish", headers=ORIGIN)
    assert 'href="/setup"' in client.get("/settings").get_data(as_text=True)


def test_reaching_the_last_step_counts_as_finished(app):
    client = app.test_client()
    client.get("/setup/done")
    assert client.get("/").location.endswith("/journal")


def test_setup_asks_the_risk_box_first_and_only_once(fresh_app):
    """Decision 0093: not financial advice, the user is responsible for every order."""
    client = fresh_app.test_client()
    html = client.get("/setup").get_data(as_text=True)
    assert 'name="risk"' in html and "not financial advice" in html
    assert client.get("/setup/style").location.endswith("/setup")
    r = client.post("/setup/accept", data={}, headers=ORIGIN)
    assert r.location.endswith("/setup")
    r = client.post("/setup/accept", data={"risk": "1"}, headers=ORIGIN)
    assert r.location.endswith("/setup/brokers")
    assert client.get("/setup/style").status_code == 200
    assert 'name="risk"' not in client.get("/setup").get_data(as_text=True)
