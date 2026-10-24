from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from optionsjourney.core.expiry_prices import PriceRead
from optionsjourney.services import ideas

SHORT = "SPXW  261023P06600000"
LONG = "SPXW  261023P06550000"
DURING = datetime(2026, 10, 23, 18, 0, tzinfo=UTC)  # 2 PM New York
AFTER = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)  # next morning


def make_card():
    return {
        "label": "Put spread",
        "qty": 2,
        "entry_unit": 1.5,
        "locked": False,
        "multiplier": 100,
        "legs": [
            {"symbol": SHORT, "side": "sell", "ratio": 1},
            {"symbol": LONG, "side": "buy", "ratio": 1},
        ],
    }


class FakeHub:
    def subscribe(self, keys):
        pass


class BrokenHub:
    def subscribe(self, keys):
        raise RuntimeError("hub down")


@pytest.fixture
def fake_marks(monkeypatch):
    table = {}
    monkeypatch.setattr(
        ideas.streaming,
        "marks",
        lambda hub, keys, now=None, **kw: {k: table[k] for k in keys if k in table},
    )
    return table


@pytest.fixture(autouse=True)
def clear_throttle():
    ideas._asked.clear()


def test_create_and_list_newest_first(factory, fake_marks):
    fake_marks["SPX"] = Decimal("6590")
    first = ideas.create(factory, FakeHub(), "spx", "First", None, [make_card()], now=DURING)
    second = ideas.create(factory, FakeHub(), "SPX", "Second", "n", [make_card()], now=DURING)
    assert first["spot_at_entry"] == 6590
    rows = ideas.listing(factory, FakeHub(), [], now=DURING)
    assert [r["label"] for r in rows] == ["Second", "First"]
    assert rows[0]["id"] == second["id"]
    assert ideas.count(factory) == 2


def test_live_pnl_from_hub(factory, fake_marks):
    idea = ideas.create(factory, FakeHub(), "SPX", "L", None, [make_card()], now=DURING)
    fake_marks.update({SHORT: Decimal("2.00"), LONG: Decimal("1.00")})  # unit +1.00
    row = ideas.listing(factory, FakeHub(), [], now=DURING)[0]
    assert row["status"] == "live"
    assert row["pnl"] == 100.0  # (1.5 - 1.0) * 2 * 100
    assert row["cards"][0]["now"] == 1.0
    assert ideas.markets(factory, FakeHub(), [], now=DURING)[idea["id"]]["pnl"] == 100.0


def test_missing_marks_gives_none_not_error(factory, fake_marks):
    ideas.create(factory, FakeHub(), "SPX", "L", None, [make_card()], now=DURING)
    assert ideas.listing(factory, FakeHub(), [], now=DURING)[0]["pnl"] is None


def test_freezes_once_settled(factory, fake_marks, monkeypatch):
    ideas.create(factory, FakeHub(), "SPX", "S", None, [make_card()], now=DURING)
    calls = []

    def closes(clients, wanted, **kw):
        calls.append(wanted)
        return {k: PriceRead(Decimal("6580"), "test", True) for k in wanted}

    monkeypatch.setattr(ideas.expiry_prices, "official_closes", closes)
    row = ideas.listing(factory, FakeHub(), [], now=AFTER)[0]
    assert calls == [{("SPX", date(2026, 10, 23))}]
    assert row["pnl"] == -3700.0  # (1.5 - 20) * 2 * 100
    again = ideas.listing(factory, FakeHub(), [], now=AFTER)[0]
    assert len(calls) == 1
    assert again["pnl"] == row["pnl"]
    assert again["status"] == "expired"


def test_non_final_close_is_shown_not_stored(factory, fake_marks, monkeypatch):
    ideas.create(factory, FakeHub(), "SPX", "S", None, [make_card()], now=DURING)
    monkeypatch.setattr(
        ideas.expiry_prices,
        "official_closes",
        lambda clients, wanted, **kw: {k: PriceRead(Decimal("6580"), "t", False) for k in wanted},
    )
    row = ideas.listing(factory, FakeHub(), [], now=AFTER)[0]
    assert row["pnl"] is not None
    with factory() as session:
        from optionsjourney.db.models import Idea

        assert session.get(Idea, row["id"]).final_pnl is None


def test_settlement_na_and_throttle(factory, fake_marks, monkeypatch):
    ideas.create(factory, FakeHub(), "SPX", "S", None, [make_card()], now=DURING)
    calls = []

    def closes(clients, wanted, **kw):
        calls.append(1)
        raise RuntimeError("brokers down")

    monkeypatch.setattr(ideas.expiry_prices, "official_closes", closes)
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    row = ideas.listing(factory, FakeHub(), [], now=AFTER, clock=clock)[0]
    assert row["settlement_na"] is True and row["pnl"] is None
    now[0] += 100
    ideas.listing(factory, FakeHub(), [], now=AFTER, clock=clock)
    assert len(calls) == 1
    now[0] += 250
    ideas.listing(factory, FakeHub(), [], now=AFTER, clock=clock)
    assert len(calls) == 2


def test_edit_delete_retire(factory, fake_marks):
    idea = ideas.create(factory, FakeHub(), "SPX", "A", None, [make_card()], now=DURING)
    ideas.edit(factory, idea["id"], label="B", note="n")
    row = ideas.listing(factory, FakeHub(), [], now=DURING)[0]
    assert (row["label"], row["note"]) == ("B", "n")
    assert ideas.delete(factory, idea["id"]) is True
    assert ideas.delete(factory, idea["id"]) is False


def test_retire_missing_id_is_false_not_error(factory):
    assert ideas.retire(factory, 999) is False


def test_throttled_poll_reuses_last_reads(factory, fake_marks, monkeypatch):
    ideas.create(factory, FakeHub(), "SPX", "S", None, [make_card()], now=DURING)
    monkeypatch.setattr(
        ideas.expiry_prices,
        "official_closes",
        lambda clients, wanted, **kw: {k: PriceRead(Decimal("6580"), "t", False) for k in wanted},
    )
    now = [1000.0]
    clock = lambda: now[0]  # noqa: E731
    first = ideas.listing(factory, FakeHub(), [], now=AFTER, clock=clock)[0]
    now[0] += 3
    second = ideas.listing(factory, FakeHub(), [], now=AFTER, clock=clock)[0]
    assert second["settlement_na"] is False
    assert second["pnl"] == first["pnl"] == -3700.0


def test_broken_hub_gives_no_pnl(factory, fake_marks):
    ideas.create(factory, BrokenHub(), "SPX", "L", None, [make_card()], now=DURING)
    row = ideas.listing(factory, BrokenHub(), [], now=DURING)[0]
    assert row["pnl"] is None and row["spot_at_entry"] is None


def test_debit_settlement(factory, fake_marks, monkeypatch):
    debit = make_card()
    debit["entry_unit"] = -2.0
    debit["legs"] = [{"symbol": SHORT, "side": "buy", "ratio": 1}]
    ideas.create(factory, FakeHub(), "SPX", "D", None, [debit], now=DURING)
    monkeypatch.setattr(
        ideas.expiry_prices,
        "official_closes",
        lambda clients, wanted, **kw: {k: PriceRead(Decimal("6580"), "t", True) for k in wanted},
    )
    # long 6600 put is worth 20: unit -20, pnl = (-2 + 20) * 2 * 100
    assert ideas.listing(factory, FakeHub(), [], now=AFTER)[0]["pnl"] == 3600.0
