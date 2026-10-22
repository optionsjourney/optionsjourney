"""Real seat processes: started, checked, stopped and cleared. Each test runs a real
app (about 3 s to start), so these are the slow ones in tests/demo."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from optionsjourney import config
from optionsjourney.demo import seat_process
from optionsjourney.demo.seat_process import SeatProcesses, check

TOKEN = "tok123"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_ok(port: int, client: httpx.Client, seconds: float = 15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        health = check(port, TOKEN, client)
        if health is not None and health.ok:
            return health
        time.sleep(0.3)
    return None


@pytest.fixture
def seats(tmp_path):
    pool = SeatProcesses(tmp_path)
    yield pool
    pool.stop_all()


def test_a_seat_starts_and_answers(seats):
    port = free_port()
    seats.start(1, port, TOKEN)
    with httpx.Client() as client:
        health = wait_ok(port, client)
        assert health is not None
        assert health.idle_seconds >= 0
        page = client.get(
            f"http://127.0.0.1:{port}/journal", headers={"Cookie": f"oj_seat=1.{TOKEN}"}
        )
        assert page.status_code == 200


def test_stop_leaves_no_seat_processes(seats, tmp_path):
    port = free_port()
    seats.start(1, port, TOKEN)
    pid = int((tmp_path / "seats/1/pid").read_text().split()[0])
    with httpx.Client() as client:
        assert wait_ok(port, client) is not None
        seats.stop(1)
        assert not alive(pid)
        assert not (tmp_path / "seats/1/pid").exists()
        assert check(port, TOKEN, client) is None


def test_stale_seats_are_cleared_at_start(tmp_path):
    first = SeatProcesses(tmp_path)
    second = SeatProcesses(tmp_path)
    try:
        first.start(1, free_port(), TOKEN)
        pid = int((tmp_path / "seats/1/pid").read_text().split()[0])
        time.sleep(1)  # let it exec so its command line is the app's
        assert alive(pid)
        second.clear_stale()
        time.sleep(0.5)
        first.exited()  # reaps the killed child
        assert not alive(pid)
        assert not (tmp_path / "seats/1/pid").exists()
    finally:
        first.stop_all()


def test_clear_stale_leaves_other_processes_alone(tmp_path):
    pool = SeatProcesses(tmp_path, command=("-c", "import time; time.sleep(60)"))
    try:
        pool.start(1, free_port(), TOKEN)
        pid = int((tmp_path / "seats/1/pid").read_text().split()[0])
        SeatProcesses(tmp_path).clear_stale()
        assert alive(pid)  # not an optionsjourney process
        assert not (tmp_path / "seats/1/pid").exists()
    finally:
        pool.stop_all()


def test_a_crashed_seat_is_reset(tmp_path):
    pool = SeatProcesses(tmp_path, command=("-c", "import sys; sys.exit(3)"))
    pool.start(2, free_port(), TOKEN)
    deadline = time.monotonic() + 10
    found: list = []
    while time.monotonic() < deadline and not found:
        found = pool.exited()
        time.sleep(0.1)
    assert found == [(2, 3)]
    assert pool.exited() == []


def test_a_seat_reads_no_env_file(tmp_path):
    """config.py looks for a .env upward from its own folder, so a copy of it sits under
    a folder with a hostile .env (the repo root can't be written to in a test)."""
    project = tmp_path / "project"
    (project / "pkg").mkdir(parents=True)
    shutil.copy(config.__file__, project / "pkg" / "config.py")
    (project / ".env").write_text(
        "BROKER_CREDENTIALS=gateway\nLISTEN_ON_NETWORK=yes\n"
        "GATEWAY_URL=http://example.invalid\nGATEWAY_SECRET=hunter2\n"
    )
    script = (
        "import importlib.util as u, json, os\n"
        f"spec = u.spec_from_file_location('cfg', {str(project / 'pkg' / 'config.py')!r})\n"
        "cfg = u.module_from_spec(spec); spec.loader.exec_module(cfg)\n"
        "s = cfg.load_settings()\n"
        "print(json.dumps([s.bind_host, s.broker_credentials, s.gateway_url, s.gateway_secret]))\n"
    )
    runner = project / "show.py"  # a file, not -c: dotenv searches from the cwd when interactive
    runner.write_text(script)
    clean = {
        k: v for k, v in os.environ.items() if not k.startswith(("GATEWAY", "LISTEN", "BROKER"))
    }
    clean.pop("PYTHON_DOTENV_DISABLED", None)
    leaky = subprocess.run(
        [sys.executable, str(runner)], env=clean, capture_output=True, text=True, check=True
    )
    assert json.loads(leaky.stdout)[0] == "0.0.0.0"  # the test can fail: .env does reach it

    pool = SeatProcesses(tmp_path / "root", command=(str(runner),))
    port = free_port()
    pool.start(1, port, TOKEN)
    child_pid = int((tmp_path / "root/seats/1/pid").read_text().split()[0])
    deadline = time.monotonic() + 10
    log = tmp_path / "root/seats/1/seat.log"
    while time.monotonic() < deadline and not (log.exists() and log.read_text().strip()):
        time.sleep(0.1)
    assert json.loads(log.read_text()) == ["127.0.0.1", "demo", None, None]
    pool.stop_all()
    assert not alive(child_pid)


def test_check_is_none_when_nothing_listens():
    with httpx.Client() as client:
        assert check(free_port(), TOKEN, client) is None


def test_python_is_this_one():
    assert SeatProcesses(Path("/nonexistent")).python == sys.executable


def _lookalike(tmp_path, folder, cwd):
    """A long-running python whose command line ends like a seat's, in its own group."""
    folder.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "-m", "optionsjourney"],
        cwd=cwd,
        start_new_session=True,
    )
    time.sleep(0.5)
    return child


def test_clear_stale_spares_a_lookalike_in_another_folder(tmp_path):
    folder = tmp_path / "seats/1"
    child = _lookalike(tmp_path, folder, tmp_path / "elsewhere")
    try:
        (folder / "pid").write_text(f"{child.pid}\n{seat_process._started(child.pid)}\n")
        SeatProcesses(tmp_path).clear_stale()
        assert child.poll() is None
        assert not (folder / "pid").exists()
    finally:
        child.kill()
        child.wait()


def test_clear_stale_spares_a_process_with_another_start_time(tmp_path):
    folder = tmp_path / "seats/1"
    child = _lookalike(tmp_path, folder, folder)
    try:
        (folder / "pid").write_text(f"{child.pid}\nMon Jan  1 00:00:00 2001\n")
        SeatProcesses(tmp_path).clear_stale()
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_clear_stale_kills_a_matching_leftover(tmp_path):
    folder = tmp_path / "seats/1"
    child = _lookalike(tmp_path, folder, folder)
    try:
        (folder / "pid").write_text(f"{child.pid}\n{seat_process._started(child.pid)}\n")
        SeatProcesses(tmp_path).clear_stale()
        assert child.wait(timeout=5) is not None
    finally:
        child.kill()
        child.wait()


def test_a_folder_that_is_not_the_demos_is_refused_not_fatal(tmp_path):
    from optionsjourney.demo import marker  # noqa: PLC0415

    (tmp_path / "x.txt").write_text("mine")
    with pytest.raises(marker.FolderRefused):
        marker.empty_folder(tmp_path)


def test_the_seat_log_is_rotated_at_each_start(tmp_path):
    pool = SeatProcesses(tmp_path, command=("-c", "print('run')"))
    folder = tmp_path / "seats/1"
    for _ in range(3):
        pool.start(1, free_port(), TOKEN)
        time.sleep(0.5)
        pool.exited()
    assert (folder / "seat.log").read_text() == "run\n"
    assert (folder / "seat.log.1").read_text() == "run\n"
    assert not (folder / "seat.log.2").exists()


# ----- ps and lsof: absolute paths, a fixed locale, and failing safe -----


def test_ps_and_lsof_are_called_by_absolute_path():
    assert seat_process._PS == "/bin/ps"
    assert seat_process._LSOF == "/usr/sbin/lsof"


def test_ps_runs_in_the_c_locale(tmp_path, monkeypatch):
    fake = tmp_path / "ps"
    fake.write_text('#!/bin/sh\necho "locale=$LC_ALL"\n')
    fake.chmod(0o755)
    monkeypatch.setattr(seat_process, "_PS", str(fake))
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    assert seat_process._ps(os.getpid(), "lstart") == "locale=C"


def test_a_missing_ps_or_lsof_fails_safe(monkeypatch):
    monkeypatch.setattr(seat_process, "_PS", "/nonexistent/ps")
    monkeypatch.setattr(seat_process, "_LSOF", "/nonexistent/lsof")
    assert seat_process._ps(os.getpid(), "command") == ""
    assert seat_process._started(os.getpid()) == ""
    assert seat_process._cwd(os.getpid()) is None


def test_clear_stale_kills_nothing_without_ps(tmp_path, monkeypatch):
    folder = tmp_path / "seats/1"
    child = _lookalike(tmp_path, folder, folder)
    try:
        (folder / "pid").write_text(f"{child.pid}\n{seat_process._started(child.pid)}\n")
        monkeypatch.setattr(seat_process, "_PS", "/nonexistent/ps")
        SeatProcesses(tmp_path).clear_stale()  # no crash: the manager still starts
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_clear_stale_kills_nothing_without_lsof(tmp_path, monkeypatch):
    folder = tmp_path / "seats/1"
    child = _lookalike(tmp_path, folder, folder)
    try:
        (folder / "pid").write_text(f"{child.pid}\n{seat_process._started(child.pid)}\n")
        monkeypatch.setattr(seat_process, "_LSOF", "/nonexistent/lsof")
        SeatProcesses(tmp_path).clear_stale()
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


# ----- stopping: a retry really retries; stop_all waits once for everyone -----


class _Stubborn:
    """A child that never goes away: every wait times out."""

    def __init__(self) -> None:
        self.pid = 999_999
        self.waits: list[float | None] = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        raise subprocess.TimeoutExpired("seat", timeout)

    def poll(self):
        return None


def test_a_seat_that_outlives_sigkill_stays_tracked_so_stop_retries(tmp_path, monkeypatch, caplog):
    sent = []
    monkeypatch.setattr(seat_process, "_kill_group", lambda pid, sig: sent.append(sig))
    pool = SeatProcesses(tmp_path)
    child = _Stubborn()
    pool._children[1] = child
    with pytest.raises(seat_process.StopFailed):
        pool.stop(1, timeout=0.01)
    assert child.waits == [0.01, seat_process.KILL_WAIT]  # the wait after SIGKILL is bounded
    assert "still there" in caplog.text
    assert pool._children[1] is child  # not forgotten: the retry has something to stop
    with pytest.raises(seat_process.StopFailed):
        pool.stop(1, timeout=0.01)
    assert sent.count(9) == 2


def test_stop_all_stops_everyone_together(tmp_path):
    stubborn = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    pool = SeatProcesses(tmp_path, command=("-c", stubborn))
    for n in (1, 2, 3):
        pool.start(n, free_port(), TOKEN)
    pids = [int((tmp_path / f"seats/{n}/pid").read_text().split()[0]) for n in (1, 2, 3)]
    try:
        time.sleep(0.5)
        began = time.monotonic()
        pool.stop_all(timeout=1)
        took = time.monotonic() - began
        assert took < 2.5  # one wait for all three, not one each
        assert not any(alive(pid) for pid in pids)
        assert pool._children == {}
    finally:
        for pid in pids:
            if alive(pid):
                os.killpg(pid, 9)


def test_a_seat_folder_left_half_made_is_still_the_managers(tmp_path):
    data = tmp_path / "seats/1/data"
    data.mkdir(parents=True)
    (data / "optionsjourney.db-journal").write_text("crashed before the demo mark")
    pool = SeatProcesses(tmp_path, command=("-c", "pass"))
    pool.start(1, free_port(), TOKEN)  # no FolderRefused
    assert not (data / "optionsjourney.db-journal").exists()
    pool.stop_all()
