"""The Trade page (#90): Tracker2's option chain, an order ticket and the risk
graph. Review order previews through the one order path; nothing is sent from
here until Confirm (slice 3). Bots slice 15: a reviewed ticket can instead be sent
as a live bot's entry (Manage with a bot)."""

import logging
from datetime import UTC, datetime

from flask import Blueprint, jsonify, render_template, request, url_for
from sqlalchemy import select

from optionsjourney.bots import underlyings
from optionsjourney.brokers.credentials.base import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core import chain as core_chain
from optionsjourney.db.models import Account
from optionsjourney.services import (
    bot_futures,
    bot_loops,
    bot_ticket,
    manual_attach,
    manual_bots,
    trade_orders,
    trade_ticket,
)
from optionsjourney.web import services

logger = logging.getLogger(__name__)
bp = Blueprint("trade", __name__)

BROKERS = {"tastytrade": "TASTYTRADE", "schwab": "SCHWAB"}
NO_MANUAL_BOT = "That manual bot isn't there any more. Nothing was sent; pick again."
REMEMBERED = "Its manual bot's rules are attached once it fills."
NOT_REMEMBERED = (
    "Its manual bot's rules couldn't be saved: once it fills, use Manage this trade in the Journal."
)


def _accounts() -> list[dict]:
    with services().session_factory() as session:
        rows = session.scalars(select(Account).order_by(Account.broker, Account.id)).all()
    return [
        {
            "id": a.id,
            "broker": BROKERS.get(a.broker, a.broker.upper()),
            "label": a.nickname or f"…{a.number[-4:]}",
        }
        for a in rows
        if a.broker in BROKERS
    ]


@bp.get("/trade")
def trade_page():
    ticker = core_chain.clean_ticker(request.args.get("ticker")) or "SPX"
    return render_template(
        "trade_ticket.html", ticker=ticker, accounts=_accounts(), tour_tray=_tour_tray()
    )


def _tour_tray() -> dict | None:
    """The tour's put spread, only in the demo and only while the tour is on its Trade step."""
    demo = services().demo
    if demo is None:
        return None
    with demo.tour_lock:
        on_trade_step = demo.tour.current == 4
    if not on_trade_step:
        return None
    from optionsjourney.demo.tour_trade import put_spread_tray  # noqa: PLC0415 -- demo only

    return put_spread_tray(demo.market, demo.clock)


def _account(source) -> tuple[str, str] | None:
    """(broker, number) for the account id in a request body or query, read from
    the database. The number never comes from the request. None when unknown."""
    account_id = source.get("account_id") if source is not None else None
    if type(account_id) is not int:
        return None
    with services().session_factory() as session:
        account = session.get(Account, account_id)
        return (account.broker, account.number) if account else None


def _unknown_account():
    return jsonify({"status": "error", "message": "That account isn't known."}), 404


def _result(result):
    return jsonify({"status": "success", "action": result.action, "ok": result.ok,
                    "message": result.message, "order_id": result.broker_order_id})  # fmt: skip


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


@bp.post("/api/trade/review")
def review():
    body = _body()
    if body is None:
        return jsonify({"status": "error", "message": "Expected the ticket."}), 400
    found = _account(body)
    if found is None:
        return _unknown_account()
    s = services()
    clients = s.broker_clients()
    # Bots slice 17: a futures ticket's legs, resolved in TastyTrade's series
    resolver = bot_futures.ticket_resolver(s.chain, clients, datetime.now(UTC))
    reviewed = trade_ticket.review(
        s.session_factory, clients, found[0], found[1], body, futures=resolver
    )
    result = reviewed.result
    return jsonify({"status": "success", "action": result.action, "ok": result.ok,
                    "message": result.message, "code": reviewed.code, "costs": result.costs,
                    "summary": reviewed.summary, "futures": reviewed.futures})  # fmt: skip


@bp.post("/api/trade/confirm")
def confirm():
    body = _body()
    found = _account(body)
    if found is None:
        return _unknown_account()
    s = services()
    code = body.get("code")
    # Manual bots (#158): Manage with. Absent (Place only): the plain send, unchanged.
    manual_bot_id = body.get("manual_bot_id")
    if manual_bot_id is not None and (
        type(manual_bot_id) is not int or manual_bots.load(s.session_factory, manual_bot_id) is None
    ):
        return jsonify({"status": "success", "action": "refuse", "ok": False,
                        "message": NO_MANUAL_BOT, "order_id": None})  # fmt: skip
    result = trade_ticket.confirm(
        s.session_factory,
        s.broker_clients(),
        found[0],
        found[1],
        code if isinstance(code, str) else "",
    )
    if manual_bot_id is None or not result.ok or not result.broker_order_id:
        return _result(result)
    answer = _result(result).get_json()
    try:
        manual_attach.remember(bot_loops.deps_for(s), found[0], found[1],
                               result.broker_order_id, manual_bot_id)  # fmt: skip
        answer["manage_note"] = REMEMBERED
    except Exception:  # noqa: BLE001 -- the order is out either way: say the rules aren't
        logger.exception("couldn't remember manual rules for order %s", result.broker_order_id)
        answer["manage_note"] = NOT_REMEMBERED
    return jsonify(answer)


@bp.get("/api/trade/manual-bots")
def manual_bot_list():
    """The manual bots the ticket's Manage with offers. Reads only."""
    rows = manual_bots.listing(services().session_factory)
    bots = [{"id": bot_id, "name": rules.name} for bot_id, rules in rows]
    return jsonify({"status": "success", "bots": bots})


@bp.get("/api/trade/working")
def working():
    found = _account(_int_args())
    if found is None:
        return _unknown_account()
    s = services()
    try:
        orders = trade_orders.working(s.session_factory, s.broker_clients(), found[0], found[1])
    except BrokerError, CredentialsUnavailable:
        message = "Couldn't read your working orders from the broker."
        return jsonify({"status": "error", "message": message}), 502
    return jsonify({"status": "success", "orders": orders})


def _int_args() -> dict:
    """The query's account_id as an int when it is a plain whole number."""
    raw = request.args.get("account_id", "")
    return {"account_id": int(raw)} if raw.isdecimal() else {}


@bp.post("/api/trade/cancel")
def cancel():
    body = _body()
    found = _account(body)
    if found is None:
        return _unknown_account()
    s = services()
    order_id = body.get("order_id")
    result = trade_orders.cancel(
        s.session_factory, s.broker_clients(), found[0], found[1],
        order_id if isinstance(order_id, str) else "",
    )  # fmt: skip
    return _result(result)


@bp.post("/api/trade/replace")
def replace():
    body = _body()
    found = _account(body)
    if found is None:
        return _unknown_account()
    s = services()
    order_id = body.get("order_id")
    result = trade_orders.replace(
        s.session_factory, s.broker_clients(), found[0], found[1],
        order_id if isinstance(order_id, str) else "", body.get("price"),
    )  # fmt: skip
    return _result(result)


# --- Manage with a bot (bots slice 15) ----------------------------------------------------


@bp.post("/api/trade/bots")
def bots():
    """The bots that can take the ticket, and why the others can't. Reads only."""
    body = _body()
    found = _account(body)
    if found is None:
        return _unknown_account()
    try:
        deps = bot_loops.deps_for(services())
        problem, offered = bot_ticket.choices(deps, found[0], found[1], body)
    except Exception:  # noqa: BLE001 -- the page always answers
        logger.exception("couldn't list the bots for a ticket")
        return jsonify({"status": "error", "message": bot_ticket.COULDNT}), 500
    rows = [
        {"id": bot.id, "name": bot.name, "problem": why, "exits": lines,
         "futures": underlyings.futures(bot.underlying)}  # bots slice 17
        for bot, why, lines in offered
    ]  # fmt: skip
    return jsonify({"status": "success", "problem": problem, "bots": rows})


@bp.post("/api/trade/bot-entry")
def send_as_bot():
    """Send the ticket as the chosen bot's entry (bot_ticket.enter): claimed on the
    page's token before anything is read from the broker."""
    body = _body()
    found = _account(body)
    if found is None:
        return _unknown_account()
    sent = bot_ticket.enter(bot_loops.deps_for(services()), found[0], found[1], body)
    run_url = url_for("automation.run_page", run_id=sent.run_id) if sent.run_id else None
    return jsonify({"status": "success", "ok": sent.ok, "message": sent.message,
                    "run_id": sent.run_id, "run_url": run_url})  # fmt: skip
