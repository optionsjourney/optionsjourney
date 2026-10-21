"""The two trading switches, the Freeze confirmation, and the order log
(spec: the order path; decision 0028)."""

from flask import Blueprint, abort, redirect, render_template, request, url_for
from sqlalchemy import select

from optionsjourney.core.alerts import BROKER_NAMES
from optionsjourney.db.models import OrderLog
from optionsjourney.services import orders
from optionsjourney.web import services

bp = Blueprint("orders", __name__)
PAGE_SIZE = 200


def _back():
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return redirect(target)
    return redirect(url_for("orders.order_log"))


@bp.route("/orders")
def order_log():
    filters = {k: request.args.get(k, "") for k in ("account", "origin", "action")}
    query = select(OrderLog).order_by(OrderLog.id.desc()).limit(PAGE_SIZE)
    if filters["account"]:
        query = query.where(OrderLog.account_number == filters["account"])
    if filters["origin"]:
        query = query.where(OrderLog.origin == filters["origin"])
    if filters["action"]:
        query = query.where(OrderLog.action == filters["action"])
    with services().session_factory() as session:
        rows = list(session.scalars(query))
        # The accounts that have orders, to filter by (the page shows only the last 4 digits)
        accounts = list(
            session.execute(
                select(OrderLog.broker, OrderLog.account_number)
                .distinct()
                .order_by(OrderLog.broker, OrderLog.account_number)
            ).all()
        )
    return render_template(
        "orders.html",
        rows=rows,
        filters=filters,
        unclear=orders.UNCLEAR,
        accounts=accounts,
        broker_names=BROKER_NAMES,
    )


@bp.route("/orders/freeze")
def freeze_page():
    return render_template("freeze.html")


@bp.post("/orders/switch/<name>")
def switch(name):
    if name not in orders.SWITCH_KEYS:
        abort(404)
    orders.set_switch(services().session_factory, name, request.form.get("on") == "1")
    return _back()


@bp.post("/orders/<int:log_id>/clear")
def clear(log_id):
    orders.clear(services().session_factory, log_id)
    return _back()
