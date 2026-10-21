"""Alerts the app raises about itself (#12): things the user should know or
fix, worked out from the app's state each time, so an alert goes away by
itself once its cause is fixed. Price and trade alerts come later (#27)."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

STALE_AFTER = timedelta(days=3)
BROKER_NAMES = {"tastytrade": "TastyTrade", "schwab": "Schwab"}


@dataclass(frozen=True)
class Alert:
    level: str  # "problem" (something failed) or "warning" (worth a look)
    title: str
    detail: str
    link: str  # where to fix it
    action: str  # what the link does
    user_alert_id: int | None = None  # set for alerts the user created (#27)


def sync_alerts(states: Iterable, now: datetime) -> list[Alert]:
    """From each account's sync state: failures, and syncs gone stale."""
    alerts, stale = [], {}
    for state in states:
        name = BROKER_NAMES.get(state.broker, state.broker)
        if state.last_error:
            alerts.append(
                Alert(
                    "problem",
                    f"{name} ••{state.account_number[-4:]} didn't sync",
                    state.last_error,
                    "/transactions",
                    "Try Sync now",
                )
            )
        elif state.synced_through and now - state.synced_through > STALE_AFTER:
            stale[name] = max(stale.get(name, STALE_AFTER), now - state.synced_through)
    for name, age in stale.items():
        alerts.append(
            Alert(
                "warning",
                f"{name} last synced {age.days} days ago",
                "Trades since then aren't in the journal yet.",
                "/transactions",
                "Sync now",
            )
        )
    return alerts


SCHWAB_REMIND_BEFORE = timedelta(days=1)


def schwab_login_alert(expires_at: datetime | None, now: datetime) -> Alert | None:
    """Schwab asks for a new login every 7 days: remind a day before, and
    say so plainly once it has run out."""
    if expires_at is None or expires_at - now > SCHWAB_REMIND_BEFORE:
        return None
    if expires_at <= now:
        return Alert(
            "problem",
            "Schwab needs you to log in again",
            "Schwab's 7-day login has run out, so Schwab accounts aren't syncing.",
            "/settings#brokers",
            "Reconnect Schwab",
        )
    hours = max(int((expires_at - now).total_seconds() // 3600), 1)
    return Alert(
        "warning",
        f"Schwab login runs out in {hours} hour{'s' if hours != 1 else ''}",
        "Log in again before then to keep Schwab syncing.",
        "/settings#brokers",
        "Reconnect Schwab",
    )


REMINDERS = (("12h", timedelta(hours=12)), ("48h", timedelta(hours=48)))


def schwab_reminder(expires_at: datetime | None, now: datetime) -> str | None:
    """Which refresh-login reminder is due (bots slice 11; Tracker2's token alerts at
    48 h and 12 h left): "expired" once it has run out, "12h" with 12 hours or less
    left, "48h" with 48 or less, else None."""
    if expires_at is None:
        return None
    left = expires_at - now
    if left <= timedelta(0):
        return "expired"
    return next((level for level, before in REMINDERS if left <= before), None)


def backup_alert(problem: tuple[datetime, str] | None) -> Alert | None:
    if problem is None:
        return None
    return Alert(
        "problem", "The last backup didn't work", problem[1], "/settings#backups", "Check Backups"
    )


def order_alerts(unclear: Iterable, refused: Iterable) -> list[Alert]:
    """Orders whose outcome is unclear (until cleared), and today's closing
    orders refused because the broker couldn't be checked (one per account)."""
    alerts = []
    for row in unclear:
        name = BROKER_NAMES.get(row.broker, row.broker)
        alerts.append(
            Alert(
                "problem",
                f"{name} ••{row.account_number[-4:]}: an order's outcome is unclear",
                row.result,
                "/orders",
                "Open the order log",
            )
        )
    seen = set()
    for row in refused:
        if (row.broker, row.account_number) in seen:
            continue
        seen.add((row.broker, row.account_number))
        name = BROKER_NAMES.get(row.broker, row.broker)
        alerts.append(
            Alert(
                "problem",
                f"{name} ••{row.account_number[-4:]}: a closing order wasn't sent",
                row.result,
                "/orders",
                "Open the order log",
            )
        )
    return alerts
