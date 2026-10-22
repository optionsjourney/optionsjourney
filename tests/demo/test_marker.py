"""Telling a demo database apart before anything is opened or written (final review M-2,
M-10)."""

import logging
import subprocess
import sys

import pytest

from optionsjourney.db import make_engine, migrate
from optionsjourney.demo import marker, seed


def test_a_local_start_reads_the_mark_without_the_demo_seed(tmp_path):
    code = (
        "import sys\n"
        "from optionsjourney.config import load_settings\n"
        "from optionsjourney import app\n"
        f"s = load_settings({{'OPTIONSJOURNEY_DATA_DIR': {str(tmp_path)!r}}})\n"
        "assert not s.is_demo\n"
        "app._refuse_demo_data(s)\n"
        "print('optionsjourney.demo.seed' in sys.modules)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         check=True)  # fmt: skip
    assert out.stdout.strip() == "False"


def test_the_mark(tmp_path):
    assert marker.DB_NAME == "optionsjourney.db"
    assert not marker.is_demo_database(tmp_path / marker.DB_NAME)  # none there
    seed.build(tmp_path)
    assert marker.is_demo_database(tmp_path / marker.DB_NAME)


def _files(folder) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def _main_refuses(entry, monkeypatch, settings, match):
    monkeypatch.setattr(entry, "load_settings", lambda: settings)
    monkeypatch.setattr(entry.logs, "start_console", lambda: None)
    handlers = list(logging.getLogger().handlers)
    try:
        with pytest.raises(SystemExit, match=match):
            entry.main()
    finally:
        for h in logging.getLogger().handlers:
            if h not in handlers:  # a log file the refusal came too late to stop
                logging.getLogger().removeHandler(h)
                h.close()


def test_a_refused_real_folder_gets_no_log_file(demo_settings, monkeypatch):
    from optionsjourney import __main__ as entry  # noqa: PLC0415

    engine = make_engine(demo_settings.database_path)
    migrate(engine)
    engine.dispose()
    before = _files(demo_settings.data_dir)
    _main_refuses(entry, monkeypatch, demo_settings, "Settings problem: .*isn't a demo database")
    assert _files(demo_settings.data_dir) == before


def test_a_refused_demo_folder_gets_no_log_file(settings, monkeypatch):
    from optionsjourney import __main__ as entry  # noqa: PLC0415

    seed.build(settings.data_dir)
    before = _files(settings.data_dir)
    _main_refuses(entry, monkeypatch, settings, "Settings problem: .*demo database")
    assert _files(settings.data_dir) == before
