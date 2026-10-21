from decimal import Decimal

import pytest

from optionsjourney.app import create_app
from optionsjourney.brokers import guard
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.accounts import Balance, BrokerAccount
from optionsjourney.web.formatting import money


class FakeBroker:
    def __init__(self, name="TastyTrade", accounts=(), balances=None, fail_accounts=None):
        self.name = name
        self.broker = name.lower()
        self.calls = 0
        self._accounts = list(accounts)
        self._balances = balances or {}
        self._fail_accounts = fail_accounts

    def accounts(self):
        self.calls += 1
        if self._fail_accounts:
            raise BrokerError(self._fail_accounts)
        return self._accounts

    def balance(self, number):
        if number not in self._balances:
            raise BrokerError("TastyTrade answered 502.")
        return self._balances[number]


def _app(settings, credentials, *brokers):
    app = create_app(settings, credentials=credentials, broker_clients=lambda: list(brokers))
    app.config["TESTING"] = True
    return app


def test_accounts_page_lists_accounts_and_total(settings, fake_credentials):
    alpha = BrokerAccount("tastytrade", "5WX01234", "Alpha")
    roth = BrokerAccount("tastytrade", "5WY05678", None, "Roth IRA")
    tastytrade = FakeBroker(
        "TastyTrade",
        [alpha, roth],
        {"5WX01234": Balance(Decimal("25000"), Decimal("5000"), Decimal("12000"))},
    )
    schwab = FakeBroker(
        "Schwab",
        [BrokerAccount("schwab", "12349999", None, "Margin")],
        {"12349999": Balance(Decimal("1000.25"), Decimal("0"), Decimal("0"))},
    )
    client = _app(settings, fake_credentials, tastytrade, schwab).test_client()
    html = client.get("/accounts/live").get_data(as_text=True)

    assert "Alpha" in html and "••1234" in html
    assert "Roth IRA" in html and "TastyTrade answered 502." in html
    assert "Schwab" in html and "••9999" in html and "$1,000.25" in html
    assert "$26,000.25" in html  # total across both brokers
    assert "<nav" not in html  # just the part of the page that changes


def test_accounts_page_shows_stored_balances_without_asking_the_brokers(settings, fake_credentials):
    tastytrade = FakeBroker(
        "TastyTrade",
        [BrokerAccount("tastytrade", "5WX01234", "Alpha")],
        {"5WX01234": Balance(Decimal("25000"), Decimal("5000"), Decimal("12000"))},
    )
    client = _app(settings, fake_credentials, tastytrade).test_client()

    html = client.get("/accounts").get_data(as_text=True)
    assert tastytrade.calls == 0
    assert "Loading balances" in html
    assert 'data-live-url="/accounts/live"' in html

    client.get("/accounts/live")
    html = client.get("/accounts").get_data(as_text=True)
    assert tastytrade.calls == 1
    assert "Alpha" in html and "$25,000.00" in html and "As of" in html
    assert "DEVELOPMENT" not in html


def test_accounts_page_shows_a_broker_problem(settings, fake_credentials):
    tastytrade = FakeBroker(fail_accounts="TastyTrade rejected the access token.")
    response = _app(settings, fake_credentials, tastytrade).test_client().get("/accounts/live")
    assert response.status_code == 200
    assert "TastyTrade rejected the access token." in response.get_data(as_text=True)


def test_local_mode_without_a_connection(settings):
    """The real local provider and client: no broker connected yet."""
    response = create_app(settings).test_client().get("/accounts/live")
    assert response.status_code == 200
    assert "isn&#39;t connected. Connect it in Settings" in response.get_data(as_text=True)
    assert not guard.is_installed()


def test_gateway_mode_installs_guard_and_shows_banner(gateway_settings, fake_credentials):
    app = _app(gateway_settings, fake_credentials, FakeBroker())
    assert guard.is_installed()
    assert "DEVELOPMENT" in app.test_client().get("/accounts").get_data(as_text=True)


def test_health(gateway_settings, fake_credentials):
    client = _app(gateway_settings, fake_credentials, FakeBroker()).test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {
        "status": "ok",
        "version": "0.1.0",
        "broker_credentials": "gateway",
        "trading_guard": True,
        "database": True,
    }


@pytest.mark.parametrize(
    ("value", "text"),
    [(Decimal("1234.5"), "$1,234.50"), (Decimal("-12"), "-$12.00"), (None, "—")],
)
def test_money(value, text):
    assert money(value) == text


def test_nav_has_the_logo_and_icons(settings, fake_credentials):
    """The logo and app icons (#70); Alerts and Settings as icons (#66)."""
    html = _app(settings, fake_credentials).test_client().get("/journal").get_data(as_text=True)
    assert 'src="/static/images/logo.png" alt="OptionsJourney"' in html
    assert 'href="/static/images/favicon.ico"' in html
    assert 'href="/static/images/apple-touch-icon.png"' in html
    assert 'class="nav__icon" href="/alerts" title="Alerts" aria-label="Alerts"' in html
    assert 'class="nav__icon" href="/settings" title="Settings" aria-label="Settings"' in html
    assert ">Settings</a>" not in html


def test_tracker2_pages_share_the_nav(settings, fake_credentials):
    client = _app(settings, fake_credentials).test_client()
    for page in ("/charts", "/trade"):
        html = client.get(page).get_data(as_text=True)
        assert 'class="nav__logo"' in html and 'aria-label="Settings"' in html, page
        assert "css/nav.css" in html and "images/favicon.ico" in html, page
    assert "images/favicon.ico" in client.get("/chart/grid?bare=1").get_data(as_text=True)


def test_buy_me_a_coffee_on_every_page(settings, fake_credentials):
    """A low-key tip link in the footer: new tab, nothing sent (#63)."""
    client = _app(settings, fake_credentials).test_client()
    link = (
        '<a class="footer__link" href="https://buymeacoffee.com/jtse90881" target="_blank" '
        'rel="noopener noreferrer">'
    )
    for page in ("/journal", "/settings", "/alerts", "/charts", "/trade"):
        assert client.get(page).get_data(as_text=True).count(link) == 1, page
    assert link not in client.get("/chart/grid?bare=1").get_data(as_text=True)
