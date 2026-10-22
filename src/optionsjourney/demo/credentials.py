"""Pretend credentials for demo mode: a token that is always valid."""

from datetime import UTC, datetime, timedelta

from optionsjourney.brokers.credentials.base import AccessToken

_LIFETIME = timedelta(days=365)


class DemoCredentials:
    """Satisfies CredentialProvider. Nothing is looked up or refreshed."""

    def _token(self) -> AccessToken:
        return AccessToken("demo", datetime.now(UTC) + _LIFETIME, is_test=True)

    def tastytrade_token(self) -> AccessToken:
        return self._token()

    def schwab_token(self) -> AccessToken:
        return self._token()
