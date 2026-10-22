"""`python -m optionsjourney.demo`: a fresh data folder each day, restarted on a new day."""

import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

from optionsjourney.demo import __main__ as runner
from optionsjourney.demo.marker import DB_NAME, DEMO_MARK


def _stub(codes, seen):
    """A child that records what it was started with, leaves a file behind and exits."""

    def launch(env):
        folder = runner.Path(env["OPTIONSJOURNEY_DATA_DIR"])
        seen.append((env, sorted(p.name for p in folder.iterdir())))
        (folder / "left-behind.txt").write_text("from the day before")
        return codes.pop(0)

    return launch


def test_runner_restarts_on_new_day(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.tempfile, "tempdir", str(tmp_path))
    seen = []
    code = runner.run(port=5099, launch=_stub([runner.NEW_DAY, 0], seen))
    assert code == 0
    assert len(seen) == 2
    (first, first_files), (second, second_files) = seen
    assert first_files == [] and second_files == []  # each start is a fresh folder
    for env, _ in seen:
        assert env["BROKER_CREDENTIALS"] == "demo" and env["PORT"] == "5099"
    assert first["OPTIONSJOURNEY_DATA_DIR"] != second["OPTIONSJOURNEY_DATA_DIR"]
    assert list(tmp_path.iterdir()) == []  # temporary folders are removed


def test_runner_stops_with_the_childs_code(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.tempfile, "tempdir", str(tmp_path))
    seen = []
    assert runner.run(launch=_stub([3], seen)) == 3
    assert len(seen) == 1


def _demo_db(folder):
    connection = sqlite3.connect(folder / DB_NAME)
    connection.execute("CREATE TABLE settings (key TEXT, value TEXT)")
    connection.execute("INSERT INTO settings VALUES (?, '1')", (DEMO_MARK,))
    connection.commit()
    connection.close()


def test_a_chosen_folder_is_emptied_each_day_when_it_is_the_demos(tmp_path):
    folder = tmp_path / "demo-data"
    folder.mkdir()
    _demo_db(folder)
    (folder / "logs").mkdir()
    seen = []
    assert runner.run(data_dir=folder, launch=_stub([runner.NEW_DAY, 0], seen)) == 0
    assert [files for _, files in seen] == [[], []]
    assert seen[0][0]["OPTIONSJOURNEY_DATA_DIR"] == str(folder)


def test_a_chosen_folder_that_is_not_the_demos_is_left_alone(tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()
    (folder / "optionsjourney.db").write_bytes(b"not a demo database")
    with pytest.raises(SystemExit):
        runner.run(data_dir=folder, launch=lambda env: pytest.fail("started"))
    assert (folder / "optionsjourney.db").read_bytes() == b"not a demo database"


def test_a_missing_chosen_folder_is_made(tmp_path):
    folder = tmp_path / "new" / "demo"
    seen = []
    assert runner.run(data_dir=folder, launch=_stub([0], seen)) == 0
    assert folder.is_dir()


def test_the_command_line(monkeypatch, tmp_path):
    got = {}
    monkeypatch.setattr(runner, "run", lambda **kw: got.update(kw) or 0)
    handlers = []
    monkeypatch.setattr(runner.signal, "signal", lambda *args: handlers.append(args))
    with pytest.raises(SystemExit) as stopped:
        runner.main(["--port", "5099", "--data-dir", str(tmp_path)])
    assert stopped.value.code == 0
    assert got == {"port": 5099, "data_dir": tmp_path}
    assert [sig for sig, _ in handlers] == [runner.signal.SIGTERM]  # stops cleanly


_RUNNER = textwrap.dedent("""
    import sys
    from optionsjourney.demo import __main__ as runner
    child = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(300)"
    def launch(env):
        return runner._run_child([sys.executable, "-c", child, sys.argv[1]], env)
    runner._stop_on_sigterm()
    sys.exit(runner.run(launch=launch))
""")


def _gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_sigterm_stops_the_child(tmp_path):
    pidfile = tmp_path / "child.pid"
    env = dict(os.environ, TMPDIR=str(tmp_path))
    runner_proc = subprocess.Popen([sys.executable, "-c", _RUNNER, str(pidfile)], env=env)
    try:
        deadline = time.monotonic() + 20
        while not (pidfile.exists() and pidfile.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        child = int(pidfile.read_text())
        runner_proc.send_signal(signal.SIGTERM)
        assert runner_proc.wait(timeout=12) == 128 + signal.SIGTERM
        assert _gone(child)
        assert [p for p in tmp_path.iterdir() if p.name.startswith("optionsjourney-demo-")] == []
    finally:
        runner_proc.kill()


def test_a_demo_day_reads_no_env_file_and_stays_on_this_computer(tmp_path, monkeypatch):
    monkeypatch.setenv("LISTEN_ON_NETWORK", "yes")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    seen = []
    runner.run_once(tmp_path, 5099, lambda env: seen.append(env) or 0)
    (env,) = seen
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["LISTEN_ON_NETWORK"] == "no"
    assert env["BROKER_CREDENTIALS"] == "demo" and env["PORT"] == "5099"


def test_the_runner_and_the_seats_share_one_demo_environment():
    import inspect  # noqa: PLC0415

    from optionsjourney.demo import seat_process  # noqa: PLC0415

    for module in (runner, seat_process):
        source = inspect.getsource(module)
        assert "demo_env(" in source
        assert 'BROKER_CREDENTIALS="demo"' not in source and '"BROKER_CREDENTIALS"' not in source
