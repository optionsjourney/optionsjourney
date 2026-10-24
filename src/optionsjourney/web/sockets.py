"""Socket.IO for the chart grid and Analyze pages (#25, #26): live candles,
drawing changes, watchlist quotes and Analyze's leg quotes, and the journal's bot chips
(bots slice 20, web/live.py), with the event names Tracker2's pages use. Socket.IO's
default same-origin check keeps other sites out."""

import logging
import threading
from datetime import UTC, datetime

from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room

from optionsjourney.services import app_alerts, chart_feed, chart_grid, expiry_settle, trade_events
from optionsjourney.services.risk_quotes import RiskQuotes
from optionsjourney.services.watchlist_quotes import WatchlistQuotes
from optionsjourney.web import live

logger = logging.getLogger(__name__)


def register(app: Flask) -> SocketIO:
    socketio = SocketIO(app, async_mode="threading", logger=False, engineio_logger=False)
    services = app.extensions["optionsjourney"]

    def push(event, payload, to):
        socketio.emit(event, payload, to=to, namespace="/")

    def tastytrade():
        return next((c for c in services.broker_clients() if hasattr(c, "quote_token")), None)

    if services.settings.is_demo:
        from optionsjourney.demo import feeds  # noqa: PLC0415

        services.chart_feed = feeds.make_chart_feed(push)  # the demo engine fills it
    else:
        services.chart_feed = chart_feed.ChartFeed(tastytrade, push)
    services.risk_quotes = RiskQuotes(services.quote_hub, push)
    services.watchlist_quotes = WatchlistQuotes(services.quote_hub, services.broker_clients, push)
    services.socketio = socketio
    # Bots slice 20: the journal's chips refresh when a run's exits or overrides change
    live.watch_chips(
        services.session_factory, lambda run_id: push(live.EVENT, {"run_id": run_id}, None)
    )

    # The journal reloads when a sync stores new or changed fills
    # and (#178) each trade that opened, rolled or closed is recorded and sent for the popups
    def journal_changed():
        push(live.JOURNAL_EVENT, {}, None)
        if app.config.get("TESTING"):
            return  # tests record events themselves, never on a background thread
        threading.Thread(target=record_trade_events, name="trade-events", daemon=True).start()

    def record_trade_events():
        try:
            new = trade_events.record(
                services.session_factory, datetime.now(UTC), cache=services.positions_cache
            )
        except Exception:  # noqa: BLE001 -- an alert problem never breaks a sync
            logger.exception("couldn't record trade events")
            new = []
        for event in new:
            push(trade_events.EVENT, event.as_dict(), None)
        try:  # #186: a synced broker record that changes a settled P/L
            expiry_settle.alert_broker_records(services.session_factory, datetime.now(UTC))
        except Exception:  # noqa: BLE001 -- an alert problem never breaks a sync
            logger.exception("couldn't compare the brokers' records with the settlements")

    # #186: the top bar's badge counts unread alerts, live
    app_alerts.set_on_change(
        lambda: push(
            "alerts_changed", {"unread": app_alerts.unread_count(services.session_factory)}, None
        )
    )

    # #275: every open page pops up a new alert
    app_alerts.set_on_new(lambda alert: push("alert_fired", alert, None))

    live.watch_journal(services.session_factory, journal_changed)
    # #183: the Paper trades view reloads when a pretend fill is saved
    live.watch_paper(services.session_factory, lambda: push(live.PAPER_EVENT, {}, None))

    @socketio.on("subscribe_candles")
    def subscribe_candles(data):
        symbol = ((data or {}).get("symbol") or "").upper()
        interval = (data or {}).get("interval")
        # Join first, so a snapshot that completes meanwhile still arrives
        join_room(chart_feed.room(symbol, interval))
        if not services.chart_feed.add(request.sid, symbol, interval):
            leave_room(chart_feed.room(symbol, interval))
            emit("chart_error", {"message": f"no candles for {symbol} {interval}"})
            return
        snapshot = services.chart_feed.snapshot(symbol, interval)
        if snapshot:
            emit("chart_candles_snapshot", snapshot)

    @socketio.on("unsubscribe_candles")
    def unsubscribe_candles(data):
        symbol = ((data or {}).get("symbol") or "").upper()
        interval = (data or {}).get("interval")
        leave_room(chart_feed.room(symbol, interval))
        services.chart_feed.remove(request.sid, symbol, interval)

    @socketio.on("watch_drawings")
    def watch_drawings(data):
        key = chart_grid.drawing_key((data or {}).get("symbol"), (data or {}).get("interval"))
        if key:
            join_room(chart_grid.drawings_room(*key))

    @socketio.on("unwatch_drawings")
    def unwatch_drawings(data):
        key = chart_grid.drawing_key((data or {}).get("symbol"), (data or {}).get("interval"))
        if key:
            leave_room(chart_grid.drawings_room(*key))

    @socketio.on("subscribe_watchlist_quotes")
    def subscribe_watchlist_quotes(data):
        services.watchlist_quotes.subscribe(request.sid, (data or {}).get("symbols") or [])

    @socketio.on("unsubscribe_watchlist_quotes")
    def unsubscribe_watchlist_quotes(data=None):
        services.watchlist_quotes.unsubscribe(request.sid)

    @socketio.on("subscribe_risk_quotes")
    def subscribe_risk_quotes(data):
        services.risk_quotes.subscribe(request.sid, (data or {}).get("symbols") or [])

    @socketio.on("unsubscribe_risk_quotes")
    def unsubscribe_risk_quotes(data=None):
        services.risk_quotes.unsubscribe(request.sid)

    @socketio.on("disconnect")
    def disconnect(reason=None):
        services.watchlist_quotes.unsubscribe(request.sid)
        services.chart_feed.remove_sid(request.sid)
        services.risk_quotes.unsubscribe(request.sid)

    return socketio
