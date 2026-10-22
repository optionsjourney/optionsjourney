"""Settings, read from the environment (and .env) in one place.

Everything else receives a Settings object; nothing outside this module
reads os.environ."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from platformdirs import user_data_dir

LOCAL = "local"
GATEWAY = "gateway"
DEMO = "demo"  # a pretend broker and market, no network (demo mode)
CREDENTIAL_MODES = (LOCAL, GATEWAY, DEMO)

DEFAULT_PORT = 2026  # the year the project started
# The address this computer's own browser uses. By default the app listens
# only here; LISTEN_ON_NETWORK or the Settings switch opens it (decisions 0063, 0104).
HOST = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"


class ConfigError(Exception):
    """A setting is missing or invalid. The message says how to fix it."""


@dataclass(frozen=True)
class Settings:
    broker_credentials: str
    data_dir: Path
    port: int = DEFAULT_PORT
    gateway_url: str | None = None
    gateway_secret: str | None = None
    listen_on_network: bool = False

    @property
    def bind_host(self) -> str:
        """The address .env alone asks for. The app's server listens on
        services.settings.bind_host, which also follows the Settings page."""
        return ALL_INTERFACES if self.listen_on_network else HOST

    @property
    def is_gateway(self) -> bool:
        return self.broker_credentials == GATEWAY

    @property
    def is_demo(self) -> bool:
        return self.broker_credentials == DEMO

    @property
    def database_path(self) -> Path:
        return self.data_dir / "optionsjourney.db"


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from `env`, or from os.environ after loading .env."""
    if env is None:
        load_dotenv()
        env = os.environ

    mode = (env.get("BROKER_CREDENTIALS") or LOCAL).strip().lower()
    if mode not in CREDENTIAL_MODES:
        raise ConfigError(
            f"BROKER_CREDENTIALS must be one of {', '.join(CREDENTIAL_MODES)} (got {mode!r})."
        )

    gateway_url = (env.get("GATEWAY_URL") or "").strip() or None
    gateway_secret = (env.get("GATEWAY_SECRET") or "").strip() or None
    if mode == GATEWAY and not (gateway_url and gateway_secret):
        raise ConfigError(
            "BROKER_CREDENTIALS=gateway needs GATEWAY_URL and GATEWAY_SECRET in .env."
        )

    port_text = (env.get("PORT") or "").strip()
    try:
        port = int(port_text) if port_text else DEFAULT_PORT
    except ValueError:
        raise ConfigError(f"PORT must be a number (got {port_text!r}).") from None

    network_text = (env.get("LISTEN_ON_NETWORK") or "").strip().lower()
    if network_text not in ("", "yes", "no", "true", "false", "1", "0"):
        raise ConfigError(f"LISTEN_ON_NETWORK must be yes or no (got {network_text!r}).")
    listen_on_network = network_text in ("yes", "true", "1")

    data_dir_text = (env.get("OPTIONSJOURNEY_DATA_DIR") or "").strip()
    data_dir = (
        Path(data_dir_text).expanduser()
        if data_dir_text
        else Path(user_data_dir("OptionsJourney", appauthor=False))
    )

    return Settings(
        broker_credentials=mode,
        data_dir=data_dir,
        port=port,
        gateway_url=gateway_url.rstrip("/") if gateway_url else None,
        gateway_secret=gateway_secret,
        listen_on_network=listen_on_network,
    )
