"""What the menu shows about the app's state."""

from optionsjourney.core.alerts import Alert

MAX_ALERTS = 3


def status_lines(alerts: list[Alert]) -> list[str]:
    lines = ["● Running"]
    lines += [f"⚠ {a.title}" for a in alerts[:MAX_ALERTS]]
    if len(alerts) > MAX_ALERTS:
        lines.append(f"⚠ …and {len(alerts) - MAX_ALERTS} more (open Alerts)")
    return lines
