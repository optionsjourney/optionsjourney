"""The charts page (#25): Tracker2's multi-chart grid, its pop-out window,
and the API its pages call (layouts, tabs, drawings, watchlist, price
history). Live candles and quotes go over Socket.IO (web/sockets.py)."""

from datetime import UTC, datetime

from flask import Blueprint, jsonify, render_template, request

from optionsjourney.core.positions import Status
from optionsjourney.services import chart_grid, chart_market, news
from optionsjourney.services.journal import load_journal
from optionsjourney.web import services

bp = Blueprint("charts", __name__)


def _error(message: str, status: int = 400):
    return jsonify({"error": True, "error_message": message}), status


def _json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.errorhandler(chart_grid.GridError)
@bp.errorhandler(chart_market.MarketError)
def _grid_error(e):
    return _error(str(e), e.status)


@bp.get("/chart/grid")  # Tracker2's address, which its pages open
@bp.get("/charts")  # registered first, so url_for gives this one
def charts_page():
    return render_template("chart_grid.html", bare=request.args.get("bare") == "1")


@bp.get("/chart/grid/popout")
def popout():
    layout_id = request.args.get("layout") or ""
    pane_id = request.args.get("pane") or ""
    tab_id = request.args.get("tab") or None
    if (
        not chart_grid.LAYOUT_ID.match(layout_id)
        or not chart_grid.PANE_ID.match(pane_id)
        or (tab_id is not None and not chart_grid.LAYOUT_ID.match(tab_id))
    ):
        return "bad layout, pane or tab", 400
    return render_template(
        "chart_grid_popout.html", layout_id=layout_id, pane_id=pane_id, tab_id=tab_id
    )


# --- market data ---


@bp.get("/api/news/<path:symbol>")
def symbol_news(symbol):
    """The News panel's headlines (Tracker2's /api/news/<symbol>)."""
    if services().settings.is_demo:
        from optionsjourney.demo import news as demo_news  # noqa: PLC0415

        # the process clock is the demo clock in demo mode
        return jsonify(demo_news.headlines(symbol, datetime.now(UTC)))
    try:
        return jsonify(news.company_news(services().session_factory, symbol))
    except news.NewsError as e:
        return jsonify({"symbol": symbol.strip().upper(), "news": [], "error": str(e)}), 502


@bp.get("/api/chart/debug")
def debug():
    feed = services().chart_feed
    return jsonify({"chart_feed": feed.status() if feed else None})


@bp.get("/api/chart/pricehistory/<symbol>")
def price_history(symbol):
    frequency = request.args.get("frequency", 5, type=int)
    period = request.args.get("period", 2, type=int)
    s = services()
    return jsonify(chart_market.price_history(s.broker_clients(), symbol, frequency, period))


@bp.get("/api/chart/futures_contract")
def futures_contract():
    s = services()
    root = (request.args.get("root") or "").strip().upper()
    symbol = s.futures_contracts.front(s.broker_clients(), root)
    if not symbol:
        return _error(f"no active contract for {root}", 404)
    return jsonify({"root": root, "symbol": symbol})


@bp.get("/api/chart/prev_close")
def prev_close():
    root = (request.args.get("symbol") or "").strip().upper()
    close = chart_market.futures_prev_close(services().broker_clients(), root)
    return jsonify({"symbol": root, "prev_close": float(close)})


# --- layouts ---


@bp.get("/api/chart_layouts")
def list_layouts():
    return jsonify(chart_grid.list_layouts(services().session_factory))


@bp.get("/api/chart_layouts/<layout_id>")
def get_layout(layout_id):
    return jsonify(chart_grid.get_layout(services().session_factory, layout_id))


@bp.post("/api/chart_layouts")
def create_layout():
    return jsonify(chart_grid.create_layout(services().session_factory, _json())), 201


@bp.put("/api/chart_layouts/<layout_id>")
def update_layout(layout_id):
    return jsonify(chart_grid.update_layout(services().session_factory, layout_id, _json()))


@bp.put("/api/chart_layouts/<layout_id>/activate")
def activate_layout(layout_id):
    return jsonify(chart_grid.activate_layout(services().session_factory, layout_id))


@bp.delete("/api/chart_layouts/<layout_id>")
def delete_layout(layout_id):
    chart_grid.delete_layout(services().session_factory, layout_id)
    return "", 204


# --- tabs ---


@bp.get("/api/chart_tabs")
def list_tabs():
    return jsonify(chart_grid.list_tabs(services().session_factory))


@bp.post("/api/chart_tabs")
def create_tab():
    return jsonify(chart_grid.create_tab(services().session_factory, _json())), 201


@bp.put("/api/chart_tabs/<tab_id>")
def update_tab(tab_id):
    return jsonify(chart_grid.update_tab(services().session_factory, tab_id, _json()))


@bp.delete("/api/chart_tabs/<tab_id>")
def delete_tab(tab_id):
    chart_grid.delete_tab(services().session_factory, tab_id)
    return "", 204


# --- drawings ---


@bp.get("/api/chart_drawings")
def get_drawings():
    return jsonify(
        chart_grid.get_drawings(
            services().session_factory, request.args.get("symbol"), request.args.get("interval")
        )
    )


@bp.put("/api/chart_drawings")
def put_drawings():
    """Every open chart of that symbol and timeframe is told to reload."""
    s = services()
    saved = chart_grid.put_drawings(s.session_factory, _json())
    if s.socketio is not None:
        s.socketio.emit(
            "chart_drawings_changed",
            {"symbol": saved["symbol"], "interval": saved["interval"]},
            to=chart_grid.drawings_room(saved["symbol"], saved["interval"]),
            namespace="/",
        )
    return jsonify(saved)


# --- watchlist ---


@bp.get("/api/watchlist")
def watchlist():
    return jsonify(chart_grid.list_groups(services().session_factory))


@bp.get("/api/watchlist/trading")
def watchlist_trading():
    """The drawer's read-only Trading group: the underlyings of open trades."""
    s = services()
    view = load_journal(s.session_factory, cache=s.positions_cache)
    symbols = {
        chart_grid.watch_symbol(e.position.underlying)
        for e in view.entries
        if e.position.status is not Status.CLOSED
    }
    symbols = {s for s in symbols if chart_grid.WATCH_SYMBOL.match(s)}
    return jsonify({"symbols": sorted(symbols)})


@bp.post("/api/watchlist/groups")
def create_group():
    return jsonify(chart_grid.create_group(services().session_factory, _json())), 201


@bp.put("/api/watchlist/groups/<group_id>")
def update_group(group_id):
    return jsonify(chart_grid.update_group(services().session_factory, group_id, _json()))


@bp.delete("/api/watchlist/groups/<group_id>")
def delete_group(group_id):
    chart_grid.delete_group(services().session_factory, group_id)
    return jsonify({"ok": True})


@bp.put("/api/watchlist/order")
def reorder_groups():
    return jsonify(chart_grid.reorder_groups(services().session_factory, _json().get("ids")))
