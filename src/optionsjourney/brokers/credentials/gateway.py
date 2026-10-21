"""Development only: access tokens leased from the author's Tracker2 server.

Tracker2 (APP_ROLE=prod) serves GET /gateway/credentials on the home network,
behind a bearer secret. The lease holds access tokens only, never refresh
tokens, so this machine can use a token until it expires but can never log in
or refresh on its own. See docs/decisions/0006-dev-gateway-credentials.md.

Lease body:
    {"tastytrade": {"session_token": str, "session_expiration": ISO-8601, "is_test": bool},
     "schwab": {"access_token": str, "expires_at": unix seconds, "token_type": "Bearer"} | null}
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from optionsjourney.brokers import guard
from optionsjourney.brokers.credentials.base import AccessToken, CredentialsUnavailable

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 5
# The server re-mints a TastyTrade token with >10 min left; fetch again well
# before ours runs out.
RENEW_BEFORE = timedelta(minutes=2)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GatewayProvider:
    def __init__(
        self,
        url: str,
        secret: str,
        *,
        http: httpx.Client | None = None,
        now: Callable[[], datetime] = _utcnow,
    ):
        # Belt and braces: the app installs the guard at startup too.
        guard.install()
        self._url = url.rstrip("/")
        self._secret = secret
        self._http = http or httpx.Client(timeout=FETCH_TIMEOUT_S)
        self._now = now
        self._lease: dict | None = None
        self._lock = threading.Lock()

    def tastytrade_token(self) -> AccessToken:
        return self._token("tastytrade")

    def schwab_token(self) -> AccessToken:
        return self._token("schwab")

    def _token(self, broker: str) -> AccessToken:
        with self._lock:
            token = self._parse(broker, self._lease)
            if token is None or token.expires_at - self._now() < RENEW_BEFORE:
                self._lease = self._fetch()
                token = self._parse(broker, self._lease)
        if token is None:
            raise CredentialsUnavailable(
                f"The Tracker2 server has no {_LABELS[broker]} token to lend right now."
                + _NO_TOKEN_HINTS.get(broker, "")
            )
        return token

    def _fetch(self) -> dict:
        try:
            response = self._http.get(
                f"{self._url}/gateway/credentials",
                headers={"Authorization": f"Bearer {self._secret}"},
            )
        except httpx.HTTPError as e:
            raise CredentialsUnavailable(f"Can't reach the Tracker2 server at {self._url}.") from e
        if response.status_code == 401:
            raise CredentialsUnavailable("The Tracker2 server rejected GATEWAY_SECRET.")
        if response.status_code == 403:
            raise CredentialsUnavailable(
                "The Tracker2 server only lends tokens on the home network."
            )
        if response.status_code != 200:
            raise CredentialsUnavailable(
                f"The Tracker2 server answered {response.status_code} for a lease."
            )
        logger.info("gateway lease fetched")
        return response.json()

    @staticmethod
    def _parse(broker: str, lease: dict | None) -> AccessToken | None:
        item = (lease or {}).get(broker)
        if not item:
            return None
        if broker == "tastytrade":
            return AccessToken(
                value=item["session_token"],
                expires_at=datetime.fromisoformat(item["session_expiration"]),
                is_test=bool(item.get("is_test")),
            )
        return AccessToken(
            value=item["access_token"],
            expires_at=datetime.fromtimestamp(item["expires_at"], UTC),
        )


_LABELS = {"tastytrade": "TastyTrade", "schwab": "Schwab"}
_NO_TOKEN_HINTS = {
    "schwab": " Its weekly Schwab login may have expired; log in again on the server."
}
