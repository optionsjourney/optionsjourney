"""The daily update check and the release download. Never touches the network."""

import io
import json
import logging
import zipfile

import httpx
import pytest

from optionsjourney import __version__
from optionsjourney.core.settings import SECTIONS
from optionsjourney.services import updates
from optionsjourney.services.updates import Release, UpdateError

API = "https://api.github.com/repos/o/r/zipball/v0.2.1"
CODELOAD = "https://codeload.github.com/o/r/legacy.zip/refs/tags/v0.2.1"
TOP = "optionsjourney-optionsjourney-abc123/"


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv("OJ_UPDATE_URL", raising=False)


def make_zip(entries=None) -> bytes:
    if entries is None:
        entries = {
            TOP + "scripts/install-mac.sh": "#!/bin/bash\n",
            TOP + "scripts/install-windows.ps1": "x",
            TOP + "README.md": "hi",
        }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in entries.items():
            z.writestr(name, body)
    return buf.getvalue()


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def release(tag="v0.2.1", zip_url=API) -> Release:
    return Release(tag=tag, page_url="https://github.com/o/r/releases/tag/" + tag, zip_url=zip_url)


def release_json(tag="v0.2.1"):
    return {
        "tag_name": tag,
        "html_url": "https://github.com/o/r/releases/tag/" + tag,
        "zipball_url": API,
    }


# --- latest ---


def test_latest_reads_the_release():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, json=release_json())

    got = updates.latest(client(handler), "0.2.0")
    assert got == release()
    assert seen["ua"] == f"optionsjourney/{__version__}"


@pytest.mark.parametrize("running", ["0.2.1", "0.3.0"])
def test_latest_none_when_not_newer(running):
    assert (
        updates.latest(client(lambda r: httpx.Response(200, json=release_json())), running) is None
    )


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_latest_none_on_http_errors_and_logs_once(status, caplog):
    with caplog.at_level(logging.WARNING, logger=updates.logger.name):
        assert updates.latest(client(lambda r: httpx.Response(status)), "0.1.0") is None
    assert len(caplog.records) == 1


def test_latest_none_on_timeout(caplog):
    def handler(request):
        raise httpx.ConnectTimeout("slow")

    with caplog.at_level(logging.WARNING, logger=updates.logger.name):
        assert updates.latest(client(handler), "0.1.0") is None
    assert len(caplog.records) == 1


@pytest.mark.parametrize("body", [b"not json", b"[1]", b'{"tag_name": "v9.0.0"}'])
def test_latest_none_on_broken_json(body, caplog):
    with caplog.at_level(logging.WARNING, logger=updates.logger.name):
        assert updates.latest(client(lambda r: httpx.Response(200, content=body)), "0.1.0") is None
    assert len(caplog.records) == 1


def test_latest_ignores_odd_tags():
    got = updates.latest(
        client(lambda r: httpx.Response(200, json=release_json("v1.0.0-beta"))), "0.1.0"
    )
    assert got is None


def test_source_url_default_and_override(monkeypatch):
    assert (
        updates.source_url()
        == "https://api.github.com/repos/optionsjourney/optionsjourney/releases/latest"
    )
    monkeypatch.setenv("OJ_UPDATE_URL", "http://127.0.0.1:8765/")
    assert updates.source_url() == "http://127.0.0.1:8765/latest.json"


def test_latest_from_override_uses_zip_url(monkeypatch):
    monkeypatch.setenv("OJ_UPDATE_URL", "http://127.0.0.1:8765")
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "tag_name": "v0.2.1",
                "html_url": "http://127.0.0.1:8765/p",
                "zip_url": "http://127.0.0.1:8765/z.zip",
            },
        )

    got = updates.latest(client(handler), "0.2.0")
    assert seen == ["http://127.0.0.1:8765/latest.json"]
    assert got == Release("v0.2.1", "http://127.0.0.1:8765/p", "http://127.0.0.1:8765/z.zip")


# --- download ---


def zip_handler(data):
    def handler(request):
        return httpx.Response(200, content=data)

    return handler


def test_download_unpacks_and_writes_ready(tmp_path):
    folder = updates.download(release(), tmp_path, client(zip_handler(make_zip())))
    assert folder == tmp_path / "v0.2.1" / TOP.rstrip("/")
    assert (folder / "scripts" / "install-mac.sh").is_file()
    assert (tmp_path / "v0.2.1" / "ready.json").is_file()
    assert not list(tmp_path.glob("*.partial"))
    assert updates.ready(tmp_path) == (release(), folder)


@pytest.mark.parametrize(
    "entries",
    [
        {
            TOP + "scripts/install-mac.sh": "x",
            TOP + "scripts/install-windows.ps1": "x",
            "../evil": "x",
        },
        {
            TOP + "scripts/install-mac.sh": "x",
            TOP + "scripts/install-windows.ps1": "x",
            "/abs/evil": "x",
        },
        {TOP + "README.md": "x"},
        {TOP + "scripts/install-mac.sh": "x"},
    ],
)
def test_download_refuses_bad_zips_and_leaves_nothing(tmp_path, entries):
    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(zip_handler(make_zip(entries))))
    assert not (tmp_path / "v0.2.1").exists()
    assert not list(tmp_path.glob("*.partial"))


def test_download_refuses_not_a_zip(tmp_path):
    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(zip_handler(b"nope")))
    assert not (tmp_path / "v0.2.1").exists()


def test_download_refuses_other_host_before_any_request(tmp_path):
    def handler(request):
        raise AssertionError("no request expected")

    for url in ["https://evil.example/z.zip", "http://api.github.com/z.zip"]:
        with pytest.raises(UpdateError):
            updates.download(release(zip_url=url), tmp_path, client(handler))


def test_override_host_allowed_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv("OJ_UPDATE_URL", "http://127.0.0.1:8765")
    ok = release(zip_url="http://127.0.0.1:8765/z.zip")
    updates.download(ok, tmp_path, client(zip_handler(make_zip())))
    with pytest.raises(UpdateError):
        updates.download(release(zip_url=API), tmp_path, client(zip_handler(make_zip())))
    with pytest.raises(UpdateError):
        updates.download(
            release(zip_url="http://127.0.0.1:9999/z.zip"),
            tmp_path,
            client(zip_handler(make_zip())),
        )


def test_follows_redirect_to_codeload(tmp_path):
    data = make_zip()
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": CODELOAD})
        return httpx.Response(200, content=data)

    updates.download(release(), tmp_path, client(handler))
    assert seen == [API, CODELOAD]


def test_refuses_redirect_to_another_host(tmp_path):
    def handler(request):
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": "https://evil.example/z.zip"})
        raise AssertionError("must not be requested")

    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(handler))
    assert not (tmp_path / "v0.2.1").exists()


def test_refuses_redirect_to_http_downgrade(tmp_path):
    def handler(request):
        return httpx.Response(302, headers={"location": "http://codeload.github.com/z.zip"})

    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(handler))


def test_redirect_loop_is_refused(tmp_path):
    def handler(request):
        return httpx.Response(302, headers={"location": API})

    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(handler))


def test_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "MAX_BYTES", 100)
    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(zip_handler(make_zip())))
    assert not (tmp_path / "v0.2.1").exists()
    assert not list(tmp_path.glob("*.partial"))


def test_network_failure_is_an_update_error(tmp_path):
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(handler))
    assert not list(tmp_path.glob("*.partial"))


def test_http_error_status_is_an_update_error(tmp_path):
    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(lambda r: httpx.Response(404)))


def test_older_download_is_removed_after_success(tmp_path):
    old = tmp_path / "v0.2.0"
    old.mkdir(parents=True)
    (old / "x").write_text("x")
    updates.download(release(), tmp_path, client(zip_handler(make_zip())))
    assert not old.exists()
    assert (tmp_path / "v0.2.1").is_dir()


def test_failed_download_keeps_older_one(tmp_path):
    old = tmp_path / "v0.2.0"
    old.mkdir(parents=True)
    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(lambda r: httpx.Response(500)))
    assert old.exists()


def test_bad_tag_is_refused(tmp_path):
    with pytest.raises(UpdateError):
        updates.download(release(tag="../x"), tmp_path, client(zip_handler(make_zip())))


# --- ready ---


def test_ready_none_when_nothing(tmp_path):
    assert updates.ready(tmp_path / "missing") is None
    assert updates.ready(tmp_path) is None


def test_half_written_folder_is_not_ready(tmp_path):
    (tmp_path / "v0.2.1" / TOP).mkdir(parents=True)
    assert updates.ready(tmp_path) is None


def test_ready_ignores_corrupt_ready_json(tmp_path):
    (tmp_path / "v0.2.1").mkdir()
    (tmp_path / "v0.2.1" / "ready.json").write_text("{oops")
    assert updates.ready(tmp_path) is None


def test_ready_needs_the_folder_to_exist(tmp_path):
    updates.download(release(), tmp_path, client(zip_handler(make_zip())))
    import shutil

    shutil.rmtree(tmp_path / "v0.2.1" / TOP.rstrip("/"))
    assert updates.ready(tmp_path) is None


def test_ready_ignores_folder_outside_updates_dir(tmp_path):
    d = tmp_path / "v0.2.1"
    d.mkdir()
    (d / "ready.json").write_text(
        json.dumps({"tag": "v0.2.1", "page_url": "p", "zip_url": "z", "folder": "../../etc"})
    )
    assert updates.ready(tmp_path) is None


def test_ready_returns_newest(tmp_path):
    for tag in ("v0.2.1", "v0.10.0"):
        d = tmp_path / tag / "top"
        (d / "scripts").mkdir(parents=True)
        (tmp_path / tag / "ready.json").write_text(
            json.dumps({"tag": tag, "page_url": "p", "zip_url": "z", "folder": f"{tag}/top"})
        )
    rel, folder = updates.ready(tmp_path)
    assert rel.tag == "v0.10.0"
    assert folder == tmp_path / "v0.10.0" / "top"


# --- setting ---


def test_check_updates_setting():
    computer = next(s for s in SECTIONS if s.key == "computer")
    setting = next(s for s in computer.settings if s.key == "check_updates")
    assert setting.default == "on"
    assert setting.choices == (("on", "On"), ("off", "Off"))


# --- hostile or damaged downloads ---


def corrupt_deflate_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(TOP + "scripts/install-mac.sh", "echo hello " * 500)
        z.writestr(TOP + "scripts/install-windows.ps1", "x")
    data = bytearray(buf.getvalue())
    # the first member's compressed bytes begin after its local header
    offset = 30 + len(TOP + "scripts/install-mac.sh")
    for i in range(offset, offset + 20):
        data[i] ^= 0xFF
    return bytes(data)


def test_corrupt_deflate_is_an_update_error_and_leaves_nothing(tmp_path):
    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(zip_handler(corrupt_deflate_zip())))
    assert not (tmp_path / "v0.2.1").exists()
    assert not list(tmp_path.glob("*.partial"))


def test_malformed_content_length_is_an_update_error(tmp_path):
    def handler(request):
        return httpx.Response(200, content=make_zip(), headers={"content-length": "abc"})

    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(handler))
    assert not (tmp_path / "v0.2.1").exists()


@pytest.mark.parametrize("name", ["./", "./scripts/install-mac.sh"])
def test_dot_member_names_are_refused(tmp_path, name):
    entries = {
        name: "x",
        TOP + "scripts/install-mac.sh": "x",
        TOP + "scripts/install-windows.ps1": "x",
    }
    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(zip_handler(make_zip(entries))))
    assert not (tmp_path / "v0.2.1").exists()


def test_unpacked_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "MAX_UNPACKED_BYTES", 10)
    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(zip_handler(make_zip())))
    assert not (tmp_path / "v0.2.1").exists()


def test_member_count_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "MAX_MEMBERS", 2)
    with pytest.raises(UpdateError):
        updates.download(release(), tmp_path, client(zip_handler(make_zip())))
    assert not (tmp_path / "v0.2.1").exists()


def test_installer_command_on_windows_keeps_paths_with_spaces_whole():
    from pathlib import PureWindowsPath

    source = PureWindowsPath("C:/Users/Jo Smith/OJ data/updates/v1.2.3/src")
    log = PureWindowsPath("C:/Users/Jo Smith/OJ data/logs/update.log")
    command = updates.installer_command(source, log, "win32")
    assert command[:4] == ["cmd", "/c", "start", "OptionsJourney update"]
    assert command[4:8] == ["powershell", "-ExecutionPolicy", "Bypass", "-Command"]
    assert command[8] == (
        "Start-Transcript -Append 'C:\\Users\\Jo Smith\\OJ data\\logs\\update.log'; "
        "try { & 'C:\\Users\\Jo Smith\\OJ data\\updates\\v1.2.3\\src\\scripts\\install-windows.ps1' } "
        "catch { Write-Host $_ -ForegroundColor Red }; "
        "Stop-Transcript; "
        "Read-Host 'Press Enter to close this window'"
    )


def test_installer_command_on_mac_opens_the_command_file_in_terminal():
    from pathlib import PurePosixPath

    source = PurePosixPath("/Users/jo smith/OJ/updates/v1.2.3/src")
    command = updates.installer_command(source, PurePosixPath("/x/update.log"), "darwin")
    assert command == ["open", "-a", "Terminal", "/Users/jo smith/OJ/updates/v1.2.3/update.command"]


def test_command_file_quotes_paths_and_logs():
    from pathlib import PurePosixPath

    text = updates.command_file_text(
        PurePosixPath("/Users/jo smith/src"), PurePosixPath("/Users/jo smith/logs/update.log")
    )
    assert text == (
        "#!/bin/bash\nbash '/Users/jo smith/src/scripts/install-mac.sh' 2>&1 "
        "| tee -a '/Users/jo smith/logs/update.log'\n"
        "exit_code=${PIPESTATUS[0]}\n"
        "echo\n"
        'read -r -p "Press Return to close this window." _\n'
        'exit "$exit_code"\n'
    )


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_the_installer_starts_outside_the_install_folder(tmp_path, monkeypatch, platform):
    """Windows can't move aside a folder that is a process's working directory, and the
    app runs from its install folder: the installer must not inherit it."""
    import subprocess

    source = tmp_path / "updates" / "v1.2.3" / "src"
    source.mkdir(parents=True)
    seen = {}

    def popen(command, **kwargs):
        seen.update(kwargs, command=command)

    monkeypatch.setattr(updates.sys, "platform", platform)
    if platform == "win32":
        monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 8, raising=False)
        monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(updates.subprocess, "Popen", popen)
    updates.start_installer(source, tmp_path / "logs" / "update.log")
    assert seen["cwd"] == source.parent


def test_update_folders_up_to_the_running_version_are_removed(tmp_path):
    for name in ("v0.1.9", "v0.2.0", "v0.2.1", "v0.10.0", "notes"):
        (tmp_path / name / "top").mkdir(parents=True)
    (tmp_path / "v0.1.0.zip.partial").write_text("x")
    updates.remove_installed(tmp_path, "0.2.0")
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "notes",
        "v0.1.0.zip.partial",
        "v0.10.0",
        "v0.2.1",
    ]


def test_removing_installed_updates_ignores_an_odd_version_or_a_missing_folder(tmp_path):
    (tmp_path / "v0.1.9").mkdir()
    updates.remove_installed(tmp_path, "0+unknown")
    assert (tmp_path / "v0.1.9").exists()
    updates.remove_installed(tmp_path / "missing", "0.2.0")  # no error
