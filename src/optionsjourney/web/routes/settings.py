"""The Settings page: every setting in core/settings.py, a form per section."""

import logging
import threading
from datetime import UTC, datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from optionsjourney.brokers.credentials.base import CredentialsUnavailable
from optionsjourney.brokers.credentials.local import LocalProvider
from optionsjourney.core.settings import SECTIONS, SettingError
from optionsjourney.db import migrate
from optionsjourney.services import backups, economic_events, notify, settings
from optionsjourney.web import services

bp = Blueprint("settings", __name__)
logger = logging.getLogger(__name__)


@bp.get("/settings")
def settings_page():
    return _render(settings.get_all(services().session_factory))


@bp.post("/settings/<section_key>")
def save(section_key):
    if section_key not in {s.key for s in SECTIONS}:
        abort(404)
    try:
        settings.save_section(services().session_factory, section_key, request.form)
    except SettingError as e:
        values = settings.get_all(services().session_factory) | dict(request.form)
        return _render(values, problems=e.problems, active=section_key)
    flash("Settings saved.", "success")
    return redirect(url_for("settings.settings_page", _anchor=section_key))


@bp.post("/settings/notifications/test")
def test_notifications():
    results = notify.send(services().session_factory, notify.TEST)
    if not results:
        flash("Nothing to send to yet: add a Discord webhook or a Telegram bot first.", "warning")
    for channel, problem in results:
        if problem:
            flash(f"{channel}: {problem}", "warning")
        else:
            flash(f"{channel}: test sent. Check that it arrived.", "success")
    return redirect(url_for("settings.settings_page", _anchor="notifications"))


@bp.post("/settings/backups/browse")
def browse_backup_folder():
    """Open this computer's folder picker; the page puts the answer in the
    folder field."""
    return jsonify(path=backups.choose_folder())


@bp.post("/settings/backups/now")
def backup_now():
    s = services()
    try:
        made = backups.backup_now(s.settings.database_path, s.session_factory, datetime.now(UTC))
        flash(f"Backed up to {made.name}.", "success")
    except backups.BackupError as e:
        flash(str(e), "warning")
    return redirect(url_for("settings.settings_page", _anchor="backups"))


@bp.post("/settings/backups/restore")
def restore():
    s = services()
    try:
        safety = backups.restore(
            s.settings.database_path,
            s.session_factory,
            request.form.get("name", ""),
            datetime.now(UTC),
        )
    except backups.BackupError as e:
        flash(str(e), "warning")
        return redirect(url_for("settings.settings_page", _anchor="backups"))
    migrate(s.engine)  # an older backup may predate the newest tables
    s.positions_cache.clear()
    flash(f"Restored. The journal as it was just before is saved as {safety.name}.", "success")
    return redirect(url_for("settings.settings_page", _anchor="backups"))


# --- Market calendar ------------------------------------------------------------------


def _calendar(session_factory, now):
    """The calendar lists and sources; if they can't be read the page still shows."""
    try:
        return economic_events.summary(session_factory, now)
    except Exception:  # noqa: BLE001 -- never a 500 on the Settings page
        logger.exception("couldn't read the market calendar for the Settings page")
        return None


def _start_refresh(session_factory, now) -> None:
    """Refresh every calendar source off the request (each fetch can take seconds)."""
    threading.Thread(
        target=economic_events.try_refresh_all,
        args=(session_factory, now),
        name="market-calendar-now",
        daemon=True,
    ).start()


@bp.post("/settings/market_calendar/refresh")
def calendar_refresh():
    """Refresh now: fetch ForexFactory, the Fed, FRED (with a key) and NYSE."""
    back = redirect(url_for("settings.settings_page", _anchor="market_calendar"))
    if economic_events.refreshing():
        flash("A market calendar refresh is already running.", "warning")
        return back
    _start_refresh(services().session_factory, datetime.now(UTC))
    flash("Refreshing the market calendar. Reload this page in a minute to see it.", "success")
    return back


# --- Brokers (local mode) ------------------------------------------------------------

BROKER_NAMES = {"tastytrade": "TastyTrade", "schwab": "Schwab"}


def _back_to_brokers():
    """Back where the user came from: a step of the setup wizard (`next`, a
    path in this app only), or Settings -> Brokers."""
    target = request.values.get("next", "")
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return redirect(target)
    return redirect(url_for("settings.settings_page", _anchor="brokers"))


def _local_provider():
    """The local provider, or None in gateway mode (logins come from Tracker2)."""
    credentials = services().credentials
    return credentials if isinstance(credentials, LocalProvider) else None


@bp.post("/settings/brokers/tastytrade")
def connect_tastytrade():
    provider = _local_provider()
    if provider is None:
        abort(404)
    try:
        provider.connect_tastytrade(
            request.form.get("client_secret", ""),
            request.form.get("refresh_token", ""),
            sandbox=request.form.get("sandbox") == "on",
        )
        flash(
            "TastyTrade connected. Click Sync now on the Transactions page to bring in "
            "your trades.",
            "broker-success",
        )
    except CredentialsUnavailable as e:
        flash(str(e), "broker-warning")
    return _back_to_brokers()


@bp.post("/settings/brokers/schwab/start")
def start_schwab():
    """Keep the Schwab app's details and send the browser to Schwab's login.
    Blank fields reuse the kept ones, so Reconnect needs no typing."""
    provider = _local_provider()
    if provider is None:
        abort(404)
    try:
        login_url = provider.start_schwab(
            request.form.get("app_key", ""),
            request.form.get("app_secret", ""),
            request.form.get("callback_url", ""),
        )
    except CredentialsUnavailable as e:
        flash(str(e), "broker-warning")
        return _back_to_brokers()
    return redirect(login_url)


@bp.post("/settings/brokers/schwab/finish")
def finish_schwab():
    provider = _local_provider()
    if provider is None:
        abort(404)
    try:
        provider.finish_schwab(request.form.get("redirected_url", ""))
        flash(
            "Schwab connected. It needs a new login every 7 days; the app reminds you.",
            "broker-success",
        )
    except CredentialsUnavailable as e:
        flash(str(e), "broker-warning")
    return _back_to_brokers()


@bp.post("/settings/brokers/<broker>/disconnect")
def disconnect(broker):
    provider = _local_provider()
    if provider is None or broker not in BROKER_NAMES:
        abort(404)
    provider.disconnect(broker)
    flash(
        f"{BROKER_NAMES[broker]} disconnected. Its login was removed from this computer.",
        "broker-success",
    )
    return _back_to_brokers()


def _render(values: dict, problems: dict | None = None, active: str = ""):
    """The page; `active` is the section to show first (one with a problem)."""
    factory = services().session_factory
    return render_template(
        "settings.html",
        sections=SECTIONS,
        values=values,
        problems=problems or {},
        backups=backups.list_backups(factory),
        backup_problem=backups.last_problem(),
        brokers=_local_provider(),
        streams=services().quote_hub.status(),
        calendar=_calendar(factory, datetime.now(UTC)),
        active=active,
    )
