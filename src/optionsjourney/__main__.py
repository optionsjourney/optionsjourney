"""Run the app: `uv run optionsjourney` (or `python -m optionsjourney`)."""

import sys

from optionsjourney import logs
from optionsjourney.config import HOST, ConfigError, load_settings


def main() -> None:
    logs.start_console()
    from optionsjourney.services.economic_events import quiet_http_logs  # noqa: PLC0415

    quiet_http_logs()  # httpx logs request URLs, FRED's key included
    try:
        settings = load_settings()
    except ConfigError as e:
        sys.exit(f"Settings problem: {e}")
    from optionsjourney.demo import marker  # noqa: PLC0415

    # A folder this mode refuses gets nothing written into it, not even the log file
    problem = marker.refusal(settings.data_dir, settings.is_demo)
    if problem:
        sys.exit(f"Settings problem: {problem}")
    logs.start_file(settings.data_dir)

    from optionsjourney.app import create_app

    try:
        app = create_app(settings)
    except ConfigError as e:  # demo mode refuses a data folder holding a real database
        sys.exit(f"Settings problem: {e}")

    from optionsjourney.services import background
    from optionsjourney.web import services

    with app.app_context():
        background.start_all(settings.database_path, services())
    print(f"OptionsJourney is running at http://{HOST}:{settings.port}")
    from optionsjourney.services import notify  # noqa: PLC0415

    try:
        app.run(host=settings.bind_host, port=settings.port)
    finally:
        notify.wait_idle(5)  # alerts still queued get a few seconds to go out


if __name__ == "__main__":
    main()
