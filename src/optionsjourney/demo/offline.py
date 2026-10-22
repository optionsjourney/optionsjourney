"""The offline guard: in demo mode no network request leaves the process.

Stricter than the trading guard (brokers/guard.py): every httpx request, sync
or async, and every websockets client connection raises DemoOffline. Once
installed it stays installed for the life of the process; there is
deliberately no way to turn it off."""

import logging

import httpx
import websockets.asyncio.client
import websockets.sync.client

logger = logging.getLogger(__name__)

_installed = False
_originals: dict = {}  # what install replaced, for tests only


class DemoOffline(ConnectionError):
    """Something tried to reach the network while the demo is running. A ConnectionError,
    so code that already copes with no network (a chart's history falling back from one
    broker to the next) answers as it would offline instead of failing the page."""

    def __init__(self, url):
        self.url = str(url)
        super().__init__(f"demo mode is offline: refused a connection to {self.url}")


def _refuse(url) -> DemoOffline:
    """Log the attempt (a route's own `except OSError` would otherwise hide it), then
    hand back the error to raise."""
    error = DemoOffline(url)
    logger.warning("demo offline guard blocked a network attempt: %s", error.url)
    return error


def _blocked_send(self, request, **kwargs):
    raise _refuse(request.url)


async def _blocked_async_send(self, request, **kwargs):
    raise _refuse(request.url)


def _blocked_connect(uri, *args, **kwargs):
    raise _refuse(uri)


def install() -> None:
    """Install the guard. Safe to call more than once."""
    global _installed
    if _installed:
        return
    _originals.update(
        client=httpx.Client.send,
        async_client=httpx.AsyncClient.send,
        sync_connect=websockets.sync.client.connect,
        async_connect=websockets.asyncio.client.connect,
    )
    httpx.Client.send = _blocked_send
    httpx.AsyncClient.send = _blocked_async_send
    websockets.sync.client.connect = _blocked_connect
    websockets.asyncio.client.connect = _blocked_connect
    _installed = True
    logger.warning("demo offline guard installed: all network access is blocked")


def _uninstall_for_tests() -> None:
    """Tests that build a demo app in-process put the network back afterwards."""
    global _installed
    if not _installed:
        return
    httpx.Client.send = _originals["client"]
    httpx.AsyncClient.send = _originals["async_client"]
    websockets.sync.client.connect = _originals["sync_connect"]
    websockets.asyncio.client.connect = _originals["async_connect"]
    _originals.clear()
    _installed = False
