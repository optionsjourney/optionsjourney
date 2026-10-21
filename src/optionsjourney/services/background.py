"""The background jobs, started the same way by `uv run optionsjourney` and the menu bar
app (decision 0094: the menu bar app had been missing auto-sync and fill sync)."""

from optionsjourney.services import (
    auto_sync,
    backups,
    bot_loops,
    expiry_settle,
    fill_sync,
    pl_day,
    stock_history,
)
from optionsjourney.services.user_alerts import start_alert_checker


def start_all(database_path, services) -> None:
    backups.start_scheduler(database_path, services.session_factory)
    start_alert_checker(services)
    pl_day.start(services)
    stock_history.start(services)
    expiry_settle.start(services)
    bot_loops.start(services)
    auto_sync.start(services)
    fill_sync.start(services)
