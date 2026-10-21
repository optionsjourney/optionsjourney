"""Settling expiring trades at the close (#147): the daily job, its guard,
the switch, and folding the settlements into the journal."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.expiry_prices import PriceRead
from optionsjourney.core.transactions import RawTransaction
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import AppAlert, ExpirySettlement, SyncState
from optionsjourney.services import expiry_settle, journal
from optionsjourney.services.sync import sync_broker

DAY = date(2026, 10, 9)  # a Friday
OPENED = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
AFTER = datetime(2026, 10, 9, 20, 20, tzinfo=UTC)  # 4:20 PM New York
BEFORE = datetime(2026, 10, 9, 20, 10, tzinfo=UTC)  # 4:10 PM
SYNCED = datetime(2026, 10, 9, 20, 5, tzinfo=UTC)  # 4:05 PM, after the close
PUT = "SPXW  261009P05800000"
KEY = "tastytrade:5WX01234:10"


def _row(transaction_id, kind="Trade", action="Sell to Open", at=OPENED, **extra):
    price = "2.00"
    row = {
        "id": transaction_id,
        "symbol": PUT,
        "instrument-type": "Equity Option",
        "underlying-symbol": "SPXW",
        "transaction-type": kind,
        "action": action,
        "quantity": "1",
        "price": price,
        "value": str(float(price) * 100),
        "value-effect": "Credit",
        "executed-at": at.isoformat(),
        "order-id": 10,
    }
    row.update(extra)
    return row


class Broker:
    name = "TastyTrade"
    broker = "tastytrade"

    def __init__(self):
        self.rows = [_row(1)]

    def accounts(self):
        return [BrokerAccount("tastytrade", "5WX01234", "Alpha")]

    def transactions(self, number, start, end):
        return [
            RawTransaction(
                str(r["id"]), datetime.fromisoformat(r["executed-at"]), r["transaction-type"], r
            )
            for r in self.rows
        ]


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def _read(close, final=True):
    return PriceRead(close, "schwab daily close" if final else "tastytrade last", final)


def _run(factory, now=AFTER, close=Decimal("5850"), sync=None, broker=None, final=True):
    calls = []

    def default_sync():
        calls.append("sync")
        sync_broker(broker or Broker(), factory, now=lambda: SYNCED)

    record = expiry_settle.run_if_due(
        factory,
        build_book=lambda: journal.build_unsplit(factory)[1],
        prices=lambda wanted: {k: _read(close, final) for k in wanted} if close is not None else {},
        sync=sync or default_sync,
        now=now,
    )
    return record, calls


def _entry(factory, cache=None):
    return journal.find_entry(journal.load_journal(factory, now=AFTER, cache=cache), KEY)


def test_off_by_default_does_nothing(factory):
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    assert _run(factory) == (None, [])
    assert expiry_settle.last_run(factory) is None


def test_not_before_4_15(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    assert _run(factory, now=BEFORE)[0] is None


def test_settles_after_a_sync_since_the_close_and_closes_the_trade(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    record, calls = _run(factory)
    assert calls == []  # synced already
    assert record["done"] and record["settled"] == 1 and record["skipped"] == []
    entry = _entry(factory)
    assert entry.position.status.value == "closed"
    assert entry.close_reason == "Expired" and entry.settled_by_app
    assert entry.position.realized_pl == Decimal("200")
    assert _run(factory)[0] is None  # once a day


def test_syncs_first_when_the_last_sync_was_before_the_close(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: OPENED)
    record, calls = _run(factory)
    assert calls == ["sync"] and record["settled"] == 1


def test_waits_when_the_sync_fails_and_tries_again(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: OPENED)
    record, _ = _run(factory, sync=lambda: None)
    assert not record["done"] and record["settled"] == 0 and "No trade sync" in record["note"]
    assert _entry(factory).position.status.value == "open"
    assert _run(factory)[0]["settled"] == 1  # the next check syncs and settles


def test_a_missing_close_is_retried_and_reported(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    record, _ = _run(factory, close=None)
    assert not record["done"]
    assert record["skipped"][0]["reason"].startswith("no close for SPX")
    assert _run(factory)[0]["settled"] == 1


def test_the_brokers_record_replaces_the_settlement_and_the_cache_rebuilds(factory):
    expiry_settle.set_on(factory, True)
    broker = Broker()
    sync_broker(broker, factory, now=lambda: SYNCED)
    cache = journal.PositionsCache()
    assert _entry(factory, cache).position.status.value == "open"
    _run(factory)
    assert _entry(factory, cache).settled_by_app  # the cache saw the new settlement

    broker.rows.append(
        _row(
            2,
            kind="Receive Deliver",
            action="Buy to Close",
            at=datetime(2026, 10, 10, 12, tzinfo=UTC),
            **{
                "transaction-sub-type": "Expiration",
                "order-id": None,
                "price": None,
                "value": "0.0",
                "value-effect": "None",
            },
        )
    )
    sync_broker(broker, factory, now=lambda: datetime(2026, 10, 10, 13, tzinfo=UTC))
    entry = _entry(factory, cache)
    assert entry.position.status.value == "closed" and not entry.settled_by_app
    assert entry.position.realized_pl == Decimal("200")


def test_a_broker_that_cant_sync_doesnt_hold_up_another_brokers_expirations(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    with factory() as session:  # Schwab, logged out since the afternoon, nothing expiring
        session.add(SyncState(broker="schwab", account_number="12345678",
                              last_attempt_at=OPENED, synced_through=OPENED))  # fmt: skip
        session.commit()
    record, calls = _run(factory)
    assert calls == [] and record["settled"] == 1 and record["done"]


class TwoAccounts(Broker):
    """Two accounts, each holding an expiring put."""

    def accounts(self):
        return [BrokerAccount("tastytrade", "5WX01234", "Alpha"),
                BrokerAccount("tastytrade", "5WX05678", "Beta")]  # fmt: skip

    def transactions(self, number, start, end):
        alpha = number == "5WX01234"
        row = _row(1 if alpha else 2, **{"order-id": 10 if alpha else 20})
        return [RawTransaction(str(row["id"]), OPENED, "Trade", row)]


def test_an_account_that_hasnt_synced_doesnt_hold_up_the_others(factory):
    """An account holding expiring trades that hasn't synced since the close (Schwab
    logged out) waits on its own: the accounts that synced are settled now."""
    expiry_settle.set_on(factory, True)
    sync_broker(TwoAccounts(), factory, now=lambda: SYNCED)
    with factory() as session:  # Beta's last sync was before the close
        state = session.get(SyncState, ("tastytrade", "5WX05678"))
        state.synced_through = OPENED
        session.commit()
    record, _ = _run(factory, sync=lambda: None)
    assert record["settled"] == 1 and not record["done"]
    [skip] = record["skipped"]
    assert "hasn't synced since the close" in skip["reason"]
    assert _entry(factory).position.status.value == "closed"  # Alpha's trade
    late = datetime(2026, 10, 10, 0, 5, tzinfo=UTC)  # 8:05 PM: the day's last try
    record, _ = _run(factory, now=late, sync=lambda: None)
    assert record["done"] and record["settled"] == 0 and len(record["skipped"]) == 1


def test_a_sync_that_raises_still_settles_the_accounts_that_synced(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(TwoAccounts(), factory, now=lambda: SYNCED)
    with factory() as session:  # Beta's last sync was before the close
        session.get(SyncState, ("tastytrade", "5WX05678")).synced_through = OPENED
        session.commit()

    def boom():
        raise RuntimeError("sync blew up")

    record, _ = _run(factory, sync=boom)
    assert record["settled"] == 1 and not record["done"]
    assert _entry(factory).position.status.value == "closed"  # Alpha's trade


def _settlement(**extra):
    from optionsjourney.core.expiry import Settlement
    from optionsjourney.core.transactions import OptionDetails

    base = dict(
        broker="tastytrade",
        account_number="5WX01234",
        symbol=PUT,
        underlying="SPXW",
        option=OptionDetails("P", Decimal("5800"), DAY),
        sign=-1,
        quantity=Decimal("1"),
        close=Decimal("5850"),
    )
    base.update(extra)
    return Settlement(**base)


def test_existing_settlements_migrate_as_final(factory):
    expiry_settle._store(factory, [_settlement()], AFTER)
    loaded = expiry_settle.load_settlements(factory)[0]
    assert (loaded.status, loaded.source) == ("final", "")


def test_a_prelim_settlement_keeps_its_status_source_and_prelim_close(factory):
    from sqlalchemy import select

    from optionsjourney.db.models import ExpirySettlement

    expiry_settle._store(factory, [_settlement(status="prelim", source="delayed")], AFTER)
    loaded = expiry_settle.load_settlements(factory)[0]
    assert (loaded.status, loaded.source) == ("prelim", "delayed")
    with factory() as session:
        assert session.scalar(select(ExpirySettlement.prelim_close)) == Decimal("5850")


def test_a_correction_changes_the_settlements_version(factory):
    from optionsjourney.db.models import ExpirySettlement

    expiry_settle._store(factory, [_settlement(status="prelim")], AFTER)
    before = expiry_settle.version(factory)
    with factory() as session:
        session.query(ExpirySettlement).update({"finalized_at": AFTER})
        session.commit()
    assert expiry_settle.version(factory) != before


def _alerts(factory):
    with factory() as session:
        return list(session.scalars(select(AppAlert)))


def test_a_prelim_price_settles_and_is_stored_as_prelim(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    record, _ = _run(factory, final=False)
    assert record["settled"] == 1
    with factory() as session:
        row = session.scalars(select(ExpirySettlement)).one()
    assert row.status == "prelim" and row.prelim_close == Decimal("5850")
    assert _entry(factory).position.status.value == "closed"


def test_the_run_says_the_days_pl_in_one_unread_alert(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    _run(factory, final=False)
    [alert] = _alerts(factory)
    assert alert.kind == "settled" and alert.link == "/journal" and alert.read_at is None
    assert alert.title == "Today's expirations settled: P/L $200.00 (prelim)"
    assert alert.detail.startswith("1 trade settled at the close.")
    assert alert.dedupe_key.startswith("settled:2026-10-09:") and len(alert.dedupe_key) < 50
    expiry_settle._save(factory, expiry_settle.LAST_RUN_KEY, "{}")  # run again the same day
    _run(factory, final=False)
    assert len(_alerts(factory)) == 1


def test_a_final_price_says_no_prelim(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    _run(factory)
    [alert] = _alerts(factory)
    assert alert.title == "Today's expirations settled: P/L $200.00"
    assert "preliminary" not in alert.detail


def test_no_alert_when_nothing_settled(factory):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    _run(factory, close=None)
    assert _alerts(factory) == []


# --- the correction pass (#186) ----------------------------------------------------------

NEXT_DAY_EVENING = datetime(2026, 10, 12, 22, 0, tzinfo=UTC)  # Mon 6 PM New York


def _shown(factory):
    def shown():
        view = journal.load_journal(factory, now=NEXT_DAY_EVENING)
        return {
            e.position.key: (
                e.position.name,
                e.position.realized_pl,
                (e.position.broker, e.position.account_number),
            )
            for e in view.entries
            if e.settled_by_app
        }

    return shown


def _official(close, asked=None):
    def official(wanted):
        if asked is not None:
            asked.append(set(wanted))
        return {k: _read(Decimal(close)) for k in wanted}

    return official


def _settle_prelim(factory, close):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    _run(factory, close=Decimal(close), final=False)


def _row_status(factory):
    with factory() as session:
        return session.scalars(select(ExpirySettlement)).one()


def _corrections(factory):
    return [a for a in _alerts(factory) if a.kind == "correction"]


def test_a_prelim_is_corrected_and_alerts_when_pl_moves(factory):
    _settle_prelim(factory, "5790")  # 10 points in the money: P/L -$805 with the fee
    done = expiry_settle.correct_prelims(
        factory, official=_official("5795"), shown=_shown(factory), now=NEXT_DAY_EVENING
    )
    assert done == 1
    row = _row_status(factory)
    assert row.status == "final" and row.close_price == Decimal("5795")
    assert row.prelim_close == Decimal("5790") and row.finalized_at == NEXT_DAY_EVENING
    assert row.price_source == "schwab daily close"
    [alert] = _corrections(factory)
    assert alert.dedupe_key == f"correction:{KEY}:2026-10-09"
    assert alert.title.endswith(": P/L corrected") and alert.link == "/journal"
    assert alert.detail == "SPX closed at 5795, not 5790. P/L -$805.00 → -$305.00."
    assert _entry(factory).position.realized_pl == Decimal("-305")


def test_a_correction_that_changes_nothing_raises_nothing(factory):
    _settle_prelim(factory, "5850")
    done = expiry_settle.correct_prelims(
        factory, official=_official("5849"), shown=_shown(factory), now=NEXT_DAY_EVENING
    )
    assert done == 1 and _row_status(factory).status == "final"
    assert _corrections(factory) == []


def test_corrections_stop_after_the_next_trading_days_8pm(factory):
    from optionsjourney.core.transactions import OptionDetails

    friday = date(2026, 10, 16)
    row = _settlement(status="prelim", option=OptionDetails("P", Decimal("5800"), friday))
    expiry_settle._store(factory, [row], datetime(2026, 10, 16, 20, 20, tzinfo=UTC))
    asked = []
    late = datetime(2026, 10, 20, 0, 5, tzinfo=UTC)  # Mon 10/19 8:05 PM New York
    assert (
        expiry_settle.correct_prelims(
            factory, official=_official("5795", asked), shown=dict, now=late
        )
        == 0
    )
    assert asked == [] and _row_status(factory).status == "prelim"
    in_time = datetime(2026, 10, 19, 23, 55, tzinfo=UTC)  # Mon 7:55 PM
    assert (
        expiry_settle.correct_prelims(
            factory, official=_official("5795", asked), shown=dict, now=in_time
        )
        == 1
    )
    assert asked == [{("SPXW", friday)}] and _row_status(factory).status == "final"


def test_corrections_run_with_the_switch_off(factory):
    _settle_prelim(factory, "5790")
    expiry_settle.set_on(factory, False)
    done = expiry_settle.correct_prelims(
        factory, official=_official("5795"), shown=_shown(factory), now=NEXT_DAY_EVENING
    )
    assert done == 1 and _row_status(factory).status == "final"


def test_correction_works_after_a_restart_and_never_alerts_twice(factory):
    _settle_prelim(factory, "5790")
    for _ in range(2):  # fresh callables each time, like a restarted app
        expiry_settle.correct_prelims(
            factory, official=_official("5795"), shown=_shown(factory), now=NEXT_DAY_EVENING
        )
    assert len(_corrections(factory)) == 1
    assert _row_status(factory).finalized_at == NEXT_DAY_EVENING


def test_each_alert_names_the_close_that_moved_its_trade(factory):
    """Two underlyings corrected in one pass: each trade's alert names its own close."""
    expiry_settle._store(
        factory,
        [
            _settlement(status="prelim", close=Decimal("5790")),
            _settlement(status="prelim", underlying="RUT", symbol="RUTW", close=Decimal("2400")),
        ],
        AFTER,
    )

    def shown():  # a stand-in P/L: each trade's settlement close
        with factory() as session:
            rows = session.scalars(select(ExpirySettlement))
            return {
                f"k-{r.underlying}": (r.underlying, r.close_price, (r.broker, r.account_number))
                for r in rows
            }

    reads = {("SPXW", DAY): _read(Decimal("5795")), ("RUT", DAY): _read(Decimal("2410"))}
    assert expiry_settle.correct_prelims(
        factory, official=lambda wanted: reads, shown=shown, now=NEXT_DAY_EVENING
    ) == 2  # fmt: skip
    details = sorted(a.detail for a in _corrections(factory))
    assert details == [
        "RUT closed at 2410, not 2400. P/L $2,400.00 → $2,410.00.",
        "SPXW closed at 5795, not 5790. P/L $5,790.00 → $5,795.00.",
    ]


RUT_PUT = "RUTW  261009P02400000"


class SpxAndRut(Broker):
    """One account holding an SPX put and a RUT put, both expiring on DAY."""

    def __init__(self):
        rut = {"symbol": RUT_PUT, "underlying-symbol": "RUTW", "order-id": 20}
        self.rows = [_row(1), _row(2, **rut)]


def test_corrections_of_two_closes_rebuild_the_real_positions_cache_between_them(factory):
    """Through the journal and a real positions cache: the second close's correction is
    seen too (the cache's key changes per corrected pair, not just per clock tick)."""
    from optionsjourney.core.transactions import OptionDetails

    sync_broker(SpxAndRut(), factory, now=lambda: SYNCED)
    rut = OptionDetails("P", Decimal("2400"), DAY)
    expiry_settle._store(
        factory,
        [
            _settlement(status="prelim", close=Decimal("5790")),
            _settlement(
                status="prelim", symbol=RUT_PUT, underlying="RUT", option=rut,
                close=Decimal("2390"),
            ),
        ],
        AFTER,
    )  # fmt: skip
    cache = journal.PositionsCache()

    def shown():
        view = journal.load_journal(factory, now=NEXT_DAY_EVENING, cache=cache)
        return {
            e.position.key: (
                e.position.name,
                e.position.realized_pl,
                (e.position.broker, e.position.account_number),
            )
            for e in view.entries
            if e.settled_by_app
        }

    assert len(shown()) == 2
    reads = {("SPXW", DAY): _read(Decimal("5795")), ("RUT", DAY): _read(Decimal("2395"))}
    done = expiry_settle.correct_prelims(
        factory, official=lambda wanted: reads, shown=shown, now=NEXT_DAY_EVENING
    )
    assert done == 2
    details = sorted(a.detail for a in _corrections(factory))
    assert details == [
        "RUT closed at 2395, not 2390. P/L -$800.00 → -$300.00.",
        "SPXW closed at 5795, not 5790. P/L -$800.00 → -$300.00.",
    ]


# --- the broker's record (#186) ------------------------------------------------------------

LATER = datetime(2026, 10, 10, 13, tzinfo=UTC)


def _expiration_row():
    return _row(
        2,
        kind="Receive Deliver",
        action="Buy to Close",
        at=datetime(2026, 10, 10, 12, tzinfo=UTC),
        **{
            "transaction-sub-type": "Expiration",
            "order-id": None,
            "price": None,
            "value": "0.0",
            "value-effect": "None",
        },
    )


def _cash_settled_rows(cash="500.0", **fees):
    """TastyTrade's two rows with no direction: the option's removal, and its cash."""
    common = {
        "transaction-sub-type": "Cash Settled Assignment",
        "order-id": None,
        "price": None,
    }
    at = datetime(2026, 10, 10, 12, tzinfo=UTC)
    removal = _row(2, kind="Receive Deliver", action=None, at=at,
                   **common, value="0.0", **{"value-effect": "None"})  # fmt: skip
    money = _row(3, kind="Receive Deliver", action=None, at=at,
                 **common, value=cash, **{"value-effect": "Debit"}, **fees)  # fmt: skip
    return [removal, money]


def _with_broker_record(factory, rows, settlement):
    broker = Broker()
    sync_broker(broker, factory, now=lambda: SYNCED)
    expiry_settle._store(factory, [settlement], AFTER)
    broker.rows.extend(rows)
    sync_broker(broker, factory, now=lambda: LATER)


def _broker_alerts(factory):
    return [a for a in _alerts(factory) if a.kind == "broker_record"]


def test_a_broker_record_that_matches_raises_nothing(factory):
    _with_broker_record(factory, [_expiration_row()], _settlement(close=Decimal("5850")))
    expiry_settle.alert_broker_records(factory, LATER)
    assert _row_status(factory).broker_alerted_at == LATER
    assert _broker_alerts(factory) == []


def test_a_broker_record_that_differs_raises_one_alert(factory):
    # the app settled at 5795.50: -$450; the broker's cash settlement is -$500
    _with_broker_record(factory, _cash_settled_rows(), _settlement(close=Decimal("5795.5")))
    expiry_settle.alert_broker_records(factory, LATER)
    [alert] = _broker_alerts(factory)
    row = _row_status(factory)
    assert row.broker_alerted_at == LATER
    assert alert.dedupe_key == f"broker:{row.id}" and alert.link == "/journal"
    assert alert.title == "SPX Short Put: your broker's record differs"
    assert alert.detail == "Settled by the app: -$450.00. Your broker: -$500.00."
    expiry_settle.alert_broker_records(factory, LATER)
    assert len(_broker_alerts(factory)) == 1


def test_fees_alone_never_alert(factory):
    rows = _cash_settled_rows(commission="1.00", **{"commission-effect": "Debit"})
    _with_broker_record(factory, rows, _settlement(close=Decimal("5795"), fee=Decimal("0.65")))
    expiry_settle.alert_broker_records(factory, LATER)
    assert _row_status(factory).broker_alerted_at == LATER
    assert _broker_alerts(factory) == []


def test_no_broker_record_yet_leaves_the_row_and_builds_no_book(factory, monkeypatch):
    sync_broker(Broker(), factory, now=lambda: SYNCED)
    expiry_settle._store(factory, [_settlement()], AFTER)

    def no_book(*args, **kwargs):
        raise AssertionError("built the book with nothing to compare")

    monkeypatch.setattr(journal, "build_unsplit", no_book)
    assert expiry_settle.alert_broker_records(factory, LATER) == 0
    assert _row_status(factory).broker_alerted_at is None
    assert _broker_alerts(factory) == []


class SchwabExpired(Broker):
    """Schwab: the put sold, then removed with $0 (its "Removed due to Expiration" row)."""

    name = "Schwab"
    broker = "schwab"

    def __init__(self):
        from tests.brokers.test_schwab_transactions import SELL_TO_OPEN

        removal = {
            **SELL_TO_OPEN,
            "activityId": 91234567899,
            "time": "2026-10-10T12:00:00+0000",
            "type": "RECEIVE_AND_DELIVER",
            "description": "OPTION EXPIRATION",
            "orderId": None,
            "netAmount": 0.0,
            "transferItems": [
                {
                    **SELL_TO_OPEN["transferItems"][0],
                    "amount": 1.0,
                    "cost": 0.0,
                    "price": 0.0,
                    "positionEffect": "CLOSING",
                }
            ],
        }
        self.rows = [SELL_TO_OPEN, removal]

    def accounts(self):
        return [BrokerAccount("schwab", "12345678", "Schwab")]

    def transactions(self, number, start, end):
        return [
            RawTransaction(str(r["activityId"]), datetime.fromisoformat(r["time"]), r["type"], r)
            for r in self.rows
        ]


def test_a_schwab_in_the_money_settlement_is_not_compared(factory):
    """Schwab's in-the-money cash settlement isn't parsed yet (#187): its $0 removal
    would read as "Your broker: $0.00", so the row is marked and nothing is raised."""
    sync_broker(SchwabExpired(), factory, now=lambda: LATER)
    settled = _settlement(broker="schwab", account_number="12345678", close=Decimal("5795"))
    expiry_settle._store(factory, [settled], AFTER)
    assert expiry_settle.alert_broker_records(factory, LATER) == 0
    assert _row_status(factory).broker_alerted_at == LATER
    assert _broker_alerts(factory) == []


# --- final-review fixes (#186) -------------------------------------------------------------


def test_a_prelim_the_brokers_record_replaced_is_left_for_the_broker_check(factory):
    """Overnight the broker's record arrives before the official close: the row stays
    prelim, so the broker check compares the broker's cash with the P/L the user saw."""
    broker = Broker()
    expiry_settle.set_on(factory, True)
    sync_broker(broker, factory, now=lambda: SYNCED)
    _run(factory, close=Decimal("5790"), final=False)  # the app: -$1,000
    broker.rows.extend(_cash_settled_rows())  # the broker: -$500, as at 5795
    sync_broker(broker, factory, now=lambda: LATER)
    done = expiry_settle.correct_prelims(
        factory, official=_official("5795"), shown=_shown(factory), now=NEXT_DAY_EVENING
    )
    assert done == 0
    row = _row_status(factory)
    assert row.status == "prelim" and row.close_price == Decimal("5790")
    expiry_settle.alert_broker_records(factory, NEXT_DAY_EVENING)
    [alert] = _broker_alerts(factory)
    assert alert.detail == "Settled by the app: -$1,000.00. Your broker: -$500.00."


def test_a_correction_whose_alert_fails_is_undone_and_tried_again(factory):
    _settle_prelim(factory, "5790")
    real = _shown(factory)
    calls = []

    def flaky():  # the journal fails once, right after the rows are corrected
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("journal down")
        return real()

    done = expiry_settle.correct_prelims(
        factory, official=_official("5795"), shown=flaky, now=NEXT_DAY_EVENING
    )
    assert done == 0 and _corrections(factory) == []
    row = _row_status(factory)
    assert (row.status, row.close_price, row.finalized_at) == ("prelim", Decimal("5790"), None)
    assert row.price_source == "tastytrade last"
    done = expiry_settle.correct_prelims(
        factory, official=_official("5795"), shown=real, now=NEXT_DAY_EVENING
    )
    assert done == 1 and _row_status(factory).status == "final"
    [alert] = _corrections(factory)
    assert alert.detail == "SPX closed at 5795, not 5790. P/L -$805.00 → -$305.00."


def test_a_correction_alert_names_each_accounts_own_prelim(factory):
    """Accounts settled in separate runs can have different prelims for one close."""
    sync_broker(TwoAccounts(), factory, now=lambda: SYNCED)
    expiry_settle._store(
        factory,
        [
            _settlement(status="prelim", close=Decimal("5790")),
            _settlement(status="prelim", account_number="5WX05678", close=Decimal("5792")),
        ],
        AFTER,
    )
    done = expiry_settle.correct_prelims(
        factory, official=_official("5795"), shown=_shown(factory), now=NEXT_DAY_EVENING
    )
    assert done == 2
    assert sorted(a.detail for a in _corrections(factory)) == [
        "SPXW closed at 5795, not 5790. P/L -$800.00 → -$300.00.",
        "SPXW closed at 5795, not 5792. P/L -$600.00 → -$300.00.",
    ]


def test_a_failing_summary_never_stops_the_run(factory, monkeypatch):
    expiry_settle.set_on(factory, True)
    sync_broker(Broker(), factory, now=lambda: SYNCED)

    def boom(*args, **kwargs):
        raise RuntimeError("alerts down")

    monkeypatch.setattr(expiry_settle.app_alerts, "add", boom)
    record, _ = _run(factory)
    assert record["done"] and record["settled"] == 1
    assert expiry_settle.last_run(factory)["settled"] == 1


def _two_broker_records(factory):
    """An SPX put and a RUT put, each settled by the app and then expired by the broker."""
    from optionsjourney.core.transactions import OptionDetails

    broker = SpxAndRut()
    sync_broker(broker, factory, now=lambda: SYNCED)
    rut = OptionDetails("P", Decimal("2400"), DAY)
    expiry_settle._store(
        factory,
        [
            _settlement(),
            _settlement(symbol=RUT_PUT, underlying="RUT", option=rut, close=Decimal("2450")),
        ],
        AFTER,
    )
    rut_expired = {**_expiration_row(), "id": 4, "symbol": RUT_PUT, "underlying-symbol": "RUTW"}
    broker.rows.extend([_expiration_row(), rut_expired])
    sync_broker(broker, factory, now=lambda: LATER)


def test_the_broker_check_marks_its_rows_in_one_commit(factory):
    """Each settlement commit wakes the journal's watcher, so one commit, not one per
    contract."""
    from sqlalchemy import event

    _two_broker_records(factory)
    marks = []

    def count(session):
        if any(isinstance(o, ExpirySettlement) for o in session.dirty):
            marks.append(1)

    event.listen(factory, "before_commit", count)
    assert expiry_settle.alert_broker_records(factory, LATER) == 0
    assert len(marks) == 1
    with factory() as session:
        stamps = [r.broker_alerted_at for r in session.scalars(select(ExpirySettlement))]
    assert stamps == [LATER, LATER]


def test_one_broker_check_at_a_time(factory):
    _two_broker_records(factory)
    with expiry_settle._broker_check:  # another thread is checking
        assert expiry_settle.alert_broker_records(factory, LATER) == 0
    with factory() as session:
        stamps = [r.broker_alerted_at for r in session.scalars(select(ExpirySettlement))]
    assert stamps == [None, None]


def test_only_the_account_with_the_prelim_is_tagged_prelim(factory):
    """Two accounts hold the same contract: one settled at the 4:15 price, the other
    already final. Only the first says prelim, with the price and its source."""
    sync_broker(TwoAccounts(), factory, now=lambda: SYNCED)
    expiry_settle._store(
        factory,
        [
            _settlement(status="prelim", source="tastytrade last", close=Decimal("5790")),
            _settlement(account_number="5WX05678", source="schwab daily close"),
        ],
        AFTER,
    )
    view = journal.load_journal(factory, now=AFTER)
    alpha = journal.find_entry(view, KEY)
    beta = journal.find_entry(view, "tastytrade:5WX05678:20")
    assert alpha.settled_prelim and not beta.settled_prelim
    assert beta.settled_by_app and beta.prelim_note == ""
    assert alpha.prelim_note.startswith("Settled at SPXW 5790 (tastytrade last) at 4:15 PM.")
