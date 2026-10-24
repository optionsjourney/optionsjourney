"""Telling a demo database apart, and the environment a demo app process runs with.
Local and gateway mode read it at every start, and the command reads it before writing
its log file, so it imports nothing heavy."""

import shutil
import sqlite3
from collections.abc import Mapping
from pathlib import Path

DB_NAME = "optionsjourney.db"
DEMO_MARK = "_demo_database"  # settings row: this database was made by the demo


def is_demo_database(path: Path) -> bool:
    """Whether the database at `path` was made by the demo. Opened read-only, so a
    real database is never touched; anything unreadable is not a demo database."""
    if not path.exists():
        return False
    # A read-only open of a WAL database leaves -wal and -shm files behind (decision
    # 0112). With no -wal file everything is in the main file, so read it as
    # immutable, which creates nothing; otherwise the -wal must be read too.
    wal = path.with_name(path.name + "-wal")
    flags = "mode=ro" if wal.exists() else "mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(f"file:{path}?{flags}", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (DEMO_MARK,))
        found = row.fetchone()
    except sqlite3.Error:
        return False
    finally:
        connection.close()
    return found is not None and found[0] == "1"


class FolderRefused(Exception):
    """A folder holds files that aren't the demo's, so it is not emptied."""


def empty_folder(folder: Path, *, ours: bool = False) -> None:
    """Make `folder` an empty folder: made if missing, emptied if it holds the demo's
    data or is `ours` (a day ran in it). A folder holding anything else is never touched."""
    folder.mkdir(parents=True, exist_ok=True)
    entries = list(folder.iterdir())
    if not entries:
        return
    database = folder / DB_NAME
    if not ours and not (database.exists() and is_demo_database(database)):
        raise FolderRefused(
            f"{folder} holds files that aren't the demo's. The demo only runs in an empty "
            "folder (or one it made before): choose another --data-dir."
        )
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def refusal(data_dir: Path, demo: bool) -> str | None:
    """Why this data folder can't open in this mode, or None. Demo mode runs only in an
    empty folder or one the demo made; local and gateway mode never open a demo's (its
    risk boxes are ticked and a Live bot is armed)."""
    path = data_dir / DB_NAME
    if not path.exists():
        return None
    ours = is_demo_database(path)
    if demo and not ours:
        return (
            f"{data_dir} holds a database that isn't a demo database. Demo mode only "
            "runs in an empty data folder (or one the demo made): set "
            "OPTIONSJOURNEY_DATA_DIR to an empty folder to try the demo."
        )
    if not demo and ours:
        return (
            f"{data_dir} holds the demo database, which only runs with "
            "BROKER_CREDENTIALS=demo. Use another data folder (OPTIONSJOURNEY_DATA_DIR) for "
            "your own trading."
        )
    return None


def demo_env(base: Mapping[str, str], data_dir: Path, port: int | None = None) -> dict[str, str]:
    """The environment for a demo app process (the runner's and each seat's): `base`, in
    demo mode on `data_dir`, listening on this computer only, and reading no `.env`
    (config.py looks for one upward from its own folder, which would be the repo's)."""
    env = dict(base)
    env.update(
        PYTHON_DOTENV_DISABLED="1",
        LISTEN_ON_NETWORK="no",
        BROKER_CREDENTIALS="demo",
        OPTIONSJOURNEY_DATA_DIR=str(data_dir),
    )
    if port is not None:
        env["PORT"] = str(port)
    return env
