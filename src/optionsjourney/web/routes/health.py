"""GET /health: is the app up, and is its database usable."""

from flask import Blueprint, jsonify

from optionsjourney import __version__
from optionsjourney.brokers import guard
from optionsjourney.db import is_healthy
from optionsjourney.web import services

bp = Blueprint("health", __name__)


@bp.route("/health")
def health():
    s = services()
    database_ok = is_healthy(s.engine)
    return jsonify(
        status="ok" if database_ok else "degraded",
        version=__version__,
        broker_credentials=s.settings.broker_credentials,
        trading_guard=guard.is_installed(),
        database=database_ok,
    ), (200 if database_ok else 503)
