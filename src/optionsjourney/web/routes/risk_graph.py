"""The risk graph (#91): Tracker2's retired risk graph for journal trades, one
or several together. ?embed=1 is the journal drawer's and grid's iframe."""

from flask import Blueprint, jsonify, render_template, request

from optionsjourney.services import journal
from optionsjourney.services.risk_graph import trade_graphs
from optionsjourney.web import services

bp = Blueprint("risk_graph", __name__)

MAX_TRADES = 50


def _bad(message: str, status: int = 400):
    return jsonify({"status": "error", "message": message}), status


def _keys() -> list[str]:
    return [k.strip() for k in (request.args.get("trade") or "").split(",") if k.strip()]


@bp.get("/risk-graph")
def risk_graph_page():
    return render_template("risk_graph.html", keys=_keys())


@bp.get("/api/risk-graph")
def risk_graph_api():
    keys = _keys()
    if not keys or len(keys) > MAX_TRADES:
        return _bad(f"Pick 1-{MAX_TRADES} trades.")
    s = services()
    view = journal.load_journal(s.session_factory, cache=s.positions_cache)
    try:
        trades = trade_graphs(view, keys, s.quote_hub, s.chain, s.broker_clients())
    except LookupError:
        return _bad("That trade isn't in the journal", 404)
    return jsonify({"status": "success", "trades": trades})
