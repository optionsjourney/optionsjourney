"""Entering trades by hand (docs/decisions/0023): the form, closing and
expiring a manual trade, and deleting an entry."""

from datetime import UTC, date, datetime
from itertools import groupby

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from optionsjourney.brokers import manual as manual_broker
from optionsjourney.brokers.manual import MAX_LEGS, ManualEntryError, build_entry
from optionsjourney.core.market import MARKET_TZ, expiry_close
from optionsjourney.services import journal, manual
from optionsjourney.web import services

bp = Blueprint("manual", __name__)

LEG_FIELDS = ("type", "side", "quantity", "underlying", "strike", "expiration", "price")


def _position(key: str):
    view = journal.load_journal(services().session_factory, cache=services().positions_cache)
    entry = journal.find_entry(view, key)
    if entry is None or entry.position.broker != manual_broker.BROKER:
        abort(404)
    return entry.position


@bp.get("/journal/manual/new")
def new_entry():
    form: dict = {"effect": "open", "date": "", "time": "", "fees": "0", "account": ""}
    legs: list[dict] = []
    closing = request.args.get("close")
    if closing:
        position = _position(closing)
        now = datetime.now(UTC).astimezone(MARKET_TZ)
        form.update(
            effect="close",
            account=position.account_number,
            date=now.date().isoformat(),
            time=now.strftime("%H:%M"),
        )
        legs = manual.closing_legs(position)
    return _render(form, legs)


@bp.post("/journal/manual/new")
def add_entry():
    form = request.form
    legs = [_leg_from_form(form, n) for n in range(1, MAX_LEGS + 1)]
    entered = [leg for leg in legs if leg["underlying"].strip() or leg["quantity"].strip()]
    try:
        account = form.get("account", "")
        if account == "new":
            account = manual.create_account(services().session_factory, form.get("new_account", ""))
        try:
            day = datetime.strptime(
                f"{form.get('date', '')} {form.get('time') or '16:00'}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            raise ManualEntryError(
                "Enter the date (and time, New York time) of the trade."
            ) from None
        entry = build_entry(
            executed_at=day.replace(tzinfo=MARKET_TZ),
            effect=form.get("effect", ""),
            fees=form.get("fees", "0"),
            legs=entered,
        )
        key = manual.add_entry(services().session_factory, account, entry)
    except ManualEntryError as e:
        flash(str(e), "warning")
        return _render(dict(form), legs)
    if entry["effect"] == "open":
        flash("Trade added.", "success")
        return redirect(url_for("journal.trade_page", key=key))
    flash("Close added.", "success")
    return redirect(url_for("journal.journal_page"))


@bp.post("/journal/manual/expire")
def expire():
    """Close what's left of a manual trade as expired, one entry per
    expiration date, each at that day's close."""
    key = request.form.get("key", "")
    position = _position(key)
    legs = [leg for leg in manual.closing_legs(position) if leg["type"] != "shares"]
    if not legs:
        flash("There's nothing left to expire.", "warning")
        return redirect(url_for("journal.trade_page", key=key))
    if any(expiry_close(date.fromisoformat(leg["expiration"])) > datetime.now(UTC) for leg in legs):
        flash("This trade hasn't expired yet.", "warning")
        return redirect(url_for("journal.trade_page", key=key))
    for expiration, group in groupby(
        sorted(legs, key=lambda leg: leg["expiration"]), key=lambda leg: leg["expiration"]
    ):
        entry = build_entry(
            executed_at=expiry_close(date.fromisoformat(expiration)),
            effect="expire",
            fees="0",
            legs=[dict(leg, price="0") for leg in group],
        )
        manual.add_entry(services().session_factory, position.account_number, entry)
    flash("Marked as expired.", "success")
    return redirect(url_for("journal.trade_page", key=key))


@bp.post("/journal/manual/entries/<entry_id>/delete")
def delete_entry(entry_id):
    try:
        manual.delete_entry(services().session_factory, entry_id)
        flash("Entry deleted.", "success")
    except ManualEntryError as e:
        flash(str(e), "warning")
    return redirect(url_for("journal.journal_page"))


def _leg_from_form(form, number: int) -> dict:
    return {name: form.get(f"leg-{number}-{name}", "") for name in LEG_FIELDS}


def _render(form: dict, legs: list[dict]):
    legs = list(legs) + [{} for _ in range(MAX_LEGS - len(legs))]
    return render_template(
        "manual_entry.html",
        form=form,
        legs=legs,
        accounts=manual.accounts(services().session_factory),
    )
