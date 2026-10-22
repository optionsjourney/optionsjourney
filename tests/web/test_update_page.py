import json
from pathlib import Path

import pytest
from tests.services.test_update_risks import arm_bot

from optionsjourney.app import create_app
from optionsjourney.brokers.credentials.local import LocalProvider
from optionsjourney.brokers.credentials.store import MemoryStore
from optionsjourney.services import updates

ORIGIN = {"Origin": "http://localhost"}


def make_ready(settings, tag="v9.9.9"):
    folder = settings.data_dir / "updates" / tag
    (folder / "src" / "scripts").mkdir(parents=True)
    (folder / "ready.json").write_text(json.dumps({
        "tag": tag, "page_url": f"https://example.test/{tag}", "zip_url": "https://example.test/z",
        "folder": f"{tag}/src",
    }), encoding="utf-8")  # fmt: skip
    return folder / "src"


@pytest.fixture(autouse=True)
def _fresh_start_flag(monkeypatch):
    from optionsjourney.web.routes import update

    monkeypatch.setattr(update, "_started", False)


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(updates, "start_installer", lambda source, log: seen.append((source, log)))
    return seen


def build(settings, menu=True):
    app = create_app(settings, credentials=LocalProvider(MemoryStore()), broker_clients=lambda: [])
    app.config["TESTING"] = True
    if menu:
        app.config["MENU_APP"] = True
    return app


def force_open(monkeypatch, is_open=True):
    monkeypatch.setattr("optionsjourney.services.update_risks.market_open", lambda now: is_open)


def test_page_lists_risks_with_boxes_and_links(settings, calls, monkeypatch):
    make_ready(settings)
    force_open(monkeypatch)
    app = build(settings)
    html = app.test_client().get("/update").get_data(as_text=True)
    assert "Install v9.9.9" in html
    assert 'name="risk_market_open"' in html and 'name="confirm"' in html
    assert "https://example.test/v9.9.9" in html and "/help/updating#going-back" in html
    assert "Install now" in html
    assert "I understand the app stops for about a minute while v9.9.9 is installed." in html


def test_page_with_no_risks(settings, calls, monkeypatch):
    make_ready(settings)
    force_open(monkeypatch, False)
    html = build(settings).test_client().get("/update").get_data(as_text=True)
    assert "Nothing is running that the restart would interrupt." in html


def test_a_missing_box_is_refused(settings, calls, monkeypatch):
    make_ready(settings)
    force_open(monkeypatch)
    response = (
        build(settings).test_client().post("/update/install", data={"confirm": "1"}, headers=ORIGIN)
    )
    assert response.status_code == 400
    assert "Tick every box first" in response.get_data(as_text=True)
    assert calls == []


def test_a_risk_that_appeared_since_the_page_loaded_is_refused(settings, calls, monkeypatch):
    make_ready(settings)
    force_open(monkeypatch)
    app = build(settings)
    client = app.test_client()
    client.get("/update")
    arm_bot(app.extensions["optionsjourney"].session_factory)
    response = client.post(
        "/update/install", data={"confirm": "1", "risk_market_open": "1"}, headers=ORIGIN
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert 'name="risk_armed_bots"' in html and "1 bot armed" in html
    assert "checked" not in html.split('name="risk_armed_bots"')[1].split(">")[0]
    assert calls == []


def test_everything_ticked_starts_the_installer(settings, calls, monkeypatch):
    source = make_ready(settings)
    force_open(monkeypatch)
    app = build(settings)
    arm_bot(app.extensions["optionsjourney"].session_factory)
    response = app.test_client().post(
        "/update/install",
        data={"confirm": "1", "risk_market_open": "1", "risk_armed_bots": "1"},
        headers=ORIGIN,
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert calls == [(Path(source), settings.data_dir / "logs" / "update.log")]
    assert "OptionsJourney will stop and come back by itself in about a minute" in html
    assert "update.log" in html


def test_a_risk_that_went_away_does_not_block(settings, calls, monkeypatch):
    make_ready(settings)
    force_open(monkeypatch, False)
    response = (
        build(settings)
        .test_client()
        .post("/update/install", data={"confirm": "1", "risk_market_open": "1"}, headers=ORIGIN)
    )
    assert response.status_code == 200 and len(calls) == 1


def test_not_found_without_the_menu_app_or_a_download(settings, calls):
    make_ready(settings)
    assert build(settings, menu=False).test_client().get("/update").status_code == 404
    assert build(settings, menu=False).test_client().post(
        "/update/install", data={}, headers=ORIGIN
    ).status_code == 404  # fmt: skip


def test_not_found_without_a_ready_update(settings, calls):
    assert build(settings).test_client().get("/update").status_code == 404


def test_not_found_in_gateway_mode(gateway_settings, calls):
    make_ready(gateway_settings)
    app = create_app(gateway_settings, broker_clients=lambda: [])
    app.config["MENU_APP"] = True
    assert app.test_client().get("/update").status_code == 404


def test_a_double_submit_starts_one_installer(settings, calls, monkeypatch):
    make_ready(settings)
    force_open(monkeypatch)
    client = build(settings).test_client()
    data = {"confirm": "1", "risk_market_open": "1"}
    first = client.post("/update/install", data=data, headers=ORIGIN)
    second = client.post("/update/install", data=data, headers=ORIGIN)
    assert first.status_code == second.status_code == 200
    assert "come back by itself" in second.get_data(as_text=True)
    assert len(calls) == 1


def test_a_changed_count_is_refused(settings, calls, monkeypatch):
    make_ready(settings)
    force_open(monkeypatch, False)
    app = build(settings)
    factory = app.extensions["optionsjourney"].session_factory
    arm_bot(factory)
    client = app.test_client()
    assert 'value="1"' in client.get("/update").get_data(as_text=True)
    arm_bot(factory, name="IC2")
    response = client.post(
        "/update/install", data={"confirm": "1", "risk_armed_bots": "1"}, headers=ORIGIN
    )
    html = response.get_data(as_text=True)
    assert response.status_code == 400 and 'value="2"' in html and "2 bots armed" in html
    assert calls == []


def test_a_failed_start_says_so_and_can_be_retried(settings, monkeypatch):
    make_ready(settings)
    force_open(monkeypatch)
    attempts = []

    def fail(source, log):
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError("no write access")

    monkeypatch.setattr(updates, "start_installer", fail)
    client = build(settings).test_client()
    data = {"confirm": "1", "risk_market_open": "1"}
    first = client.post("/update/install", data=data, headers=ORIGIN)
    assert first.status_code == 500
    assert "Couldn't start the installer: no write access. Your app is still running." in (
        first.get_data(as_text=True).replace("&#39;", "'")
    )
    second = client.post("/update/install", data=data, headers=ORIGIN)
    assert second.status_code == 200 and len(attempts) == 2
