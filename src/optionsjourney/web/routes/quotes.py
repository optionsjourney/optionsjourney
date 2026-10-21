"""Live prices for the app's pages (#22): GET /api/quotes?symbols=SPX,SPY,...
Subscribes the brokers' streams to anything new and returns what's known."""

from flask import Blueprint, jsonify, request

from optionsjourney.web import services

bp = Blueprint("quotes", __name__)
MAX_SYMBOLS = 500


@bp.get("/api/quotes")
def quotes():
    keys = [k for k in request.args.get("symbols", "").split(",") if k.strip()][:MAX_SYMBOLS]
    hub = services().quote_hub
    hub.subscribe(keys)
    return jsonify(
        quotes={key: _json(hub.latest(key)) for key in keys},
        streams=hub.status(),
    )


def _json(quote):
    if quote is None:
        return None
    number = lambda value: None if value is None else float(value)  # noqa: E731
    return {
        "bid": number(quote.bid),
        "ask": number(quote.ask),
        "last": number(quote.last),
        "mark": number(quote.mark),
        "source": quote.source,
        "at": quote.at.isoformat(),
    }
