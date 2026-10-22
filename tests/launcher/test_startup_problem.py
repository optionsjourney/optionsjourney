from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from werkzeug.test import Client

from optionsjourney import __version__
from optionsjourney.app import NewerDataError, UpgradeBackupError
from optionsjourney.config import Settings
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import Tag
from optionsjourney.launcher import startup_problem
from optionsjourney.services import backups

POINT = backups.Backup(
    "optionsjourney-2026-10-21-1530-before-0.3.0-from-0.2.0.db.gz",
    datetime(2026, 10, 21, 19, 30, tzinfo=UTC),
    1234,
)
GUIDE = (
    "https://github.com/optionsjourney/optionsjourney/blob/main/docs/guide/updating.md#going-back"
)
RELEASES = "https://github.com/optionsjourney/optionsjourney/releases"


SAME_ORIGIN = {"Origin": "http://localhost"}  # werkzeug's test client host


def _client(kind, detail="", restore=None, on_restore=lambda: None):
    return Client(startup_problem.problem_app(kind, detail, restore, on_restore))


def test_newer_data_page_offers_the_backup_from_before_the_upgrade():
    page = _client("newer", restore=POINT).get("/").get_data(as_text=True)
    assert "This data is from a newer version of OptionsJourney" in page
    assert "10/21/26 15:30 ET" in page  # as Settings → Backups lists it
    assert "Changes made after the upgrade (tags, notes, bot edits) are lost." in page
    assert GUIDE in page and RELEASES in page
    assert '<form method="post" action="/restore">' in page
    assert "Restore the backup from before the upgrade" in page


def test_newer_data_page_without_a_backup_has_no_button():
    page = _client("newer").get("/").get_data(as_text=True)
    assert "This data is from a newer version of OptionsJourney" in page
    assert "No backup from before the upgrade was found" in page
    assert "<form" not in page
    assert RELEASES in page


def test_restore_runs_once_and_says_so():
    calls = []
    client = _client("newer", restore=POINT, on_restore=lambda: calls.append(1))
    first = client.post("/restore", headers=SAME_ORIGIN)
    assert first.status_code == 200
    assert "Restoring…" in first.get_data(as_text=True)
    client.post("/restore", headers=SAME_ORIGIN)  # a second click doesn't restore again
    assert calls == [1]


def test_a_restore_that_fails_says_why():
    def broken():
        raise backups.BackupError("The backup couldn't be restored: disk full")

    response = _client("newer", restore=POINT, on_restore=broken).post(
        "/restore", headers=SAME_ORIGIN
    )
    assert response.status_code == 500
    assert "disk full" in response.get_data(as_text=True)


@pytest.mark.parametrize(("kind", "restore"), [("newer", None), ("backup", None), ("other", None)])
def test_restore_is_only_there_when_there_is_something_to_restore(kind, restore):
    calls = []
    client = _client(kind, "x", restore=restore, on_restore=lambda: calls.append(1))
    assert client.post("/restore", headers=SAME_ORIGIN).status_code == 404
    assert calls == []


def test_backup_page_says_the_upgrade_didnt_run_and_why():
    page = _client("backup", "the folder /Volumes/Gone can't be used").get("/")
    text = page.get_data(as_text=True)
    assert "OptionsJourney didn't upgrade your data" in text
    assert (
        "that backup couldn't be made in your backup folder or in Documents/OptionsJourney "
        "Backups: the folder /Volumes/Gone can&#39;t be used." in text
    )
    assert "Your data hasn't been changed." in text
    assert "free space on this computer" in text
    assert "Settings" not in text  # the app didn't start: Settings can't be reached
    assert "Open logs" in text and "Restart OptionsJourney" in text
    assert "<form" not in text


def test_other_problem_page_points_to_the_logs():
    text = _client("other", "boom <script>").get("/").get_data(as_text=True)
    assert "OptionsJourney couldn't start" in text
    assert "Open logs" in text and "Restart OptionsJourney" in text
    assert "hello@optionsjourney.com" in text and "optionsjourney.log" in text
    assert "boom &lt;script&gt;" in text  # escaped
    assert "<form" not in text


def test_each_start_up_error_has_its_kind():
    assert startup_problem.kind_of(NewerDataError("x")) == "newer"
    assert startup_problem.kind_of(UpgradeBackupError("x")) == "backup"
    assert startup_problem.kind_of(RuntimeError("x")) == "other"


@pytest.fixture
def newer_data(tmp_path):
    """Data a newer version upgraded, with the backup it made first."""
    settings = Settings(broker_credentials="local", data_dir=tmp_path)
    engine = make_engine(settings.database_path)
    migrate(engine)
    factory = make_session_factory(engine)
    now = datetime(2026, 10, 21, 19, 30, tzinfo=UTC)
    backups.backup_before_upgrade(settings.database_path, factory, __version__, "99.0.0", now)
    with factory() as session:
        session.add(Tag(name="Made by the newer version"))
        session.commit()
    engine.dispose()
    return settings


def _tag_names(settings):
    engine = make_engine(settings.database_path)
    with make_session_factory(engine)() as session:
        names = [tag.name for tag in session.query(Tag)]
    engine.dispose()
    return names


def test_the_restore_point_is_found_without_upgrading_the_data(newer_data):
    point = startup_problem.find_restore_point(newer_data)
    assert point is not None
    assert point.name.endswith(f"-before-99.0.0-from-{__version__}.db.gz")


def test_restoring_puts_the_old_data_back_then_restarts(newer_data, monkeypatch):
    events = []
    monkeypatch.setattr(startup_problem.places, "start_again", lambda *a: events.append("start"))
    on_restore = startup_problem.restore_and_restart(
        newer_data, stop=lambda: events.append("stop"), later=lambda fn: fn()
    )
    on_restore()
    assert "Made by the newer version" not in _tag_names(newer_data)
    assert events == ["stop", "start"]  # the port is free before the new copy starts


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (NewerDataError("newer"), "newer"),
        (UpgradeBackupError("disk full"), "backup"),
        (RuntimeError("boom"), "other"),
    ],
)
def test_a_failed_start_shows_the_problem_icon(error, kind, settings, monkeypatch):
    from optionsjourney import app as web_app
    from optionsjourney.launcher import app as launcher

    shown = []
    monkeypatch.setattr(launcher.logs, "start_console", lambda: None)
    monkeypatch.setattr(launcher.logs, "start_file", lambda data_dir: None)
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)

    def fail(settings):
        raise error

    monkeypatch.setattr(web_app, "create_app", fail)
    monkeypatch.setattr(startup_problem, "run_problem_icon", lambda *a: shown.append(a))
    launcher.main()
    assert shown == [(settings, kind, str(error))]


def test_stopping_by_hand_isnt_a_start_up_problem(settings, monkeypatch):
    from optionsjourney import app as web_app
    from optionsjourney.launcher import app as launcher

    monkeypatch.setattr(launcher.logs, "start_console", lambda: None)
    monkeypatch.setattr(launcher.logs, "start_file", lambda data_dir: None)
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)

    def interrupted(settings):
        raise KeyboardInterrupt

    monkeypatch.setattr(web_app, "create_app", interrupted)
    monkeypatch.setattr(startup_problem, "run_problem_icon", lambda *a: pytest.fail("shown"))
    with pytest.raises(KeyboardInterrupt):
        launcher.main()


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://evil.example"}, {"Referer": "https://evil.example/page"}],
)
def test_restore_from_another_website_is_refused(headers):
    calls = []
    client = _client("newer", restore=POINT, on_restore=lambda: calls.append(1))
    assert client.post("/restore", headers=headers).status_code == 403
    assert calls == []
    assert client.post("/restore", headers={"Referer": "http://localhost/"}).status_code == 200
    assert calls == [1]


def test_a_backup_folder_that_cant_be_read_means_no_restore_point(settings, monkeypatch):
    def unreadable(session_factory, running):
        raise OSError("the drive went away")

    monkeypatch.setattr(startup_problem.backups, "restore_point", unreadable)
    assert startup_problem.find_restore_point(settings) is None


class _FakeWebApp:
    def __init__(self, session_factory):
        self.config = {}
        self.extensions = {"optionsjourney": SimpleNamespace(session_factory=session_factory)}


class _FakeServer:
    def __init__(self, events):
        self.events = events
        self.stopped = __import__("threading").Event()

    def serve_forever(self):
        self.stopped.wait(5)

    def shutdown(self):
        self.events.append("shutdown")
        self.stopped.set()

    def server_close(self):
        self.events.append("server_close")


@pytest.fixture
def menu_start(settings, monkeypatch):
    """main() up to the menu: no log files, a fake web app, the problem icon recorded."""
    from optionsjourney import app as web_app
    from optionsjourney.launcher import app as launcher

    events = []
    engine = make_engine(settings.database_path)
    migrate(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(launcher.logs, "start_console", lambda: None)
    monkeypatch.setattr(launcher.logs, "start_file", lambda data_dir: None)
    monkeypatch.setattr(launcher, "load_settings", lambda: settings)
    monkeypatch.setattr(web_app, "create_app", lambda settings: _FakeWebApp(factory))
    monkeypatch.setattr(
        startup_problem, "run_problem_icon", lambda s, kind, detail: events.append((kind, detail))
    )
    return launcher, events


def test_a_port_in_use_shows_the_problem_icon(menu_start, monkeypatch):
    launcher, events = menu_start

    def taken(*a, **k):
        raise OSError(48, "Address already in use")

    monkeypatch.setattr(launcher, "make_server", taken)
    launcher.main()
    assert events == [("other", "[Errno 48] Address already in use")]


def test_a_failed_background_start_frees_the_port_before_the_problem_icon(menu_start, monkeypatch):
    from optionsjourney.services import background

    launcher, events = menu_start
    monkeypatch.setattr(launcher, "make_server", lambda *a, **k: _FakeServer(events))

    def boom(database_path, services):
        raise RuntimeError("no threads")

    monkeypatch.setattr(background, "start_all", boom)
    launcher.main()
    assert events == ["shutdown", "server_close", ("other", "no threads")]


def test_an_import_that_fails_shows_the_problem_icon(menu_start, monkeypatch):
    from optionsjourney import app as web_app

    launcher, events = menu_start
    monkeypatch.delattr(web_app, "create_app")
    launcher.main()
    assert len(events) == 1 and events[0][0] == "other"


def test_without_the_port_the_problem_icon_still_shows_and_the_log_says_why(
    settings, monkeypatch, caplog
):
    import sys
    import types

    shown, opened = [], []

    class Icon:
        def __init__(self, name, image, title, menu):
            self.title, self.menu = title, menu

        def run(self, setup):
            shown.append(self.title)
            setup(self)

    fake = types.SimpleNamespace(
        Icon=Icon, Menu=lambda *items: list(items), MenuItem=lambda *a, **k: a[0]
    )
    monkeypatch.setitem(sys.modules, "pystray", fake)

    def taken(*a, **k):
        raise OSError(48, "Address already in use")

    monkeypatch.setattr(startup_problem, "make_server", taken)
    monkeypatch.setattr(startup_problem.webbrowser, "open", opened.append)
    startup_problem.run_problem_icon(settings, "other", "boom")
    assert shown == ["OptionsJourney · couldn't start"]
    assert opened == []
    assert f"port {settings.port} is in use" in caplog.text


def test_the_restart_after_a_restore_outlives_the_old_process():
    # The restore button runs in a web-server thread, which is a daemon; a timer it
    # starts would be one too, and Python ends daemon threads at exit, so the old
    # process quit before starting the new copy (found on a fresh Mac, v0.2.1 → v0.2.0)
    import threading

    made = []

    def from_a_request():
        startup_problem.in_a_moment(lambda: None, made.append)

    request = threading.Thread(target=from_a_request, daemon=True)
    request.start()
    request.join()
    assert made and made[0].daemon is False
