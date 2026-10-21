import json
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from tests.services import test_bot_adopt as fixtures
from tests.services.bot_fakes import FakeChain, FakeHub, FakeTasty, make

from optionsjourney.app import create_app
from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.transactions import RawTransaction
from optionsjourney.db.models import Bot, BotIntegrityAlert, BotRun, BotTradeMatch, Tag
from optionsjourney.services import bot_entry, bot_loops
from optionsjourney.services import journal as journal_service
from optionsjourney.web.routes import journal as journal_routes

ORIGIN = {"Origin": "http://localhost"}
NOW = datetime.now(UTC)


def _tasty(transaction_id, action, price, effect, symbol, order_id=10):
    return {
        "id": transaction_id,
        "symbol": symbol,
        "instrument-type": "Equity Option",
        "underlying-symbol": "SPXW",
        "transaction-type": "Trade",
        "action": action,
        "quantity": "1",
        "price": price,
        "value": str(float(price) * 100),
        "value-effect": effect,
        "executed-at": (NOW - timedelta(days=1)).isoformat(),
        "order-id": order_id,
    }


IRON_CONDOR = [
    _tasty(1, "Buy to Open", "1.00", "Debit", "SPXW  261120P05780000"),
    _tasty(2, "Sell to Open", "2.00", "Credit", "SPXW  261120P05800000"),
    _tasty(3, "Sell to Open", "1.50", "Credit", "SPXW  261120C06000000"),
    _tasty(4, "Buy to Open", "0.50", "Debit", "SPXW  261120C06020000"),
]
KEY = "tastytrade:5WX01234:10"


class FakeTastyTrade:
    name = "TastyTrade"
    broker = "tastytrade"

    def __init__(self, rows=None):
        self.rows = IRON_CONDOR if rows is None else rows

    def accounts(self):
        return [BrokerAccount("tastytrade", "5WX01234", "Alpha")]

    def balance(self, number):
        raise AssertionError("not used")

    def transactions(self, number, start, end):
        return [
            RawTransaction(str(r["id"]), datetime.fromisoformat(r["executed-at"]), "Trade", r)
            for r in self.rows
        ]


@pytest.fixture
def client(settings, fake_credentials):
    broker = FakeTastyTrade()
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/sync", headers=ORIGIN)
    return client


def _trade_url(key=KEY):
    return "/journal/trades/" + quote(key, safe=":")


def test_home_is_the_journal(client):
    response = client.get("/")
    assert response.status_code == 302 and response.location.endswith("/journal")


def test_journal_page(client):
    html = client.get("/journal").get_data(as_text=True)
    assert "All trades" in html
    assert "SPX Iron Condor" in html
    assert "2.00 cr" in html
    # Open iron condor: 2.00 + 1.50 - 1.00 - 0.50 = 2.00 credit, $200 of open premium
    assert "Open Premium" in html and "$200" in html
    assert "journal_live.js" in html  # reloads itself when new fills are synced
    assert "data-schedule-open" in html and 'id="schedule-drawer"' in html  # Tracker2's button


def test_edit_a_trade(client):
    html = client.get(_trade_url()).get_data(as_text=True)
    assert "Split into put and call spreads" in html

    response = client.post(
        _trade_url(),
        data={
            "name": "Friday IC",
            "tags": "earnings",
            "notes": "Tight wings",
            "close_reason": "",
            "back": "/journal?status=open",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 302 and response.location.endswith("/journal?status=open")
    html = client.get("/journal").get_data(as_text=True)
    assert "Friday IC" in html and "earnings" in html and "Tight wings" in html


def test_a_trades_tags_are_ticks_and_its_bots_tag_stays(client):
    """The Tags box is the tick dropdown; the bot's tag is ticked and locked, links to
    the bot, and isn't stored when the trade is saved."""
    with client.application.extensions["optionsjourney"].session_factory() as session:
        zero = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
        bot = Bot(name="Morning condor", tag_id=zero, broker="tastytrade",
                  account_number="5WX01234", live=False, enabled=False, max_per_trade=1,
                  max_per_day=1, created_at=datetime.now(UTC))  # fmt: skip
        session.add(bot)
        session.flush()
        session.add(BotTradeMatch(position_key=KEY, bot_id=bot.id, matched_at=datetime.now(UTC)))
        session.commit()
    html = client.get(_trade_url()).get_data(as_text=True)
    assert 'data-tag-list="No tags"' in html and 'data-tag-fixed="[&#34;0DTE&#34;]"' in html
    assert 'name="tags" value=""' in html  # the bot's tag isn't in the box itself
    assert "change the bot's Journal tag" in html

    client.post(_trade_url(), data={"name": "", "tags": "", "new_tags": "Wings", "notes": "",
                                    "close_reason": ""}, headers=ORIGIN)  # fmt: skip
    view = journal_service.load_journal(
        client.application.extensions["optionsjourney"].session_factory
    )
    entry = journal_service.find_entry(view, KEY)
    assert [t.name for t in entry.tags] == ["0DTE", "Wings"]
    assert [t.name for t in entry.fixed_tags] == ["0DTE"]


def test_tag_ticked_trades_from_the_select_bar(client):
    html = client.get("/journal").get_data(as_text=True)
    assert 'id="select-edit"' in html
    response = client.post(
        "/journal/trades/edit-many",
        data={"trade": [KEY], "add_tags": "bulk-tag", "back": "/journal"},
        headers=ORIGIN,
    )
    assert response.status_code == 302
    assert "bulk-tag" in client.get("/journal").get_data(as_text=True)


def test_back_link_never_leaves_the_app(client):
    response = client.post(
        _trade_url(),
        data={
            "name": "",
            "tags": "",
            "notes": "",
            "close_reason": "",
            "back": "https://evil.example/",
        },
        headers=ORIGIN,
    )
    assert response.location.endswith("/journal")


def test_split_and_combine(client):
    client.post(_trade_url() + "/split", data={"split": "1"}, headers=ORIGIN)
    html = client.get("/journal").get_data(as_text=True)
    assert "SPX Put Credit Spread" in html and "SPX Call Credit Spread" in html
    assert "SPX Iron Condor" not in html

    half = client.get(_trade_url(KEY + "#put")).get_data(as_text=True)
    assert "Combine back into one iron condor" in half

    client.post(_trade_url() + "/split", data={"split": "0"}, headers=ORIGIN)
    assert "SPX Iron Condor" in client.get("/journal").get_data(as_text=True)


def test_a_bots_trade_shows_its_bot_badge(client):
    with client.application.app_context():
        factory = client.application.extensions["optionsjourney"].session_factory
        with factory() as session:
            bot = Bot(
                name="Morning condor",
                tag_id=session.scalar(select(Tag.id)),
                broker="tastytrade",
                account_number="5WX01234",
                max_per_trade=1,
                max_per_day=1,
                split_iron_condors=False,
                created_at=NOW,
            )
            session.add(bot)
            session.flush()
            session.add(
                BotRun(
                    bot_id=bot.id,
                    key="web-badge",
                    trading_date=NOW.date(),
                    entry_time="09:45",
                    status="filled",
                    live=False,
                    broker_order_id="10",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.commit()
            bot_id = bot.id

    html = client.get("/journal").get_data(as_text=True)
    # Unnamed, it takes the bot's name, so the badge needn't repeat it
    assert "badge--bot" in html and 'title="Opened by Morning condor"' in html
    assert "</svg></a>" in html and "</svg> Morning condor" not in html
    assert f"/automation/{bot_id}/runs" in html
    assert "Morning condor" in client.get(_trade_url()).get_data(as_text=True)


def test_an_open_bot_trade_that_needs_a_look_gets_a_red_badge(client):
    """#242 (Tracker2's red bot chip): a run listed under Needs a look turns its open
    trade's bot badge red, the findings in its tooltip, linking to the run."""
    with client.application.app_context():
        factory = client.application.extensions["optionsjourney"].session_factory
        with factory() as session:
            bot = Bot(name="Morning condor", tag_id=session.scalar(select(Tag.id)),
                      broker="tastytrade", account_number="5WX01234", max_per_trade=1,
                      max_per_day=1, split_iron_condors=False, created_at=NOW)  # fmt: skip
            session.add(bot)
            session.flush()
            run = BotRun(bot_id=bot.id, key="web-alert", trading_date=NOW.date(),
                         entry_time="09:45", status="filled", live=True, broker_order_id="10",
                         created_at=NOW, updated_at=NOW)  # fmt: skip
            session.add(run)
            session.flush()
            alert = BotIntegrityAlert(run_id=run.id, check_key="stop_without_order",
                                      subject="side 0", summary="Put side: its stop has no order",
                                      first_seen_at=NOW)  # fmt: skip
            session.add(alert)
            session.commit()
            run_id, alert_id = run.id, alert.id

    html = client.get("/journal").get_data(as_text=True)
    assert "badge--alert" in html
    assert "needs a look: Put side: its stop has no order" in html
    assert f'href="/automation/runs/{run_id}"' in html

    with factory() as session:  # fixed: the check clears it
        session.get(BotIntegrityAlert, alert_id).cleared_at = NOW
        session.commit()
    html = client.get("/journal").get_data(as_text=True)
    assert "badge--alert" not in html and "badge--bot" in html


def test_a_closed_bot_trade_never_needs_a_look(client, monkeypatch):
    from types import SimpleNamespace

    from optionsjourney.core.positions import Status
    from optionsjourney.services import bot_integrity

    found = {7: ["x"], 8: ["y"]}
    monkeypatch.setattr(bot_integrity, "findings_by_run", lambda factory, runs: found)
    bot = lambda run_id: SimpleNamespace(run_id=run_id)  # noqa: E731
    entries = [
        SimpleNamespace(position=SimpleNamespace(key="open", status=Status.OPEN), bot=bot(7)),
        SimpleNamespace(position=SimpleNamespace(key="closed", status=Status.CLOSED), bot=bot(8)),
        SimpleNamespace(position=SimpleNamespace(key="manual", status=Status.OPEN), bot=None),
    ]
    with client.application.app_context():
        assert journal_routes._needs_a_look(entries) == {"open": ["x"]}


def test_unknown_trade_is_404(client):
    assert client.get(_trade_url("nope")).status_code == 404


def test_dashboards(client):
    response = client.post(
        "/journal/dashboards", data={"name": "Open trades", "status": "open"}, headers=ORIGIN
    )
    assert "dashboard=1" in response.location and "status=open" in response.location

    # Opening the tab applies its saved filters
    response = client.get("/journal?dashboard=1")
    assert response.status_code == 302 and "status=open" in response.location
    html = client.get(response.location).get_data(as_text=True)
    assert "Open trades" in html and "Delete dashboard" in html

    assert "dashboard=1" in client.get("/journal").get_data(as_text=True)  # its tab link
    client.post("/journal/dashboards/1/delete", headers=ORIGIN)
    # (the tab's link, not its name: the trading-switches menu says "Open trades are still watched")
    assert "dashboard=1" not in client.get("/journal").get_data(as_text=True)


def test_tags_page(client):
    client.post("/journal/tags", data={"name": "MEIC"}, headers=ORIGIN)
    html = client.get("/journal/tags").get_data(as_text=True)
    assert "MEIC" in html and "Wheel" in html

    client.post("/journal/tags", data={"name": "meic"}, headers=ORIGIN)
    html = client.get("/journal/tags").get_data(as_text=True)
    assert "There&#39;s already a tag called meic." in html

    with client.application.extensions["optionsjourney"].session_factory() as session:
        meic = session.scalar(select(Tag.id).where(Tag.name == "MEIC"))
    client.post(f"/journal/tags/{meic}/rename", data={"name": "MEIC v2"}, headers=ORIGIN)
    assert "MEIC v2" in client.get("/journal/tags").get_data(as_text=True)
    client.post(f"/journal/tags/{meic}/delete", headers=ORIGIN)
    assert "MEIC v2" not in client.get("/journal/tags").get_data(as_text=True)


def test_a_tag_a_bot_files_under_cant_be_deleted_from_the_tags_page(client):
    with client.application.extensions["optionsjourney"].session_factory() as session:
        zero = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
        session.add(Bot(name="Morning condor", tag_id=zero, broker="tastytrade",
                        account_number="5WX01234", live=False, enabled=False, max_per_trade=1,
                        max_per_day=1, created_at=datetime.now(UTC)))  # fmt: skip
        session.commit()
    html = client.get("/journal/tags").get_data(as_text=True)
    assert "so it can't be deleted" in html and "pick another Journal tag" in html
    link = r'bot: <a class="link" href="/automation/\d+#basics"[^>]*>Morning condor</a>'
    assert re.search(link, html)
    client.post(f"/journal/tags/{zero}/delete", headers=ORIGIN)
    html = client.get("/journal/tags").get_data(as_text=True)
    assert "Give the bot another tag first." in html


def test_expired_trade_waiting_on_the_broker(settings, fake_credentials):
    # A short put that expired on 2026-09-04, with no expiration record yet (#2)
    expired = _tasty(1, "Sell to Open", "1.00", "Credit", "SPXW  260904P05800000")
    expired["executed-at"] = datetime(2026, 9, 4, 14, 0, tzinfo=UTC).isoformat()
    broker = FakeTastyTrade([expired])
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    client = app.test_client()
    client.post("/sync", headers=ORIGIN)

    html = client.get("/journal").get_data(as_text=True)
    assert "Waiting on broker" in html
    html = client.get(_trade_url()).get_data(as_text=True)
    assert "Expired · waiting on broker" in html


def _closed_trade_client(settings, fake_credentials, day=datetime(2026, 9, 1, tzinfo=UTC)):
    """A short put sold (Tue Sep 1 2026) for 2.00 and bought back the same day for 0.50."""
    opened = _tasty(1, "Sell to Open", "2.00", "Credit", "SPXW  261016P05800000", order_id=10)
    closed = _tasty(2, "Buy to Close", "0.50", "Debit", "SPXW  261016P05800000", order_id=11)
    opened["executed-at"] = day.replace(hour=14).isoformat()
    closed["executed-at"] = day.replace(hour=15).isoformat()
    broker = FakeTastyTrade([opened, closed])
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    client = app.test_client()
    client.post("/sync", headers=ORIGIN)
    return client


def test_pl_calendar(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    html = client.get("/journal/pl?month=2026-09").get_data(as_text=True)
    assert "P/L calendar" in html
    assert "September 2026" in html
    assert "+$150" in html  # Tue Sep 1
    assert 'href="/journal/pl?month=2026-08"' in html  # previous month
    assert 'href="/journal/pl?month=2026-10"' in html  # next month


def test_pl_calendar_keeps_the_filters(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    html = client.get("/journal/pl?month=2026-09&status=closed").get_data(as_text=True)
    assert "status=closed" in html.split("September 2026")[0].rsplit("<a", 1)[-1]


def test_pl_calendar_ignores_a_bad_month(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    assert client.get("/journal/pl?month=nonsense").status_code == 200


def test_pl_over_time_chart(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    html = client.get("/journal/pl").get_data(as_text=True)
    assert "P/L over time" in html
    # The chart's data, for the script that draws it
    assert '"day": "2026-09-01"' in html and '"cumulative": 150.0' in html
    # The same numbers as a table, without the chart
    assert "Show as a table" in html and "Sep 2026" in html


def test_trading_metrics_page(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    html = client.get("/journal/metrics").get_data(as_text=True)
    assert "Trading metrics" in html
    assert "Profit factor" in html and "Max drawdown" in html
    assert "Day of the week" in html and "Tue" in html
    assert "+$150" in html or "$150.00" in html


def test_trading_metrics_follow_dashboards(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    client.post("/journal/dashboards", data={"name": "Closed", "status": "closed"}, headers=ORIGIN)
    response = client.get("/journal/metrics?dashboard=1")
    assert response.status_code == 302
    assert "status=closed" in response.location and "/journal/metrics" in response.location
    html = client.get(response.location).get_data(as_text=True)
    assert "Trading metrics" in html


def test_journal_links_to_metrics_with_its_filters(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    html = client.get("/journal?status=closed").get_data(as_text=True)
    assert 'href="/journal/metrics?status=closed"' in html


def test_journal_shows_only_the_trades(settings, fake_credentials):
    # The calendar and charts live in the P/L view (#48)
    client = _closed_trade_client(settings, fake_credentials)
    html = client.get("/journal").get_data(as_text=True)
    assert "P/L calendar" not in html and "P/L over time" not in html
    assert 'href="/journal/pl"' in html


def test_journal_opens_on_open_trades(settings, fake_credentials):
    """Open trades and those closed today; older closed ones only when asked for; P/L
    still shows them all (#68)."""
    client = _closed_trade_client(settings, fake_credentials)
    html = client.get("/journal").get_data(as_text=True)
    assert "$150.00" not in html
    assert '<option value="" selected>Open + closed today</option>' in html
    assert 'href="/journal/pl"' in html  # P/L keeps its own default
    assert "Showing open + closed today trades only" in html and 'href="/journal?status=all"' in html

    html = client.get("/journal?status=all").get_data(as_text=True)
    assert "$150.00" in html
    assert '<option value="all" selected>Open and closed</option>' in html
    assert 'href="/journal/pl?status=all"' in html
    assert "Showing" not in html

    html = client.get("/journal/pl").get_data(as_text=True)
    assert '<option value="" selected>Open and closed</option>' in html
    assert '<option value="open" >Open</option>' in html


def test_journal_opens_with_trades_closed_today(settings, fake_credentials):
    # New York's date, not UTC's: in the evening they differ.
    today = datetime.combine(datetime.now(ZoneInfo("America/New_York")).date(), time(), tzinfo=UTC)
    client = _closed_trade_client(settings, fake_credentials, day=today)
    assert "$150.00" in client.get("/journal").get_data(as_text=True)


def test_a_dashboard_keeps_its_status(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    client.post("/journal/dashboards", data={"name": "All", "status": "all"}, headers=ORIGIN)
    response = client.get("/journal?dashboard=1")
    assert "status=all" in response.location
    assert "$150.00" in client.get(response.location).get_data(as_text=True)


def test_pl_view_follows_dashboards(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    client.post("/journal/dashboards", data={"name": "Closed", "status": "closed"}, headers=ORIGIN)
    response = client.get("/journal/pl?dashboard=1")
    assert response.status_code == 302 and "/journal/pl" in response.location


def test_percent_never_shows_minus_zero():
    from decimal import Decimal

    from optionsjourney.web.formatting import percent

    assert percent(Decimal("-0.006"), 1) == "0.0%"
    assert percent(Decimal("75"), 0) == "75%"
    assert percent(Decimal("-12.34"), 1) == "-12.3%"
    assert percent(None) == "—"


def test_journal_summary_like_tracker2(settings, fake_credentials):
    # Sold for 2.00 and bought back for 0.50: +$150 on $200 of premium (#49)
    client = _closed_trade_client(settings, fake_credentials)
    html = client.get("/journal?status=all").get_data(as_text=True)
    for label in (
        "Closed P/L",
        "Open Premium",
        "Premium",
        "Avg/Trade/Lot",
        "Avg Winner/Lot",
        "Avg Loser/Lot",
        "Max Winner/Lot",
        "Max Loser/Lot",
        "Winners",
        "Win Rate",
        "Capture Rate",
    ):
        assert label in html, label
    assert "$150.00" in html and "$200" in html
    assert "75.0%" in html  # capture rate: 150 / 200


def test_expired_trade_shows_an_estimate_from_the_brokers_close(settings, fake_credentials):
    # Short 5800 put sold for 1.00, expired 2026-09-04 with SPX at 5900: kept the $100 (#23)
    expired = _tasty(1, "Sell to Open", "1.00", "Credit", "SPXW  260904P05800000")
    expired["executed-at"] = datetime(2026, 9, 4, 14, 0, tzinfo=UTC).isoformat()

    class WithCloses(FakeTastyTrade):
        def market_data(self, by_type):
            return [{"symbol": "SPX", "prev-close": "5900", "prev-close-date": "2026-09-04"}]

    broker = WithCloses([expired])
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    client = app.test_client()
    client.post("/sync", headers=ORIGIN)
    html = client.get("/journal").get_data(as_text=True)
    assert "est." in html and "$100.00" in html
    trade = client.get(_trade_url()).get_data(as_text=True)
    assert "Estimated P/L" in trade and "$100.00" in trade


class _MarksHub:
    """A quote hub with fixed prices, for the live P/L endpoint."""

    def __init__(self, marks):
        self.marks, self.subscribed = marks, set()

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        from optionsjourney.core.quotes import Quote

        mark = self.marks.get(key)
        return (
            None if mark is None else Quote(key, mark, mark, mark, "tastytrade", datetime.now(UTC))
        )

    def status(self):
        return {"tastytrade": "streaming"}


def test_live_open_pl(settings, fake_credentials):
    # The open iron condor: 2.00 + 1.50 - 1.00 - 0.50 = $200 credit
    marks = {
        "SPXW  261120P05780000": Decimal("0.40"),
        "SPXW  261120P05800000": Decimal("1.00"),
        "SPXW  261120C06000000": Decimal("0.70"),
        "SPXW  261120C06020000": Decimal("0.20"),
    }
    hub = _MarksHub(marks)
    broker = FakeTastyTrade()
    app = create_app(
        settings, credentials=fake_credentials, broker_clients=lambda: [broker], quote_hub=hub
    )
    client = app.test_client()
    client.post("/sync", headers=ORIGIN)
    body = client.get("/api/open-pl").get_json()
    assert hub.subscribed == set(marks)
    # closing now costs 1.00 + 0.70 - 0.40 - 0.20 = 1.10 ($110): $90 up
    assert body["positions"] == {KEY: 90.0}
    assert body["total"] == 90.0 and body["priced"] == 1 and body["open"] == 1


def test_journal_shows_a_live_open_pl_box(client):
    html = client.get("/journal").get_data(as_text=True)
    assert "Open P/L" in html and "open_pl.js" in html
    assert f'data-open-pl-key="{KEY}"' in html


def test_a_view_saved_with_no_filters_opens(client):
    """A view saved with no filters used to redirect to itself forever."""
    made = client.post(
        "/journal/dashboards", data={"name": "Everything"}, headers={"Origin": "http://localhost"}
    )
    view = client.get(made.location)
    assert view.status_code == 200 and "Everything" in view.get_data(as_text=True)
    for page in ("/journal/pl", "/journal/metrics"):
        assert client.get(f"{page}?{made.location.split('?')[1]}").status_code == 200


def test_empty_filter_values_are_ignored(client):
    html = client.get("/journal?tag=").get_data(as_text=True)
    assert "Tags (1)" not in html


def test_stocks_tab(client):
    html = client.get("/journal").get_data(as_text=True)
    assert 'href="/journal?kind=stocks"' in html
    stocks = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert "SPX Iron Condor" not in stocks
    assert 'tabs__tab tabs__tab--active" href="/journal?kind=stocks"' in stocks
    assert '<input type="hidden" name="kind" value="stocks">' in stocks  # Apply keeps the tab
    assert client.get("/journal/pl?kind=stocks").status_code == 200


def test_all_trades_leaves_out_stock_trades(client, monkeypatch):
    assert "SPX Iron Condor" in client.get("/journal").get_data(as_text=True)
    monkeypatch.setattr(journal_service, "is_stock", lambda position: True)
    assert "SPX Iron Condor" not in client.get("/journal").get_data(as_text=True)


def test_stocks_tab_shows_holdings_only(client):
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert "No shares held" in html and "Open Premium" not in html
    # it says it gathers every share in every connected account, not just the app's trades
    assert "across all your connected accounts" in html
    assert client.get("/api/stocks?kind=stocks").get_json() == {"rows": []}


def test_stocks_tab_keeps_only_the_account_filter(client):
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert 'name="account"' in html
    assert 'name="status"' not in html and "Trading metrics" not in html


def test_search_and_dates_in_the_filter_bar(client):
    html = client.get("/journal?q=condor&start=2026-01-01").get_data(as_text=True)
    assert 'name="q" value="condor"' in html and 'name="start" value="2026-01-01"' in html
    assert "SPX Iron Condor" in html
    assert "SPX Iron Condor" not in client.get("/journal?q=wheel").get_data(as_text=True)


def test_sorting_links(client):
    html = client.get("/journal?status=open").get_data(as_text=True)
    assert "aria-sort" not in html  # grouped by default (#151): no column is the sort
    assert 'aria-sort="descending"' in client.get("/journal?sort=opened").get_data(as_text=True)
    assert "sort=pl" in html and "status=open" in html  # headings keep the filters
    sorted_html = client.get("/journal?sort=pl&dir=asc").get_data(as_text=True)
    assert "P/L ▲" in sorted_html


def test_rows_show_legs_premium_and_fees(client):
    html = client.get("/journal").get_data(as_text=True)
    assert "+1 5780 P 11/20" in html and "-1 5800 P 11/20" in html  # the iron condor's legs
    assert 'data-mark-key="SPXW  261120P05800000"' in html
    assert "$200 <span" in html  # its premium, 2.00 cr
    assert client.get("/api/open-pl").get_json()["marks"] == {}


def test_open_trades_open_the_risk_graph(client):
    html = client.get("/journal").get_data(as_text=True)
    assert "/risk-graph?trade=tastytrade" in html and 'id="risk-drawer"' in html
    assert "/analyze?trade=" not in html and "Risk graph together" in html
    grid = client.get("/journal/grid?status=open").get_data(as_text=True)
    assert "SPX Iron Condor" in grid and "/risk-graph?trade=" in grid and "embed=1" in grid
    assert "/analyze?trade=" not in grid
    assert "No open option trades" in client.get("/journal/grid?q=nothing").get_data(as_text=True)
    page = client.get(_trade_url()).get_data(as_text=True)
    assert "/risk-graph?trade=" in page and "Analyze this trade" not in page


def test_the_grid_ticks_trades_for_one_combined_graph_with_totals(client):
    """#215 (Tracker2's grid view): an overview table with a box per trade, ticked to
    start, a select-all box, a totals line that collapses the table, and one graph of
    the ticked trades together."""
    grid = client.get("/journal/grid?status=open").get_data(as_text=True)
    assert 'id="grid-check-all"' in grid and 'id="grid-table-toggle"' in grid
    assert grid.count('class="grid-check"') == grid.count("data-grid-key=") >= 1
    assert 'class="grid-check" value="' + KEY + '" checked' in grid
    assert grid.count("title=\"Risk graph of") == 1  # one graph, not one per trade
    assert "trade=" + quote(KEY, safe="") + "&embed=1" in grid
    assert 'data-url="/api/open-pl?status=open"' in grid and "journal_grid.js" in grid
    assert "Total open P/L" in grid and "$200" in grid  # the condor's premium


def test_the_journal_shows_chips_and_manage_on_the_right_half(client):
    from optionsjourney.db.models import BotTradeOverride

    with client.application.app_context():
        factory = client.application.extensions["optionsjourney"].session_factory
        with factory() as session:
            bot = Bot(
                name="Morning condor", tag_id=session.scalar(select(Tag.id)),
                broker="tastytrade", account_number="5WX01234", max_per_trade=1,
                max_per_day=1, split_iron_condors=True, created_at=NOW,
            )  # fmt: skip
            session.add(bot)
            session.flush()
            legs = [
                {"symbol": "SPXW  261120C06000000", "side": "sell", "ratio": 1, "group": 0},
                {"symbol": "SPXW  261120C06020000", "side": "buy", "ratio": 1, "group": 0},
                {"symbol": "SPXW  261120P05800000", "side": "sell", "ratio": 1, "group": 1},
                {"symbol": "SPXW  261120P05780000", "side": "buy", "ratio": 1, "group": 1},
            ]
            run = BotRun(
                bot_id=bot.id, key="web-chips", trading_date=NOW.date(), entry_time="09:45",
                status="filled", live=True, broker_order_id="10", legs=legs,
                filled_legs=[{"symbol": leg["symbol"]} for leg in legs], contracts=1,
                created_at=NOW, updated_at=NOW,
            )  # fmt: skip
            session.add(run)
            session.flush()
            session.add(BotTradeOverride(run_id=run.id, group_position=1, target_pct=25,
                                         changed_at=NOW, log=[]))  # fmt: skip
            session.commit()
            run_id = run.id
    html = client.get("/journal").get_data(as_text=True)
    rows = html.split('class="select-trade"')  # one piece per trade row
    put = next(r for r in rows if "261120P05800000" in r)
    call = next(r for r in rows if "261120C06000000" in r)
    assert "Target 25%" in put and "Target 25%" not in call
    assert f'href="/automation/runs/{run_id}#manage"' not in put  # the gear's panel manages it
    # Bots slice 20: the cell says which run it is, and its chips can be read again live
    assert f'data-run-id="{run_id}"' in put and "SPXW  261120P05800000" in put
    assert "bot_chips.js" in html
    put_symbols = "SPXW  261120P05800000,SPXW  261120P05780000"
    data = client.get(f"/journal/bot-chips/{run_id}", query_string={"symbols": put_symbols})
    assert [c["label"] for c in data.get_json()["chips"]] == ["Target 25%"]
    other = client.get(f"/journal/bot-chips/{run_id}?symbols=SPXW  261120C06000000").get_json()
    assert other == {"chips": []}


# --- Handing a trade to a bot (bots slice 12) --------------------------------------------


@pytest.fixture
def adopting(settings, fake_credentials, monkeypatch):
    """A same-day iron condor opened by hand, a live bot on its account, and the bot
    machinery (broker, quotes, clock) faked."""
    broker = FakeTastyTrade(rows=fixtures.condor())
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/sync", headers=ORIGIN)
    factory = app.extensions["optionsjourney"].session_factory
    tasty = FakeTasty()
    deps = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: fixtures.NOW, background=lambda fn, *a: fn(*a),
    )  # fmt: skip
    deps.tasty = tasty
    fixtures.hold(deps)
    fixtures.quote(deps)
    monkeypatch.setattr(bot_loops, "deps_for", lambda services: deps)
    monkeypatch.setattr(journal_routes, "_now", lambda: fixtures.NOW)
    client.bot_id = make(factory, live=True)
    client.deps = deps
    client.key = fixtures.KEY
    return client


def test_a_same_day_condor_offers_the_hand_over(adopting):
    html = adopting.get(_trade_url(adopting.key)).get_data(as_text=True)
    assert "Hand this trade to a bot" in html
    page = adopting.get(_trade_url(adopting.key) + "/adopt").get_data(as_text=True)
    assert f'<option value="{adopting.bot_id}"' in page and "Check" in page


def test_the_hand_over_button_and_check_table_have_tips(adopting):
    panel = adopting.get(_trade_url(adopting.key) + "/panel", query_string={"back": "/journal"})
    assert 'id="tip-manage-hand-to-bot"' in panel.get_data(as_text=True)
    page = adopting.get(_trade_url(adopting.key) + "/adopt").get_data(as_text=True)
    assert 'id="tip-adopt-fill"' in page
    url = _trade_url(adopting.key) + "/adopt/check"
    found = adopting.post(url, data={"bot": adopting.bot_id}, headers=ORIGIN).get_data(as_text=True)
    for tip in ("adopt-fill", "adopt-group", "adopt-profit-target"):
        assert f'id="tip-{tip}"' in found, tip


def test_an_older_trade_says_which_trades_can_be_handed_over(client):
    html = client.get(_trade_url()).get_data(as_text=True)
    assert "Hand this trade to a bot" not in html
    assert "Only an options trade of up to 4 legs, opened today" in html


def test_check_then_hand_over(adopting):
    url = _trade_url(adopting.key) + "/adopt"
    html = adopting.post(url + "/check", data={"bot": adopting.bot_id}, headers=ORIGIN)
    html = html.get_data(as_text=True)
    assert "4.80" in html and "3.90" in html and "Hand to IC" in html
    # The pairing the bot will use, shown before you confirm
    assert "sell SPXW  261013C05900000 / buy SPXW  261013C05975000" in html
    assert adopting.deps.tasty.calls == []
    fingerprint = html.split('name="fingerprint" value="')[1].split('"')[0]
    response = adopting.post(
        url, data={"bot": adopting.bot_id, "fingerprint": fingerprint}, headers=ORIGIN
    )
    assert response.status_code == 302 and "/automation/runs/" in response.location
    run_page = adopting.get(response.location).get_data(as_text=True)
    assert "stop at 4.80" in run_page and "A trade you opened, handed to this bot" in run_page
    trade = adopting.get(_trade_url(adopting.key), follow_redirects=True).get_data(as_text=True)
    assert "Opened by IC" in trade and "Hand this trade to a bot" not in trade  # shown split


def test_the_check_says_what_rests_and_who_holds_the_stop(adopting):
    """Final review I2 (spec section 6): a credit group with one sold leg set to Profit
    target rests its target at TastyTrade and the app holds its stop on the short: the
    check says both, with the app-running caveat, and the group's notes."""
    from dataclasses import replace

    from optionsjourney.services import bot_store

    factory = adopting.application.extensions["optionsjourney"].session_factory
    bot = bot_store.load(factory, adopting.bot_id)
    groups = tuple(replace(g, broker_exit="target", target_pct=Decimal("50"))
                   for g in bot.groups)  # fmt: skip
    assert bot_store.save(factory, replace(bot, groups=groups), fixtures.NOW)[1] == {}
    bot_store.set_live(factory, adopting.bot_id, True, fixtures.NOW)  # a save turns it off
    html = adopting.post(
        _trade_url(adopting.key) + "/adopt/check", data={"bot": adopting.bot_id}, headers=ORIGIN
    ).get_data(as_text=True)
    assert "Rests at the broker: its profit target" in html
    assert "Stop held by the app on its short" in html
    assert "while the app is running and the computer is awake" in html


def test_the_check_shows_a_groups_notes(adopting):
    from dataclasses import replace

    from optionsjourney.services import bot_store

    factory = adopting.application.extensions["optionsjourney"].session_factory
    bot = bot_store.load(factory, adopting.bot_id)
    groups = tuple(replace(g, target_pct=Decimal("150")) for g in bot.groups)
    assert bot_store.save(factory, replace(bot, groups=groups), fixtures.NOW)[1] == {}
    bot_store.set_live(factory, adopting.bot_id, True, fixtures.NOW)
    found = bot_adopt_check(adopting)
    assert found.ok, found.message
    assert found.sides and any(side.notes for side in found.sides)
    html = adopting.post(
        _trade_url(adopting.key) + "/adopt/check", data={"bot": adopting.bot_id}, headers=ORIGIN
    ).get_data(as_text=True)
    note = next(n for side in found.sides for n in side.notes)
    assert note.replace("'", "&#39;") in html


def bot_adopt_check(client):
    from optionsjourney.services import bot_adopt

    return bot_adopt.check(client.deps, client.key, client.bot_id)


def test_a_refused_check_shows_why_and_offers_no_button(adopting):
    adopting.deps.hub.set(fixtures.CALL, "4.70", "4.80", fixtures.NOW)
    html = adopting.post(
        _trade_url(adopting.key) + "/adopt/check", data={"bot": adopting.bot_id}, headers=ORIGIN
    ).get_data(as_text=True)
    assert "would fire at once" in html and "Hand to IC" not in html


def test_a_stale_confirm_goes_back_to_check(adopting):
    response = adopting.post(
        _trade_url(adopting.key) + "/adopt",
        data={"bot": adopting.bot_id, "fingerprint": "old"},
        headers=ORIGIN,
    )
    assert response.status_code == 302 and response.location.split("?")[0].endswith("/adopt")
    assert "Check again" in adopting.get(response.location).get_data(as_text=True)


def test_the_hand_over_posts_refuse_other_sites(adopting):
    url = _trade_url(adopting.key) + "/adopt"
    other = {"Origin": "https://evil.example"}
    assert (
        adopting.post(url + "/check", data={"bot": adopting.bot_id}, headers=other).status_code
        == 403
    )
    assert adopting.post(url, data={"bot": adopting.bot_id, "fingerprint": "x"},
                         headers=other).status_code == 403  # fmt: skip


def test_a_hand_over_whose_exits_broke_is_flashed_as_a_warning(adopting, monkeypatch):
    def broken(d, run_id, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_entry, "place_protections", broken)
    url = _trade_url(adopting.key) + "/adopt"
    html = adopting.post(url + "/check", data={"bot": adopting.bot_id}, headers=ORIGIN)
    fingerprint = html.get_data(as_text=True).split('name="fingerprint" value="')[1].split('"')[0]
    response = adopting.post(
        url, data={"bot": adopting.bot_id, "fingerprint": fingerprint}, headers=ORIGIN,
        follow_redirects=True,
    )  # fmt: skip
    page = response.get_data(as_text=True)
    assert 'notice--warning">Handed to IC, but NO EXITS PLACED' in page


def test_a_calendar_opened_today_offers_the_hand_over(settings, fake_credentials, monkeypatch):
    # Slice 15: any options trade opened today; the hand-over page checks the rest.
    near, far = fixtures.NEAR_PUT, fixtures.FAR_PUT
    rows = [fixtures._tx(13, "Sell to Open", "4.00", near, order_id=700),
            fixtures._tx(14, "Buy to Open", "6.00", far, order_id=700)]  # fmt: skip
    broker = FakeTastyTrade(rows=rows)
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/sync", headers=ORIGIN)
    monkeypatch.setattr(journal_routes, "_now", lambda: fixtures.NOW)
    html = client.get(_trade_url(fixtures.CALENDAR_KEY)).get_data(as_text=True)
    assert "Hand this trade to a bot" in html


PAPER_NOTICE = (
    "Paper trades are pretend. Fills are estimated at the mid price, and stops are watched "
    "by the app instead of resting at the broker, so real results would differ."
)


def _paper_trade(client, name_bot_live=False):
    from optionsjourney.db.models import PaperFill
    from optionsjourney.web import services

    with client.application.app_context():
        factory = services().session_factory
    bot_id = make(factory, live=name_bot_live)
    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key="p1", trading_date=NOW.date(), entry_time="09:47",
            status="filled", live=False, paper=True, account_number="5WX01234",
            broker="tastytrade", filled_legs=[], chase_log=[], contracts=1,
            legs=[{"symbol": "A", "side": "sell", "ratio": 1, "group": 0, "position": 0}],
            created_at=NOW, updated_at=NOW,
        )  # fmt: skip
        session.add(run)
        session.flush()
        for kind, price, reason in (("entry", "5.025", ""), ("exit", "-4.90", "target")):
            session.add(PaperFill(run_id=run.id, kind=kind, group=None, price=Decimal(price),
                                  contracts=1, reason=reason, legs=[], at=NOW))  # fmt: skip
        session.commit()
    return bot_id


def test_paper_journal_shows_notice(client):
    html = client.get("/journal/paper").get_data(as_text=True)
    assert PAPER_NOTICE in html and "No paper trades yet" in html
    assert "Paper journal" not in html  # no top-bar tab of its own
    assert 'view-switch__link--active" href="/journal/paper">Paper trades' in html


def test_journal_views_include_paper_trades(client):
    html = client.get("/journal").get_data(as_text=True)
    assert 'href="/journal/paper">Paper trades</a>' in html


def test_paper_journal_lists_paper_trades_and_the_real_one_does_not(client):
    _paper_trade(client)
    html = client.get("/journal/paper").get_data(as_text=True)
    assert "$12.50" in html and "target" in html
    assert "IC" not in client.get("/journal").get_data(as_text=True)


def test_paper_journal_filters_by_tag(client):
    _paper_trade(client)
    html = client.get("/journal/paper").get_data(as_text=True)
    assert '<option value="0DTE"' in html
    assert "$12.50" in client.get("/journal/paper?tag=0dte").get_data(as_text=True)
    html = client.get("/journal/paper?tag=nope").get_data(as_text=True)
    assert "$12.50" not in html and "/api/paper-open-pl?tag=nope" in html
    assert "No paper trades match these filters" in html


def test_automation_list_shows_paper_stats_with_a_tag(client):
    _paper_trade(client)
    html = client.get("/automation").get_data(as_text=True)
    assert "$12.50" in html and ">paper<" in html


def test_the_settle_expirations_switch(client):
    from optionsjourney.services import expiry_settle  # noqa: PLC0415
    from optionsjourney.web import services  # noqa: PLC0415

    html = client.get("/journal").get_data(as_text=True)
    assert "Settle expirations at 4:15 PM" in html and 'id="settle-switch"' in html
    response = client.post(
        "/journal/settle-expirations",
        data={"on": "1", "back": "/journal?status=open"},
        headers=ORIGIN,
    )
    assert response.status_code == 302 and response.location.endswith("/journal?status=open")
    with client.application.app_context():
        sf = services().session_factory
        assert expiry_settle.is_on(sf)
        expiry_settle._save(
            sf,
            expiry_settle.LAST_RUN_KEY,
            '{"day": "2026-10-09", "done": true, "settled": 0, "note": null,'
            ' "skipped": [{"name": "AAPL Put", "reason": "it becomes shares"}]}',
        )
    html = client.get("/journal").get_data(as_text=True)
    assert "AAPL Put (it becomes shares)" in html
    client.post("/journal/settle-expirations", data={"on": "0"}, headers=ORIGIN)
    with client.application.app_context():
        assert not expiry_settle.is_on(services().session_factory)


def test_sync_now_on_the_journal_and_settings_comes_back_there(client):
    assert 'action="/sync"' in client.get("/journal").get_data(as_text=True)
    assert 'action="/sync"' in client.get("/settings").get_data(as_text=True)
    back = client.post("/sync", data={"back": "/journal?view=table"}, headers=ORIGIN)
    assert back.status_code == 302 and back.location.endswith("/journal?view=table")
    elsewhere = client.post("/sync", data={"back": "https://example.com/"}, headers=ORIGIN)
    assert elsewhere.location.endswith("/transactions")  # only paths in this app


def test_paper_journal_splits_a_condor_into_its_sides(client):
    """#181: each side on its own row, with the stop that closed it."""
    from tests.services.test_paper_journal import add_condor

    from optionsjourney.web import services

    with client.application.app_context():
        factory = services().session_factory
    add_condor(factory, make(factory), [
        ("exit", 0, "-4.60", "stop", 30), ("expiry", 2, "0", "expired", 360),
        ("expiry", 1, "0", "expired", 360), ("expiry", 3, "0", "expired", 360),
    ])  # fmt: skip
    html = client.get("/journal/paper").get_data(as_text=True)
    assert "Put side" in html and "Call side" in html
    assert "-$215.00" in html and "$277.50" in html and ">stop<" in html


def test_a_paper_runs_page_lists_its_fills(client):
    """#182: a paper run's page shows each pretend fill leg by leg."""
    from sqlalchemy import select
    from tests.services.test_paper_journal import add_condor

    from optionsjourney.db.models import BotRun
    from optionsjourney.web import services

    with client.application.app_context():
        factory = services().session_factory
    add_condor(factory, make(factory), [("exit", 0, "-4.60", "stop", 30)])
    run_id = factory().scalar(select(BotRun.id).where(BotRun.key == "ic"))
    with factory() as session:  # add_condor's exit names no legs; give it its short put
        from optionsjourney.db.models import PaperFill

        row = session.scalars(select(PaperFill).where(PaperFill.kind == "exit")).one()
        row.legs = [{"symbol": "P1", "side": "sell", "ratio": 1, "mid": "4.60"}]
        session.commit()
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert 'id="paper-fills"' in html and "buy to close" in html and "-$460.00" in html
    assert "sell to open" in html and "$252.50" in html


def test_paper_journal_is_live_like_the_journal(client):
    """#183: open sides priced live, a stopped side closed with its wing held, and the
    page reloads on paper_journal_changed."""
    from tests.services.test_paper_journal import add_condor

    from optionsjourney.web import services

    with client.application.app_context():
        s = services()
        s.quote_hub = _MarksHub({"P1": Decimal("3.00"), "P2": Decimal("0.10")})
    add_condor(s.session_factory, make(s.session_factory), [("exit", 1, "-5.15", "stop", 30)])
    html = client.get("/journal/paper").get_data(as_text=True)
    assert 'data-live-event="paper_journal_changed"' in html and "journal_live.js" in html
    assert 'id="open-pl"' in html and "open_pl.js" in html and "wing held" in html
    assert "-$237.50" in html  # the stopped call side, closed
    data = client.get("/api/paper-open-pl").get_json()
    [(key, value)] = data["positions"].items()
    assert key.endswith(":put") and value == -45.0 and data["total"] == -45.0
    assert f'data-open-pl-key="{key}"' in html and data["open"] == 1 == data["priced"]


def test_a_prelim_settlement_says_so_in_the_journal(client):
    """#186: a trade settled at the 4:15 price says "(prelim)" until the official close."""
    from optionsjourney.db.models import ExpirySettlement

    factory = client.application.extensions["optionsjourney"].session_factory
    with factory() as session:
        session.add(
            ExpirySettlement(
                broker="tastytrade", account_number="5WX01234", symbol="SPXW  261120P05800000",
                underlying="SPXW", put_call="P", strike=Decimal("5800"),
                expiration=date(2026, 11, 20), sign=-1, quantity=Decimal("1"),
                close_price=Decimal("5900"), fee=Decimal("0"), created_at=datetime.now(UTC),
                status="prelim", price_source="tastytrade last",
            )
        )  # fmt: skip
        session.commit()
    html = client.get("/journal").get_data(as_text=True)
    assert "Settled at close (prelim)" in html
    with factory() as session:
        row = session.scalar(select(ExpirySettlement))
        row.status, row.finalized_at = "final", datetime.now(UTC)
        session.commit()
    html = client.get("/journal").get_data(as_text=True)
    assert "Settled at close" in html and "(prelim)" not in html


def _prelim(symbol, strike, expiration, close="5900"):
    from optionsjourney.db.models import ExpirySettlement

    return ExpirySettlement(
        broker="tastytrade", account_number="5WX01234", symbol=symbol, underlying="SPXW",
        put_call="P", strike=Decimal(strike), expiration=expiration, sign=-1,
        quantity=Decimal("1"), close_price=Decimal(close), prelim_close=Decimal(close),
        fee=Decimal("0"), created_at=datetime.now(UTC), status="prelim",
        price_source="tastytrade last",
    )  # fmt: skip


def test_the_prelim_tag_and_the_switch_say_how_settling_works(client):
    factory = client.application.extensions["optionsjourney"].session_factory
    with factory() as session:
        session.add(_prelim("SPXW  261120P05800000", "5800", date(2026, 11, 20)))
        session.commit()
    html = client.get("/journal").get_data(as_text=True)
    assert 'title="Settled at SPXW 5900 (tastytrade last) at 4:15 PM.' in html
    assert "closing price" not in html
    assert "at the last price from your broker" in html and "$1 or more" in html


# --- Tips on the Journal views (#230) -----------------------------------------------


def _has_tips(html, *ids):
    missing = [i for i in ids if f'id="tip-{i}"' not in html]
    assert not missing, f"missing tips: {missing}"


def test_journal_page_has_tips(client):
    html = client.get("/journal").get_data(as_text=True)
    _has_tips(html, "journal-open-pl", "journal-pl-day", "journal-pl-day-col", "journal-legs",
              "journal-sync-now", "journal-graph-together")  # fmt: skip
    assert 'title="Read new trades from your brokers now' not in html
    # the menu bar launcher doesn't sync on its own yet (#225): the tip says so
    assert "Not yet when you started the app from the menu bar icon" in html


def test_journal_summary_boxes_have_tips(client):
    html = client.get("/journal").get_data(as_text=True)
    _has_tips(html, "journal-closed-pl", "journal-open-premium", "journal-closed-premium",
              "journal-avg-per-lot", "journal-avg-winner", "journal-avg-loser",
              "journal-max-winner", "journal-max-loser", "journal-trades", "journal-winners",
              "journal-win-rate", "journal-capture-rate")  # fmt: skip
    assert "/help/journal#the-summary-above-the-trades" in html
    # the per-lot boxes, Winners and both dials leave open trades out, and say so
    assert html.count("Open trades aren&#39;t counted until they close.") >= 7


def test_stocks_tab_has_a_cost_basis_tip(client, monkeypatch):
    from types import SimpleNamespace

    held = SimpleNamespace(
        ticker="AAPL", quantity=10, cost_basis=150, cost=1500, price=None,
        pl=None, pl_percent=None, today_pl=None,
    )  # fmt: skip
    monkeypatch.setattr(journal_routes.holdings, "stock_holdings", lambda *a, **k: [held])
    _has_tips(client.get("/journal?kind=stocks").get_data(as_text=True), "journal-cost-basis")


def test_grid_has_tips(client):
    _has_tips(client.get("/journal/grid").get_data(as_text=True), "grid-premium", "grid-open-pl")


def test_paper_journal_has_tips(client):
    _paper_trade(client)
    _has_tips(client.get("/journal/paper").get_data(as_text=True), "paper-pl", "paper-closed-by")


def test_pl_page_has_tips(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    _has_tips(client.get("/journal/pl").get_data(as_text=True),
              "pl-cumulative", "pl-deepest-drawdown")  # fmt: skip


def test_metrics_page_has_tips(settings, fake_credentials):
    client = _closed_trade_client(settings, fake_credentials)
    _has_tips(client.get("/journal/metrics").get_data(as_text=True),
              "metrics-winning-days", "metrics-day-winning-days")  # fmt: skip


def test_trade_page_has_tips(client):
    html = client.get(_trade_url()).get_data(as_text=True)
    _has_tips(html, "trade-close-reason", "trade-split-ic")


def test_a_split_halfs_trade_page_has_the_combine_tip(client):
    client.post(_trade_url() + "/split", data={"split": "1"}, headers=ORIGIN)
    half = client.get(_trade_url(KEY + "#put")).get_data(as_text=True)
    _has_tips(half, "trade-combine-ic")


def test_the_trade_pages_hand_over_button_has_a_tip(adopting):
    html = adopting.get(_trade_url(adopting.key)).get_data(as_text=True)
    _has_tips(html, "trade-hand-to-bot")


def _save_stocks(client, rows):
    from optionsjourney.db.models import StockSnapshot

    factory = client.application.extensions["optionsjourney"].session_factory
    with factory() as session:
        for day, account, ticker, qty, cost, close in rows:
            session.add(
                StockSnapshot(
                    day=day, account=account, ticker=ticker, quantity=Decimal(qty),
                    cost_basis=Decimal(cost), close=None if close is None else Decimal(close),
                    taken_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )  # fmt: skip
        session.commit()


def test_stocks_tab_draws_pl_against_cost(client):
    _save_stocks(client, [
        (date(2026, 9, 1), "tt:1", "AAA", "10", "100", "110"),
        (date(2026, 9, 2), "tt:1", "AAA", "10", "100", "120"),
    ])  # fmt: skip
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert "data-line-chart" in html
    data = re.search(r'id="stocks-pl-data">(.*?)</script>', html).group(1)
    assert {"day": "2026-09-02", "pl": 200.0, "pct": 20.0} in json.loads(data)
    assert "$200" in html and "+20.00%" in html
    assert 'id="tip-stocks-pl-chart"' in html
    assert 'id="stocks-scope"' in html


def test_stocks_chart_follows_account_and_range(client):
    today = datetime.now(ZoneInfo("America/New_York")).date()
    old, new = today - timedelta(days=60), today - timedelta(days=2)
    _save_stocks(client, [
        (old, "tt:1", "AAA", "10", "100", "110"),
        (new, "tt:1", "AAA", "10", "100", "120"),
        (new, "tt:2", "BBB", "1", "50", "60"),
    ])  # fmt: skip
    both = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert old.isoformat() in both and '"pl":210.0' in both.replace(" ", "")
    one = client.get("/journal?kind=stocks&account=tt:1").get_data(as_text=True)
    assert '"pl":200.0' in one.replace(" ", "") and '"pl":210.0' not in one.replace(" ", "")
    month = client.get("/journal?kind=stocks&account=tt:1&range=1M").get_data(as_text=True)
    assert old.isoformat() not in month and new.isoformat() in month
    links = re.findall(r'href="(/journal\?[^"]*)"[^>]*>(?:1M|3M|1Y|All)</a>', month)
    assert len(links) == 4
    for link in links:
        assert "kind=stocks" in link and "account=tt:1" in link and "range=" in link


def test_stocks_chart_empty_state_with_no_shares(client):
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert "The chart starts after the first close at which you hold shares." in html
    assert "today's close" not in html and "data-line-chart" not in html


def test_stocks_chart_empty_state_with_shares(client, monkeypatch):
    from types import SimpleNamespace

    held = SimpleNamespace(
        ticker="AAPL", quantity=10, cost_basis=150, cost=1500, price=None,
        pl=None, pl_percent=None, today_pl=None,
    )  # fmt: skip
    monkeypatch.setattr(journal_routes.holdings, "stock_holdings", lambda *a, **k: [held])
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert "Your first point appears after the next close (4:15 PM New York time)." in html


def test_stocks_scope_note_comes_before_the_chart(client):
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert html.index('id="stocks-scope"') < html.index('id="stocks-pl-title"')


def test_stocks_chart_leaves_out_a_day_with_nothing_priced(client):
    """#236: a day whose every close is missing has no P/L; it isn't drawn as $0."""
    _save_stocks(client, [
        (date(2026, 9, 1), "tt:1", "AAA", "10", "100", "110"),
        (date(2026, 9, 2), "tt:1", "AAA", "10", "100", None),
    ])  # fmt: skip
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    data = json.loads(re.search(r'id="stocks-pl-data">(.*?)</script>', html).group(1))
    assert [d["day"] for d in data] == ["2026-09-01"]
    assert "$100" in html and "Sep 1, 2026" in html
    assert "Sep 2: 0 of 1 priced" in html


def test_stocks_chart_with_only_unpriced_days_draws_nothing(client):
    _save_stocks(client, [(date(2026, 9, 2), "tt:1", "AAA", "10", "100", None)])
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert "data-line-chart" not in html
    assert "Nothing saved in this range yet." in html and "0 of 1 priced" in html


def test_stocks_chart_notes_unpriced(client):
    _save_stocks(client, [
        (date(2026, 9, 1), "tt:1", "AAA", "10", "100", "110"),
        (date(2026, 9, 1), "tt:1", "BBB", "1", "50", None),
    ])  # fmt: skip
    assert "1 of 2 priced" in client.get("/journal?kind=stocks").get_data(as_text=True)


def test_stocks_chart_says_nothing_in_range_when_rows_exist_elsewhere(client):
    _save_stocks(client, [(date(2026, 1, 5), "tt:1", "AAA", "10", "100", "110")])
    html = client.get("/journal?kind=stocks&range=1M").get_data(as_text=True)
    assert "Nothing saved in this range yet." in html
    assert "Your first point appears" not in html
    assert "range=3M" in html
    other = client.get("/journal?kind=stocks&account=tt:9").get_data(as_text=True)
    assert "Nothing saved in this range yet." in other


def test_stocks_tip_mentions_early_close_and_retry(client):
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert "1:15 PM on early-close days" in html and "until 8 PM" in html


def _stock_chart_setup(monkeypatch, candles=None, error=False):
    from decimal import Decimal
    from types import SimpleNamespace

    from optionsjourney.services import chart_market

    held = SimpleNamespace(ticker="AAPL", quantity=Decimal(10), cost_basis=Decimal(150))
    monkeypatch.setattr(journal_routes.holdings, "stock_holdings", lambda *a, **k: [held])

    def history(clients, symbol, frequency, period, now=None):
        if error:
            raise chart_market.MarketError("down", 502)
        assert (symbol, frequency) == ("AAPL", 1440)
        return {"candles": candles}

    monkeypatch.setattr(journal_routes.chart_market, "price_history", history)


def test_stock_chart_api_shape(client, monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal
    from types import SimpleNamespace

    from optionsjourney.core.stock_history import Mark

    _stock_chart_setup(monkeypatch, candles=[
        {"time": int(datetime(2026, 9, 1, tzinfo=UTC).timestamp()), "close": 148.5},
        {"time": int(datetime(2026, 9, 2, tzinfo=UTC).timestamp()), "close": 152},
    ])  # fmt: skip
    no_fills = lambda *a, **k: SimpleNamespace(fills=[])  # noqa: E731
    monkeypatch.setattr(journal_routes.transactions, "load_fills", no_fills)
    seen = {}

    def marks(fills, ticker, account=""):
        seen["ticker"] = ticker
        return [Mark(date(2026, 9, 2), "buy", Decimal(10), Decimal("150"))]

    monkeypatch.setattr(journal_routes, "trade_marks", marks)
    data = client.get("/api/stocks/aapl/chart?range=All").get_json()
    assert seen["ticker"] == "AAPL"
    assert data["ticker"] == "AAPL" and data["quantity"] == 10 and data["cost_basis"] == 150
    assert [c["close"] for c in data["closes"]] == [148.5, 152]
    assert data["closes"][0]["day"] == "2026-09-01"
    assert data["marks"] == [{"day": "2026-09-02", "side": "buy", "quantity": 10, "price": 150}]
    assert data["note"] == ""


def test_stock_chart_api_prices_unavailable(client, monkeypatch):
    from types import SimpleNamespace

    _stock_chart_setup(monkeypatch, error=True)
    no_fills = lambda *a, **k: SimpleNamespace(fills=[])  # noqa: E731
    monkeypatch.setattr(journal_routes.transactions, "load_fills", no_fills)
    res = client.get("/api/stocks/AAPL/chart")
    assert res.status_code == 200
    assert res.get_json()["closes"] == []
    assert res.get_json()["note"] == "Prices aren't available right now."


def test_stock_chart_api_unknown_ticker_404(client, monkeypatch):
    monkeypatch.setattr(journal_routes.holdings, "stock_holdings", lambda *a, **k: [])
    assert client.get("/api/stocks/ZZZ/chart").status_code == 404


def test_stocks_tab_ticker_opens_drawer(client, monkeypatch):
    from types import SimpleNamespace

    held = SimpleNamespace(
        ticker="AAPL", quantity=10, cost_basis=150, cost=1500, price=None,
        pl=None, pl_percent=None, today_pl=None,
    )  # fmt: skip
    monkeypatch.setattr(journal_routes.holdings, "stock_holdings", lambda *a, **k: [held])
    html = client.get("/journal?kind=stocks").get_data(as_text=True)
    assert 'data-stock-chart="AAPL"' in html
    assert 'id="stock-drawer"' in html and "js/stock_drawer.js" in html


def test_stock_chart_api_ticker_with_a_slash(client, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    held = SimpleNamespace(ticker="BRK/B", quantity=Decimal(2), cost_basis=Decimal(400))
    monkeypatch.setattr(journal_routes.holdings, "stock_holdings", lambda *a, **k: [held])
    seen = {}

    def history(clients, symbol, frequency, period, now=None):
        seen["symbol"] = symbol
        return {"candles": []}

    monkeypatch.setattr(journal_routes.chart_market, "price_history", history)
    for url in ("/api/stocks/BRK/B/chart", "/api/stocks/BRK%2FB/chart"):
        res = client.get(url)
        assert res.status_code == 200
        assert res.get_json()["ticker"] == "BRK/B" and seen["symbol"] == "BRK/B"


def test_stock_chart_api_any_price_failure_is_a_note(client, monkeypatch, caplog):
    from decimal import Decimal
    from types import SimpleNamespace

    held = SimpleNamespace(ticker="AAPL", quantity=Decimal(10), cost_basis=Decimal(150))
    monkeypatch.setattr(journal_routes.holdings, "stock_holdings", lambda *a, **k: [held])

    def history(*a, **k):
        raise KeyError("candles")

    monkeypatch.setattr(journal_routes.chart_market, "price_history", history)
    res = client.get("/api/stocks/AAPL/chart")
    assert res.status_code == 200
    assert res.get_json()["closes"] == []
    assert res.get_json()["note"] == "Prices aren't available right now."
    assert res.get_json()["quantity"] == 10
    assert "Stock chart prices failed for AAPL" in caplog.text


def _equity_trade(transaction_id, account_number, action, quantity, price, days_ago):
    executed = NOW - timedelta(days=days_ago)
    return {
        "id": transaction_id,
        "account-number": account_number,
        "symbol": "AAPL",
        "instrument-type": "Equity",
        "underlying-symbol": "AAPL",
        "transaction-type": "Trade",
        "action": action,
        "quantity": quantity,
        "price": price,
        "value": str(float(price) * float(quantity)),
        "value-effect": "Debit" if action.startswith("Buy") else "Credit",
        "executed-at": executed.isoformat(),
        "order-id": 100 + transaction_id,
    }


class TwoAccountTasty(FakeTastyTrade):
    """Real AAPL fills in two accounts, read through the sync (#236)."""

    def __init__(self):
        super().__init__(rows=[])
        self.by_account = {
            "5WX01234": [_equity_trade(1, "5WX01234", "Buy to Open", "10", "150", 5)],
            "5WX09999": [_equity_trade(2, "5WX09999", "Sell to Open", "3", "170", 4)],
        }

    def accounts(self):
        return [
            BrokerAccount("tastytrade", "5WX01234", "Alpha"),
            BrokerAccount("tastytrade", "5WX09999", "Beta"),
        ]

    def transactions(self, number, start, end):
        return [
            RawTransaction(str(r["id"]), datetime.fromisoformat(r["executed-at"]), "Trade", r)
            for r in self.by_account[number]
        ]


def test_stock_chart_api_marks_follow_the_account(settings, fake_credentials, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    broker = TwoAccountTasty()
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [broker])
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/sync", headers=ORIGIN)
    held = SimpleNamespace(ticker="AAPL", quantity=Decimal(7), cost_basis=Decimal(150))
    monkeypatch.setattr(journal_routes.holdings, "stock_holdings", lambda *a, **k: [held])
    monkeypatch.setattr(
        journal_routes.chart_market, "price_history", lambda *a, **k: {"candles": []}
    )

    def marks(account):
        url = "/api/stocks/AAPL/chart?range=All" + (f"&account={account}" if account else "")
        return [(m["side"], m["quantity"], m["price"]) for m in client.get(url).get_json()["marks"]]

    assert marks("tastytrade:5WX01234") == [("buy", 10, 150)]
    assert marks("tastytrade:5WX09999") == [("sell", 3, 170)]
    assert sorted(marks("")) == [("buy", 10, 150), ("sell", 3, 170)]
