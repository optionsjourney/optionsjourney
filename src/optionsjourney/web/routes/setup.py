"""The setup wizard (#15, docs/decisions/0007): a guided first run in the
browser. It can be re-run from Settings, e.g. to add Schwab later."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from optionsjourney.brokers.credentials.local import LocalProvider
from optionsjourney.core.settings import SettingError, section
from optionsjourney.db.models import Tag
from optionsjourney.services import settings, setup
from optionsjourney.web import services

bp = Blueprint("setup", __name__)

STEPS = ("welcome", "brokers", "tastytrade", "schwab", "style", "backups", "done")
TITLES = {
    "welcome": "Welcome",
    "brokers": "Your brokers",
    "tastytrade": "Connect TastyTrade",
    "schwab": "Connect Schwab",
    "style": "How you trade",
    "backups": "Backups",
    "done": "All set",
}


def _steps() -> list[str]:
    """The steps this user goes through: broker steps only for the brokers
    they use, and none at all in development (gateway) mode."""
    local = isinstance(services().credentials, LocalProvider)
    chosen = setup.chosen_brokers(services().session_factory) if local else []
    return [
        step
        for step in STEPS
        if (step not in ("brokers", "tastytrade", "schwab") or local)
        and (step not in ("tastytrade", "schwab") or step in chosen)
    ]


def _next(step: str) -> str:
    steps = _steps()
    later = steps[steps.index(step) + 1 :] if step in steps else []
    return url_for("setup.step", name=later[0] if later else "done")


@bp.get("/setup", defaults={"name": "welcome"})
@bp.get("/setup/<name>")
def step(name):
    if name not in STEPS:
        abort(404)
    s = services()
    local = isinstance(s.credentials, LocalProvider)
    if name != "welcome" and local and not setup.risk_accepted(s.session_factory):
        return redirect(url_for("setup.step", name="welcome"))
    if name == "done":
        # Reaching the end is finishing, even if the user goes off to sync first
        setup.mark_complete(s.session_factory)
    steps = _steps()
    return render_template(
        "setup.html",
        step=name,
        steps=steps,
        titles=TITLES,
        next_url=_next(name),
        brokers=s.credentials if isinstance(s.credentials, LocalProvider) else None,
        chosen=setup.chosen_brokers(s.session_factory),
        styles=setup.STYLES,
        risk_accepted=setup.risk_accepted(s.session_factory),
        current_styles={t.name for t in _tags()},
        backup_section=section("backups"),
        values=settings.get_all(s.session_factory),
        problems={},
    )


@bp.post("/setup/accept")
def accept():
    """The welcome step's box: not financial advice, and the user is responsible for
    every order (decision 0093). Asked once; setup can't go on until it's ticked."""
    if request.form.get("risk") != "1":
        flash("Tick the box to show you understand first.", "warning")
        return redirect(url_for("setup.step", name="welcome"))
    setup.accept_risk(services().session_factory)
    return redirect(_next("welcome"))


@bp.post("/setup/brokers")
def save_brokers():
    setup.choose_brokers(services().session_factory, request.form.getlist("brokers"))
    return redirect(_next("brokers"))


@bp.post("/setup/style")
def save_style():
    setup.apply_styles(services().session_factory, request.form.getlist("styles"))
    return redirect(_next("style"))


@bp.post("/setup/backups")
def save_backups():
    try:
        settings.save_section(services().session_factory, "backups", request.form)
    except SettingError as e:
        for problem in e.problems.values():
            flash(problem, "warning")
        return redirect(url_for("setup.step", name="backups"))
    return redirect(_next("backups"))


@bp.post("/setup/finish")
def finish():
    setup.mark_complete(services().session_factory)
    return redirect(url_for("journal.journal_page"))


def _tags():
    # Straight from the table: building the journal would rebuild every position
    with services().session_factory() as session:
        return list(session.scalars(select(Tag)))
