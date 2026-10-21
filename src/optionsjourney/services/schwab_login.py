"""Schwab's refresh-login reminders (bots slice 11; Tracker2's schwab_token_alerts).

Schwab asks for a fresh login every 7 days. Ahead of that, at 48 and 12 hours left,
and once it has run out, a safety alert goes to every channel, each once per login
(the login's end and the levels said are kept in the settings table, so a restart
doesn't repeat them). Without a login (gateway mode, or Schwab not connected) there's
nothing to time. The page banner (core/alerts.schwab_login_alert) is separate."""

import json
import logging
from datetime import datetime

from optionsjourney.core.alerts import schwab_reminder
from optionsjourney.db.models import Setting
from optionsjourney.services.user_alerts import Fired

logger = logging.getLogger(__name__)

REMINDED_KEY = "_schwab_login_reminded"
LINK = "/settings#brokers"
_ORDER = ("48h", "12h", "expired")


def _said(session_factory) -> dict:
    with session_factory() as session:
        row = session.get(Setting, REMINDED_KEY)
        try:
            return json.loads(row.value) if row is not None else {}
        except ValueError:
            return {}


def _record(session_factory, login: str, levels: list[str], now: datetime) -> None:
    with session_factory() as session:
        row = session.get(Setting, REMINDED_KEY) or Setting(key=REMINDED_KEY)
        row.value, row.updated_at = json.dumps({"login": login, "levels": levels}), now
        session.add(row)
        session.commit()


def _event(level: str, expires_at: datetime, now: datetime, bots: bool) -> Fired:
    """`bots`: Schwab bots are on, or there are some (review M-1): only then are they
    mentioned."""
    stopped = (
        ", and Schwab bots can't send orders, so their stops, which are watched by this "
        "app, can't close anything"
        if bots
        else ""
    )
    if level == "expired":
        return Fired(
            0,
            "Schwab login needs you: it has run out",
            f"Schwab's 7-day login has run out: Schwab isn't syncing{stopped}. Reconnect "
            "Schwab in Settings → Brokers now, and check your broker.",
            LINK,
        )
    hours = max(int((expires_at - now).total_seconds() // 3600), 1)
    return Fired(
        0,
        f"Schwab login needs you: it runs out in {hours} hour{'s' if hours != 1 else ''}",
        f"Schwab asks you to log in again every 7 days. Reconnect Schwab in Settings → "
        f"Brokers before then. If it runs out, Schwab stops syncing{stopped}.",
        LINK,
    )


def check(
    session_factory, credentials, now: datetime, notify=None, bots: bool = False
) -> str | None:
    """Send the reminder that's due, once per login. Returns the level sent, or None.
    Never raises."""
    try:
        expires_at = getattr(credentials, "schwab_login_expires_at", lambda: None)()
        level = schwab_reminder(expires_at, now)
        if level is None:
            return None
        login = expires_at.isoformat()
        said = _said(session_factory)
        levels = list(said.get("levels", [])) if said.get("login") == login else []
        if level in levels:
            return None
        event = _event(level, expires_at, now, bots)
        if notify is not None:
            notify(event)
        else:
            from optionsjourney.services import notify as notify_service

            notify_service.send_soon(session_factory, event)
        # This level and any earlier one count as said: time only moves on.
        _record(session_factory, login, list(_ORDER[: _ORDER.index(level) + 1]), now)
        return level
    except Exception:  # noqa: BLE001 -- a reminder never breaks the loop
        logger.exception("couldn't check the Schwab login's reminders")
        return None
