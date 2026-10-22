"""The app's version comes from pyproject.toml, the one place it's set."""

import tomllib
from pathlib import Path

import optionsjourney


def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert optionsjourney.__version__ == expected
