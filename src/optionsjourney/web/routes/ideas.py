"""Trade Ideas' API (#292): save a Trade page ticket as an idea, list the ideas with
their P/L, poll their markets, rename or delete one. Nothing here places an order;
trading an idea goes through the Trade page's own Send, which retires it."""

from flask import Blueprint, jsonify, request

from optionsjourney.core.ideas import IdeaRefused
from optionsjourney.services import ideas
from optionsjourney.web import services

bp = Blueprint("ideas", __name__)


def _refused(message: str):
    return jsonify({"status": "error", "message": message}), 400


def _missing():
    return jsonify({"status": "error", "message": "That idea isn't there any more."}), 404


def _body() -> dict | None:
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _text(body: dict, key: str) -> str | None:
    value = body.get(key)
    return value if isinstance(value, str) else None


@bp.post("/api/ideas")
def create():
    body = _body()
    if body is None:
        return _refused("Expected the idea.")
    s = services()
    try:
        idea = ideas.create(
            s.session_factory, s.quote_hub,
            _text(body, "ticker"), _text(body, "label"), _text(body, "note"), body.get("cards"),
        )  # fmt: skip
    except IdeaRefused as refused:
        return _refused(str(refused))
    return jsonify({"status": "success", "idea": idea}), 201


@bp.get("/api/ideas")
def listing():
    s = services()
    if request.args.get("count") == "1":
        return jsonify({"count": ideas.count(s.session_factory)})
    return jsonify({"ideas": ideas.listing(s.session_factory, s.quote_hub, s.broker_clients())})


@bp.get("/api/ideas/markets")
def markets():
    s = services()
    return jsonify({"ideas": ideas.markets(s.session_factory, s.quote_hub, s.broker_clients())})


@bp.patch("/api/ideas/<int:idea_id>")
def edit(idea_id: int):
    body = _body()
    if body is None:
        return _refused("Expected the label or the note.")
    try:
        found = ideas.edit(services().session_factory, idea_id,
                           label=_text(body, "label"), note=_text(body, "note"))  # fmt: skip
    except IdeaRefused as refused:
        return _refused(str(refused))
    return jsonify({"status": "success"}) if found else _missing()


@bp.delete("/api/ideas/<int:idea_id>")
def delete(idea_id: int):
    if not ideas.delete(services().session_factory, idea_id):
        return _missing()
    return jsonify({"status": "success"})
