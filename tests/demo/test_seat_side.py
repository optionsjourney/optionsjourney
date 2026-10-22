"""The seat side: what a demo copy run by the seat manager does for its one visitor."""

import time

ORIGIN = {"Origin": "http://localhost"}


def _client(app, cookie=None):
    client = app.test_client()
    if cookie:
        client.set_cookie("oj_seat", cookie, domain="localhost")
    return client


def test_an_old_cookie_never_reaches_a_new_seat(demo_app, monkeypatch):
    monkeypatch.setenv("DEMO_SEAT_TOKEN", "abc")
    old = _client(demo_app, "3.zzz")
    response = old.get("/journal")
    assert response.status_code == 302 and response.headers["Location"].endswith("/_seat/ended")
    assert old.get("/socket.io/?EIO=4&transport=polling").status_code == 403
    assert _client(demo_app).get("/journal").status_code == 302  # no cookie at all
    assert _client(demo_app, "3.abc").get("/journal").status_code == 200


def test_no_token_no_check(demo_app, monkeypatch):
    monkeypatch.delenv("DEMO_SEAT_TOKEN", raising=False)
    assert demo_app.test_client().get("/journal").status_code == 200


def test_activity_counts_pages_not_polls(demo_app, monkeypatch):
    client = demo_app.test_client()
    client.get("/journal")
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 60)
    for _ in range(3):
        client.get("/demo/tour")
    client.get("/static/nothing.css")
    body = client.get("/demo/activity").get_json()
    assert body["idle_seconds"] >= 60  # the polls didn't reset it
    assert body["tour_furthest"] == 1 and body["day_over"] is False
    client.get("/journal")
    assert client.get("/demo/activity").get_json()["idle_seconds"] < 5


def test_activity_needs_the_seat_token_header(demo_app, monkeypatch):
    monkeypatch.setenv("DEMO_SEAT_TOKEN", "abc")
    client = _client(demo_app)
    assert client.get("/demo/activity").status_code == 403
    assert client.get("/demo/activity", headers={"X-Seat-Token": "zzz"}).status_code == 403
    assert client.get("/demo/activity", headers={"X-Seat-Token": "abc"}).status_code == 200
    # Asking is not activity: the visitor stays as idle as they were
    ask = client.get("/demo/activity", headers={"X-Seat-Token": "abc"})
    assert "idle_seconds" in ask.get_json()


def _socket_status(app, cookie_header):
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/socket.io/",
        "QUERY_STRING": "EIO=4&transport=polling",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.url_scheme": "http",
        "HTTP_COOKIE": cookie_header,
    }
    seen = []
    app.wsgi_app(environ, lambda status, headers, exc=None: seen.append(status))
    return int(seen[0].split()[0])


def test_the_socket_gate_reads_cookies_like_the_page_check(demo_app, monkeypatch):
    monkeypatch.setenv("DEMO_SEAT_TOKEN", "abc")
    assert _socket_status(demo_app, "oj_seat=3.abc") == 200
    assert _socket_status(demo_app, 'a={"x":1,"y":2}; oj_seat=3.abc') == 200
    assert _socket_status(demo_app, "pref=a b; oj_seat=3.abc") == 200
    assert _socket_status(demo_app, "oj_seat=3.zzz") == 403
    assert _socket_status(demo_app, "") == 403


def test_the_ended_page_never_redirects_to_itself(demo_app, monkeypatch):
    monkeypatch.setenv("DEMO_SEAT_TOKEN", "abc")
    response = _client(demo_app, "3.zzz").get("/_seat/ended")
    assert response.status_code == 403
    assert b"This demo session has ended." in response.data


def test_tour_clicks_count_as_activity(demo_app, monkeypatch):
    client = demo_app.test_client()
    client.get("/journal")
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 60)
    assert client.get("/demo/activity").get_json()["idle_seconds"] >= 60
    client.post("/demo/tour/skip", json={}, headers={"Origin": "http://localhost"})
    assert client.get("/demo/activity").get_json()["idle_seconds"] < 5


def _idle_after(client, monkeypatch, *requests):
    """Idle seconds after a page view, a minute's wait, then `requests` (method, path)."""
    client.get("/journal")
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 60)
    for method, path, kwargs in requests:
        getattr(client, method)(path, **kwargs)
    return client.get("/demo/activity").get_json()["idle_seconds"]


def test_data_polls_are_not_activity(demo_app, monkeypatch):
    """Open pages poll /api/ (and the tour box) on timers: a forgotten tab isn't a person."""
    client = demo_app.test_client()
    idle = _idle_after(
        client,
        monkeypatch,
        ("get", "/api/alerts", {}),
        ("get", "/api/trade/expirations", {}),
        ("get", "/demo/tour", {}),
        ("get", "/journal", {"headers": {"Sec-Fetch-Mode": "cors"}}),  # a page fetched by script
    )
    assert idle >= 60


def test_opening_a_page_is_activity(demo_app, monkeypatch):
    client = demo_app.test_client()
    idle = _idle_after(
        client, monkeypatch, ("get", "/journal", {"headers": {"Sec-Fetch-Mode": "navigate"}})
    )
    assert idle < 5


def test_sending_anything_is_activity(demo_app, monkeypatch):
    client = demo_app.test_client()
    idle = _idle_after(client, monkeypatch, ("post", "/demo/skip", {"headers": ORIGIN}))
    assert idle < 5


def test_the_seat_cookie_name_is_the_managers():
    from optionsjourney.demo import manager, seats  # noqa: PLC0415
    from optionsjourney.web.routes import demo  # noqa: PLC0415

    assert manager.SEAT == seats.COOKIE == "oj_seat"
    source = __import__("inspect").getsource(demo)
    assert '"oj_seat"' not in source


def test_a_turned_away_request_is_not_the_visitors_activity(demo_app, monkeypatch):
    """A stale cookie (the seat's earlier visitor) gets sent to /_seat/ended: that is not
    the current visitor doing something, so it neither resets their idle time nor counts
    as their first request."""
    monkeypatch.setenv("DEMO_SEAT_TOKEN", "abc")
    current = _client(demo_app, "3.abc")
    stale = _client(demo_app, "3.zzz")
    asks = {"headers": {"X-Seat-Token": "abc"}}
    current.get("/journal")
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + 60)
    assert stale.get("/journal", headers={"Sec-Fetch-Mode": "navigate"}).status_code == 302
    assert stale.post("/demo/skip", headers=ORIGIN).status_code == 302
    assert stale.get("/_seat/ended").status_code == 403
    idle = current.get("/demo/activity", **asks).get_json()["idle_seconds"]
    assert idle >= 60
    current.post("/demo/skip", headers=ORIGIN)  # the seat's visitor
    assert current.get("/demo/activity", **asks).get_json()["idle_seconds"] < 5
