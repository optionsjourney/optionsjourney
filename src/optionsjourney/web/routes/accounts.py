"""Accounts page: connected accounts and their balances, and their values
over time."""

from flask import Blueprint, render_template, request

from optionsjourney.core.accounts import total_net_liquidating_value
from optionsjourney.services import account_values
from optionsjourney.services.accounts import live_overviews, stored_overviews
from optionsjourney.web import services

bp = Blueprint("accounts", __name__)

# How far back the Account values page looks: days (0 = everything recorded)
VALUE_PERIODS = {90: "90 days", 365: "1 year", 0: "All recorded"}


@bp.route("/accounts")
def accounts_page():
    """The balances last read, straight away; the page then asks
    /accounts/live for fresh ones (#74)."""
    brokers = stored_overviews(services().broker_clients(), services().session_factory)
    return render_template("accounts.html", **_balances(brokers), pending=True)


@bp.route("/accounts/live")
def live_balances():
    """Fresh balances from the brokers: the part of the Accounts page that
    changes, for accounts.js to swap in."""
    brokers = live_overviews(services().broker_clients(), services().session_factory)
    # Schwab keeps no history, so every balance read here is worth keeping
    account_values.record_balances(
        services().session_factory,
        (s for b in brokers for s in b.summaries if s.error is None),
    )
    return render_template("accounts_live.html", **_balances(brokers), pending=False)


def _balances(brokers) -> dict:
    summaries = [s for b in brokers for s in b.summaries]
    return {
        "brokers": brokers,
        "total": total_net_liquidating_value(summaries),
        "has_balances": any(s.balance is not None for s in summaries),
    }


@bp.route("/accounts/values")
def values_page():
    days = request.args.get("days", 365, type=int)
    if days not in VALUE_PERIODS:
        days = 365
    account = request.args.get("account", "")
    view = account_values.load_values(services().session_factory, account=account, days=days)
    return render_template(
        "account_values.html",
        view=view,
        account=account,
        days=days,
        periods=VALUE_PERIODS,
    )
