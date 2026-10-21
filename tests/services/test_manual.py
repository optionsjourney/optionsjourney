from datetime import UTC, datetime

import pytest

from optionsjourney.brokers.manual import ManualEntryError, build_entry
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services import journal, manual

AT = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
LEGS = [
    {
        "type": "put",
        "side": "sell",
        "quantity": "2",
        "underlying": "SPX",
        "strike": "5800",
        "expiration": "2026-10-16",
        "price": "2.00",
    },
    {
        "type": "put",
        "side": "buy",
        "quantity": "2",
        "underlying": "SPX",
        "strike": "5790",
        "expiration": "2026-10-16",
        "price": "1.00",
    },
]


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    migrate(engine)
    return make_session_factory(engine)


def test_manual_accounts(session_factory):
    first = manual.create_account(session_factory, "Fidelity IRA")
    second = manual.create_account(session_factory, "IBKR")
    assert (first, second) == ("M001", "M002")
    assert [a.nickname for a in manual.accounts(session_factory)] == ["Fidelity IRA", "IBKR"]


def test_account_names_are_needed_and_unique(session_factory):
    manual.create_account(session_factory, "IBKR")
    with pytest.raises(ManualEntryError, match="already"):
        manual.create_account(session_factory, " ibkr ")
    with pytest.raises(ManualEntryError, match="name"):
        manual.create_account(session_factory, "  ")


def test_an_entry_becomes_a_trade_in_the_journal(session_factory):
    number = manual.create_account(session_factory, "IBKR")
    key = manual.add_entry(
        session_factory, number, build_entry(executed_at=AT, effect="open", fees="1", legs=LEGS)
    )
    view = journal.load_journal(session_factory)
    entry = journal.find_entry(view, key)
    assert entry.name == "SPX Put Credit Spread"
    assert entry.position.units == 2


def test_closing_legs_are_the_opposite_of_whats_left(session_factory):
    number = manual.create_account(session_factory, "IBKR")
    key = manual.add_entry(
        session_factory, number, build_entry(executed_at=AT, effect="open", fees="0", legs=LEGS)
    )
    position = journal.find_entry(journal.load_journal(session_factory), key).position
    legs = manual.closing_legs(position)
    assert [(leg["side"], leg["quantity"], leg["strike"]) for leg in legs] == [
        ("buy", "2", "5800"),
        ("sell", "2", "5790"),
    ]


def test_deleting_an_entry_removes_the_trade(session_factory):
    number = manual.create_account(session_factory, "IBKR")
    key = manual.add_entry(
        session_factory, number, build_entry(executed_at=AT, effect="open", fees="0", legs=LEGS)
    )
    position = journal.find_entry(journal.load_journal(session_factory), key).position
    [order_id] = manual.entry_ids(position)
    manual.delete_entry(session_factory, order_id)
    assert journal.find_entry(journal.load_journal(session_factory), key) is None


def test_only_manual_entries_can_be_deleted(session_factory):
    from optionsjourney.db.models import BrokerTransaction

    with session_factory() as session:
        session.add(
            BrokerTransaction(
                broker="tastytrade",
                account_number="5WX01234",
                transaction_id="1",
                transaction_type="Trade",
                executed_at=AT,
                raw={},
                first_seen_at=AT,
                updated_at=AT,
            )
        )
        session.commit()
    with pytest.raises(ManualEntryError):
        manual.delete_entry(session_factory, "1")
