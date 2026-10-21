"""The Trade page's option chain (#90): the JSON its chain panel reads, with
Tracker2's checks and status codes. Analyze is gone (#223); its old addresses
redirect to the Trade page, or to a trade's risk graph."""

import re

from flask import Blueprint, jsonify, redirect, request, url_for

from optionsjourney.core import chain as core_chain
from optionsjourney.services import journal
from optionsjourney.web import services

bp = Blueprint("chain", __name__)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _bad(message: str):
    return jsonify({"status": "error", "message": message}), 400


def _ticker():
    return core_chain.clean_ticker(request.args.get("ticker"))


@bp.get("/analyze")
@bp.get("/tools/analyzer/")
def analyze_moved():
    """Bookmarks and old alert links: a journal trade opens its risk graph, and
    anything else opens the Trade page on its ticker."""
    trade = request.args.get("trade") or request.args.get("position")
    if trade:
        return redirect(url_for("risk_graph.risk_graph_page", trade=trade))
    ticker = _ticker()
    return redirect(
        url_for("trade.trade_page", ticker=ticker) if ticker else url_for("trade.trade_page")
    )


@bp.get("/api/trade/expirations")
def expirations():
    ticker = _ticker()
    if not ticker:
        return _bad("Invalid ticker")
    s = services()
    found = s.chain.expirations(s.broker_clients(), ticker)
    return jsonify({"status": "success", "ticker": ticker, **found})


@bp.get("/api/trade/earnings")
def earnings():
    ticker = _ticker()
    if not ticker:
        return _bad("Invalid ticker")
    s = services()
    found = s.chain.earnings(s.broker_clients(), ticker)
    return jsonify({"status": "success", "ticker": ticker, "earnings": found})


@bp.get("/api/trade/held-legs")
def held_legs():
    """The chain's Pos column (#210): what each account holds on the ticker,
    from the journal's open trades."""
    ticker = _ticker()
    if not ticker:
        return _bad("Invalid ticker")
    s = services()
    legs = journal.held_legs(s.session_factory, ticker, cache=s.positions_cache)
    return jsonify({"status": "success", "ticker": ticker, "legs": legs})


@bp.get("/api/trade/chain-table")
def chain_table():
    ticker = _ticker()
    if not ticker:
        return _bad("Invalid ticker")
    expiration = (request.args.get("expiration") or "").strip()
    if not _DATE.match(expiration):
        return _bad("Invalid expiration")
    s = services()
    prefer = request.args.get("broker", "").strip().lower() or None
    found = s.chain.chain_table(s.broker_clients(), s.quote_hub, ticker, expiration, prefer)
    if not found:
        message = f"No chain data for {ticker} {expiration}"
        return jsonify({"status": "error", "message": message}), 503
    return jsonify({"status": "success", "ticker": ticker, **found})
