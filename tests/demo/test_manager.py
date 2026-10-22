"""The seat manager: its pages, cookies, /stats and the control loop, with fake seat
processes, a fake health check and a fake clock."""

from __future__ import annotations

import base64
import itertools
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from optionsjourney.demo import manager
from optionsjourney.demo.counts import Counts
from optionsjourney.demo.seat_process import Health
from optionsjourney.demo.seats import IDLE_LIMIT, NO_SHOW, Pool, State

DAY = date(2026, 10, 22)


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeProcesses:
    def __init__(self) -> None:
        self.running: dict[int, str] = {}
        self.stopped: list[int] = []
        self.exits: list[tuple[int, int]] = []
        self.stop_all_called = False
        self.fail_stop: set[int] = set()

    def start(self, n, port, token, *, keep_data=False):
        self.running[n] = token

    def stop(self, n, timeout=10):
        if n in self.fail_stop:
            raise OSError("stuck")
        self.running.pop(n, None)
        self.stopped.append(n)

    def exited(self):
        done, self.exits = self.exits, []
        for n, _ in done:
            self.running.pop(n, None)
        return done

    def clear_stale(self):
        pass

    def stop_all(self):
        self.stop_all_called = True
        self.running.clear()


class FakeCheck:
    """Every running seat answers ok, idle `idle` seconds, at tour step `step`."""

    def __init__(self, processes: FakeProcesses) -> None:
        self.processes = processes
        self.idle = 0.0
        self.step = 1
        self.down: set[int] = set()

    def __call__(self, port, token, client):
        n = port - 5100
        if self.processes.running.get(n) != token or n in self.down:
            return None
        return Health(ok=True, idle_seconds=self.idle, tour_furthest=self.step)


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    counter = itertools.count(1)
    pool = Pool(cap=2, warm=1, tokens=lambda: f"t{next(counter)}")
    processes = FakeProcesses()
    fake_check = FakeCheck(processes)
    counts = Counts(tmp_path / "counts.json", today=lambda: DAY)
    loop = manager.Loop(pool, processes, counts, fake_check, now=clock)

    w = SimpleNamespace(
        clock=clock, pool=pool, processes=processes, check=fake_check, counts=counts, loop=loop
    )

    def app(password=None):
        a = manager.create_manager(
            pool, processes, counts, stats_password=password, check=fake_check, now=clock
        )
        a.config["TESTING"] = True
        return a.test_client()

    w.app = app
    return w


def cookie_header(response, name):
    return next(h for h in response.headers.getlist("Set-Cookie") if h.startswith(f"{name}="))


def assert_private(header):
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=Lax" in header
    assert "Path=/" in header


def test_arrival_gets_a_seat_cookie(world):
    world.loop.step()
    world.loop.step()  # started, then healthy: seat 1 is READY
    assert world.pool.seats[0].state == State.READY
    r = world.app().get("/")
    assert r.status_code == 302 and r.headers["Location"] == "/"
    header = cookie_header(r, "oj_seat")
    assert header.startswith("oj_seat=1.t1;")
    assert_private(header)
    assert not world.counts.days()  # no visit until the visitor reaches the seat
    world.clock.t = 3
    world.loop.step()  # the seat reports a request since it was handed out
    assert world.counts.days()[-1].visits == 1


def test_full_house_shows_the_queue(world):
    for _ in range(2):  # both seats (the cap) come up and are taken
        world.loop.step()
        world.loop.step()
        assert world.app().get("/").status_code == 302
    r = world.app().get("/")
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "<h1 id=\"busy\">The demo's busy right now. You're next in line</h1>" in page
    assert 'id="place">1<' in page
    assert_private(cookie_header(r, "oj_wait"))
    assert "Getting your demo ready…" in page  # there, hidden, for when a seat frees up


def test_cold_start_shows_getting_ready(world):
    r = world.app().get("/")
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert '<h1 id="ready">Getting your demo ready…</h1>' in page
    assert '<h1 id="busy" hidden>' in page


def test_reloading_the_wait_page_keeps_the_place(world):
    client = world.app()
    client.get("/")
    client.get("/")
    assert world.pool.queue_length() == 1


def test_wait_poll_hands_over_a_seat_and_counts_the_wait(world):
    client = world.app()
    client.get("/")  # cold: queued
    world.clock.t = 1
    r = client.get("/_seat/wait")
    assert r.get_json() == {"ready": False, "place": 1, "starting": True}
    world.loop.step()
    world.clock.t = 5
    world.loop.step()
    r = client.get("/_seat/wait")
    assert r.get_json() == {"ready": True}
    assert cookie_header(r, "oj_seat").startswith("oj_seat=1.")
    gone = cookie_header(r, "oj_wait")
    assert "Max-Age=0" in gone or "expires=Thu, 01 Jan 1970" in gone
    assert not world.counts.days()
    world.clock.t = 7
    world.loop.step()  # the visitor reaches the seat: the visit and its wait count
    day = world.counts.days()[-1]
    assert (day.visits, day.waited, day.wait_seconds) == (1, 1, 5)


def test_unknown_wait_token_is_gone(world):
    client = world.app()
    assert client.get("/_seat/wait").get_json() == {"ready": False, "gone": True}
    client.set_cookie("oj_wait", "nope")
    assert client.get("/_seat/wait").get_json() == {"ready": False, "gone": True}


def seated(world, at=3.0):
    """Seat 1 up, handed to a visitor (their client), who then reaches it."""
    world.loop.step()
    world.loop.step()
    client = world.app()
    client.get("/")
    world.clock.t = at
    world.loop.step()
    return client


def test_ended_page_and_start_again(world):
    client = world.app()
    page = client.get("/_seat/ended").get_data(as_text=True)
    assert "Your demo session ended. Start again?" in page
    assert 'action="/_seat/start"' in page and 'method="post"' in page
    client.set_cookie("oj_seat", "1.old")
    r = client.post("/_seat/start")
    assert r.status_code == 302 and r.headers["Location"] == "/"
    header = cookie_header(r, "oj_seat")
    assert header.startswith("oj_seat=;")
    assert_private(header)


def test_pages_render_their_styles_without_a_seat(world):
    r = world.app().get("/static/css/demo.css")
    assert r.status_code == 200 and b".seat-page" in r.data


def test_health_is_local_only(world):
    client = world.app()
    assert client.get("/_seat/health", environ_base={"REMOTE_ADDR": "10.0.0.5"}).status_code == 404
    proxied = client.get("/_seat/health", headers={"X-Forwarded-For": "203.0.113.9"})
    assert proxied.status_code == 404  # through Caddy: from 127.0.0.1, but not local
    r = client.get("/_seat/health")
    assert r.get_json() == {"assigned": 0, "queued": 0, "ready": 0}


def basic(password):
    return {"Authorization": "Basic " + base64.b64encode(f"me:{password}".encode()).decode()}


def test_stats_needs_the_password(world):
    assert world.app().get("/stats", headers=basic("x")).status_code == 404
    client = world.app(password="s3cret")
    r = client.get("/stats")
    assert r.status_code == 401 and r.headers["WWW-Authenticate"].startswith("Basic")
    assert client.get("/stats", headers=basic("wrong")).status_code == 401
    world.counts.visit()
    world.counts.waited(30)
    world.counts.visit_ended(600, 8)
    r = client.get("/stats", headers=basic("s3cret"))
    page = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Visits" in page and "2026-10-22" in page
    assert "30 s" in page and "10 min" in page and "100%" in page
    assert r.headers["Cache-Control"] == "no-store"


def test_loop_resets_an_idle_seat_and_counts_the_visit(world):
    world.loop.step()
    world.loop.step()
    world.app().get("/")
    world.check.step = 4
    world.clock.t = 30
    world.loop.step()
    world.check.idle = IDLE_LIMIT + 1
    world.clock.t = IDLE_LIMIT + 31
    world.loop.step()
    assert 1 in world.processes.stopped
    assert world.pool.seats[0].state != State.ASSIGNED
    assert world.pool.seats[0].token != "t1"
    day = world.counts.days()[-1]
    assert day.furthest == {4: 1}
    assert day.visit_seconds == IDLE_LIMIT + 31


def test_a_crash_ends_the_visit_and_counts_it(world):
    seated(world)
    world.clock.t = 100
    world.processes.exits = [(1, 1), (1, 1)]  # reported twice: the second is ignored
    world.loop.step()
    assert world.pool.seats[0].state == State.FAILED
    assert world.counts.days()[-1].visit_seconds == 100


def test_a_stuck_stop_is_retried_then_given_up(world, caplog):
    seated(world)
    world.processes.fail_stop.add(1)
    world.check.idle = IDLE_LIMIT + 1
    world.clock.t = IDLE_LIMIT + 1
    world.loop.step()  # Reset: the stop raises, the loop carries on
    assert world.pool.seats[0].state == State.RESETTING
    assert world.counts.days()[-1].ended == 1  # the visit ended with the Reset
    world.clock.t += 10
    world.loop.step()
    assert world.pool.seats[0].state == State.RESETTING
    world.processes.fail_stop.clear()
    world.clock.t += 1
    world.loop.step()  # retried: now it stops
    assert 1 in world.processes.stopped


def test_a_stop_that_never_works_is_given_up_after_30_s(world, caplog):
    seated(world)
    world.processes.fail_stop.add(1)
    world.check.idle = IDLE_LIMIT + 1
    world.clock.t = IDLE_LIMIT + 1
    world.loop.step()
    world.clock.t += 29
    world.loop.step()
    assert world.pool.seats[0].state == State.RESETTING
    world.clock.t += 1
    world.loop.step()
    assert world.pool.seats[0].state != State.RESETTING
    assert "gave up stopping seat 1" in caplog.text
    assert world.counts.days()[-1].ended == 1  # counted once, not again at the give-up


def test_one_seats_failure_never_stops_the_loop(world, caplog):
    def broken(port, token, client):
        raise RuntimeError("boom")

    world.loop.check = broken
    world.loop.step()
    world.loop.step()
    assert world.pool.seats[0].state == State.STARTING
    assert "boom" in caplog.text


def test_run_loop_stops_on_the_event(world):
    stop = threading.Event()
    stop.set()
    manager.run_loop(
        world.pool, world.processes, world.counts, world.check, stop, every=0.01, now=world.clock
    )
    assert world.processes.running  # one pass ran before it noticed


def test_sigterm_stops_every_seat(world):
    stop = threading.Event()
    world.processes.running[1] = "t1"
    handler = manager.stopper(stop, world.processes)
    with pytest.raises(SystemExit) as done:
        handler(15, None)
    assert done.value.code == 0
    assert stop.is_set()
    assert world.processes.stop_all_called


def test_the_manager_never_loads_the_app():
    source = Path(manager.__file__).read_text()
    assert "create_app" not in source and "optionsjourney.app" not in source


def test_a_seat_that_cannot_start_backs_off(world, caplog):
    class Refused(Exception):  # like marker.FolderRefused: the seat's folder isn't ours
        pass

    def refuse(n, port, token, *, keep_data=False):
        raise Refused("not a demo folder")

    world.processes.start = refuse
    world.loop.step()
    seat = world.pool.seats[0]
    assert seat.state == State.FAILED and seat.retry_at is not None
    assert "seat 1 could not start" in caplog.text
    world.loop.step()  # the loop carries on; the other seat is tried instead
    assert world.pool.seats[1].state in (State.STARTING, State.FAILED)


def test_a_seat_answering_wrongly_counts_as_unhealthy(world):
    world.loop.step()
    world.loop.step()
    world.loop.check = lambda port, token, client: Health(ok=False, idle_seconds=0, tour_furthest=1)
    for _ in range(3):
        world.loop.step()
    assert world.pool.seats[0].state != State.READY


# ----- final review: counting, no-shows, robots, start again, a new day -----


def test_a_link_preview_is_not_a_visit_and_its_seat_is_freed(world):
    world.loop.step()
    world.loop.step()
    world.app().get("/")  # fetched / and never came back
    world.clock.t = 2
    world.check.idle = 50  # the seat's last request was before it was handed out
    world.loop.step()
    world.clock.t = NO_SHOW
    world.check.idle = NO_SHOW + 48
    world.loop.step()
    assert 1 in world.processes.stopped
    days = world.counts.days()
    assert days == [] or (days[-1].visits, days[-1].ended) == (0, 0)


def test_robots_are_turned_away(world):
    client = world.app()
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.get_data(as_text=True) == "User-agent: *\nDisallow: /\n"
    for path in ("/", "/_seat/ended", "/robots.txt"):
        assert client.get(path).headers["X-Robots-Tag"] == "noindex"


def test_other_paths_without_a_seat_go_to_the_front(world):
    client = world.app()
    for path in ("/journal", "/api/positions", "/favicon.ico", "/automation?x=1"):
        r = client.get(path)
        assert r.status_code == 302 and r.headers["Location"] == "/", path
    assert client.get("/static/css/demo.css").status_code == 200
    image = client.get("/favicon.ico", headers={"Sec-Fetch-Dest": "image"})
    assert image.status_code == 404  # not sent on to /, where it would take a seat
    assert world.pool.queue_length() == 0 and world.pool.assigned() == 0
    assert client.get("/_seat/wait").status_code == 200
    assert client.get("/stats").status_code == 404  # no password: still its own 404


def test_start_again_frees_the_old_seat(world):
    client = seated(world)
    r = client.post("/_seat/start")
    assert r.status_code == 302
    assert cookie_header(r, "oj_seat").startswith("oj_seat=;")
    world.clock.t = 4
    world.loop.step()
    assert 1 in world.processes.stopped
    assert world.counts.days()[-1].ended == 1


def test_start_again_with_someone_elses_cookie_frees_nothing(world):
    seated(world)
    stranger = world.app()
    stranger.set_cookie("oj_seat", "1.t1x")
    stranger.post("/_seat/start")
    stranger.set_cookie("oj_seat", "1.")
    stranger.post("/_seat/start")
    stranger.set_cookie("oj_seat", "x.t1")
    stranger.post("/_seat/start")
    world.clock.t = 4
    world.loop.step()
    assert world.processes.stopped == []
    assert world.pool.seats[0].state == State.ASSIGNED


def test_a_new_day_shows_restarting_not_ended(world):
    client = seated(world)
    world.processes.exits = [(1, 75)]
    world.clock.t = 5
    world.loop.step()  # NEW_DAY: the seat starts again for the same visitor
    assert world.pool.seats[0].state == State.STARTING
    r = client.get("/_seat/ended")
    page = r.get_data(as_text=True)
    assert "Your demo is restarting…" in page
    assert '<meta http-equiv="refresh" content="2;url=/">' in page
    assert "Start again?" not in page
    stranger = world.app()
    stranger.set_cookie("oj_seat", "1.nope")
    assert "Start again?" in stranger.get("/_seat/ended").get_data(as_text=True)


def test_a_failing_count_never_leaves_a_seat_resetting(world, monkeypatch):
    seated(world)

    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr(world.counts, "visit_ended", broken)
    world.check.idle = IDLE_LIMIT + 1
    world.clock.t = IDLE_LIMIT + 10
    world.loop.step()
    assert 1 in world.processes.stopped
    assert world.pool.seats[0].state != State.RESETTING


def test_the_stats_page_has_a_column_per_tour_step(world):
    from optionsjourney.demo import tour  # noqa: PLC0415

    client = world.app(password="pw")
    page = client.get("/stats", headers=basic("pw")).get_data(as_text=True)
    assert f"{tour.TOTAL}" in page
    assert not hasattr(manager, "TOUR_STEPS")
    assert manager.SEAT == "oj_seat"
