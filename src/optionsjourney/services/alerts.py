"""The app's own alerts (#12), worked out fresh on each page."""

from datetime import UTC, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from optionsjourney.core.alerts import (
    Alert,
    backup_alert,
    order_alerts,
    schwab_login_alert,
    sync_alerts,
)
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.db.models import OrderLog, SyncState
from optionsjourney.services import backups, user_alerts


def system_alerts(
    session_factory: sessionmaker, now: datetime | None = None, credentials=None
) -> list[Alert]:
    """Problems first, then warnings."""
    now = now or datetime.now(UTC)
    with session_factory() as session:
        states = list(session.scalars(select(SyncState)))
        start = datetime.combine(now.astimezone(MARKET_TZ).date(), time(), MARKET_TZ)
        unclear = list(
            session.scalars(
                select(OrderLog).where(
                    OrderLog.result.like("Unclear:%"), OrderLog.cleared_at.is_(None)
                )
            )
        )
        refused = list(
            session.scalars(
                select(OrderLog)
                .where(
                    OrderLog.action == "refuse",
                    OrderLog.result.like("Couldn't check%"),
                    OrderLog.created_at >= start.astimezone(UTC),
                )
                .order_by(OrderLog.id.desc())
            )
        )
    alerts = sync_alerts(states, now)
    alerts.extend(order_alerts(unclear, refused))
    expires_at = getattr(credentials, "schwab_login_expires_at", lambda: None)()
    if schwab := schwab_login_alert(expires_at, now):
        alerts.append(schwab)
    if backup := backup_alert(backups.last_problem()):
        alerts.append(backup)
    for alert in user_alerts.triggered(session_factory):
        fired = user_alerts.describe(alert, alert.triggered_value)
        alerts.append(
            Alert("warning", fired.title, fired.detail, fired.link, "Look", user_alert_id=alert.id)
        )
    return sorted(alerts, key=lambda a: a.level != "problem")
