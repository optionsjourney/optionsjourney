from datetime import UTC, datetime, timedelta

import pytest

from optionsjourney.app import create_app

ORIGIN = {"Origin": "http://localhost"}


class FakeSchwab:
    name = "Schwab"
    broker = "schwab"

    def accounts(self):
        return []

    def price_history(self, symbol, params):
        start = datetime(2026, 10, 8, 13, 30, tzinfo=UTC)
        if params["frequencyType"] == "daily":
            return [
                {
                    "datetime": int(datetime(2026, 10, d, 5, tzinfo=UTC).timestamp() * 1000),
                    "open": 1,
                    "high": 2,
                    "low": 1,
                    "close": c,
                    "volume": 1,
                }
                for d, c in ((7, 100), (8, 101))
            ]
        return [
            {
                "datetime": int((start + timedelta(days=1, minutes=5 * n)).timestamp() * 1000),
                "open": 102,
                "high": 103,
                "low": 101,
                "close": 102 + n,
                "volume": 10,
            }
            for n in range(25)
        ]


@pytest.fixture
def client(settings, fake_credentials):
    app = create_app(settings, credentials=fake_credentials, broker_clients=lambda: [FakeSchwab()])
    app.config["TESTING"] = True
    return app.test_client()


def test_charts_is_in_the_nav(client):
    assert 'href="/charts"' in client.get("/journal").get_data(as_text=True)


def test_charts_page_is_tracker2s_grid(client):
    html = client.get("/charts").get_data(as_text=True)
    assert "js/chart_grid/dashboard.js" in html and "GridDashboard" in html
    assert "vendor/lightweight-charts-5.0.0" in html and "vendor/socket.io" in html
    bare = client.get("/chart/grid?bare=1").get_data(as_text=True)
    assert "chart-bare" in bare and "nav__brand" not in bare


def test_chart_grid_runs_edge_to_edge_with_offline_fonts(client):
    # #217: Tracker2's one-toolbar grid, edge to edge under the top bar; its fonts come
    # from the system, never a font CDN (the app works offline).
    html = client.get("/charts").get_data(as_text=True)
    assert "p-0" in html.split("<main", 1)[1].split(">", 1)[0]
    css = client.get("/static/css/chart_grid.css").get_data(as_text=True)
    assert "--cg-nav-h" in css and ".grid-tabs { order: -1;" in css
    assert "@import" not in css and "googleapis" not in css


def test_popout_checks_its_ids(client):
    assert client.get("/chart/grid/popout?layout=abc&pane=p-1").status_code == 200
    assert client.get("/chart/grid/popout?layout=a b&pane=p-1").status_code == 400


def test_price_history_in_chart_bar_format(client):
    body = client.get("/api/chart/pricehistory/spy?frequency=5&period=7").get_json()
    assert body["symbol"] == "SPY" and len(body["candles"]) == 25
    first = body["candles"][0]
    # 13:30 UTC on Oct 9 is 09:30 New York: written as 09:30 UTC
    assert first["time"] == int(datetime(2026, 10, 9, 9, 30, tzinfo=UTC).timestamp())
    assert (first["open"], first["close"], first["volume"]) == (102.0, 102.0, 10.0)
    daily = client.get("/api/chart/pricehistory/SPY?frequency=1440&period=5").get_json()
    assert [c["time"] for c in daily["candles"]] == [
        int(datetime(2026, 10, d, tzinfo=UTC).timestamp()) for d in (7, 8)
    ]


def test_hour_bars_are_built_from_half_hours(client):
    body = client.get("/api/chart/pricehistory/SPY?frequency=60&period=7").get_json()
    times = [c["time"] % 86400 // 60 for c in body["candles"]]
    assert times == [570, 630, 690]  # 09:30, 10:30 and 11:30
    assert body["candles"][0]["volume"] == 120.0  # twelve five-minute bars of 10


def test_layouts_crud_and_one_active(client):
    payload = {"shape": "eq1", "panes": [{"id": "p-1", "symbol": "SPY", "timeframe": "5m"}]}
    made = client.post(
        "/api/chart_layouts", json={"id": "l-1", "name": "One", "payload": payload}, headers=ORIGIN
    )
    assert made.status_code == 201 and made.get_json()["isActive"] is False
    again = client.post(
        "/api/chart_layouts", json={"id": "l-1", "name": "One", "payload": payload}, headers=ORIGIN
    )
    assert again.status_code == 409
    client.post(
        "/api/chart_layouts", json={"id": "l-2", "name": "Two", "payload": payload}, headers=ORIGIN
    )
    client.put("/api/chart_layouts/l-1/activate", headers=ORIGIN)
    client.put("/api/chart_layouts/l-2/activate", headers=ORIGIN)
    active = [x["id"] for x in client.get("/api/chart_layouts").get_json() if x["isActive"]]
    assert active == ["l-2"]
    renamed = client.put("/api/chart_layouts/l-1", json={"name": "Uno"}, headers=ORIGIN)
    assert renamed.get_json()["name"] == "Uno" and renamed.get_json()["payload"] == payload
    blank = client.put("/api/chart_layouts/l-1", json={"name": " "}, headers=ORIGIN)
    assert blank.status_code == 400 and blank.get_json()["error"] is True
    assert client.delete("/api/chart_layouts/l-2", headers=ORIGIN).status_code == 204
    assert client.get("/api/chart_layouts").get_json()[0]["isActive"] is True  # l-1 took over
    assert client.get("/api/chart_layouts/nope").status_code == 404


def test_tabs(client):
    payload = {"shape": "eq1", "panes": []}
    client.post(
        "/api/chart_layouts", json={"id": "l-1", "name": "One", "payload": payload}, headers=ORIGIN
    )
    tab = {"id": "t-1", "layoutId": "l-1", "symbols": {"p-1": "spy"}}
    made = client.post("/api/chart_tabs", json=tab, headers=ORIGIN).get_json()
    assert made == {"id": "t-1", "layoutId": "l-1", "position": 0, "symbols": {"p-1": "SPY"}}
    bad = client.put("/api/chart_tabs/t-1", json={"symbols": {"p-1": "S P Y"}}, headers=ORIGIN)
    assert bad.status_code == 400
    missing = dict(tab, id="t-2", layoutId="nope")
    assert client.post("/api/chart_tabs", json=missing, headers=ORIGIN).status_code == 404
    client.delete("/api/chart_layouts/l-1", headers=ORIGIN)
    assert client.get("/api/chart_tabs").get_json() == []


def test_drawings_saved_checked_and_removed(client):
    line = {"id": "d1", "type": "hline", "p": [{"time": 1, "price": 7000.5, "extra": 1}]}
    saved = client.put(
        "/api/chart_drawings",
        json={"symbol": "spx", "interval": "5m", "drawings": [line]},
        headers=ORIGIN,
    ).get_json()
    assert saved["drawings"] == [{"id": "d1", "type": "hline", "p": [{"time": 1, "price": 7000.5}]}]
    got = client.get("/api/chart_drawings?symbol=SPX&interval=5m").get_json()
    assert got["drawings"] == saved["drawings"]
    trend_with_one_point = dict(line, type="trend")
    bad = client.put(
        "/api/chart_drawings",
        json={"symbol": "SPX", "interval": "5m", "drawings": [trend_with_one_point]},
        headers=ORIGIN,
    )
    assert bad.status_code == 400
    client.put(
        "/api/chart_drawings",
        json={"symbol": "SPX", "interval": "5m", "drawings": []},
        headers=ORIGIN,
    )
    assert client.get("/api/chart_drawings?symbol=SPX&interval=5m").get_json()["drawings"] == []


def test_watchlist_groups(client):
    made = client.post("/api/watchlist/groups", json={"name": "Tech"}, headers=ORIGIN)
    group = made.get_json()
    assert made.status_code == 201 and group["symbols"] == [] and group["sortOrder"] == 0
    updated = client.put(
        f"/api/watchlist/groups/{group['id']}",
        json={"symbols": ["nvda", "NVDA", "/ES"]},
        headers=ORIGIN,
    ).get_json()
    assert updated["symbols"] == ["NVDA", "/ES"]
    bad = client.put(
        f"/api/watchlist/groups/{group['id']}", json={"symbols": ["SPXW 261016"]}, headers=ORIGIN
    )
    assert bad.status_code == 400
    second = client.post("/api/watchlist/groups", json={"name": "Two"}, headers=ORIGIN).get_json()
    order = client.put(
        "/api/watchlist/order", json={"ids": [second["id"], group["id"]]}, headers=ORIGIN
    ).get_json()
    assert [g["name"] for g in order] == ["Two", "Tech"]
    gone = client.delete(f"/api/watchlist/groups/{group['id']}", headers=ORIGIN)
    assert gone.get_json() == {"ok": True}
    assert client.get("/api/watchlist/trading").get_json() == {"symbols": []}


def test_futures_endpoints_without_tastytrade(client):
    assert client.get("/api/chart/futures_contract?root=/ES").status_code == 404
    assert client.get("/api/chart/prev_close?symbol=SPY").status_code == 400


def test_news_without_a_key_reads_yahoo(client, monkeypatch):
    from optionsjourney.services import news

    news.clear_cache()
    monkeypatch.setattr(news, "fetch_yahoo", lambda sym: "<rss><channel></channel></rss>")
    body = client.get("/api/news/dell").get_json()
    assert body == {"symbol": "DELL", "news": []}
    assert "futures" in client.get("/api/news/%24SPX").get_json()["note"]


def test_news_failure_is_a_502_with_the_reason(client, monkeypatch):
    from optionsjourney.services import news

    def fail(session_factory, symbol):
        raise news.NewsError("Finnhub HTTP 500")

    monkeypatch.setattr(news, "company_news", fail)
    res = client.get("/api/news/DELL")
    assert res.status_code == 502
    assert res.get_json() == {"symbol": "DELL", "news": [], "error": "Finnhub HTTP 500"}


def test_charts_page_fonts_are_stored_in_the_app(client):
    from pathlib import Path

    static = Path(client.application.static_folder)
    css = (static / "css/chart_grid.css").read_text()
    assert "fonts.googleapis.com" not in css
    for face in ("ibm-plex-mono", "ibm-plex-sans-condensed"):
        for weight in (400, 500, 600):
            name = f"{face}-latin-{weight}-normal.woff2"
            assert f"../vendor/fonts/{name}" in css and (static / "vendor/fonts" / name).is_file()
