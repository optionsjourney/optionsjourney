"""What every credential provider hands out: a short-lived access token."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class CredentialsUnavailable(Exception):
    """No usable token right now. The message is shown to the user."""


class NotConnected(CredentialsUnavailable):
    """The broker was never connected (or was disconnected): there is nothing
    to read from it, which isn't the same as it failing to answer."""


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_at: datetime
    is_test: bool = False


class CredentialProvider(Protocol):
    """Source of broker access tokens. Callers ask for a token before each
    request; providers cache and renew as needed."""

    def tastytrade_token(self) -> AccessToken: ...

    def schwab_token(self) -> AccessToken: ...
