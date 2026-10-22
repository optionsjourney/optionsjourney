"""The whole tour on the demo clock: each step is entered in order, by doing the thing."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from tests.demo.conftest import (
    _advance,
    _arm,
    _bot_id,
    _condor_run,
    _deps,
    _Inline,
    _parts,
    _prime_quotes,
)
from tests.demo.test_tour_facts import HEADERS

from optionsjourney.bots.definition import BotDef, GroupDef, LegDef
from optionsjourney.db.models import Account
from optionsjourney.demo.book import TASTY_ACCOUNT
from optionsjourney.demo.history import CONDOR
from optionsjourney.demo.seed import MANUAL_EXITS, WATCHLIST, WATCHLIST_ID
from optionsjourney.demo.tour_trade import put_spread_tray
from optionsjourney.services import bot_loops, bot_store, journal, manual_bots
from optionsjourney.trading.orders import Side


def test_the_whole_tour_on_a_test_clock(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    client = demo_app.test_client()
    started = demo.clock.now()
    seen = []

    def step():
        n = client.get("/demo/tour").get_json()["step"]
        if not seen or seen[-1] != n:
            seen.append(n)
        return n

    assert step() == 1

    # 1 -> 2: open Automation.
    client.get("/automation")
    assert step() == 2

    # 2 -> 3: arm the condor through the route; it enters and its stop is reached.
    response = client.post(
        f"/automation/{_bot_id(s, CONDOR)}/arm",
        data={"on": "1", "confirmed": "1", "understood": "1"},
        headers=HEADERS,
    )
    assert response.status_code == 302
    assert step() == 3
    _prime_quotes(s)
    bot_loops.clock_pass(deps, spawn=_Inline)
    bot_loops._monitor(deps)
    run = _condor_run(s)
    assert run is not None
    for _ in range(4):
        _advance(s, deps, 60)
    # 3 -> 4: the stop has filled; opening the run page finishes step 3.
    assert step() == 3
    assert client.get(f"/automation/runs/{run.id}").status_code == 200
    assert step() == 4

    # 4 -> 5: review and send the tray's put spread, managed by Put spread exits.
    with s.session_factory() as session:
        account_id = session.scalar(select(Account.id).where(Account.number == TASTY_ACCOUNT))
    [(manual_id, _)] = [
        x for x in manual_bots.listing(s.session_factory) if x[1].name == MANUAL_EXITS
    ]
    cells = put_spread_tray(demo.market, demo.clock)["cells"]
    ticket = {
        "account_id": account_id, "quantity": 1, "tif": "day",
        "price": round(cells[0]["bid"] - cells[1]["ask"], 2) or 0.05,
        "legs": [{"stream_symbol": c["stream_symbol"], "side": c["side"], "ratio": 1}
                 for c in cells],
    }  # fmt: skip
    reviewed = client.post("/api/trade/review", json=ticket, headers=HEADERS).get_json()
    assert reviewed["ok"], reviewed["message"]
    sent = client.post(
        "/api/trade/confirm",
        json={"account_id": account_id, "code": reviewed["code"], "manual_bot_id": manual_id},
        headers=HEADERS,
    ).get_json()
    assert sent["ok"], sent
    for _ in range(6):
        if step() == 5:
            break
        _advance(s, deps, 10)
    assert step() == 5

    # 5 -> 6: the calendar's target fills, about 6 minutes into the day. Usually that's
    # before the visitor gets here (it is in this run); then step 5 waits for Next.
    for _ in range(6):
        view = client.get("/demo/tour").get_json()
        if view["step"] != 5:
            break
        if view["text"].startswith("Your Weekly calendar reached its profit target"):
            client.post("/demo/tour/next", json={"from": 5}, headers=HEADERS)
        else:
            _advance(s, deps, 60)
    assert step() == 6

    # 6 -> 7: change the watchlist.
    response = client.put(
        f"/api/watchlist/groups/{WATCHLIST_ID}",
        json={"symbols": [*WATCHLIST, "SPY"]},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert step() == 7

    # 7 -> finished: save and arm a bot of one's own; it enters.
    own = BotDef(
        None, "My spread", journal.tag_for(s.session_factory, "Credit Spreads").id,
        "tastytrade", TASTY_ACCOUNT, underlying="SPX", contracts=1, max_entries_per_day=1,
        legs=(
            LegDef(0, Side.SELL, "put", pick="spot", target=Decimal(-30)),
            LegDef(1, Side.BUY, "put", pick="offset", target=Decimal(-10), link=0),
        ),
        groups=(GroupDef(0, "Put spread", stop_pct=Decimal(100), target_pct=Decimal(50)),),
        use_floating_entry=True, floating_start="09:40", floating_end="15:00",
    )  # fmt: skip
    _, problems = bot_store.save(s.session_factory, own, demo.clock.now())
    assert not problems, problems
    _arm(s, "My spread")
    _prime_quotes(s)
    bot_loops.clock_pass(deps, spawn=_Inline)
    bot_loops._monitor(deps)
    view = client.get("/demo/tour").get_json()
    seen.append(view["step"])
    assert view["finished"]

    assert seen == [1, 2, 3, 4, 5, 6, 7, 8]
    assert demo.clock.now() - started < timedelta(minutes=30)
