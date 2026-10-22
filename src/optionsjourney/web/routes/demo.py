"""Demo mode's own routes (Skip ahead, Start a new day) and `not_in_demo`, which
switches off the features that would reach outside the demo.

The blueprint is registered only in demo mode (create_app)."""

import functools
import hmac
import logging
import os
import threading
import time
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
)
from werkzeug.http import parse_cookie

from optionsjourney.demo.clock import NY
from optionsjourney.demo.seats import COOKIE
from optionsjourney.web import services

logger = logging.getLogger(__name__)
bp = Blueprint("demo", __name__)

NOT_IN_DEMO = "Not in the demo"
SKIP_MINUTES = 15
_EXIT_DELAY = 1.0  # seconds: the "Starting a new day…" page reaches the browser first
SEAT_ENDED = "/_seat/ended"
_NOT_ACTIVITY = ("/socket.io/", "/static/", "/demo/activity", "/health")  # polls, not people
_NOT_A_PAGE = ("/api/", "/demo/tour")  # data pages fetch (and the tour box polls) by GET
_last_activity = time.monotonic()  # monotonic: the demo clock moves time.time(), not this


def _back():
    """A redirect to the page the form was on (this app's pages only), else the journal."""
    source = urlsplit(request.referrer or "")
    if source.netloc == request.host and source.path.startswith("/"):
        return redirect(source.path + (f"?{source.query}" if source.query else ""))
    return redirect("/")


def not_in_demo(view):
    """In demo mode the view never runs: a page's form gets "Not in the demo" and goes
    back where it came from, an /api/ call gets a 403. Outside demo mode, nothing changes."""

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not services().settings.is_demo:
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify(error=NOT_IN_DEMO), 403
        flash(f"{NOT_IN_DEMO}.", "warning")
        return _back()

    return wrapper


@bp.app_context_processor
def _demo_banner():
    demo = services().demo
    if demo is None:
        return {}
    now = demo.clock.now().astimezone(NY)
    return {"demo_over": demo.clock.is_over(), "demo_time": now.strftime("%I:%M %p").lstrip("0")}


def _seat_token() -> str:
    """The seat's token, set in the environment only by the seat manager ("" = no seats)."""
    return os.environ.get("DEMO_SEAT_TOKEN", "")


def _matches(token: str, given: str) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), token.encode())


def _cookie_ok(token: str, cookie: str) -> bool:
    """The cookie is "<seat>.<token>": only the part after the first "." is compared. An
    old cookie from a seat's earlier visitor carries another token."""
    return _matches(token, cookie.partition(".")[2])


@bp.before_app_request
def _check_seat_token():
    """Under the seat manager, only the seat's current visitor gets in. Only a request
    that got in this way can count as their activity (`g.seat_visitor`)."""
    token = _seat_token()
    if not token or _cookie_ok(token, request.cookies.get(COOKIE, "")):
        g.seat_visitor = True
        return None
    if request.path == SEAT_ENDED:  # where turned-away requests go: let it answer
        return None
    if request.path == "/demo/activity":  # the manager asks with the token in a header
        if _matches(token, request.headers.get("X-Seat-Token", "")):
            return None
        abort(403)
    return redirect(SEAT_ENDED)


def gate_sockets(app) -> None:
    """Socket.IO answers before Flask's request hooks run, so it gets the same token check
    one layer out: the wrong cookie gets a 403. Call after the sockets are registered."""
    inner = app.wsgi_app

    def gated(environ, start_response):
        token = _seat_token()
        if token and environ.get("PATH_INFO", "").startswith("/socket.io/"):
            cookie = parse_cookie(environ.get("HTTP_COOKIE", "")).get(COOKIE, "")
            if not _cookie_ok(token, cookie):
                start_response("403 Forbidden", [("Content-Type", "text/plain")])
                return [b"This demo seat has ended."]
        return inner(environ, start_response)

    app.wsgi_app = gated


def _is_activity(response) -> bool:
    """Whether this request was the visitor doing something: opening a page (a GET that
    answers HTML, not an /api/ call or the tour box's poll) or sending anything (a form,
    a click; not HEAD or OPTIONS). Pages poll for data by GET, so those never count.
    A request the seat check turned away (an earlier visitor's cookie) never counts."""
    if not g.get("seat_visitor"):
        return False
    path = request.path
    if path.startswith(_NOT_ACTIVITY):
        return False
    if request.method == "GET":
        fetched = request.headers.get("Sec-Fetch-Mode", "navigate") != "navigate"  # by script
        return not fetched and not path.startswith(_NOT_A_PAGE) and response.mimetype == "text/html"
    return request.method not in ("HEAD", "OPTIONS")


@bp.after_app_request
def _note_activity(response):
    global _last_activity  # noqa: PLW0603 -- one seat per process
    if _is_activity(response):
        _last_activity = time.monotonic()
    return response


@bp.get(SEAT_ENDED)
def seat_ended():
    return "This demo session has ended.", 403


@bp.get("/demo/activity")
def activity():
    demo = services().demo
    with demo.tour_lock:
        furthest = demo.tour.furthest
    return jsonify(
        idle_seconds=time.monotonic() - _last_activity,
        tour_furthest=furthest,
        day_over=demo.clock.is_over(),
    )


@bp.before_app_request
def _record_visit():
    demo = services().demo
    if (
        demo is not None
        and request.method == "GET"
        and not request.path.startswith(_NOT_ACTIVITY)
    ):
        demo.visited.record(request.path)


def _view(demo, enter=None):
    """The tour box's JSON, after `enter(tour)` (if any) and the facts. Reaching step 5
    brings the calendar's target forward."""
    from optionsjourney.demo.tour_facts import facts  # noqa: PLC0415 -- pulls in the demo seed

    with demo.tour_lock:
        before = demo.tour.current
        if enter is not None:
            enter(demo.tour)
        current = facts(services().session_factory, demo.visited, demo.clock)
        demo.tour.update(current)
        step = demo.tour.current
        view = demo.tour.view(current)
    if before < 5 <= step:
        demo.director.on_tour_step(5)
    return view


def _tour_reply(demo, enter=None):
    view = _view(demo, enter)
    if request.is_json:
        return jsonify(view)
    return _back()


@bp.get("/demo/tour")
def tour():
    return jsonify(_view(services().demo))


@bp.post("/demo/tour/next")
def tour_next():
    payload = request.get_json(silent=True)
    raw = payload.get("from") if isinstance(payload, dict) else request.form.get("from")
    try:
        from_step = int(raw)
    except TypeError, ValueError:
        abort(400)
    return _tour_reply(services().demo, lambda t: t.next(from_step))


@bp.post("/demo/tour/skip")
def tour_skip():
    return _tour_reply(services().demo, lambda t: t.skip())


@bp.post("/demo/tour/restart")
def tour_restart():
    from optionsjourney.demo.tour_facts import facts  # noqa: PLC0415 -- pulls in the demo seed

    demo = services().demo
    # The facts as they are now: the steps already done are held until Next.
    current = facts(services().session_factory, demo.visited, demo.clock)
    return _tour_reply(demo, lambda t: t.restart(current))


@bp.post("/demo/skip")
def skip():
    clock = services().demo.clock
    if request.form.get("to") == "close":
        clock.to_close()
    else:
        try:
            minutes = int(request.form.get("minutes", SKIP_MINUTES))
        except ValueError:
            abort(400)
        if minutes != SKIP_MINUTES:
            abort(400)
        clock.skip(minutes)
    return _back()


def exit_soon(code: int) -> None:
    """End the process with `code` a second from now. The route calls this once its
    response has closed. The demo runner (demo/__main__.py) starts a new day on NEW_DAY."""
    timer = threading.Timer(_EXIT_DELAY, os._exit, args=(code,))
    timer.daemon = False
    timer.start()


@bp.post("/demo/new-day")
def new_day():
    from optionsjourney.demo.__main__ import NEW_DAY  # noqa: PLC0415 -- the runner, demo only

    logger.info("Starting a new demo day")
    response = make_response(render_template("demo_new_day.html"))
    # The exit timer starts only once the page has gone out, so it can't fire first.
    response.call_on_close(lambda: exit_soon(NEW_DAY))
    return response
