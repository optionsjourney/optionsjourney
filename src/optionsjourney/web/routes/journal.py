"""The journal: every trade, dashboards (saved views), editing a trade, and
managing tags."""

import logging
from dataclasses import replace
from datetime import UTC, date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from optionsjourney.bots import adopt as adopt_rules
from optionsjourney.bots import manual as manual_rules
from optionsjourney.bots import stops
from optionsjourney.bots.validation import BROKER_NAMES
from optionsjourney.brokers.credentials.local import LocalProvider
from optionsjourney.core import date_ranges
from optionsjourney.core.holdings import stock_summary
from optionsjourney.core.market import MARKET_TZ, market_date
from optionsjourney.core.positions import Status
from optionsjourney.core.stock_history import RANGES, range_start, stock_transactions, trade_marks
from optionsjourney.core.symbols import parse_occ
from optionsjourney.core.trade_rows import row_details
from optionsjourney.services import (
    bot_adopt,
    bot_integrity,
    bot_loops,
    bot_manage,
    bot_match,
    bot_store,
    chart_market,
    holdings,
    journal,
    manual,
    manual_attach,
    manual_bots,
    paper_journal,
    pl_day,
    setup,
    stock_value,
    transactions,
)
from optionsjourney.services.journal import Filters, JournalError
from optionsjourney.web import services
from optionsjourney.web.pagination import paginate
from optionsjourney.web.routes import risk_graph
from optionsjourney.web.routes.automation import run_context
from optionsjourney.web.security import local_path

bp = Blueprint("journal", __name__)
logger = logging.getLogger(__name__)


def _now():
    return datetime.now(UTC)


TRADES_PER_PAGE = 100


@bp.route("/")
def home():
    # First start on a user's own computer: the setup wizard (#15)
    s = services()
    if isinstance(s.credentials, LocalProvider) and not setup.is_complete(s.session_factory):
        return redirect(url_for("setup.step", name="welcome"))
    return redirect(url_for("journal.journal_page"))


def _closes(wanted):
    s = services()
    return s.close_cache.closes(s.broker_clients(), wanted)


def _estimate(view) -> None:
    """Estimated P/L for expired trades the broker hasn't settled yet (#23)."""
    journal.estimate_expired(view, _closes)


def _dashboard(endpoint: str):
    """The dashboard the page is showing, or a redirect: to its saved
    filters when a tab was just opened, or away from one that's gone."""
    dashboard_id = request.args.get("dashboard", type=int)
    if dashboard_id is None:
        return None, None
    dashboard = journal.get_dashboard(services().session_factory, dashboard_id)
    if dashboard is None:
        return None, redirect(url_for(endpoint))
    if set(request.args) == {"dashboard"} and dashboard.filters:
        # Opening a dashboard tab: show its saved filters. A view saved with
        # none is already showing them (redirecting would loop forever).
        return dashboard, redirect(url_for(endpoint, dashboard=dashboard_id, **dashboard.filters))
    return dashboard, None


@bp.route("/journal")
def journal_page():
    dashboard, response = _dashboard("journal.journal_page")
    if response:
        return response

    filters = Filters.from_args(
        request.args, default_status=journal.trades_status(services().settings.is_demo)
    )
    view = journal.load_journal(
        services().session_factory, filters, cache=services().positions_cache
    )
    if dashboard is None and filters.kind != "stocks":
        # All trades is the options journal: shares live on the Stocks tab
        view.entries = [e for e in view.entries if not journal.is_stock(e.position)]
    _estimate(view)
    sort, direction = request.args.get("sort", "opened"), request.args.get("dir", "desc")
    if sort not in journal.SORTS:
        sort, direction = "opened", "desc"
    # #151: unsorted, the list is Tracker2's: open trades, then closed ones by day
    grouped = "sort" not in request.args
    if grouped:
        view.entries = journal.open_then_closed(view.entries)
    else:
        view.entries = journal.sort_entries(view.entries, sort, direction != "asc")
    stocks, summary, stocks_view, stock_rows = None, None, "holdings", []
    if filters.kind == "stocks":
        stocks = holdings.stock_holdings(
            services().broker_clients(), services().quote_hub, filters.accounts
        )
        summary = _stock_summary(stocks)
        # #286: Holdings or Transaction History; anything else is Holdings
        if request.args.get("stocks_view") == "history":
            stocks_view = "history"
            # since the first day of history, as the Portfolio value chart
            factory = services().session_factory
            since = stock_value.fills_since(stock_value.first_day(factory, datetime.now(UTC)))
            fills = transactions.load_fills(factory, since=since).fills
            stock_rows = stock_transactions(fills, filters.accounts)
    page = paginate(view.entries, TRADES_PER_PAGE)
    rows = journal.with_headers(view.entries, page.items) if grouped else page.items
    return render_template(
        "journal.html",
        rows=rows,
        grouped=grouped,
        bot_chips=_bot_chips(page.items),
        needs_a_look=_needs_a_look(page.items),
        stocks=stocks,
        stock_summary=summary,
        stocks_view=stocks_view,
        stock_rows=stock_rows,
        stock_ranges=list(RANGES),
        row_details=row_details,
        analyzable=journal.analyzable,
        sort=sort,
        direction=direction,
        page_endpoint="journal.journal_page",
        view=view,
        page=page,
        filters=filters,
        dashboard=dashboard,
        periods=journal.PERIODS,
        date_ranges=date_ranges.RANGES,
        broker_names=BROKER_NAMES,
        today=market_date(_now()),
        statuses=journal.STATUSES,
        uncategorized=journal.UNCATEGORIZED,
    )


def _date_arg(name: str) -> date | None:
    try:
        return date.fromisoformat(request.args.get(name, ""))
    except ValueError:
        return None


@bp.get("/journal/paper")
def paper_page():
    """The Journal's Paper trades view (decision 0061): the pretend trades of Paper bots, apart from the
    real journal. A trade still open has no P/L yet, and only closed ones are counted."""
    s = services()
    start, end = _date_arg("start"), _date_arg("end")
    bot_id = request.args.get("bot", type=int)
    tag = request.args.get("tag", "").strip() or None
    trades = paper_journal.trades(s.session_factory, start, end, bot_id, split=True, tag=tag)
    page = paginate(trades, TRADES_PER_PAGE)
    return render_template(
        "journal_paper.html",
        trades=page.items,
        page=page,
        totals=paper_journal.totals(trades),
        bots=bot_store.listing(s.session_factory),
        bot_id=bot_id,
        tags=paper_journal.tags(s.session_factory),
        tag=tag,
        start=start,
        end=end,
        page_endpoint="journal.paper_page",
    )


@bp.get("/api/paper-open-pl")
def paper_open_pl():
    """#183: live P/L of the Paper trades view's open trades and sides, with its filters,
    in /api/open-pl's shape so the page's open_pl.js reads it the same way."""
    s = services()
    trades = paper_journal.trades(
        s.session_factory, _date_arg("start"), _date_arg("end"),
        request.args.get("bot", type=int), split=True,
        tag=request.args.get("tag", "").strip() or None,
    )  # fmt: skip
    values = paper_journal.live_pl(trades, s.quote_hub)
    known = [v for v in values.values() if v is not None]
    return jsonify(
        positions={key: None if v is None else float(v) for key, v in values.items()},
        marks={},
        total=float(sum(known)) if known else None,
        priced=len(known),
        open=len(values),
        streams=s.quote_hub.status(),
    )


def _bot_chips(entries) -> dict:
    """What each bot trade on the page runs with that differs from its bot (bots
    slice 6). The journal still shows if they can't be read: no chips."""
    try:
        return bot_manage.journal_chips(services().session_factory, entries)
    except Exception:  # noqa: BLE001 -- the journal must render
        logging.getLogger(__name__).exception("couldn't read the bot trades' chips")
        return {}


def _needs_a_look(entries) -> dict:
    """#242: {position key: findings} for the open bot trades on the page whose run
    is listed under Needs a look. The journal still shows if they can't be read."""
    runs = {
        e.position.key: e.bot.run_id
        for e in entries
        if getattr(e, "bot", None) is not None
        and e.bot.run_id is not None
        and e.position.status is not Status.CLOSED
    }
    try:
        found = bot_integrity.findings_by_run(services().session_factory, runs.values())
    except Exception:  # noqa: BLE001 -- the journal must render
        logging.getLogger(__name__).exception("couldn't read the bot trades' findings")
        return {}
    return {key: found[run_id] for key, run_id in runs.items() if run_id in found}


@bp.get("/journal/bot-chips/<int:run_id>")
def bot_chips(run_id):
    """Bots slice 20 (Tracker2's api_run_tags): one journal row's chips, read again when
    the page hears bot_tags_changed for its run (static/js/bot_chips.js). `symbols`: the
    row's legs, comma-separated. Read-only."""
    symbols = [s for s in request.args.get("symbols", "").split(",") if s.strip()]
    try:
        chips = bot_manage.chips_for(services().session_factory, run_id, symbols)
    except Exception:  # noqa: BLE001 -- the page keeps the chips it has
        logging.getLogger(__name__).exception("couldn't read run %s's chips", run_id)
        return jsonify(error="couldn't read the chips"), 503
    return jsonify(
        chips=[{"label": c.label, "title": c.title, "warning": c.warning} for c in chips]
    )


GRID_LIMIT = 200  # rows in the grid's table


@bp.get("/journal/grid")
def grid_page():
    """The open trades the filters pick (#62, #91), as Tracker2's grid view (#215): a
    table with a box per trade and one risk graph of the ticked ones together. All are
    ticked to start, up to what one graph takes (risk_graph.MAX_TRADES)."""
    filters = Filters.from_args(request.args)
    view = journal.load_journal(
        services().session_factory, filters, cache=services().positions_cache
    )
    trades = [e for e in view.entries if journal.analyzable(e)]
    return render_template(
        "journal_grid.html",
        trades=trades[:GRID_LIMIT],
        more=max(len(trades) - GRID_LIMIT, 0),
        graphed=risk_graph.MAX_TRADES,
        row_details=row_details,
        filters=filters,
        view=view,
    )


def _accounts_arg() -> tuple[str, ...]:
    """The accounts asked for (#285): `account` repeated, or commas in one."""
    return tuple(a for v in request.args.getlist("account") for a in v.split(",") if a)


def _stock_summary(rows):
    """The cards' totals; shares held with no price yet show "—", not $0 (#286)."""
    summary = stock_summary(rows)
    if rows and all(r.price is None for r in rows):
        return replace(summary, total_pl=None, market_value=None, day_pl=None)
    return summary


@bp.get("/api/stocks")
def stocks_api():
    """The Stocks tab's rows, refreshed every few seconds (#58)."""
    rows = holdings.stock_holdings(
        services().broker_clients(), services().quote_hub, _accounts_arg()
    )
    summary = _stock_summary(rows)
    number = lambda value: None if value is None else float(value)  # noqa: E731
    return jsonify(
        rows=[
            {
                "ticker": h.ticker,
                "quantity": number(h.quantity),
                "cost_basis": number(h.cost_basis),
                "cost": number(h.cost),
                "price": number(h.price),
                "pl": number(h.pl),
                "pl_percent": number(h.pl_percent),
                "today_pl": number(h.today_pl),
                "market_value": number(h.market_value),
            }
            for h in rows
        ],
        summary={
            "total_pl": number(summary.total_pl),
            "total_pl_percent": number(summary.total_pl_percent),
            "market_value": number(summary.market_value),
            "day_pl": number(summary.day_pl),
            "day_pl_percent": number(summary.day_pl_percent),
        },
    )


@bp.get("/api/stocks/value")
def stock_value_api():
    """The Stocks tab's portfolio value by day (#286), loaded after the page: it
    reads every broker's positions and each ticker's closes."""
    s = services()
    chart = stock_value.portfolio_value(
        s.session_factory, s.broker_clients(), s.quote_hub, _accounts_arg(), _now()
    )
    return jsonify(
        points=[{"day": p.day.isoformat(), "value": float(p.value)} for p in chart.points],
        note=chart.note,
    )


@bp.get("/api/stocks/<path:ticker>/chart")
def stock_chart_api(ticker):
    """One stock's daily closes, your cost per share and your buys and sells (#236).
    `path:` so a ticker with a slash (BRK/B) reaches it."""
    ticker = ticker.strip().upper()
    account = _accounts_arg()
    key = request.args.get("range", "1Y")
    if key not in RANGES:
        key = "1Y"
    # The same merged holding as the table's row, so the two agree
    held = next(
        (
            h
            for h in holdings.stock_holdings(
                services().broker_clients(), services().quote_hub, account
            )
            if h.ticker == ticker
        ),
        None,
    )
    if held is None:
        abort(404)
    quantity, cost_basis = held.quantity, held.cost_basis
    today = market_date(_now())
    start = range_start(key, today)
    closes, note = [], ""
    try:
        days = RANGES[key] or 3650
        candles = chart_market.price_history(
            services().broker_clients(), ticker, 1440, days
        )["candles"]
        closes = [
            {
                "day": datetime.fromtimestamp(c["time"], UTC).date().isoformat(),
                "close": float(c["close"]),
            }
            for c in candles
        ]
    except chart_market.MarketError:
        note = "Prices aren't available right now."
    except Exception:  # noqa: BLE001 -- the drawer still shows cost and trades
        logger.exception("Stock chart prices failed for %s", ticker)
        closes, note = [], "Prices aren't available right now."
    # One account is read alone; several are read together and picked by trade_marks
    pair = tuple(account[0].partition(":")[::2]) if len(account) == 1 else None
    fills = transactions.load_fills(services().session_factory, account=pair).fills
    marks = [
        {
            "day": m.day.isoformat(),
            "side": m.side,
            "quantity": float(m.quantity),
            "price": float(m.price),
        }
        for m in trade_marks(fills, ticker, account)
        if start is None or m.day >= start
    ]
    return jsonify(
        ticker=ticker, quantity=float(quantity), cost_basis=float(cost_basis),
        closes=closes, marks=marks, note=note,
    )  # fmt: skip


@bp.get("/api/open-pl")
def open_pl():
    """Live open P/L for the journal's open trades, with its filters (#24)."""
    view = journal.load_journal(
        services().session_factory,
        Filters.from_args(request.args),
        cache=services().positions_cache,
    )
    result = journal.live_open_pl(view, services().quote_hub)
    day = pl_day.for_entries(
        services().session_factory, view.entries, services().quote_hub, services().broker_clients()
    )
    number = lambda value: None if value is None else float(value)  # noqa: E731
    return jsonify(
        pl_day={key: number(r.value) for key, r in day.positions.items()},
        pl_day_total=number(day.total),
        pl_day_approximate=day.approximate,
        pl_day_label=pl_day.label(day.day),
        positions={key: number(value) for key, value in result["positions"].items()},
        marks={symbol: number(mark) for symbol, mark in result["marks"].items()},
        total=number(result["total"]),
        priced=result["priced"],
        open=result["open"],
        streams=services().quote_hub.status(),
    )


@bp.route("/journal/pl")
def pl_page():
    """The P/L calendar and P/L over time, for the trades the filters pick."""
    dashboard, response = _dashboard("journal.pl_page")
    if response:
        return response

    filters = Filters.from_args(request.args)
    view = journal.load_journal(
        services().session_factory, filters, cache=services().positions_cache
    )
    month = journal.parse_month(request.args.get("month"))
    days = journal.pl_by_day(view)
    return render_template(
        "pl.html",
        page_endpoint="journal.pl_page",
        view=view,
        calendar=journal.load_calendar(days, month),
        history=journal.load_history(days),
        month_url=lambda months: _month_url(month, months),
        filters=filters,
        dashboard=dashboard,
        periods=journal.PERIODS,
        date_ranges=date_ranges.RANGES,
        broker_names=BROKER_NAMES,
        today=market_date(_now()),
        statuses=journal.STATUSES,
        uncategorized=journal.UNCATEGORIZED,
    )


@bp.route("/journal/metrics")
def metrics_page():
    dashboard, response = _dashboard("journal.metrics_page")
    if response:
        return response

    filters = Filters.from_args(request.args)
    view = journal.load_journal(
        services().session_factory, filters, cache=services().positions_cache
    )
    period = request.args.get("period", "week")
    if period not in journal.METRIC_PERIODS:
        period = "week"
    return render_template(
        "metrics.html",
        page_endpoint="journal.metrics_page",
        view=view,
        metrics=journal.load_metrics(view, period),
        period=period,
        filters=filters,
        dashboard=dashboard,
        periods=journal.PERIODS,
        date_ranges=date_ranges.RANGES,
        broker_names=BROKER_NAMES,
        today=market_date(_now()),
        statuses=journal.STATUSES,
        uncategorized=journal.UNCATEGORIZED,
    )


def _month_url(month, months: int) -> str:
    """This page with the calendar moved `months` forward (or back), keeping
    the filters."""
    index = month.year * 12 + month.month - 1 + months
    args = request.args.to_dict(flat=False)
    args["month"] = f"{index // 12:04d}-{index % 12 + 1:02d}"
    return url_for("journal.pl_page", **args)


# --- Dashboards ----------------------------------------------------------------


@bp.post("/journal/dashboards")
def create_dashboard():
    filters = Filters.from_args(request.form)
    try:
        dashboard_id = journal.create_dashboard(
            services().session_factory, request.form.get("name", ""), filters
        )
    except JournalError as e:
        flash(str(e), "warning")
        return redirect(url_for("journal.journal_page", **filters.to_args()))
    return redirect(url_for("journal.journal_page", dashboard=dashboard_id, **filters.to_args()))


@bp.post("/journal/dashboards/<int:dashboard_id>/update")
def update_dashboard(dashboard_id):
    filters = Filters.from_args(request.form)
    try:
        journal.update_dashboard(services().session_factory, dashboard_id, filters)
        flash("Dashboard updated.", "success")
    except JournalError as e:
        flash(str(e), "warning")
    return redirect(url_for("journal.journal_page", dashboard=dashboard_id, **filters.to_args()))


@bp.post("/journal/dashboards/<int:dashboard_id>/delete")
def delete_dashboard(dashboard_id):
    journal.delete_dashboard(services().session_factory, dashboard_id)
    return redirect(url_for("journal.journal_page"))


# --- One trade -------------------------------------------------------------------


@bp.route("/journal/trades/<path:key>")
def trade_page(key):
    view = journal.load_journal(services().session_factory, cache=services().positions_cache)
    entry = journal.find_entry(view, key)
    if entry is None:
        # An iron condor shown split (a bot that splits its condors took it over, bots
        # slice 12): its first half.
        half = next((e for e in view.entries if e.position.split_from == key), None)
        if half is None:
            abort(404)
        return redirect(url_for("journal.trade_page", key=half.position.key, back=_back()))
    if entry.awaiting_broker:
        journal.estimate_expired(journal.JournalView(entries=[entry]), _closes)
    return render_template(
        "trade.html",
        entry=entry,
        accounts=view.accounts,
        close_reasons=journal.CLOSE_REASONS,
        tags=view.tags,
        manual_entries=manual.entry_ids(entry.position),
        adopt=_adoptable(entry),
        manage=_manageable(entry) or _per_trade(entry),
        which_trades=adopt_rules.WHICH_TRADES,
        back=_back(),
    )


def _run_bot(entry) -> bool:
    """A bot run placed (or manages) the trade. A trade matched to a bot by hand (#169) has
    no run: it can still be handed to a bot or managed."""
    return entry.bot is not None and not entry.bot.matched


def _adoptable(entry) -> bool | None:
    """Whether the trade page offers handing it to a bot (bots slice 12, widened in
    slice 15): None when it doesn't apply (a bot's trade, closed, not options), False
    when it wasn't opened today; the hand-over page says the rest."""
    p = entry.position
    if _run_bot(entry) or p.status.value == "closed":
        return None
    if not any(leg.option is not None for leg in p.live_legs):
        return None
    today = _now().astimezone(MARKET_TZ).date()
    return p.opened_at.astimezone(MARKET_TZ).date() == today


def _manageable(entry) -> bool:
    """Whether the trade offers Manage this trade (manual bots, #158): any open trade
    with an option leg that no bot manages, opened any day (unlike _adoptable)."""
    p = entry.position
    if _run_bot(entry) or p.status.value == "closed":
        return False
    return any(leg.option is not None for leg in p.live_legs)


def _per_trade(entry) -> bool:
    """An open trade whose rules came from Manage this trade (a hidden per-trade bot)."""
    return (entry.bot is not None and entry.bot.per_trade
            and entry.position.status.value != "closed")  # fmt: skip


@bp.post("/journal/trades/<path:key>")
def save_trade(key):
    form = request.form
    try:
        journal.save_trade(
            services().session_factory,
            key,
            name=form.get("name", ""),
            tags=", ".join(t for t in (form.get("tags"), form.get("new_tags")) if t and t.strip()),
            notes=form.get("notes", ""),
            close_reason=form.get("close_reason", ""),
        )
    except JournalError as e:
        flash(str(e), "warning")
        return redirect(url_for("journal.trade_page", key=key, back=_back()))
    flash("Trade saved.", "success")
    return redirect(_back())


@bp.post("/journal/trades/edit-many")
def edit_many():
    """The select bar's Save: tags added to or removed from every ticked trade."""
    form = request.form
    keys = form.getlist("trade")
    add, remove = form.get("add_tags", ""), form.get("remove_tags", "")
    if not keys:
        flash("Tick at least one trade.", "warning")
    elif not add.strip() and not remove.strip():
        flash("Nothing changed: type a tag to add or remove.", "warning")
    else:
        try:
            n = journal.edit_many(
                services().session_factory, keys, add_tags=add, remove_tags=remove
            )
            flash(f"Updated {n} trade{'' if n == 1 else 's'}.", "success")
        except JournalError as e:
            flash(str(e), "warning")
    return redirect(_back())


@bp.post("/journal/trades/<path:key>/split")
def split_trade(key):
    split = request.form.get("split") == "1"
    journal.set_split(services().session_factory, key, split)
    flash(
        "Split into a put spread and a call spread." if split else "Combined into one position.",
        "success",
    )
    return redirect(_back())


# --- Handing a trade to a bot (bots slice 12) ------------------------------------------


def _adopt_page(key, found=None):
    deps = bot_loops.deps_for(services())
    trade = found.trade if found is not None and found.trade is not None else None
    if trade is None:
        trade = bot_adopt.find(deps, key)
    if trade is None:
        abort(404)
    offered = [] if trade.problem else bot_adopt.candidates(deps, trade)
    how = {
        side.position: " · ".join(stops.describe(side.stop.as_log()["stop_calc"]))
        for side in (found.sides if found is not None else [])
        if side.stop is not None
    }
    return render_template(
        "trade_adopt.html", key=key, trade=trade, offered=offered, found=found, how=how,
        back=_back(),
    )  # fmt: skip


@bp.route("/journal/trades/<path:key>/adopt")
def adopt_page(key):
    """Pick a bot to hand this trade to (Tracker2's "Manage with bot…")."""
    return _adopt_page(key)


@bp.post("/journal/trades/<path:key>/adopt/check")
def adopt_check(key):
    """What the bot would place, checked against the broker and the live asks. Reads
    only."""
    bot_id = request.form.get("bot", type=int)
    if bot_id is None:
        flash("Pick a bot.", "warning")
        return redirect(url_for("journal.adopt_page", key=key, back=_back()))
    found = bot_adopt.check(bot_loops.deps_for(services()), key, bot_id)
    return _adopt_page(key, found)


@bp.post("/journal/trades/<path:key>/adopt")
def adopt_confirm(key):
    """Hand it over: checked again, then the run and its exits."""
    bot_id = request.form.get("bot", type=int)
    fingerprint = request.form.get("fingerprint", "")
    if bot_id is None:
        abort(400)
    try:
        outcome = bot_adopt.adopt(bot_loops.deps_for(services()), key, bot_id, fingerprint)
    except Exception:  # noqa: BLE001 -- the page always answers
        logger.exception("handing %s to bot %s failed", key, bot_id)
        outcome = bot_adopt.Outcome(
            False, "Something went wrong part-way; check the bot's runs and your broker."
        )
    if outcome.ok and outcome.run_id is not None:
        flash(outcome.message, "warning" if outcome.warning else "success")
        return redirect(url_for("automation.run_page", run_id=outcome.run_id))
    flash(outcome.message, "warning")
    return redirect(url_for("journal.adopt_page", key=key, back=_back()))


# --- Manage this trade (manual bots, #158) ---------------------------------------------

EXIT_TIME = ("15:45", 0)  # Add an exit time: the expiration day, 15:45 New York


def _manage_entry(key):
    view = journal.load_journal(services().session_factory, cache=services().positions_cache)
    entry = journal.find_entry(view, key)
    if entry is None:
        abort(404)
    return entry


def _custom_rules(form) -> tuple[manual_rules.ManualRules, dict]:
    """Custom rules for this trade only: the manual bot form's fields, never saved."""
    form = form.to_dict()
    form["name"] = "Custom rules"
    if not form.get("exit_on"):
        form["close_time"] = ""
    return manual_rules.read_form(form)


@bp.get("/journal/trades/<path:key>/manage")
def manage_page(key, problems=None, status=200):
    entry = _manage_entry(key)
    if not (_manageable(entry) or _per_trade(entry)):
        abort(404)
    return render_template("manage_trade.html", **_manage_context(entry, key, problems)), status


def _manage_return(key) -> str:
    """After a Manage this trade form: back where it came from (the journal's Manage
    panel posts `next`, #174), else the manage page."""
    return local_path(request.form.get("next")) or url_for(
        "journal.manage_page", key=key, back=_back()
    )


def _manage_context(entry, key, problems=None) -> dict:
    """What Manage this trade shows: the manage page's and the Manage panel's (#174)."""
    deps = bot_loops.deps_for(services())
    run_id = entry.bot.run_id if _run_bot(entry) else None
    rules = manual_attach.rules_of_run(deps, run_id) if run_id is not None else None
    trade = manual_attach.read_trade(deps, entry.position.split_from or entry.position.key)[0]
    assignable = manual_attach.assignable(trade) if trade is not None else None
    values = request.form.to_dict() if problems else {}

    def typed(name, current):
        if name in values:
            return values[name]
        return "" if current is None else current

    return dict(
        entry=entry, key=key, rules=rules, run_id=run_id,
        closing=run_id is not None and manual_attach.closing(deps, run_id),
        warn=assignable if rules is not None and not rules.close_time else None,
        manual_bots=manual_bots.listing(services().session_factory),
        problems=problems or {}, typed=typed, alert_rows=[None], back=_back(),
    )  # fmt: skip


@bp.post("/journal/trades/<path:key>/manage")
def manage_save(key):
    entry = _manage_entry(key)
    deps = bot_loops.deps_for(services())
    form = request.form
    manual_bot_id = None
    if form.get("add_exit_time") == "1":
        if not _per_trade(entry):
            abort(400)
        current = manual_attach.rules_of_run(deps, entry.bot.run_id)
        rules = replace(current, close_time=EXIT_TIME[0], close_dte=EXIT_TIME[1])
    elif form.get("custom") == "1":
        rules, problems = _custom_rules(form)
        if problems:
            flash(next(iter(problems.values())), "warning")
            return manage_page(key, problems, 400)
    else:
        manual_bot_id = form.get("manual_bot_id", type=int)
        rules = None
        if manual_bot_id is not None:
            rules = manual_bots.load(services().session_factory, manual_bot_id)
        if rules is None:
            flash("Pick a manual bot, or set custom rules.", "warning")
            return redirect(_manage_return(key))
    try:
        if _per_trade(entry):
            outcome = manual_attach.change(deps, entry.bot.run_id, rules, manual_bot_id)
        elif _manageable(entry):
            outcome = manual_attach.attach(
                deps, entry.position.split_from or entry.position.key, rules, manual_bot_id
            )
        else:
            abort(400)
    except Exception:  # noqa: BLE001 -- the page always answers
        logger.exception("managing %s failed", key)
        outcome = manual_attach.bot_adopt.Outcome(False, bot_manage.CHECK_BROKER)
    flash(outcome.message, "warning" if outcome.warning or not outcome.ok else "success")
    return redirect(_manage_return(key))


@bp.post("/journal/trades/<path:key>/manage/remove")
def manage_remove(key):
    entry = _manage_entry(key)
    if not _per_trade(entry):
        abort(400)
    outcome = manual_attach.remove(bot_loops.deps_for(services()), entry.bot.run_id)
    flash(outcome.message, "success" if outcome.ok else "warning")
    return redirect(_manage_return(key))


@bp.post("/journal/trades/<path:key>/unlink")
def unlink_bot(key):
    """#177: take a trade matched to a bot by hand off it, for good."""
    entry = _manage_entry(key)
    if entry.bot is None or not entry.bot.matched:
        abort(400)
    p = entry.position
    name = bot_match.unlink(services().session_factory, p.split_from or p.key)
    flash(f"Unlinked from {name}: it no longer counts in that bot's record.", "success")
    back = url_for("journal.trade_page", key=key, back=_back())
    return redirect(local_path(request.form.get("next")) or back)


# --- The journal's Manage panel (#174) -------------------------------------------------


def _panel_next(back: str, key: str) -> str:
    """The journal view the panel was opened from, with ?panel=<key> so it opens again
    after a form in it is sent."""
    parts = urlsplit(back)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "panel"]
    return urlunsplit(("", "", parts.path, urlencode([*query, ("panel", key)]), ""))


@bp.get("/journal/trades/<path:key>/panel")
def panel(key):
    """The gear's slide-in panel on the journal (Tracker2's manage-bot drawer): just the
    panel's HTML, made of the run page's and the manage page's own sections."""
    entry = _manage_entry(key)
    p = entry.position
    run = rules = None
    if _run_bot(entry) and not entry.bot.per_trade:
        run = _safe_run(entry.bot.run_id)
        if run and p.split_from:
            run = _one_side(run, p.key.rsplit("#", 1)[-1])
    elif _manageable(entry) or _per_trade(entry):
        rules = _manage_context(entry, p.split_from or p.key)
    context = {
        **(run or rules or {}), "entry": entry, "panel_run": run is not None,
        "panel_rules": rules is not None, "closed": p.status.value == "closed",
        "adopt": _adoptable(entry), "which_trades": adopt_rules.WHICH_TRADES,
        "analyzable": journal.analyzable(entry), "back": _back(),
        "next_url": _panel_next(_back(), p.key),
    }  # fmt: skip
    return render_template("_manage_panel.html", **context)


def _one_side(run: dict, half: str) -> dict:
    """The panel of one side of a split iron condor: only its groups (the put or the call
    ones), only their exit steps (the entry's stay), and Close this side for those groups.
    Splitting makes each side its own trade; the other side has its own row."""
    put_call = {"put": "PUT", "call": "CALL"}.get(half)
    groups = {
        leg["group"] for leg in run["run"].legs or ()
        if (found := parse_occ(leg.get("symbol", ""))) and found[1].put_call == put_call
    }  # fmt: skip
    if not groups:
        return run
    sides = [side for side in run["sides"] or () if side.position in groups]
    others = {side.name for side in run["sides"] or () if side.position not in groups}
    steps = [
        step for step in run["steps"] or ()
        if step.source.startswith("Entry") or step.source.rsplit(" · ", 1)[-1] not in others
    ]  # fmt: skip
    return {**run, "sides": sides, "steps": steps, "half_groups": sorted(groups)}


def _safe_run(run_id):
    try:
        return run_context(run_id)
    except Exception:  # noqa: BLE001 -- the panel always answers
        logger.exception("the Manage panel for run %s failed", run_id)
        return None


# --- Tags ----------------------------------------------------------------------


@bp.route("/journal/tags")
def tags_page():
    """Add, rename and delete tags (decision 0072: the Strategies page became this)."""
    factory = services().session_factory
    return render_template(
        "tags.html",
        tags=journal.all_tags(factory),
        counts=journal.tag_counts(factory),
        bots=journal.bot_tags(factory),
    )


@bp.post("/journal/tags")
def add_tag():
    _try(journal.add_tag, request.form.get("name", ""))
    return redirect(url_for("journal.tags_page"))


@bp.post("/journal/tags/<int:tag_id>/rename")
def rename_tag(tag_id):
    _try(journal.rename_tag, tag_id, request.form.get("name", ""))
    return redirect(url_for("journal.tags_page"))


@bp.post("/journal/tags/<int:tag_id>/delete")
def delete_tag(tag_id):
    _try(journal.delete_tag, tag_id)
    return redirect(url_for("journal.tags_page"))


def _try(action, *args) -> None:
    try:
        action(services().session_factory, *args)
    except JournalError as e:
        flash(str(e), "warning")


def _back() -> str:
    """Where to return after editing: the journal view the user came from.
    Only paths within this app are accepted."""
    return local_path(request.values.get("back")) or url_for("journal.journal_page")
