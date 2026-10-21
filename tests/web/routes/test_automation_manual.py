"""The Automation page's manual bots (#158): list, create, edit, delete."""

import pytest
from tests.web.test_automation_pages import ORIGIN, sf

from optionsjourney.app import create_app
from optionsjourney.bots.manual import ManualRules
from optionsjourney.services import manual_bots

NOTE = (
    "Changes apply to trades you attach from now on. "
    "Trades already using this bot keep their rules."
)


@pytest.fixture
def app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app


def valid(**changes):
    return {
        "name": "Take 50", "target_value": "50", "target_unit": "pct",
        "stop_value": "200", "stop_unit": "pct", "close_dte": "0", "close_time": "15:45",
        "alerts": "2", "da-0-type": "pnl_pct", "da-0-direction": "above", "da-0-value": "25",
    } | changes  # fmt: skip


def listed(app):
    return manual_bots.listing(sf(app))


def test_list_shows_manual_bots_separately(app):
    from datetime import UTC, datetime

    manual_bots.save(sf(app), None, ManualRules("Take 50", close_time="15:45"),
                     datetime.now(UTC))  # fmt: skip
    page = app.test_client().get("/automation").get_data(as_text=True)
    assert "Manual bots" in page
    assert "Take 50" in page
    assert "No bots yet." in page  # the scheduled-bot list is unchanged


def test_create_then_edit(app):
    client = app.test_client()
    assert "Changes apply" not in client.get("/automation/manual/new").get_data(as_text=True)
    r = client.post("/automation/manual", data=valid(exit_on="on"), headers=ORIGIN)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/automation")
    [(bot_id, rules)] = listed(app)
    assert rules.name == "Take 50"
    assert str(rules.target_value) == "50"
    assert rules.close_time == "15:45"
    assert [a.value and str(a.value) for a in rules.alerts] == ["25"]
    page = client.get(f"/automation/manual/{bot_id}").get_data(as_text=True)
    assert NOTE in page
    changed = valid(name="Take 40", target_value="40", id=str(bot_id))  # exit time unticked
    assert client.post("/automation/manual", data=changed, headers=ORIGIN).status_code == 302
    [(same_id, rules)] = listed(app)
    assert same_id == bot_id
    assert rules.name == "Take 40"
    assert rules.close_time is None


def test_bad_form_rerenders_with_problems(app):
    r = app.test_client().post(
        "/automation/manual", data=valid(name="", target_value="abc", stop_value="9"),
        headers=ORIGIN,
    )  # fmt: skip
    assert r.status_code == 400
    page = r.get_data(as_text=True)
    assert "Give the manual bot a name." in page
    assert 'value="abc"' in page
    assert 'value="9"' in page
    assert listed(app) == []


def test_taken_name_is_a_problem(app):
    client = app.test_client()
    client.post("/automation/manual", data=valid(), headers=ORIGIN)
    r = client.post("/automation/manual", data=valid(), headers=ORIGIN)
    assert r.status_code == 400
    assert "already has that name" in r.get_data(as_text=True)


def test_unknown_manual_bot_is_404(app):
    assert app.test_client().get("/automation/manual/999").status_code == 404


def test_delete(app):
    client = app.test_client()
    client.post("/automation/manual", data=valid(), headers=ORIGIN)
    [(bot_id, _)] = listed(app)
    r = client.post(f"/automation/manual/{bot_id}/delete", headers=ORIGIN)
    assert r.status_code == 302
    assert listed(app) == []
    assert "Take 50" not in client.get("/automation").get_data(as_text=True)
