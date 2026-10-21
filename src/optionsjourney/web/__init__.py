"""Pages and API."""

from flask import current_app


def services():
    """The app's Services (see optionsjourney.app)."""
    return current_app.extensions["optionsjourney"]
