"""Importing a Tracker2 analyzer bot (#158): it becomes a manual bot; Place only saves nothing."""

import json

import pytest
from sqlalchemy import select

from optionsjourney.app import create_app
from optionsjourney.db.models import Bot, ManualBot
from optionsjourney.web import services

ORIGIN = {"Origin": "http://localhost"}
PLACE_ONLY = "Place only is built into this app: choose it under Manage with on the Trade page"


@pytest.fixture
def app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app


def _file(**changes):
    bot = {"name": "Closer", "entry_source": "analyzer", "underlying": None, "legs": [],
           "profit_target_pct": 50.0, "profit_target_unit": "pct", "stop_loss_pct": None,
           "scheduled_close_time": "15:45", "scheduled_close_dte": 1}  # fmt: skip
    return json.dumps({"format": "tracker2.bot", "schema_version": 1, "bot": bot | changes})


def _rows(app):
    with app.app_context(), services().session_factory() as session:
        return (len(session.scalars(select(Bot)).all()),
                list(session.scalars(select(ManualBot))))  # fmt: skip


def test_analyzer_import_previews_then_saves_a_manual_bot(app):
    client = app.test_client()
    text = _file()
    page = client.post("/automation/import/preview", data={"json": text}, headers=ORIGIN)
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "manual bot" in html and "15:45" in html
    assert _rows(app) == (0, [])
    saved = client.post("/automation/import/save", data={"json": text, "name": "Mine"},
                        headers=ORIGIN)  # fmt: skip
    assert saved.status_code == 302
    bots, manual = _rows(app)
    assert bots == 0 and [(m.name, m.close_time, m.close_dte) for m in manual] == [
        ("Mine", "15:45", 1)]  # fmt: skip


def test_a_taken_name_is_refused(app):
    client = app.test_client()
    text = _file()
    client.post("/automation/import/save", data={"json": text}, headers=ORIGIN)
    again = client.post("/automation/import/save", data={"json": text, "name": "Closer"},
                        headers=ORIGIN)  # fmt: skip
    assert again.status_code == 400 and "already has that name" in again.get_data(as_text=True)
    assert len(_rows(app)[1]) == 1


def test_place_only_saves_nothing(app):
    text = _file(profit_target_pct=None, scheduled_close_time=None, scheduled_close_dte=None)
    for path in ("preview", "save"):
        response = app.test_client().post(f"/automation/import/{path}", data={"json": text},
                                          headers=ORIGIN)  # fmt: skip
        assert response.status_code == 400 and PLACE_ONLY in response.get_data(as_text=True)
    assert _rows(app) == (0, [])


def test_non_analyzer_import_unchanged(app):
    text = _file(entry_source="scheduled")
    response = app.test_client().post("/automation/import/preview", data={"json": text},
                                      headers=ORIGIN)  # fmt: skip
    html = response.get_data(as_text=True)
    assert response.status_code == 400 and "manual bot" not in html
    assert _rows(app) == (0, [])


def test_an_alerts_only_analyzer_imports_and_shows_its_alerts(app):
    alert = {"type": "pnl_pct", "direction": "above", "value": 40}
    text = _file(profit_target_pct=None, scheduled_close_time=None, scheduled_close_dte=None,
                 use_default_alerts=True, default_alerts=[alert])  # fmt: skip
    client = app.test_client()
    html = client.post("/automation/import/preview", data={"json": text},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert "profit/loss above 40%" in html
    client.post("/automation/import/save", data={"json": text}, headers=ORIGIN)
    assert len(_rows(app)[1][0].alerts) == 1
