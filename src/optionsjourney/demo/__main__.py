"""Run the demo: `python -m optionsjourney.demo [--port N] [--data-dir PATH]`.

Each demo day runs the app in a child process with BROKER_CREDENTIALS=demo, on a
fresh data folder. Start a new day (the banner's button) makes the app exit with
NEW_DAY, and the day starts again on a fresh folder; any other exit stops the demo
with that code. Nothing is kept from one run to the next.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from optionsjourney.demo.marker import FolderRefused, demo_env, empty_folder

NEW_DAY = 75  # the app's exit code for Start a new day (EX_TEMPFAIL: run me again)

Launch = Callable[[Mapping[str, str]], int]


STOP_WAIT = 10  # seconds a stopped child gets to finish before it is killed


def _run_child(args: Sequence[str], env: Mapping[str, str]) -> int:
    """`args` as a child process; its exit code. If this process is stopped meanwhile
    (SIGTERM becomes SystemExit, Ctrl-C KeyboardInterrupt), the child is sent SIGTERM,
    given STOP_WAIT seconds, then killed, so none is left running."""
    child = subprocess.Popen(list(args), env=dict(env))
    try:
        return child.wait()
    except BaseException as stopped:
        child.terminate()
        try:
            child.wait(timeout=STOP_WAIT)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        if isinstance(stopped, KeyboardInterrupt):
            return 130
        raise


def _launch(env: Mapping[str, str]) -> int:
    """The app in a child process; its exit code."""
    return _run_child([sys.executable, "-m", "optionsjourney"], env)


def _stop_on_sigterm() -> None:
    """Stopped from outside: stop the app too and remove the day's temporary folder."""
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(128 + signal.SIGTERM))


def run_once(data_dir: Path, port: int | None = None, launch: Launch = _launch) -> int:
    """One demo day in `data_dir`: the app's exit code."""
    return launch(demo_env(os.environ, data_dir, port))


def run(port: int | None = None, data_dir: Path | None = None, launch: Launch = _launch) -> int:
    """Demo days until one ends with anything but NEW_DAY; that day's exit code."""
    ours = False
    while True:
        if data_dir is None:
            folder = Path(tempfile.mkdtemp(prefix="optionsjourney-demo-"))
            try:
                code = run_once(folder, port, launch)
            finally:
                shutil.rmtree(folder, ignore_errors=True)
        else:
            try:
                empty_folder(data_dir, ours=ours)
            except FolderRefused as refused:
                sys.exit(str(refused))
            ours = True
            code = run_once(data_dir, port, launch)
        if code != NEW_DAY:
            return code
        print("Starting a new demo day…", flush=True)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m optionsjourney.demo",
        description="Run OptionsJourney's demo: made-up prices, nothing here is real money.",
    )
    parser.add_argument("--port", type=int, help="the port to serve on (default: the app's)")
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="a folder for the demo's data (default: a temporary one). It is emptied each day.",
    )
    args = parser.parse_args(argv)
    _stop_on_sigterm()
    sys.exit(run(port=args.port, data_dir=args.data_dir))


if __name__ == "__main__":
    main()
