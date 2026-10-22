"""The processes behind the demo seats: start, check and stop one copy of the app each.

Each seat runs `python -m optionsjourney` with BROKER_CREDENTIALS=demo in its own folder
(`<root>/seats/<n>/`, working directory too, so no `.env` of the repo is read) and its
own process group, so stopping it takes the whole group. The seat manager is not in demo
mode itself, so it may use the network (httpx) to ask its seats how they are.
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from optionsjourney.demo.marker import demo_env, empty_folder
from optionsjourney.demo.seats import COOKIE

log = logging.getLogger(__name__)

_ENV_KEPT = ("PATH", "HOME", "LANG", "TZ")
_CHECK_TIMEOUT = 3.0
STOP_WAIT = 10.0  # seconds stopping seats get to finish before they are killed
KILL_WAIT = 5.0  # seconds a killed seat gets to be reaped
# By absolute path, so a daemon's short PATH can't miss them or pick up another copy.
_PS = "/bin/ps"
_LSOF = "/usr/sbin/lsof"


class StopFailed(Exception):
    """A seat's process didn't go away even after SIGKILL; it is still tracked, so the
    next stop tries again."""


@dataclass(frozen=True)
class Health:
    ok: bool
    idle_seconds: float
    tour_furthest: int


class SeatProcesses:
    def __init__(
        self,
        root: Path,
        python: str = sys.executable,
        command: Sequence[str] = ("-m", "optionsjourney"),
    ) -> None:
        self.root = root
        self.python = python
        self.command = tuple(command)
        self._children: dict[int, subprocess.Popen] = {}

    def folder(self, n: int) -> Path:
        return self.root / "seats" / str(n)

    def start(self, n: int, port: int, token: str, *, keep_data: bool = False) -> None:
        folder = self.folder(n)
        data = folder / "data"
        if keep_data:
            data.mkdir(parents=True, exist_ok=True)
        else:
            empty_folder(data, ours=True)  # the manager's own folder, even half-made
        kept = {k: os.environ[k] for k in _ENV_KEPT if k in os.environ}
        env = demo_env(kept, data, port)
        env["DEMO_SEAT_TOKEN"] = token
        log_file = folder / "seat.log"
        if log_file.exists():
            log_file.replace(folder / "seat.log.1")
        with open(log_file, "ab") as log:
            child = subprocess.Popen(
                [self.python, *self.command],
                cwd=folder,
                env=env,
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        self._children[n] = child
        (folder / "pid").write_text(f"{child.pid}\n{_started(child.pid)}\n")

    def stop(self, n: int, timeout: float = STOP_WAIT) -> None:
        """Stop seat n's process group. Raises StopFailed if it outlives SIGKILL; it
        stays tracked, so calling stop again really tries again."""
        child = self._children.get(n)
        if child is not None:
            _kill_group(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill(n, child)
        self._forget(n)

    def _kill(self, n: int, child: subprocess.Popen) -> None:
        _kill_group(child.pid, signal.SIGKILL)  # still ours: not yet reaped
        try:
            child.wait(timeout=KILL_WAIT)
        except subprocess.TimeoutExpired:
            log.error("seat %d (pid %d) is still there %g s after SIGKILL", n, child.pid, KILL_WAIT)
            raise StopFailed(f"seat {n} did not stop") from None

    def _forget(self, n: int) -> None:
        self._children.pop(n, None)
        (self.folder(n) / "pid").unlink(missing_ok=True)

    def exited(self) -> list[tuple[int, int]]:
        """Seats whose process ended since the last call, with their exit codes."""
        done = []
        for n, child in list(self._children.items()):
            code = child.poll()
            if code is not None:
                del self._children[n]
                (self.folder(n) / "pid").unlink(missing_ok=True)
                done.append((n, code))
        return sorted(done)

    def clear_stale(self) -> None:
        """Kill seats left by an earlier manager. A process is killed only if it is this
        seat's: a seat command, started when the pid file says, in the seat's folder.
        Anything else that took over the pid (the real app included) is left alone."""
        for pidfile in (self.root / "seats").glob("*/pid"):
            folder = pidfile.parent
            pid, started = _read_pidfile(pidfile)
            if pid > 1 and _is_seat(pid, folder, started):
                _kill_group(pid, signal.SIGTERM)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and _group_alive(pid):
                    time.sleep(0.1)
                if _is_seat(pid, folder, started):  # still the same process: no mercy
                    _kill_group(pid, signal.SIGKILL)
            pidfile.unlink(missing_ok=True)

    def stop_all(self, timeout: float = STOP_WAIT) -> None:
        """Stop every seat together: SIGTERM to all, one wait of at most `timeout` in
        all, then SIGKILL for any left. Never raises (it runs as the manager exits)."""
        children = dict(self._children)
        for child in children.values():
            _kill_group(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        for n, child in children.items():
            try:
                child.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                with contextlib.suppress(StopFailed):
                    self._kill(n, child)
                    self._forget(n)
                continue
            self._forget(n)


def _kill_group(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, sig)


def _group_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ps(pid: int, field: str) -> str:
    """One ps field for `pid`, in the C locale so a start time reads the same every time.
    "" if ps can't be run: then nothing looks like a seat and nothing is killed."""
    try:
        done = subprocess.run(
            [_PS, "-o", f"{field}=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except OSError, subprocess.SubprocessError:
        return ""
    return " ".join(done.stdout.split())


def _started(pid: int) -> str:
    """When `pid` started, as ps prints it: with the pid, it names one process."""
    return _ps(pid, "lstart")


def _read_pidfile(path: Path) -> tuple[int, str]:
    try:
        first, _, rest = path.read_text().partition("\n")
        return int(first.strip()), rest.strip()
    except OSError, ValueError:
        return 0, ""


def _cwd(pid: int) -> Path | None:
    """The working directory of `pid`, or None (also when lsof can't be run)."""
    try:
        done = subprocess.run(
            [_LSOF, "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    for line in done.stdout.splitlines():
        if line.startswith("n"):
            return Path(line[1:])
    return None


def _is_seat(pid: int, folder: Path, started: str) -> bool:
    """Whether `pid` is the seat that was started in `folder` at `started`: a python
    running exactly `-m optionsjourney` (not the launcher or the demo runner), leading
    its own group, with `folder` as its working directory."""
    if not started or _started(pid) != started:
        return False
    command = _ps(pid, "command")
    if "python" not in command.lower() or not command.endswith(" -m optionsjourney"):
        return False
    try:
        if os.getpgid(pid) != pid:
            return False
    except ProcessLookupError:
        return False
    cwd = _cwd(pid)
    return cwd is not None and cwd.resolve() == folder.resolve()


def check(port: int, token: str, client: httpx.Client) -> Health | None:
    """How the seat on `port` is, or None if it can't be reached. The seat only answers
    its current visitor, so the manager sends the seat's cookie (any prefix before the
    "." will do) and, for the activity, the token header."""
    base = f"http://127.0.0.1:{port}"
    try:
        health = client.get(
            f"{base}/health", headers={"Cookie": f"{COOKIE}=0.{token}"}, timeout=_CHECK_TIMEOUT
        )
        activity = client.get(
            f"{base}/demo/activity", headers={"X-Seat-Token": token}, timeout=_CHECK_TIMEOUT
        )
    except httpx.HTTPError:
        return None
    ok = health.status_code == 200 and activity.status_code == 200
    try:
        status = health.json().get("status") if ok else None
        body = activity.json() if ok else {}
        return Health(
            ok=ok and status == "ok",
            idle_seconds=float(body.get("idle_seconds", 0.0)),
            tour_furthest=int(body.get("tour_furthest", 0)),
        )
    except ValueError, AttributeError, TypeError:
        return Health(ok=False, idle_seconds=0.0, tour_furthest=0)
