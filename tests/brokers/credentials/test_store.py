import json

import pytest

from optionsjourney.brokers.credentials.store import SERVICE, KeyringStore, MemoryStore


def test_memory_store_round_trip():
    store = MemoryStore()
    assert store.get("tastytrade") is None
    store.set("tastytrade", {"refresh_token": "r", "client_secret": "s"})
    assert store.get("tastytrade") == {"refresh_token": "r", "client_secret": "s"}
    store.delete("tastytrade")
    assert store.get("tastytrade") is None


class FakeKeyring:
    """Stands in for the system keychain, so tests never touch the real one."""

    def __init__(self):
        self.items = {}

    def get_password(self, service, user):
        return self.items.get((service, user))

    def set_password(self, service, user, value):
        self.items[(service, user)] = value

    def delete_password(self, service, user):
        self.items.pop((service, user), None)


def test_keyring_store_keeps_one_json_item_per_broker():
    backend = FakeKeyring()
    store = KeyringStore(backend)
    store.set("schwab", {"app_key": "k", "app_secret": "s", "refresh_token": "r"})
    assert json.loads(backend.items[(SERVICE, "schwab")]) == {
        "app_key": "k",
        "app_secret": "s",
        "refresh_token": "r",
    }
    assert store.get("schwab")["app_key"] == "k"
    store.delete("schwab")
    assert store.get("schwab") is None


def test_unknown_broker_is_refused():
    with pytest.raises(ValueError):
        MemoryStore().set("robinhood", {})
