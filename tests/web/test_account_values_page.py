from datetime import timedelta
from decimal import Decimal

from optionsjourney.app import create_app
from optionsjourney.core.accounts import Balance, BrokerAccount, DailyValue

ORIGIN = {"Origin": "http://localhost"}


class FakeTasty:
    name = "TastyTrade"
    broker = "tastytrade"

    def accounts(self):
        return [BrokerAccount("tastytrade", "5WX01234", "Alpha")]

    def balance(self, number):
        return Balance(Decimal("25000"), Decimal("5000"), Decimal("12000"))

    def balance_history(self, number, start, end):
        return [
            DailyValue(end - timedelta(days=1), Decimal("24000"), Decimal("5000")),
            DailyValue(end, Decimal("25000"), Decimal("5000")),
        ]

    def transactions(self, number, start, end):
        return []


class FakeSchwab:
    name = "Schwab"
    broker = "schwab"

    def accounts(self):
        return [BrokerAccount("schwab", "12349999", None, "Margin")]

    def balance(self, number):
        return Balance(Decimal("1000"), Decimal("0"), Decimal("0"))

    def transactions(self, number, start, end):
        return []


def _client(settings, credentials, *brokers):
    app = create_app(settings, credentials=credentials, broker_clients=lambda: list(brokers))
    app.config["TESTING"] = True
    return app.test_client()


def test_nothing_recorded_yet(settings, fake_credentials):
    client = _client(settings, fake_credentials, FakeTasty())
    html = client.get("/accounts/values").get_data(as_text=True)
    assert "Account values" in html
    assert "Sync now" in html  # how to get some


def test_sync_records_values_and_the_page_adds_them_up(settings, fake_credentials):
    client = _client(settings, fake_credentials, FakeTasty(), FakeSchwab())
    client.post("/sync", headers=ORIGIN)
    html = client.get("/accounts/values").get_data(as_text=True)
    assert "$26,000.00" in html  # 25,000 TastyTrade + 1,000 Schwab today
    assert "Schwab doesn't keep a history" in html
    assert '"value": 26000.0' in html  # the chart's data


def test_visiting_accounts_records_schwab(settings, fake_credentials):
    client = _client(settings, fake_credentials, FakeSchwab())
    client.get("/accounts/live")  # the Accounts page asks for this as it opens
    html = client.get("/accounts/values?account=schwab:12349999").get_data(as_text=True)
    assert "$1,000.00" in html


def test_accounts_page_links_to_values(settings, fake_credentials):
    client = _client(settings, fake_credentials, FakeTasty())
    html = client.get("/accounts").get_data(as_text=True)
    assert 'href="/accounts/values"' in html


def test_the_balances_and_money_in_out_have_tips(settings, fake_credentials):
    client = _client(settings, fake_credentials, FakeTasty())
    client.post("/sync", headers=ORIGIN)
    html = client.get("/accounts/live").get_data(as_text=True)
    assert 'id="tip-accounts-net-liq-1"' in html and 'id="tip-accounts-buying-power-1"' in html
    values = client.get("/accounts/values").get_data(as_text=True)
    assert 'id="tip-values-money-in-out"' in values
