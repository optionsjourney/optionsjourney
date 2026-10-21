"""A link to a bot file posted online, checked and turned into the file's own
link (decision 0056; Tracker2's share_import.normalize_url). Pure: fetching is
services/bot_fetch.py.

Only https links to the internet: never this computer or the home network, so a
pasted link can't reach a router's page or this app. A share page from Google
Drive, Dropbox or GitHub, or a Tracker2 share link (decision 0057), becomes the link
that downloads the file itself.
"""

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from optionsjourney.bots.bot_file import FileError

MAX_LENGTH = 2000
_LOCAL_NAMES = ("localhost", ".local", ".internal", ".lan", ".home.arpa")
_DRIVE_FILE = re.compile(r"^/file/d/([A-Za-z0-9_-]+)")
_GITHUB_BLOB = re.compile(r"^/([^/]+)/([^/]+)/blob/(.+)$")
_GIST = re.compile(r"^/[^/]+/[0-9a-fA-F]+/?$")
_TRACKER2_SHARE = re.compile(r"^/share/bot/([A-Za-z0-9_-]+)/?$")  # share_import._SHARE_PATH
_OWN_EXPORT = re.compile(r"^/automation/\d+/export/?$")


NOT_HERE = (
    "That link points at this computer or your home network, so it can't be imported "
    "from. Use a link to a file posted online (Google Drive, Dropbox, GitHub ...)."
)


def check(url: str) -> str:
    """`url` as it is if it can be fetched: https, a host on the internet, no
    name or password in it. Else FileError, worded for the user."""
    url = url.strip()
    if not url:
        raise FileError("Paste a link to a bot file.")
    if len(url) > MAX_LENGTH:
        raise FileError("That link is too long to be a link to a bot file.")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if _OWN_EXPORT.match(parts.path) and _is_local(host):
        raise FileError(
            "That's this app's own Download bot file link, which only works on this "
            "computer. Press Download bot file on the bot's page, then choose that file "
            "here, or post it online and paste that link."
        )
    if parts.scheme != "https" or not host:
        raise FileError("A link to a bot file must start with https://.")
    if parts.username is not None or parts.password is not None:
        raise FileError("That link has a name or password in it; use one without.")
    if _is_local(host):
        raise FileError(NOT_HERE)
    return url


def direct(url: str) -> str:
    """The link that downloads the file `url` shares (checked)."""
    url = check(url)
    parts = urlsplit(url)
    host = parts.hostname.lower()
    if host == "drive.google.com":
        found = _DRIVE_FILE.match(parts.path)
        file_id = found.group(1) if found else dict(parse_qsl(parts.query)).get("id")
        if file_id:
            return f"https://drive.google.com/uc?export=download&id={file_id}"
    if host in ("dropbox.com", "www.dropbox.com"):
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("dl", "raw")]
        return urlunsplit(parts._replace(query=urlencode(query + [("dl", "1")]), fragment=""))
    if host == "github.com":
        found = _GITHUB_BLOB.match(parts.path)
        if found:
            owner, repo, rest = found.groups()
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"
    found = _TRACKER2_SHARE.match(parts.path)
    if found:  # decision 0057: a Tracker2 share page's bot file
        return urlunsplit(("https", parts.netloc, f"/share/bot/{found.group(1)}.json", "", ""))
    if host == "gist.github.com" and _GIST.match(parts.path):
        return urlunsplit(parts._replace(path=parts.path.rstrip("/") + "/raw", query="",
                                         fragment=""))  # fmt: skip
    return url


def is_public(address: str) -> bool:
    """Whether an IP address is on the internet: not this computer, the home
    network, link-local or any other reserved range."""
    try:
        ip = ipaddress.ip_address(address.strip("[]"))
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


def _is_local(host: str) -> bool:
    """An address that isn't on the internet, or a name only a home network knows
    (localhost, printer.local, a bare "nas")."""
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return host.endswith(_LOCAL_NAMES) or "." not in host
    return not is_public(host)
