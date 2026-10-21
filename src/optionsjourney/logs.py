"""Where the app writes its log (decision 0094): the console, and a file in the data
folder's logs/ so a user can send it when something goes wrong."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_NAME = "optionsjourney.log"


def log_dir(data_dir: Path) -> Path:
    return data_dir / "logs"


def start_console() -> None:
    logging.basicConfig(level=logging.INFO, format=FORMAT)


def start_file(data_dir: Path) -> Path:
    """Adds the log file (1 MB, plus 3 older ones) to the root logger; returns its path."""
    folder = log_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOG_NAME
    root = logging.getLogger()
    if not any(getattr(h, "baseFilename", None) == str(path) for h in root.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
    return path
