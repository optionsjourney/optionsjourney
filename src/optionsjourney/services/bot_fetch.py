"""A bot file fetched from a link posted online (decision 0056; Tracker2's
share_import.fetch_snapshot).

bots/bot_link.py checks the link and turns a share page into the file's own
link. Here each step is fetched by hand so every redirect is checked the same
way, and the name is looked up first so one that leads to this computer or the
home network is refused. At most MAX_BYTES are read. What comes back is text for
bot_file.parse, which reads it as strictly as an uploaded file.
"""

import socket
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import httpx

from optionsjourney.bots import bot_file, bot_link
from optionsjourney.bots.bot_file import FileError

TIMEOUT_S = 10
MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (OptionsJourney)"  # Drive and Dropbox answer a browser's


def _addresses(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


def fetch(
    url: str,
    http: httpx.Client | None = None,
    resolve: Callable[[str], Iterable[str]] = _addresses,
) -> str:
    """The text of the bot file `url` links to, or FileError worded for the user."""
    target = bot_link.direct(url)
    own = http is None
    http = http or httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _check_host(urlsplit(target).hostname, resolve)
            with http.stream("GET", target, follow_redirects=False) as response:
                if response.is_redirect:
                    target = bot_link.check(urljoin(target, response.headers["Location"]))
                    continue
                return _text(response)
        raise FileError("That link redirects too many times to reach a bot file.")
    except httpx.TimeoutException:
        raise FileError("That link timed out. Try again, or download the file and choose it "
                        "here.") from None  # fmt: skip
    except httpx.HTTPError:
        raise FileError("That link couldn't be reached. Check it, and that this computer "
                        "is online.") from None  # fmt: skip
    finally:
        if own:
            http.close()


def _check_host(host: str, resolve) -> None:
    try:
        addresses = list(resolve(host))
    except OSError:
        raise FileError(f"That link's site ({host}) couldn't be found. Check the link.") from None
    if not addresses or not all(bot_link.is_public(a) for a in addresses):
        raise FileError(bot_link.NOT_HERE)


def _text(response: httpx.Response) -> str:
    if response.status_code in (401, 403):
        raise FileError(f"That link answered {response.status_code}: the file may not be shared "
                        "publicly. Set it so anyone with the link can view it.")  # fmt: skip
    if response.status_code != 200:
        raise FileError(f"That link answered {response.status_code} (deleted, or mistyped?).")
    body = b""
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) > bot_file.MAX_BYTES:
            raise FileError("That link's file is too large to be a bot file.")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileError("That link didn't give a bot file: it isn't plain text.") from None
    if text.lstrip().startswith("<"):
        raise FileError("That link gave a web page, not a bot file. If it's on Google Drive "
                        "or Dropbox, share it so anyone with the link can view it.")  # fmt: skip
    return text
