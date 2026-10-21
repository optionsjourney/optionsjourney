"""Manual bots (#158): the Trade ticket's Manage with (Place only by default)."""
# ruff: noqa: F811 -- the Trade page fixtures, imported by name

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from tests.web.test_journal_pages import ORIGIN
from tests.web.test_trade_page import (  # noqa: F401 -- fixtures
    _account_id,
    _no_leftover_codes,
    _places,
    _review,
    app,
)

from optionsjourney.bots.manual import ManualRules
from optionsjourney.db.models import PendingManual
from optionsjourney.services import manual_bots
from optionsjourney.web import services


def _manual(app, name="Half off"):
    with app.app_context():
        rules = ManualRules(name, target_value=Decimal("50"))
        return manual_bots.save(services().session_factory, None, rules, datetime.now(UTC))


def _pending(app):
    with app.app_context(), services().session_factory() as session:
        return list(session.scalars(select(PendingManual)))


def _confirm(app, client, **extra):
    code = _review(app, client)["code"]
    body = {"account_id": _account_id(app), "code": code, **extra}
    return client.post("/api/trade/confirm", json=body, headers=ORIGIN).get_json()


def test_place_only_default_sends_plain_order(app):
    _manual(app)
    client = app.test_client()
    got = _confirm(app, client)
    assert got["ok"] and got["order_id"] == "777" and len(_places(app)) == 1
    assert _pending(app) == []


def test_manage_with_remembers_after_the_send(app):
    manual_id = _manual(app)
    client = app.test_client()
    got = _confirm(app, client, manual_bot_id=manual_id)
    assert got["ok"] and len(_places(app)) == 1  # the same one send
    (row,) = _pending(app)
    assert (row.order_id, row.manual_bot_id, row.status) == ("777", manual_id, "waiting")
    assert row.rules["name"] == "Half off" and row.rules["target_value"] == "50"
    assert "rules" in got["manage_note"]


def test_unknown_manual_bot_sends_nothing(app):
    client = app.test_client()
    got = _confirm(app, client, manual_bot_id=999)
    assert not got["ok"] and not _places(app) and _pending(app) == []


def test_manual_bots_listing(app):
    _manual(app, "Zed")
    manual_id = _manual(app, "Alpha")
    got = app.test_client().get("/api/trade/manual-bots").get_json()
    assert got["status"] == "success"
    assert [b["name"] for b in got["bots"]] == ["Alpha", "Zed"]
    assert got["bots"][0]["id"] == manual_id
