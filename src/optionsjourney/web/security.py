"""Refuse state-changing requests that didn't come from the app's own pages.

The app has no login and listens only on this computer, but a web page open in
the same browser could still try to POST to it. Browsers send an Origin (or at
least a Referer) header with such requests; anything not from this app is
refused."""

from collections.abc import Mapping
from urllib.parse import urlsplit

from flask import Flask, abort, request

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def same_origin(headers: Mapping[str, str], host: str) -> bool:
    """Whether a request came from a page at `host`, by its Origin or Referer.
    Also used by the start-up problem page (launcher/startup_problem.py)."""
    source = headers.get("Origin") or headers.get("Referer")
    if not source:
        return False
    return urlsplit(source).netloc == host


def local_path(value: str | None) -> str | None:
    """`value` when it is a path within this app (where to go back to after a form),
    else None: never another site, so a link can't send the user off it."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    if urlsplit(value).netloc:
        return None
    return value


def register(app: Flask) -> None:
    @app.before_request
    def _check_origin():
        if request.method not in _SAFE_METHODS and not same_origin(request.headers, request.host):
            abort(403)
