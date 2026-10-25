"""The Alerts page: what the app needs the user to know or fix, and the
alerts the user sets on prices and trades (#27)."""

from datetime import UTC, datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from optionsjourney.core import trade_events as trade_events_core
from optionsjourney.core.stats import net_entry
from optionsjourney.services import app_alerts, journal, trade_events, user_alerts
from optionsjourney.services.alerts import system_alerts
from optionsjourney.web import services

bp = Blueprint("alerts", __name__)


@bp.route("/alerts")
def alerts_page():
    s = services()
    kind = request.args.get("kind", "")
    show = request.args.get("show", "active")
    show = show if show in user_alerts.TABLE_SHOWS else "active"

    def open_pl(key):
        return journal.open_pl_of(s.session_factory, s.positions_cache, s.quote_hub, key)

    recent = app_alerts.recent(s.session_factory)
    unread_ids = {a.id for a in recent if a.read_at is None}  # before this view marks them read
    app_alerts.mark_read(s.session_factory, unread_ids, datetime.now(UTC))  # only the ones shown
    return render_template(
        "alerts.html",
        unread_alerts=recent,
        unread_ids=unread_ids,
        alerts=system_alerts(s.session_factory, credentials=s.credentials),
        show=show,
        price_alerts=user_alerts.table(s.session_factory, show, s.quote_hub, open_pl),
        kind=kind,
        popups=trade_events.popups_on(s.session_factory),
        kinds=trade_events_core.LABELS,
        events=trade_events.recent(s.session_factory, kind=kind),
    )


BELL_LIMIT = 15  # the bell's list: the newest alerts (#272)


@bp.get("/api/alerts/recent")
def api_recent():
    """The bell's list (#272): what needs fixing or a look, then the newest alerts.
    Like the page, it marks the ones it shows read."""
    s = services()
    recent = app_alerts.recent(s.session_factory, limit=BELL_LIMIT)
    unread_ids = {a.id for a in recent if a.read_at is None}
    app_alerts.mark_read(s.session_factory, unread_ids, datetime.now(UTC))
    # #275: a price or trade alert that went off is listed once, as a new alert below
    standing = [
        a
        for a in system_alerts(s.session_factory, credentials=s.credentials)
        if a.user_alert_id is None
    ]
    return jsonify(
        {
            "standing": [
                {
                    "level": a.level,
                    "title": a.title,
                    "detail": a.detail,
                    "link": a.link,
                    "action": a.action,
                }
                for a in standing
            ],  # fmt: skip
            "recent": [
                {
                    "id": a.id,
                    "title": a.title,
                    "detail": a.detail,
                    "link": a.link,
                    "created_at": a.created_at.isoformat(),
                    "unread": a.id in unread_ids,
                }
                for a in recent
            ],  # fmt: skip
            "unread": app_alerts.unread_count(s.session_factory),
        }
    )


def _back():
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for("alerts.alerts_page"))


@bp.post("/alerts/new")
def create():
    form = request.form
    quiet = form.get("quiet") == "1"  # in the app only, never sent (#300)
    try:
        if form.get("kind") == "trade_pl":
            keys = form.getlist("position")
            name = form.get("name", "").strip() or "Trade"
            level = form.get("level", "")
            if form.get("unit") == "pct":
                level, name = _pct_of_entry(keys, level, name)
            user_alerts.create_trade(
                services().session_factory, keys if len(keys) > 1 else (keys or [""])[0],
                name, form.get("direction", ""), level, form.get("note", ""), quiet,
            )  # fmt: skip
        else:
            user_alerts.create_price(
                services().session_factory,
                form.get("symbol", ""),
                form.get("direction", ""),
                form.get("level", ""),
                form.get("note", ""),
                quiet,
            )
        flash("Alert set. You'll see it here, and in the top bar, when it goes off.", "success")
    except user_alerts.AlertError as e:
        flash(str(e), "warning")
    return _back()


def _pct_of_entry(keys, text, name):
    """A "% of entry" level as dollars of P/L, from the trades' net entry (#300)."""
    s = services()
    view = journal.load_journal(s.session_factory, cache=s.positions_cache)
    positions = [e.position for key in keys if (e := journal.find_entry(view, key))]
    pct = text.strip().rstrip("%")
    level = user_alerts.pct_level(pct, net_entry(positions))
    return level, f"{name} ({'' if pct.startswith('-') else '+'}{pct}% of entry)"


@bp.post("/alerts/<int:alert_id>/<action>")
def change(alert_id, action):
    actions = {
        "dismiss": user_alerts.dismiss,
        "disable": user_alerts.disable,
        "rearm": user_alerts.rearm,
        "delete": user_alerts.delete,
    }
    if action in actions:
        try:
            actions[action](services().session_factory, alert_id)
        except user_alerts.AlertError as e:
            flash(str(e), "warning")
    return _back()


# --- Analyze's Alerts dialog (#54): Tracker2's /api/alerts, on the app's alerts ---


def _api_error(message: str, status: int = 400):
    return jsonify({"status": "error", "message": message}), status


@bp.get("/api/alerts")
def api_list():
    wanted = {s for s in (request.args.get("state") or "").split(",") if s} or None
    return jsonify({"alerts": user_alerts.listing(services().session_factory, wanted)})


@bp.post("/api/alerts")
def api_create():
    body = request.get_json(silent=True) or {}
    factory = services().session_factory
    try:
        if body.get("kind") == "underlying":
            alert_id = user_alerts.create_price(
                factory,
                body.get("symbol") or "",
                body.get("direction") or "",
                body.get("threshold"),
            )
        elif body.get("kind") == "spread":
            alert_id = user_alerts.create_spread(
                factory,
                body.get("symbol") or "",
                body.get("contracts"),
                body.get("direction") or "",
                body.get("threshold"),
            )
        else:
            return _api_error("Set trade alerts from the trade's page.")
    except user_alerts.AlertError as e:
        return _api_error(str(e))
    return jsonify({"status": "success", "id": alert_id}), 201


@bp.delete("/api/alerts/<int:alert_id>")
def api_delete(alert_id):
    user_alerts.delete(services().session_factory, alert_id)
    return jsonify({"status": "success"})


@bp.post("/api/alerts/<int:alert_id>/<action>")
def api_action(alert_id, action):
    actions = {"disable": user_alerts.disable, "rearm": user_alerts.rearm}
    if action not in actions:
        return _api_error("Unknown action.", 404)
    try:
        actions[action](services().session_factory, alert_id)
    except user_alerts.AlertError as e:
        return _api_error(str(e), 404)
    return jsonify({"status": "success"})
