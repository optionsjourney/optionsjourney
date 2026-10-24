"""The user's own broker logins, kept in the system keychain (#18).

TastyTrade (#16): an OAuth client secret plus a refresh token, which the
user creates once on TastyTrade's site. The refresh token never changes; each
short-lived access token (15 minutes) is minted from it with
POST /oauth/token. Using it from a second computer doesn't sign the first one
out.

Schwab (#17): see the Schwab section below."""

import base64
import logging
import threading
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx

from optionsjourney.brokers.credentials.base import (
    AccessToken,
    CredentialsUnavailable,
    NotConnected,
)
from optionsjourney.brokers.credentials.store import SecretStore

logger = logging.getLogger(__name__)

TASTYTRADE_URL = "https://api.tastyworks.com"
SCHWAB_AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
SCHWAB_LOGIN_LASTS = timedelta(days=7)
TASTYTRADE_SANDBOX_URL = "https://api.cert.tastyworks.com"
RENEW_BEFORE = timedelta(minutes=1)
RETRY_AFTER = timedelta(seconds=30)  # after a failed refresh, don't ask again this soon
TIMEOUT_S = 10

_NOT_CONNECTED = "{broker} isn't connected. Connect it in Settings → Brokers."


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LocalProvider:
    def __init__(
        self,
        store: SecretStore,
        *,
        http: httpx.Client | None = None,
        now: Callable[[], datetime] = _utcnow,
    ):
        self.store = store
        self._http = http or httpx.Client(timeout=TIMEOUT_S)
        self._now = now
        self._tokens: dict[str, AccessToken] = {}
        # One login lock per broker, so one broker's slow token server never holds
        # up the other. Each guards its own broker's store key, cached token and
        # back-off entry; nothing is shared between the two.
        self._locks = {"tastytrade": threading.Lock(), "schwab": threading.Lock()}
        # broker -> (when the last refresh failed, the message shown)
        self._failed: dict[str, tuple[datetime, str]] = {}

    # --- TastyTrade -------------------------------------------------------------

    def tastytrade_token(self) -> AccessToken:
        with self._locks["tastytrade"]:
            cached = self._tokens.get("tastytrade")
            if cached and cached.expires_at - self._now() > RENEW_BEFORE:
                return cached
            secrets = self.store.get("tastytrade")
            if not secrets:
                raise NotConnected(_NOT_CONNECTED.format(broker="TastyTrade"))
            self._raise_if_backing_off("tastytrade")
            try:
                token = self._mint_tastytrade(secrets)
            except CredentialsUnavailable as e:
                self._failed["tastytrade"] = (self._now(), str(e))
                raise
            self._failed.pop("tastytrade", None)
            self._tokens["tastytrade"] = token
            return token

    def _raise_if_backing_off(self, broker: str) -> None:
        """A refresh failed a moment ago: say the same thing again without asking
        the broker. Called with the broker's lock held."""
        failed = self._failed.get(broker)
        if failed and self._now() - failed[0] < RETRY_AFTER:
            raise CredentialsUnavailable(failed[1])

    def connect_tastytrade(self, client_secret: str, refresh_token: str, *, sandbox: bool) -> None:
        """Check the login with TastyTrade, then keep it. Raises
        CredentialsUnavailable (and keeps nothing) if TastyTrade refuses it."""
        secrets = {
            "client_secret": (client_secret or "").strip(),
            "refresh_token": (refresh_token or "").strip(),
            "sandbox": bool(sandbox),
        }
        if not secrets["client_secret"] or not secrets["refresh_token"]:
            raise CredentialsUnavailable("Paste both the client secret and the refresh token.")
        token = self._mint_tastytrade(secrets)
        with self._locks["tastytrade"]:
            self.store.set("tastytrade", secrets)
            self._tokens["tastytrade"] = token
            self._failed.pop("tastytrade", None)

    def _mint_tastytrade(self, secrets: dict) -> AccessToken:
        sandbox = bool(secrets.get("sandbox"))
        base = TASTYTRADE_SANDBOX_URL if sandbox else TASTYTRADE_URL
        try:
            response = self._http.post(
                f"{base}/oauth/token",
                json={
                    "grant_type": "refresh_token",
                    "client_secret": secrets["client_secret"],
                    "refresh_token": secrets["refresh_token"],
                },
            )
        except httpx.HTTPError as e:
            raise CredentialsUnavailable("Can't reach TastyTrade to log in.") from e
        if response.status_code in (400, 401, 403):
            raise CredentialsUnavailable(
                "TastyTrade didn't accept the saved login. Reconnect it in Settings → Brokers."
            )
        if response.status_code != 200:
            raise CredentialsUnavailable(
                f"TastyTrade answered {response.status_code} to the login."
            )
        data = _json_with_token(response, "TastyTrade")
        logger.info("TastyTrade access token minted")
        return AccessToken(
            value=data["access_token"],
            expires_at=self._now() + timedelta(seconds=int(data.get("expires_in", 900))),
            is_test=sandbox,
        )

    # --- Schwab (#17) -------------------------------------------------------------
    # The user registers an app on Schwab's developer site (app key, secret and
    # a callback URL such as https://127.0.0.1). Logging in sends the browser
    # to that callback with a one-time code in the address; nothing listens
    # there, so the user pastes the address back and the code is traded for
    # tokens. Schwab's refresh token lasts 7 days, then the user logs in again.

    @staticmethod
    def schwab_login_url(app_key: str, callback_url: str) -> str:
        query = urllib.parse.urlencode({"client_id": app_key, "redirect_uri": callback_url})
        return f"{SCHWAB_AUTHORIZE_URL}?{query}"

    def start_schwab(self, app_key: str, app_secret: str, callback_url: str) -> str:
        """Keep the app's details (or reuse the kept ones when blank) and
        return the Schwab login link."""
        kept = self.store.get("schwab") or {}
        app = {
            "app_key": (app_key or "").strip() or kept.get("app_key", ""),
            "app_secret": (app_secret or "").strip() or kept.get("app_secret", ""),
            "callback_url": (callback_url or "").strip() or kept.get("callback_url", ""),
        }
        if not all(app.values()):
            raise CredentialsUnavailable("Enter the app key, the secret and the callback URL.")
        if not app["callback_url"].startswith("https://"):
            raise CredentialsUnavailable(
                "The callback URL starts with https://, as on Schwab's site."
            )
        with self._locks["schwab"]:
            self.store.set("schwab", {**kept, **app})
        return self.schwab_login_url(app["app_key"], app["callback_url"])

    def finish_schwab(self, redirected_url: str) -> None:
        """Trade the code in the pasted address for tokens and keep them."""
        app = self.store.get("schwab") or {}
        if not app.get("app_key"):
            raise CredentialsUnavailable("Start by entering the Schwab app's details.")
        redirected_url = (redirected_url or "").strip()
        if not redirected_url.startswith(app["callback_url"]):
            raise CredentialsUnavailable(
                "Paste the address you land on after logging in. "
                f"It starts with {app['callback_url']}."
            )
        code = urllib.parse.parse_qs(urllib.parse.urlparse(redirected_url).query).get("code")
        if not code:
            raise CredentialsUnavailable(
                "That address has no code in it. Copy the whole address after logging in."
            )
        data = self._schwab_post(
            app,
            {
                "grant_type": "authorization_code",
                "code": code[0],
                "redirect_uri": app["callback_url"],
            },
            refused=(
                "Schwab didn't accept that login. Codes only last about 30 seconds: "
                "log in again and paste straight away."
            ),
        )
        token = self._schwab_access(data)
        if not data.get("refresh_token"):
            raise CredentialsUnavailable("Schwab sent a login reply the app couldn't read.")
        with self._locks["schwab"]:
            self.store.set(
                "schwab",
                {
                    **app,
                    "refresh_token": data["refresh_token"],
                    "logged_in_at": self._now().isoformat(),
                },
            )
            self._tokens["schwab"] = token
            self._failed.pop("schwab", None)

    def schwab_token(self) -> AccessToken:
        with self._locks["schwab"]:
            cached = self._tokens.get("schwab")
            if cached and cached.expires_at - self._now() > RENEW_BEFORE:
                return cached
            app = self.store.get("schwab") or {}
            if not app.get("refresh_token"):
                raise NotConnected(_NOT_CONNECTED.format(broker="Schwab"))
            if self._now() >= _logged_in_at(app) + SCHWAB_LOGIN_LASTS:
                raise CredentialsUnavailable(
                    "It's been 7 days: log in to Schwab again in Settings → Brokers "
                    "(Reconnect Schwab)."
                )
            self._raise_if_backing_off("schwab")
            try:
                data = self._schwab_post(
                    app,
                    {"grant_type": "refresh_token", "refresh_token": app["refresh_token"]},
                    refused=(
                        "Schwab didn't accept the saved login. "
                        "Reconnect Schwab in Settings → Brokers."
                    ),
                )
                token = self._schwab_access(data)
            except CredentialsUnavailable as e:
                self._failed["schwab"] = (self._now(), str(e))
                raise
            self._failed.pop("schwab", None)
            if data.get("refresh_token") and data["refresh_token"] != app["refresh_token"]:
                self.store.set("schwab", {**app, "refresh_token": data["refresh_token"]})
            self._tokens["schwab"] = token
            return token

    def schwab_login_expires_at(self) -> datetime | None:
        """When the Schwab login runs out and the user must log in again."""
        app = self.store.get("schwab") or {}
        if not app.get("refresh_token"):
            return None
        return _logged_in_at(app) + SCHWAB_LOGIN_LASTS

    def _schwab_post(self, app: dict, form: dict, refused: str) -> dict:
        basic = base64.b64encode(f"{app['app_key']}:{app['app_secret']}".encode()).decode()
        try:
            response = self._http.post(
                SCHWAB_TOKEN_URL, headers={"Authorization": f"Basic {basic}"}, data=form
            )
        except httpx.HTTPError as e:
            raise CredentialsUnavailable("Can't reach Schwab to log in.") from e
        if response.status_code != 200:
            reason = _reason(response)
            logger.warning(
                "Schwab refused %s (%s): %s", form["grant_type"], response.status_code, reason
            )
            if response.status_code in (400, 401, 403):
                raise CredentialsUnavailable(f"{refused} Schwab said: {reason}")
            raise CredentialsUnavailable(f"Schwab answered {response.status_code}: {reason}")
        logger.info("Schwab token call: %s", form["grant_type"])
        return _json_with_token(response, "Schwab")

    def _schwab_access(self, data: dict) -> AccessToken:
        return AccessToken(
            value=data["access_token"],
            expires_at=self._now() + timedelta(seconds=int(data.get("expires_in", 1800))),
        )

    # --- Both -----------------------------------------------------------------------

    def connected(self, broker: str) -> bool:
        """Logged in: for Schwab, past the login step, not just the app details."""
        secrets = self.store.get(broker)
        return bool(secrets and secrets.get("refresh_token"))

    def disconnect(self, broker: str) -> None:
        with self._locks[broker]:
            self.store.delete(broker)
            self._tokens.pop(broker, None)
            self._failed.pop(broker, None)


def _json_with_token(response: httpx.Response, broker: str) -> dict:
    """The token reply's body, or CredentialsUnavailable if it isn't JSON or has
    no access token (a login page, a proxy's error page...)."""
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict) or not data.get("access_token"):
        raise CredentialsUnavailable(f"{broker} sent a login reply the app couldn't read.")
    return data


def _logged_in_at(app: dict) -> datetime:
    return datetime.fromisoformat(app["logged_in_at"])


def _reason(response: httpx.Response) -> str:
    """The broker's own explanation of a refusal, short enough to show."""
    try:
        body = response.json()
        text = body.get("error_description") or body.get("error") or str(body)
    except ValueError:
        text = response.text
    return " ".join(str(text).split())[:200] or f"HTTP {response.status_code}"
