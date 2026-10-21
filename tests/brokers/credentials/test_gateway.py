from datetime import UTC, datetime, timedelta

import httpx
import pytest

from optionsjourney.brokers import guard
from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.credentials.gateway import GatewayProvider

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def _lease(tt_expires=NOW + timedelta(minutes=30), schwab=True):
    return {
        "tastytrade": {
            "session_token": "tt-access",
            "session_expiration": tt_expires.isoformat(),
            "is_test": False,
        },
        "schwab": {
            "access_token": "sw-access",
            "expires_at": int((NOW + timedelta(minutes=25)).timestamp()),
            "token_type": "Bearer",
        }
        if schwab
        else None,
    }


def _provider(responses, now=lambda: NOW):
    """A provider whose gateway answers with `responses` in order."""
    requests = []

    def handler(request):
        requests.append(request)
        return responses.pop(0)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return GatewayProvider("http://tracker2.test:8800/", "s3cret", http=http, now=now), requests


def test_fetches_lease_with_bearer_secret():
    provider, requests = _provider([httpx.Response(200, json=_lease())])
    token = provider.tastytrade_token()
    assert token.value == "tt-access"
    assert not token.is_test
    assert requests[0].url == "http://tracker2.test:8800/gateway/credentials"
    assert requests[0].headers["Authorization"] == "Bearer s3cret"


def test_installs_the_trading_guard():
    _provider([])
    assert guard.is_installed()


def test_reuses_a_fresh_lease():
    provider, requests = _provider([httpx.Response(200, json=_lease())])
    provider.tastytrade_token()
    provider.schwab_token()
    provider.tastytrade_token()
    assert len(requests) == 1


def test_fetches_again_when_the_token_is_about_to_expire():
    almost_expired = _lease(tt_expires=NOW + timedelta(seconds=90))
    provider, requests = _provider(
        [
            httpx.Response(200, json=almost_expired),
            httpx.Response(200, json=_lease()),
        ]
    )
    assert provider.tastytrade_token().expires_at == NOW + timedelta(seconds=90)
    assert provider.tastytrade_token().expires_at == NOW + timedelta(minutes=30)
    assert len(requests) == 2


def test_schwab_token():
    provider, _ = _provider([httpx.Response(200, json=_lease())])
    token = provider.schwab_token()
    assert token.value == "sw-access"
    assert token.expires_at == NOW + timedelta(minutes=25)


def test_no_schwab_token_on_the_server():
    provider, _ = _provider([httpx.Response(200, json=_lease(schwab=False))] * 2)
    with pytest.raises(CredentialsUnavailable, match="no Schwab token"):
        provider.schwab_token()


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "rejected GATEWAY_SECRET"), (403, "home network"), (500, "answered 500")],
)
def test_gateway_errors(status, message):
    provider, _ = _provider([httpx.Response(status)])
    with pytest.raises(CredentialsUnavailable, match=message):
        provider.tastytrade_token()


def test_gateway_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GatewayProvider("http://tracker2.test:8800", "s3cret", http=http)
    with pytest.raises(CredentialsUnavailable, match="Can't reach"):
        provider.tastytrade_token()
