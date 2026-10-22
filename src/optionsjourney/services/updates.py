"""Ask GitHub whether a newer OptionsJourney is out, and fetch and unpack it.

Only the release zip is downloaded here. Running the installer is done
elsewhere. Every address is checked against a short list of hosts before it is
fetched, redirects included, and a zip is unpacked only if every entry stays
inside its folder. Nothing about the user is sent but the usual User-Agent.
"""

import json
import logging
import os
import shlex
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

import httpx

from optionsjourney import __version__
from optionsjourney.core.versions import is_newer, parse_tag

logger = logging.getLogger(__name__)

GITHUB_LATEST = "https://api.github.com/repos/optionsjourney/optionsjourney/releases/latest"
GITHUB_HOSTS = {"api.github.com", "codeload.github.com", "github.com"}
MAX_BYTES = 200 * 1024 * 1024
TIMEOUT_S = 30
MAX_REDIRECTS = 5
MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 20_000
INSTALLERS = ("scripts/install-mac.sh", "scripts/install-windows.ps1")
READY = "ready.json"


class UpdateError(Exception):
    """The download failed. The message is plain words, fit to show a person."""


@dataclass(frozen=True)
class Release:
    tag: str
    page_url: str
    zip_url: str


def _override() -> str | None:
    value = os.environ.get("OJ_UPDATE_URL", "").strip()
    return value.rstrip("/") if value else None


def source_url() -> str:
    override = _override()
    return f"{override}/latest.json" if override else GITHUB_LATEST


def _origin(url: str) -> tuple[str, str, int | None] | None:
    try:
        parts = urlsplit(url)
        return parts.scheme, (parts.hostname or ""), parts.port
    except ValueError:
        return None


def _allowed(url: str) -> bool:
    origin = _origin(url)
    if origin is None:
        return False
    override = _override()
    if override:
        return origin == _origin(override)
    scheme, host, port = origin
    return scheme == "https" and host in GITHUB_HOSTS and port in (None, 443)


def _headers() -> dict:
    return {"Accept": "application/json", "User-Agent": f"optionsjourney/{__version__}"}


def latest(http: httpx.Client, running: str) -> Release | None:
    """The newest release if it is newer than `running`; None otherwise and on
    any problem (private repo, rate limit, no network, odd reply)."""
    url = source_url()
    try:
        response = http.get(url, headers=_headers(), timeout=TIMEOUT_S)
        if response.status_code != 200:
            logger.warning(
                "Update check: %s answered %s", urlsplit(url).hostname, response.status_code
            )
            return None
        data = response.json()
        tag, page, zip_url = (
            data["tag_name"],
            data["html_url"],
            data.get("zip_url") or data["zipball_url"],
        )
        if not all(isinstance(v, str) and v for v in (tag, page, zip_url)):
            raise ValueError("fields are not text")
    except httpx.HTTPError as e:
        logger.warning(
            "Update check: can't reach %s (%s)", urlsplit(url).hostname, type(e).__name__
        )
        return None
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        logger.warning("Update check: unreadable reply (%s)", type(e).__name__)
        return None
    if not is_newer(tag, running):
        return None
    return Release(tag=tag, page_url=page, zip_url=zip_url)


def _fetch(release: Release, http: httpx.Client, partial: Path) -> None:
    """Stream the zip to `partial`, checking the host of every hop."""
    url = release.zip_url
    for _ in range(MAX_REDIRECTS + 1):
        if not _allowed(url):
            raise UpdateError(
                "The update isn't coming from a place OptionsJourney trusts, "
                "so it was not downloaded."
            )
        try:
            with http.stream(
                "GET", url, headers=_headers(), timeout=TIMEOUT_S, follow_redirects=False
            ) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    location = r.headers.get("location")
                    if not location:
                        raise UpdateError("The download address sent no place to go next.")
                    url = urljoin(url, location)
                    continue
                if r.status_code != 200:
                    raise UpdateError(f"The download failed (the server answered {r.status_code}).")
                if int(r.headers.get("content-length") or 0) > MAX_BYTES:
                    raise UpdateError(
                        "The update is bigger than expected, so it was not downloaded."
                    )
                size = 0
                with partial.open("wb") as out:
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise UpdateError(
                                "The update is bigger than expected, so it was not downloaded."
                            )
                        out.write(chunk)
                return
        except httpx.HTTPError as e:
            raise UpdateError(
                "Couldn't download the update. Check your internet connection."
            ) from e
    raise UpdateError("The download address kept redirecting, so it was not downloaded.")


def _safe_members(z: zipfile.ZipFile, target: Path) -> str:
    """Check every entry stays inside `target`; return the single top folder."""
    root = target.resolve()
    tops = set()
    total = 0
    names = set()
    for count, info in enumerate(z.infolist(), 1):
        name = info.filename
        path = PurePosixPath(name)
        if not path.parts or path.parts[0] == ".":
            raise UpdateError("The update file has an unsafe entry, so it was not used.")
        total += info.file_size
        if count > MAX_MEMBERS or total > MAX_UNPACKED_BYTES:
            raise UpdateError("The update file unpacks to more than expected, so it was not used.")
        if (
            name.startswith(("/", "\\"))
            or ".." in path.parts
            or "\\" in name
            or ":" in path.parts[0]
        ):
            raise UpdateError("The update file has an unsafe entry, so it was not used.")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise UpdateError("The update file has an unsafe entry, so it was not used.")
        resolved = (root / name).resolve()
        if resolved != root and root not in resolved.parents:
            raise UpdateError("The update file has an unsafe entry, so it was not used.")
        tops.add(path.parts[0])
        names.add(name)
    if len(tops) != 1:
        raise UpdateError("The update file isn't laid out as expected, so it was not used.")
    top = tops.pop()
    for installer in INSTALLERS:
        if f"{top}/{installer}" not in names:
            raise UpdateError("The update file has no installer in it, so it was not used.")
    return top


def _remove_older(updates_dir: Path, tag: str) -> None:
    keep = parse_tag(tag)
    for child in updates_dir.iterdir():
        found = parse_tag(child.name)
        if child.is_dir() and found is not None and found < keep:
            shutil.rmtree(child, ignore_errors=True)


def remove_installed(updates_dir: Path, running: str) -> None:
    """Remove the downloads of this version and older ones: once a version runs, its
    download is no longer needed. Called when the menu bar app starts."""
    current = parse_tag(running)
    if current is None:
        return
    try:
        children = list(updates_dir.iterdir())
    except OSError:
        return
    for child in children:
        found = parse_tag(child.name)
        if child.is_dir() and found is not None and found <= current:
            shutil.rmtree(child, ignore_errors=True)


def download(release: Release, updates_dir: Path, http: httpx.Client) -> Path:
    """Fetch and unpack the release; return the folder that holds `scripts/`."""
    if parse_tag(release.tag) is None:
        raise UpdateError("That version number isn't one OptionsJourney recognises.")
    if not _allowed(release.zip_url):
        raise UpdateError(
            "The update isn't coming from a place OptionsJourney trusts, so it was not downloaded."
        )
    target = updates_dir / release.tag
    partial = updates_dir / f"{release.tag}.zip.partial"
    try:
        updates_dir.mkdir(parents=True, exist_ok=True)
        _fetch(release, http, partial)
        shutil.rmtree(target, ignore_errors=True)
        try:
            with zipfile.ZipFile(partial) as z:
                target.mkdir()
                top = _safe_members(z, target)
                z.extractall(target)
        except zipfile.BadZipFile as e:
            raise UpdateError("The downloaded update file was damaged, so it was not used.") from e
        folder = target / top
        ready = {
            "tag": release.tag,
            "page_url": release.page_url,
            "zip_url": release.zip_url,
            "folder": f"{release.tag}/{top}",
        }
        (target / READY).write_text(json.dumps(ready), encoding="utf-8")
    except UpdateError:
        shutil.rmtree(target, ignore_errors=True)
        logger.warning("Update download of %s failed", release.tag)
        raise
    except OSError as e:
        shutil.rmtree(target, ignore_errors=True)
        logger.warning("Update download of %s failed: %s", release.tag, e)
        raise UpdateError("Couldn't save the update on this computer.") from e
    except Exception as e:
        shutil.rmtree(target, ignore_errors=True)
        logger.warning("Update download of %s failed: %s", release.tag, type(e).__name__)
        raise UpdateError("The downloaded update file was damaged, so it was not used.") from e
    finally:
        partial.unlink(missing_ok=True)
    _remove_older(updates_dir, release.tag)
    return folder


def ready(updates_dir: Path) -> tuple[Release, Path] | None:
    """The newest finished download, or None. A folder without a readable
    ready.json (half written) never counts."""
    if not updates_dir.is_dir():
        return None
    base = updates_dir.resolve()
    best = None
    for child in updates_dir.iterdir():
        version = parse_tag(child.name)
        if version is None or not child.is_dir():
            continue
        try:
            data = json.loads((child / READY).read_text(encoding="utf-8"))
            rel = Release(tag=data["tag"], page_url=data["page_url"], zip_url=data["zip_url"])
            folder = (updates_dir / data["folder"]).resolve()
        except OSError, ValueError, KeyError, TypeError:
            continue
        if rel.tag != child.name or base not in folder.parents or not (folder / "scripts").is_dir():
            continue
        if best is None or version > best[0]:
            best = (version, rel, updates_dir / data["folder"])
    return (best[1], best[2]) if best else None


UPDATE_LOG = "update.log"


def installer_command(source: Path, log_path: Path, platform: str) -> list[str]:
    """The command that opens the installer in its own window, as an argument list
    (no shell string, so paths with spaces stay whole). `platform` is sys.platform."""
    if platform == "win32":
        script = source / "scripts" / "install-windows.ps1"
        run = (
            f"Start-Transcript -Append {_ps_quote(log_path)}; "
            # An error the script doesn't catch would end -Command and close the window:
            # catch it here, so it stays on screen with the Read-Host
            f"try {{ & {_ps_quote(script)} }} catch {{ Write-Host $_ -ForegroundColor Red }}; "
            "Stop-Transcript; "
            "Read-Host 'Press Enter to close this window'"
        )
        return [
            "cmd", "/c", "start", "OptionsJourney update",
            "powershell", "-ExecutionPolicy", "Bypass", "-Command", run,
        ]  # fmt: skip
    return ["open", "-a", "Terminal", str(source.parent / "update.command")]


def _ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def command_file_text(source: Path, log_path: Path) -> str:
    """The Mac `update.command`: Terminal runs it, and the installer's output also
    goes to the log. The window waits for Return so errors stay on screen, and the
    file ends with the installer's exit code, not tee's."""
    script = shlex.quote(str(source / "scripts" / "install-mac.sh"))
    log = shlex.quote(str(log_path))
    return (
        f"#!/bin/bash\nbash {script} 2>&1 | tee -a {log}\n"
        "exit_code=${PIPESTATUS[0]}\n"
        "echo\n"
        'read -r -p "Press Return to close this window." _\n'
        'exit "$exit_code"\n'
    )


def start_installer(source: Path, log_path: Path) -> None:
    """Starts the installer in its own window, detached, so it outlives this app
    (the installer stops the app). It runs from the updates folder: the app's own
    folder as its working directory would stop Windows moving that folder aside."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        command_file = source.parent / "update.command"
        command_file.write_text(command_file_text(source, log_path), encoding="utf-8")
        command_file.chmod(0o755)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    quiet = subprocess.DEVNULL
    subprocess.Popen(installer_command(source, log_path, sys.platform),  # noqa: S603
                     cwd=source.parent, creationflags=flags,
                     start_new_session=sys.platform != "win32",
                     stdin=quiet, stdout=quiet, stderr=quiet)  # fmt: skip
