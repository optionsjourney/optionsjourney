"""Alerts the user sets (#27): prices and trades' open P/L."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from optionsjourney.core.quotes import Quote
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services import user_alerts
from optionsjourney.services.user_alerts import AlertError

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


class Hub:
    def __init__(self, marks):
        self.marks, self.subscribed = marks, set()

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        m = self.marks.get(key)
        return (
            None if m is None else Quote(key, Decimal(m), Decimal(m), Decimal(m), "tastytrade", NOW)
        )


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def test_a_price_alert_goes_off_once_when_crossed(factory):
    user_alerts.create_price(factory, "spx", "below", "7700", note="add hedges")
    sent = []
    assert (
        user_alerts.check(
            factory, Hub({"SPX": "7750"}), open_pl=lambda key: None, notify=sent.append, now=NOW
        )
        == []
    )
    [fired] = user_alerts.check(
        factory, Hub({"SPX": "7690"}), open_pl=lambda key: None, notify=sent.append, now=NOW
    )
    assert (
        fired.title == "SPX went below 7,700.00"
        and "now 7,690.00" in fired.detail
        and "add hedges" in fired.detail
    )
    assert sent == [fired]
    # one-shot: it doesn't go off again until set again
    assert (
        user_alerts.check(
            factory, Hub({"SPX": "7600"}), open_pl=lambda key: None, notify=sent.append, now=NOW
        )
        == []
    )
    [shown] = user_alerts.triggered(factory)
    user_alerts.rearm(factory, shown.id)
    assert (
        len(
            user_alerts.check(
                factory, Hub({"SPX": "7600"}), open_pl=lambda key: None, notify=sent.append, now=NOW
            )
        )
        == 1
    )


def test_a_trade_open_pl_alert(factory):
    user_alerts.create_trade(factory, "tastytrade:5WX01234:1", "SPX Iron Condor", "below", "-500")
    fired = user_alerts.check(
        factory, Hub({}), open_pl=lambda key: Decimal("-650"), notify=lambda a: None, now=NOW
    )
    assert fired[0].title == "SPX Iron Condor open P/L went below -$500"
    assert "now -$650" in fired[0].detail


def test_a_combined_alert_watches_the_trades_together(factory):
    """#300: one alert on several trades, valued by their P/L together; the bells see it
    on each of them."""
    keys = ["tt:1:1", "tt:1:2"]
    user_alerts.create_trade(factory, keys, "SPX × 2 trades", "above", "100", quiet=True)
    asked = []
    fired = user_alerts.check(
        factory, Hub({}), open_pl=lambda key: asked.append(key) or Decimal("120"),
        notify=lambda a: pytest.fail("in the app only: never sent"), now=NOW,
    )  # fmt: skip
    assert asked == [keys]
    assert fired[0].title == "SPX × 2 trades combined P/L went above $100"
    assert user_alerts.states_by_trade(factory) == {"tt:1:1": ["fired"], "tt:1:2": ["fired"]}
    assert user_alerts.for_trades(factory, ["tt:1:2"])[0]["condition"].startswith("combined P/L")
    with pytest.raises(AlertError, match="at most 10"):
        user_alerts.create_trade(factory, [f"tt:1:{n}" for n in range(11)], "x", "above", "1")


def test_a_percent_of_entry_level_in_dollars():
    assert user_alerts.pct_level("-50", Decimal("1300")) == Decimal("-650.00")
    with pytest.raises(AlertError, match=r"\$50"):
        user_alerts.pct_level("10", Decimal("49"))
    with pytest.raises(AlertError, match="0%"):
        user_alerts.pct_level("0", Decimal("500"))


def test_no_price_no_alert(factory):
    user_alerts.create_price(factory, "SPY", "above", "800")
    assert (
        user_alerts.check(
            factory, Hub({}), open_pl=lambda key: None, notify=lambda a: None, now=NOW
        )
        == []
    )


def test_bad_alerts_are_refused(factory):
    with pytest.raises(AlertError, match="number"):
        user_alerts.create_price(factory, "SPY", "above", "lots")
    with pytest.raises(AlertError, match="above or below"):
        user_alerts.create_price(factory, "SPY", "sideways", "800")
    with pytest.raises(AlertError, match="symbol"):
        user_alerts.create_price(factory, "", "above", "800")


def test_dismissing_and_deleting(factory):
    alert_id = user_alerts.create_price(factory, "SPY", "above", "700")
    user_alerts.check(
        factory, Hub({"SPY": "778"}), open_pl=lambda key: None, notify=lambda a: None, now=NOW
    )
    user_alerts.dismiss(factory, alert_id)
    assert user_alerts.triggered(factory) == []
    user_alerts.delete(factory, alert_id)
    assert user_alerts.active(factory) == [] and user_alerts.triggered(factory) == []


SPREAD = [
    {"expiry": "2026-10-16", "strike": 5800, "right": "put", "qty": -1},
    {"expiry": "2026-10-16", "strike": 5790, "right": "put", "qty": 1},
]


def test_a_spread_alert_watches_the_net_mid_of_its_legs(factory):
    alert_id = user_alerts.create_spread(factory, "SPX", SPREAD, "below", "0.90")
    hub = Hub({"SPXW  261016P05800000": "3.00", "SPXW  261016P05790000": "2.00"})
    sent = []
    assert user_alerts.check(factory, hub, lambda key: None, sent.append, now=NOW) == []
    assert hub.subscribed == {"SPXW  261016P05800000", "SPXW  261016P05790000"}
    hub.marks["SPXW  261016P05800000"] = "2.80"  # the 1.00 credit spread is now 0.80
    fired = user_alerts.check(factory, hub, lambda key: None, sent.append, now=NOW)
    assert [f.id for f in fired] == [alert_id]
    assert fired[0].title == "SPX spread went below 0.90" and "now 0.80" in fired[0].detail


def test_spread_alerts_are_checked(factory):
    with pytest.raises(AlertError):
        user_alerts.create_spread(factory, "SPX", [], "below", "1")
    with pytest.raises(AlertError):
        user_alerts.create_spread(factory, "/ES", SPREAD, "below", "1")
    with pytest.raises(AlertError):
        user_alerts.create_spread(factory, "SPX", [dict(SPREAD[0], right="x")], "below", "1")


def test_states_for_the_analyzer(factory):
    armed = user_alerts.create_price(factory, "SPX", "above", "1")
    off = user_alerts.create_price(factory, "SPY", "above", "1")
    user_alerts.disable(factory, off)
    states = {a["id"]: a["state"] for a in user_alerts.listing(factory)}
    assert states == {armed: "armed", off: "off"}
    assert [a["id"] for a in user_alerts.listing(factory, {"armed"})] == [armed]


def test_the_price_alerts_table_shows_state_condition_and_live_value(factory):
    """#274: Tracker2's Price alerts tab: every alert with its live value and state."""
    armed = user_alerts.create_price(factory, "SPX", "below", "7,700")
    fired = user_alerts.create_price(factory, "QQQ", "above", "500")
    off = user_alerts.create_trade(factory, "tt:1#x", "SPX Iron Condor", "above", "250")
    user_alerts.check(factory, Hub({"QQQ": "510"}), open_pl=lambda k: None,
                      notify=lambda e: None, now=NOW)  # fmt: skip
    user_alerts.disable(factory, off)
    hub = Hub({"SPX": "7812.5", "QQQ": "505"})
    rows = user_alerts.table(factory, "all", hub, open_pl=lambda k: Decimal("120"), now=NOW)
    by_id = {r["id"]: r for r in rows}
    assert by_id[armed]["state"] == "armed" and by_id[armed]["live"] == "7,812.50"
    assert by_id[armed]["name"] == "SPX" and by_id[armed]["condition"] == "goes below 7,700.00"
    assert by_id[fired]["state"] == "fired" and by_id[fired]["fired_at"] == NOW
    assert by_id[off]["state"] == "off" and by_id[off]["live"] == "$120"
    assert by_id[off]["condition"] == "open P/L goes above $250"


def test_the_table_filters_like_tracker2(factory):
    armed = user_alerts.create_price(factory, "SPX", "below", "7,700")
    fired = user_alerts.create_price(factory, "QQQ", "above", "500")
    old = user_alerts.create_price(factory, "IWM", "above", "200")
    hub = Hub({"QQQ": "510"})
    user_alerts.check(factory, hub, open_pl=lambda k: None, notify=lambda e: None, now=NOW)
    yesterday = NOW.replace(day=NOW.day - 1)
    user_alerts.check(factory, Hub({"IWM": "210"}), open_pl=lambda k: None,
                      notify=lambda e: None, now=yesterday)  # fmt: skip

    def ids(show):
        return {r["id"] for r in user_alerts.table(factory, show, hub, lambda k: None, now=NOW)}

    assert ids("active") == {armed, fired}  # armed, and fired today
    assert ids("armed") == {armed}
    assert ids("fired") == {fired, old}
    assert ids("all") == {armed, fired, old}
    assert ids("nonsense") == ids("active")


def test_a_live_value_that_cant_be_read_is_a_dash(factory):
    user_alerts.create_trade(factory, "tt:1#x", "Condor", "above", "250")

    def broken(key):
        raise RuntimeError("no book")

    [row] = user_alerts.table(factory, "all", Hub({}), broken, now=NOW)
    assert row["live"] == "—"
