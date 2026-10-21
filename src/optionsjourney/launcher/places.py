"""Folders the menu opens, and starting a fresh copy of the app (decision 0094)."""

import os
import subprocess
import sys
from pathlib import Path


def app_folder(prefix: str | None = None) -> Path:
    """The installed app: the folder that holds its .venv (and pyproject.toml)."""
    venv = Path(prefix or sys.prefix)
    folder = venv.parent
    return folder if (folder / "pyproject.toml").exists() else Path.cwd()


def open_folder(path: Path) -> None:
    """Shows a folder in Finder or File Explorer."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 -- a folder we made, opened in Explorer
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])  # noqa: S603


def start_again(command: list[str], folder: Path) -> None:
    """Starts a new copy of the menu bar app, detached, before this one quits."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    quiet = subprocess.DEVNULL
    subprocess.Popen(command, cwd=folder, creationflags=flags,  # noqa: S603
                     start_new_session=sys.platform != "win32",
                     stdin=quiet, stdout=quiet, stderr=quiet)  # fmt: skip
