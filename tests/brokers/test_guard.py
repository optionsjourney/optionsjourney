import httpx
import pytest

from optionsjourney.brokers import guard
from optionsjourney.brokers.http import BrokerError
from optionsjourney.brokers.schwab import SchwabClient
from optionsjourney.brokers.tastytrade import TastyTradeClient

TT = "api.tastyworks.com"
SCHWAB = "api.schwabapi.com"


@pytest.mark.parametrize(
    ("method", "host", "path"),
    [
        # TastyTrade orders, account changes and token refresh
        ("POST", TT, "/accounts/5WX00000/orders"),
        ("POST", TT, "/accounts/5WX00000/complex-orders"),
        ("PUT", TT, "/accounts/5WX00000/orders/123"),
        ("DELETE", TT, "/accounts/5WX00000/orders/123"),
        ("DELETE", TT, "/accounts/5WX00000/complex-orders/123"),
        ("PATCH", TT, "/accounts/5WX00000/orders/123"),
        ("POST", "api.cert.tastyworks.com", "/accounts/5WX00000/orders"),
        ("POST", TT, "/oauth/token"),
        # Schwab orders, account changes and token refresh
        ("POST", SCHWAB, "/trader/v1/accounts/HASH/orders"),
        ("PUT", SCHWAB, "/trader/v1/accounts/HASH/orders/123"),
        ("DELETE", SCHWAB, "/trader/v1/accounts/HASH/orders/123"),
        ("POST", SCHWAB, "/v1/oauth/token"),
        # Schwab's streamer login: one session per login, and it's the server's
        ("GET", SCHWAB, "/trader/v1/userPreference"),
        ("GET", SCHWAB, "/trader/v1/userPreference/"),
    ],
)
def test_refuses_trading_and_token_refresh(method, host, path):
    assert guard.refusal(method, host, path) is not None


@pytest.mark.parametrize(
    ("method", "host", "path"),
    [
        ("GET", TT, "/customers/me/accounts"),
        ("GET", TT, "/accounts/5WX00000/balances"),
        ("GET", TT, "/accounts/5WX00000/orders"),
        ("POST", TT, "/accounts/5WX00000/orders/dry-run"),
        ("POST", TT, "/accounts/5WX00000/complex-orders/dry-run"),
        ("POST", TT, "/accounts/5WX00000/orders/123/dry-run"),
        ("POST", TT, "/api-quote-tokens"),
        ("GET", SCHWAB, "/trader/v1/accounts"),
        ("POST", SCHWAB, "/trader/v1/accounts/HASH/previewOrder"),
        ("POST", "tracker2.test", "/accounts/anything"),
    ],
)
def test_allows_reads_and_previews(method, host, path):
    assert guard.refusal(method, host, path) is None


def _client(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_installed_guard_blocks_before_the_request_leaves():
    guard.install()
    seen = []
    response = _client(seen).post(f"https://{TT}/accounts/5WX00000/orders", json={})
    assert response.status_code == guard.REFUSED_STATUS
    assert response.json()["error"]["code"] == "trading_guard"
    assert seen == []


@pytest.mark.parametrize(
    ("host", "path"),
    [(TT, "/accounts/5WX01234/orders/123"), (SCHWAB, "/trader/v1/accounts/HASH-A/orders")],
)
def test_the_refusal_message_never_shows_the_account(host, path):
    guard.install()
    response = _client([]).post(f"https://{host}{path}", json={})
    message = response.json()["error"]["message"]
    assert "5WX01234" not in message and "HASH-A" not in message
    assert "/accounts/…/orders" in message and host in message


def test_installed_guard_lets_previews_through():
    guard.install()
    seen = []
    response = _client(seen).post(f"https://{TT}/accounts/5WX00000/orders/dry-run", json={})
    assert response.status_code == 200
    assert len(seen) == 1


async def _async_post(url):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
        return await c.post(url)


def test_installed_guard_blocks_async_clients():
    import asyncio

    guard.install()
    response = asyncio.run(_async_post(f"https://{SCHWAB}/trader/v1/accounts/HASH/orders"))
    assert response.status_code == guard.REFUSED_STATUS


def test_install_is_idempotent():
    guard.install()
    guard.install()
    assert guard.is_installed()


def _recording_http(seen):
    def handler(request):
        seen.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/accountNumbers"):
            return httpx.Response(200, json=[{"accountNumber": "12345678", "hashValue": "HASH-A"}])
        if path.endswith("/trader/v1/accounts"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"data": {}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_installed_guard_blocks_every_order_change_from_both_clients(fake_credentials):
    guard.install()
    seen = []
    tasty = TastyTradeClient(fake_credentials, http=_recording_http(seen))
    schwab = SchwabClient(fake_credentials, http=_recording_http(seen))
    for call in (
        lambda: tasty.place_order("5WX01234", {}),
        lambda: tasty.replace_order("5WX01234", "1", {}),
        lambda: tasty.cancel_order("5WX01234", "1"),
        lambda: schwab.place_order("12345678", {}),
        lambda: schwab.replace_order("12345678", "1", {}),
        lambda: schwab.cancel_order("12345678", "1"),
    ):
        with pytest.raises(BrokerError):
            call()
    tasty.preview_order("5WX01234", {})
    schwab.preview_order("12345678", {})
    changes = [(m, p) for m, p in seen if m in ("PUT", "DELETE") or p.endswith("/orders")]
    assert changes == []  # nothing that changes an order left the process


def test_installed_guard_keeps_schwabs_streamer_login_from_leaving(fake_credentials):
    guard.install()
    seen = []
    schwab = SchwabClient(fake_credentials, http=_recording_http(seen))
    with pytest.raises(BrokerError):
        schwab.streamer_info()
    assert seen == []
