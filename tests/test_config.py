from pathlib import Path

import pytest

from optionsjourney.config import DEFAULT_PORT, ConfigError, load_settings


def test_defaults_to_local_mode():
    settings = load_settings({"OPTIONSJOURNEY_DATA_DIR": "/tmp/oj"})
    assert settings.broker_credentials == "local"
    assert not settings.is_gateway
    assert settings.port == DEFAULT_PORT == 2026  # the year the project started (#46)
    assert settings.database_path == Path("/tmp/oj/optionsjourney.db")


def test_gateway_mode():
    settings = load_settings(
        {
            "BROKER_CREDENTIALS": " Gateway ",
            "GATEWAY_URL": "http://10.0.0.5:8800/",
            "GATEWAY_SECRET": "s3cret",
        }
    )
    assert settings.is_gateway
    assert settings.gateway_url == "http://10.0.0.5:8800"


def test_gateway_mode_needs_url_and_secret():
    with pytest.raises(ConfigError, match="GATEWAY_URL and GATEWAY_SECRET"):
        load_settings({"BROKER_CREDENTIALS": "gateway", "GATEWAY_URL": "http://x"})


def test_rejects_unknown_mode():
    with pytest.raises(ConfigError, match="must be one of"):
        load_settings({"BROKER_CREDENTIALS": "prod"})


def test_rejects_bad_port():
    with pytest.raises(ConfigError, match="PORT"):
        load_settings({"PORT": "eighty"})


def test_listens_only_on_this_computer_by_default():
    settings = load_settings({})
    assert settings.listen_on_network is False
    assert settings.bind_host == "127.0.0.1"


def test_listen_on_network_opens_all_interfaces():
    settings = load_settings({"LISTEN_ON_NETWORK": "yes"})
    assert settings.bind_host == "0.0.0.0"


def test_listen_on_network_rejects_unclear_values():
    with pytest.raises(ConfigError, match="LISTEN_ON_NETWORK"):
        load_settings({"LISTEN_ON_NETWORK": "maybe"})
