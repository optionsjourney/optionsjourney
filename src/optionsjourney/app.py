"""Creates the Flask app."""

import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Protocol

from flask import Flask, jsonify, render_template, request, url_for
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from optionsjourney import __version__, logs
from optionsjourney.brokers import guard
from optionsjourney.brokers.credentials import CredentialProvider, provider_for
from optionsjourney.brokers.schwab import SchwabClient
from optionsjourney.brokers.tastytrade import TastyTradeClient
from optionsjourney.config import ConfigError, Settings, load_settings
from optionsjourney.db import make_engine, make_session_factory, migrate, schema_state
from optionsjourney.services import backups
from optionsjourney.services import settings as settings_service
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
    update,
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
    # Demo mode only (create_app): the pretend market, brokers, clock, stream and engine
    demo: SimpleNamespace | None = None


class NewerDataError(Exception):
    """The data was upgraded by a newer version of the app. Nothing was changed."""


class UpgradeBackupError(Exception):
    """The backup before an upgrade couldn't be made, so the data wasn't
    upgraded. The message is for the user."""


def _prepare_data(settings: Settings, engine, session_factory) -> None:
    """Back up and upgrade the data, or refuse it if a newer version made it,
    then record which version ran."""
    state = schema_state(engine)
    if state == "newer":
        raise NewerDataError(
            f"This data was upgraded by a newer version than {__version__}. "
            "It has not been changed."
        )
    if state == "older":
        from_version = settings_service.get_app_version(session_factory) or "unknown"
        try:
            backups.backup_before_upgrade(
                settings.database_path,
                session_factory,
                from_version,
                __version__,
                datetime.now(UTC),
            )
        except backups.BackupError as e:
            raise UpgradeBackupError(str(e)) from e
    migrate(engine)
    settings_service.set_app_version(session_factory, __version__)


def _refuse_demo_data(settings: Settings) -> None:
    from optionsjourney.demo import marker  # noqa: PLC0415 -- small: no demo engine

    problem = marker.refusal(settings.data_dir, demo=False)
    if problem:
        raise ConfigError(problem)


def _demo_parts(settings: Settings) -> SimpleNamespace:
    """Demo mode's pretend world: the guards, the starting account (built fresh in the
    data folder, which must hold no database but the demo's own), the demo clock, one
    made-up market and a pretend TastyTrade and Schwab trading against it."""
    from optionsjourney.demo import offline, seed  # noqa: PLC0415
    from optionsjourney.demo.broker import PretendSchwab, PretendTasty  # noqa: PLC0415
    from optionsjourney.demo.clock import DEMO_DAY, DEMO_START, NY, DemoClock  # noqa: PLC0415
    from optionsjourney.demo.feeds import DemoStream  # noqa: PLC0415
    from optionsjourney.demo.market import PretendMarket  # noqa: PLC0415

    offline.install()
    guard.install()
    books = seed.prepare(settings.data_dir)  # refuses a real database, leaving it alone
    clock = DemoClock.start()
    # The market the seed priced its trades on: the same seed and start
    market = PretendMarket(
        seed=seed.MARKET_SEED, start=datetime.combine(DEMO_DAY, DEMO_START, tzinfo=NY)
    )
    return SimpleNamespace(
        credentials=provider_for(settings),
        clock=clock,
        market=market,
        tasty=PretendTasty(market, books.tasty),
        schwab=PretendSchwab(market, books.schwab),
        stream=DemoStream(),
        engine=None,
        director=None,
    )


def create_app(
    settings: Settings | None = None,
    *,
    credentials: CredentialProvider | None = None,
    broker_clients: Callable[[], list[BrokerClient]] | None = None,
    quote_hub: QuoteHub | None = None,
) -> Flask:
    settings = settings or load_settings()

    demo = None
    if settings.is_demo:
        # Before anything else: no network at all, and the trading guard as in gateway
        # mode. Neither is ever removed for this process.
        demo = _demo_parts(settings)
        credentials = demo.credentials
        broker_clients = lambda: [demo.tasty, demo.schwab]  # noqa: E731
        quote_hub = QuoteHub([demo.stream])
    else:
        # Before anything is written: a demo's database (risk boxes ticked, a Live bot
        # armed) never opens as a real app
        _refuse_demo_data(settings)
        if settings.is_gateway:
            # Before any broker client exists. Never removed for this process.
            guard.install()

    engine = make_engine(settings.database_path)
    session_factory = make_session_factory(engine)
    try:
        _prepare_data(settings, engine, session_factory)
    except NewerDataError, UpgradeBackupError:
        engine.dispose()  # no open handle on the file, so it can be restored
        raise

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
        session_factory=session_factory,
        credentials=credentials,
        broker_clients=broker_clients,
        positions_cache=PositionsCache(),
        close_cache=CloseCache(),
        quote_hub=quote_hub or make_hub(broker_clients(), credentials, gateway=settings.is_gateway),
    )
    if demo is not None:
        from optionsjourney.demo.engine import DemoEngine  # noqa: PLC0415

        services = app.extensions["optionsjourney"]
        from optionsjourney.demo.director import Director  # noqa: PLC0415
        from optionsjourney.demo.tour import Tour  # noqa: PLC0415
        from optionsjourney.demo.tour_facts import Visited  # noqa: PLC0415

        demo.engine = DemoEngine(services, demo.market, demo.tasty, demo.clock, demo.stream)
        demo.director = Director(session_factory, demo.market, demo.clock, demo.clock.now())
        demo.engine.director = demo.director
        demo.tour = Tour()
        demo.visited = Visited()
        demo.tour_lock = threading.Lock()
        services.demo = demo

    formatting.register(app)
    security.register(app)

    @app.context_processor
    def _inject_mode():
        return {
            "gateway_mode": settings.is_gateway,
            "demo_mode": settings.is_demo,
            "app_version": __version__,
        }

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
    app.register_blueprint(update.bp)
    app.register_blueprint(help_pages.bp)
    if settings.is_demo:
        from optionsjourney.web.routes import demo as demo_routes  # noqa: PLC0415

        app.register_blueprint(demo_routes.bp)

    sockets.register(app)
    if settings.is_demo:
        demo_routes.gate_sockets(app)

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
