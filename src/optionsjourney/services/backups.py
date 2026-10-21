"""Backing up and restoring the database (docs/decisions/0023).

Copies use SQLite's own backup feature, which is safe while the app is using
the database. A copy is written under a temporary name and renamed when
complete, so a backup interrupted halfway never looks like a good one.
Restoring first backs up the database as it is now, so a restore can be
undone by restoring that copy."""

import contextlib
import gzip
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from optionsjourney.core.backups import KEEP, backup_name, backup_time, is_due, to_delete
from optionsjourney.services import settings

logger = logging.getLogger(__name__)


class BackupError(Exception):
    """A backup or restore that couldn't be done. The message is for the user."""


@dataclass(frozen=True)
class Backup:
    name: str
    made_at: datetime
    size: int  # bytes


def folder(session_factory: sessionmaker) -> Path:
    return Path(settings.get(session_factory, "backup_folder"))


def list_backups(session_factory: sessionmaker) -> list[Backup]:
    """The backups in the folder, newest first. None if it can't be read."""
    try:
        entries = list(folder(session_factory).iterdir())
    except OSError:
        return []
    found = [
        Backup(entry.name, made_at, entry.stat().st_size)
        for entry in entries
        if (made_at := backup_time(entry.name)) is not None
    ]
    return sorted(found, key=lambda b: b.made_at, reverse=True)


def backup_now(db_path: Path, session_factory: sessionmaker, now: datetime) -> Path:
    """Copy the database into the backup folder, compressed; returns the new
    file."""
    target_folder = folder(session_factory)
    target = target_folder / backup_name(now)
    plain = target.with_name(target.name + ".copy")
    partial = target.with_name(target.name + ".partial")
    try:
        target_folder.mkdir(parents=True, exist_ok=True)
        _copy(db_path, plain)
        with open(plain, "rb") as src, gzip.open(partial, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        os.replace(partial, target)
    except (OSError, sqlite3.Error) as e:
        raise BackupError(f"The backup folder {target_folder} can't be used: {e}") from e
    finally:
        for leftover in (plain, partial):
            with contextlib.suppress(OSError):
                leftover.unlink(missing_ok=True)
    keep = int(settings.get(session_factory, "backup_keep") or KEEP)
    for name in to_delete(os.listdir(target_folder), keep=keep):
        (target_folder / name).unlink(missing_ok=True)
    logger.info("backed up to %s", target)
    return target


def run_if_due(db_path: Path, session_factory: sessionmaker, now: datetime) -> Path | None:
    """Back up if one is due under the chosen frequency. Raises BackupError."""
    frequency = settings.get(session_factory, "backup_frequency")
    existing = list_backups(session_factory)
    last = existing[0].made_at if existing else None
    if not is_due(last, frequency, now):
        return None
    return backup_now(db_path, session_factory, now)


def restore(db_path: Path, session_factory: sessionmaker, name: str, now: datetime) -> Path:
    """Replace the database with the backup called `name` from the backup
    folder, after backing up the database as it is now. Returns that copy."""
    source = folder(session_factory) / name
    if Path(name).name != name or backup_time(name) is None or not source.is_file():
        raise BackupError("That backup isn't in the backup folder.")
    safety_copy = backup_now(db_path, session_factory, now)
    try:
        if name.endswith(".gz"):
            plain = db_path.with_name(db_path.name + ".restoring")
            try:
                with gzip.open(source, "rb") as src, open(plain, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                _copy(plain, db_path)
            finally:
                plain.unlink(missing_ok=True)
        else:
            _copy(source, db_path)
    except (OSError, sqlite3.Error) as e:
        raise BackupError(f"The backup couldn't be restored: {e}") from e
    logger.info("restored %s (the database before it is in %s)", source, safety_copy)
    return safety_copy


# --- In the background, while the app runs ----------------------------------------

CHECK_EVERY = 60 * 60  # seconds
_problem: tuple[datetime, str] | None = None


def last_problem() -> tuple[datetime, str] | None:
    """The last scheduled backup's failure (when, what), if it hasn't since worked."""
    return _problem


def start_scheduler(db_path: Path, session_factory: sessionmaker) -> threading.Thread:
    """Check at start and then hourly, and back up when one is due. Runs in
    the app process (__main__), never in tests."""

    def loop():
        global _problem
        while True:
            now = datetime.now(UTC)
            try:
                run_if_due(db_path, session_factory, now)
                _problem = None
            except BackupError as e:
                logger.warning("scheduled backup failed: %s", e)
                _problem = (now, str(e))
            except Exception:  # noqa: BLE001 -- never let the loop die
                logger.exception("scheduled backup crashed")
            time.sleep(CHECK_EVERY)

    thread = threading.Thread(target=loop, name="backups", daemon=True)
    thread.start()
    return thread


def _copy(source: Path, target: Path) -> None:
    """SQLite's online backup: a consistent copy even while in use."""
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    # sqlite3's context manager commits but doesn't close
    src.close()
    dst.close()


def choose_folder() -> str | None:
    """Ask the computer's own folder picker (the app runs on this computer, so
    the dialog opens in front of the user). None if they cancel."""
    prompt = "Choose where OptionsJourney keeps its backups"
    if sys.platform == "darwin":
        args = ["osascript", "-e", f'POSIX path of (choose folder with prompt "{prompt}")']
    elif sys.platform == "win32":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
            f"$d.Description = '{prompt}';"
            "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"
        )
        args = ["powershell", "-NoProfile", "-Command", script]
    else:
        return None
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=300)
    except OSError, subprocess.TimeoutExpired:
        return None
    path = done.stdout.strip()
    if done.returncode != 0 or not path:
        return None
    return path.rstrip("/\\") or path
