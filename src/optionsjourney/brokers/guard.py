"""The trading guard: in gateway mode, refuse every request that could trade or
touch the Tracker2 server's broker logins.

It sits under every HTTP call the app makes (httpx.Client.send and
httpx.AsyncClient.send), so no module can get around it by building its own
client. Read-only requests and broker order previews (dry runs) pass, except
Schwab's /userPreference: it holds the streamer's address and login, and
Schwab allows one streamer session per login (the server's).

A refused request comes back as a synthetic 403 rather than an exception: to
the bot layer an exception means "ambiguous, maybe placed", while a 4xx is a
clean "rejected".

Ported from Tracker2's src/integrations/dev_guard.py. Once installed it stays
installed for the life of the process; there is deliberately no public way to
turn it off."""

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

REFUSED_STATUS = 403

_TASTYTRADE_HOSTS = {"api.tastyworks.com", "api.cert.tastyworks.com"}
_SCHWAB_HOSTS = {"api.schwabapi.com"}

# Allow rules are checked first: POST .../orders/{id}/dry-run would otherwise
# match the order block.
_TASTYTRADE_PREVIEW = re.compile(r"^/accounts/[^/]+/(complex-)?orders(/[^/]+)?/dry-run$")
_SCHWAB_STREAMER_INFO = "/trader/v1/userPreference"
_SCHWAB_PREVIEW = re.compile(r"^/trader/v1/accounts/[^/]+/previewOrder$")

# The account segment of a path, kept out of the refusal message (it's logged and shown).
_ACCOUNT_SEGMENT = re.compile(r"/accounts/[^/]+")

_original_send = None
_original_async_send = None


def refusal(method: str, host: str, path: str) -> str | None:
    """Why this request is refused, or None to let it through."""
    method = (method or "").upper()
    host = (host or "").lower()
    path = (path or "/").rstrip("/") or "/"
    if host in _SCHWAB_HOSTS and path == _SCHWAB_STREAMER_INFO:
        return "Schwab streamer login"
    if method in ("GET", "HEAD", "OPTIONS"):
        return None
    if host in _TASTYTRADE_HOSTS:
        if _TASTYTRADE_PREVIEW.match(path):
            return None
        if path.startswith("/accounts/"):
            return "TastyTrade order or account change"
        if path.startswith("/oauth/token"):
            return "TastyTrade token refresh"
    elif host in _SCHWAB_HOSTS:
        if _SCHWAB_PREVIEW.match(path):
            return None
        if path.startswith("/trader/v1/accounts/"):
            return "Schwab order or account change"
        if path.startswith("/v1/oauth/token"):
            return "Schwab token refresh"
    return None


def _refused_response(request: httpx.Request) -> httpx.Response | None:
    reason = refusal(request.method, request.url.host, request.url.path)
    if reason is None:
        return None
    path = _ACCOUNT_SEGMENT.sub("/accounts/…", request.url.path)
    message = (
        f"trading guard: {reason} blocked in gateway mode "
        f"({request.method} {request.url.host}{path})"
    )
    logger.warning(message)
    body = json.dumps({"error": {"code": "trading_guard", "message": message}}).encode()
    return httpx.Response(
        REFUSED_STATUS,
        content=body,
        request=request,
        headers={"content-type": "application/json"},
    )


def _guarded_send(self, request, **kwargs):
    refused = _refused_response(request)
    return refused if refused is not None else _original_send(self, request, **kwargs)


async def _guarded_async_send(self, request, **kwargs):
    refused = _refused_response(request)
    return refused if refused is not None else await _original_async_send(self, request, **kwargs)


def install() -> None:
    """Install the guard. Safe to call more than once."""
    global _original_send, _original_async_send
    if _original_send is not None:
        return
    _original_send = httpx.Client.send
    _original_async_send = httpx.AsyncClient.send
    httpx.Client.send = _guarded_send
    httpx.AsyncClient.send = _guarded_async_send
    logger.warning("trading guard installed: orders and token refresh are blocked")


def is_installed() -> bool:
    return _original_send is not None


def _uninstall_for_tests() -> None:
    global _original_send, _original_async_send
    if _original_send is None:
        return
    httpx.Client.send = _original_send
    httpx.AsyncClient.send = _original_async_send
    _original_send = _original_async_send = None
