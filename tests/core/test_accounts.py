from decimal import Decimal

from optionsjourney.core.accounts import (
    AccountSummary,
    Balance,
    BrokerAccount,
    mask_number,
    total_net_liquidating_value,
)


def test_display_name_prefers_nickname_then_type():
    assert BrokerAccount("tastytrade", "5WX01234", "Alpha", "Individual").display_name == "Alpha"
    assert BrokerAccount("tastytrade", "5WX01234", None, "Roth IRA").display_name == "Roth IRA"
    assert BrokerAccount("tastytrade", "5WX01234").display_name == "Account"


def test_masked_number_shows_last_four():
    assert BrokerAccount("tastytrade", "5WX01234").masked_number == "••1234"


def test_total_skips_accounts_without_a_balance():
    account = BrokerAccount("tastytrade", "5WX01234")
    summaries = [
        AccountSummary(account, Balance(Decimal("100.50"), Decimal("0"), Decimal("0"))),
        AccountSummary(account, error="TastyTrade answered 502."),
        AccountSummary(account, Balance(Decimal("-0.50"), Decimal("0"), Decimal("0"))),
    ]
    assert total_net_liquidating_value(summaries) == Decimal("100.00")


def test_mask_number_hides_every_copy_of_the_account_number():
    text = "POST /accounts/5WX01234/orders failed for 5WX01234"
    assert mask_number(text, "5WX01234") == "POST /accounts/…1234/orders failed for …1234"
    assert mask_number("no number here", "5WX01234") == "no number here"
    assert mask_number("anything", "") == "anything"
