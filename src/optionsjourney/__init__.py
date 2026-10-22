"""OptionsJourney: a local-first options trading journal and automation app."""

from importlib.metadata import PackageNotFoundError, version

# pyproject.toml is the one place the version is set; this reads it from the install.
try:
    __version__ = version("optionsjourney")
except PackageNotFoundError:  # running from a copy that was never installed
    __version__ = "0+unknown"
