"""Where the user's own broker logins are kept: the system keychain (macOS
Keychain, Windows Credential Manager), never the database or a file, so
backups and copies of the database never carry them (#18).

Each broker's secrets are one JSON item in the keychain, service
"OptionsJourney", account named after the broker."""

import json
from typing import Protocol

SERVICE = "OptionsJourney"
BROKERS = ("tastytrade", "schwab")


class SecretStore(Protocol):
    def get(self, broker: str) -> dict | None: ...

    def set(self, broker: str, secrets: dict) -> None: ...

    def delete(self, broker: str) -> None: ...


def _check(broker: str) -> None:
    if broker not in BROKERS:
        raise ValueError(f"unknown broker {broker!r}")


def default_backend():
    """The system keychain. Tests replace this so they never touch it."""
    import keyring

    return keyring


class KeyringStore:
    """The system keychain, through the `keyring` library."""

    def __init__(self, backend=None):
        self._keyring = backend or default_backend()

    def get(self, broker: str) -> dict | None:
        _check(broker)
        text = self._keyring.get_password(SERVICE, broker)
        return json.loads(text) if text else None

    def set(self, broker: str, secrets: dict) -> None:
        _check(broker)
        self._keyring.set_password(SERVICE, broker, json.dumps(secrets))

    def delete(self, broker: str) -> None:
        _check(broker)
        if self._keyring.get_password(SERVICE, broker) is not None:
            self._keyring.delete_password(SERVICE, broker)


class MemoryStore:
    """For tests: kept in memory, gone when the process ends."""

    def __init__(self):
        self._items: dict[str, dict] = {}

    def get(self, broker: str) -> dict | None:
        _check(broker)
        item = self._items.get(broker)
        return dict(item) if item else None

    def set(self, broker: str, secrets: dict) -> None:
        _check(broker)
        self._items[broker] = dict(secrets)

    def delete(self, broker: str) -> None:
        _check(broker)
        self._items.pop(broker, None)
