"""Demo mode's pages: the banner, Skip ahead, the end of the day, and the features
that are switched off."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from optionsjourney.app import create_app
from optionsjourney.config import load_settings
from optionsjourney.db.models import Bot, Setting
from optionsjourney.web.routes import demo as demo_routes

BANNER = "Demo · made-up prices · nothing here is real money"
ORIGIN = {"Origin": "http://localhost"}
LIVE_WORDS = "This bot sends orders to the demo's pretend broker. Nothing here is real money."

PAGES = [
    "/",
    "/journal",
    "/journal/paper",
    "/journal/grid",
    "/journal/pl",
    "/journal/metrics",
    "/journal/tags",
    "/charts",
    "/trade",
    "/automation",
    "/automation/import",
    "/transactions",
    "/accounts",
    "/accounts/values",
    "/alerts",
    "/orders",
    "/orders/freeze",
    "/settings",
    "/setup",
    "/setup/tastytrade",
    "/setup/schwab",
    "/help",
    "/help/disclaimer",
]


def _services(app):
    return app.extensions["optionsjourney"]


def _bot_ids(app) -> list[int]:
    with _services(app).session_factory() as session:
        return list(session.scalars(select(Bot.id).order_by(Bot.id)))


def _pages(app) -> list[str]:
    return PAGES + [f"/automation/{i}" for i in _bot_ids(app)]


def test_every_page_has_the_banner(demo_app):
    client = demo_app.test_client()
    named = []
    for url in _pages(demo_app):
        r = client.get(url, follow_redirects=True)
        assert r.status_code == 200, url
        html = r.get_data(as_text=True)
        assert BANNER in html, url
        named += [(url, line.strip()) for line in html.splitlines() if "Tracker2" in line]
        assert 'href="/help/disclaimer"' in html, url
        assert "Skip 15 min" in html and "Skip to the close" in html, url
    assert named == []  # never on a demo page, not even in a script's comment


def test_every_page_has_the_tour_box(demo_app):
    client = demo_app.test_client()
    for url in _pages(demo_app):
        html = client.get(url, follow_redirects=True).get_data(as_text=True)
        assert 'id="demo-tour"' in html, url
        assert 'data-tour-url="/demo/tour"' in html, url


def test_automation_marks_each_bot_by_name_for_the_tour(demo_app):
    html = demo_app.test_client().get("/automation").get_data(as_text=True)
    assert 'data-bot-name="SPX 0DTE condor"' in html
    bot_id = _bot_ids(demo_app)[0]
    page = demo_app.test_client().get(f"/automation/{bot_id}").get_data(as_text=True)
    assert "data-bot-name=" in page and 'id="arm"' in page


def test_step_two_points_at_the_condors_arm_control(demo_app):
    from optionsjourney.demo.tour import STEPS

    selector = STEPS[1].highlight
    assert selector.count('[data-bot-name="SPX 0DTE condor"]') >= 2
    html = demo_app.test_client().get("/automation").get_data(as_text=True)
    assert 'data-act="arm"' in html and 'action="/automation/1/arm"' in html


def test_every_page_and_api_answers_in_demo(demo_app):
    client = demo_app.test_client()
    day = date(2026, 10, 21)
    friday = day + timedelta(days=(4 - day.weekday()) % 7)
    urls = _pages(demo_app) + [
        "/accounts/live",
        "/api/news/SPX",
        "/api/chart/pricehistory/SPX",
        "/api/trade/expirations?ticker=SPX",
        f"/api/trade/chain-table?ticker=SPX&expiration={day.isoformat()}",
        f"/api/trade/chain-table?ticker=SPX&expiration={friday.isoformat()}",
        "/api/trade/expirations?ticker=ZZZZ",
        f"/api/trade/chain-table?ticker=ZZZZ&expiration={day.isoformat()}",
        "/api/news/ZZZZ",
        "/api/chart/pricehistory/ZZZZ",
    ]
    for url in urls:
        r = client.get(url)
        if "ZZZZ" in url and r.status_code in (502, 503):
            # The API's own answer when no broker has the ticker (no chain, no history),
            # as JSON for the page to show; not a failure of the app
            assert r.is_json, url
            continue
        assert r.status_code < 500, url


def test_skip_moves_the_clock(demo_app):
    clock = _services(demo_app).demo.clock
    client = demo_app.test_client()
    before = clock.now()
    r = client.post("/demo/skip", data={"minutes": "15"},
                    headers=ORIGIN | {"Referer": "http://localhost/journal"})  # fmt: skip
    assert r.status_code == 302 and r.headers["Location"] == "/journal"
    assert timedelta(minutes=15) <= clock.now() - before < timedelta(minutes=16)
    r = client.post("/demo/skip", data={"to": "close"}, headers=ORIGIN)
    assert r.status_code == 302
    assert clock.is_over()


def test_after_the_close_the_banner_offers_a_new_day(demo_app, monkeypatch):
    client = demo_app.test_client()
    html = client.get("/journal").get_data(as_text=True)
    assert "The demo's trading day is over" not in html and "Start a new day" not in html
    _services(demo_app).demo.clock.to_close()
    html = client.get("/journal").get_data(as_text=True)
    assert BANNER in html
    assert "The demo's trading day is over" in html and "Start a new day" in html
    assert "Skip 15 min" not in html

    exits = []
    monkeypatch.setattr(demo_routes, "exit_soon", lambda code: exits.append(code))
    r = client.post("/demo/new-day", headers=ORIGIN, buffered=False)
    assert r.status_code == 200 and exits == []  # not before the page has been sent
    assert "Starting a new day" in r.get_data(as_text=True)
    r.close()
    assert exits == [75]


def _setting_rows(app) -> dict:
    with _services(app).session_factory() as session:
        return {row.key: row.value for row in session.scalars(select(Setting))}


SWITCHED_OFF = [
    ("/settings/brokers/tastytrade", {"client_secret": "x", "refresh_token": "y", "risk": "1"}),
    ("/settings/brokers/schwab/start", {"app_key": "k", "app_secret": "s", "risk": "1",
                                        "callback_url": "https://127.0.0.1"}),
    ("/settings/brokers/schwab/finish", {"redirected_url": "https://127.0.0.1/?code=x"}),
    ("/settings/brokers/schwab/disconnect", {}),
    ("/settings/backups/browse", {}),
    ("/settings/backups/now", {}),
    ("/settings/backups/restore", {"name": "x.db"}),
    ("/settings/notifications/test", {}),
    ("/settings/notifications", {"discord_webhook": "https://discord.com/api/webhooks/1/abc"}),
    ("/settings/market_calendar/refresh", {}),
    ("/update/install", {"confirm": "1"}),
    ("/automation/import/preview", {"json": "{}"}),
    ("/automation/import/save", {"json": "{}", "name": "Imported"}),
]  # fmt: skip


@pytest.mark.parametrize(("url", "form"), SWITCHED_OFF)
def test_outside_features_are_off(demo_app, url, form):
    client = demo_app.test_client()
    settings_before = _setting_rows(demo_app)
    bots_before = _bot_ids(demo_app)
    backups = demo_app.extensions["optionsjourney"].settings.data_dir
    files_before = sorted(p.name for p in backups.iterdir())
    r = client.post(url, data=form, headers=ORIGIN | {"Referer": "http://localhost/settings"})
    assert r.status_code == 302, url
    assert r.headers["Location"] == "/settings"
    page = client.get("/settings").get_data(as_text=True)
    assert "Not in the demo" in page
    assert _setting_rows(demo_app) == settings_before
    assert _bot_ids(demo_app) == bots_before
    assert sorted(p.name for p in backups.iterdir()) == files_before


def test_switched_off_api_call_answers_403(demo_app):
    @demo_routes.not_in_demo
    def view():
        return "ran"

    with demo_app.test_request_context("/api/anything", method="POST"):
        response, status = view()
        assert status == 403 and response.get_json() == {"error": "Not in the demo"}


def test_switched_off_buttons_say_so(demo_app):
    client = demo_app.test_client()
    settings = client.get("/settings").get_data(as_text=True)
    assert '<span class="demo-off">Not in the demo</span>' in settings
    for gone in (">Back up now<", ">Send a test<", ">Refresh now<", 'id="browse-folder"'):
        assert gone not in settings, gone
    imported = client.get("/automation/import").get_data(as_text=True)
    assert '<span class="demo-off">Not in the demo</span>' in imported
    assert ">Preview</button>" not in imported


def test_live_wording_in_demo(demo_app):
    client = demo_app.test_client()
    ids = _bot_ids(demo_app)
    html = client.get(f"/automation/{ids[0]}/live?arm=1").get_data(as_text=True)
    assert LIVE_WORDS in html
    assert "I understand this bot sends real orders." not in html
    page = client.post("/automation/arm-many", data={"bot_id": [str(i) for i in ids], "on": "1"},
                       headers=ORIGIN)  # fmt: skip
    assert LIVE_WORDS.replace("This bot sends", "These bots send") in page.get_data(as_text=True)


def test_no_banner_outside_demo(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    client = app.test_client()
    html = client.get("/help").get_data(as_text=True)
    assert BANNER not in html and "Not in the demo" not in html
    assert client.post("/demo/skip", data={"minutes": "15"}, headers=ORIGIN).status_code == 404
    # The switched-off routes work as before outside the demo
    r = client.post("/settings/notifications", data={}, headers=ORIGIN)
    assert r.status_code == 302 and "Not in the demo" not in client.get("/settings").get_data(
        as_text=True
    )


def _words_in_demo_pages(app):
    client = app.test_client()
    ids = _bot_ids(app)
    pages = [client.get(f"/automation/{i}/live?arm=1").get_data(as_text=True) for i in ids]
    pages += [client.get(f"/automation/{i}/live").get_data(as_text=True) for i in ids]
    for arming in ("1", ""):
        data = {"bot_id": [str(i) for i in ids], "on": arming}
        pages.append(client.post("/automation/arm-many", data=data, headers=ORIGIN)
                     .get_data(as_text=True))  # fmt: skip
    return pages


def test_no_demo_page_says_orders_are_real(demo_app):
    for html in _words_in_demo_pages(demo_app):
        assert "real orders" not in html
        assert "orders to your broker" not in html
        assert "sends real" not in html
    page = demo_app.test_client().get(f"/automation/{_bot_ids(demo_app)[0]}/live?arm=1")
    assert "pretend broker" in page.get_data(as_text=True)


def test_many_bots_wording_is_plural_in_demo(demo_app):
    ids = _bot_ids(demo_app)
    page = demo_app.test_client().post(
        "/automation/arm-many", data={"bot_id": [str(i) for i in ids], "on": "1"}, headers=ORIGIN
    )
    html = page.get_data(as_text=True)
    assert "These bots send orders to the demo's pretend broker." in html
    assert "This bot sends" not in html


REAL_ORDER_WORDS = ("real order", "live order to your broker", "your broker accepted")


def _says_real(html: str) -> list[str]:
    low = html.lower()
    return [w for w in REAL_ORDER_WORDS if w in low]


def _bot(app, name: str) -> int:
    with _services(app).session_factory() as session:
        return session.scalar(select(Bot.id).where(Bot.name == name))


def test_no_demo_page_or_message_says_orders_are_real(demo_app):
    """Final review I-1: the bot list, each bot's page, its Preview order, the Trade page
    and the flashed messages all say the demo's pretend broker, never real orders."""
    client = demo_app.test_client()
    ids = _bot_ids(demo_app)
    pages = {"/automation": client.get("/automation"), "/trade": client.get("/trade")}
    for i in ids:
        pages[f"/automation/{i}"] = client.get(f"/automation/{i}")
        pages[f"/automation/{i}/preview"] = client.post(
            f"/automation/{i}/preview", data={"understood": "1"}, headers=ORIGIN,
            follow_redirects=True,
        )  # fmt: skip
    live = _bot(demo_app, "SPX 0DTE condor")
    paper = _bot(demo_app, "QQQ put spread")
    flashes = {
        "arm unticked": client.post(f"/automation/{live}/arm",
                                    data={"on": "1", "confirmed": "1"}, headers=ORIGIN,
                                    follow_redirects=True),
        "trade now, no TRADE": client.post(f"/automation/{live}/trade-now", data={"confirm": ""},
                                           headers=ORIGIN, follow_redirects=True),
        "trade now, paper": client.post(f"/automation/{paper}/trade-now",
                                        data={"confirm": "TRADE"}, headers=ORIGIN,
                                        follow_redirects=True),
    }  # fmt: skip
    for name, r in (pages | flashes).items():
        assert r.status_code == 200, name
        assert _says_real(r.get_data(as_text=True)) == [], name
    unticked = flashes["arm unticked"].get_data(as_text=True).replace("&#39;", "'")
    assert "you understand this bot sends orders to the demo's pretend broker." in unticked
    assert "pretend broker" in pages["/automation"].get_data(as_text=True)
    assert 'data-demo="1"' in pages["/trade"].get_data(as_text=True)
    ticket = client.get("/static/js/trade/trade_ticket.js").get_data(as_text=True)
    for words in ("This sends an order to the demo's pretend broker. Nothing here is real money.",
                  "The demo's pretend broker accepted it.", "Send order (demo)"):  # fmt: skip
        assert words in ticket, words
    page_js = client.get("/static/js/trade/trade_page.js").get_data(as_text=True)
    assert "sendWarning(DEMO)" in page_js and "acceptedWords(DEMO)" in page_js
    assert "This sends a live order to your broker." not in page_js
    assert "Your broker accepted it." not in page_js


def test_going_live_flash_in_demo(demo_app):
    client = demo_app.test_client()
    paper = _bot(demo_app, "QQQ put spread")
    client.post(f"/automation/{paper}/arm", data={"on": "1"}, headers=ORIGIN)
    r = client.post(f"/automation/{paper}/live", data={"on": "1", "risk": "1"}, headers=ORIGIN,
                    follow_redirects=True)  # fmt: skip
    html = r.get_data(as_text=True).replace("&#39;", "'")
    assert "arm it when you're ready to send orders to the demo's pretend broker." in html
    assert _says_real(html) == []


@pytest.mark.parametrize("step", ["tastytrade", "schwab"])
def test_setup_broker_steps_say_not_in_the_demo(demo_app, step):
    """Final review I-2: the pretend brokers are connected; no connect form, never a 500."""
    r = demo_app.test_client().get(f"/setup/{step}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert '<span class="demo-off">Not in the demo</span>' in html
    assert "pretend TastyTrade and a pretend Schwab, already connected" in html
    assert 'name="refresh_token"' not in html and 'name="app_key"' not in html


def test_demo_journal_opens_on_all_trades(demo_app):
    """Final review I-4a: the demo's journal opens on its whole made-up history, not only
    today's trades (outside demo: tests/web/test_journal_pages.py
    test_journal_opens_on_open_trades)."""
    html = demo_app.test_client().get("/journal").get_data(as_text=True)
    assert '<option value="" selected>Open and closed</option>' in html
    assert "Showing open + closed today trades only" not in html
    assert html.count('<tr class="trade-row" data-group="closed"') > 50


def test_local_mode_has_no_tour_box(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [])
    app.config["TESTING"] = True
    html = app.test_client().get("/automation").get_data(as_text=True)
    assert 'id="demo-tour"' not in html
    assert "demo_tour.js" not in html


def test_help_says_its_the_demo(demo_app, tmp_path_factory, fake_credentials):
    note = "You're in the demo. These pages describe the real app"
    assert note in demo_app.test_client().get("/help").get_data(as_text=True)
    assert note in demo_app.test_client().get("/help/search?q=bot").get_data(as_text=True)
    local_settings = load_settings(
        {"OPTIONSJOURNEY_DATA_DIR": str(tmp_path_factory.mktemp("local"))}
    )
    local = create_app(local_settings, credentials=fake_credentials, broker_clients=lambda: [])
    local.config["TESTING"] = True
    assert "in the demo" not in local.test_client().get("/help").get_data(as_text=True)
