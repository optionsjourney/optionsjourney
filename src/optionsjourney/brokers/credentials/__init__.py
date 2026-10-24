"""Where broker access tokens come from, chosen by BROKER_CREDENTIALS."""

from optionsjourney.brokers.credentials.base import (
    AccessToken,
    CredentialProvider,
    CredentialsUnavailable,
    NotConnected,
)
from optionsjourney.config import Settings

__all__ = [
    "AccessToken",
    "CredentialProvider",
    "CredentialsUnavailable",
    "NotConnected",
    "provider_for",
]


def provider_for(settings: Settings) -> CredentialProvider:
    if settings.is_demo:
        # The demo's pretend tokens: demo mode never reaches the keychain or the gateway
        from optionsjourney.demo.credentials import DemoCredentials

        return DemoCredentials()
    if settings.is_gateway:
        from optionsjourney.brokers.credentials.gateway import GatewayProvider

        return GatewayProvider(settings.gateway_url, settings.gateway_secret)
    from optionsjourney.brokers.credentials.local import LocalProvider
    from optionsjourney.brokers.credentials.store import KeyringStore

    return LocalProvider(KeyringStore())
