"""Refuse state-changing requests that didn't come from the app's own pages.

The app has no login and listens only on this computer, but a web page open in
the same browser could still try to POST to it. Browsers send an Origin (or at
least a Referer) header with such requests; anything not from this app is
refused."""

from urllib.parse import urlsplit

from flask import Flask, abort, request

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _same_origin() -> bool:
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return False
    return urlsplit(source).netloc == request.host


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
        if request.method not in _SAFE_METHODS and not _same_origin():
            abort(403)
