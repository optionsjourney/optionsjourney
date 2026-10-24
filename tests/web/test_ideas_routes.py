"""Trade Ideas' API (#292): saving, listing, editing and deleting ideas. A trade placed
from an idea retiring it is in test_trade_page.py."""

import pytest
from tests.services.test_orders import FakeBroker
from tests.web.test_journal_pages import ORIGIN

from optionsjourney.app import create_app
from optionsjourney.db.models import Idea
from optionsjourney.services import ideas
from optionsjourney.web import services

SHORT = "SPXW  301220P05800000"
LONG = "SPXW  301220P05790000"


def card(**over):
    return {
        "label": "",
        "qty": 2,
        "entry_unit": -1.25,
        "locked": True,
        "multiplier": 100,
        "legs": [
            {"symbol": SHORT, "side": "sell", "ratio": 1},
            {"symbol": LONG, "side": "buy", "ratio": 2},
        ],
        **over,
    }


@pytest.fixture
def app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials,
                     broker_clients=lambda: [FakeBroker(held={})])  # fmt: skip
    app.config["TESTING"] = True
    return app


@pytest.fixture(autouse=True)
def _no_throttle():
    ideas._asked.clear()
    ideas._last_reads.clear()


def _save(client, **over):
    body = {"ticker": "SPX", "label": "Put fly", "note": "watch it", "cards": [card()], **over}
    return client.post("/api/ideas", json=body, headers=ORIGIN)


def _stored(app):
    with app.app_context(), services().session_factory() as session:
        return [idea.id for idea in session.query(Idea)]


def test_create_list_count(app):
    client = app.test_client()
    got = _save(client)
    assert got.status_code == 201
    saved = got.get_json()
    assert saved["status"] == "success" and isinstance(saved["idea"]["id"], int)
    listed = client.get("/api/ideas").get_json()["ideas"]
    (row,) = listed
    assert row["id"] == saved["idea"]["id"] and row["label"] == "Put fly"
    assert row["note"] == "watch it" and row["ticker"] == "SPX"
    assert row["cards"][0]["entry"] == -1.25 and row["cards"][0]["locked"] is True
    assert client.get("/api/ideas?count=1").get_json() == {"count": 1}
    markets = client.get("/api/ideas/markets").get_json()["ideas"]
    assert list(markets) == [str(row["id"])] and "pnl" in markets[str(row["id"])]


def test_patch_and_delete(app):
    client = app.test_client()
    idea_id = _save(client).get_json()["idea"]["id"]
    got = client.patch(f"/api/ideas/{idea_id}", json={"label": "Renamed", "note": ""},
                       headers=ORIGIN)  # fmt: skip
    assert got.status_code == 200 and got.get_json()["status"] == "success"
    row = client.get("/api/ideas").get_json()["ideas"][0]
    assert row["label"] == "Renamed" and row["note"] is None
    blank = client.patch(f"/api/ideas/{idea_id}", json={"label": " "}, headers=ORIGIN)
    assert blank.status_code == 400 and blank.get_json()["status"] == "error"
    assert client.delete(f"/api/ideas/{idea_id}", headers=ORIGIN).status_code == 200
    assert client.get("/api/ideas?count=1").get_json() == {"count": 0}
    assert client.delete(f"/api/ideas/{idea_id}", headers=ORIGIN).status_code == 404
    missing = client.patch(f"/api/ideas/{idea_id}", json={"label": "x"}, headers=ORIGIN)
    assert missing.status_code == 404


def test_a_vix_leg_is_refused(app):
    vix = card(legs=[{"symbol": "VIX   301216C00020000", "side": "buy", "ratio": 1}])
    got = _save(app.test_client(), ticker="VIX", cards=[vix])
    assert got.status_code == 400
    assert got.get_json() == {"status": "error", "message": "VIX options can't be saved as ideas."}
    assert _stored(app) == []


def test_a_body_that_isnt_an_idea_is_refused(app):
    client = app.test_client()
    assert client.post("/api/ideas", json=[], headers=ORIGIN).status_code == 400
    assert _save(client, cards="nope").status_code == 400


def test_writes_refuse_other_sites(app):
    body = {"ticker": "SPX", "label": "x", "cards": [card()]}
    got = app.test_client().post("/api/ideas", json=body, headers={"Origin": "http://evil.example"})
    assert got.status_code == 403
    assert _stored(app) == []
