"""Creates the Flask app."""

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from flask import Flask, jsonify, render_template, request, url_for
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from optionsjourney import logs
from optionsjourney.brokers import guard
from optionsjourney.brokers.credentials import CredentialProvider, provider_for
from optionsjourney.brokers.schwab import SchwabClient
from optionsjourney.brokers.tastytrade import TastyTradeClient
from optionsjourney.config import Settings, load_settings
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services.accounts import AccountsClient
from optionsjourney.services.chain import ChainData
from optionsjourney.services.chart_market import FuturesContracts
from optionsjourney.services.journal import PositionsCache
from optionsjourney.services.market_data import CloseCache
from optionsjourney.services.streaming import QuoteHub, make_hub
from optionsjourney.services.sync import SyncClient
from optionsjourney.web import formatting, security, sockets
from optionsjourney.web.routes import (
    accounts,
    alerts,
    automation,
    chain,
    chart_grid,
    health,
    help_pages,
    journal,
    manual,
    quotes,
    risk_graph,
    setup,
    trade,
    transactions,
)
from optionsjourney.web.routes import (
    orders as orders_routes,
)
from optionsjourney.web.routes import settings as settings_routes

logger = logging.getLogger(__name__)


class BrokerClient(AccountsClient, SyncClient, Protocol):
    """What the app needs from each broker's client."""


@dataclass
class Services:
    """What request handlers need, built once at startup. Stored on
    app.extensions["optionsjourney"]; read it with web.services()."""

    settings: Settings
    engine: Engine
    session_factory: sessionmaker
    credentials: CredentialProvider
    broker_clients: Callable[[], list[BrokerClient]]
    positions_cache: PositionsCache
    close_cache: CloseCache
    quote_hub: QuoteHub
    futures_contracts: FuturesContracts = field(default_factory=FuturesContracts)
    chain: ChainData = field(default_factory=ChainData)
    # Set by web.sockets.register
    socketio: object | None = None
    chart_feed: object | None = None
    watchlist_quotes: object | None = None
    risk_quotes: object | None = None
    # Set by services.fill_sync.start (#172)
    fill_sync: object | None = None


def create_app(
    settings: Settings | None = None,
    *,
    credentials: CredentialProvider | None = None,
    broker_clients: Callable[[], list[BrokerClient]] | None = None,
    quote_hub: QuoteHub | None = None,
) -> Flask:
    settings = settings or load_settings()

    if settings.is_gateway:
        # Before any broker client exists. Never removed for this process.
        guard.install()

    engine = make_engine(settings.database_path)
    migrate(engine)

    credentials = credentials or provider_for(settings)
    if broker_clients is None:
        clients = [TastyTradeClient(credentials), SchwabClient(credentials)]
        broker_clients = lambda: clients  # noqa: E731

    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    # Only signs flash messages; a new key each start is fine.
    app.secret_key = secrets.token_hex(32)
    app.extensions["optionsjourney"] = Services(
        settings=settings,
        engine=engine,
        session_factory=make_session_factory(engine),
        credentials=credentials,
        broker_clients=broker_clients,
        positions_cache=PositionsCache(),
        close_cache=CloseCache(),
        quote_hub=quote_hub or make_hub(broker_clients(), credentials, gateway=settings.is_gateway),
    )

    formatting.register(app)
    security.register(app)

    @app.context_processor
    def _inject_mode():
        return {"gateway_mode": settings.is_gateway}

    @app.context_processor
    def _inject_alert_count():
        # Every page shows how many alerts are waiting (#12)
        from optionsjourney.services import app_alerts
        from optionsjourney.services.alerts import system_alerts

        s = app.extensions["optionsjourney"]
        system = len(system_alerts(s.session_factory, credentials=s.credentials))
        # #186: unread alerts (a shown P/L changed) count in the same badge
        return {
            "alert_count": system + app_alerts.unread_count(s.session_factory),
            "system_alert_count": system,
        }

    @app.context_processor
    def _inject_trading_switches():
        from optionsjourney.services.orders import switches

        s = app.extensions["optionsjourney"]
        return {"trading_switches": switches(s.session_factory)}

    app.register_blueprint(accounts.bp)
    app.register_blueprint(alerts.bp)
    app.register_blueprint(quotes.bp)
    app.register_blueprint(chart_grid.bp)
    app.register_blueprint(chain.bp)
    app.register_blueprint(risk_graph.bp)
    app.register_blueprint(trade.bp)
    app.register_blueprint(automation.bp)
    app.register_blueprint(journal.bp)
    app.register_blueprint(manual.bp)
    app.register_blueprint(orders_routes.bp)
    app.register_blueprint(setup.bp)
    app.register_blueprint(settings_routes.bp)
    app.register_blueprint(transactions.bp)
    app.register_blueprint(health.bp)
    app.register_blueprint(help_pages.bp)

    sockets.register(app)

    @app.errorhandler(500)
    def unexpected_error(e):
        """Plain words and what to do next instead of a bare 500 (decision 0094). Flask
        has already logged the traceback, which goes to the log file."""
        cause = getattr(e, "original_exception", None) or e
        error = f"{type(cause).__name__} on {request.path}"
        if request.path.startswith("/api/"):
            return jsonify(error="Something went wrong. Restart OptionsJourney from its menu."), 500
        return render_template("error.html", error=error, log_name=logs.LOG_NAME), 500

    @app.template_global()
    def asset_url(filename: str) -> str:
        return url_for("static", filename=filename)

    logger.info("OptionsJourney ready (broker credentials: %s)", settings.broker_credentials)
    return app
