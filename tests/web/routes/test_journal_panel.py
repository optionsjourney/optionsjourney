"""The journal's Manage panel (#174): a gear on every row opens a slide-in panel made of
the run page's and the manage page's own sections; its forms come back to the journal
with the panel open."""

from decimal import Decimal

from sqlalchemy import select
from tests.services.bot_fakes import make
from tests.web.routes import test_journal_manage as manage
from tests.web.routes.test_journal_manage import CLOSED, NOW, ORIGIN, SPX, SPX_PUT, SPY, post, url

from optionsjourney.db.models import BotExit, BotRun, BotTradeMatch

client = manage.client  # the fixture
BACK = "/journal?status=open"


def panel(client, key, back=BACK):
    response = client.get(url(key, "/panel"), query_string={"back": back})
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _bot_run(client, firing=False):
    """A live bot run that opened the SPX trade, with a stop and a target."""
    bot_id = make(client.factory, live=True)
    with client.factory() as session:
        run = BotRun(
            bot_id=bot_id, key="panel", trading_date=NOW.date(), entry_time="09:40",
            status="filled", live=True, account_number="5WX01234", broker="tastytrade",
            broker_order_id="10", contracts=1, target_credit=Decimal("2.00"),
            fill_credit=Decimal("2.00"), reason="", chase_log=[],
            legs=[{"symbol": SPX_PUT, "side": "sell", "ratio": 1, "group": 0, "mid": "2.00",
                   "strike": 5700, "right": "put"}],
            filled_legs=[{"symbol": SPX_PUT, "fill_price": "2.00"}],
            created_at=NOW, updated_at=NOW,
        )  # fmt: skip
        session.add(run)
        session.flush()
        for kind, state in (("stop", "working"), ("target", "firing" if firing else "armed")):
            session.add(BotExit(
                run_id=run.id, group_position=0, kind=kind, state=state, short_symbol=SPX_PUT,
                long_symbol="", contracts=1, broker_order_id="900" if kind == "stop" else None,
                trigger=Decimal("4.00") if kind == "stop" else None,
                limit=Decimal("0.50") if kind == "target" else None, rearms=0, log=[],
                created_at=NOW, updated_at=NOW,
            ))  # fmt: skip
        session.commit()
        exit_id = session.scalar(select(BotExit.id).where(BotExit.kind == "target"))
        return run.id, exit_id


def test_every_row_has_a_gear(client):
    html = client.get("/journal?status=all").get_data(as_text=True)
    gears = html.count('aria-label="Manage this trade"')
    rows = html.count('class="trade-row"')
    assert rows >= 3 and gears == rows
    assert url(SPX, "/panel") in html and 'id="manage-drawer"' in html
    assert "js/manage_panel.js" in html


def test_a_bots_trade_shows_its_summary_orders_and_sides(client):
    run_id, _ = _bot_run(client)
    html = panel(client, SPX)
    assert "Manage: " in html and "Fill price" in html and "Broker order 10" in html
    assert f"/automation/runs/{run_id}/sides/0/stop" in html  # its side's card
    assert f"/automation/runs/{run_id}/close-trade" in html  # Close entire trade
    assert f'href="/automation/runs/{run_id}"' in html  # Open full page
    # every form comes back to this journal view with the panel open
    assert 'name="next" value="/journal?status=open&amp;panel=tastytrade%3A5WX01234%3A10"' in html
    assert "<html" not in html  # just the panel


def test_a_closing_exit_shows_cancel_at_the_top(client):
    run_id, exit_id = _bot_run(client, firing=True)
    html = panel(client, SPX)
    assert 'id="panel-closing"' in html
    assert f"/automation/runs/{run_id}/exits/{exit_id}/cancel" in html


def test_a_manual_rule_trade_shows_its_rules(client):
    post(client, SPY, {"custom": "1", "target_value": "50", "target_unit": "pct",
                       "stop_value": "100", "stop_unit": "pct"})  # fmt: skip
    html = panel(client, SPY)
    assert "Its rules: Custom rules" in html and "Remove its rules" in html
    assert "Profit target: 50%" in html


def test_a_plain_trade_offers_rules_and_the_trade_page(client):
    html = panel(client, SPX)
    assert "Pick a manual bot" in html and "Use custom rules" in html
    assert url(SPX) in html  # the trade page, for name, tags and notes
    assert "Only an options trade of up to 4 legs, opened today" in html  # adopt's note


def test_a_closed_trade_is_read_only(client):
    html = panel(client, CLOSED)
    assert "nothing left to manage" in html
    assert "<form" not in html


def test_a_form_from_the_panel_returns_to_the_journal_with_it_open(client):
    nxt = "/journal?status=open&panel=" + SPX
    response = post(client, SPX, {"custom": "1", "target_value": "50", "target_unit": "pct",
                                  "next": nxt})  # fmt: skip
    assert response.status_code == 302 and response.location == nxt
    run_id, _ = _bot_run(client)
    response = client.post(f"/automation/runs/{run_id}/restore", data={"apply": "0", "next": nxt},
                           headers=ORIGIN)  # fmt: skip
    assert response.location == nxt


def test_next_never_leaves_the_app(client):
    for bad in ("https://evil.example/x", "//evil.example/x", "/\\evil.example"):
        response = post(client, SPX, {"custom": "1", "target_value": "50", "next": bad})
        assert response.location.startswith("/journal/trades/")
    html = panel(client, SPX, back="https://evil.example/")
    assert 'value="/journal?panel=' in html


def test_a_confirm_step_keeps_next(client):
    run_id, _ = _bot_run(client)
    nxt = "/journal?panel=" + SPX
    html = client.post(f"/automation/runs/{run_id}/close-trade", data={"next": nxt},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert 'name="next" value="/journal?panel=' in html and 'href="/journal?panel=' in html


def test_the_full_pages_keep_their_sections_and_no_next(client):
    run_id, _ = _bot_run(client)
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Orders, step by step" in html or "Manage this trade" in html
    assert f"/automation/runs/{run_id}/sides/0/stop" in html and 'value="/journal?' not in html
    html = client.get(url(SPY, "/manage")).get_data(as_text=True)
    assert "Or custom rules for this trade only" in html and 'value="/journal?' not in html


def test_a_trade_no_bot_placed_lists_its_broker_orders(client):
    for key in (SPX, CLOSED):
        assert "Broker orders (" in panel(client, key)


def test_a_matched_trade_can_be_unlinked_from_its_bot(client):
    """#177: a trade Match trades linked to a bot (no run) offers Unlink, which takes it off."""
    assert "Unlink from" not in panel(client, CLOSED)
    bot_id = make(client.factory, live=True)
    with client.factory() as session:
        session.add(BotTradeMatch(position_key=CLOSED, bot_id=bot_id, matched_at=NOW))
        session.commit()
    html = panel(client, CLOSED)
    assert "Unlink from" in html and url(CLOSED, "/unlink") in html
    assert "Unlink from" in client.get(url(CLOSED)).get_data(as_text=True)  # the trade page
    nxt = "/journal?status=open&panel=" + CLOSED
    response = post(client, CLOSED, {"next": nxt}, tail="/unlink")
    assert response.status_code == 302 and response.location == nxt
    with client.factory() as session:
        assert session.get(BotTradeMatch, CLOSED) is None
    assert "Unlink from" not in panel(client, CLOSED)
    assert post(client, CLOSED, {}, tail="/unlink").status_code == 400  # nothing to unlink


def test_a_split_sides_panel_shows_only_its_own_groups():
    from types import SimpleNamespace as NS

    from optionsjourney.web.routes.journal import _one_side

    legs = [{"symbol": "XSP   261019P00770000", "group": 0}, {"symbol": "XSP   261019C00771000", "group": 1},
            {"symbol": "XSP   261019P00767000", "group": 2}, {"symbol": "XSP   261019C00774000", "group": 3}]  # fmt: skip
    names = ["Short Puts", "Short Calls", "Long Puts", "Long Calls"]
    run = {
        "run": NS(legs=legs),
        "sides": [NS(position=i, name=n) for i, n in enumerate(names)],
        "steps": [NS(source="Entry"), NS(source="stop · Short Puts"),
                  NS(source="close (Short Calls's stop) · Long Calls")],
    }  # fmt: skip
    calls = _one_side(run, "call")
    assert [s.name for s in calls["sides"]] == ["Short Calls", "Long Calls"]
    assert [s.source for s in calls["steps"]] == ["Entry", "close (Short Calls's stop) · Long Calls"]
    assert calls["half_groups"] == [1, 3]


def test_custom_rules_show_one_alert_row_and_hide_the_exit_time_until_ticked(client):
    html = panel(client, SPY)
    custom = html[html.index("Or custom rules for this trade only"):]
    assert custom.count("data-alert-row>") == 1 and "data-add-alert" in custom
    assert 'name="exit_on" data-filter-use' in custom and "data-filter-fields" in custom
