"""The Pending drawer's API (#292): working orders across every account, their live
mids and the count for the badge. Change price and Cancel reuse /api/trade/replace
and /api/trade/cancel."""

from flask import Blueprint, jsonify, request

from optionsjourney.services import pending_orders, trade_orders
from optionsjourney.web import services

bp = Blueprint("pending", __name__)


def _snapshot() -> dict:
    s = services()
    accounts = trade_orders.trading_accounts(s.session_factory)
    return pending_orders.snapshot(s.session_factory, s.broker_clients(), accounts)


@bp.get("/api/pending")
def pending():
    got = _snapshot()
    if request.args.get("count") == "1":
        return jsonify(count=got["count"], errors=got["errors"])
    return jsonify(got)


@bp.get("/api/pending/markets")
def markets():
    """Live mids for the listed order ids, priced from the last /api/pending listing
    (no broker call); ids not in it are left out."""
    ids = {i for i in request.args.get("ids", "").split(",") if i}
    if not ids:
        return jsonify(marks={})
    return jsonify(marks=pending_orders.listed_marks(services().quote_hub, sorted(ids)))
