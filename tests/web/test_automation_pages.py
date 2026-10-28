import io
import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from sqlalchemy import select

from optionsjourney.app import create_app
from optionsjourney.bots import bot_file
from optionsjourney.bots.definition import keep_trying, min_width
from optionsjourney.db.models import (
    Account,
    Bot,
    BotExit,
    BotRun,
    EconomicEvent,
    MarketCalendarDay,
    SyncState,
    Tag,
)
from optionsjourney.services import bot_store
from optionsjourney.web import services
from optionsjourney.web.routes import automation
from optionsjourney.web.routes import settings as settings_routes

ORIGIN = {"Origin": "http://localhost"}
# Going Live: the watched-stops box and the first-Live risk box (decision 0093) ticked.
LIVE_TICKED = {"on": "1", "understood": "1", "risk": "1"}


@pytest.fixture
def app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    return app


def sf(app):
    with app.app_context():
        return services().session_factory


def form(app, **changes):
    with sf(app)() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    data = {
        "name": "SPX IC", "tag_id": str(sid), "account": "tastytrade:5WX01234",
        "underlying": "SPX", "contracts": "1", "max_per_trade": "1", "max_per_day": "2",
        "split": "on", "times": "1", "time-0": "09:47", "time-0-on": "on",
        "group-0-name": "Call side", "group-0-stop": "95", "group-0-target": "",
        "group-1-name": "Put side", "group-1-stop": "95", "group-1-target": "",
        "chase-entry-start": "0", "chase-entry-step": "0.05", "chase-entry-interval": "10",
        "chase-entry-tries": "6", "chase-exit-start": "0", "chase-exit-step": "0.05",
        "chase-exit-interval": "5", "chase-exit-tries": "6",
    }  # fmt: skip
    for i, (side, right, pick, target, link, group) in enumerate([
        ("sell", "call", "premium", "2.70", "", "0"), ("buy", "call", "offset", "75", "0", "0"),
        ("sell", "put", "premium", "2.90", "", "1"), ("buy", "put", "offset", "-75", "2", "1"),
    ]):  # fmt: skip
        data |= {f"leg-{i}-side": side, f"leg-{i}-right": right, f"leg-{i}-ratio": "1",
                 f"leg-{i}-pick": pick, f"leg-{i}-target": target, f"leg-{i}-link": link,
                 f"leg-{i}-exact": "on", f"leg-{i}-group": group}  # fmt: skip
    return data | changes


def strangle_form(app, **changes):
    """form()'s bot as two groups of one sold leg each: they can rest their stops
    (decision 0054), so the app watches nothing and arming needs no box."""
    data = {k: v for k, v in form(app).items() if not k.startswith(("leg-2-", "leg-3-"))}
    data |= {"leg_count": "2", "leg-1-side": "sell", "leg-1-right": "put",
             "leg-1-pick": "premium", "leg-1-target": "2.90", "leg-1-link": "",
             "leg-1-group": "1"}  # fmt: skip
    return data | changes


def test_automation_is_in_the_nav_and_lists_nothing_at_first(app):
    html = app.test_client().get("/journal").get_data(as_text=True)
    assert 'href="/automation"' in html
    assert "No bots yet" in app.test_client().get("/automation").get_data(as_text=True)


def test_the_new_bot_form_starts_with_a_blank_iron_condor(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert html.count('name="leg-') >= 4 * 8
    assert "TastyTrade only" in html


def test_a_new_tag_can_be_made_from_the_bot_form(app):
    client = app.test_client()
    html = client.get("/automation/new").get_data(as_text=True)
    assert 'name="tag_id"' not in html and 'name="tags"' in html  # #278: one Tags box
    r = client.post("/automation/journal-tags", data={"name": "  Iron  fly "}, headers=ORIGIN)
    assert r.status_code == 200
    with sf(app)() as session:
        tid = session.scalar(select(Tag.id).where(Tag.name == "Iron fly"))
    assert r.get_json() == {"id": tid, "name": "Iron fly"}
    # The bot saves with it, and the form lists it from then on
    saved = client.post("/automation/save", data=form(app, tag_id=str(tid)), headers=ORIGIN)
    assert saved.status_code == 302
    assert "Iron fly" in client.get("/automation/new").get_data(as_text=True)  # the picker


def test_a_new_tag_with_a_name_taken_picks_that_tag(app):
    client = app.test_client()
    taken = client.post("/automation/journal-tags", data={"name": "0dte"}, headers=ORIGIN)
    with sf(app)() as session:
        tid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    assert taken.get_json() == {"id": tid, "name": "0DTE"}
    blank = client.post("/automation/journal-tags", data={"name": " "}, headers=ORIGIN)
    assert blank.status_code == 400 and blank.get_json() == {"error": "Give it a name."}


def test_a_new_tag_needs_this_site(app):
    r = app.test_client().post("/automation/journal-tags", data={"name": "X"},
                               headers={"Origin": "http://evil.example"})  # fmt: skip
    assert r.status_code == 403


def test_saving_a_bot_starts_it_live_and_disarmed(app):
    response = app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    assert response.status_code == 302
    with sf(app)() as session:
        bot = session.scalar(select(Bot))
    assert bot.name == "SPX IC" and bot.live and not bot.enabled
    page = app.test_client().get(f"/automation/{bot.id}").get_data(as_text=True)
    assert "Arm it (sends real orders)" in page
    assert "SPX IC" in app.test_client().get("/automation").get_data(as_text=True)


def test_outside_the_demo_live_words_say_real_orders(app):
    """Demo final review I-1 changed these words for the demo only: outside it they stay."""
    app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    listing = app.test_client().get("/automation").get_data(as_text=True).replace("&#39;", "'")
    assert "arm one and it sends real orders." in listing
    assert "counts the armed bots that are Live: they send real orders." in listing
    assert "asks you to tick a box: Live bots send real orders." in listing
    assert "Arm them afterwards to send real orders." in listing
    assert "pretend broker" not in listing
    page = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    page = page.replace("&#39;", "'")
    assert "Sends real orders." in page and 'aria-label="Type TRADE to send a real order"' in page
    assert "confirm('Send a real order now? It counts" in page
    assert "off its schedule, as a real order: type TRADE first." in page
    assert "pretend broker" not in page
    trade = app.test_client().get("/trade").get_data(as_text=True)
    assert "data-demo" not in trade


def test_unreadable_quantity_and_bound_are_shown_not_dropped(app):
    data = form(app, **{"time-0-qty": "two", "use_premium_bounds": "on", "premium_min": "x1"})
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "quantity &#39;two&#39; isn" in html and "premium &#39;x1&#39; isn" in html
    with sf(app)() as session:
        assert session.scalar(select(Bot)) is None


def test_problems_are_shown_beside_the_fields(app):
    data = form(app, name="", **{"leg-0-target": ""})
    html = (
        app.test_client().post("/automation/save", data=data, headers=ORIGIN).get_data(as_text=True)
    )
    assert "Give the bot a name" in html and "premium to aim for" in html


def test_going_live_asks_first(app):
    app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    page = app.test_client().get(f"/automation/{bot_id}/live").get_data(as_text=True)
    assert "real orders" in page and "••1234" in page
    app.test_client().post(f"/automation/{bot_id}/live", data=LIVE_TICKED,
                           headers=ORIGIN)  # fmt: skip
    assert bot_store.load(sf(app), bot_id).live
    app.test_client().post(f"/automation/{bot_id}/arm", data={"on": "1", "understood": "1", "confirmed": "1"},
                           headers=ORIGIN)  # fmt: skip
    assert bot_store.load(sf(app), bot_id).enabled


def test_runs_pages(app):
    app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
        now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
        run = BotRun(
            bot_id=bot_id, key="bot-1-0947-20261013", trading_date=now.date(), entry_time="09:47",
            status="skipped", live=False, reason="The market isn't open.", legs=[],
            filled_legs=[], chase_log=[], contracts=0, created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.commit()
        run_id = run.id
    runs = app.test_client().get(f"/automation/{bot_id}/runs").get_data(as_text=True)
    assert "The market isn&#39;t open." in runs  # the page escapes the apostrophe
    assert "09:47" in app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)


def test_delete_hides_the_bot(app):
    app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    app.test_client().post(f"/automation/{bot_id}/delete", headers=ORIGIN)
    assert "SPX IC" not in app.test_client().get("/automation").get_data(as_text=True)


def _saved_bot_id(app):
    app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    with sf(app)() as session:
        return session.scalar(select(Bot.id))


def test_arm_redirects_stay_on_the_site(app):
    bot_id = _saved_bot_id(app)
    client = app.test_client()
    url = f"/automation/{bot_id}/arm"
    listed = client.post(
        url, data={"on": "1", "next": "list", "understood": "1", "confirmed": "1"}, headers=ORIGIN
    )
    assert listed.headers["Location"] == "/automation"
    for extra in ({"next": "//evil.com"}, {}):
        r = client.post(url, data={"on": "0"} | extra, headers=ORIGIN)
        assert r.headers["Location"] == f"/automation/{bot_id}"


def test_a_failed_save_still_shows_the_stored_mode(app):
    bot_id = _saved_bot_id(app)
    client = app.test_client()
    data = form(app, id=str(bot_id), name="")
    html = client.post("/automation/save", data=data, headers=ORIGIN).get_data(as_text=True)
    assert "Give the bot a name" in html
    assert "Switch to Paper" in html and "badge--live" in html


def test_preview_order_page(app, monkeypatch):
    from optionsjourney.services.bot_entry import PreviewResult

    bot_id = _saved_bot_id(app)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    graph = {
        "key": "k",
        "label": "IC",
        "symbol": "SPX",
        "legs": [],
        "realized_pl": 0.0,
        "spot": 5800,
    }
    legs = [{"side": "sell", "strike": "5910", "right": "call", "ratio": 1, "mid": "3.05",
             "expiration": "2026-10-13"}]  # fmt: skip
    seen = []

    exits = ("Put spread: stop 4.80 rests at TastyTrade; no target.",)

    def fake_preview(deps, bot, now=None):
        seen.append(bot)
        return PreviewResult(True, "Checked by your broker.", legs, Decimal("5.02"),
                             Decimal("1200"), graph, 1, exits)  # fmt: skip

    monkeypatch.setattr(automation.bot_entry, "preview_order", fake_preview)
    r = app.test_client().post(f"/automation/{bot_id}/preview", headers=ORIGIN)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and seen == [bot_id]
    assert "Preview order" in html and "Your broker accepted it." in html
    assert "5910 Call" in html and 'id="risk-graph"' in html and 'id="preview-graph"' in html
    assert exits[0] in html
    with sf(app)() as session:
        assert session.scalar(select(BotRun)) is None  # no run saved


def test_preview_order_page_when_refused_has_no_graph(app, monkeypatch):
    from optionsjourney.services.bot_entry import PreviewResult

    bot_id = _saved_bot_id(app)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    monkeypatch.setattr(automation.bot_entry, "preview_order", lambda *a, **k: PreviewResult(
        False, "The market isn't open.", [], None, None, None))  # fmt: skip
    html = (
        app.test_client()
        .post(f"/automation/{bot_id}/preview", headers=ORIGIN)
        .get_data(as_text=True)
    )
    assert "The market isn&#39;t open." in html and "Not accepted." in html
    assert 'id="risk-graph"' not in html


def test_preview_order_page_survives_a_broker_failure(app, monkeypatch):
    from optionsjourney.brokers.http import BrokerError
    from optionsjourney.services import bot_entry

    def boom(*args, **kwargs):
        raise BrokerError("connection reset")

    bot_id = _saved_bot_id(app)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    monkeypatch.setattr(bot_entry, "_guard", lambda *a, **k: None)
    monkeypatch.setattr(bot_entry, "_find_legs", boom)
    r = app.test_client().post(f"/automation/{bot_id}/preview", headers=ORIGIN)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Couldn&#39;t reach the broker: connection reset" in html
    assert 'id="risk-graph"' not in html
    with sf(app)() as session:
        assert session.scalar(select(BotRun)) is None


def test_an_old_preview_run_still_renders(app):
    app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
        now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
        run = BotRun(
            bot_id=bot_id, key="bot-1-now-20261013140000", trading_date=now.date(),
            entry_time="09:47", status="preview", live=False, legs=[], filled_legs=[],
            chase_log=[], contracts=1, reason="Preview now: checked, not sent.",
            created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.commit()
        run_id = run.id
    r = app.test_client().get(f"/automation/runs/{run_id}")
    assert r.status_code == 200 and "run-chip--preview" in r.get_data(as_text=True)


PAPER_NOTICE = (
    "Paper trades are pretend. Fills are estimated at the mid price, and stops are watched "
    "by the app instead of resting at the broker, so real results would differ."
)


def _run_page_for(app, *, paper, legs=()):
    app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
        now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
        run = BotRun(
            bot_id=bot_id, key=f"bot-1-{now:%Y%m%d%H%M%S}", trading_date=now.date(),
            entry_time="09:47", status="filled", live=False, paper=paper, legs=list(legs),
            filled_legs=[{"symbol": leg["symbol"], "side": leg["side"], "fill_price": "2.40",
                          "contracts": 1} for leg in legs],
            chase_log=[], contracts=1, created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.commit()
        run_id = run.id
    return app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)


def test_paper_run_page_notice(app):
    assert PAPER_NOTICE in _run_page_for(app, paper=True)


def test_an_old_preview_run_page_has_no_paper_notice(app):
    assert PAPER_NOTICE not in _run_page_for(app, paper=False)


def test_a_paper_bot_page_has_the_paper_notice_and_a_live_one_does_not(app):
    bot_id = _saved_bot_id(app)
    assert PAPER_NOTICE not in app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    assert PAPER_NOTICE in app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)


def test_the_paper_filter_lists_paper_bots_and_not_live_ones(app):
    bot_id = _saved_bot_id(app)
    page = app.test_client().get("/automation?tag=Paper").get_data(as_text=True)
    assert "tag=Paper" in page and "tag=Preview" not in page
    assert "<table" not in page  # a Live bot is not listed under Paper
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    page = app.test_client().get("/automation?tag=Paper").get_data(as_text=True)
    assert f"/automation/{bot_id}" in page


NEW_FIELDS = {
    "contracts": "2", "max_per_trade": "5", "max_per_day": "6", "max_entries_per_day": "3",
    "max_open_trades": "2", "frequency": "weekly", "entry_days": ["1", "3", "5"],
    "monthly_week": "2", "monthly_weekday": "4", "specific_dates": "2026-10-14\n2026-10-15",
    "use_blackout_days": "on", "blackout_days": "2026-12-24\n2026-12-25",
    "use_premium_bounds": "on", "premium_min": "1.50",
    "premium_max": "4", "times": "2", "time-0-qty": "4", "time-0-label": "open",
    "time-1": "12:03", "time-1-qty": "", "time-1-label": "",
    # groups that stop on the entry price lost (they read the same on a debit fill)
    "group-0-stop-style": "group_entry", "group-1-stop-style": "group_entry",
}  # fmt: skip


class Fields(HTMLParser):
    """What a browser would submit for the page's first form."""

    def __init__(self):
        super().__init__()
        self.pairs, self._select, self._text, self._options = [], None, None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get("name")
        if tag == "select" and name:
            self._select, self._options = name, []
        elif tag == "option" and self._select:
            self._options.append((a.get("value", ""), "selected" in a))
        elif tag == "textarea" and name:
            self._text = name
            self.pairs.append([name, ""])
        elif tag == "input" and name:
            kind = a.get("type", "text")
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self.pairs.append([name, a.get("value", "on")])
            else:
                self.pairs.append([name, a.get("value", "")])

    def handle_endtag(self, tag):
        if tag == "select" and self._select:
            chosen = (
                [v for v, picked in self._options if picked]
                or self._options[:1]
                and [self._options[0][0]]
            )
            self.pairs += [[self._select, v] for v in chosen]
            self._select = None
        elif tag == "textarea":
            self._text = None

    def handle_data(self, data):
        if self._text:
            self.pairs[-1][1] += data


def posted_back(html):
    """The page's save form as a multi-dict, the way a browser sends it."""
    from werkzeug.datastructures import MultiDict

    html = html[html.index('class="bot-form"') :]
    parser = Fields()
    parser.feed(html[: html.index("</form>")])
    return MultiDict([tuple(pair) for pair in parser.pairs])


def test_the_bot_page_has_schedule_and_filters_sections(app):
    bot_id = _saved_bot_id(app)
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    for text in ("Schedule", "Filters", "Blackout days", "Premium bounds"):
        assert text in html
    for name in ("max_entries_per_day", "max_open_trades", "frequency", "entry_days",
                 "monthly_week", "monthly_weekday", "specific_dates", "use_blackout_days",
                 "blackout_days", "use_premium_bounds", "premium_min",
                 "premium_max", "time-0-qty", "time-0-label"):  # fmt: skip
        assert f'name="{name}"' in html, name
    assert html.count('name="entry_days"') == 5  # #114: Mon to Fri, no weekend


def test_an_entry_times_label_has_no_box_but_is_kept(app):
    """#114 (Tracker2's form has no box for it): a saved label goes back hidden."""
    bot_id = _saved_bot_id(app)
    bot = bot_store.load(sf(app), bot_id)
    first = replace(bot.entry_times[0], label="Open")
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    labelled = replace(bot, entry_times=(first, *bot.entry_times[1:]))
    _, problems = bot_store.save(sf(app), labelled, now)
    assert problems == {}
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert '<input type="hidden" name="time-0-label" value="Open">' in html
    assert 'placeholder="Label"' not in html


def test_the_schedule_marks_what_each_choice_shows(app):
    """#114 (Tracker2's applyEntryToggles): schedule_choice.js hides by these marks."""
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert "schedule_choice.js" in html and "data-schedule-choice" in html
    assert '<div class="inline-form" data-shown-by="use_floating_entry">' in html
    assert '<div data-hidden-by="use_floating_entry">' in html  # the entry times
    for frequency in ("weekly", "monthly", "specific_dates"):
        assert f'data-frequency="{frequency}"' in html
    assert html.count('data-shown-by="use_blackout_days"') == 2
    assert 'data-sizing="contracts" >' not in html and 'data-sizing="contracts">' in html
    assert html.count('data-sizing="contracts">') == 4  # Contracts per entry + 3 time rows
    assert "data-add-time" in html and html.count("data-remove-time") == 3
    assert 'class="day-toggle"><input type="checkbox" name="entry_days" value="1"' in html
    assert "Seconds are optional, in 15-second steps" in html  # the Entry times tip


def test_the_side_menu_jumps_to_each_section_shown(app):
    client = app.test_client()
    new = client.get("/automation/new").get_data(as_text=True)
    saved = client.get(f"/automation/{_saved_bot_id(app)}").get_data(as_text=True)
    for html, first, last in ((new, "start", "chase"), (saved, "basics", "this-bot")):
        links = re.findall(r'class="bot-page__link" href="#([a-z-]+)"', html)
        assert links[0] == first and links[-1] == last
        for key in links:
            assert f'id="{key}"' in html, key
    assert 'href="#this-bot"' not in new and 'href="#start"' not in saved


def test_an_edit_round_trips_every_new_setting(app):
    client = app.test_client()
    data = form(app) | NEW_FIELDS | {"time-0-on": "on"}
    assert client.post("/automation/save", data=data, headers=ORIGIN).status_code == 302
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    before = bot_store.load(sf(app), bot_id)
    assert before.entry_days == (1, 3, 5) and before.max_entries_per_day == 3
    assert before.entry_times[0].quantity == 4 and before.entry_times[0].label == "open"
    assert not before.entry_times[1].enabled
    html = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    again = client.post("/automation/save", data=posted_back(html), headers=ORIGIN)
    assert again.status_code == 302
    assert bot_store.load(sf(app), bot_id) == before


def test_bad_dates_are_shown_by_name_beside_the_field(app):
    data = form(app) | NEW_FIELDS | {"frequency": "specific_dates", "specific_dates": "soon"}
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 400 and "soon" in r.get_data(as_text=True)


def _armed_bot(app, monkeypatch, when=datetime(2026, 10, 12, 14, 0, tzinfo=UTC)):
    bot_id = _saved_bot_id(app)
    app.test_client().post(
        f"/automation/{bot_id}/arm",
        data={"on": "1", "understood": "1", "confirmed": "1"},
        headers=ORIGIN,
    )  # fmt: skip  (0054: an iron condor's stops are watched)
    monkeypatch.setattr(automation, "_now", lambda: when)
    return bot_id


def test_skip_and_undo_from_the_bot_page(app, monkeypatch):
    bot_id = _armed_bot(app, monkeypatch)
    client = app.test_client()
    assert "Skip today" in client.get(f"/automation/{bot_id}").get_data(as_text=True)
    r = client.post(f"/automation/{bot_id}/skip", headers=ORIGIN)
    assert r.headers["Location"] == f"/automation/{bot_id}"
    assert bot_store.load(sf(app), bot_id).blackout_days == ("2026-10-12",)
    html = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "Skipping Mon 10/12" in html and "Undo" in html and "Skip today" not in html
    client.post(f"/automation/{bot_id}/unskip", data={"day": "2026-10-12"}, headers=ORIGIN)
    loaded = bot_store.load(sf(app), bot_id)
    assert loaded.blackout_days == () and not loaded.use_blackout_days


def test_after_the_close_the_button_names_the_next_day(app, monkeypatch):
    bot_id = _armed_bot(app, monkeypatch, datetime(2026, 10, 12, 21, 0, tzinfo=UTC))
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "Skip Tue 10/13" in html


def test_skip_needs_an_armed_bot_and_keeps_redirects_on_site(app, monkeypatch):
    bot_id = _saved_bot_id(app)
    monkeypatch.setattr(automation, "_now", lambda: datetime(2026, 10, 12, 14, 0, tzinfo=UTC))
    client = app.test_client()
    assert "Skip today" not in client.get(f"/automation/{bot_id}").get_data(as_text=True)
    r = client.post(f"/automation/{bot_id}/skip", data={"next": "list"}, headers=ORIGIN)
    assert r.headers["Location"] == "/automation"
    assert bot_store.load(sf(app), bot_id).blackout_days == ()
    page = client.get("/automation").get_data(as_text=True)
    assert "Only an armed bot can skip a day" in page
    r = client.post(f"/automation/{bot_id}/skip", data={"next": "//evil.com"}, headers=ORIGIN)
    assert r.headers["Location"] == f"/automation/{bot_id}"
    r = client.post(f"/automation/{bot_id}/unskip", data={"day": "nonsense"}, headers=ORIGIN)
    assert r.status_code == 302  # an unreadable day is ignored, not an error


def test_bulk_skip_from_the_list(app, monkeypatch):
    armed = _armed_bot(app, monkeypatch)
    client = app.test_client()
    other = form(app, name="Second")
    client.post("/automation/save", data=other, headers=ORIGIN)
    with sf(app)() as session:
        second = session.scalar(select(Bot.id).where(Bot.name == "Second"))
    html = client.get("/automation").get_data(as_text=True)
    assert html.count('name="bot_id"') == 2 and "Skip day" in html
    r = client.post("/automation/skip", data={"bot_id": [str(armed), str(second)]}, headers=ORIGIN)
    assert r.headers["Location"] == "/automation"
    assert bot_store.load(sf(app), armed).blackout_days == ("2026-10-12",)
    assert bot_store.load(sf(app), second).blackout_days == ()
    assert "Not armed, so not skipped: Second" in client.get("/automation").get_data(as_text=True)


def test_tranches_are_grouped_under_their_root(app):
    bot_id = _saved_bot_id(app)
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)

    def run(key, parent=None, reason=""):
        return BotRun(
            bot_id=bot_id, key=key, trading_date=now.date(), entry_time="09:47", status="filled",
            live=False, reason=reason, legs=[], filled_legs=[], chase_log=[], contracts=1,
            parent_run_id=parent, created_at=now, updated_at=now,
        )  # fmt: skip

    with sf(app)() as session:
        root = run("bot-1-0947-20261013")
        session.add(root)
        session.flush()
        session.add_all(
            [run("bot-1-0947-20261013#t2", root.id), run("bot-1-0947-20261013#t3", root.id)]
        )
        session.add(run("bot-1-1203-20261013", reason="later root"))
        session.commit()
    html = app.test_client().get(f"/automation/{bot_id}/runs").get_data(as_text=True)
    assert html.count("tranche 2") == 1 and html.count("tranche 3") == 1
    # the newest root first, and the first root's tranches right after it
    assert html.index("later root") < html.index("tranche 2") < html.index("tranche 3")


def test_the_sides_offer_stop_styles_units_and_the_wing(app):
    bot_id = _saved_bot_id(app)
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    for name in ("group-0-stop-unit", "group-0-stop-basis", "group-0-target-unit",
                 "group-0-remainder",
                 "group-0-remainder-limit", "stop_co_mode", "stop_co_amount"):  # fmt: skip
        assert f'name="{name}"' in html, name
    # #124: Tracker2's names for the stop's unit and basis
    for text in ("% / basis", "entry premium", "closing order ($)", "Closing order",
                 "Whichever fires first", "Left alone"):  # fmt: skip
        assert text in html, text


EXIT_FIELDS = {
    "group-0-stop": "0.20", "group-0-stop-style": "closing_order",
    "group-0-target": "1.00", "group-0-target-unit": "usd",
    "group-0-remainder": "close_limit", "group-0-remainder-limit": "0.10",
    "group-1-stop": "2.00", "group-1-stop-style": "usd", "group-1-remainder": "leave",
    "stop_co_mode": "tighter", "stop_co_amount": "0.15",
}  # fmt: skip


def test_an_edit_round_trips_the_exit_settings(app):
    client = app.test_client()
    data = form(app) | EXIT_FIELDS
    assert client.post("/automation/save", data=data, headers=ORIGIN).status_code == 302
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    before = bot_store.load(sf(app), bot_id)
    call, put = before.groups
    assert (call.stop_basis, call.stop_pct, call.target_usd) == (
        "closing_order",
        Decimal("0.20"),
        Decimal("1.00"),
    )
    assert (put.stop_unit, put.stop_usd, put.on_stop_remainder) == ("usd", Decimal("2.00"), "leave")
    assert (before.stop_co_mode, before.stop_co_amount) == ("tighter", Decimal("0.15"))
    html = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    again = client.post("/automation/save", data=posted_back(html), headers=ORIGIN)
    assert again.status_code == 302
    assert bot_store.load(sf(app), bot_id) == before


def test_an_unreadable_stop_is_named_beside_its_side(app):
    data = form(app) | {"group-0-stop": "9S"}
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 400 and "9S" in r.get_data(as_text=True)


def test_the_wing_switch_shows_only_when_something_leaves_its_wing(app):
    client = app.test_client()
    client.post("/automation/save", data=form(app), headers=ORIGIN)
    assert "Wings left open" not in client.get("/automation").get_data(as_text=True)
    client.post("/automation/leave/pause", data={"until": ""}, headers=ORIGIN)
    html = client.get("/automation").get_data(as_text=True)  # a pause stays resumable
    assert 'action="/automation/leave/resume"' in html


def test_leave_can_be_paused_and_resumed_from_the_list(app):
    client = app.test_client()
    client.post("/automation/save", data=form(app) | EXIT_FIELDS, headers=ORIGIN)
    assert 'action="/automation/leave/pause"' in client.get("/automation").get_data(as_text=True)
    client.post("/automation/leave/pause", data={"until": ""}, headers=ORIGIN)
    assert bot_store.leave_paused(sf(app), datetime.now(UTC))
    assert 'action="/automation/leave/resume"' in client.get("/automation").get_data(as_text=True)
    client.post("/automation/leave/resume", headers=ORIGIN)
    assert not bot_store.leave_paused(sf(app), datetime.now(UTC))
    r = client.post("/automation/leave/pause", data={"until": "nonsense"}, headers=ORIGIN,
                    follow_redirects=True)  # fmt: skip
    assert "like 15:30" in r.get_data(as_text=True)
    assert not bot_store.leave_paused(sf(app), datetime.now(UTC))


def test_the_run_page_shows_how_each_stop_was_worked_out(app):
    bot_id = _saved_bot_id(app)
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    calc = {"basis": "leg_premium", "configured": "95", "short_fill": "2.50",
            "entry_credit": "4.90", "configured_raw": "4.8750", "co_mode": "off",
            "co_amount": None, "co_raw": None, "winner": "basis", "raw": "4.8750",
            "tick": "0.10", "trigger": "4.80", "problem": None}  # fmt: skip
    with sf(app)() as session:
        run = BotRun(
            bot_id=bot_id, key="bot-1-0947-20261013", trading_date=now.date(), entry_time="09:47",
            status="filled", live=True, reason="", legs=[], filled_legs=[], chase_log=[],
            contracts=1, created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        session.add(BotExit(
            run_id=run.id, group_position=0, kind="stop", state="working",
            short_symbol="SPXW  261013C05910000", long_symbol="SPXW  261013C05985000",
            contracts=1, trigger=Decimal("4.80"), rearms=0,
            log=[{"at": now.isoformat(), "stop_calc": calc}], created_at=now, updated_at=now,
        ))  # fmt: skip
        session.commit()
        run_id = run.id
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "4.875" in html and "rounded down to the 0.10 tick" in html


def _closing_target(app):
    bot_id = _saved_bot_id(app)
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    with sf(app)() as session:
        run = BotRun(
            bot_id=bot_id, key="bot-1-0947-20261013", trading_date=now.date(), entry_time="09:47",
            status="filled", live=True, reason="", legs=[], filled_legs=[], chase_log=[],
            contracts=1, created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        target = BotExit(
            run_id=run.id, group_position=0, kind="target", state="firing",
            short_symbol="SPXW  261013C05910000", long_symbol="SPXW  261013C05985000",
            contracts=1, limit=Decimal("1.20"), rearms=0, log=[], created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(target)
        session.commit()
        return run.id, target.id


def test_the_run_page_shows_cancel_for_a_closing_target(app):
    run_id, exit_id = _closing_target(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "closing" in html
    assert f"/automation/runs/{run_id}/exits/{exit_id}/cancel" in html


def test_the_cancel_route_marks_the_close(app):
    run_id, exit_id = _closing_target(app)
    client = app.test_client()
    response = client.post(f"/automation/runs/{run_id}/exits/{exit_id}/cancel", headers=ORIGIN)
    assert response.status_code == 302
    with sf(app)() as session:
        assert session.get(BotExit, exit_id).cancel_requested_at is not None
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "cancel asked" in html
    again = client.post(f"/automation/runs/{run_id}/exits/{exit_id}/cancel", headers=ORIGIN)
    assert again.status_code == 302


def test_cancel_during_the_leg_out_at_market_says_it_cant_stop_it_now(app):
    run_id, exit_id = _closing_target(app)
    with sf(app)() as session:
        target = session.get(BotExit, exit_id)
        target.log = [{"stage": "leg_out", "leg": target.short_symbol, "market": 1}]
        session.commit()
    client = app.test_client()
    client.post(f"/automation/runs/{run_id}/exits/{exit_id}/cancel", headers=ORIGIN)
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True).replace("&#39;", "'")
    assert "can't stop it now" in html and "Its stop goes back" not in html


def test_cancel_for_a_close_that_isnt_running_says_so(app):
    run_id, exit_id = _closing_target(app)
    with sf(app)() as session:
        session.get(BotExit, exit_id).state = "filled"
        session.commit()
    client = app.test_client()
    client.post(f"/automation/runs/{run_id}/exits/{exit_id}/cancel", headers=ORIGIN)
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "isn't running any more" in html.replace("&#39;", "'")


def test_the_chase_section_shows_the_exhaust_choice(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert 'name="chase-exit-exhaust"' in html and 'name="chase-exit-natural"' in html
    assert "Natural price, then leg out" in html and "Give up and put the stop back" in html
    client = app.test_client()
    data = form(app, **{"chase-exit-exhaust": "give_up", "chase-exit-natural": "4"})
    client.post("/automation/save", data=data, headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    chase = bot_store.load(sf(app), bot_id).exit_chase
    assert (chase.exhaust_mode, chase.natural_minutes) == ("give_up", 4)
    page = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'value="give_up" selected' in page and 'value="4"' in page


def test_the_chase_section_offers_keep_conceding(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert "Keep lowering the price until it fills" in html
    assert "never past the natural price, until it fills or the market closes" in html
    client = app.test_client()
    client.post("/automation/save", data=form(app, **{"chase-exit-exhaust": "keep_conceding"}),
                headers=ORIGIN)  # fmt: skip
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    assert bot_store.load(sf(app), bot_id).exit_chase.exhaust_mode == "keep_conceding"
    page = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'value="keep_conceding" selected' in page


def test_a_natural_window_out_of_range_is_named(app):
    data = form(app, **{"chase-exit-exhaust": "natural_then_leg_out", "chase-exit-natural": "45"})
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 400
    assert "1 to 30 minutes" in response.get_data(as_text=True)


# Slice 5: market filters, date lists and the Market calendar panel

FILTERS = {
    "use_vix_level": "on", "vix_min": "12", "vix_max": "20",
    "use_vix_overnight": "on", "vix_on_max_up": "3",
    "use_vix9d_ratio": "on", "vix9d_ratio_max": "0.95",
    "use_technicals": "on", "sma_mode": "above", "sma_days_a": "5",
    "ema_mode": "compare", "ema_minutes_a": "5", "ema_minutes_b": "20",
    "use_intraday_move": "on", "intraday_min_up": "0.3",
    "use_blackout_days": "on", "blackout_date_sets": ["CPI", "EOM"],
    "blackout_dte_sets": ["HOLIDAY", "FOMC"],
}  # fmt: skip


def saved_id(app):
    with sf(app)() as session:
        return session.scalar(select(Bot.id).order_by(Bot.id.desc()))


def test_the_filters_and_lists_round_trip_through_the_page(app):
    client = app.test_client()
    response = client.post("/automation/save", data=form(app, **FILTERS), headers=ORIGIN)
    assert response.status_code == 302
    bot = bot_store.load(sf(app), saved_id(app))
    assert bot.use_vix_level and bot.vix_max == Decimal("20")
    assert bot.blackout_date_sets == ("CPI", "EOM")
    html = client.get(f"/automation/{bot.id}").get_data(as_text=True)
    page = Inputs()
    page.feed(html)
    assert page.values["vix_max"] == "20" and page.values["vix_on_max_up"] == "3"
    assert page.values["ema_minutes_b"] == "20" and page.values["intraday_min_up"] == "0.3"
    assert page.selected["sma_mode"] == "above" and page.selected["ema_mode"] == "compare"
    assert {"use_vix_level", "use_technicals", "use_intraday_move"} <= page.checked
    assert ("blackout_date_sets", "CPI") in page.checked_values
    assert ("blackout_date_sets", "FOMC") not in page.checked_values
    assert ("specific_date_sets", "FOMC") in page.boxes
    # #207: each list has Entry day and Before expiration; a market holiday only the latter
    assert bot.blackout_dte_sets == ("FOMC", "HOLIDAY")
    assert {("blackout_dte_sets", "FOMC"), ("blackout_dte_sets", "HOLIDAY")} <= (
        page.checked_values
    )
    assert ("blackout_dte_sets", "CPI") in page.boxes
    assert ("blackout_dte_sets", "CPI") not in page.checked_values
    assert ("blackout_date_sets", "HOLIDAY") not in page.boxes
    assert ("specific_date_sets", "HOLIDAY") not in page.boxes
    assert "Market holiday (before expiration)" in html


def test_an_unreadable_filter_number_is_shown_beside_its_filter(app):
    data = form(app, use_vix_level="on", vix_max="1,5")
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 400
    assert "The maximum VIX &#39;1,5&#39; isn&#39;t a number." in response.get_data(as_text=True)


def test_each_filter_is_a_row_with_its_pill_and_boxes(app):
    # #134: six rows, each pill marks its boxes for filter_fields.js; moves are Up/Down by Min/Max
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    section = html[html.index('id="filters"') : html.index('id="exits"')]
    assert section.count("data-filter-use") == 6 and section.count("data-filter-fields") == 6
    for name in ("use_premium_bounds", "use_vix_level", "use_vix_overnight", "use_vix9d_ratio",
                 "use_technicals", "use_intraday_move"):  # fmt: skip
        assert f'name="{name}" data-filter-use' in section
    assert section.count('data-when="compare"') == 2 and "&gt; days" in section
    assert 'aria-label="VIX overnight move: max up %"' in section
    assert "Preview order ignores them all" in section
    assert "filter_fields.js" in html


def test_a_filter_with_a_problem_keeps_its_boxes_shown(app):
    data = form(app, use_vix_level="on", vix_max="1,5")
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.get_data(as_text=True).count("data-filter-problem") == 1


def test_exits_alerts_and_chase_are_rows_with_their_pills_and_boxes(app):
    # #135: time actions and default alerts are pills marking their boxes; a default alert's
    # boxes and the natural price's minutes show only for their choice (filter_fields.js)
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    exits = html[html.index('id="exits"') : html.index('id="watch"')]
    assert 'name="use_time_actions" data-filter-use' in exits
    assert exits.count("data-filter-fields") == 1 and 'name="close_time"' in exits
    alerts = html[html.index('id="alerts"') : html.index('id="chase"')]
    assert 'name="use_default_alerts" data-filter-use' in alerts
    assert alerts.count('data-when="pnl_pct"') == 1  # one blank row; + Add alert adds more
    assert alerts.count('data-when="strike_offset"') == 1
    assert 'data-alert-most="10"' in alerts and "data-add-alert" in alerts
    chase = html[html.index('id="chase"') : html.index("</form>", html.index('id="chase"'))]
    assert 'after the last try" data-choice-by' in chase and "chase-table" in chase
    assert 'data-when="natural_then_leg_out"' in chase and 'name="chase-exit-market"' in chase


def test_watch_exits_is_its_own_card_like_tracker2s(app):
    # #136 (Tracker2's Watch exits): its three Use pills and their boxes, a delta row per
    # leg for watch_rows.js, and a short put row (the iron condor sells both) and call row
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    watch = html[html.index('id="watch"') : html.index('id="alerts"')]
    for name in ("use_delta_exit", "use_sl_ratio_exit", "use_underlying_exit"):
        assert f'name="{name}" data-filter-use' in watch
    assert "Exit below position delta" in watch and "Exit when S/L ratio is above" in watch
    assert watch.count("data-leg-delta=") == 4 and "Add leg delta exit" in watch
    assert 'name="leg-0-delta-below"' in watch
    assert 'data-short-tested="put"' in watch and 'data-short-tested="call"' in watch
    assert "Exit when OTM short put is" in watch and "watch_rows.js" in html
    assert '<a class="bot-page__link" href="#watch">Watch exits</a>' in html


def test_a_watch_rule_problem_keeps_every_rule_shown_and_says_it_once(app):
    data = form(app, use_sl_ratio_exit="on")
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    html = response.get_data(as_text=True)
    watch = html[html.index('id="watch"') : html.index('id="alerts"')]
    assert watch.count("data-filter-problem") == 3 and "data-keep" in watch
    assert watch.count("notice notice--warning small") == 1


def test_a_filter_ticked_with_nothing_set_is_refused(app):
    data = form(app, use_vix9d_ratio="on")
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 400
    assert "VIX9D/VIX ratio is on" in response.get_data(as_text=True)


def test_the_market_calendar_panel_is_on_settings(app):
    assert "Refresh now" not in app.test_client().get("/automation").get_data(as_text=True)
    html = app.test_client().get("/settings").get_data(as_text=True)
    assert "Market calendar" in html
    for words in ("FOMC days", "CPI days", "Last trading day of the month", "Refresh now",
                  "ForexFactory", "NYSE calendar", "Never"):  # fmt: skip
        assert words in html, words


def test_refresh_now_starts_a_background_refresh(app, monkeypatch):
    started = []
    monkeypatch.setattr(settings_routes, "_start_refresh", lambda sf, now: started.append(now))
    client = app.test_client()
    response = client.post("/settings/market_calendar/refresh", headers=ORIGIN)
    assert response.status_code == 302 and len(started) == 1
    assert response.headers["Location"].endswith("/settings#market_calendar")
    assert "Refreshing the market calendar" in client.get("/settings").get_data(as_text=True)


def test_the_settings_page_has_the_fred_key(app):
    html = app.test_client().get("/settings").get_data(as_text=True)
    assert 'name="fred_api_key"' in html and "Market calendar" in html


class Inputs(HTMLParser):
    """The form's text values, selected options and ticked boxes."""

    def __init__(self):
        super().__init__()
        self.values, self.selected, self._select = {}, {}, None
        self.checked, self.checked_values, self.boxes = set(), set(), set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("type") == "checkbox":
            self.boxes.add((a.get("name"), a.get("value")))
            if "checked" in a:
                self.checked.add(a.get("name"))
                self.checked_values.add((a.get("name"), a.get("value")))
        elif tag == "input" and a.get("name"):
            self.values[a["name"]] = a.get("value", "")
        elif tag == "select":
            self._select = a.get("name")
        elif tag == "option" and self._select and "selected" in a:
            self.selected[self._select] = a.get("value")


def test_settings_shows_even_when_the_calendar_summary_breaks(app, monkeypatch):
    from optionsjourney.services import economic_events

    def broken(*args):
        raise RuntimeError("database locked")

    monkeypatch.setattr(economic_events, "summary", broken)
    response = app.test_client().get("/settings")
    assert response.status_code == 200
    assert "Market calendar unavailable" in response.get_data(as_text=True)


def test_refresh_now_while_one_is_running_says_so(app, monkeypatch):
    from optionsjourney.services import economic_events

    started = []
    monkeypatch.setattr(settings_routes, "_start_refresh", lambda sf, now: started.append(now))
    with economic_events.REFRESH_LOCK:
        client = app.test_client()
        client.post("/settings/market_calendar/refresh", headers=ORIGIN)
        html = client.get("/settings").get_data(as_text=True)
    assert started == [] and "already running" in html


# Slice 4: timed close and time actions

TIMED = {
    "close_time": "15:45", "close_dte": "0", "use_time_actions": "on", "actions": "3",
    "ta-0-type": "dte", "ta-0-days": "0", "ta-0-at": "14:00", "ta-0-stop": "150",
    "ta-0-target": "",
    "ta-1-type": "dit", "ta-1-days": "0", "ta-1-at": "15:00", "ta-1-stop": "",
    "ta-1-target": "25",
    "ta-2-type": "dte", "ta-2-days": "", "ta-2-at": "", "ta-2-stop": "", "ta-2-target": "",
}  # fmt: skip


def test_the_bot_page_shows_the_exits_section(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert "Timed close" in html and "Time actions" in html
    for name in ("close_time", "close_dte", "use_time_actions", "actions", "ta-0-type",
                 "ta-0-days", "ta-0-at", "ta-0-stop", "ta-0-target", "ta-1-at"):  # fmt: skip
        assert f'name="{name}"' in html, name


def test_saving_a_timed_close_and_time_actions(app):
    client = app.test_client()
    response = client.post("/automation/save", data=form(app, **TIMED), headers=ORIGIN)
    assert response.status_code == 302
    bot = bot_store.load(sf(app), saved_id(app))
    assert (bot.close_time, bot.close_dte, bot.use_time_actions) == ("15:45", 0, True)
    assert [a.label for a in bot.time_actions] == [
        "0 DTE @ 14:00: stop 150%",
        "0 DIT @ 15:00: target 25%",
    ]
    html = client.get(f"/automation/{bot.id}").get_data(as_text=True)
    again = client.post("/automation/save", data=posted_back(html), headers=ORIGIN)
    assert again.status_code == 302
    assert bot_store.load(sf(app), bot.id) == bot


def test_an_entry_after_the_timed_close_is_shown_beside_it(app):
    data = form(app, **(TIMED | {"close_time": "09:45"}))
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 400
    assert "at or after the timed close" in response.get_data(as_text=True)


def test_the_run_page_shows_a_timed_close_with_cancel_and_a_time_action(app):
    run_id, target_id = _closing_target(app)
    now = datetime(2026, 10, 13, 19, 45, tzinfo=UTC)
    with sf(app)() as session:
        session.get(BotExit, target_id).state = "cancelled"
        timed = BotExit(
            run_id=run_id, group_position=0, kind="timed", state="firing",
            short_symbol="SPXW  261013C05910000", long_symbol="SPXW  261013C05985000",
            contracts=1, rearms=0, log=[{"event": "timed close: 15:45, 0 DTE or fewer"}],
            created_at=now, updated_at=now,
        )  # fmt: skip
        action = BotExit(
            run_id=run_id, group_position=1, kind="action", state="filled",
            short_symbol="SPXW  261013P05690000", long_symbol="SPXW  261013P05615000",
            contracts=1, rearms=0,
            log=[{"sig": "x", "rule": "0 DTE @ 14:00: target 25%", "notes": ["target 1.2 → 1.8"]}],
            created_at=now, updated_at=now,
        )  # fmt: skip
        session.add_all([timed, action])
        session.commit()
        timed_id = timed.id
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "timed close" in html and "time action" in html
    assert f"/automation/runs/{run_id}/exits/{timed_id}/cancel" in html
    assert "0 DTE @ 14:00: target 25%" in html and "target 1.2 → 1.8" in html


def test_a_close_time_just_before_a_half_day_close_is_warned(app):
    client = app.test_client()
    client.post("/automation/save", data=form(app, **(TIMED | {"close_time": "12:50"})),
                headers=ORIGIN)  # fmt: skip
    html = client.get(f"/automation/{saved_id(app)}").get_data(as_text=True)
    assert "less than 15 minutes" in html
    client.post("/automation/save", data=form(app, **TIMED), headers=ORIGIN)
    html = client.get(f"/automation/{saved_id(app)}").get_data(as_text=True)
    assert "less than 15 minutes" not in html
    assert "at or after the 13:00 close" in html


# --- Slice 8: the record, tags, clone, archive, the bot file -------------------------


def saved_bot(app, **changes):
    app.test_client().post("/automation/save", data=form(app, **changes), headers=ORIGIN)
    with sf(app)() as session:
        return session.scalars(select(Bot.id).order_by(Bot.id.desc())).first()


def test_the_list_shows_pl_schedule_and_tags_not_win_rate(app, monkeypatch):
    from optionsjourney.core.stats import journal_summary
    from optionsjourney.services import bot_stats

    bot_id = saved_bot(app, tags="Mornings, 0DTE")
    summary = replace(journal_summary([]), closed_pl=Decimal("250"), winners=3,
                      win_rate=Decimal("60"))  # fmt: skip
    monkeypatch.setattr(bot_stats, "summaries", lambda *a: {bot_id: bot_stats.Stats(summary, 5)})
    html = app.test_client().get("/automation").get_data(as_text=True)
    assert '<span class="chip">Mornings</span>' in html and "$250.00" in html
    assert "Win rate" not in html and "3 of 5" not in html and "<th>Schedule</th>" in html


def test_the_list_still_shows_the_bots_if_the_record_cant_be_read(app, monkeypatch):
    from optionsjourney.services import bot_stats

    saved_bot(app)

    def broken(*args):
        raise RuntimeError("journal")

    monkeypatch.setattr(bot_stats, "summaries", broken)
    response = app.test_client().get("/automation")
    assert response.status_code == 200 and "SPX IC" in response.get_data(as_text=True)


def test_the_bot_form_offers_every_bot_tag_to_pick(app):
    saved_bot(app, tags="Mornings, 0DTE")
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert 'class="tag-picker"' in html and "tag_picker.js" in html
    known = json.loads(html.split('data-known="')[1].split('"')[0].replace("&#34;", '"'))
    assert {"Mornings", "Wheel"} <= set(known)  # a bot's tag, and the journal's (#110)
    assert 'name="tags" value=""' in html  # the comma box is still what's sent


def test_the_list_filters_by_tag(app):
    saved_bot(app, name="Morning IC", tags="Mornings")
    saved_bot(app, name="Late IC", tags="Afternoons")
    html = app.test_client().get("/automation?tag=mornings").get_data(as_text=True)
    assert "Morning IC" in html and "Late IC" not in html
    html = app.test_client().get("/automation?tag=Off").get_data(as_text=True)
    assert "Morning IC" in html and "Late IC" in html  # both are off
    html = app.test_client().get("/automation?tag=Armed").get_data(as_text=True)
    assert "No bots with that tag" in html


def test_the_record_page_shows_stats_and_trades(app):
    bot_id = saved_bot(app)
    html = app.test_client().get(f"/automation/{bot_id}/record").get_data(as_text=True)
    assert "Closed P/L" in html and "Win rate" in html and "No closed trades yet" in html
    assert "/journal?tag=" in html and "account=tastytrade:5WX01234" in html
    html = (
        app.test_client().get(f"/automation/{bot_id}/record?result=losers").get_data(as_text=True)
    )
    assert "No closed trades here yet" in html
    assert app.test_client().get("/automation/999/record").status_code == 404


def test_clone_opens_the_copy(app):
    bot_id = saved_bot(app)
    with sf(app)() as session:
        session.get(Bot, bot_id).live = True
        session.commit()
    response = app.test_client().post(f"/automation/{bot_id}/clone", headers=ORIGIN)
    with sf(app)() as session:
        copy = session.scalars(select(Bot).order_by(Bot.id.desc())).first()
    assert response.headers["Location"].endswith(f"/automation/{copy.id}")
    assert copy.name == "SPX IC (copy)" and copy.live and not copy.enabled


def test_bot_list_rows_have_a_clone_button(app):
    bot_id = saved_bot(app)
    html = app.test_client().get("/automation").get_data(as_text=True)
    assert f'action="/automation/{bot_id}/clone"' in html


def test_archive_refused_says_why(app):
    bot_id = saved_bot(app)
    with sf(app)() as session:
        session.add(BotRun(
            bot_id=bot_id, key="k", trading_date=datetime.now(UTC).date(), entry_time="09:47",
            status="running", live=False, legs=[], chase_log=[], contracts=1,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        ))  # fmt: skip
        session.commit()
    client = app.test_client()
    client.post(f"/automation/{bot_id}/archive", headers=ORIGIN)
    html = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "Can&#39;t archive it while anything is open: 1 entry in progress" in html
    assert not bot_store.load(sf(app), bot_id).archived


def test_archive_and_show_archived(app):
    bot_id = saved_bot(app)
    client = app.test_client()
    client.post(
        f"/automation/{bot_id}/arm",
        data={"on": "1", "understood": "1", "confirmed": "1"},
        headers=ORIGIN,
    )
    response = client.post(f"/automation/{bot_id}/archive", headers=ORIGIN)
    assert response.headers["Location"].endswith("/automation")
    html = client.get("/automation").get_data(as_text=True)
    assert "SPX IC" not in html and "Show archived (1)" in html
    html = client.get("/automation?archived=1").get_data(as_text=True)
    assert "SPX IC" in html and "Archived" in html
    client.post(
        f"/automation/{bot_id}/arm",
        data={"on": "1", "understood": "1", "confirmed": "1"},
        headers=ORIGIN,
    )
    assert not bot_store.load(sf(app), bot_id).enabled
    html = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "unarchive it first" in html and "Unarchive" in html
    client.post(f"/automation/{bot_id}/unarchive", headers=ORIGIN)
    bot = bot_store.load(sf(app), bot_id)
    assert not bot.archived and not bot.enabled


def test_the_exported_file_has_no_account_journal_tag_or_tags(app):
    bot_id = saved_bot(app, tags="Mine", contracts="2", max_per_trade="3", max_per_day="4")
    response = app.test_client().get(f"/automation/{bot_id}/export")
    assert response.mimetype == "application/json"
    assert 'filename="spx-ic.json"' in response.headers["Content-Disposition"]
    text = response.get_data(as_text=True)
    data = json.loads(text)
    assert data["format"] == "optionsjourney.bot" and data["bot"]["name"] == "SPX IC"
    assert "5WX01234" not in text and "Mine" not in text and "0DTE" not in text
    for key in ("account_number", "tag_id", "contracts", "max_per_trade", "max_per_day",
                "tags", "live", "enabled"):  # fmt: skip
        assert key not in data["bot"]


def test_import_preview_then_save(app):
    bot_id = saved_bot(app, tags="Mine")
    text = app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True)
    client = app.test_client()
    html = client.post(
        "/automation/import/preview",
        data={"file": (io.BytesIO(text.encode()), "spx-ic.json")},
        headers=ORIGIN,
        content_type="multipart/form-data",
    ).get_data(as_text=True)
    assert "SPX IC (imported)" in html and "Your choices" in html and "Import" in html
    assert "+ New tag" in html  # #108: the importer can make one too
    with sf(app)() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    data = {"json": text, "name": "Friend's IC", "tag_id": str(sid),
            "account": "tastytrade:5WX01234", "contracts": "1", "max_per_trade": "1",
            "max_per_day": "1"}  # fmt: skip
    response = client.post("/automation/import/save", data=data, headers=ORIGIN)
    assert response.status_code == 302
    with sf(app)() as session:
        new = session.scalars(select(Bot).order_by(Bot.id.desc())).first()
    assert new.name == "Friend's IC" and new.live and not new.enabled
    assert new.tags == ["0DTE"] and new.imported_at is not None  # #278: its main tag
    html = client.get(f"/automation/{new.id}").get_data(as_text=True)
    assert "Imported from a bot file" in html


def test_an_import_without_an_account_saves_nothing(app):
    bot_id = saved_bot(app)
    text = app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True)
    data = {"json": text, "name": "x", "tag_id": "", "account": "", "contracts": "two",
            "max_per_trade": "1", "max_per_day": "1"}  # fmt: skip
    response = app.test_client().post("/automation/import/save", data=data, headers=ORIGIN)
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Give the bot at least one tag" in html and "Pick the TastyTrade account" in html
    assert "Contracts per entry must be a whole number" in html
    with sf(app)() as session:
        assert len(session.scalars(select(Bot)).all()) == 1


def test_import_of_a_newer_version_says_update_the_app(app):
    bot_id = saved_bot(app)
    data = json.loads(app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True))
    data["version"] = 4
    response = app.test_client().post(
        "/automation/import/preview", data={"json": json.dumps(data)}, headers=ORIGIN
    )
    html = response.get_data(as_text=True)
    assert (
        response.status_code == 400 and "newer OptionsJourney" in html and "Update the app" in html
    )
    response = app.test_client().post(
        "/automation/import/preview", data={"json": "not a bot"}, headers=ORIGIN
    )
    assert response.status_code == 400 and "isn&#39;t JSON" in response.get_data(as_text=True)


def test_import_from_another_site_is_refused(app):
    response = app.test_client().post(
        "/automation/import/save", data={"json": "{}"}, headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403


def test_the_panels_show_findings_and_the_schedule_check(app, monkeypatch):
    from optionsjourney.services import bot_audit, bot_integrity

    bot_audit.reset()
    html = app.test_client().get("/automation").get_data(as_text=True)
    assert "Bots may not run" not in html
    finding = {"run_id": 7, "bot_id": 1, "bot": "SPX IC", "check": "side_unprotected",
               "summary": "Call side has no working stop. Check your broker.",
               "since": datetime(2026, 10, 13, 15, 0, tzinfo=UTC)}  # fmt: skip
    monkeypatch.setattr(bot_integrity, "open_findings", lambda factory: [finding])
    bot_audit.started("bot-clock", datetime(2020, 1, 1, tzinfo=UTC))
    try:
        html = app.test_client().get("/automation").get_data(as_text=True)
    finally:
        bot_audit.reset()
    assert "Needs a look" in html and "Call side has no working stop" in html
    assert "/automation/runs/7" in html
    assert "Bots may not run" in html
    assert "The bot clock has stopped since the app started: no bot will fire." in html


def test_an_upload_that_is_not_utf8_is_refused_with_a_message(app):
    bot_id = saved_bot(app)
    text = app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True)
    response = app.test_client().post(
        "/automation/import/preview",
        data={"file": (io.BytesIO(text.encode("utf-16")), "bot.json")},
        headers=ORIGIN,
        content_type="multipart/form-data",
    )
    assert response.status_code == 400 and "UTF-8" in response.get_data(as_text=True)


def _choices(app, text):
    with sf(app)() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    return {"json": text, "name": "Hostile", "tag_id": str(sid),
            "account": "tastytrade:5WX01234", "contracts": "1", "max_per_trade": "1",
            "max_per_day": "1"}  # fmt: skip


@pytest.mark.parametrize(
    "tamper, words",
    [
        (lambda b: b.update(underlying="/ESZ6"), "not a contract month"),
        (lambda b: b.update(underlying="X" * 200_000), "Enter a stock or ETF ticker"),
        (lambda b: b.update(legs=[], sides=[]), "1 to 4 legs in 1 to 4 groups"),
        (lambda b: b["entry_chase"].update(max_tries=999_999_999), "tries are 1 to 60"),
        (lambda b: b["entry_chase"].update(start_offset="500"), "start offset"),
        (lambda b: b["exit_chase"].update(step="0.000001"), "the step"),
        (lambda b: b["entry_chase"].update(exhaust_mode="forever"), "Unknown ending"),
    ],
)
def test_a_hostile_file_is_refused_and_saves_nothing(app, tamper, words):
    bot_id = saved_bot(app)
    data = json.loads(app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True))
    tamper(data["bot"])
    text = json.dumps(data)
    preview = (
        app.test_client()
        .post("/automation/import/preview", data={"json": text}, headers=ORIGIN)
        .get_data(as_text=True)
    )
    assert words in preview and "can't be imported as it is" in preview
    response = app.test_client().post("/automation/import/save", data=_choices(app, text),
                                      headers=ORIGIN)  # fmt: skip
    assert response.status_code == 400 and words in response.get_data(as_text=True)
    with sf(app)() as session:
        assert len(session.scalars(select(Bot)).all()) == 1


def test_the_import_preview_shows_both_chases(app):
    bot_id = saved_bot(app)
    text = app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True)
    html = (
        app.test_client()
        .post("/automation/import/preview", data={"json": text}, headers=ORIGIN)
        .get_data(as_text=True)
    )
    assert "Entry chase:" in html and "every 10 s, up to 6 tries" in html
    assert "Exit chase:" in html and "every 5 s, up to 6 tries" in html


def test_a_file_with_a_utf8_bom_imports(app):
    bot_id = saved_bot(app)
    text = app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True)
    response = app.test_client().post(
        "/automation/import/preview",
        data={"file": (io.BytesIO(text.encode("utf-8-sig")), "bot.json")},
        headers=ORIGIN,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200 and "Your choices" in response.get_data(as_text=True)
    response = app.test_client().post("/automation/import/save",
                                      data=_choices(app, "﻿" + text), headers=ORIGIN)  # fmt: skip
    assert response.status_code == 302


def test_a_bot_imports_from_a_link_and_its_page_shows_the_link(app, monkeypatch):
    """#137: a link to a bot file posted online is fetched, previewed, then saved with
    the link it came from."""
    from optionsjourney.services import bot_fetch

    bot_id = saved_bot(app)
    text = app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True)
    link = "https://example.com/bots/spx-ic.json"
    fetched = []
    monkeypatch.setattr(bot_fetch, "fetch", lambda url: fetched.append(url) or text)
    client = app.test_client()
    assert "Paste a link to a bot file" in client.get("/automation/import").get_data(as_text=True)
    html = client.post("/automation/import/preview", data={"url": link, "json": "ignored"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert fetched == [link] and "Your choices" in html and "SPX IC (imported)" in html
    assert f'name="source_url" value="{link}"' in html
    response = client.post(
        "/automation/import/save", data=_choices(app, text) | {"source_url": link}, headers=ORIGIN
    )
    assert response.status_code == 302 and fetched == [link]  # saving fetches nothing
    with sf(app)() as session:
        new = session.scalars(select(Bot).order_by(Bot.id.desc())).first()
    assert new.imported_from_url == link
    html = client.get(f"/automation/{new.id}").get_data(as_text=True)
    assert f'href="{link}"' in html and "Imported from a bot file" in html


def test_a_tracker2_bot_file_imports_with_what_changed_on_the_way(app):
    """#138: a Tracker2 bot file previews with a From another app note, then saves."""
    from tests.bots.test_tracker2_file import six_entry_ic

    data = six_entry_ic()
    data["bot"]["execution_configs"]["exit"]["exhaust_mode"] = "leave_alert"
    text = json.dumps(data)
    client = app.test_client()
    html = client.post(
        "/automation/import/preview",
        data={"file": (io.BytesIO(text.encode()), "t2.json")},
        headers=ORIGIN,
        content_type="multipart/form-data",
    ).get_data(as_text=True)
    assert "From another app" in html and "Leave working" in html
    assert "Tracker2" not in html  # its name stays off the screen
    assert "when its stop fills, Long Puts closes too" in html
    assert "Your choices" in html and "Six-entry IC" in html
    assert "can't be imported as it is" not in html
    response = client.post("/automation/import/save", data=_choices(app, text) | {
        "max_per_day": "6"}, headers=ORIGIN)  # fmt: skip
    assert response.status_code == 302
    with sf(app)() as session:
        new_id = session.scalars(select(Bot.id).order_by(Bot.id.desc())).first()
    bot = bot_store.load(sf(app), new_id)
    assert [g.close_on_stop for g in bot.groups] == [2, 3, None, None]
    assert len(bot.entry_times) == 6 and bot.exit_chase.exhaust_mode == "give_up"


def test_a_link_that_cant_be_imported_says_why(app, monkeypatch):
    from optionsjourney.services import bot_fetch

    def refused(url):
        raise bot_file.FileError("That link answered 404 (deleted, or mistyped?).")

    monkeypatch.setattr(bot_fetch, "fetch", refused)
    response = app.test_client().post("/automation/import/preview",
                                      data={"url": "https://example.com/gone.json"},
                                      headers=ORIGIN)  # fmt: skip
    html = response.get_data(as_text=True)
    assert response.status_code == 400 and "answered 404" in html
    assert 'value="https://example.com/gone.json"' in html  # kept, to fix and try again


def test_a_source_link_that_isnt_https_is_not_kept(app):
    bot_id = saved_bot(app)
    text = app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True)
    data = _choices(app, text) | {"source_url": "javascript:alert(1)"}
    app.test_client().post("/automation/import/save", data=data, headers=ORIGIN)
    with sf(app)() as session:
        new = session.scalars(select(Bot).order_by(Bot.id.desc())).first()
    assert new.imported_from_url is None


# Slice 6: managing a trade from the run page

LEGS = [
    {"symbol": f"SPXW  261013{r[0].upper()}0{k}000", "side": side, "ratio": 1, "group": g,
     "strike": k, "right": r, "mid": "1.00"}
    for k, r, side, g in ((5910, "call", "sell", 0), (5985, "call", "buy", 0),
                          (5690, "put", "sell", 1), (5615, "put", "buy", 1))
]  # fmt: skip


def _held_run(app, off_side=None):
    """A filled live run with a working stop and an armed target on each side."""
    from optionsjourney.db.models import BotTradeOverride

    bot_id = _saved_bot_id(app)
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    with sf(app)() as session:
        run = BotRun(
            bot_id=bot_id, key="bot-1-0947-20261013", trading_date=now.date(), entry_time="09:47",
            status="filled", live=True, reason="", legs=LEGS, chase_log=[], contracts=2,
            filled_legs=[{"symbol": leg["symbol"], "fill_price": "1.00"} for leg in LEGS],
            created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        for side in (0, 1):
            short, long = LEGS[2 * side]["symbol"], LEGS[2 * side + 1]["symbol"]
            off = side == off_side
            for kind, state, value in (("stop", "working", "4.80"), ("target", "armed", "0.50")):
                session.add(BotExit(
                    run_id=run.id, group_position=side, kind=kind,
                    state="cancelled" if off else state, short_symbol=short, long_symbol=long,
                    contracts=2, broker_order_id="900" if kind == "stop" else None,
                    trigger=Decimal(value) if kind == "stop" else None,
                    limit=Decimal(value) if kind == "target" else None, rearms=0, log=[],
                    created_at=now, updated_at=now,
                ))  # fmt: skip
        if off_side is not None:
            session.add(BotTradeOverride(run_id=run.id, group_position=off_side, exits_off=True,
                                         changed_at=now, log=[]))  # fmt: skip
        session.commit()
        return run.id


def test_the_run_page_shows_manage_this_trade_for_each_open_side(app):
    run_id = _held_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Manage this trade" in html
    for side in (0, 1):
        for action in ("stop", "target", "close", "exits-off"):
            assert f"/automation/runs/{run_id}/sides/{side}/{action}" in html
    assert "Call side" in html and "Put side" in html and "4.80" in html
    assert 'max="2"' in html  # the close's quantity, up to what's open


def test_a_one_leg_three_lot_closes_by_the_contract(app):
    # #209: one bot contract of a short put x3 is 3 units of one contract each, so Close
    # asks for 1 to 3 contracts; an iron condor's side still counts its units.
    bot_id = _saved_bot_id(app)
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    leg = {**LEGS[2], "ratio": 3, "group": 0}
    with sf(app)() as session:
        run = BotRun(
            bot_id=bot_id, key="bot-1-1000-20261013", trading_date=now.date(), entry_time="10:00",
            status="filled", live=True, reason="", legs=[leg], chase_log=[], contracts=1,
            filled_legs=[{"symbol": leg["symbol"], "fill_price": "1.00"}],
            created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        session.add(BotExit(
            run_id=run.id, group_position=0, kind="stop", state="working",
            short_symbol=leg["symbol"], long_symbol="", contracts=3, broker_order_id="900",
            trigger=Decimal("2.00"), rearms=0, log=[], created_at=now, updated_at=now,
        ))  # fmt: skip
        session.commit()
        run_id = run.id
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert 'max="3"' in html and "of 3 contracts" in html
    condor = _held_run(app)
    html = app.test_client().get(f"/automation/runs/{condor}").get_data(as_text=True)
    assert 'max="2"' in html and "of 2 contracts" not in html


def test_the_run_page_shows_the_red_exits_off_box_with_turn_back_on(app):
    run_id = _held_run(app, off_side=1)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "notice--danger" in html and "Exits are off" in html
    assert f"/automation/runs/{run_id}/sides/1/exits-on" in html
    assert f"/automation/runs/{run_id}/sides/1/exits-off" not in html
    assert f"/automation/runs/{run_id}/sides/1/stop" not in html
    assert "Exits off: not protected" in html  # the chip
    assert f"/automation/runs/{run_id}/sides/0/exits-off" in html


def test_each_manage_route_flashes_its_outcome(app, monkeypatch):
    from optionsjourney.services import bot_manage

    run_id = _held_run(app)
    calls = []

    def fake(name, ok=True):
        def run(deps, run_id_, side, *args):
            calls.append((name, run_id_, side, *args))
            return bot_manage.Outcome(ok, f"{name} done")

        return run

    for name in ("change_stop", "change_target", "close_part", "exits_off", "exits_on"):
        monkeypatch.setattr(bot_manage, name, fake(name, ok=name != "close_part"))
    client = app.test_client()
    for action, data in (("stop", {"pct": "200"}), ("target", {"pct": "25"}),
                         ("close", {"quantity": "1"}), ("exits-off", {}),
                         ("exits-on", {"pct": ""})):  # fmt: skip
        url = f"/automation/runs/{run_id}/sides/1/{action}"
        response = client.post(url, data=data, headers=ORIGIN)
        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"/automation/runs/{run_id}#manage")
    assert calls == [
        ("change_stop", run_id, 1, "200", ""),
        ("change_target", run_id, 1, "25"),
        ("close_part", run_id, 1, "1"),
        ("exits_off", run_id, 1),
        ("exits_on", run_id, 1, ""),
    ]
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "notice--success" in html and "notice--warning" in html and "close_part done" in html
    assert client.post(f"/automation/runs/{run_id}/sides/1/nope", headers=ORIGIN).status_code == 404


def test_a_manage_action_that_breaks_says_check_your_broker(app, monkeypatch):
    from optionsjourney.services import bot_manage

    run_id = _held_run(app)

    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_manage, "exits_off", broken)
    client = app.test_client()
    client.post(f"/automation/runs/{run_id}/sides/0/exits-off", headers=ORIGIN)
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "check your broker" in html


def test_manage_routes_refuse_another_site(app, monkeypatch):
    from optionsjourney.services import bot_manage

    run_id = _held_run(app)
    monkeypatch.setattr(bot_manage, "exits_off", lambda *a: pytest.fail("must not run"))
    client = app.test_client()
    url = f"/automation/runs/{run_id}/sides/0/exits-off"
    assert client.post(url).status_code == 403
    assert client.post(url, headers={"Origin": "https://evil.example"}).status_code == 403


def test_a_held_back_side_shows_the_recheck_button(app, monkeypatch):
    from optionsjourney.services import bot_exits, bot_manage

    run_id = _held_run(app)
    with sf(app)() as session:
        stop = session.scalar(
            select(BotExit).where(BotExit.run_id == run_id, BotExit.kind == "stop",
                                  BotExit.group_position == 0)
        )  # fmt: skip
        stop.state, stop.log = "cancelled", [{"event": bot_exits.CANCELLED_FOR_TARGET}]
        session.commit()
    client = app.test_client()
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "may still be working at your broker" in html
    url = f"/automation/runs/{run_id}/sides/0/recheck"
    assert url in html and f"/automation/runs/{run_id}/sides/0/close" not in html
    calls = []

    def recheck(deps, r, s):
        calls.append((r, s))
        return bot_manage.Outcome(True, "ok")

    monkeypatch.setattr(bot_manage, "recheck", recheck)
    assert client.post(url, headers=ORIGIN).status_code == 302 and calls == [(run_id, 0)]


# --- Slice 7: watch rules and profit actions ------------------------------------------

WATCH = {
    "use_sl_ratio_exit": "on", "sl_ratio_below": "0.78", "sl_ratio_above": "",
    "use_delta_exit": "", "position_delta_below": "", "position_delta_above": "30",
    "use_underlying_exit": "on", "underlying_up_pct": "", "underlying_down_pct": "1.5",
    "short_put_tested_pts": "-5", "short_call_tested_pts": "",
    "group-1-target": "80", "group-1-use-profit": "on", "group-1-profits": "3",
    "pa-1-0-at": "25", "pa-1-0-close": "33", "pa-1-0-stop": "0",
    "pa-1-1-at": "50", "pa-1-1-close": "0", "pa-1-1-stop": "-20",
    "pa-1-2-at": "", "pa-1-2-close": "", "pa-1-2-stop": "",
    "use_time_actions": "on", "actions": "1", "ta-0-type": "dte", "ta-0-days": "0",
    "ta-0-at": "14:00", "ta-0-stop": "", "ta-0-target": "", "ta-0-close": "50",
}  # fmt: skip


def test_the_bot_page_shows_watch_rules_profit_actions_and_a_close_column(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert "Watch exits" in html and "Use profit actions" in html
    for name in ("use_delta_exit", "position_delta_below", "position_delta_above",
                 "use_sl_ratio_exit", "sl_ratio_below", "sl_ratio_above", "use_underlying_exit",
                 "underlying_up_pct", "underlying_down_pct", "short_put_tested_pts",
                 "short_call_tested_pts", "group-0-use-profit", "group-0-profits",
                 "pa-0-0-at", "pa-0-0-close", "pa-0-0-stop", "pa-1-0-at",
                 "ta-0-close"):  # fmt: skip
        assert f'name="{name}"' in html, name


def test_watch_rules_profit_actions_and_a_close_round_trip_through_the_page(app):
    from optionsjourney.bots.definition import ProfitActionDef

    client = app.test_client()
    response = client.post("/automation/save", data=form(app, **WATCH), headers=ORIGIN)
    assert response.status_code == 302, response.get_data(as_text=True)
    bot = bot_store.load(sf(app), saved_id(app))
    assert (bot.use_sl_ratio_exit, bot.sl_ratio_below) == (True, Decimal("0.78"))
    assert (bot.use_delta_exit, bot.position_delta_above) == (False, Decimal("30"))
    assert (bot.underlying_down_pct, bot.short_put_tested_pts) == (Decimal("1.5"), Decimal("-5"))
    put = bot.groups[1]
    assert put.use_profit_actions and put.profit_actions == (
        ProfitActionDef(Decimal("25"), 33, Decimal("0")),
        ProfitActionDef(Decimal("50"), 0, Decimal("-20")),
    )
    assert bot.time_actions[0].close_pct == 50
    html = client.get(f"/automation/{bot.id}").get_data(as_text=True)
    again = client.post("/automation/save", data=posted_back(html), headers=ORIGIN)
    assert again.status_code == 302
    assert bot_store.load(sf(app), bot.id) == bot


def test_a_bad_profit_action_is_shown_beside_its_side(app):
    data = form(app, **(WATCH | {"pa-1-1-stop": "10"}))  # looser than the first's
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 400
    assert "looser than an earlier" in response.get_data(as_text=True)


def test_the_run_page_shows_watch_and_profit_rows_and_labels_a_rules_close(app):
    run_id, target_id = _closing_target(app)
    now = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)
    symbols = {"short_symbol": "SPXW  261013C05910000", "long_symbol": "SPXW  261013C05985000"}
    with sf(app)() as session:
        session.get(BotExit, target_id).state = "cancelled"
        session.add_all([
            BotExit(run_id=run_id, group_position=0, kind="watch", state="filled", contracts=1,
                    rearms=0, log=[{"rule": "S/L ratio exit",
                                    "detail": "S/L ratio 0.770 < 0.78",
                                    "notes": ["closing 1 (close 9)"]}],
                    created_at=now, updated_at=now, **symbols),
            BotExit(run_id=run_id, group_position=0, kind="close", state="filled", contracts=1,
                    rearms=0, log=[{"event": "a close of 1 for the S/L ratio exit",
                                    "by": "S/L ratio exit"}],
                    created_at=now, updated_at=now, **symbols),
            BotExit(run_id=run_id, group_position=1, kind="profit", state="filled", contracts=1,
                    rearms=0, log=[{"number": 1, "rule": "profit action 1: at 50%: stop to 0%",
                                    "detail": "Put side profit action 1: P/L 1.90 ≥ 1.22",
                                    "notes": ["stop 4.80 → 2.45"]}],
                    created_at=now, updated_at=now, **symbols),
        ])  # fmt: skip
        session.commit()
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "watch rule" in html and "close (S/L ratio exit)" in html and "profit action" in html
    assert "S/L ratio 0.770 &lt; 0.78" in html or "S/L ratio 0.770 < 0.78" in html
    assert "stop 4.80 → 2.45" in html and "profit action 1: at 50%: stop to 0%" in html


def test_the_page_says_the_delta_rule_needs_tastytrades_stream(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert "Delta needs TastyTrade&#39;s quote stream" in html


# --- Slice 9: alerts --------------------------------------------------------------------

ALERTS = {
    "alert_name": "IC Joint", "alert_channel": "telegram", "use_default_alerts": "on",
    "default_alerts_in_app_only": "on", "alerts": "3",
    "da-0-type": "pnl_pct", "da-0-direction": "below", "da-0-value": "-50", "da-0-points": "",
    "da-1-type": "strike_offset", "da-1-direction": "above", "da-1-value": "",
    "da-1-points": "10",
    "da-2-type": "pnl_pct", "da-2-direction": "above", "da-2-value": "", "da-2-points": "",
}  # fmt: skip


def test_the_bot_page_shows_the_alerts_section(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert "Alerts" in html and "always go to every channel" in html
    for name in ("alert_name", "alert_channel", "use_default_alerts",
                 "default_alerts_in_app_only", "alerts", "da-0-type", "da-0-direction",
                 "da-0-value", "da-0-points"):  # fmt: skip
        assert f'name="{name}"' in html, name
    for label in ("Every channel", "Discord only", "Telegram only"):
        assert label in html


def test_alert_settings_round_trip_through_the_page(app):
    from optionsjourney.bots.definition import DefaultAlertDef

    client = app.test_client()
    response = client.post("/automation/save", data=form(app, **ALERTS), headers=ORIGIN)
    assert response.status_code == 302, response.get_data(as_text=True)
    bot = bot_store.load(sf(app), saved_id(app))
    assert (bot.alert_name, bot.alert_channel) == ("IC Joint", "telegram")
    assert bot.use_default_alerts and bot.default_alerts_in_app_only
    assert bot.default_alerts == (
        DefaultAlertDef("pnl_pct", "below", Decimal("-50")),
        DefaultAlertDef("strike_offset", points=Decimal("10")),
    )
    html = client.get(f"/automation/{bot.id}").get_data(as_text=True)
    assert html.count("data-alert-row>") == 2  # the saved ones, no extra blank row
    again = client.post("/automation/save", data=posted_back(html), headers=ORIGIN)
    assert again.status_code == 302
    assert bot_store.load(sf(app), bot.id) == bot


def test_a_bad_default_alert_is_shown(app):
    data = form(app, **(ALERTS | {"da-0-value": "0"}))
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 400
    assert "non-zero" in response.get_data(as_text=True)


# Slice 10: delta and spot picks, the floating window


def market_form(app, **changes):
    return form(app, **{"leg-0-pick": "delta", "leg-0-target": "16", "leg-2-pick": "spot",
                        "leg-2-target": "-50", **changes})  # fmt: skip


def test_the_form_offers_delta_and_spot_and_shows_a_delta_back_in_points(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert 'value="delta"' in html and 'value="spot"' in html
    response = app.test_client().post("/automation/save", data=market_form(app), headers=ORIGIN)
    assert response.status_code == 302
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    bot = bot_store.load(sf(app), bot_id)
    assert (bot.legs[0].pick, bot.legs[0].target) == ("delta", Decimal("0.16"))
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="leg-0-target" inputmode="decimal" value="16"' in html
    assert 'name="leg-2-target" inputmode="decimal" value="-50"' in html


def test_a_short_spot_leg_pointing_into_the_money_is_refused(app):
    data = market_form(app, **{"leg-2-target": "50"})
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 400
    assert "short put" in response.get_data(as_text=True)


def test_the_floating_window_saves_and_shows_its_last_check(app, monkeypatch):
    from optionsjourney.services import bot_entry

    data = form(app, use_floating_entry="on", floating_start="11:30", floating_end="11:45")
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 302
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    last = ("11:32", "VIX level filter: VIX 21.3; above max 20")
    monkeypatch.setattr(bot_entry, "floating_last", lambda b, day: last)
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="use_floating_entry" checked' in html
    assert 'value="11:30"' in html and 'value="11:45"' in html
    assert "Last check 11:32: VIX level filter: VIX 21.3; above max 20" in html
    bad = form(app, use_floating_entry="on", floating_start="11:45", floating_end="11:30")
    response = app.test_client().post("/automation/save", data=bad, headers=ORIGIN)
    assert response.status_code == 400 and "starts before it ends" in response.get_data(
        as_text=True
    )


def test_the_run_page_shows_a_legs_delta(app):
    app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
        now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
        legs = [{"position": 0, "symbol": "SPXW  261013P05630000", "strike": "5630",
                 "right": "put", "side": "sell", "ratio": 1, "group": 1, "mid": "2.10",
                 "delta": "-0.16"}]  # fmt: skip
        run = BotRun(
            bot_id=bot_id, key="bot-1-0947-20261013", trading_date=now.date(), entry_time="09:47",
            status="preview", live=False, legs=legs, filled_legs=[], chase_log=[], contracts=0,
            created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.commit()
        run_id = run.id
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "<th>Delta</th>" in html and "<td>-0.16</td>" in html


def test_import_preview_words_the_new_picks_and_the_window(app):
    data = market_form(app, use_floating_entry="on", floating_start="11:30", floating_end="11:45")
    app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    text = app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True)
    html = (
        app.test_client()
        .post("/automation/import/preview", data={"json": text}, headers=ORIGIN)
        .get_data(as_text=True)
    )
    assert "Sell call at about 16 delta" in html
    assert "Sell put 50 points below SPX" in html
    assert "Entry window (New York):</strong> 11:30–11:45" in html


# Bots slice 11: Schwab bots


@pytest.fixture
def schwab_app(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    with sf(app)() as session:
        session.add(SyncState(broker="tastytrade", account_number="5WX01234", last_attempt_at=now))
        session.add(SyncState(broker="schwab", account_number="12345678", last_attempt_at=now))
        session.commit()
    return app


def _schwab_bot(app):
    data = form(app, account="schwab:12345678")
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 302, response.get_data(as_text=True)
    with sf(app)() as session:
        return session.scalar(select(Bot.id).where(Bot.broker == "schwab"))


def test_schwab_accounts_are_offered_like_tastytrade_ones(schwab_app):
    """Decision 0059: no switch for Schwab."""
    page = schwab_app.test_client().get("/automation/new").get_data(as_text=True)
    assert 'value="schwab:12345678"' in page and "Schwab ••5678" in page
    assert 'value="tastytrade:5WX01234"' in page
    assert _schwab_bot(schwab_app)


def test_a_schwab_bots_page_says_its_stops_live_in_the_app(schwab_app):
    bot_id = _schwab_bot(schwab_app)
    html = schwab_app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "watched by this app, not held at Schwab" in html
    assert "on and awake" in html
    assert 'name="understood"' in html


def test_arming_a_schwab_bot_needs_the_box_ticked(schwab_app):
    bot_id = _schwab_bot(schwab_app)
    client = schwab_app.test_client()
    url = f"/automation/{bot_id}/arm"
    client.post(url, data={"on": "1", "next": "list"}, headers=ORIGIN)
    with sf(schwab_app)() as session:
        assert not session.get(Bot, bot_id).enabled
    page = client.get(f"/automation/{bot_id}/live?arm=1").get_data(as_text=True)
    assert 'name="understood"' in page and "watched by this app" in page
    r = client.post(url, data={"on": "1", "understood": "1"}, headers=ORIGIN)
    assert "/live?arm=1" in r.headers["Location"]  # the tick box alone can't skip the summary
    with sf(schwab_app)() as session:
        assert not session.get(Bot, bot_id).enabled
    client.post(url, data={"on": "1", "understood": "1", "confirmed": "1"}, headers=ORIGIN)
    with sf(schwab_app)() as session:
        assert session.get(Bot, bot_id).enabled
    client.post(url, data={"on": "0"}, headers=ORIGIN)  # turning it off needs nothing
    with sf(schwab_app)() as session:
        assert not session.get(Bot, bot_id).enabled


def test_the_list_sends_a_schwab_bot_to_its_page_to_arm(schwab_app):
    bot_id = _schwab_bot(schwab_app)
    html = schwab_app.test_client().get("/automation").get_data(as_text=True)
    assert f'href="/automation/{bot_id}#arm"' in html and "Schwab ••5678" in html


def test_the_live_page_says_where_a_schwab_bots_stops_are(schwab_app):
    bot_id = _schwab_bot(schwab_app)
    html = schwab_app.test_client().get(f"/automation/{bot_id}/live").get_data(as_text=True)
    assert "Schwab account ••5678" in html and "watched by this app" in html
    assert "stops at your broker" not in html


def test_the_run_page_says_a_stop_is_watched_by_the_app(schwab_app):
    bot_id = _schwab_bot(schwab_app)
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    with sf(schwab_app)() as session:
        run = BotRun(
            bot_id=bot_id, key="bot-1-0947-20261013", trading_date=now.date(), entry_time="09:47",
            status="filled", live=True, reason="", legs=[], filled_legs=[], chase_log=[],
            contracts=1, broker="schwab", created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        session.add(BotExit(
            run_id=run.id, group_position=0, kind="stop", state="watching",
            short_symbol="SPXW  261013C05910000", long_symbol="SPXW  261013C05985000",
            contracts=1, trigger=Decimal("4.80"), rearms=0, log=[], created_at=now,
            updated_at=now,
        ))  # fmt: skip
        session.commit()
        run_id = run.id
    html = schwab_app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "watched by the app" in html


def test_moving_an_armed_live_bot_to_schwab_disarms_it_and_says_why(schwab_app):
    client = schwab_app.test_client()
    client.post("/automation/save", data=form(schwab_app), headers=ORIGIN)
    with sf(schwab_app)() as session:
        bot_id = session.scalar(select(Bot.id))
    client.post(f"/automation/{bot_id}/live", data=LIVE_TICKED, headers=ORIGIN)
    client.post(
        f"/automation/{bot_id}/arm",
        data={"on": "1", "understood": "1", "confirmed": "1"},
        headers=ORIGIN,
    )
    data = form(schwab_app, id=str(bot_id), account="schwab:12345678")
    client.post("/automation/save", data=data, headers=ORIGIN)
    with sf(schwab_app)() as session:
        bot = session.get(Bot, bot_id)
        assert bot.broker == "schwab" and not bot.enabled and not bot.live
    page = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "switched off and back to Paper" in page


def test_making_a_schwab_bot_live_needs_the_box_ticked(schwab_app):
    bot_id = _schwab_bot(schwab_app)
    bot_store.set_mode(sf(schwab_app), bot_id, "paper", datetime.now(UTC))
    client = schwab_app.test_client()
    page = client.get(f"/automation/{bot_id}/live").get_data(as_text=True)
    assert 'name="understood"' in page
    client.post(f"/automation/{bot_id}/live", data={"on": "1", "risk": "1"}, headers=ORIGIN)
    with sf(schwab_app)() as session:
        assert not session.get(Bot, bot_id).live
    client.post(f"/automation/{bot_id}/live", data=LIVE_TICKED, headers=ORIGIN)
    with sf(schwab_app)() as session:
        assert session.get(Bot, bot_id).live


def test_preview_now_on_a_live_schwab_bot_needs_the_box_ticked(schwab_app, monkeypatch):
    from optionsjourney.services import bot_entry

    fired = []
    monkeypatch.setattr(
        bot_entry, "preview_order",
        lambda *a, **k: fired.append(a)
        or bot_entry.PreviewResult(False, "x", [], None, None, None),
    )  # fmt: skip
    bot_id = _schwab_bot(schwab_app)
    client = schwab_app.test_client()
    client.post(f"/automation/{bot_id}/live", data=LIVE_TICKED, headers=ORIGIN)
    client.post(f"/automation/{bot_id}/preview", headers=ORIGIN)
    assert fired == []
    listed = client.get("/automation").get_data(as_text=True)
    assert f'href="/automation/{bot_id}#preview"' in listed
    client.post(f"/automation/{bot_id}/preview", data={"understood": "1"}, headers=ORIGIN)
    assert len(fired) == 1


def test_the_form_says_nothing_special_about_a_schwab_account(schwab_app, app):
    """Decision 0060: a Schwab bot's stops rest as a TastyTrade bot's do."""
    on = schwab_app.test_client().get("/automation/new").get_data(as_text=True)
    assert "If you pick a Schwab account" not in on
    assert "Schwab bots rest nothing" not in on


def test_a_schwab_bot_that_rests_every_stop_has_no_watched_warning(schwab_app):
    """Decision 0060: the warning and the tick box only when some stop is the app's."""
    client = schwab_app.test_client()
    data = strangle_form(schwab_app, account="schwab:12345678")
    response = client.post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 302, response.get_data(as_text=True)
    with sf(schwab_app)() as session:
        bot_id = session.scalar(select(Bot.id).where(Bot.broker == "schwab"))
    html = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "watched by this app" not in html
    assert 'name="understood"' not in html
    assert 'name="group-0-rest-stop" checked' in html
    client.post(
        f"/automation/{bot_id}/arm",
        data={"on": "1", "understood": "1", "confirmed": "1"},
        headers=ORIGIN,
    )
    with sf(schwab_app)() as session:
        assert session.get(Bot, bot_id).enabled  # a Live bot's arm step still asks


# Slice 13: the structure picker and editing any shape


def _options(html, name):
    """(value, label, selected) for each option of the select named `name`."""
    import re
    from html import unescape

    start = html.index(f'<select name="{name}"')
    block = html[start : html.index("</select>", start)]
    found = re.findall(r'<option value="([^"]*)"( selected)?[^>]*>([^<]*)</option>', block)
    return [(v, unescape(label).strip(), bool(sel)) for v, sel, label in found]


def _shape_form(app, key, **changes):
    """The new-bot form filled from a shape, as a browser would post it, with what a
    person would type."""
    html = app.test_client().get(f"/automation/new?shape={key}").get_data(as_text=True)
    data = posted_back(html)
    with sf(app)() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    typed = {"name": f"My {key}", "tag_id": str(sid), "account": "tastytrade:5WX01234",
             "time-0": "09:47", "time-0-on": "on"}  # fmt: skip
    for i in range(int(data["leg_count"])):
        if data[f"leg-{i}-pick"] == "premium":
            typed[f"leg-{i}-target"] = "2.00"
        elif data[f"leg-{i}-pick"] == "offset":
            typed[f"leg-{i}-target"] = "50" if data[f"leg-{i}-right"] == "call" else "-50"
    for key_, value in (typed | changes).items():
        data[key_] = value
    return data


def _saved_shape(app, key, **changes):
    r = app.test_client().post("/automation/save", data=_shape_form(app, key, **changes),
                               headers=ORIGIN)  # fmt: skip
    assert r.status_code == 302, r.get_data(as_text=True)
    with sf(app)() as session:
        return session.scalar(select(Bot.id).where(Bot.name == f"My {key}"))


def test_the_new_bot_page_offers_the_start_from_picker_and_an_existing_bot_doesnt(app):
    from optionsjourney.bots.definition import SHAPES

    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert "Start from" in html and "Use this shape" in html
    offered = _options(html, "shape")
    assert [v for v, _, _ in offered] == list(SHAPES)
    assert [label for _, label, _ in offered] == [s.label for s in SHAPES.values()]
    assert html.index('name="shape"') < html.index('class="bot-form"')  # its own form
    bot_id = _saved_bot_id(app)
    page = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="shape"' not in page and "Use this shape" not in page


def test_use_this_shape_fills_the_form(app):
    html = app.test_client().get("/automation/new?shape=long_call").get_data(as_text=True)
    data = posted_back(html)
    assert data["leg_count"] == "1" and data["group_count"] == "1"
    assert data["leg-0-side"] == "buy" and data["leg-0-right"] == "call"
    assert "leg-1-side" not in data and "group-1-name" not in data
    assert data["group-0-name"] == "Long call"
    assert data["group-0-rests"] == "target"
    assert ("long_call", "Long call", True) in _options(html, "shape")
    unknown = app.test_client().get("/automation/new?shape=nope").get_data(as_text=True)
    assert posted_back(unknown)["leg_count"] == "4"  # an iron condor


def test_add_and_remove_redraw_the_form_with_what_was_typed_and_save_nothing(app):
    client = app.test_client()
    full = form(app, name="Typed name", **{"leg_count": "4", "group_count": "2"})

    def redraw(**changes):
        r = client.post("/automation/save", data=full | changes, headers=ORIGIN)
        assert r.status_code == 200
        return r.get_data(as_text=True)

    html = redraw(op="add-leg")
    assert "Typed name" in html and 'value="2.70"' in html
    assert posted_back(html)["leg_count"] == "4" and 'name="leg-4-side"' not in html
    assert 'value="add-leg"' not in html  # a 5th leg is never offered
    html = redraw(op="remove-leg-3")
    data = posted_back(html)
    assert data["leg_count"] == "3" and 'name="leg-3-side"' not in html
    assert 'value="add-leg"' in html and 'value="remove-leg-2"' in html
    html = redraw(op="add-group", name="")
    assert posted_back(html)["group_count"] == "3" and 'value="Group 3"' in html
    assert "Give the bot a name" not in html  # no problems shown while editing
    html = redraw(op="remove-group-0")
    data = posted_back(html)
    assert data["group_count"] == "1" and data["group-0-name"] == "Put side"
    assert {data[f"leg-{i}-group"] for i in range(4)} == {"0"}
    assert 'value="remove-group-0"' not in html  # the last group stays
    one = _shape_form(app, "long_call")
    html = client.post("/automation/save", data=one | {"op": "add-leg"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert posted_back(html)["leg_count"] == "2" and 'value="remove-leg-0"' in html
    html = client.post("/automation/save", data=one | {"op": "remove-leg-0"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert posted_back(html)["leg_count"] == "1"
    assert 'value="remove-leg-0"' not in html  # the last leg has no remove button
    with sf(app)() as session:
        assert session.scalar(select(Bot)) is None  # nothing was saved
    # The page's first submit button is Save, so Enter in a box saves and never edits.
    page = app.test_client().get("/automation/new").get_data(as_text=True)
    form_html = page[page.index('class="bot-form"') :]
    first = form_html[form_html.index("<button") : form_html.index("</button>")]
    assert 'name="op"' not in first and "Save" in first


def test_each_group_says_where_its_stop_rests_and_offers_only_its_styles(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    # decision 0054: an iron condor's sides are spreads, measured on the short, watched
    assert html.count('data-exit-show="watched-note">Stop watched by the app') == 2
    # #124: Tracker2's unit, then the basis of a % stop
    assert [v for v, _, _ in _options(html, "group-0-stop-unit")] == ["pct", "usd"]
    assert [v for v, _, _ in _options(html, "group-0-stop-basis")] == [
        "leg_premium", "entry_premium", "closing_order", "group_entry",
    ]  # fmt: skip
    html = app.test_client().get("/automation/new?shape=long_call").get_data(as_text=True)
    # #126: a bought leg offers no resting stop
    assert "Only a sold leg&#39;s stop can rest at the broker" in html
    assert 'name="group-0-rest-stop"' not in html
    assert _options(html, "group-0-stop-basis") == [("group_entry", "group premium", True)]
    # Help text with a credit group and a debit group, never "the wing" for one with none
    assert "took in 1.20" in html and "bought for 2.00" in html
    assert "the wing" not in html


def test_entry_price_lost_on_a_credit_spread_says_how_its_measured(app):
    """Final review I3: on a group with one sold leg, "% of entry price lost" is measured
    on the short with the bought leg counted as worthless: the page says so."""
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    labels = dict((v, label) for v, label, _ in _options(html, "group-0-stop-basis"))
    assert labels["group_entry"] == "group premium"  # #124: the tip says how it's measured
    assert "bought leg counted as worthless" in html
    assert "a group with one sold leg (a credit spread)" in html
    assert "fires early or may be refused" in html


def test_split_iron_condors_shows_only_for_an_iron_condor(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert 'name="split"' in html and "Split iron condors" in html
    fly = app.test_client().get("/automation/new?shape=iron_butterfly").get_data(as_text=True)
    assert 'name="split"' not in fly and "Split iron condors" not in fly


def test_arming_a_bot_with_a_watched_group_needs_the_box(app):
    bot_id = _saved_shape(app, "long_call", **{"group-0-stop": "50"})
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    client = app.test_client()
    html = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="understood"' in html
    assert "watched by this app, not held at TastyTrade" in html and "on and awake" in html
    client.post(f"/automation/{bot_id}/arm", data={"on": "1"}, headers=ORIGIN)
    assert not bot_store.load(sf(app), bot_id).enabled
    assert "Tick the box" in client.get(f"/automation/{bot_id}").get_data(as_text=True)
    client.post(
        f"/automation/{bot_id}/arm",
        data={"on": "1", "understood": "1", "confirmed": "1"},
        headers=ORIGIN,
    )
    assert bot_store.load(sf(app), bot_id).enabled
    live = client.get(f"/automation/{bot_id}/live").get_data(as_text=True)
    assert 'name="understood"' in live and "Long call" in live
    client.post(f"/automation/{bot_id}/live", data={"on": "1", "risk": "1"}, headers=ORIGIN)
    assert not bot_store.load(sf(app), bot_id).live
    # A TastyTrade iron condor rests its stops at TastyTrade: no box.
    resting = _saved_bot_id(app)
    assert 'name="understood"' not in client.get(f"/automation/{resting}").get_data(as_text=True)


def test_the_limits_say_contracts_in_the_largest_leg(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert "largest leg" in html
    fly = app.test_client().get("/automation/new?shape=long_call_butterfly").get_data(as_text=True)
    data = posted_back(fly)
    assert data["max_per_trade"] == "" and data["max_per_day"] == ""  # 0058: worked out
    assert 'placeholder="Auto: 2"' in fly
    assert data["leg-0-ratio"] == "2"


def test_a_bot_saves_with_blank_limits_and_says_what_they_work_out_to(app):
    """#139: blank limits save (stored 0), redraw blank, and the go-live page names the
    worked-out numbers."""
    bot_id = saved_bot(app, max_per_trade="", max_per_day="", contracts="2")
    assert bot_id is not None
    bot = bot_store.load(sf(app), bot_id)
    assert (bot.max_per_trade, bot.max_per_day) == (None, None)
    with sf(app)() as session:
        row = session.get(Bot, bot_id)
        assert (row.max_per_trade, row.max_per_day) == (0, 0)
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="max_per_trade" min="1" placeholder="Auto: 2" value=""' in html
    assert 'name="max_per_day" min="1" placeholder="Auto: 2" value=""' in html
    live = app.test_client().get(f"/automation/{bot_id}/live").get_data(as_text=True)
    assert "up to 2 per trade and 2 a day (Auto, from" in live


def test_an_import_with_blank_limits_saves_them_blank(app):
    bot_id = saved_bot(app)
    text = app.test_client().get(f"/automation/{bot_id}/export").get_data(as_text=True)
    html = app.test_client().post("/automation/import/preview", data={"json": text},
                                  headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert 'name="max_per_trade" min="1" placeholder="Auto" value=""' in html
    data = _choices(app, text) | {"max_per_trade": "", "max_per_day": ""}
    response = app.test_client().post("/automation/import/save", data=data, headers=ORIGIN)
    assert response.status_code == 302
    with sf(app)() as session:
        new_id = session.scalars(select(Bot.id).order_by(Bot.id.desc())).first()
    assert (bot_store.load(sf(app), new_id).max_per_trade, bot_store.load(sf(app), new_id)
            .max_per_day) == (None, None)  # fmt: skip


def test_a_limit_of_0_is_refused_with_blank_offered(app):
    html = app.test_client().post("/automation/save", data=form(app, max_per_trade="0"),
                                  headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert "or blank for Auto" in html


def test_the_run_page_names_groups_and_watched_stops(app):
    bot_id = _saved_shape(app, "long_call", **{"group-0-stop": "50"})
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    symbol = "SPXW  261013C05900000"
    legs = [{"symbol": symbol, "side": "buy", "ratio": 1, "group": 0, "strike": 5900,
             "right": "call", "mid": "2.00"}]  # fmt: skip
    with sf(app)() as session:
        run = BotRun(
            bot_id=bot_id, key="bot-1-0947-20261013", trading_date=now.date(), entry_time="09:47",
            status="filled", live=True, reason="", legs=legs, chase_log=[], contracts=1,
            filled_legs=[{"symbol": symbol, "fill_price": "2.00"}], created_at=now,
            updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        session.add(BotExit(
            run_id=run.id, group_position=0, kind="stop", state="watching", short_symbol="",
            long_symbol=symbol, legs=[{"symbol": symbol, "side": "buy", "ratio": 1}],
            anchor_symbol=None, watched=True, contracts=1, trigger=Decimal("-1.00"), rearms=0,
            log=[], created_at=now, updated_at=now,
        ))  # fmt: skip
        session.commit()
        run_id = run.id
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Stop watched by the app" in html
    assert "<th>Group</th>" in html and "<th>Side</th>" not in html.split("Exits")[-1]
    assert html.count("Long call") >= 2  # the manage box and the exits table


def test_an_edit_keeps_what_couldnt_be_read_as_typed_and_says_so(app):
    """Review C1: an add or remove never blanks a number it can't read."""
    client = app.test_client()
    data = form(app, **{"leg_count": "4", "group_count": "2", "group-1-stop": "1,5",
                        "use_premium_bounds": "on", "premium_min": "x1",
                        "leg-2-target": "2,90", "op": "add-group"})  # fmt: skip
    r = client.post("/automation/save", data=data, headers=ORIGIN)
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    back = posted_back(html)
    assert back["group-1-stop"] == "1,5" and back["premium_min"] == "x1"
    assert back["leg-2-target"] == "2,90"
    assert "The stop &#39;1,5&#39; isn" in html and "premium &#39;x1&#39; isn" in html
    assert "The target &#39;2,90&#39; isn" in html
    assert "Give the bot a name" not in html  # still no validation problems
    with sf(app)() as session:
        assert session.scalar(select(Bot)) is None
    # Removing the first group moves the second's text and its notice with it.
    html = client.post("/automation/save", data=data | {"op": "remove-group-0"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    back = posted_back(html)
    assert back["group_count"] == "1" and back["group-0-stop"] == "1,5"
    assert html.index("The stop &#39;1,5&#39; isn") > html.index('name="group-0-stop"')
    # Removing a leg moves a later leg's text too.
    html = client.post("/automation/save", data=data | {"op": "remove-leg-0"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert posted_back(html)["leg-1-target"] == "2,90"


def test_an_edit_that_changes_a_groups_kind_never_swaps_its_stop_style(app):
    """Review I1: a saved style the group can no longer use stays picked, marked, so
    Save refuses it and you choose on purpose."""
    client = app.test_client()
    data = form(app, **{"leg_count": "4", "group_count": "2", "leg-1-side": "sell",
                        "op": "add-group"})  # fmt: skip
    html = client.post("/automation/save", data=data, headers=ORIGIN).get_data(as_text=True)
    picked = [o for o in _options(html, "group-0-stop-basis") if o[2]]
    label = "leg premium (not available for this group)"
    assert picked == [("leg_premium", label, True)]
    r = client.post("/automation/save", data=posted_back(html), headers=ORIGIN)
    assert r.status_code == 400 and "no single short" in r.get_data(as_text=True)


def test_after_an_edit_the_picker_names_no_shape(app):
    """Review M1."""
    data = form(app, **{"leg_count": "4", "group_count": "2", "op": "add-leg"})
    html = (
        app.test_client().post("/automation/save", data=data, headers=ORIGIN).get_data(as_text=True)
    )
    assert _options(html, "shape")[0] == ("", "Pick a shape", True)
    assert not any(sel for _, _, sel in _options(html, "shape")[1:])


def test_unreadable_text_stays_on_its_own_row_after_a_blank_row(app):
    """Re-review 1: a blank row above is dropped, and the typed text, its notice and
    its row's own selects move together."""
    client = app.test_client()
    data = form(app, **{
        "leg_count": "4", "group_count": "2",
        "use_time_actions": "on", "actions": "3",
        "ta-0-type": "dte", "ta-0-days": "0", "ta-0-at": "14:00", "ta-0-target": "25",
        "ta-1-type": "dte",
        "ta-2-type": "dit", "ta-2-days": "x", "ta-2-at": "15:00", "ta-2-stop": "50",
        "use_default_alerts": "on", "alerts": "3",
        "da-0-type": "pnl_pct", "da-0-direction": "above", "da-0-value": "50",
        "da-1-type": "pnl_pct", "da-1-direction": "above",
        "da-2-type": "pnl_pct", "da-2-direction": "below", "da-2-value": "y",
        "group-0-use-profit": "on", "group-0-profits": "3",
        "pa-0-0-at": "20", "pa-0-0-close": "10",
        "pa-0-2-at": "z", "pa-0-2-close": "33",
    })  # fmt: skip
    for extra in ({"op": "add-group"}, {}):  # a redraw, and a failed Save
        r = client.post("/automation/save", data=data | extra, headers=ORIGIN)
        html = r.get_data(as_text=True)
        back = posted_back(html)
        assert back["ta-1-days"] == "x" and back["ta-1-type"] == "dit"
        assert back["ta-1-stop"] == "50" and back.get("ta-2-days", "") == ""
        assert back["da-1-value"] == "y" and back["da-1-direction"] == "below"
        assert back.get("da-2-value", "") == ""
        assert back["pa-0-1-at"] == "z" and back["pa-0-1-close"] == "33"
        assert back.get("pa-0-2-at", "") == ""
        assert "Time action 2: the days &#39;x&#39;" in html
        assert "Default alert 2: &#39;y&#39;" in html
        assert "Profit action 2: the level &#39;z&#39;" in html
    with sf(app)() as session:
        assert session.scalar(select(Bot)) is None


def test_editing_an_armed_bot_into_app_watched_stops_disarms_it_and_says_why(app):
    """Final review I3: a change that makes the app watch a group's stop (its targets
    rest now) switches the bot off and back to preview, so the "watched by the app"
    box is ticked when it's armed again."""
    client = app.test_client()
    client.post("/automation/save", data=strangle_form(app), headers=ORIGIN)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    client.post(
        f"/automation/{bot_id}/arm",
        data={"on": "1", "understood": "1", "confirmed": "1"},
        headers=ORIGIN,
    )
    with sf(app)() as session:
        bot = session.get(Bot, bot_id)
        assert bot.enabled and bot.live
    data = strangle_form(app, id=str(bot_id), **{
        "group-0-rests": "target", "group-1-rests": "target", "group-0-target": "50",
        "group-1-target": "50", "group-0-stop-style": "group_entry",
        "group-1-stop-style": "group_entry"})  # fmt: skip
    assert client.post("/automation/save", data=data, headers=ORIGIN).status_code == 302
    with sf(app)() as session:
        bot = session.get(Bot, bot_id)
        assert not bot.enabled and not bot.live
    page = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "Its stops are watched by this app now" in page and "back in preview" in page


# --- Bots slice 14: legs with their own DTE, trades held overnight ---------------------


def test_a_calendar_shape_shows_its_dtes_and_saves_them(app):
    html = app.test_client().get("/automation/new?shape=calendar").get_data(as_text=True)
    data = posted_back(html)
    assert (data["leg-0-dte"], data["leg-1-dte"]) == ("7", "14")
    assert data["leg-1-target"] == "0" and data["exact_dte_shown"] == "1"
    assert data.get("use_exact_dte") == "on"
    assert "This bot holds trades overnight" in html
    assert "must close before its near legs expire" in html
    with sf(app)() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    typed = {"name": "My calendar", "tag_id": str(sid), "account": "tastytrade:5WX01234",
             "time-0": "09:47", "time-0-on": "on", "leg-0-target": "6.40"}  # fmt: skip
    for key, value in typed.items():
        data[key] = value
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 400  # no timed close: refused, said beside the field
    assert "must close before its near legs expire" in r.get_data(as_text=True)
    typed["close_time"] = "15:45"
    for key, value in typed.items():
        data[key] = value
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 302, r.get_data(as_text=True)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id).where(Bot.name == "My calendar"))
    bot = bot_store.load(sf(app), bot_id)
    assert [leg.dte for leg in bot.legs] == [7, 14] and bot.use_exact_dte
    page = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    again = posted_back(page)
    again.pop("use_exact_dte")  # untick Exact DTE only
    assert app.test_client().post("/automation/save", data=again,
                                  headers=ORIGIN).status_code == 302  # fmt: skip
    assert bot_store.load(sf(app), bot_id).use_exact_dte is False


def test_a_same_day_bot_shows_no_overnight_notice(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert "This bot holds trades overnight" not in html
    assert 'name="leg-0-dte"' in html and "Exact DTE only" in html


def test_a_reverse_calendar_is_refused_beside_its_leg(app):
    data = form(app, **{"leg-0-dte": "14", "leg-1-dte": "7"})
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 400
    assert "can&#39;t expire after a bought leg" in r.get_data(as_text=True)


def test_the_run_page_shows_far_expirations_and_the_settle_row(app):
    bot_id = _saved_bot_id(app)
    now = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)
    near, far = "SPXW  261020P05690000", "SPXW  261027P05690000"
    legs = [
        {"position": 0, "symbol": near, "strike": "5690", "right": "put", "side": "sell",
         "ratio": 1, "group": 0, "mid": "6.00", "expiration": "2026-10-20", "dte": 7},
        {"position": 1, "symbol": far, "strike": "5690", "right": "put", "side": "buy",
         "ratio": 1, "group": 0, "mid": "9.50", "expiration": "2026-10-27", "dte": 14},
    ]  # fmt: skip
    with sf(app)() as session:
        run = BotRun(
            bot_id=bot_id, key="bot-1-0947-20261013", trading_date=now.date(), entry_time="09:47",
            status="filled", live=True, reason="", legs=legs, filled_legs=[], chase_log=[],
            contracts=1, created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        session.add(BotExit(
            run_id=run.id, group_position=0, kind="settle", state="pending", short_symbol="",
            long_symbol=far, legs=[{"symbol": far, "side": "buy", "ratio": 1}], contracts=1,
            rearms=0, log=[{"at": now.isoformat(), "event": "the nearer legs expired",
                            "expired": [near], "live": [far]}],
            created_at=now, updated_at=now,
        ))  # fmt: skip
        session.commit()
        run_id = run.id
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "5690P 10-20/5690P 10-27" in html
    assert "far legs (near legs expired)" in html and "expired: SPXW  261020P05690000" in html


# --- Bots slice 16: other underlyings ---


def test_the_underlying_is_typed_with_the_five_suggested(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert 'name="underlying" value="SPX" list="underlyings"' in html
    for symbol in ("SPX", "XSP", "SPY", "QQQ", "IWM"):
        assert f'<option value="{symbol}">' in html
    assert "can be assigned early" in html and "earnings or dividend dates" in html
    assert "SPX moves up (%)" in html  # an SPX bot's labels are as before


def test_an_xsp_bot_saves_from_the_form_and_its_labels_say_xsp(app):
    data = form(app, underlying="XSP", **{"chase-entry-step": "0.01"})
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 302
    with sf(app)() as session:
        bot = session.scalar(select(Bot))
    assert bot.underlying == "XSP"
    html = app.test_client().get(f"/automation/{bot.id}").get_data(as_text=True)
    assert 'name="underlying" value="XSP"' in html
    assert "XSP moves up (%)" in html and "SPX moves up (%)" not in html
    assert "Points from the XSP price" in html and 'data-underlying="XSP"' in html


def test_a_typed_stock_is_checked_at_the_broker(app, monkeypatch):
    """Bots slice 16: a stock must have an option chain at the broker."""
    from optionsjourney.web.routes import automation

    cases = ((None, "Couldn&#39;t check AAPL&#39;s options at your broker"),
             (False, "Your broker lists no options on AAPL. Check the ticker."))  # fmt: skip
    for answer, words in cases:
        monkeypatch.setattr(automation, "_listed", lambda ticker, a=answer: a)
        response = app.test_client().post("/automation/save", headers=ORIGIN,
                                          data=form(app, underlying="aapl"))  # fmt: skip
        assert response.status_code == 400 and words in response.get_data(as_text=True)
    asked = []
    monkeypatch.setattr(automation, "_listed", lambda ticker: asked.append(ticker) or True)
    response = app.test_client().post("/automation/save", headers=ORIGIN,
                                      data=form(app, underlying="AAPL"))  # fmt: skip
    assert response.status_code == 302 and asked == ["AAPL"]
    with sf(app)() as session:
        bot = session.scalar(select(Bot))
    assert bot.underlying == "AAPL"
    # Saved again with the same stock: not asked again
    app.test_client().post("/automation/save", headers=ORIGIN,
                           data=form(app, id=str(bot.id), underlying="AAPL"))  # fmt: skip
    assert asked == ["AAPL"]


def test_a_new_underlying_says_the_bot_was_switched_off(app):
    app.test_client().post("/automation/save", data=form(app), headers=ORIGIN)
    with sf(app)() as session:
        bot = session.scalar(select(Bot))
        bot.enabled, bot.live = True, True
        session.commit()
        bot_id = bot.id
    data = form(app, id=str(bot_id), underlying="XSP")
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN,
                                      follow_redirects=True)  # fmt: skip
    html = response.get_data(as_text=True)
    assert "It now trades XSP, so it&#39;s switched off and back to Paper" in html
    with sf(app)() as session:
        bot = session.get(Bot, bot_id)
        assert (bot.underlying, bot.enabled, bot.live) == ("XSP", False, False)


# --- Bots slice 17: futures-options bots ---


def futures_form(app, **changes):
    """A /ES short put bot as the form posts it: one leg, one group, a target, no stop."""
    data = form(app, name="ES put", underlying="/ES", **{"group-0-name": "Put",
                "group-0-stop": "", "group-0-target": "50"})  # fmt: skip
    for key in [k for k in data if k.startswith(("leg-1", "leg-2", "leg-3", "group-1"))]:
        del data[key]
    data |= {"leg-0-side": "sell", "leg-0-right": "put", "leg-0-pick": "premium",
             "leg-0-target": "", "leg-0-link": "", "leg-0-group": "0", "leg-0-dte": "38",
             "leg_count": "1", "group_count": "1"}  # fmt: skip
    return data | changes


def test_a_futures_bot_saves_from_the_form_and_says_target_only(app, monkeypatch):
    monkeypatch.setattr(automation, "_listed", lambda ticker: True)
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert '<option value="/ES">' in html and '<option value="/MES">' in html
    response = app.test_client().post("/automation/save", data=futures_form(app), headers=ORIGIN)
    assert response.status_code == 302, response.get_data(as_text=True)
    with sf(app)() as session:
        bot = session.scalar(select(Bot))
    assert bot.underlying == "/ES"
    html = app.test_client().get(f"/automation/{bot.id}").get_data(as_text=True)
    assert "Futures: profit target only, no stop." in html
    assert ("usd", "pts", False) in _options(html, "group-0-target-unit")  # not "$" (#124)
    assert "On futures the amount is in points, times the contract" in html
    assert ">Arm it" not in html and ">Preview now</button>" not in html
    assert "it's never armed and has no Preview order" in html.replace("&#39;", "'")
    response = app.test_client().post(f"/automation/{bot.id}/arm", data={"on": "1"},
                                      headers=ORIGIN, follow_redirects=True)  # fmt: skip
    assert "so it&#39;s never armed" in response.get_data(as_text=True)
    assert not bot_store.load(sf(app), bot.id).enabled


def test_a_futures_bot_with_a_stop_is_refused_on_the_form(app, monkeypatch):
    monkeypatch.setattr(automation, "_listed", lambda ticker: True)
    data = futures_form(app, **{"group-0-stop": "95"})
    response = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert response.status_code == 400
    assert "Futures: profit target only, no stop. Leave the stop blank." in response.get_data(
        as_text=True
    )


def _futures_run(app, status="working", target_state="working"):
    from optionsjourney.bots.validation import validate  # noqa: F401 -- saved by the form

    with sf(app)() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    from optionsjourney.bots.definition import GroupDef, LegDef, iron_condor_skeleton
    from optionsjourney.trading.orders import Side

    bot = replace(
        iron_condor_skeleton("ES"), tag_id=sid, account_number="5WX01234",
        underlying="/ES", legs=(LegDef(0, Side.SELL, "put", dte=38),),
        groups=(GroupDef(0, "Put", stop_pct=None, target_pct=Decimal("50")),), entry_times=(),
    )  # fmt: skip
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    bot_id, problems = bot_store.save(sf(app), bot, now)
    assert problems == {}
    symbol = "./ESZ6 EW3X6 261120P7000"
    with sf(app)() as session:
        run = BotRun(
            bot_id=bot_id, key=f"ticket-{bot_id}-0123456789ab", trading_date=now.date(),
            entry_time="09:47", status=status, live=True, reason="",
            legs=[{"position": 0, "symbol": symbol, "strike": "7000", "right": "put",
                   "side": "sell", "ratio": 1, "group": 0, "mid": "—"}],
            filled_legs=[] if status == "working" else
            [{"symbol": symbol, "side": "sell", "fill_price": "12.50", "contracts": 1}],
            target_credit=Decimal("12.50"),
            fill_credit=None if status == "working" else Decimal("12.50"),
            chase_log=[{"event": "from the Trade page"}], contracts=1, broker_order_id="901",
            multiplier=Decimal(50), contract="/ESZ6", pt_only=True, entry_mode="limit",
            created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        if status == "filled":
            session.add(BotExit(
                run_id=run.id, group_position=0, kind="target", state=target_state,
                short_symbol=symbol, long_symbol="", contracts=1, limit=Decimal("6.25"),
                broker_order_id="902", rearms=0, legs=[{"symbol": symbol, "side": "sell",
                                                        "ratio": 1}],
                log=[], created_at=now, updated_at=now,
            ))  # fmt: skip
        session.commit()
        return run.id


def test_a_resting_futures_entry_shows_its_contract_dollars_and_cancel(app, monkeypatch):
    run_id = _futures_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Futures: profit target only, no stop" in html
    assert "/ESZ6" in html and "×50 a point" in html
    assert "12.50 cr ($625.00 a contract)" in html
    assert f"/automation/runs/{run_id}/cancel-entry" in html and "order 901" in html
    asked = []

    def cancel(deps, rid):
        asked.append(rid)
        from optionsjourney.services.bot_limit import Outcome

        return Outcome(True, "The entry was cancelled; nothing filled.")

    monkeypatch.setattr(automation.bot_limit, "cancel_entry", cancel)
    response = app.test_client().post(f"/automation/runs/{run_id}/cancel-entry",
                                      headers=ORIGIN, follow_redirects=True)  # fmt: skip
    assert asked == [run_id]
    assert "The entry was cancelled; nothing filled." in response.get_data(as_text=True)


def test_a_futures_trade_isnt_managed_from_the_run_page_yet(app):
    run_id = _futures_run(app, status="filled")
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "rests at TastyTrade (order 902)" in html
    assert "Adding a stop, closing part" in html
    assert "Change stop" not in html and "Turn exits off" not in html
    assert "Cancel entry" not in html
    # bots slice 21: its target can be changed or switched off (after a confirm)
    assert "Change target" in html and f"/automation/runs/{run_id}/sides/0/target-off" in html
    page = app.test_client().post(f"/automation/runs/{run_id}/sides/0/target-off", headers=ORIGIN)
    assert page.status_code == 200 and "no exit at all" in page.get_data(as_text=True)


def test_the_run_page_has_each_switch_and_shows_a_stop_switched_off(app, monkeypatch):
    """Bots slice 18: the stop and the target switch off separately."""
    from optionsjourney.db.models import BotTradeOverride
    from optionsjourney.services import bot_manage

    run_id = _held_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    for action in ("stop-off", "target-off", "exits-off"):
        assert f"/automation/runs/{run_id}/sides/0/{action}" in html
    with sf(app)() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=0, stop_off=True,
                                     changed_at=datetime(2026, 10, 13, 14, 0, tzinfo=UTC),
                                     log=[]))  # fmt: skip
        session.commit()
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "The stop is off." in html and "Stop off: no stop" in html
    assert f"/automation/runs/{run_id}/sides/0/stop-on" in html
    assert f"/automation/runs/{run_id}/sides/0/stop-off" not in html
    assert f"/automation/runs/{run_id}/sides/0/target-off" in html  # still on
    calls = []
    for name in ("stop_off", "stop_on", "target_off", "target_on"):
        monkeypatch.setattr(bot_manage, name, lambda d, r, s, *a, n=name: (
            calls.append((n, *a)) or bot_manage.Outcome(True, f"{n} done")))  # fmt: skip
    client = app.test_client()
    for action, data in (("stop-off", {}), ("stop-on", {"pct": "200"}), ("target-off", {}),
                         ("target-on", {})):  # fmt: skip
        client.post(f"/automation/runs/{run_id}/sides/1/{action}", data=data, headers=ORIGIN)
    assert calls == [("stop_off",), ("stop_on", "200"), ("target_off",), ("target_on",)]


def test_restore_checks_and_restores_and_flashes_each_item(app, monkeypatch):
    """Bots slice 18: Check is a dry run, Restore applies; each item is flashed."""
    from decimal import Decimal as Dec

    from optionsjourney.services import bot_rearm

    run_id = _held_run(app)
    calls = []

    def fake(deps, run_id_, *, dry_run):
        calls.append((run_id_, dry_run))
        status = "missing" if dry_run else "placed"
        return [bot_rearm.Item(run_id_, "IC", "Call side", 0, "stop", 2, Dec("4.80"), status,
                               "" if dry_run else "at 4.80")]  # fmt: skip

    monkeypatch.setattr(bot_rearm, "rearm_trade", fake)
    client = app.test_client()
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert f"/automation/runs/{run_id}/restore" in html
    client.post(f"/automation/runs/{run_id}/restore", data={"apply": "0"}, headers=ORIGIN)
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Call side&#39;s stop is missing: it goes back at 4.80 for 2." in html
    assert "nothing was sent" in html
    client.post(f"/automation/runs/{run_id}/restore", data={"apply": "1"}, headers=ORIGIN)
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Call side&#39;s stop: placed (at 4.80)." in html
    assert calls == [(run_id, True), (run_id, False)]
    assert client.post(f"/automation/runs/{run_id}/restore", data={"apply": "1"}).status_code == 403


def test_the_automation_page_lists_open_trades_with_restore_all(app, monkeypatch):
    from optionsjourney.services import bot_rearm

    run_id = _held_run(app)
    monkeypatch.setattr("optionsjourney.web.routes.automation._now",
                        lambda: datetime(2026, 10, 13, 14, 0, tzinfo=UTC))  # fmt: skip
    client = app.test_client()
    html = client.get("/automation").get_data(as_text=True)
    assert "Open bot trades" in html and f"run {run_id}" in html and "/automation/restore" in html
    monkeypatch.setattr(bot_rearm, "rearm_all", lambda deps, *, dry_run: [])
    client.post("/automation/restore", data={"apply": "0"}, headers=ORIGIN)
    assert "Nothing is missing" in client.get("/automation").get_data(as_text=True)


def test_the_stop_box_has_preview_which_flashes_without_changing(app, monkeypatch):
    from optionsjourney.services import bot_manage

    run_id = _held_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert f"/automation/runs/{run_id}/sides/0/preview-stop" in html
    monkeypatch.setattr(bot_manage, "preview_stop", lambda d, r, s, pct, unit: bot_manage.Outcome(
        True, f"Preview, nothing changed: a {pct}% stop"))  # fmt: skip
    client = app.test_client()
    client.post(f"/automation/runs/{run_id}/sides/0/preview-stop", data={"pct": "200"},
                headers=ORIGIN)  # fmt: skip
    assert "a 200% stop" in client.get(f"/automation/runs/{run_id}").get_data(as_text=True)


def test_close_entire_trade_and_close_selected_routes(app, monkeypatch):
    """Bots slice 18: Close entire trade on the run page; Close selected on the
    Automation page's open trades, 0DTE only; the order type passed through."""
    from optionsjourney.services import bot_manage

    run_id = _held_run(app)
    calls = []
    monkeypatch.setattr(bot_manage, "close_trade", lambda d, r, kind, only=None: (
        calls.append(("trade", r, kind)) or bot_manage.Outcome(True, "closing it")))  # fmt: skip
    monkeypatch.setattr(bot_manage, "close_selected", lambda d, ids, kind: (
        calls.append(("selected", ids, kind))
        or [(int(ids[0]), bot_manage.Outcome(False, "refused it"))]))  # fmt: skip
    monkeypatch.setattr(bot_manage, "close_part", lambda d, r, s, q, *kind: (
        calls.append(("part", q, *kind)) or bot_manage.Outcome(True, "part")))  # fmt: skip
    client = app.test_client()
    html = client.get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert f"/automation/runs/{run_id}/close-trade" in html and 'value="market"' in html
    # Each first asks to confirm, in a plain sentence; nothing is done until Confirm.
    page = client.post(f"/automation/runs/{run_id}/close-trade", data={"order_type": "market"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert "Close the whole trade?" in page and 'name="confirm" value="1"' in page
    page = client.post(f"/automation/runs/{run_id}/sides/0/close",
                       data={"quantity": "1", "order_type": "market"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert "Close at market?" in page
    page = client.post("/automation/close-selected", data={"run_id": [str(run_id)]},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert "Close the selected trades?" in page and f'name="run_id" value="{run_id}"' in page
    assert calls == []
    client.post(f"/automation/runs/{run_id}/close-trade",
                data={"order_type": "market", "confirm": "1"}, headers=ORIGIN)  # fmt: skip
    client.post(f"/automation/runs/{run_id}/sides/0/close",
                data={"quantity": "1", "order_type": "market", "confirm": "1"},
                headers=ORIGIN)  # fmt: skip
    monkeypatch.setattr("optionsjourney.web.routes.automation._now",
                        lambda: datetime(2026, 10, 13, 14, 0, tzinfo=UTC))  # fmt: skip
    html = client.get("/automation").get_data(as_text=True)
    assert f'name="run_id" value="{run_id}"' in html and "Close selected" in html
    client.post("/automation/close-selected", data={"run_id": [str(run_id)],
                "order_type": "limit", "confirm": "1"}, headers=ORIGIN)  # fmt: skip
    assert f"Run {run_id}: refused it" in client.get("/automation").get_data(as_text=True)
    assert calls == [("trade", run_id, "market"), ("part", "1", "market"),
                     ("selected", [str(run_id)], "limit")]  # fmt: skip
    assert client.post("/automation/close-selected", data={"run_id": "1"}).status_code == 403


def test_the_exits_off_box_says_the_timed_close_still_closes_it(app):
    """Bots slice 18, the author's ruling: exits off is no stop and no target, and the
    timed close still runs; the run page says so before it acts."""
    from optionsjourney.services import bot_store

    run_id = _held_run(app, off_side=1)
    with sf(app)() as session:
        bot_id = session.get(BotRun, run_id).bot_id
    bot = bot_store.load(sf(app), bot_id)
    bot_store.save(
        sf(app), replace(bot, close_time="15:45"), datetime(2026, 10, 13, 14, tzinfo=UTC)
    )
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Its timed close at 15:45 still closes it" in html
    assert "won't close it" not in html


def test_trade_now_needs_trade_typed_and_goes_to_the_run(app, monkeypatch):
    """Bots slice 19: the route passes what was typed; a refusal comes back to the bot's
    page, a start goes to the run."""
    from optionsjourney.web.routes import automation

    bot_id = _saved_bot_id(app)
    calls = []

    def fake(deps, bot, typed):
        calls.append((bot, typed))
        return (None, "Type TRADE to send a real order.") if typed != "TRADE" else (43, None)

    monkeypatch.setattr(automation.bot_entry, "trade_now", fake)
    client = app.test_client()
    r = client.post(
        f"/automation/{bot_id}/trade-now", data={"confirm": "no", "understood": "1"}, headers=ORIGIN
    )
    assert r.headers["Location"].endswith(f"/automation/{bot_id}#trade-now")
    r = client.post(
        f"/automation/{bot_id}/trade-now",
        data={"confirm": "TRADE", "understood": "1"},
        headers=ORIGIN,
    )
    assert r.headers["Location"] == "/automation/runs/43"
    assert calls == [(bot_id, "no"), (bot_id, "TRADE")]


def test_trade_now_is_offered_on_a_live_bots_page_only(app):
    bot_id = _saved_bot_id(app)
    client = app.test_client()
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    assert 'id="trade-now"' not in client.get(f"/automation/{bot_id}").get_data(as_text=True)
    client.post(f"/automation/{bot_id}/live", data=LIVE_TICKED, headers=ORIGIN)
    assert 'id="trade-now"' in client.get(f"/automation/{bot_id}").get_data(as_text=True)


def test_slice_19_settings_round_trip_through_the_form(app):
    """The chase's time limit and market switch, the leg deltas, a linked wing's max
    offset, the min short width, moving conflicted strikes and a $ profit-action stop."""
    data = form(app, **{
        "chase-entry-window": "3", "chase-exit-market": "on", "use_delta_exit": "on",
        "leg-0-delta-below": "-40", "leg-3-pick": "premium", "leg-3-target": "0.50",
        "leg-3-max-offset": "-25", "min_short_width": "20", "use_min_short_width": "on",
        "move_conflicted_strikes": "on",
        "leg-0-fallback": "1.00", "leg-2-fallback": "1.00",  # #314: needed with a min width
        "group-0-target": "80", "group-0-use-profit": "on", "group-0-profits": "1",
        "pa-0-0-at": "50", "pa-0-0-close": "0", "pa-0-0-stop": "0.40",
        "pa-0-0-stop-unit": "usd",
    })  # fmt: skip
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 302, r.get_data(as_text=True)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    bot = bot_store.load(sf(app), bot_id)
    assert (bot.entry_chase.window_minutes, bot.exit_chase.switch_to_market) == (3, True)
    assert bot.legs[0].exit_delta_below == Decimal("-40")
    assert (bot.legs[3].pick, bot.legs[3].max_offset) == ("premium", Decimal("-25"))
    assert (bot.min_short_width, bot.move_conflicted_strikes) == (Decimal("20"), True)
    action = bot.groups[0].profit_actions[0]
    assert (action.stop_pct, action.stop_usd) == (None, Decimal("0.40"))
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="chase-entry-window" inputmode="numeric" value="3"' in html
    assert 'name="leg-3-max-offset" inputmode="decimal" value="-25"' in html
    assert '<option value="usd" selected>$ P&amp;L</option>' in html


def test_dollar_sizing_round_trips_and_an_unreadable_amount_is_named(app):
    data = form(app, sizing="dollars", max_per_entry_usd="300",
                **{"group-0-stop-style": "group_entry", "group-1-stop-style": "group_entry",
                   "leg-0-side": "buy", "leg-1-side": "sell", "leg-2-side": "buy",
                   "leg-3-side": "sell", "leg-0-target": "20", "leg-2-target": "20",
                   "split": ""})  # fmt: skip
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 302, r.get_data(as_text=True)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    assert bot_store.load(sf(app), bot_id).sizing == "dollars"
    bad = form(app, max_per_entry_usd="lots")
    html = (
        app.test_client().post("/automation/save", data=bad, headers=ORIGIN).get_data(as_text=True)
    )
    assert "most buying power &#39;lots&#39; isn" in html


def test_split_iron_condors_sits_under_groups(app):
    """#112: it decides whether each side is its own journal trade."""
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert html.index('id="groups"') < html.index('name="split"') < html.index('id="exits"')


def test_every_basics_field_has_a_tooltip(app):
    """#113: an ⓘ on each Basics field, read by screen readers through aria-describedby."""
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    basics = html[html.index('id="basics"') : html.index('id="schedule"')]
    for field in ("name", "account", "underlying", "sizing", "contracts",
                  "buying-power", "per-trade", "per-day", "entries", "open", "tags"):  # fmt: skip
        assert f'aria-describedby="tip-bot-{field}"' in basics, field
        assert f'role="tooltip" id="tip-bot-{field}"' in basics, field
    assert "settle in cash at the close" in basics  # the underlying's note, now its tip


def test_only_the_chosen_way_to_size_shows(app):
    """#111, decision 0050: sizing_choice.js shows the chosen box; the page marks both."""
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert ">Buying power</option>" in html
    named = '<span class="bot-form__label">'
    assert f'data-sizing="contracts">{named}Contracts per entry' in html
    assert f'data-sizing="dollars">{named}Most buying power' in html
    assert "sizing_choice.js" in html and ">Size and limits</h3>" in html


def test_a_new_bots_form_starts_with_move_conflicted_strikes_ticked(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert 'name="move_conflicted_strikes" checked' in html


def test_a_dollar_sized_run_says_how_it_was_sized(app):
    bot_id = _saved_bot_id(app)
    record = {"sizing": "dollars", "allocation": "600.00", "worst": "-2.75",
              "unit_cost": "275.00", "asked": 2, "contracts": 2}  # fmt: skip
    with sf(app)() as session:
        run = BotRun(bot_id=bot_id, key="bot-1-0947-x", trading_date=datetime(2026, 10, 13).date(),
                     entry_time="09:47", status="filled", live=True, account_number="5WX01234",
                     legs=[], filled_legs=[], chase_log=[record], contracts=2,
                     created_at=datetime(2026, 10, 13, tzinfo=UTC),
                     updated_at=datetime(2026, 10, 13, tzinfo=UTC))  # fmt: skip
        session.add(run)
        session.commit()
        run_id = run.id
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Sized by dollars" in html and "$600.00 at the worst price" in html  # older runs


def test_a_buying_power_run_says_what_the_broker_said(app):
    bot_id = _saved_bot_id(app)
    record = {"sizing": "buying_power", "broker": "TastyTrade", "budget": "2000.00",
              "worst": "0.05", "unit_cost": "497.30", "asked": 4, "contracts": 4}  # fmt: skip
    with sf(app)() as session:
        run = BotRun(bot_id=bot_id, key="bot-1-0947-y", trading_date=datetime(2026, 10, 13).date(),
                     entry_time="09:47", status="filled", live=True, account_number="5WX01234",
                     legs=[], filled_legs=[], chase_log=[record], contracts=4,
                     created_at=datetime(2026, 10, 13, tzinfo=UTC),
                     updated_at=datetime(2026, 10, 13, tzinfo=UTC))  # fmt: skip
        session.add(run)
        session.commit()
        run_id = run.id
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Sized by buying power" in html
    assert "TastyTrade says one contract needs $497.30: 4" in html


# --- Bots slice 20: seeing what bots do -----------------------------------------------


def _run_with(app, chase_log, status="filled", exits=(), bot_id=None):
    bot_id = bot_id or _saved_bot_id(app)
    now = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
    with sf(app)() as session:
        run = BotRun(
            bot_id=bot_id, key=f"bot-{bot_id}-0947-20261014-{len(chase_log)}-{status}",
            trading_date=now.date(), entry_time="09:47", status=status, live=True, reason="",
            legs=[{"symbol": "SPXW  261014P05800000", "side": "sell", "ratio": 1, "group": 0,
                   "position": 0, "strike": "5800", "right": "put", "mid": "1.30"}],
            filled_legs=[], chase_log=chase_log, contracts=1, created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        for kind, state, log in exits:
            session.add(BotExit(
                run_id=run.id, group_position=0, kind=kind, state=state,
                short_symbol="SPXW  261014P05800000", long_symbol="", contracts=1, rearms=0,
                legs=[{"symbol": "SPXW  261014P05800000", "side": "sell", "ratio": 1}],
                log=log, created_at=now, updated_at=now,
            ))  # fmt: skip
        session.commit()
        return run.id


def test_the_run_page_shows_the_order_timeline_not_raw_pairs(app):
    log = [
        {"at": "2026-10-14T14:00:00+00:00", "price": "1.30", "result": "Sent."},
        {"at": "2026-10-14T14:00:10+00:00", "price": "1.25", "result": "Replaced."},
        {"at": "2026-10-14T14:00:20+00:00", "tranche": 1, "filled": 1},
    ]
    run_id = _run_with(app, log, exits=[("stop", "working", [
        {"at": "2026-10-14T14:00:30+00:00", "stop": "95"},
        {"at": "2026-10-14T14:00:31+00:00", "result": "Sent."}])])  # fmt: skip
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Orders, step by step" in html and '<details class="card section-gap steps"' in html
    # Folded, one line each: the time only (all one day), no legs on the entry's steps
    assert "10:00:00" in html and "10-14 10:00:00" not in html and "Replaced" in html
    assert "STO 1 5800P" not in html
    assert "BTC 1 5800P" in html and "working now" in html
    assert "price: 1.30, result: Sent." not in html


def test_watch_status_says_a_failed_watch_close_and_the_page_shows_it(app):
    run_id = _run_with(app, [], exits=[
        ("watch", "filled", [{"rule": "Delta exit", "detail": "position delta 32 above 30"}]),
        ("close", "failed", [{"event": "a close of 1 for the Delta exit", "by": "Delta exit"}]),
    ])  # fmt: skip
    data = app.test_client().get(f"/automation/runs/{run_id}/watch-status").get_json()
    assert data["banner"]["tone"] == "danger"
    assert "close it yourself" in data["banner"]["text"]
    assert data["open"] is True  # the close failed: the side is still open
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert 'id="watch-banner"' in html and "close it yourself" in html
    assert "watch_banner.js" in html


def test_watch_status_is_empty_without_a_watch_row_and_404_for_no_run(app):
    run_id = _run_with(app, [])
    data = app.test_client().get(f"/automation/runs/{run_id}/watch-status").get_json()
    assert data["banner"] is None
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "watch_banner.js" not in html  # the bot has no watch rule on
    assert app.test_client().get("/automation/runs/999/watch-status").status_code == 404


def _armed_saved_bot(app):
    bot_id = _saved_bot_id(app)
    app.test_client().post(f"/automation/{bot_id}/arm", data={"on": "1", "understood": "1", "confirmed": "1"},
                           headers=ORIGIN)  # fmt: skip
    return bot_id


def test_the_list_opens_the_schedule_drawer(app, monkeypatch):
    monkeypatch.setattr(automation, "_now", lambda: datetime(2026, 10, 14, 13, 0, tzinfo=UTC))
    _armed_saved_bot(app)
    html = app.test_client().get("/automation").get_data(as_text=True)
    assert "data-schedule-open" in html and 'id="schedule-drawer"' in html
    assert 'data-schedule-url="/automation/schedule"' in html and "schedule_drawer.js" in html


def test_the_schedule_answers_json_for_today_and_any_day(app, monkeypatch):
    monkeypatch.setattr(automation, "_now", lambda: datetime(2026, 10, 14, 13, 0, tzinfo=UTC))
    bot_id = _armed_saved_bot(app)  # 09:47 New York, still to come at 09:00
    client = app.test_client()
    today = client.get("/automation/schedule").get_json()
    assert (today["date"], today["is_today"], today["now"]) == ("2026-10-14", True, "09:00:00")
    assert today["counts"]["pending"] == 1
    row = today["rows"][0]
    assert (row["time"], row["bot_id"], row["state"]) == ("09:47", bot_id, "pending")
    assert row["can_skip"]
    assert row["bot_url"] == f"/automation/{bot_id}" and row["underlying"] == "SPX"
    assert row["account"] and row["size"]
    future = client.get("/automation/schedule?date=2026-10-15").get_json()
    assert not future["is_today"] and future["rows"][0]["can_skip"]
    assert any("Scheduled, not guaranteed" in n["text"] for n in future["notes"])
    past = client.get("/automation/schedule?date=2026-10-13").get_json()
    assert past["rows"][0]["note"] == "no run recorded" and not past["rows"][0]["can_skip"]
    assert client.get("/automation/schedule?date=2026-10-17").get_json()["market_closed"]  # a Sat
    assert client.get("/automation/schedule?date=nonsense").status_code == 400


def test_the_schedule_carries_the_days_events_and_holiday(app, monkeypatch):
    monkeypatch.setattr(automation, "_now", lambda: datetime(2026, 10, 14, 13, 0, tzinfo=UTC))
    with sf(app)() as session:
        session.add(EconomicEvent(event_date=date(2026, 10, 14), event_time="08:30",
                                  title="CPI m/m", impact="high", key="CPI",
                                  source="forexfactory", forecast="0.3%",
                                  previous="0.4%"))  # fmt: skip
        session.add(
            MarketCalendarDay(day=date(2026, 11, 26), kind="closed", name="Thanksgiving Day")
        )
        session.commit()
    client = app.test_client()
    today = client.get("/automation/schedule").get_json()
    assert today["econ_events"] == [{"time": "08:30", "label": "CPI", "impact": "high",
                                     "key": "CPI", "prints": [{"title": "CPI m/m",
                                     "forecast": "0.3%", "previous": "0.4%"}]}]  # fmt: skip
    assert today["holiday"] is None and today["calendar_covers"]
    holiday = client.get("/automation/schedule?date=2026-11-26").get_json()
    assert holiday["holiday"] == "Thanksgiving Day" and holiday["econ_events"] == []


def test_skip_day_from_the_drawer_skips_the_day_shown_and_undoes(app, monkeypatch):
    monkeypatch.setattr(automation, "_now", lambda: datetime(2026, 10, 14, 13, 0, tzinfo=UTC))
    bot_id = _armed_saved_bot(app)
    client = app.test_client()
    url = f"/automation/{bot_id}/skip-day?date=2026-10-15"
    assert client.post(url, headers=ORIGIN).get_json() == {"day": "2026-10-15"}
    stored = bot_store.load(sf(app), bot_id)
    assert "2026-10-15" in stored.blackout_days and stored.use_blackout_days
    assert client.get("/automation/schedule?date=2026-10-15").get_json()["rows"] == []
    assert client.delete(url, headers=ORIGIN).status_code == 200
    assert client.get("/automation/schedule?date=2026-10-15").get_json()["rows"]
    over = client.post(f"/automation/{bot_id}/skip-day?date=2026-10-13", headers=ORIGIN)
    assert over.status_code == 400
    assert client.post(url).status_code == 403  # not from the app's own page


def test_filters_now_answers_json_and_only_reads(app, monkeypatch):
    from optionsjourney.services import bot_filters

    bot_id = _saved_bot_id(app)
    asked = []

    def fake(market, bot, now):
        asked.append(bot.id)
        return [{"label": "VIX level", "ok": True, "detail": "VIX 18.5", "as_of": "09:47:10",
                 "stale": False}]  # fmt: skip

    monkeypatch.setattr(bot_filters, "preview", fake)
    data = app.test_client().get(f"/automation/{bot_id}/filters-now").get_json()
    assert data == {"filters": [{"label": "VIX level", "ok": True, "detail": "VIX 18.5",
                                 "as_of": "09:47:10", "stale": False}]}  # fmt: skip
    assert asked == [bot_id]
    assert app.test_client().get("/automation/999/filters-now").status_code == 404
    with sf(app)() as session:
        assert session.scalars(select(BotRun)).all() == []  # nothing written
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "Check the filters now" in html and "filters_now.js" in html


def test_the_list_searches_by_name_and_day(app):
    _saved_bot_id(app)
    app.test_client().post("/automation/save", data=form(app, name="Wings QQQ"), headers=ORIGIN)
    client = app.test_client()
    html = client.get("/automation?q=wings").get_data(as_text=True)
    assert "Wings QQQ" in html and ">SPX IC<" not in html and "1 of 2 bots" in html
    html = client.get("/automation?q=sat").get_data(as_text=True)  # not a day word: text
    assert "No bots match" in html
    html = client.get("/automation?q=fri").get_data(as_text=True)  # daily: Mon to Fri
    assert "Wings QQQ" in html and ">SPX IC<" in html and 'value="fri"' in html


def test_bulk_tagging_from_the_list(app):
    first = _saved_bot_id(app)
    app.test_client().post("/automation/save", data=form(app, name="Other"), headers=ORIGIN)
    with sf(app)() as session:
        second = session.scalars(select(Bot.id).order_by(Bot.id.desc())).first()
    client = app.test_client()
    html = client.get("/automation").get_data(as_text=True)
    assert 'id="tag-form"' in html and 'action="/automation/tags"' in html
    r = client.post("/automation/tags", data={"bot_id": [str(first), str(second)],
                                              "add": "Beta, Wings"}, headers=ORIGIN,
                    follow_redirects=True)  # fmt: skip
    assert "Tagged 2 bots." in r.get_data(as_text=True)
    assert bot_store.load(sf(app), first).tags == ("0DTE", "Beta", "Wings")  # 0DTE: main
    client.post("/automation/tags", data={"bot_id": [str(first)], "remove": "beta"},
                headers=ORIGIN)  # fmt: skip
    assert bot_store.load(sf(app), first).tags == ("0DTE", "Wings")
    r = client.post("/automation/tags", data={"add": "x"}, headers=ORIGIN, follow_redirects=True)
    assert "Tick the bots to tag first." in r.get_data(as_text=True)
    # #144: one Tags box, read by whichever button was pressed
    client.post("/automation/tags", data={"bot_id": [str(second)], "tags": "Gamma",
                                          "tag_action": "add"}, headers=ORIGIN)  # fmt: skip
    assert "Gamma" in bot_store.load(sf(app), second).tags
    client.post("/automation/tags", data={"bot_id": [str(second)], "tags": "gamma",
                                          "tag_action": "remove"}, headers=ORIGIN)  # fmt: skip
    assert "Gamma" not in bot_store.load(sf(app), second).tags


def test_the_record_shows_pl_percent_and_days(app, monkeypatch):
    from optionsjourney.core.stats import journal_summary
    from optionsjourney.services import bot_stats

    bot_id = saved_bot(app)
    opened = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)
    pos = type("P", (), {})()
    entry = type("E", (), {})()
    entry.name, entry.bot, entry.close_reason, entry.position = "SPX 5800/5790", None, None, pos
    record = bot_stats.Record(journal_summary([]), [], [entry])
    monkeypatch.setattr(bot_stats, "record", lambda *a, **k: record)
    monkeypatch.setattr(automation.trade_rows, "realized_percent", lambda p: Decimal("37.5"))
    monkeypatch.setattr(automation.trade_rows, "days_in_trade", lambda p, now=None: 3)
    pos.opened_at = pos.closed_at = opened
    pos.realized_pl = Decimal("75")
    pos.key = "k1"
    html = app.test_client().get(f"/automation/{bot_id}/record").get_data(as_text=True)
    assert "<th>P/L % " in html and "37.5%" in html
    assert 'id="tip-record-pl-pct"' in html and 'id="tip-record-source"' in html
    assert "<th>Days</th>" in html and ">3<" in html


class _Forms(HTMLParser):
    """Which form each named input sits in (its own form= wins)."""

    def __init__(self):
        super().__init__()
        self.stack, self.inputs = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self.stack.append(a.get("id"))
        elif tag in ("input", "button") and a.get("name"):
            self.inputs.append(
                (a["name"], a.get("form") or (self.stack[-1] if self.stack else None))
            )

    def handle_endtag(self, tag):
        if tag == "form" and self.stack:
            self.stack.pop()


def test_the_tag_boxes_arent_in_the_skip_form(app):
    _saved_bot_id(app)
    parser = _Forms()
    html = app.test_client().get("/automation").get_data(as_text=True)
    parser.feed(html)
    owners = {}
    for name, owner in parser.inputs:
        owners.setdefault(name, set()).add(owner)
    assert owners["add"] == {"tag-form"} and owners["remove"] == {"tag-form"}
    assert 'id="bulk-bar"' in html and "data-clear" in html


def test_skip_many_never_acts_on_a_post_carrying_tags(app):
    bot_id = _armed_saved_bot(app)
    r = app.test_client().post("/automation/skip", data={"bot_id": str(bot_id), "tags": "Beta"},
                               headers=ORIGIN, follow_redirects=True)  # fmt: skip
    assert "Nothing skipped" in r.get_data(as_text=True)
    assert not bot_store.load(sf(app), bot_id).blackout_days


def test_watch_status_keeps_polling_before_the_fill(app):
    for status, still in (("pending", True), ("running", True), ("missed", False)):
        run_id = _run_with(app, [], status=status)
        data = app.test_client().get(f"/automation/runs/{run_id}/watch-status").get_json()
        assert data["open"] is still, status


# --- Bots slice 21: per-trade settings ----------------------------------------------------


def test_the_run_page_offers_the_wing_rule_and_posts_it(app, monkeypatch):
    from optionsjourney.services import bot_manage

    run_id = _held_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert f"/automation/runs/{run_id}/sides/0/wing" in html and "After the stop" in html
    calls = []
    monkeypatch.setattr(bot_manage, "set_wing_rule", lambda deps, run, side, *args: (
        calls.append((run, side, *args)) or bot_manage.Outcome(True, "saved")))  # fmt: skip
    client = app.test_client()
    url = f"/automation/runs/{run_id}/sides/0/wing"
    assert client.post(url, data={"mode": "leave", "limit": ""}, headers=ORIGIN).status_code == 302
    assert calls == [(run_id, 0, "leave", "")]


def test_the_stop_box_takes_a_unit_and_a_credit_group_has_its_closing_order(app, monkeypatch):
    from optionsjourney.services import bot_manage

    run_id = _held_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert 'name="unit"' in html and "$ a contract" in html
    assert f"/automation/runs/{run_id}/sides/0/closing-order" in html
    calls = []
    monkeypatch.setattr(bot_manage, "set_closing_order", lambda deps, run, side, *args: (
        calls.append((run, side, *args)) or bot_manage.Outcome(True, "set")))  # fmt: skip
    url = f"/automation/runs/{run_id}/sides/0/closing-order"
    app.test_client().post(url, data={"mode": "tighter", "amount": "0.80"}, headers=ORIGIN)
    assert calls == [(run_id, 0, "tighter", "0.80")]


def test_a_groups_profit_actions_post_the_whole_form(app, monkeypatch):
    from optionsjourney.services import bot_manage

    run_id = _held_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Profit actions for this trade" in html and 'name="pa-0-0-at"' in html
    seen = []
    monkeypatch.setattr(
        bot_manage,
        "set_profit_actions",
        lambda deps, run, side, form: (
            seen.append((run, side, form.get("pa-1-0-at"))) or bot_manage.Outcome(True, "saved")
        ),
    )
    url = f"/automation/runs/{run_id}/sides/1/profits"
    app.test_client().post(url, data={"pa-1-0-at": "50", "group-1-profits": "5"}, headers=ORIGIN)
    assert seen == [(run_id, 1, "50")]


def test_the_trades_timed_close_card_saves_and_confirms_a_close_due_now(app, monkeypatch):
    from optionsjourney.services import bot_trade_settings

    run_id = _held_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "This trade's settings" in html and f"/automation/runs/{run_id}/timed-close" in html
    calls = []

    def save(deps, run, time, dte, confirmed=False):
        calls.append((run, time, dte, confirmed))
        if not confirmed:
            return bot_trade_settings.Saved(False, "Not saved yet.", "It would close now.")
        return bot_trade_settings.Saved(True, "saved it")

    monkeypatch.setattr(bot_trade_settings, "save_timed", save)
    client = app.test_client()
    url = f"/automation/runs/{run_id}/timed-close"
    page = client.post(url, data={"time": "11:00", "dte": "0"}, headers=ORIGIN)
    assert page.status_code == 200 and "It would close now." in page.get_data(as_text=True)
    done = client.post(url, data={"time": "11:00", "dte": "0", "confirm": "1"}, headers=ORIGIN)
    assert done.status_code == 302 and done.headers["Location"].endswith("#settings")
    assert calls == [(run_id, "11:00", "0", False), (run_id, "11:00", "0", True)]


def test_the_trades_watch_rules_card_posts_its_boxes(app, monkeypatch):
    from optionsjourney.services import bot_trade_settings

    run_id = _held_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert f"/automation/runs/{run_id}/watch-rules" in html and 'name="sl_ratio_above"' in html
    assert 'name="leg-0-delta-below"' in html
    seen = []
    monkeypatch.setattr(bot_trade_settings, "save_watch", lambda deps, run, form, confirmed=False: (
        seen.append((run, form.get("sl_ratio_above"), confirmed))
        or bot_trade_settings.Saved(True, "saved")))  # fmt: skip
    url = f"/automation/runs/{run_id}/watch-rules"
    data = {"use_sl_ratio_exit": "on", "sl_ratio_above": "20"}
    assert app.test_client().post(url, data=data, headers=ORIGIN).status_code == 302
    assert seen == [(run_id, "20", False)]


def test_the_bot_form_offers_closing_another_group_after_a_stop(app):
    bot_id = _saved_bot_id(app)
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="group-0-pair"' in html and "When its stop fills, also close" in html


def test_a_resting_entry_can_be_modified_from_the_run_page(app, monkeypatch):
    run_id = _futures_run(app)
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert f"/automation/runs/{run_id}/modify-entry" in html
    asked = []

    def modify(deps, rid, price):
        from optionsjourney.services.bot_limit import Outcome

        asked.append((rid, price))
        return Outcome(True, "The entry rests at 13.00 credit now.")

    monkeypatch.setattr(automation.bot_limit, "modify_entry", modify)
    response = app.test_client().post(f"/automation/runs/{run_id}/modify-entry",
                                      data={"price": "13.00"}, headers=ORIGIN,
                                      follow_redirects=True)  # fmt: skip
    assert asked == [(run_id, "13.00")] and "rests at 13.00" in response.get_data(as_text=True)


# --- Decision 0048: Rests at the broker, the side, what rests ----------------------------


def test_the_bot_page_offers_rests_at_the_broker_and_no_opens_for(app):
    html = app.test_client().get("/automation/new?shape=short_put").get_data(as_text=True)
    assert 'name="premium_side"' not in html and "Opens for" not in html
    # #126: one sold leg offers its resting stop, ticked by default
    assert '<input type="checkbox" name="group-0-rest-stop" checked' in html
    assert "the share of the credit kept" in html  # the target's tip (#124)
    assert "credit trades only" in html
    assert "opens for" not in html.lower()
    # one sold leg: every stop style is offered, none marked unavailable
    assert "not available for this group" not in html
    # unticked, the target rests and the app watches the stop, said on the page
    assert "the profit target rests and the app watches the stop" in html


def test_a_debit_shape_starts_with_its_target_resting(app):
    html = app.test_client().get("/automation/new?shape=call_debit_spread").get_data(as_text=True)
    assert '<input type="hidden" name="group-0-rests" value="target">' in html  # two legs
    html = app.test_client().get("/automation/new?shape=long_call").get_data(as_text=True)
    assert '<input type="hidden" name="group-0-rests" value="target">' in html  # a bought leg


def test_a_schwab_bot_isnt_offered_the_choice(schwab_app):
    bot_id = _schwab_bot(schwab_app)
    html = schwab_app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert '<select name="group-0-rests"' not in html
    assert '<input type="hidden" name="group-0-rests"' in html


def test_a_futures_bot_isnt_offered_the_choice(app, monkeypatch):
    monkeypatch.setattr(automation, "_listed", lambda ticker: True)
    response = app.test_client().post("/automation/save", data=futures_form(app), headers=ORIGIN)
    assert response.status_code == 302, response.get_data(as_text=True)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert '<select name="group-0-rests"' not in html
    assert '<input type="hidden" name="group-0-rests"' in html
    assert "Profit target rests at TastyTrade" in html


def test_a_bot_saved_through_the_page_keeps_its_target_resting(app):
    client = app.test_client()
    data = strangle_form(app, **{"group-0-rest-shown": "1", "group-0-target": "50"})  # unticked
    assert client.post("/automation/save", data=data, headers=ORIGIN).status_code == 302
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    html = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="group-0-rest-stop"' in html  # offered, unticked; the put side's is ticked
    assert 'name="group-0-rest-stop" checked' not in html
    assert 'name="group-1-rest-stop" checked' in html
    again = client.post("/automation/save", data=posted_back(html), headers=ORIGIN)
    assert again.status_code == 302, again.get_data(as_text=True)
    groups = bot_store.load(sf(app), bot_id).groups
    assert [g.broker_exit for g in groups] == ["target", "stop"]


def test_the_run_page_shows_the_side_what_it_priced_and_each_groups_note(app):
    note = "Opened for a debit; the closing order is ignored."
    log = [{"rests": "target"}, {"note": note}]
    run_id = _run_with(app, [], exits=[("target", "working", log)])
    with sf(app)() as session:
        run = session.get(BotRun, run_id)
        run.fill_credit, run.priced = Decimal("-1.80"), "Priced as a debit of 1.80. G: …"
        # held: the run page shows its group
        run.filled_legs = [{"symbol": "SPXW  261014P05800000", "side": "sell",
                            "fill_price": "1.80"}]  # fmt: skip
        bot_id = run.bot_id
        session.commit()
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Opened for" in html and "a debit" in html
    assert "Priced as a debit of 1.80" in html
    assert note in html
    # a debit fill's target may be past 100%: the Change target box says so
    assert 'name="pct" min="1" max="1000"' in html and 'name="pct" min="1" max="99"' not in html
    assert "Target % gained" in html and "Target % kept" not in html
    assert "<th>Entry price</th>" not in html  # the runs list's header, not here
    runs = app.test_client().get(f"/automation/{bot_id}/runs").get_data(as_text=True)
    assert "<th>Entry price</th>" in runs and "<th>Fill price</th>" in runs


def test_a_credit_fills_target_box_stops_at_99(app):
    run_id = _run_with(app, [], exits=[("target", "armed", [])])
    with sf(app)() as session:
        run = session.get(BotRun, run_id)
        run.fill_credit = Decimal("1.30")
        run.filled_legs = [{"symbol": "SPXW  261014P05800000", "side": "sell",
                            "fill_price": "1.30"}]  # fmt: skip
        session.commit()
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Opened for</dt><dd>a credit" in html
    assert 'name="pct" min="1" max="99"' in html
    assert "Target % kept" in html


def test_a_futures_groups_note_isnt_said_twice(app):
    run_id = _futures_run(app, status="filled")
    with sf(app)() as session:
        target = session.scalar(select(BotExit).where(BotExit.run_id == run_id))
        target.log = [{"note": "Futures: profit target only, no stop"}]
        session.commit()
    html = app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)
    assert "Futures: profit target only, no stop" in html  # said once, in the run facts
    assert 'notice--warning small">Futures: profit target only' not in html


def _filled(app, run_id, fill="-1.80"):
    with sf(app)() as session:
        run = session.get(BotRun, run_id)
        run.fill_credit = Decimal(fill)
        run.filled_legs = [{"symbol": "SPXW  261014P05800000", "side": "sell",
                            "fill_price": "1.80"}]  # fmt: skip
        session.commit()
    return app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)


def test_a_resting_target_says_what_its_stop_is(app):
    with_stop = _run_with(app, [], exits=[
        ("target", "working", [{"rests": "target"}]), ("stop", "watching", [])])  # fmt: skip
    with sf(app)() as session:
        for row in session.scalars(select(BotExit).where(BotExit.run_id == with_stop)):
            row.broker_order_id = "77" if row.kind == "target" else None
            row.trigger = Decimal("-3.60") if row.kind == "stop" else None
        session.commit()
    html = _filled(app, with_stop)
    assert "Target rests at TastyTrade (order 77); the stop is watched by the app." in html
    alone = _run_with(
        app, [{"event": "a second run"}], exits=[("target", "working", [{"rests": "target"}])]
    )
    with sf(app)() as session:
        session.scalar(select(BotExit).where(BotExit.run_id == alone)).broker_order_id = "78"
        session.commit()
    html = _filled(app, alone)
    assert "Target rests at TastyTrade (order 78); this group has no stop." in html
    assert "the stop is watched by the app" not in html


def test_a_group_resting_its_stop_shows_no_resting_target_line(app):
    run_id = _run_with(app, [], exits=[("stop", "working", []), ("target", "armed", [])])
    with sf(app)() as session:
        for row in session.scalars(select(BotExit).where(BotExit.run_id == run_id)):
            row.broker_order_id = "79" if row.kind == "stop" else None
        session.commit()
    html = _filled(app, run_id, "1.30")
    assert "Target rests at TastyTrade" not in html


def test_a_zero_fill_isnt_called_a_debit(app):
    html = _filled(app, _run_with(app, []), "0")
    assert "Opened for" not in html


def test_the_link_button_puts_a_linked_leg_below_its_leg(app):
    """#115 (Tracker2's 🔗): a sold call's link adds a bought call 5 points above it, right
    under it, and a later row's typed text moves down with its row."""
    client = app.test_client()
    data = form(app, **{"leg_count": "4", "group_count": "2"})
    data = {k: v for k, v in data.items() if not k.startswith("leg-3-")} | {"leg_count": "3"}
    data |= {"leg-2-target": "2,90", "op": "link-leg-0"}
    html = client.post("/automation/save", data=data, headers=ORIGIN).get_data(as_text=True)
    back = posted_back(html)
    assert back["leg_count"] == "4"
    assert (back["leg-1-side"], back["leg-1-right"], back["leg-1-pick"]) == (
        "buy",
        "call",
        "offset",
    )
    assert (back["leg-1-target"], back["leg-1-link"]) == ("5", "0")
    assert back["leg-3-target"] == "2,90"
    assert back["leg-2-link"] == "0"  # the old wing still measures from the sold call
    assert 'value="link-leg-' not in html  # four legs: no room for another


def test_a_link_button_shows_only_on_a_leg_that_can_be_measured_from(app):
    data = form(app, **{"leg_count": "4", "group_count": "2"})
    data = {k: v for k, v in data.items() if not k.startswith("leg-3-")} | {"leg_count": "3"}
    html = app.test_client().post("/automation/save", data=data | {"op": "remove-leg-2"},
                                  headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert 'value="link-leg-0"' in html and 'value="link-leg-1"' not in html


def test_the_legs_headings_carry_their_help_and_one_group_has_no_belongs_to(app):
    """#115: the help is in each heading's ⓘ; with one group the legs post it hidden."""
    client = app.test_client()
    html = client.get("/automation/new?shape=long_call").get_data(as_text=True)
    for tip in ("bot-leg-pick", "bot-leg-target", "bot-leg-from", "bot-leg-max", "bot-exact-dte"):
        assert f'id="tip-{tip}"' in html
    assert "Strike picking" in html and "js/leg_rows.js" in html
    assert 'aria-label="Belongs to"' not in html
    assert posted_back(html)["leg-0-group"] == "0"
    html = client.get("/automation/new").get_data(as_text=True)  # an iron condor: 2 groups
    # #120: no Belongs to column; a leg moves group with its arrows under Groups
    assert 'aria-label="Belongs to"' not in html and 'value="move-leg-0-down"' in html


def test_one_group_reads_as_the_whole_trade(app):
    """#116 (Tracker2's Use leg groups): off, the section is the trade's stop and target."""
    html = app.test_client().get("/automation/new?shape=long_call").get_data(as_text=True)
    section = html[html.index('id="groups"') : html.index('id="exits"')]
    assert "Stop and target" in section and 'aria-label="Group name"' not in section
    assert (
        'name="use_leg_groups"' in section
        and 'name="use_leg_groups" data-use-groups checked' not in section
    )
    assert 'value="add-group"' not in section and 'aria-label="Group name"' not in section
    assert "Use profit actions on this trade" in section
    html = app.test_client().get("/automation/new").get_data(as_text=True)  # an iron condor
    section = html[html.index('id="groups"') : html.index('id="exits"')]
    assert 'name="use_leg_groups" data-use-groups checked' in section
    assert 'aria-label="Group name"' in section  # #120: each group's name over its legs
    assert 'value="add-group"' in section


def test_leg_groups_off_merges_every_leg_into_the_first_group(app):
    client = app.test_client()
    data = form(app, **{"leg_count": "4", "group_count": "2", "group-1-stop": "1,5",
                        "op": "one-group"})  # fmt: skip
    html = client.post("/automation/save", data=data, headers=ORIGIN).get_data(as_text=True)
    back = posted_back(html)
    assert back["group_count"] == "1" and back["group-0-name"] == "Whole trade"
    assert {back[f"leg-{i}-group"] for i in range(4)} == {"0"}
    assert "group-1-stop" not in back
    assert "one stop and one target for the whole trade" in html and "Call side" not in html
    with sf(app)() as session:
        assert session.scalar(select(Bot)) is None  # nothing saved
    html = client.post("/automation/save", data=back.to_dict() | {"op": "use-groups"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert posted_back(html)["group_count"] == "2"


def test_the_groups_headings_carry_their_help_and_mark_what_hides(app):
    """#118 (Tracker2's renderGroups): the help is in each heading's ⓘ, and the boxes a
    choice turns on are marked for group_exits.js; every one is still posted."""
    html = app.test_client().get("/automation/new").get_data(as_text=True)  # iron condor
    section = html[html.index('id="groups"') : html.index('id="exits"')]
    for tip in ("rests-0", "stop-1", "target-0", "after-1", "pa-at-0", "pa-close-1", "pa-stop-0",
                "closing-order"):  # fmt: skip
        assert f'id="tip-bot-group-{tip}"' in section or f'id="tip-bot-{tip}"' in section, tip
    assert "A group with one sold leg and other legs: after its stop fills" not in section
    # "pair" is only a lone short's (0054), "limit" only an old limit's (#127)
    for part in ("remainder", "profit", "co-amount"):
        assert f'data-exit-show="{part}"' in section, part
    assert "js/group_exits.js" in html
    back = posted_back(html)
    for name in ("group-0-remainder-limit", "group-0-pair", "pa-0-0-at", "stop_co_amount"):
        assert name in back, name


def test_every_place_a_group_is_named_follows_its_name_box(app):
    """#119: group_exits.js renames a group everywhere it's named, from its name box."""
    html = app.test_client().get("/automation/new").get_data(as_text=True)  # iron condor
    assert 'data-group-name="0"' in html and 'data-group-name="1"' in html
    labels = re.findall(r'data-group-label="(\d)"[^>]*>([^<]*)<', html)
    # #120: its panel's heading (an iron condor's sides offer no "also close", 0054)
    assert labels.count(("0", "Call side")) == 1
    assert labels.count(("1", "Put side")) == 1


def test_a_spread_says_its_stop_is_watched_and_offers_no_rests_choice(app):
    """Decision 0054: a group of more than one leg can't rest its stop."""
    html = app.test_client().get("/automation/new?shape=put_credit_spread").get_data(as_text=True)
    section = html[html.index('id="groups"') : html.index('id="exits"')]
    assert '<select name="group-0-rests"' not in section
    assert '<input type="hidden" name="group-0-rests" value="target">' in section
    assert 'data-exit-show="watched-note">Stop watched by the app' in section
    assert "rest a stop at the broker" in section and 'name="group-0-rest-stop"' not in section


def test_a_lone_short_keeps_the_choice(app):
    html = app.test_client().get("/automation/new?shape=short_put").get_data(as_text=True)
    assert 'name="group-0-rest-stop"' in html and 'data-exit-show="rest-stop"' in html
    assert 'data-exit-show="watched-note"' not in html


def test_only_a_lone_short_can_close_another_group_and_the_remainder_is_for_the_closed(app):
    html = app.test_client().get("/automation/new").get_data(as_text=True)  # iron condor
    # its sides are spreads: no "also close", each posts an empty pairing
    assert '<select name="group-0-pair"' not in html
    assert '<input type="hidden" name="group-0-pair" value="">' in html
    assert "When another group's stop closes it:" in html
    assert 'data-exit-show="remainder"' in html
    data = strangle_form(app)
    html = app.test_client().post("/automation/save", data=data | {"op": "add-leg"},
                                  headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert '<select name="group-0-pair"' in html  # two lone shorts: each may pair


def test_a_bot_switched_off_by_migration_0034_says_why_until_armed(app):
    bot_id = _saved_bot_id(app)
    with sf(app)() as session:
        session.get(Bot, bot_id).notice = "This bot was switched off and back to preview."
        session.commit()
    client = app.test_client()
    assert "This bot was switched off and back to preview." in client.get(
        f"/automation/{bot_id}").get_data(as_text=True)  # fmt: skip
    client.post(
        f"/automation/{bot_id}/arm",
        data={"on": "1", "understood": "1", "confirmed": "1"},
        headers=ORIGIN,
    )
    assert 'id="switched-off"' not in client.get(f"/automation/{bot_id}").get_data(as_text=True)


def test_a_legs_arrows_move_it_between_groups_and_save_nothing(app):
    """#120 (OptionOmega's leg groups): each group lists its legs as cards; ▲ and ▼ move a
    leg to the group above or below and redraw the form."""
    client = app.test_client()
    html = client.get("/automation/new").get_data(as_text=True)  # iron condor
    assert 'value="move-leg-0-up" formnovalidate disabled' in html  # the first group's
    assert 'value="move-leg-3-down" formnovalidate disabled' in html  # the last group's
    assert "Sell Call" in html and "Buy Put" in html
    html = client.post("/automation/save", data=form(app) | {"op": "move-leg-1-down"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    back = posted_back(html)
    assert [back[f"leg-{i}-group"] for i in range(4)] == ["0", "1", "1", "1"]
    assert "Stop and target: <span" in html
    with sf(app)() as session:
        assert session.scalar(select(Bot)) is None


def test_a_closed_groups_legs_are_closed_never_sold_at_a_limit(app):
    """#127: Market closed, Chased closed or Left alone; a bot saved with the limit keeps
    it, marked, until changed."""
    html = app.test_client().get("/automation/new").get_data(as_text=True)
    assert _options(html, "group-0-remainder") == [
        ("close_market", "Market closed", True), ("close_chase", "Chased closed", False),
        ("leave", "Left alone", False),
    ]  # fmt: skip
    assert 'name="group-0-remainder-limit" inputmode' not in html
    data = form(app, **{"group-1-remainder": "close_limit", "group-1-remainder-limit": "0.10",
                        "op": "add-leg"})  # fmt: skip
    html = app.test_client().post("/automation/save", data=data, headers=ORIGIN).get_data(
        as_text=True)  # fmt: skip
    assert ("close_limit", "At a limit (no longer offered)", True) in _options(
        html, "group-1-remainder")  # fmt: skip
    assert posted_back(html)["group-1-remainder-limit"] == "0.10"


def test_profit_actions_have_three_columns_one_blank_row_and_an_example(app):
    """#128: the new stop's unit sits beside it; saved rows and one blank one, more by
    + Add profit action; Close % and the new stop are each optional."""
    html = app.test_client().get("/automation/new?shape=short_put").get_data(as_text=True)
    assert 'name="pa-0-0-at"' in html and 'name="pa-0-1-at"' not in html
    assert '<input type="hidden" name="group-0-profits" value="1"' in html
    assert "Close % (optional)" in html and "Then move the stop to (optional)" in html
    assert "<th>In</th>" not in html and "data-add-profit" in html
    assert "<td>Move the stop to break-even once it's 75% up, closing none</td><td>75</td>" in html


def test_a_lone_short_is_not_offered_group_premium(app):
    """#130: on one leg, group premium is the same as leg premium."""
    html = app.test_client().get("/automation/new?shape=short_put").get_data(as_text=True)
    assert [v for v, _, _ in _options(html, "group-0-stop-basis")] == [
        "leg_premium", "entry_premium", "closing_order",
    ]  # fmt: skip
    html = app.test_client().get("/automation/new?shape=put_credit_spread").get_data(as_text=True)
    assert "group_entry" in [v for v, _, _ in _options(html, "group-0-stop-basis")]


def test_advanced_closing_orders_show_only_with_leg_groups(app):
    """#132: one group's credit is the whole trade's, so the overlay needs leg groups."""
    client = app.test_client()
    html = client.get("/automation/new").get_data(as_text=True)  # iron condor: 2 groups
    assert "Advanced closing orders (optional)" in html and '<table class="tip-table">' in html
    html = client.get("/automation/new?shape=short_put").get_data(as_text=True)
    assert "Advanced closing orders" not in html
    assert '<input type="hidden" name="stop_co_mode" value="off">' in html


def test_bot_pages_name_accounts_by_their_broker_nickname(schwab_app):
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    with sf(schwab_app)() as session:
        session.add(Account(broker="tastytrade", number="5WX01234", nickname="Income",
                            updated_at=now))  # fmt: skip
        session.commit()
    client = schwab_app.test_client()
    new = client.get("/automation/new").get_data(as_text=True)
    assert "Income ••1234" in new and "Schwab ••5678" in new  # no nickname: the broker
    assert client.post("/automation/save", data=form(schwab_app), headers=ORIGIN).status_code == 302
    assert "Income ••1234" in client.get("/automation").get_data(as_text=True)
    live = client.get(f"/automation/{saved_id(schwab_app)}/live").get_data(as_text=True)
    assert "TastyTrade account Income ••1234" in live


def test_a_paper_bot_is_chipped_paper_and_never_preview(app):
    bot_id = _saved_bot_id(app)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    row = app.test_client().get("/automation").get_data(as_text=True)
    assert 'title="Paper: go Live"' in row and 'badge--preview">Preview</span>' not in row
    page = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert ">Arm it<" in page and "sends real orders)" not in page


def test_arming_a_live_bot_goes_through_the_confirm_step(app):
    bot_id = _saved_bot_id(app)
    client = app.test_client()
    url = f"/automation/{bot_id}/arm"
    r = client.post(url, data={"on": "1", "next": "list"}, headers=ORIGIN)
    assert "/live?arm=1" in r.headers["Location"]
    assert not bot_store.load(sf(app), bot_id).enabled
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "real orders" in page and 'name="understood"' in page and "Arm it" in page
    client.post(url, data={"on": "1", "understood": "1", "confirmed": "1"}, headers=ORIGIN)
    assert bot_store.load(sf(app), bot_id).enabled


def test_a_paper_bot_arms_in_one_click(app):
    bot_id = _saved_bot_id(app)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    client = app.test_client()
    client.post(
        f"/automation/{bot_id}/arm",
        data={"on": "1", "understood": "1", "confirmed": "1"},
        headers=ORIGIN,
    )
    assert bot_store.load(sf(app), bot_id).enabled


def test_a_schwab_paper_bot_keeps_the_tick_the_box_flow(schwab_app):
    bot_id = _schwab_bot(schwab_app)
    bot_store.set_mode(sf(schwab_app), bot_id, "paper", datetime.now(UTC))
    client = schwab_app.test_client()
    client.post(f"/automation/{bot_id}/arm", data={"on": "1"}, headers=ORIGIN)
    assert not bot_store.load(sf(schwab_app), bot_id).enabled
    assert "Tick the box" in client.get(f"/automation/{bot_id}").get_data(as_text=True)
    client.post(f"/automation/{bot_id}/arm", data={"on": "1", "understood": "1"}, headers=ORIGIN)
    assert bot_store.load(sf(schwab_app), bot_id).enabled


SHORT_PUT = ({"symbol": "SPXW  261013P05700000", "side": "sell", "ratio": 1, "group": 0,
              "position": 0, "strike": "5700", "right": "put"},)  # fmt: skip


def test_a_paper_run_page_has_no_manage_controls(app):
    """Final review M1: nothing at the broker to close or change."""
    paper = _run_page_for(app, paper=True, legs=SHORT_PUT)
    assert "Close entire trade" not in paper and "This is a paper trade" in paper


def test_an_old_preview_run_page_keeps_its_manage_controls(app):
    assert "Close entire trade" in _run_page_for(app, paper=False, legs=SHORT_PUT)


def test_arming_a_live_bot_needs_the_box_ticked_server_side(app):
    """Final review I2: the hidden confirmed=1 alone doesn't arm."""
    bot_id = _saved_bot_id(app)  # a new bot is Live
    client = app.test_client()
    r = client.post(f"/automation/{bot_id}/arm", data={"on": "1", "confirmed": "1"},
                    headers=ORIGIN)  # fmt: skip
    assert r.headers["Location"].startswith(f"/automation/{bot_id}/live?arm=1")
    assert not bot_store.load(sf(app), bot_id).enabled
    page = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "Tick the box to show you understand this bot sends real orders." in page


def test_switching_an_armed_paper_bot_to_live_disarms_it(app):
    """Final review I1: armed again only through the go-live summary and its box."""
    bot_id = _saved_bot_id(app)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    client = app.test_client()
    r = client.post(f"/automation/{bot_id}/arm", data={"on": "1", "understood": "1"},
                    headers=ORIGIN)  # fmt: skip
    assert bot_store.load(sf(app), bot_id).enabled, r.headers  # a Paper bot arms without the summary
    client.post(f"/automation/{bot_id}/live", data=LIVE_TICKED,
                headers=ORIGIN)  # fmt: skip
    bot = bot_store.load(sf(app), bot_id)
    assert bot.live and not bot.enabled
    page = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "It&#39;s Live and switched off" in page
    r = client.post(f"/automation/{bot_id}/arm", data={"on": "1", "understood": "1"},
                    headers=ORIGIN)  # fmt: skip
    assert r.headers["Location"].startswith(f"/automation/{bot_id}/live?arm=1")
    assert not bot_store.load(sf(app), bot_id).enabled


@pytest.mark.parametrize("error", [RuntimeError, ValueError])
def test_a_failed_switch_to_live_stays_paper_and_says_so(app, monkeypatch, error):
    """Final review I3: no 500 when the paper trades can't all be closed."""
    bot_id = _saved_bot_id(app)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))

    def fail(*args, **kwargs):
        raise error("still open")

    monkeypatch.setattr(automation.bot_store, "set_mode", fail)
    client = app.test_client()
    r = client.post(f"/automation/{bot_id}/live", data=LIVE_TICKED,
                    headers=ORIGIN)  # fmt: skip
    assert r.status_code == 302 and not bot_store.load(sf(app), bot_id).live
    page = client.get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "Couldn&#39;t close the paper trades, so the bot stays in Paper." in page


def test_match_trades_files_the_ticked_trades_and_undoes(app):
    from tests.services.bot_fakes import make_shape
    from tests.services.test_journal import PUT_KEY, FakeBroker

    from optionsjourney.db.models import BotEntryTime
    from optionsjourney.services.sync import sync_broker

    factory = sf(app)
    sync_broker(FakeBroker(), factory)
    bot_id = make_shape(factory, "short_put")
    with factory() as session:  # the test trades open at now-based times
        session.query(BotEntryTime).filter_by(bot_id=bot_id).delete()
        session.commit()
    client = app.test_client()
    assert "Match trades" in client.get(f"/automation/{bot_id}").get_data(as_text=True)
    html = client.get(f"/automation/{bot_id}/match").get_data(as_text=True)
    assert f'value="{PUT_KEY}"' in html and "Not filed yet (1)" in html and "a guess" in html
    assert "Undo" not in html
    response = client.post(f"/automation/{bot_id}/match", data={"key": PUT_KEY},
                           headers=ORIGIN, follow_redirects=True)  # fmt: skip
    html = response.get_data(as_text=True)
    assert "Linked 1 trade to this bot" in html and "No trades to match" in html and "Undo" in html
    html = client.post(f"/automation/{bot_id}/match/undo", headers=ORIGIN,
                       follow_redirects=True).get_data(as_text=True)  # fmt: skip
    assert "Undone: 1 trade" in html and "Not filed yet (1)" in html
    assert client.get("/automation/999/match").status_code == 404


def test_an_imported_bot_offers_to_find_its_past_trades(app):
    bot_id = saved_bot(app)
    html = app.test_client().get(f"/automation/{bot_id}?imported=1").get_data(as_text=True)
    assert "Find this bot&#39;s past trades" in html or "Find this bot's past trades" in html


def test_split_iron_condors_shows_for_a_condor_with_a_group_per_leg(app):
    # #165: a Tracker2 MEIC bot (each leg its own group) can still be split.
    from tests.bots.test_definition import per_leg

    bot_id = _saved_shape(app, "iron_condor")
    bot = per_leg(bot_store.load(sf(app), bot_id))
    assert bot_store.save(sf(app), bot, datetime.now(UTC))[1] == {}
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="split"' in html and "Split iron condors" in html


def test_match_all_bots_links_the_ticked_trades_and_undoes(app):
    """#169: one page for every live bot; the journal then shows the bot's name."""
    from tests.services.bot_fakes import make_shape
    from tests.services.test_journal import PUT_KEY, FakeBroker

    from optionsjourney.db.models import Bot, BotEntryTime
    from optionsjourney.services.sync import sync_broker

    factory = sf(app)
    sync_broker(FakeBroker(), factory)
    bot_id = make_shape(factory, "short_put", live=True)
    with factory() as session:
        session.query(BotEntryTime).filter_by(bot_id=bot_id).delete()
        session.get(Bot, bot_id).name = "Put seller"
        session.commit()
    client = app.test_client()
    assert "Match all bots" in client.get("/automation").get_data(as_text=True)
    html = client.get("/automation/match").get_data(as_text=True)
    assert "Put seller</a> (1)" in html and f'value="{bot_id}:{PUT_KEY}"' in html
    assert "1234" in html  # the account's last digits, as the app shows them
    response = client.post("/automation/match", data={"key": f"{bot_id}:{PUT_KEY}"},
                           headers=ORIGIN, follow_redirects=True)  # fmt: skip
    html = response.get_data(as_text=True)
    assert "Matched 1 trade" in html and "Undo" in html and "No trades to match" in html
    journal_html = client.get("/journal?status=all").get_data(as_text=True)
    assert "badge--matched" in journal_html and "Matched to Put seller" in journal_html
    assert ">matched" in journal_html  # #276: an outlined word, not the bot icon
    html = client.post("/automation/match/undo", headers=ORIGIN,
                       follow_redirects=True).get_data(as_text=True)  # fmt: skip
    assert "Undone: 1 trade" in html and "Put seller</a> (1)" in html


def test_a_saved_bots_page_opens_with_its_summary(app):
    bot_id = _saved_bot_id(app)
    page = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'id="summary"' in page and "Execution" in page and "Trades | Winners" in page
    assert "Strategy</div>" not in page
    new = app.test_client().get("/automation/new").get_data(as_text=True)
    assert 'id="summary"' not in new


def test_a_bots_page_has_a_folded_pl_graph_from_zero(app, monkeypatch):
    """#208 (Tracker2 f52ae8a0, ca044d27): the cumulative P/L graph sits in the summary,
    folded until opened, and starts at $0 so a bot with one trade still draws a line."""
    from optionsjourney.core.stats import journal_summary, trade_curve
    from optionsjourney.services import bot_stats

    bot_id = _saved_bot_id(app)
    page = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'id="pl-graph"' not in page  # no closed trades: nothing to draw

    closed = datetime(2026, 10, 19, 19, 45, tzinfo=UTC)  # 3:45 PM in New York
    curve = trade_curve([(closed, Decimal("-406"))])
    stats = bot_stats.Stats(journal_summary([]), 1, curve=curve)
    monkeypatch.setattr(bot_stats, "summaries", lambda *a: {bot_id: stats})
    page = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert '<details class="bot-graph" id="pl-graph">' in page  # folded until opened
    assert "line_chart.js" in page and "bot_pl_graph.js" in page
    data = json.loads(page.split('id="pl-graph-data">')[1].split("</script>")[0])
    assert data == [
        {"label": "Start", "pl": None, "cumulative": 0.0},
        {"day": "2026-10-19", "label": "Mon Oct 19, 2026 3:45 PM", "pl": -406.0,
         "cumulative": -406.0},
    ]  # fmt: skip


def _two_bots(app):
    client = app.test_client()
    for name in ("First", "Second"):
        client.post("/automation/save", data=form(app, name=name), headers=ORIGIN)
    with sf(app)() as session:
        return client, [str(i) for i in session.scalars(select(Bot.id).order_by(Bot.id))]


def test_bulk_arm_lists_the_bots_first_then_turn_off_at_once(app):
    client, ids = _two_bots(app)
    html = client.get("/automation").get_data(as_text=True)
    assert 'formaction="/automation/arm-many"' in html
    assert 'formaction="/automation/mode-many"' in html
    assert "data-tick-all" in html  # select all / none
    page = client.post("/automation/arm-many", data={"bot_id": ids, "on": "1"}, headers=ORIGIN)
    text = page.get_data(as_text=True)
    assert page.status_code == 200 and "Arm 2 bots?" in text
    assert "First" in text and "Second" in text
    assert not any(bot_store.load(sf(app), int(i)).enabled for i in ids)  # nothing armed yet
    client.post("/automation/arm-many", data={"bot_id": ids, "on": "1", "confirmed": "1"},
                headers=ORIGIN)  # fmt: skip  (the box isn't ticked)
    assert not any(bot_store.load(sf(app), int(i)).enabled for i in ids)
    r = client.post("/automation/arm-many", headers=ORIGIN,
                    data={"bot_id": ids, "on": "1", "confirmed": "1",
                          "understood": "1"})  # fmt: skip
    assert r.headers["Location"] == "/automation"
    assert all(bot_store.load(sf(app), int(i)).enabled for i in ids)
    client.post("/automation/arm-many", data={"bot_id": ids, "on": "0"}, headers=ORIGIN)
    assert not any(bot_store.load(sf(app), int(i)).enabled for i in ids)


def test_bulk_paper_at_once_and_live_after_its_page(app):
    client, ids = _two_bots(app)
    paper = {"bot_id": ids, "mode": "paper"}
    r = client.post("/automation/mode-many", data=paper, headers=ORIGIN)
    assert r.headers["Location"] == "/automation"
    assert not any(bot_store.load(sf(app), int(i)).live for i in ids)
    live = {"bot_id": ids, "mode": "live"}
    page = client.post("/automation/mode-many", data=live, headers=ORIGIN)
    assert "Switch to Live 2 bots?" in page.get_data(as_text=True)
    assert not any(bot_store.load(sf(app), int(i)).live for i in ids)
    client.post("/automation/mode-many", data={**live, "confirmed": "1", "understood": "1",
                                                  "risk": "1"},
                headers=ORIGIN)  # fmt: skip
    bots = [bot_store.load(sf(app), int(i)) for i in ids]
    assert all(b.live and not b.enabled for b in bots)  # Live, switched off


def test_bulk_needs_ticked_bots(app):
    r = app.test_client().post("/automation/mode-many", data={"mode": "paper"}, headers=ORIGIN,
                               follow_redirects=True)  # fmt: skip
    assert "Tick the bots first" in r.get_data(as_text=True)


def test_the_list_puts_bots_under_their_strategy_one_row_across_accounts(app):
    # #216: "Iron Condor - Beta" and "Iron Condor - Roth" in accounts of those names are one row with
    # an account chip each, under the tag their trades are filed under (decision 0072).
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    with sf(app)() as session:
        session.add_all([
            Account(broker="tastytrade", number="5WX01234", nickname="Beta", updated_at=now),
            Account(broker="tastytrade", number="5WX05678", nickname="Roth", updated_at=now),
        ])  # fmt: skip
        session.commit()
    client = app.test_client()
    client.post("/automation/save", data=form(app, name="Iron Condor - Beta"), headers=ORIGIN)
    client.post("/automation/save", headers=ORIGIN,
                data=form(app, name="Iron Condor - Roth", account="tastytrade:5WX05678"))  # fmt: skip
    html = client.get("/automation").get_data(as_text=True)
    assert html.count('class="st-item') == 1 and ">Iron Condor</a>" in html
    assert "Beta ••1234 <b>×1</b>" in html and "Roth ••5678 <b>×1</b>" in html
    assert "<h3>Off</h3>" in html  # neither is armed
    with sf(app)() as session:
        ids = session.scalars(select(Bot.id)).all()
    for bot_id in ids:
        client.post(f"/automation/{bot_id}/arm", headers=ORIGIN,
                    data={"on": "1", "understood": "1", "confirmed": "1"})  # fmt: skip
    html = client.get("/automation").get_data(as_text=True)
    assert "<h3>0DTE</h3>" in html and "2 bots" in html and "2 lots" in html
    assert 'data-schedule-url="/automation/schedule"' in html and "automation_board.js" in html


# --- Tooltips on the Automation list and the run page (#230) -------------------------------

LIST_TIPS = (
    "automation-new-entries", "automation-armed", "automation-integrity", "automation-match-all",
    "automation-search", "automation-group", "automation-bulk-skip", "automation-bulk-arm",
    "automation-bulk-off", "automation-bulk-paper", "automation-bulk-live",
    "automation-needs-look", "automation-restore-missing", "automation-wings",
)  # fmt: skip


def _tipped(html, tip_id):
    return f'id="tip-{tip_id}"' in html and f'aria-describedby="tip-{tip_id}"' in html


def test_the_automation_list_explains_its_own_words(app, monkeypatch):
    from optionsjourney.services import bot_integrity, bot_manage

    client = app.test_client()
    client.post("/automation/save", data=form(app) | EXIT_FIELDS, headers=ORIGIN)
    finding = {"run_id": 7, "bot_id": 1, "bot": "SPX IC", "check": "side_unprotected",
               "summary": "Call side has no working stop. Check your broker.",
               "since": datetime(2026, 10, 13, 15, 0, tzinfo=UTC)}  # fmt: skip
    monkeypatch.setattr(bot_integrity, "open_findings", lambda factory: [finding])
    monkeypatch.setattr(bot_manage, "open_trades", lambda factory, now: [
        {"run_id": 7, "bot_name": "SPX IC", "groups": ["Call side"], "zero_dte": True}
    ])  # fmt: skip
    html = client.get("/automation").get_data(as_text=True)
    for tip_id in LIST_TIPS:
        assert _tipped(html, tip_id), tip_id
    # the hovers that explained these words are gone; icon-only hovers stay
    for gone in ("The app&#39;s checks of its open bot trades", "Change this from the switches",
                 "Link your journal&#39;s past trades", "Every word must match",
                 "Skips the next entry day", "Switches the ticked bots to Paper."):  # fmt: skip
        assert f'title="{gone}' not in html, gone
    assert 'title="Preview order"' in html


def test_the_integrity_box_is_not_a_link_around_its_tip(app):
    html = app.test_client().get("/automation").get_data(as_text=True)
    assert _tipped(html, "automation-integrity")
    assert '<a class="st-stat' not in html  # the box isn't a link: a button can't sit in one


def _run_with_tips(app, *, status="filled", chase=False, exits=True, two_groups=False):
    app.test_client().post("/automation/save", data=form(app) | EXIT_FIELDS, headers=ORIGIN)
    bot_id = _saved_bot_id(app)
    now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
    legs = [{"position": 0, "symbol": "SPXW  261013C05910000", "strike": "5910", "right": "call",
             "side": "sell", "ratio": 1, "group": 0, "mid": "3.05", "delta": "0.12",
             "expiration": "2026-10-13", "dte": 0}]  # fmt: skip
    if two_groups:
        legs.append({**legs[0], "position": 1, "symbol": "SPXW  261013P05690000", "strike": "5690",
                     "right": "put", "group": 1})  # fmt: skip
    with sf(app)() as session:
        run = BotRun(
            bot_id=bot_id, key="bot-1-0947-20261013", trading_date=now.date(), entry_time="09:47",
            status=status, live=True, reason="", legs=legs,
            filled_legs=[] if status == "working" else [{**legs[0], "fill_price": "3.00"}],
            chase_log=[{"try": 1, "price": "1.10"}] if chase else [], contracts=1,
            broker_order_id="123" if status == "working" else None, created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.flush()
        if exits:
            session.add(BotExit(
                run_id=run.id, group_position=0, kind="stop", state="working",
                broker_order_id="55", short_symbol="SPXW  261013C05910000",
                long_symbol="SPXW  261013C05985000", contracts=1, trigger=Decimal("4.80"),
                rearms=1, log=[], created_at=now, updated_at=now,
            ))  # fmt: skip
        session.commit()
        run_id = run.id
    return app.test_client().get(f"/automation/runs/{run_id}").get_data(as_text=True)


# a tip inside the per-group loop carries the group's position, so ids stay unique (group 0 here)
RUN_TIPS = (
    "run-mid", "run-trigger-limit", "run-rearms", "run-missing-exits",
    "run-move-stop-to-0", "run-watch-rules", "run-timed-close", "run-stop-off-0",
    "run-target-off-0", "run-all-exits-off-0", "run-closing-order-0",
)  # fmt: skip


def test_the_run_page_explains_its_own_words(app):
    html = _run_with_tips(app)
    for tip_id in RUN_TIPS:
        assert _tipped(html, tip_id), tip_id
    assert "A stop or target that&#39;s gone" not in html


def test_the_run_page_tip_ids_are_unique(app):
    html = _run_with_tips(app, two_groups=True)
    ids = re.findall(r'id="(tip-[^"]+)"', html)
    assert "tip-run-stop-off-1" in ids  # the second group has its own
    assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)


def test_a_resting_entry_is_explained(app):
    assert _tipped(_run_with_tips(app, status="working"), "run-resting-entry")


def test_the_chase_section_of_a_run_is_explained(app, monkeypatch):
    # the plain Chase list is what the page falls back to when its timeline can't be built
    monkeypatch.setattr(automation, "_timeline", lambda *a, **k: 1 / 0)
    html = _run_with_tips(app, chase=True, exits=False)
    assert 'id="timeline"' not in html and "Chase" in html
    assert _tipped(html, "run-chase")


def test_the_runs_record_and_preview_pages_have_tips(app, monkeypatch):
    from optionsjourney.services.bot_entry import PreviewResult

    bot_id = _saved_bot_id(app)
    client = app.test_client()
    with sf(app)() as session:
        now = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
        session.add(BotRun(bot_id=bot_id, key="k-tip", trading_date=now.date(), entry_time="09:47",
                           status="skipped", live=False, reason="x", legs=[], filled_legs=[],
                           chase_log=[], contracts=0, created_at=now, updated_at=now))  # fmt: skip
        session.commit()
    assert 'id="tip-runs-status"' in client.get(f"/automation/{bot_id}/runs").get_data(as_text=True)
    record = client.get(f"/automation/{bot_id}/record").get_data(as_text=True)
    assert 'id="tip-record-per-lot"' in record
    assert "/help/automation#a-bots-record" in record

    def fake_preview(deps, bot, now=None):
        return PreviewResult(True, "Checked.", [], Decimal("5.02"), Decimal("1200"), None, 1,
                             ("Put spread: stop 4.80 rests at TastyTrade.",))  # fmt: skip

    monkeypatch.setattr(automation.bot_entry, "preview_order", fake_preview)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    html = client.post(f"/automation/{bot_id}/preview", headers=ORIGIN).get_data(as_text=True)
    assert 'id="tip-preview-buying-power"' in html and 'id="tip-preview-exits"' in html


def test_the_live_and_many_pages_have_tips(app):
    client, ids = _two_bots(app)
    page = client.get(f"/automation/{ids[0]}/live").get_data(as_text=True)
    assert 'aria-describedby="tip-live-switch"' in page and 'id="tip-live-switch"' in page
    assert "/help/automation#switching-to-live" in page
    many = client.post("/automation/mode-many", data={"bot_id": ids, "mode": "live"},
                       headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    for tip in ("many-mode", "many-per-trade", "many-per-day", "many-stops"):
        assert f'id="tip-{tip}"' in many
    arm = client.post("/automation/arm-many", data={"bot_id": ids, "on": "1"},
                      headers=ORIGIN).get_data(as_text=True)  # fmt: skip
    assert 'id="tip-many-mode"' in arm
    arm_one = client.get(f"/automation/{ids[0]}/live?arm=1").get_data(as_text=True)
    assert 'id="tip-live-arm"' in arm_one


def test_the_match_pages_explain_why_it_matches(app):
    from tests.services.bot_fakes import make_shape
    from tests.services.test_journal import FakeBroker

    from optionsjourney.db.models import BotEntryTime
    from optionsjourney.services.sync import sync_broker

    factory = sf(app)
    sync_broker(FakeBroker(), factory)
    bot_id = make_shape(factory, "short_put", live=True)
    with factory() as session:
        session.query(BotEntryTime).filter_by(bot_id=bot_id).delete()
        session.commit()
    client = app.test_client()
    one = client.get(f"/automation/{bot_id}/match").get_data(as_text=True)
    assert 'id="tip-match-why-offered"' in one
    assert "/help/automation#finding-a-bots-past-trades" in one
    assert 'id="tip-match-all-why-1"' in client.get("/automation/match").get_data(as_text=True)


def test_the_first_live_bot_asks_the_risk_box_once(app):
    """Decision 0093: the first time any bot goes Live, the user ticks that they're
    responsible for its orders; after that it isn't asked again."""
    bot_id = _saved_bot_id(app)
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    client = app.test_client()
    assert 'name="risk"' in client.get(f"/automation/{bot_id}/live").get_data(as_text=True)
    client.post(f"/automation/{bot_id}/live", data={"on": "1", "understood": "1"}, headers=ORIGIN)
    assert not bot_store.load(sf(app), bot_id).live
    client.post(f"/automation/{bot_id}/live", data=LIVE_TICKED, headers=ORIGIN)
    assert bot_store.load(sf(app), bot_id).live
    bot_store.set_mode(sf(app), bot_id, "paper", datetime.now(UTC))
    assert 'name="risk"' not in client.get(f"/automation/{bot_id}/live").get_data(as_text=True)
    client.post(f"/automation/{bot_id}/live", data={"on": "1", "understood": "1"}, headers=ORIGIN)
    assert bot_store.load(sf(app), bot_id).live


def test_the_footer_says_not_financial_advice(app):
    client = app.test_client()
    html = client.get("/automation").get_data(as_text=True)
    assert "Not financial advice" in html and 'href="/help/disclaimer"' in html
    assert "solely responsible" in client.get("/help/disclaimer").get_data(as_text=True)


def test_outside_demo_the_arm_pages_keep_the_real_order_wording(app):
    client, ids = _two_bots(app)
    bot_id = ids[0]
    client.post(f"/automation/{bot_id}/arm", data={"on": "1", "next": "list"}, headers=ORIGIN)
    page = client.get(f"/automation/{bot_id}/live?arm=1").get_data(as_text=True)
    assert "a Live bot sends real orders to your broker." in page
    assert "pretend broker" not in page
    many = client.post("/automation/arm-many", data={"bot_id": ids, "on": "1"}, headers=ORIGIN)
    assert "Live bots send real orders to your broker" in many.get_data(as_text=True)
    assert "pretend broker" not in many.get_data(as_text=True)


# #314
def test_keep_trying_and_a_fallback_round_trip_through_the_form(app):
    data = form(app, keep_trying_minutes="2", min_short_width="20", use_keep_trying="on",
                use_min_short_width="on",
                **{"leg-0-fallback": "1.00", "leg-2-fallback": "1.00"})  # fmt: skip
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 302, r.get_data(as_text=True)
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    bot = bot_store.load(sf(app), bot_id)
    assert bot.keep_trying_minutes == 2 and bot.legs[0].fallback_target == Decimal("1.00")
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="keep_trying_minutes" inputmode="numeric" value="2"' in html
    assert 'name="leg-0-fallback" inputmode="decimal" value="1.00"' in html
    assert "<th>At the entry</th>" in html and "Enters at 10:00:30" in html  # the ⓘ's examples
    assert "No fallback try: a skipped entry stays skipped." in html  # Fallback's ⓘ table
    assert "&lt;table" not in html  # every ⓘ table renders as a table, not as text
    for row in ("Moved to 5,790 / 5,810.", "Buys 4 ($1,989.20).", "Skipped: up more than 3%."):
        assert row in html


def test_saving_with_a_min_width_and_no_fallback_is_refused(app):
    data = form(app, min_short_width="20", use_min_short_width="on")
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 400
    assert "Enter a fallback premium" in r.get_data(as_text=True)
    with sf(app)() as session:
        assert session.scalar(select(Bot.id)) is None


# #315: Min short width and Keep trying have switches; off keeps the number, unused
def test_switching_off_min_width_and_keep_trying_keeps_their_numbers(app):
    data = form(app, keep_trying_minutes="2", min_short_width="20")  # both switches off
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    assert r.status_code == 302, r.get_data(as_text=True)  # no fallback needed while off
    with sf(app)() as session:
        bot_id = session.scalar(select(Bot.id))
    bot = bot_store.load(sf(app), bot_id)
    assert (bot.min_short_width, bot.keep_trying_minutes) == (Decimal("20"), 2)
    assert (min_width(bot), keep_trying(bot)) == (None, 0)
    html = app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert 'name="use_keep_trying" >' in html and 'name="use_min_short_width" data-min-width-switch >' in html


def test_a_switch_on_with_no_number_is_refused(app):
    data = form(app, use_keep_trying="on", use_min_short_width="on")
    r = app.test_client().post("/automation/save", data=data, headers=ORIGIN)
    body = r.get_data(as_text=True)
    assert r.status_code == 400
    assert "Enter how long to keep trying, or switch it off." in body
    assert "Enter the min short width, or switch it off." in body
