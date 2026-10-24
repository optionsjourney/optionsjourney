"""Transactions page: what was synced from the brokers, as orders or as
individual fills, and Sync now."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, url_for

from optionsjourney.core.orders import group_fills
from optionsjourney.core.transactions import matches_search
from optionsjourney.services import account_values
from optionsjourney.services.sync import sync_all
from optionsjourney.services.transactions import load_fills
from optionsjourney.web import services
from optionsjourney.web.pagination import paginate

bp = Blueprint("transactions", __name__)

PERIODS = {7: "7 days", 30: "30 days", 90: "90 days", 365: "1 year"}
DEFAULT_PERIOD = 30
VIEWS = ("orders", "fills")
ROWS_PER_PAGE = 200


@bp.route("/transactions")
def transactions_page():
    days = request.args.get("days", DEFAULT_PERIOD, type=int)
    if days not in PERIODS:
        days = DEFAULT_PERIOD
    view_mode = request.args.get("view", VIEWS[0])
    if view_mode not in VIEWS:
        view_mode = VIEWS[0]
    account = _account_filter(request.args.get("account", ""))
    query = request.args.get("q", "").strip()

    view = load_fills(
        services().session_factory,
        since=datetime.now(UTC) - timedelta(days=days),
        account=account,
    )
    rows = group_fills(view.fills) if view_mode == "orders" else view.fills
    if query:
        rows = [row for row in rows if _matches(row, query, view.accounts)]
    return render_template(
        "transactions.html",
        view=view,
        page=paginate(rows, ROWS_PER_PAGE),
        query=query,
        view_mode=view_mode,
        periods=PERIODS,
        days=days,
        selected_account=request.args.get("account", ""),
    )


@bp.post("/sync")
def sync_now():
    s = services()
    clients = s.broker_clients()
    for result in sync_all(clients, s.session_factory):
        if result.ok:
            flash(
                f"{result.broker}: {result.accounts} accounts, {result.new} new, "
                f"{result.updated} updated.",
                "success",
            )
        for problem in result.problems or []:
            flash(f"{result.broker}: {problem}", "warning")
    for problem in account_values.sync_values(clients, s.session_factory):
        flash(f"Account values: {problem}", "warning")
    # Back to the page Sync now was pressed on (the Journal, Settings), else Transactions.
    back = request.form.get("back", "")
    if back.startswith("/") and not back.startswith("//") and not urlsplit(back).netloc:
        return redirect(back)
    return redirect(url_for("transactions.transactions_page", **request.args))


def _matches(row, query: str, accounts: dict) -> bool:
    """The search (#287) also finds a row by its account's name."""
    account = accounts.get((row.broker, row.account_number))
    name = f"{account.display_name} {account.masked_number}" if account else row.broker
    return matches_search(f"{row.search_text} {name}", query)


def _account_filter(value: str) -> tuple[str, str] | None:
    """Turn the account filter value, e.g. "tastytrade:5WX01234", into
    ("tastytrade", "5WX01234")."""
    broker, _, number = value.partition(":")
    return (broker, number) if broker and number else None
