from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from optionsjourney.bots.definition import DefaultAlertDef
from optionsjourney.bots.manual import ManualRules
from optionsjourney.db.models import Bot, Tag
from optionsjourney.services import manual_bots


@pytest.fixture
def sf(factory):
    return factory


@pytest.fixture
def now():
    return datetime(2026, 10, 17, 15, 0, tzinfo=UTC)


def _rules(name="Half"):
    return ManualRules(
        name=name, target_value=Decimal("50"), target_unit="pct", stop_value=Decimal("300"),
        stop_unit="usd", close_time="15:45", close_dte=0,
        alerts=(DefaultAlertDef("pnl_pct", "above", Decimal("25")),),
    )  # fmt: skip


def test_save_load_round_trip(sf, now):
    rules = _rules()
    bot_id = manual_bots.save(sf, None, rules, now)
    assert manual_bots.load(sf, bot_id) == rules
    changed = _rules("Half v2")
    assert manual_bots.save(sf, bot_id, changed, now) == bot_id
    assert manual_bots.load(sf, bot_id) == changed


def test_names_unique(sf, now):
    manual_bots.save(sf, None, _rules(), now)
    with pytest.raises(ValueError, match="name taken"):
        manual_bots.save(sf, None, _rules(), now)


def test_delete_hides_from_listing(sf, now):
    keep = manual_bots.save(sf, None, _rules("Keep"), now)
    gone = manual_bots.save(sf, None, _rules("Gone"), now)
    manual_bots.delete(sf, gone, now)
    assert [i for i, _ in manual_bots.listing(sf)] == [keep]
    assert manual_bots.load(sf, gone) is None
    assert manual_bots.names(sf) == {keep: "Keep", gone: "Gone"}


def test_delete_keeps_attached_copies(sf, now):
    bot_id = manual_bots.save(sf, None, _rules(), now)
    with sf() as session:
        tag = session.query(Tag).first()
        copy = Bot(name="Half", tag_id=tag.id, broker="tastytrade",
                   account_number="X1", max_per_trade=1, max_per_day=1, hidden=True,
                   per_trade=True, manual_bot_id=bot_id, created_at=now,
                   updated_at=now)  # fmt: skip
        session.add(copy)
        session.commit()
        copy_id = copy.id
    manual_bots.delete(sf, bot_id, now)
    with sf() as session:
        row = session.get(Bot, copy_id)
        assert row is not None and row.hidden and row.per_trade and row.manual_bot_id == bot_id
    assert manual_bots.names(sf)[bot_id] == "Half"


def test_removing_a_manual_bot_row_clears_its_copies_link(sf, now):
    """The database itself sets manual_bot_id to null (0040), should a row ever go."""
    bot_id = manual_bots.save(sf, None, _rules(), now)
    with sf() as session:
        tag = session.query(Tag).first()
        copy = Bot(name="Half", tag_id=tag.id, broker="tastytrade",
                   account_number="X1", max_per_trade=1, max_per_day=1, hidden=True,
                   per_trade=True, manual_bot_id=bot_id, created_at=now,
                   updated_at=now)  # fmt: skip
        session.add(copy)
        session.commit()
        session.execute(text("DELETE FROM manual_bots"))
        session.commit()
        assert session.get(Bot, copy.id, populate_existing=True).manual_bot_id is None
