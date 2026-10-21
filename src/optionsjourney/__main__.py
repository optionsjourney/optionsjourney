"""Run the app: `uv run optionsjourney` (or `python -m optionsjourney`)."""

import logging
import sys

from optionsjourney.config import HOST, ConfigError, load_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from optionsjourney.services.economic_events import quiet_http_logs  # noqa: PLC0415

    quiet_http_logs()  # httpx logs request URLs, FRED's key included
    try:
        settings = load_settings()
    except ConfigError as e:
        sys.exit(f"Settings problem: {e}")

    from optionsjourney.app import create_app

    app = create_app(settings)

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
    from optionsjourney.web import services

    with app.app_context():
        backups.start_scheduler(settings.database_path, services().session_factory)
        start_alert_checker(services())
        pl_day.start(services())
        stock_history.start(services())
        expiry_settle.start(services())
        bot_loops.start(services())
        auto_sync.start(services())
        fill_sync.start(services())
    print(f"OptionsJourney is running at http://{HOST}:{settings.port}")
    from optionsjourney.services import notify  # noqa: PLC0415

    try:
        app.run(host=settings.bind_host, port=settings.port)
    finally:
        notify.wait_idle(5)  # alerts still queued get a few seconds to go out


if __name__ == "__main__":
    main()
