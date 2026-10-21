import json

import httpx
import pytest

from optionsjourney.bots import bot_file
from optionsjourney.bots.bot_file import FileError
from optionsjourney.services import bot_fetch

FILE = json.dumps({"format": "optionsjourney.bot", "version": 3, "bot": {"name": "SPX IC"}})
PUBLIC = "93.184.215.14"


def public(host):
    return [PUBLIC]


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def fetch(url, handler, resolve=public):
    return bot_fetch.fetch(url, http=client(handler), resolve=resolve)


def test_a_link_to_a_bot_file_gives_its_text():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text=FILE)

    assert fetch("https://example.com/spx-ic.json", handler) == FILE
    assert bot_file.parse(FILE).bot.name == "SPX IC"
    assert seen == ["https://example.com/spx-ic.json"]


def test_a_drive_share_link_is_fetched_as_its_download_following_redirects():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "drive.google.com":
            return httpx.Response(
                303, headers={"Location": "https://drive.usercontent.google.com/download?id=1Ab"}
            )
        return httpx.Response(200, text=FILE)

    assert fetch("https://drive.google.com/file/d/1Ab/view?usp=sharing", handler) == FILE
    assert seen == ["https://drive.google.com/uc?export=download&id=1Ab",
                    "https://drive.usercontent.google.com/download?id=1Ab"]  # fmt: skip


@pytest.mark.parametrize("location", [
    "http://example.com/a.json", "https://localhost/a.json", "https://192.168.1.1/a.json",
])  # fmt: skip
def test_a_redirect_is_checked_like_the_link(location):
    def handler(request):
        if request.url.path == "/a.json":
            return httpx.Response(302, headers={"Location": location})
        raise AssertionError("followed a redirect it shouldn't have")

    with pytest.raises(FileError):
        fetch("https://example.com/a.json", handler)


def test_too_many_redirects_are_refused():
    def handler(request):
        return httpx.Response(302, headers={"Location": "https://example.com/again"})

    with pytest.raises(FileError, match="redirect"):
        fetch("https://example.com/a.json", handler)


def test_a_name_that_leads_to_the_home_network_is_refused():
    with pytest.raises(FileError, match="home network"):
        fetch("https://sneaky.example.com/a.json", lambda r: httpx.Response(200, text=FILE),
              resolve=lambda host: [PUBLIC, "192.168.1.5"])  # fmt: skip


def test_a_name_that_cant_be_found_says_so():
    def unknown(host):
        raise OSError("no such host")

    with pytest.raises(FileError, match="couldn't be found"):
        fetch("https://nowhere.example/a.json", lambda r: httpx.Response(200), resolve=unknown)


@pytest.mark.parametrize("response, words", [
    (httpx.Response(404), "404"),
    (httpx.Response(403), "shared publicly"),
    (httpx.Response(200, text="<!DOCTYPE html><html>Sign in</html>"), "web page"),
    (httpx.Response(200, text="x" * (bot_file.MAX_BYTES + 1)), "too large"),
])  # fmt: skip
def test_answers_that_arent_a_bot_file_are_explained(response, words):
    with pytest.raises(FileError, match=words):
        fetch("https://example.com/a.json", lambda request: response)


def test_a_timeout_or_dropped_connection_is_explained():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def dropped(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(FileError, match="timed out"):
        fetch("https://example.com/a.json", slow)
    with pytest.raises(FileError, match="couldn't be reached"):
        fetch("https://example.com/a.json", dropped)


def test_a_file_with_a_byte_order_mark_reads():
    body = "﻿".encode() + FILE.encode()
    assert fetch("https://example.com/a.json", lambda r: httpx.Response(200, content=body)) == FILE
