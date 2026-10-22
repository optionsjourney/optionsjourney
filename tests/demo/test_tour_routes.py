from datetime import timedelta

from optionsjourney.demo.director import TOUR_DRIFT

ORIGIN = {"Origin": "http://localhost"}


def _tour(client):
    return client.get("/demo/tour").get_json()


def test_tour_json_starts_at_step_one(demo_app):
    body = _tour(demo_app.test_client())
    assert body["step"] == 1
    assert body["total"] == 7


def test_opening_automation_moves_the_tour(demo_app):
    client = demo_app.test_client()
    client.get("/automation")
    assert _tour(client)["step"] == 2


def test_next_names_the_step_it_moves_from(demo_app):
    client = demo_app.test_client()
    client.post("/demo/tour/next", json={"from": 1}, headers=ORIGIN)
    body = client.post("/demo/tour/next", json={"from": 1}, headers=ORIGIN).get_json()
    assert body["step"] == 2


def test_next_needs_a_whole_number_from(demo_app):
    client = demo_app.test_client()
    assert client.post("/demo/tour/next", json={}, headers=ORIGIN).status_code == 400
    assert client.post("/demo/tour/next", json={"from": "x"}, headers=ORIGIN).status_code == 400


def test_reaching_step_five_brings_the_calendar_forward(demo_app):
    demo = demo_app.extensions["optionsjourney"].demo
    demo.tour.current = demo.tour.furthest = 4
    demo_app.test_client().post("/demo/tour/next", json={"from": 4}, headers=ORIGIN)
    assert demo.director._calendar_due <= demo.clock.now() + TOUR_DRIFT + timedelta(seconds=5)


def test_restart_tour_from_the_banner_redirects_back(demo_app):
    demo = demo_app.extensions["optionsjourney"].demo
    demo.tour.current = 3
    response = demo_app.test_client().post(
        "/demo/tour/restart", headers={"Referer": "http://localhost/charts"}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/charts")
    assert demo.tour.current == 1


def test_restart_after_opening_automation_shows_step_one(demo_app):
    client = demo_app.test_client()
    client.get("/automation")
    assert _tour(client)["step"] == 2
    body = client.post("/demo/tour/restart", json={}, headers=ORIGIN).get_json()
    assert body["step"] == 1
    assert _tour(client)["step"] == 1
    client.get("/automation")
    assert _tour(client)["step"] == 1
    assert client.post("/demo/tour/next", json={"from": 1}, headers=ORIGIN).get_json()["step"] == 2


def test_tour_routes_need_the_same_origin(demo_app):
    response = demo_app.test_client().post("/demo/tour/next", json={"from": 1})
    assert response.status_code == 403


def test_banner_has_restart_tour(demo_app):
    assert 'action="/demo/tour/restart"' in demo_app.test_client().get("/journal").get_data(
        as_text=True
    )
