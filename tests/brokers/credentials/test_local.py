import json
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from optionsjourney.brokers.credentials.base import CredentialsUnavailable, NotConnected
from optionsjourney.brokers.credentials.local import LocalProvider
from optionsjourney.brokers.credentials.store import MemoryStore

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
TASTY = {"client_secret": "secret-1", "refresh_token": "refresh-1"}


def provider(handler, store=None, clock=None):
    store = store or MemoryStore()
    times = clock or [NOW]
    return LocalProvider(
        store, http=httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: times[0]
    ), store


def test_not_connected_says_where_to_connect():
    p, _ = provider(lambda r: httpx.Response(500))
    with pytest.raises(CredentialsUnavailable, match="Settings"):
        p.tastytrade_token()


def test_never_connected_is_its_own_kind():
    # The Stocks chart skips a broker that was never connected, but not one that fails
    p, _ = provider(lambda r: httpx.Response(500))
    with pytest.raises(NotConnected):
        p.tastytrade_token()
    with pytest.raises(NotConnected):
        p.schwab_token()


def test_tastytrade_token_from_the_refresh_token():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"access_token": "access-1", "expires_in": 900})

    p, store = provider(handler)
    store.set("tastytrade", TASTY)
    token = p.tastytrade_token()
    assert token.value == "access-1"
    assert token.expires_at == NOW + timedelta(seconds=900)
    request = seen[0]
    assert request.method == "POST" and str(request.url) == "https://api.tastyworks.com/oauth/token"
    assert json.loads(request.content) == {
        "grant_type": "refresh_token",
        "client_secret": "secret-1",
        "refresh_token": "refresh-1",
    }
    assert "Authorization" not in request.headers


def test_tastytrade_token_is_reused_until_nearly_expired():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"access_token": f"access-{len(calls)}", "expires_in": 900})

    clock = [NOW]
    p, store = provider(handler, clock=clock)
    store.set("tastytrade", TASTY)
    assert p.tastytrade_token().value == "access-1"
    clock[0] = NOW + timedelta(minutes=10)
    assert p.tastytrade_token().value == "access-1"
    clock[0] = NOW + timedelta(minutes=14, seconds=30)  # under a minute left
    assert p.tastytrade_token().value == "access-2"


def test_sandbox_logins_use_the_sandbox():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"access_token": "a", "expires_in": 900})

    p, store = provider(handler)
    store.set("tastytrade", dict(TASTY, sandbox=True))
    assert p.tastytrade_token().is_test
    assert seen[0].url.host == "api.cert.tastyworks.com"


def test_a_refused_login_says_to_reconnect():
    p, store = provider(lambda r: httpx.Response(401, json={"error": "invalid_grant"}))
    store.set("tastytrade", TASTY)
    with pytest.raises(CredentialsUnavailable, match="Reconnect"):
        p.tastytrade_token()


def test_connecting_checks_the_login_before_saving():
    p, store = provider(lambda r: httpx.Response(401))
    with pytest.raises(CredentialsUnavailable):
        p.connect_tastytrade("bad", "bad", sandbox=False)
    assert store.get("tastytrade") is None

    p, store = provider(
        lambda r: httpx.Response(200, json={"access_token": "a", "expires_in": 900})
    )
    p.connect_tastytrade(" secret ", " refresh ", sandbox=False)
    assert store.get("tastytrade") == {
        "client_secret": "secret",
        "refresh_token": "refresh",
        "sandbox": False,
    }
    p.disconnect("tastytrade")
    assert store.get("tastytrade") is None


# --- Schwab (#17) -----------------------------------------------------------------

SCHWAB_APP = {"app_key": "key-1", "app_secret": "secret-1", "callback_url": "https://127.0.0.1"}
REDIRECTED = "https://127.0.0.1/?code=C0DE%40&session=xyz"


def schwab_handler(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{len(seen)}",
                "refresh_token": "refresh-1",
                "expires_in": 1800,
            },
        )

    return handler


def test_schwab_login_link():
    url = LocalProvider.schwab_login_url("key-1", "https://127.0.0.1")
    assert (
        url
        == "https://api.schwabapi.com/v1/oauth/authorize?client_id=key-1&redirect_uri=https%3A%2F%2F127.0.0.1"
    )


def test_connecting_schwab_trades_the_code_for_tokens():
    seen = []
    p, store = provider(schwab_handler(seen))
    p.start_schwab(**SCHWAB_APP)
    assert not p.connected("schwab")  # app details kept, not logged in yet
    p.finish_schwab(REDIRECTED)
    request = seen[0]
    assert str(request.url) == "https://api.schwabapi.com/v1/oauth/token"
    assert request.headers["Authorization"] == "Basic a2V5LTE6c2VjcmV0LTE="  # key-1:secret-1
    form = dict(x.split("=") for x in request.content.decode().split("&"))
    assert form["grant_type"] == "authorization_code" and form["code"] == "C0DE%40"
    assert p.connected("schwab")
    saved = store.get("schwab")
    assert saved["refresh_token"] == "refresh-1" and saved["logged_in_at"] == NOW.isoformat()
    assert p.schwab_token().value == "access-1"  # the first access token is used straight away


def test_a_pasted_url_without_a_code_is_explained():
    p, _ = provider(schwab_handler([]))
    p.start_schwab(**SCHWAB_APP)
    with pytest.raises(CredentialsUnavailable, match="whole address"):
        p.finish_schwab("https://127.0.0.1/?error=denied")
    with pytest.raises(CredentialsUnavailable, match="starts with"):
        p.finish_schwab("https://example.com/?code=x")


def test_schwab_access_tokens_come_from_the_refresh_token():
    seen = []
    clock = [NOW]
    p, store = provider(schwab_handler(seen), clock=clock)
    store.set("schwab", dict(SCHWAB_APP, refresh_token="refresh-1", logged_in_at=NOW.isoformat()))
    assert p.schwab_token().value == "access-1"
    form = dict(x.split("=") for x in seen[0].content.decode().split("&"))
    assert form == {"grant_type": "refresh_token", "refresh_token": "refresh-1"}
    clock[0] = NOW + timedelta(minutes=29, seconds=30)
    assert p.schwab_token().value == "access-2"


def test_schwab_login_lasts_7_days():
    clock = [NOW]
    p, store = provider(schwab_handler([]), clock=clock)
    store.set("schwab", dict(SCHWAB_APP, refresh_token="refresh-1", logged_in_at=NOW.isoformat()))
    assert p.schwab_login_expires_at() == NOW + timedelta(days=7)
    clock[0] = NOW + timedelta(days=7, minutes=1)
    with pytest.raises(CredentialsUnavailable, match="log in to Schwab again"):
        p.schwab_token()


def test_a_slow_schwab_login_doesnt_hold_up_tastytrade():
    release = threading.Event()
    started = threading.Event()

    def handler(request):
        if request.url.host == "api.schwabapi.com":
            started.set()
            release.wait(5)
            return httpx.Response(200, json={"access_token": "s-1", "expires_in": 1800})
        return httpx.Response(200, json={"access_token": "t-1", "expires_in": 900})

    p, store = provider(handler)
    store.set("tastytrade", TASTY)
    store.set("schwab", dict(SCHWAB_APP, refresh_token="refresh-1", logged_in_at=NOW.isoformat()))
    waiter = threading.Thread(target=p.schwab_token)
    waiter.start()
    try:
        assert started.wait(2)
        began = time.monotonic()
        assert p.tastytrade_token().value == "t-1"
        assert time.monotonic() - began < 1
    finally:
        release.set()
        waiter.join()


def test_a_failed_refresh_is_not_retried_for_30_seconds():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    clock = [NOW]
    p, store = provider(handler, clock=clock)
    store.set("tastytrade", TASTY)
    for offset in (0, 10):
        clock[0] = NOW + timedelta(seconds=offset)
        with pytest.raises(CredentialsUnavailable, match="answered 503"):
            p.tastytrade_token()
        assert len(calls) == 1
    clock[0] = NOW + timedelta(seconds=31)
    with pytest.raises(CredentialsUnavailable, match="answered 503"):
        p.tastytrade_token()
    assert len(calls) == 2


def test_reconnecting_clears_the_back_off():
    state = {"up": False}

    def handler(request):
        if not state["up"]:
            return httpx.Response(503)
        return httpx.Response(200, json={"access_token": "t-2", "expires_in": 900})

    p, store = provider(handler)
    store.set("tastytrade", TASTY)
    with pytest.raises(CredentialsUnavailable):
        p.tastytrade_token()
    state["up"] = True
    p.connect_tastytrade("secret-1", "refresh-1", sandbox=False)
    assert p.tastytrade_token().value == "t-2"


def test_a_login_reply_that_isnt_json_is_credentials_unavailable():
    p, store = provider(lambda r: httpx.Response(200, text="<html>"))
    store.set("tastytrade", TASTY)
    with pytest.raises(CredentialsUnavailable, match="couldn't read"):
        p.tastytrade_token()
    store.set("schwab", dict(SCHWAB_APP, refresh_token="refresh-1", logged_in_at=NOW.isoformat()))
    with pytest.raises(CredentialsUnavailable, match="couldn't read"):
        p.schwab_token()
