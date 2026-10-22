"""The demo seat manager: hands each visitor a seat (their own copy of the demo app),
queues them when every seat is taken, resets seats after use and keeps private counts.

Caddy sends a request with a valid `oj_seat` cookie straight to its seat; everything
else (no cookie, `/_seat/*`, `/stats`) comes here, on 127.0.0.1:8096. A visit counts once
its visitor reaches the seat (the pool's `visits_began`), so a link preview or crawler
that only fetches `/` isn't counted, and its seat is freed after `NO_SHOW` seconds. A control loop
thread (`run_loop`) starts, checks and stops the seat processes; the decisions are the
pool's (`seats.py`). This process is not in demo mode and never loads the app itself.

    python -m optionsjourney.demo.manager --root <folder>
"""

from __future__ import annotations

import argparse
import hmac
import logging
import os
import signal
import sys
import threading
import time
import weakref
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
from flask import Flask, jsonify, make_response, redirect, render_template, request

from optionsjourney.demo import tour
from optionsjourney.demo.counts import Counts
from optionsjourney.demo.seat_process import SeatProcesses, check
from optionsjourney.demo.seats import COOKIE, Assign, Pool, Reset, Start, State, Stop, Wait

log = logging.getLogger(__name__)

SEAT = COOKIE
WAIT = "oj_wait"
STOP_GIVE_UP = 30  # seconds a seat may stay RESETTING because its stop keeps failing
STATS_DAYS = 14
ROBOTS = "User-agent: *\nDisallow: /\n"
# The manager's own paths; any other GET that reaches it (no seat cookie) goes to /.
_OWN = ("/", "/stats", "/robots.txt")
_OWN_PREFIXES = ("/_seat/", "/static/")

_WEB = Path(__file__).resolve().parent.parent / "web"
_COOKIE = {"path": "/", "secure": True, "httponly": True, "samesite": "Lax"}
_locks: weakref.WeakKeyDictionary[Pool, threading.Lock] = weakref.WeakKeyDictionary()


def _lock_for(pool: Pool) -> threading.Lock:
    """One lock per pool, shared by the web threads and the control loop."""
    return _locks.setdefault(pool, threading.Lock())


# ----- the web side -----


def create_manager(
    pool: Pool,
    processes: SeatProcesses,
    counts: Counts,
    *,
    stats_password: str | None,
    check: Callable = check,
    now: Callable[[], float] = time.monotonic,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(_WEB / "templates"),
        static_folder=str(_WEB / "static"),
        static_url_path="/static",
    )
    lock = _lock_for(pool)

    def starting(place: int) -> bool:
        """A seat is (or will be) coming up for this place without anyone leaving."""
        return place <= pool.cap - pool.assigned()

    def seated(response, got: Assign):
        """The visit (and any wait) is counted by the loop once the visitor shows up."""
        response.set_cookie(SEAT, got.cookie_value, **_COOKIE)
        response.delete_cookie(WAIT, **_COOKIE)
        return response

    def seat_cookie() -> tuple[int, str] | None:
        """The visitor's seat cookie as (seat, token), or None."""
        number, _, token = request.cookies.get(SEAT, "").partition(".")
        if not number.isdigit() or not token:
            return None
        return int(number), token

    @app.before_request
    def own_paths_only():
        """A page of the app without a seat goes to the front door. Anything else (an
        image, a script: /favicon.ico) is a 404, so it can't take a seat on the way."""
        path = request.path
        if request.method == "GET" and path not in _OWN and not path.startswith(_OWN_PREFIXES):
            if request.headers.get("Sec-Fetch-Dest", "document") != "document":
                return "", 404
            return redirect("/")
        return None

    @app.after_request
    def no_index(response):
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    @app.get("/robots.txt")
    def robots():
        return ROBOTS, 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.get("/")
    def arrive():
        token = request.cookies.get(WAIT)
        with lock:
            polled = pool.poll(token, now()) if token else None  # a reload keeps the place
            got = polled or pool.arrive(now())
            ready = isinstance(got, Wait) and starting(got.place)
        if isinstance(got, Assign):
            return seated(redirect("/"), got)
        response = make_response(
            render_template("demo_seat_wait.html", place=got.place, starting=ready)
        )
        response.set_cookie(WAIT, got.token, **_COOKIE)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/_seat/wait")
    def wait():
        token = request.cookies.get(WAIT)
        with lock:
            got = pool.poll(token, now()) if token else None
            ready = isinstance(got, Wait) and starting(got.place)
        if got is None:
            return jsonify(ready=False, gone=True)
        if isinstance(got, Wait):
            return jsonify(ready=False, place=got.place, starting=ready)
        return seated(jsonify(ready=True), got)

    @app.get("/_seat/ended")
    def ended():
        seat = seat_cookie()
        with lock:
            restarting = seat is not None and pool.restarting(*seat)
        if restarting:  # Start a new day: the seat is coming back up for this visitor
            response = make_response(render_template("demo_seat_restarting.html"))
        else:
            response = make_response(render_template("demo_seat_ended.html"))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/_seat/start")
    def start_again():
        """Start again: the old seat, if it is still this visitor's, is freed for others."""
        seat = seat_cookie()
        if seat is not None:
            with lock:
                pool.leave(*seat)
        response = redirect("/")
        response.delete_cookie(SEAT, **_COOKIE)
        return response

    @app.get("/_seat/health")
    def health():
        # Through Caddy every request comes from 127.0.0.1 too, with X-Forwarded-For.
        if request.remote_addr != "127.0.0.1" or "X-Forwarded-For" in request.headers:
            return "", 404
        with lock:
            ready = sum(1 for s in pool.seats if s.state == State.READY and s.assigned_at is None)
            return jsonify(assigned=pool.assigned(), queued=pool.queue_length(), ready=ready)

    @app.get("/stats")
    def stats():
        if not stats_password:
            return "", 404
        auth = request.authorization
        given = (auth.password or "") if auth is not None and auth.type == "basic" else ""
        if not hmac.compare_digest(given.encode(), stats_password.encode()):
            return "", 401, {"WWW-Authenticate": 'Basic realm="stats"'}
        response = make_response(
            render_template(
                "demo_seat_stats.html",
                days=list(reversed(counts.days(STATS_DAYS))),
                steps=range(2, tour.TOTAL + 1),
                finished=tour.FINISHED,
                duration=_duration,
            )
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{round(seconds)} s"
    return f"{round(seconds / 60)} min"


# ----- the control loop -----


class Loop:
    """One pass every couple of seconds: exits, health checks, then the pool's actions.
    Process work (starting, stopping, checking) happens outside the pool's lock, and
    each seat's trouble is logged and kept to that seat."""

    def __init__(
        self,
        pool: Pool,
        processes: SeatProcesses,
        counts: Counts,
        check: Callable,
        *,
        now: Callable[[], float] = time.monotonic,
        client: httpx.Client | None = None,
    ) -> None:
        self.pool = pool
        self.processes = processes
        self.counts = counts
        self.check = check
        self.now = now
        self.client = client or httpx.Client(trust_env=False)
        self.lock = _lock_for(pool)
        self._stopping: dict[int, float] = {}  # seat -> when its Reset/Stop was applied

    def step(self) -> None:
        try:
            self._exits()
            self._checks()
            self._finish_stops()
            self._tick()
        except Exception:
            log.exception("seat manager: a loop pass failed")

    def _exits(self) -> None:
        try:
            exits = self.processes.exited()
        except Exception:
            log.exception("seat manager: could not read seat exits")
            return
        for n, code in exits:
            log.info("seat %d exited with code %d", n, code)
            self._seat(n, lambda n=n, code=code: self._locked(self.pool.exited, n, code))

    def _checks(self) -> None:
        live = (State.STARTING, State.READY, State.ASSIGNED)
        with self.lock:
            seats = [(s.n, s.port, s.token) for s in self.pool.seats if s.state in live]
        for n, port, token in seats:
            self._seat(n, lambda n=n, port=port, token=token: self._check(n, port, token))
        self._count()  # visitors who showed up

    def _check(self, n: int, port: int, token: str | None) -> None:
        if token is None:
            return
        health = self.check(port, token, self.client)
        with self.lock:
            if self.pool.seats[n - 1].token != token:
                return  # the seat moved on while we asked
            t = self.now()
            if health is None or not health.ok:
                self.pool.unhealthy(n, t)
            else:
                self.pool.healthy(n, t)
                self.pool.activity(n, health.idle_seconds, health.tour_furthest, t)

    def _tick(self) -> None:
        with self.lock:
            actions = self.pool.tick(self.now())
        stops = [a for a in actions if isinstance(a, Reset | Stop)]
        for action in stops:  # first, so a stop is retried even if anything below fails
            self._stopping[action.n] = self.now()
        self._count()  # a Reset ends its visit as it's decided
        for action in actions:
            if isinstance(action, Start):
                self._seat(action.n, lambda a=action: self._start(a))
            else:
                if isinstance(action, Reset):
                    log.info("seat %d reset: %s", action.n, action.reason)
                self._seat(action.n, lambda a=action: self._stop(a.n))

    def _start(self, action: Start) -> None:
        port = self.pool.seats[action.n - 1].port
        try:
            self.processes.start(action.n, port, action.token)
        except Exception:
            log.exception("seat %d could not start", action.n)
            self._locked(self.pool.exited, action.n, -1)  # backs off like a crash
            return
        self._locked(self.pool.started, action.n)

    def _stop(self, n: int) -> None:
        self.processes.stop(n)
        self._stopping.pop(n, None)
        self._locked(self.pool.stopped, n)

    def _finish_stops(self) -> None:
        """Seats whose stop failed: try again, and after STOP_GIVE_UP mark them stopped."""
        for n, since in list(self._stopping.items()):
            with self.lock:
                state = self.pool.seats[n - 1].state
            if state != State.RESETTING:
                self._stopping.pop(n, None)
                continue
            if self.now() - since >= STOP_GIVE_UP:
                log.error(
                    "gave up stopping seat %d after %d s; marking it stopped", n, STOP_GIVE_UP
                )
                self._stopping.pop(n, None)
                self._seat(n, lambda n=n: self._locked(self.pool.stopped, n))
            else:
                self._seat(n, lambda n=n: self._stop(n))

    def _locked(self, report: Callable, n: int, *args) -> None:
        with self.lock:
            report(n, *args, self.now())
        self._count()

    def _count(self) -> None:
        """Visits that began and ended, into the counts. Never raises: the counts are
        disposable, and a pass must still carry out its stops."""
        try:
            with self.lock:
                began = self.pool.visits_began()
                ended = [self.pool.visit_ended(s.n) for s in self.pool.seats]
            for begin in began:
                self.counts.visit()
                if begin.waited is not None:
                    self.counts.waited(begin.waited)
            for end in ended:
                if end is not None:
                    self.counts.visit_ended(end.seconds, end.tour_furthest)
        except Exception:
            log.exception("seat manager: could not count visits")

    @staticmethod
    def _seat(n: int, work: Callable[[], None]) -> None:
        try:
            work()
        except Exception:
            log.exception("seat %d: loop work failed", n)


def run_loop(
    pool: Pool,
    processes: SeatProcesses,
    counts: Counts,
    check: Callable,
    stop: threading.Event,
    every: float = 2.0,
    *,
    now: Callable[[], float] = time.monotonic,
) -> None:
    loop = Loop(pool, processes, counts, check, now=now)
    while True:
        loop.step()
        if stop.wait(every):
            return


# ----- running it -----


def stopper(
    stop: threading.Event, processes: SeatProcesses, thread: threading.Thread | None = None
) -> Callable:
    """The SIGTERM/SIGINT handler: end the loop, stop every seat, exit 0."""

    def handle(signum, frame):
        stop.set()
        if thread is not None:
            thread.join(timeout=15)  # a pass may be starting or stopping a seat
        processes.stop_all()
        sys.exit(0)

    return handle


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="The demo seat manager.")
    parser.add_argument("--port", type=int, default=8096)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cap", type=int, default=10)
    parser.add_argument("--warm", type=int, default=2)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("werkzeug", "httpx"):  # no line per request or health check
        logging.getLogger(noisy).setLevel(logging.WARNING)

    pool = Pool(cap=args.cap, warm=args.warm)
    processes = SeatProcesses(args.root)
    processes.clear_stale()
    counts = Counts(args.root / "counts.json", today=date.today)
    app = create_manager(
        pool, processes, counts, stats_password=os.environ.get("DEMO_STATS_PASSWORD") or None
    )

    stop = threading.Event()
    thread = threading.Thread(
        target=run_loop, args=(pool, processes, counts, check, stop), daemon=True
    )
    handler = stopper(stop, processes, thread)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    thread.start()

    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", args.port, app, threaded=True)
    log.info("seat manager on 127.0.0.1:%d, %d seats, %d warm", args.port, args.cap, args.warm)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
