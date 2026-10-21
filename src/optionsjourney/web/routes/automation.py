"""The Automation pages (spec: the first automation, section 6): the bot
list, the bot form, going live, Preview now, and the runs."""

import json
import logging
import re
from dataclasses import replace
from datetime import UTC, date, datetime

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)  # fmt: skip
from sqlalchemy import func, select

from optionsjourney.bots import (
    bot_file,
    bot_link,
    day_plan,
    definition,
    futures,
    manage,
    schedule,
    search,
    stops,
    strategy_rows,
    timeline,
    timing,
    tracker2_file,
    underlyings,
)
from optionsjourney.bots import manual as manual_rules
from optionsjourney.bots import summary as bot_summary
from optionsjourney.bots.definition import (
    MAX_DTE,
    MAX_GROUPS,
    MAX_LEGS,
    MAX_RATIO,
    RESERVED_TAGS,
    SHAPES,
    app_watched,
    iron_condor_skeleton,
    is_iron_condor_shape,
    watched_groups,
)
from optionsjourney.bots.schedule import skip_target_day
from optionsjourney.bots.underlyings import UNDERLYINGS
from optionsjourney.bots.validation import BROKER_NAMES, validate
from optionsjourney.core import trade_rows
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close
from optionsjourney.db.models import Account, OrderLog, SyncState, Tag
from optionsjourney.services import (
    bot_adopt,
    bot_audit,
    bot_entry,
    bot_exits,
    bot_fetch,
    bot_filters,
    bot_integrity,
    bot_limit,
    bot_loops,
    bot_manage,
    bot_match,
    bot_rearm,
    bot_stats,
    bot_store,
    bot_ticket,
    bot_trade_settings,
    economic_events,
    journal,
    manual_bots,
    orders,
    paper_journal,
    setup,
    transactions,
)  # fmt: skip
from optionsjourney.web import services
from optionsjourney.web.pagination import paginate
from optionsjourney.web.security import local_path

bp = Blueprint("automation", __name__)
logger = logging.getLogger(__name__)


def _now():
    return datetime.now(UTC)


def understood_words(bot, short: bool = False) -> str:
    """The arming box's words for a bot whose stops the app watches (slice 11's box,
    decision 0037; any broker from slice 13, decision 0039)."""
    broker = BROKER_NAMES.get(bot.broker, bot.broker)
    what = f"the stops of {', '.join(g.name for g in watched_groups(bot))} are"
    if short:
        return f"I understand {what} watched by this app, not held at {broker}."
    return (
        f"I understand: {what} watched by this app, not held at {broker}, and work only while "
        "this computer is on and awake and the app is running."
    )


@bp.context_processor
def _helpers():
    return dict(
        app_watched=app_watched,
        watched_groups=watched_groups,
        trade_cap=definition.trade_cap,  # decision 0058: what a blank limit works out to
        day_cap=definition.day_cap,
        is_iron_condor_shape=is_iron_condor_shape,
        understood_words=understood_words,
        account_label=_account_label,
        bot_schedule=schedule.summary,
        bot_legs=bot_summary.legs,
        bot_exits=bot_summary.exit_lines,
    )


def _account_label(broker: str, number: str, with_broker: bool = True) -> str:
    """How a bot's account is named on these pages: its broker nickname (as the
    Accounts page shows it), else the broker (unless `with_broker` is off, for text
    that names the broker already), then the last four digits."""
    if "account_nicknames" not in g:
        with services().session_factory() as session:
            g.account_nicknames = {
                (b, n): nick
                for b, n, nick in session.execute(
                    select(Account.broker, Account.number, Account.nickname)
                )
                if nick
            }
    name = g.account_nicknames.get((broker, number))
    if not name and with_broker:
        name = BROKER_NAMES.get(broker, broker)
    return f"{name} ••{number[-4:]}" if name else f"••{number[-4:]}"


def _accounts(session) -> list[tuple[str, str]]:
    """(broker, account number) for each account the app syncs at a broker bots
    trade through (no network calls): TastyTrade first, then Schwab."""
    brokers = tuple(BROKER_NAMES)
    rows = session.execute(
        select(SyncState.broker, SyncState.account_number)
        .where(SyncState.broker.in_(brokers))
        .distinct()
    )
    return sorted(((b, n) for b, n in rows), key=lambda r: (brokers.index(r[0]), r[1]))


def _back(bot_id):
    """Where a button sends you back to: the list if it asked for that (`next`),
    otherwise the bot's page. Anything else is ignored, so it can't leave the site."""
    if request.form.get("next") == "list":
        return redirect(url_for("automation.automation_page"))
    return redirect(url_for("automation.bot_page", bot_id=bot_id))


def _skip_info(stored):
    """What the bot page says about Skip a day: the day it would skip, and
    whether that day is already skipped."""
    now_et = _now().astimezone(MARKET_TZ)
    day = skip_target_day(now_et)
    return {
        "day": day,
        "button": "today" if day == now_et.date() else day.strftime("%a %m/%d"),
        "label": day.strftime("%a %m/%d"),
        "skipping": bool(
            stored and stored.use_blackout_days and day.isoformat() in stored.blackout_days
        ),
    }


def _bot_summary(bot):
    """The summary cards at the top of a saved bot's page (Tracker2's config and stat
    cards): what it trades and when, and its record so far."""
    s = services()
    stats = _safe(
        "the bot's record",
        lambda: bot_stats.summaries(s.session_factory, [bot], s.positions_cache).get(bot.id),
        None,
    )
    return {
        "legs": bot_summary.legs(bot),
        "entry": bot_summary.entry(bot),
        "exit": bot_summary.exit_lines(bot),
        "execution": bot_summary.execution(bot),
        "account": _account_label(bot.broker, bot.account_number),
        "stats": stats,
        "graph": _graph_points(stats.curve) if stats else [],
    }


def _graph_points(curve) -> list[dict]:
    """#208: the P/L graph's points for line_chart.js: one per closed trade after the
    $0 start, each with its New York day and close time."""
    points = []
    for point in curve:
        if point.at is None:
            points.append({"label": "Start", "pl": None, "cumulative": float(point.cumulative)})
            continue
        at = point.at.astimezone(MARKET_TZ)
        points.append({
            "day": at.date().isoformat(),
            "label": f"{at:%a %b} {at.day}, {at.year} {at.hour % 12 or 12}:{at:%M %p}",
            "pl": float(point.pl),
            "cumulative": float(point.cumulative),
        })  # fmt: skip
    return points


def _form(bot, problems=None, status=200, shape_key="iron_condor", typed=None):
    """The bot page. `typed`: {field: text} the form couldn't read, shown as typed
    instead of the blank the BotDef holds, so a redraw never loses it."""
    with services().session_factory() as session:
        journal_tags = _journal_tags(session)
        accounts = _accounts(session)
    stored = bot_store.load(services().session_factory, bot.id) if bot.id else None
    page = render_template(
        "automation_bot.html",
        bot=bot,
        problems=problems or {},
        journal_tags=journal_tags,
        all_tags=bot_store.all_tags(services().session_factory),  # #109: the tag picker
        reserved_tags=RESERVED_TAGS,
        max_tags=definition.MAX_TAGS,
        accounts=accounts,
        broker_names=BROKER_NAMES,
        blank_times=3,
        typed=_typed_value(typed or {}),
        shapes=SHAPES,
        shape_key=shape_key,
        max_legs=MAX_LEGS,
        max_groups=MAX_GROUPS,
        max_ratio=MAX_RATIO,
        max_dte=MAX_DTE,
        underlyings=list(UNDERLYINGS.values()),  # bots slice 16
        futures_roots=FUTURES_ROOTS,  # bots slice 17
        is_futures=underlyings.futures,
        # The mode and Armed controls show what is stored, not what was typed.
        stored=stored,
        notice=bot_store.notice_of(services().session_factory, bot.id) if bot.id else None,
        skip=_skip_info(stored),
        imported=bot_store.imported(services().session_factory, bot.id) if bot.id else None,
        summary=_bot_summary(stored) if stored else None,
        floating_last=(
            bot_entry.floating_last(bot.id, _now().astimezone(MARKET_TZ).date())
            if bot.id and bot.use_floating_entry
            else None
        ),
    )
    return page, status


def _has_tag(bot, tag: str) -> bool:
    """The list's filter: a reserved word is the bot's state (Tracker2's auto tags),
    anything else one of its own tags, ignoring case."""
    word = tag.strip().lower()
    states = {"armed": bot.enabled, "off": not bot.enabled, "live": bot.live,
              "paper": not bot.live}  # fmt: skip
    if word in states:
        return states[word]
    return any(t.lower() == word for t in bot.tags)


def _safe(what: str, call, default):
    """A panel that can't be read shows nothing; the page with the bots' switches
    always renders."""
    try:
        return call()
    except Exception:  # noqa: BLE001
        logger.exception("couldn't read %s for the Automation page", what)
        return default


def _tag_names(session_factory) -> dict[int, str]:
    """{tag id: name}: the strategy each bot files its trades under (decision 0072)."""
    with session_factory() as session:
        return {tag_id: name for tag_id, name in session.execute(select(Tag.id, Tag.name))}


def _record_pl(stats) -> dict:
    """{bot id: (closed P/L, a Paper bot's pretend trades)} for the bots with a closed
    trade, as the list's P/L column shows them."""
    out = {}
    for bot_id, stat in stats.items():
        if stat and stat.closed:
            out[bot_id] = (stat.paper.pl, True) if stat.paper else (stat.summary.closed_pl, False)
    return out


@bp.route("/automation")
def automation_page():
    s = services()
    now = _now()
    paused, until = bot_store.leave_pause(s.session_factory, now)
    show_archived = request.args.get("archived") == "1"
    tag = request.args.get("tag", "").strip()
    query = request.args.get("q", "").strip()[:100]  # bots slice 20: the search
    bots = bot_store.listing(s.session_factory, archived=show_archived)
    tags = sorted({t for bot in bots for t in bot.tags}, key=str.lower)
    shown = [bot for bot in bots if (not tag or _has_tag(bot, tag)) and search.matches(bot, query)]
    stats = _safe(
        "the bots' records",
        lambda: bot_stats.summaries(s.session_factory, shown, s.positions_cache),
        {},
    )
    rows = strategy_rows.rows(
        shown,
        lambda bot: _account_label(bot.broker, bot.account_number),
        _safe("the strategies' names", lambda: _tag_names(s.session_factory), {}),
        _record_pl(stats),
    )
    armed = [bot for bot in bots if bot.enabled and not bot.archived]
    return render_template(
        "automation.html",
        bots=shown,
        groups=strategy_rows.by_strategy(rows),
        armed_count=len(armed),
        live_armed_count=sum(1 for bot in armed if bot.live),
        today_dow=now.astimezone(MARKET_TZ).isoweekday(),
        any_bots=bool(bots),
        total=len(bots),
        query=query,
        tags=tags,
        reserved=RESERVED_TAGS,
        tag=tag,
        show_archived=show_archived,
        archived_count=bot_store.archived_count(s.session_factory),
        manual_bots=manual_bots.listing(s.session_factory),
        stats=stats,
        leave={
            "paused": paused,
            "until": until.astimezone(MARKET_TZ).strftime("%H:%M") if paused and until else None,
            # Only when some group or trade leaves its wing; a pause always shows, so it
            # can be resumed. If it can't be read, show it.
            "shown": paused
            or _safe("the leave groups", lambda: bot_store.leave_in_use(s.session_factory), True),
        },
        findings=_safe(
            "the integrity findings", lambda: bot_integrity.open_findings(s.session_factory), []
        ),
        audit=bot_audit.audit(now),
        open_trades=_safe(
            "the open bot trades", lambda: bot_manage.open_trades(s.session_factory, now), []
        ),
    )


def _schedule(day: date, now: datetime) -> day_plan.DayPlan:
    """Bots slice 20 (automation_bp.api_schedule): what each armed bot does on `day`,
    from its runs, the stored calendars, the two switches and the schedule check. Reads
    only the database and the clock's memory."""
    s = services()
    bots = bot_store.armed(s.session_factory)
    runs = [r for rows in bot_store.today(s.session_factory, day).values() for r in rows]
    names = sorted({name for bot in bots for name in schedule.lists_in_use(bot)})
    lists = economic_events.lists_for(s.session_factory, names, day, now) if names else {}
    # #207: the Before expiration lists, over the days each bot's trade would be open
    later = sorted({name for bot in bots for name in schedule.blackout_expiry_lists(bot)})
    window = sorted({d for bot in bots if schedule.blackout_expiry_lists(bot)
                     for d in schedule.expiry_window(bot, day)})  # fmt: skip
    ahead = economic_events.lists_ahead(s.session_factory, later, window, now) if window else {}
    switches = orders.switches(s.session_factory)
    audit = bot_audit.audit(now)
    clock_ok = (not any(p.loop == "bot-clock" for p in audit.problems)) if audit.checked else None
    return day_plan.plan(
        bots, runs, day, now.astimezone(MARKET_TZ).replace(tzinfo=None), session_close(day),
        lists=lists, ahead=ahead, freeze=switches.freeze, no_new_trades=switches.no_new_trades,
        clock_ok=clock_ok, floating_last=lambda bot_id: bot_entry.floating_last(bot_id, day),
    )  # fmt: skip


@bp.get("/automation/schedule")
def schedule_day():
    """Any day's schedule as JSON (?date=YYYY-MM-DD, default today) for the schedule
    drawer (Tracker2's api_schedule and schedule_drawer.js). Read-only."""
    now = _now()
    now_et = now.astimezone(MARKET_TZ)
    raw = request.args.get("date", "").strip()
    try:
        day = date.fromisoformat(raw) if raw else now_et.date()
    except ValueError:
        return jsonify(error="The date must be YYYY-MM-DD."), 400
    plan = _safe("the day's schedule", lambda: _schedule(day, now), None)
    if plan is None:
        return jsonify(error="Couldn't read the schedule right now. The bots still run."), 503
    # The events and the holiday are extras: unreadable, the drawer shows none
    none = {"events": [], "holiday": None, "calendar_covers": True}
    extra = _safe(
        "the day's events", lambda: economic_events.day_notes(services().session_factory, day), none
    )
    return jsonify(
        date=day.isoformat(),
        econ_events=extra["events"],
        holiday=extra["holiday"],
        calendar_covers=extra["calendar_covers"],
        today=now_et.date().isoformat(),
        is_today=plan.is_today,
        now=now_et.strftime("%H:%M:%S"),
        clock_ok=plan.clock_ok,
        market_closed=plan.market_closed,
        counts=plan.counts,
        notes=[{"text": n, "warn": n in plan.warnings} for n in plan.notes],
        rows=[
            {
                "time": r.time,
                "bot_id": r.bot_id,
                "bot_name": r.bot_name,
                "bot_url": url_for("automation.bot_page", bot_id=r.bot_id),
                "live": r.live,
                "underlying": r.underlying,
                "size": r.size,
                "account": _account_label(r.broker, r.account_number),
                "tags": list(r.tags),
                "state": r.state,
                "note": r.note,
                "run_url": url_for("automation.run_page", run_id=r.run_id) if r.run_id else None,
                "floating": r.floating,
                "can_skip": r.can_skip,
            }
            for r in plan.rows
        ],
    )


@bp.route("/automation/<int:bot_id>/skip-day", methods=["POST", "DELETE"])
def skip_day(bot_id):
    """The schedule drawer's Skip day and its Undo (Tracker2's api_skip_day): POST adds
    ?date= to the bot's blackout days, DELETE takes it back. JSON either way."""
    now = _now()
    try:
        day = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return jsonify(error="The date must be YYYY-MM-DD."), 400
    if day < now.astimezone(MARKET_TZ).date():
        return jsonify(error="That day is already over."), 400
    s = services()
    if request.method == "DELETE":
        bot_store.undo_skip(s.session_factory, bot_id, day, now)
        return jsonify(day=day.isoformat())
    if bot_store.skip_day(s.session_factory, bot_id, now, day) is None:
        return jsonify(error="Only an armed bot can skip a day."), 409
    return jsonify(day=day.isoformat())


@bp.route("/automation/new")
def new_page():
    """A new bot, from the Start from picker's shape (an iron condor if none, or one
    it doesn't know)."""
    key = request.args.get("shape", "")
    # Slice 19: a new bot's form starts with Move conflicted strikes ticked (Tracker2's
    # default); existing bots were migrated with it off.
    base = replace(iron_condor_skeleton(""), move_conflicted_strikes=True)
    if key not in SHAPES:
        return _form(base)
    return _form(definition.shape(key, base), shape_key=key)


def _refuse_per_trade(bot_id) -> None:
    """Final review I1: a hidden per-trade bot (Manage this trade, #158) is one trade's
    rules, changed only on the Journal's Manage page: no bot page, arming, Live, Preview,
    Trade now, saving or deleting."""
    bot = bot_store.load(services().session_factory, bot_id) if bot_id else None
    if bot is not None and bot.per_trade:
        abort(404)


@bp.route("/automation/<int:bot_id>")
def bot_page(bot_id):
    bot = bot_store.load(services().session_factory, bot_id)
    if bot is None or bot.per_trade:
        abort(404)
    return _form(bot)


def _manual_form(rules, bot_id=None, problems=None, status=200, posted=None):
    """The manual bot page (#158). `posted`: the form as sent, when it is shown again
    with its problems, so what was typed stays."""
    values = dict(posted) if posted is not None else {}
    if posted is not None:
        count = min(
            int(re.sub(r"\D", "", values.get("alerts") or "") or 0), manual_rules.MAX_ALERTS
        )
        rows = [None] * count
    else:
        rows = list(rules.alerts) + [None]

    def typed(name, current):
        if name in values:
            return values[name]
        return "" if current is None else current

    return (
        render_template(
            "manual_bot.html",
            rules=rules, bot_id=bot_id, name=rules.name, problems=problems or {},
            typed=typed, alert_rows=rows, posted=posted is not None,
        ),
        status,
    )  # fmt: skip


@bp.get("/automation/manual/new")
def manual_new():
    return _manual_form(manual_rules.ManualRules(""))


@bp.get("/automation/manual/<int:bot_id>")
def manual_page(bot_id):
    rules = manual_bots.load(services().session_factory, bot_id)
    if rules is None:
        abort(404)
    return _manual_form(rules, bot_id)


@bp.post("/automation/manual")
def manual_save():
    form = request.form.to_dict()
    if not form.get("exit_on"):
        form["close_time"] = ""  # no exit time unless ticked
    rules, problems = manual_rules.read_form(form)
    raw_id = (form.get("id") or "").strip()
    bot_id = int(raw_id) if raw_id.isdigit() else None
    if not problems:
        try:
            manual_bots.save(services().session_factory, bot_id, rules, _now())
        except ValueError as error:
            if str(error) == "name taken":
                problems["name"] = "Another manual bot already has that name."
            else:
                abort(404)
    if problems:
        return _manual_form(rules, bot_id, problems, 400, posted=request.form.to_dict())
    return redirect(url_for("automation.automation_page"))


@bp.post("/automation/manual/<int:bot_id>/delete")
def manual_delete(bot_id):
    manual_bots.delete(services().session_factory, bot_id, _now())
    return redirect(url_for("automation.automation_page"))


def _typed_value(typed: dict):
    def value(name, current):
        if name in typed:
            return typed[name]
        return "" if current is None else current

    return value


def _edited(bot, op: str, problems: dict, typed: dict):
    """The form's add and remove buttons (`op`) applied to what was typed, with the
    unreadable texts and their problems moved to where their leg or group now is."""
    if op == "add-leg":
        return definition.add_leg(bot), problems, typed
    if op in ("add-group", "use-groups"):  # #116: Use leg groups ticked adds the second
        return definition.add_group(bot), problems, typed
    if op == "one-group" and len(bot.groups) > 1:
        # #116: Use leg groups off. The form redraws with the notice and saves nothing,
        # so reloading the page undoes it.
        for group in reversed(bot.groups[1:]):
            problems = bot_store.renumbered(problems, "group", group.position)
            typed = bot_store.renumbered(typed, "group", group.position)
        out = definition.one_group(bot)
        problems = problems | {"groups": "The legs are now one trade, with one stop and one "
                               "target for the whole trade: check them below. Nothing is saved "
                               "until you press Save; reload the page to undo it."}  # fmt: skip
        return out, problems, typed
    # #120 (OptionOmega's arrows): a leg into the group above or below
    move = re.fullmatch(r"move-leg-(\d+)-(up|down)", op)
    if move:
        step = -1 if move.group(2) == "up" else 1
        return definition.move_leg(bot, int(move.group(1)), step), problems, typed
    number = op.removeprefix("link-leg-")
    if number != op and number.isdigit():
        out = definition.add_linked_leg(bot, int(number))
        if out == bot:
            return bot, problems, typed
        at = int(number) + 1
        return (out, bot_store.made_room(problems, "leg", at),
                bot_store.made_room(typed, "leg", at))  # fmt: skip
    for prefix, kind, remove in (("remove-leg-", "leg", definition.remove_leg),
                                 ("remove-group-", "group", definition.remove_group)):  # fmt: skip
        number = op.removeprefix(prefix)
        if number != op and number.isdigit():
            out = remove(bot, int(number))
            if out == bot:  # the last leg or group stays: nothing moves
                return bot, problems, typed
            n = int(number)
            return (out, bot_store.renumbered(problems, kind, n),
                    bot_store.renumbered(typed, kind, n))  # fmt: skip
    return bot, problems, typed


def _listed(ticker: str) -> bool | None:
    """Whether the broker lists options on `ticker` (bots slice 16), from the expirations
    the bots' chain reads; None when no broker could be asked."""
    clients = services().broker_clients()
    if not clients:
        return None
    found = services().chain.expirations(clients, ticker).get("expirations")
    return bool(found)


@bp.post("/automation/save")
def save():
    bot, form_problems = bot_store.read_form(request.form)
    _refuse_per_trade(bot.id)
    typed = bot_store.typed_texts(request.form)
    op = str(request.form.get("op") or "")
    if op:
        # Add or remove a leg or group: redraw what was typed with the change, saving
        # nothing (no JavaScript on the form). Validation isn't shown yet, but text the
        # form couldn't read is, as typed, so nothing is silently lost (review C1).
        edited, problems, typed = _edited(bot, op, form_problems, typed)
        return _form(edited, problems, shape_key=None, typed=typed)
    before = bot_store.load(services().session_factory, bot.id) if bot.id else None
    bot_id, problems = bot_store.save(
        services().session_factory,
        bot,
        _now(),
        form_problems,
        listed=_listed,
    )
    if problems:
        return _form(bot, problems, 400, typed=typed)
    if before is not None and (before.enabled or before.live):
        if before.broker != bot.broker:
            flash(
                f"It now trades through {BROKER_NAMES.get(bot.broker, bot.broker)}, so it's "
                "switched off and back to Paper: arm it and make it live again when you're "
                "ready.",
                "warning",
            )
        elif before.underlying != bot.underlying:
            # Bots slice 16: bot_store.save switched it off, as for a broker change.
            flash(
                f"It now trades {bot.underlying}, so it's switched off and back to Paper: "
                "check its legs and numbers, then arm it and make it live again when you're "
                "ready.",
                "warning",
            )
        elif app_watched(bot) and not app_watched(before):
            # Final review I3: bot_store.save switched it off, as for a broker change.
            flash(
                "Its stops are watched by this app now, so it's switched off and back in "
                "preview: tick the box, arm it and make it live again when you're ready.",
                "warning",
            )
    return redirect(url_for("automation.bot_page", bot_id=bot_id))


FUTURES_ROOTS = (  # the form's futures suggestions (bots slice 17)
    ("/ES", "E-mini S&P 500 futures options, TastyTrade only"),
    ("/MES", "Micro E-mini S&P 500 futures options, TastyTrade only"),
)
FUTURES_NEVER_ARMED = (
    "A futures bot enters only from the Trade page's Manage with a bot, so it's never armed."
)


@bp.post("/automation/<int:bot_id>/arm")
def arm(bot_id):
    _refuse_per_trade(bot_id)
    on = request.form.get("on") == "1"
    bot = bot_store.load(services().session_factory, bot_id)
    if (
        on and bot is not None and bot.live and not bot.archived
        and not underlyings.futures(bot.underlying)
        and (request.form.get("confirmed") != "1" or request.form.get("understood") != "1")
    ):  # fmt: skip
        # Arming a Live bot sends real orders: every time, the go-live summary (account,
        # caps, where stops are held) and its box come first (decision 0061). The box is
        # checked here too, not only in the browser (final review I2).
        if request.form.get("confirmed") == "1":
            flash("Tick the box to show you understand this bot sends real orders.", "warning")
        return redirect(url_for("automation.live_page", bot_id=bot_id, arm=1,
                                next=request.form.get("next", "")))  # fmt: skip
    if on and _needs_understood(bot_id):
        # Bots slice 11 (0037) and 13 (0039): stops the app watches aren't held at the
        # broker. Arming the bot is the moment to say so: the box on its page is ticked.
        flash(f"{_tick(bot_id)[:-1]}, then arm it.", "warning")
        return redirect(url_for("automation.bot_page", bot_id=bot_id) + "#arm")
    if not bot_store.set_armed(services().session_factory, bot_id, on, _now()):
        bot = bot_store.load(services().session_factory, bot_id)
        if bot is not None and underlyings.futures(bot.underlying):
            flash(FUTURES_NEVER_ARMED, "warning")
        else:
            flash("This bot is archived: unarchive it first.", "warning")
    return _back(bot_id)


@bp.post("/automation/<int:bot_id>/skip")
def skip(bot_id):
    """Skip a day: no entries on the next entry day. Armed bots only."""
    day = bot_store.skip_day(services().session_factory, bot_id, _now())
    if day is None:
        flash("Only an armed bot can skip a day.", "warning")
    return _back(bot_id)


@bp.post("/automation/<int:bot_id>/unskip")
def unskip(bot_id):
    try:
        day = date.fromisoformat(request.form.get("day", ""))
    except ValueError:
        return _back(bot_id)  # nothing readable to undo
    bot_store.undo_skip(services().session_factory, bot_id, day, _now())
    return _back(bot_id)


@bp.post("/automation/skip")
def skip_many():
    """Skip a day for the ticked bots; those not armed are named and left alone."""
    s = services()
    if request.form.get("tags") or request.form.get("add") or request.form.get("remove"):
        # Bots slice 20: tags never ride on a skip (Enter in a tag box once pressed it)
        flash("Nothing skipped: that was a tag change. Use Add tag or Remove tag.", "warning")
        return redirect(url_for("automation.automation_page"))
    ids = [int(i) for i in request.form.getlist("bot_id") if i.isdigit()]
    skipped, refused = bot_store.skip_many(s.session_factory, ids, _now())
    if skipped:
        flash(f"Skipped a day for {len(skipped)} bot{'s' if len(skipped) != 1 else ''}.", "success")
    if refused:
        names = [b.name for b in bot_store.listing(s.session_factory) if b.id in refused]
        flash(f"Not armed, so not skipped: {', '.join(names) or 'unknown bot'}.", "warning")
    if not ids:
        flash("Tick the bots to skip first.", "warning")
    return redirect(url_for("automation.automation_page"))


def _journal_tags(session) -> list:
    """The journal's tags, by name: what a bot files its trades under (decision 0072)."""
    return list(session.scalars(select(Tag).order_by(func.lower(Tag.name))))


def _tag_name(tag_id) -> str:
    with services().session_factory() as session:
        tag = session.get(Tag, tag_id) if tag_id else None
        return tag.name if tag is not None else ""


@bp.post("/automation/journal-tags")
def new_journal_tag():
    """#108 (Tracker2's "+ New strategy" under the picker; a tag since decision 0072):
    make a journal tag from the bot form, or find the one with that name, and hand back
    its id, so the form can pick it without leaving the page."""
    try:
        tag = journal.tag_for(services().session_factory, request.form.get("name", ""))
    except journal.JournalError as e:
        return jsonify(error=str(e)), 400
    return jsonify(id=tag.id, name=tag.name)


@bp.post("/automation/tags")
def tag_many():
    """Bots slice 20 (api_bulk_tag_bots): add or remove tags on the ticked bots. Only the
    tags change, so armed and live bots too."""
    s = services()
    ids = [int(i) for i in request.form.getlist("bot_id") if i.isdigit()]
    pressed = request.form.get("tag_action")  # the button: only its own box counts

    def words(field: str) -> list[str]:
        # #144: the list has one Tags box; the button pressed says whether it adds or removes
        text = request.form.get("tags") if pressed == field else None
        return [t for t in (text or request.form.get(field, "")).split(",") if t.strip()]

    add, remove = words("add"), words("remove")
    if pressed == "add":
        remove = []
    elif pressed == "remove":
        add = []
    if not ids:
        flash("Tick the bots to tag first.", "warning")
    elif not add and not remove:
        flash("Type a tag to add or remove first.", "warning")
    else:
        changed, too_many = bot_store.tag_many(s.session_factory, ids, add, remove)
        n = len(changed)
        flash(f"Tagged {n} bot{'s' if n != 1 else ''}." if n else "No tags changed.",
              "success" if n else "warning")  # fmt: skip
        if too_many:
            flash(f"Left as they were (more than {definition.MAX_TAGS} tags): "
                  f"{', '.join(too_many)}.", "warning")  # fmt: skip
    return redirect(url_for("automation.automation_page"))


@bp.post("/automation/leave/pause")
def leave_pause():
    """Pause "leave" (Tracker2's actively-monitoring pause): until a time today
    (New York), or until resumed."""
    text = request.form.get("until", "").strip()
    until = None
    if text:
        try:
            at = datetime.strptime(text, "%H:%M").time()
        except ValueError:
            flash("Pick a time like 15:30, or leave it blank to pause until you resume.", "warning")
            return redirect(url_for("automation.automation_page"))
        now_et = _now().astimezone(MARKET_TZ)
        until = datetime.combine(now_et.date(), at, MARKET_TZ)
        if until <= now_et:
            flash("That time has passed today; pick a later one.", "warning")
            return redirect(url_for("automation.automation_page"))
    bot_store.pause_leave(services().session_factory, until, _now())
    return redirect(url_for("automation.automation_page"))


@bp.post("/automation/leave/resume")
def leave_resume():
    bot_store.resume_leave(services().session_factory, _now())
    return redirect(url_for("automation.automation_page"))


def _ticked_bots() -> list:
    """The ticked bots still on the list (not deleted, archived or a per-trade bot)."""
    ids = {int(i) for i in request.form.getlist("bot_id") if i.isdigit()}
    return [b for b in bot_store.listing(services().session_factory)
            if b.id in ids and not b.per_trade]  # fmt: skip


def _names(bots) -> str:
    return ", ".join(b.name for b in bots)


@bp.post("/automation/arm-many")
def arm_many():
    """The ticked bots: arm or turn off. Turning off acts at once. Arming a Live bot sends real
    orders and a watched bot's stops aren't held at the broker, so when any ticked bot is
    either, one page lists them all and its box is ticked first (decisions 0061, 0075)."""
    s = services()
    bots = _ticked_bots()
    on = request.form.get("on") == "1"
    if not bots:
        flash("Tick the bots first.", "warning")
        return redirect(url_for("automation.automation_page"))
    if (
        on
        and any(b.live or app_watched(b) for b in bots)
        and not (request.form.get("confirmed") == "1" and request.form.get("understood") == "1")
    ):
        if request.form.get("confirmed") == "1":
            flash("Tick the box to show you understand first.", "warning")
        return render_template("automation_many.html", bots=bots, arming=True)
    done, refused = [], []
    for bot in bots:
        if bot.enabled == on:
            continue
        ok = bot_store.set_armed(s.session_factory, bot.id, on, _now())
        (done if ok else refused).append(bot)
    n = len(done)
    word = "Armed" if on else "Turned off"
    flash(f"{word} {n} bot{'s' if n != 1 else ''}." if n else "Nothing changed.",
          "success" if n else "warning")  # fmt: skip
    if refused:
        flash(f"Can't be armed (archived or futures): {_names(refused)}.", "warning")
    return redirect(url_for("automation.automation_page"))


@bp.post("/automation/mode-many")
def mode_many():
    """The ticked bots: switch to Paper (at once) or Live. Going Live closes each bot's paper
    trades and leaves it switched off; one page lists them first (decisions 0061, 0075)."""
    s = services()
    bots = _ticked_bots()
    live = request.form.get("mode") == "live"
    if not bots:
        flash("Tick the bots first.", "warning")
        return redirect(url_for("automation.automation_page"))
    if live and not (
        request.form.get("confirmed") == "1"
        and (request.form.get("understood") == "1" or not any(app_watched(b) for b in bots))
        and (request.form.get("risk") == "1" or not _risk_needed())
    ):
        if request.form.get("confirmed") == "1":
            flash("Tick the box to show you understand first.", "warning")
        return render_template("automation_many.html", bots=bots, arming=False,
                               risk_needed=_risk_needed())  # fmt: skip
    done, failed = [], []
    deps = bot_loops.deps_for(s)
    for bot in bots:
        if bot.live == live:
            continue
        try:
            bot_store.set_mode(s.session_factory, bot.id, "live" if live else "paper", _now(), deps)
        except RuntimeError, ValueError:
            failed.append(bot)
            continue
        done.append(bot)
    if live and done:
        setup.accept_live_risk(s.session_factory)
    n = len(done)
    word = "Live" if live else "Paper"
    flash(f"Switched {n} bot{'s' if n != 1 else ''} to {word}"
          + (": they're switched off, arm them when you're ready." if live and n else ".")
          if n else "Nothing changed.", "success" if n else "warning")  # fmt: skip
    if failed:
        flash(f"Couldn't close their paper trades, so they stay in Paper: {_names(failed)}.",
              "warning")  # fmt: skip
    return redirect(url_for("automation.automation_page"))


@bp.route("/automation/<int:bot_id>/live")
def live_page(bot_id):
    _refuse_per_trade(bot_id)
    bot = bot_store.load(services().session_factory, bot_id)
    if bot is None:
        abort(404)
    return render_template("automation_live.html", bot=bot, arming=request.args.get("arm") == "1",
                           next_page=request.args.get("next", ""),
                           risk_needed=_risk_needed())  # fmt: skip


@bp.post("/automation/<int:bot_id>/live")
def set_live(bot_id):
    _refuse_per_trade(bot_id)
    on = request.form.get("on") == "1"
    if on and _needs_understood(bot_id):
        flash(_tick(bot_id), "warning")
        return redirect(url_for("automation.live_page", bot_id=bot_id))
    if on and _risk_needed() and request.form.get("risk") != "1":
        flash("Tick the box to show you're responsible for this bot's orders first.", "warning")
        return redirect(url_for("automation.live_page", bot_id=bot_id))
    before = bot_store.load(services().session_factory, bot_id)
    try:
        bot_store.set_mode(
            services().session_factory,
            bot_id,
            "live" if on else "paper",
            _now(),
            bot_loops.deps_for(services()),
        )
    except RuntimeError, ValueError:
        # Final review I3: its paper trades couldn't all be closed; set_mode left it Paper
        flash("Couldn't close the paper trades, so the bot stays in Paper.", "warning")
        return redirect(url_for("automation.bot_page", bot_id=bot_id))
    if on:
        setup.accept_live_risk(services().session_factory)
    if on and before is not None and before.enabled:
        # Final review I1: going Live disarms; arming again shows the go-live summary
        flash("It's Live and switched off: arm it when you're ready to send real orders.",
              "warning")  # fmt: skip
    return redirect(url_for("automation.bot_page", bot_id=bot_id))


def _risk_needed() -> bool:
    """The first time any bot goes Live, the user ticks that they're responsible for its
    orders (decision 0093). Asked once per install."""
    return not setup.live_risk_accepted(services().session_factory)


def _tick(bot_id) -> str:
    bot = bot_store.load(services().session_factory, bot_id)
    broker = BROKER_NAMES.get(bot.broker, bot.broker) if bot else "your broker"
    return (
        "Tick the box to show you understand this bot's stops are watched by this app, not "
        f"held at {broker}."
    )


def _needs_understood(bot_id, live_only=False) -> bool:
    """Arming, going live and (live) Preview now need the box ticked for a bot whose
    stops the app watches: Schwab (slice 11, decision 0037) or a watched group with a
    stop (slice 13, decision 0039)."""
    bot = bot_store.load(services().session_factory, bot_id)
    if bot is None or not app_watched(bot) or (live_only and not bot.live):
        return False
    return request.form.get("understood") != "1"


@bp.post("/automation/<int:bot_id>/preview")
def preview_now(bot_id):
    """Preview order: the order the bot would send now, checked with the broker and shown
    with its risk graph. Nothing is sent and no run is saved. A live Schwab bot's needs its
    box ticked (final review I-2)."""
    _refuse_per_trade(bot_id)
    if _needs_understood(bot_id, live_only=True):
        flash(_tick(bot_id), "warning")
        return redirect(url_for("automation.bot_page", bot_id=bot_id) + "#preview")
    bot = bot_store.load(services().session_factory, bot_id)
    if bot is None:
        abort(404)
    result = bot_entry.preview_order(bot_loops.deps_for(services()), bot_id)
    return render_template("automation_preview_order.html", bot=bot, result=result)


@bp.post("/automation/<int:bot_id>/trade-now")
def trade_now(bot_id):
    """Trade now (bots slice 19; Tracker2's automation_bp.api_run_now): a live entry off
    schedule, sent once TRADE is typed, with guards.check_manual's checks and the limits. A bot
    whose stops the app watches needs its box ticked, as Preview now does. It runs in the
    background; the run's page shows it."""
    _refuse_per_trade(bot_id)
    back = url_for("automation.bot_page", bot_id=bot_id) + "#trade-now"
    if _needs_understood(bot_id, live_only=True):
        flash(_tick(bot_id), "warning")
        return redirect(back)
    deps = bot_loops.deps_for(services())
    run_id, problem = bot_entry.trade_now(deps, bot_id, request.form.get("confirm", ""))
    if problem:
        flash(problem, "warning")
        return redirect(back)
    flash("Trade now started: the entry is checked, then sent and "
          "chased. Reload this page to follow it.", "success")  # fmt: skip
    return redirect(url_for("automation.run_page", run_id=run_id))


@bp.post("/automation/<int:bot_id>/delete")
def delete(bot_id):
    _refuse_per_trade(bot_id)
    journal.keep_bot_tag(services().session_factory, bot_id)
    bot_store.hide(services().session_factory, bot_id, _now())
    return redirect(url_for("automation.automation_page"))


@bp.route("/automation/<int:bot_id>/runs")
def runs_page(bot_id):
    s = services()
    bot = bot_store.load(s.session_factory, bot_id)
    if bot is None:
        abort(404)
    bot = replace(bot, name=bot_store.label(s.session_factory, bot_id) or bot.name)  # #158
    return render_template(
        "automation_runs.html", bot=bot, rows=bot_store.grouped_runs(s.session_factory, bot_id)
    )


@bp.route("/automation/runs/<int:run_id>")
def run_page(run_id):
    context = run_context(run_id)
    if context is None:
        abort(404)
    return render_template("automation_run.html", **context)


def run_context(run_id: int) -> dict | None:
    """What the run page shows, and the journal's Manage panel too (#174): one place."""
    s = services()
    run = bot_store.run(s.session_factory, run_id)
    if run is None:
        return None
    with s.session_factory() as session:
        log = list(
            session.scalars(
                select(OrderLog).where(OrderLog.bot_run_id == run_id).order_by(OrderLog.id)
            )
        )
    exits = bot_store.exits_of(s.session_factory, run_id)
    bot = bot_store.load(s.session_factory, run.bot_id)
    if bot is not None:  # a manual bot's per-trade bot shows its manual bot's name (#158)
        bot = replace(bot, name=bot_store.label(s.session_factory, run.bot_id) or bot.name)
    adopted_from = _adopted_from(run)
    from_ticket = bot_ticket.is_from_ticket(run)
    trade_now = bot_entry.is_trade_now(run)  # bots slice 19
    return dict(
        run=run,
        bot=bot,
        exits=exits,
        banner=_safe("the watch banner", lambda: _watch_banner(exits), None),
        watch_live=_watch_live(bot, exits, run_id),
        steps=_safe(
            "the run's timeline",
            lambda: _timeline(run, exits, bot, adopted_from, from_ticket, trade_now),
            None,
        ),
        calcs={e.id: _how(e) for e in exits},
        closed_how=_safe("how the sides closed", lambda: _closed_how(run, exits, bot), {}),
        kinds={e.id: _kind(e) for e in exits},
        log=log,
        paper_fills=_safe(
            "the paper fills", lambda: paper_journal.fills(s.session_factory, run_id), []
        ),  # #182
        adopted_from=adopted_from,
        from_ticket=from_ticket,
        trade_now=trade_now,
        dollars=lambda points: futures.dollars(points, run.multiplier),  # bots slice 17
        futures_note=bot_exits.FUTURES_NOTE,
        futures_not_yet=bot_manage.FUTURES_NOT_YET,
        wing_words=manage.WING_WORDS,  # bots slice 21
        # decision 0048: a debit fill's target can pass 100% (manage.target_range)
        target_max=manage.target_range(run.fill_credit)[1],
        target_words="% gained" if manage.target_range(run.fill_credit)[1] > 100 else "% kept",
        sides=_safe(
            "the trade's sides", lambda: bot_manage.sides(s.session_factory, run_id, bot), []
        ),  # fmt: skip
        timed=_safe(  # bots slice 21: this trade's own timed close and watch rules
            "the trade's timed close",
            lambda: bot_trade_settings.timed_view(s.session_factory, run_id, bot, _now()),
            None,
        ),
        watch_rules=_safe(
            "the trade's watch rules",
            lambda: bot_trade_settings.watch_view(s.session_factory, run_id, bot),
            None,
        ),
    )


def _closed_how(run, exits, bot) -> dict:
    """Group position -> how it closed (timeline.side_closed), for the Manage section."""
    fills = _order_fills(run, exits, bot)
    return {p: timeline.side_closed(exits, p, fills) for p in {e.group_position for e in exits}}


def _watch_banner(exits):
    """Bots slice 20 (automation_watch_banner.js): the latest watch rule that fired, and
    how its closes went."""
    return timeline.watch_banner(
        [e for e in exits if e.kind == "watch"],
        [e for e in exits if e.kind == "close"],
        now=_now(),
    )


def _watch_live(bot, exits, run_id: int | None = None) -> bool:
    """Whether the run page refreshes the banner: the bot has a watch rule on, the trade
    its own (bots slice 21), or the run already has a watch row (question 2)."""
    on = bool(bot) and (bot.use_delta_exit or bot.use_sl_ratio_exit or bot.use_underlying_exit)
    if not on and run_id is not None:
        with services().session_factory() as session:
            mine = bot_trade_settings.trade_watch_of(bot_trade_settings.watch_row(session, run_id))
        on = mine is not None and (mine.use_delta or mine.use_sl_ratio)
    return on or any(e.kind == "watch" for e in exits)


@bp.get("/automation/runs/<int:run_id>/watch-status")
def watch_status(run_id):
    """The banner as JSON, polled every 2 s by static/js/watch_banner.js (Tracker2's
    api_run_watch `watch_exit`). Read-only. `open`: a side is still open, so polling
    goes on."""
    s = services()
    run = bot_store.run(s.session_factory, run_id)
    if run is None:
        abort(404)
    exits = bot_store.exits_of(s.session_factory, run_id)
    banner = _watch_banner(exits)
    sides: dict[int, list] = {}
    for e in exits:
        sides.setdefault(e.group_position, []).append((e.kind, e.state))
    groups = {leg.get("group", 0) for leg in run.legs or () if isinstance(leg, dict)} or {0}
    held = run.status in ("filled", "partial", "pending", "running")  # before the fill too
    still_open = held and any(timing.side_state(sides.get(g, [])) != "closed" for g in groups)
    return jsonify(
        banner=None if banner is None else {"tone": banner.tone, "text": banner.text},
        open=still_open,
    )


def _timeline(run, exits, bot, adopted_from, from_ticket, trade_now) -> list:
    """Bots slice 20 (run_view.order_timeline): the entry's and the exits' steps as one
    list, oldest first, timed in New York."""
    source = (
        "Entry (handed over)" if adopted_from is not None
        else "Entry (Trade page)" if from_ticket
        else "Entry (Trade now)" if trade_now
        else "Entry"
    )  # fmt: skip
    names = {g.position: g.name for g in bot.groups} if bot else {}
    fills = _order_fills(run, exits, bot)
    return timeline.order_timeline(run, exits, names, entry_source=source, fills=fills)


def _order_fills(run, exits, bot) -> dict:
    """The synced fill price of each filled close, by its broker order (the steps' Filled)."""
    contracts = {str(r.broker_order_id): r.contracts for r in exits
                 if r.state == "filled" and r.broker_order_id}  # fmt: skip
    if not contracts or bot is None or run.created_at is None:
        return {}
    return transactions.order_fills(services().session_factory,
                                    (bot.broker, bot.account_number), run.created_at,
                                    contracts)  # fmt: skip


def _adopted_from(run) -> str | None:
    """The journal trade a handed-over run came from (bots slice 12), or None."""
    if not bot_adopt.is_adopted(run):
        return None
    first = (run.chase_log or [{}])[0]
    return first.get("trade") or ""


def _kind(exit_row) -> str:
    """The run page's Kind column; a close a rule started names it (slice 7)."""
    return timeline.kind_label(exit_row)


def _how(exit_row) -> list[str]:
    """The run page's How column: a stop's arithmetic, a time action's rule and what it
    moved, a timed close's setting and first price, a watch rule's or profit action's
    reading and what it did."""
    log = exit_row.log or []
    if exit_row.kind == "action" and log:
        return [log[0].get("rule", ""), *log[0].get("notes", [])]
    if exit_row.kind == "paired" and log:  # bots slice 21: what the paired step did
        return [e["event"] for e in log[1:] if "event" in e][-1:] or [log[0].get("event", "")]
    if exit_row.kind in ("watch", "profit") and log:
        return [log[0].get("rule", ""), log[0].get("detail", ""), *log[0].get("notes", [])]
    if exit_row.kind == "settle" and log:  # slice 14: what expired, what's sold after
        first = log[0]
        expired = ", ".join(s.strip() for s in first.get("expired", []))
        parts = [f"expired: {expired}"] if expired else []
        if first.get("exits_off"):
            parts.append("exits off: left to you")
        return parts
    if exit_row.kind in ("timed", "close"):
        parts = [e["event"] for e in log[:1] if "event" in e]
        start = next((e for e in log if e.get("stage") == exit_row.kind), None)
        if start:
            parts.append(f"from the {start['priced']} at {start['start']}")
        return parts
    return stops.describe(stops.calc_of(log))


@bp.post("/automation/runs/<int:run_id>/cancel-entry")
def cancel_entry(run_id):
    """Cancel a resting entry (bots slice 17; Tracker2's analyzer_entry.cancel_entry): what
    filled first keeps its profit target."""
    outcome = bot_limit.cancel_entry(bot_loops.deps_for(services()), run_id)
    flash(outcome.message, "success" if outcome.ok else "warning")
    return redirect(_return(url_for("automation.run_page", run_id=run_id)))


@bp.post("/automation/runs/<int:run_id>/modify-entry")
def modify_entry(run_id):
    """Re-price a resting entry in place (bots slice 21; Tracker2's analyzer_entry.modify_entry)."""
    try:
        outcome = bot_limit.modify_entry(
            bot_loops.deps_for(services()), run_id, request.form.get("price", "")
        )
    except Exception:  # noqa: BLE001 -- the page always answers
        logger.exception("modifying run %s's entry failed", run_id)
        outcome = bot_limit.Outcome(False, "Something went wrong; check your broker.")
    flash(outcome.message, "success" if outcome.ok else "warning")
    return redirect(_return(url_for("automation.run_page", run_id=run_id)))


@bp.post("/automation/runs/<int:run_id>/exits/<int:exit_id>/cancel")
def cancel_close(run_id, exit_id):
    """Cancel a running close (Tracker2's close_jobs.request_cancel): the close stops
    at its next step, its order is cancelled and the side's stop goes back."""
    factory = services().session_factory
    if bot_exits.request_cancel(factory, run_id, exit_id, _now()):
        if bot_exits.cancel_reaches(factory, exit_id):
            flash(
                "Cancelling the close. Its stop goes back once its order is cancelled.", "success"
            )
        else:
            flash(
                "This close is already buying its short back at market or selling its wing, "
                "so Cancel can't stop it now. Check the run's log and your broker.",
                "warning",
            )
    else:
        flash("That close isn't running any more.", "warning")
    return redirect(_return(url_for("automation.run_page", run_id=run_id)))


# --- Managing one trade (bots slice 6) ---------------------------------------------------

# Each action, what it calls and the form field it passes (None: nothing typed).
MANAGE = {
    "stop": ("change_stop", ("pct", "unit")),  # bots slice 21: % or $ a contract
    "preview-stop": ("preview_stop", ("pct", "unit")),  # bots slice 18: what it would set
    "target": ("change_target", ("pct",)),
    "close": ("close_part", ("quantity",)),
    "exits-off": ("exits_off", ()),
    "exits-on": ("exits_on", ("pct",)),
    # bots slice 18: the stop's and the target's own switches
    "stop-off": ("stop_off", ()),
    "stop-on": ("stop_on", ("pct",)),
    "target-off": ("target_off", ()),
    "target-on": ("target_on", ()),
    "recheck": ("recheck", ()),  # "I've checked my broker: re-check now"
    # bots slice 21: the group's own settings on this trade
    "wing": ("set_wing_rule", ("mode", "limit")),
    "wing-revert": ("revert_wing_rule", ()),
    "closing-order": ("set_closing_order", ("mode", "amount")),
    "profits": ("set_profit_actions", None),  # the group's rows, read as the bot form's
    "profits-revert": ("revert_profit_actions", ()),
}


@bp.post("/automation/runs/<int:run_id>/sides/<int:position>/<action>")
def manage_side(run_id, position, action):
    """Change one side's stop or target, turn its exits off or on, or close part of
    it (Tracker2's management drawer): the outcome is flashed on the run page."""
    if action not in MANAGE:
        abort(404)
    name, fields = MANAGE[action]
    deps = bot_loops.deps_for(services())
    if fields is None:  # the whole form (bots slice 21: a group's profit action rows)
        args = (request.form,)
    else:
        args = tuple(request.form.get(field, "") for field in fields)
    if action == "close" and "order_type" in request.form:  # bots slice 18: Limit or Market
        kind = request.form.get("order_type", "limit")
        args += (kind,)
        if kind == "market" and request.form.get("confirm") != "1":
            quantity = request.form.get("quantity", "")
            return _confirm(
                "Close at market?",
                f"Close {quantity} of this group at market: one market order for a group of "
                "one leg (a group of more legs is closed by the bot's exit chase). A market "
                "order fills at whatever price is there.",
                url_for("automation.manage_side", run_id=run_id, position=position,
                        action="close"),
                [("quantity", quantity), ("order_type", kind)],
                url_for("automation.run_page", run_id=run_id, _anchor="manage"),
            )  # fmt: skip
    if (
        action == "target-off"
        and request.form.get("confirm") != "1"
        and bot_manage._futures_run(deps, run_id)
    ):  # bots slice 21: a futures trade has no stop, so this leaves it with no exit
        return _confirm(
            "Switch this futures target off?", bot_manage.FUTURES_NO_EXIT,
            url_for("automation.manage_side", run_id=run_id, position=position,
                    action="target-off"), [],
            url_for("automation.run_page", run_id=run_id, _anchor="manage"),
        )  # fmt: skip
    try:
        outcome = getattr(bot_manage, name)(deps, run_id, position, *args)
    except Exception:  # noqa: BLE001 -- the page always answers
        logger.exception("managing run %s side %s (%s) failed", run_id, position, action)
        outcome = bot_manage.Outcome(False, bot_manage.CHECK_BROKER)
    flash(outcome.message, "success" if outcome.ok else "warning")
    return redirect(_return(url_for("automation.run_page", run_id=run_id, _anchor="manage")))


# --- A trade's own settings (bots slice 21) -----------------------------------------------


def _settings_back(run_id: int) -> str:
    return url_for("automation.run_page", run_id=run_id, _anchor="settings")


def _settings_saved(run_id: int, call, heading: str, action: str, fields):
    """Run a save; a save that would act at once shows the confirm step first."""
    try:
        saved = call(request.form.get("confirm") == "1")
    except Exception:  # noqa: BLE001 -- the page always answers
        logger.exception("saving run %s's own settings failed", run_id)
        flash("Something went wrong; nothing may have been saved. Check the run page.", "warning")
        return redirect(_return(_settings_back(run_id)))
    if saved.confirm:
        return _confirm(heading, saved.confirm, action, fields, _settings_back(run_id))
    flash(saved.message, "success" if saved.ok else "warning")
    return redirect(_return(_settings_back(run_id)))


@bp.post("/automation/runs/<int:run_id>/timed-close")
def trade_timed_close(run_id):
    """This trade's own timed close (Tracker2's PUT /api/runs/<id>/timed-close)."""
    deps = bot_loops.deps_for(services())
    typed = (request.form.get("time", ""), request.form.get("dte", ""))
    return _settings_saved(
        run_id, lambda ok: bot_trade_settings.save_timed(deps, run_id, *typed, confirmed=ok),
        "Save this timed close?", url_for("automation.trade_timed_close", run_id=run_id),
        [("time", typed[0]), ("dte", typed[1])],
    )  # fmt: skip


@bp.post("/automation/runs/<int:run_id>/timed-close/revert")
def trade_timed_close_revert(run_id):
    deps = bot_loops.deps_for(services())
    return _settings_saved(
        run_id, lambda ok: bot_trade_settings.revert_timed(deps, run_id, confirmed=ok),
        "Go back to the bot's timed close?",
        url_for("automation.trade_timed_close_revert", run_id=run_id), [],
    )  # fmt: skip


@bp.post("/automation/runs/<int:run_id>/watch-rules")
def trade_watch_rules(run_id):
    """This trade's own watch rules (Tracker2's PUT /api/runs/<id>/watch-rules)."""
    deps = bot_loops.deps_for(services())
    form = {k: v for k, v in request.form.items() if k not in ("confirm", "next")}
    return _settings_saved(
        run_id, lambda ok: bot_trade_settings.save_watch(deps, run_id, form, confirmed=ok),
        "Save these watch rules?", url_for("automation.trade_watch_rules", run_id=run_id),
        list(form.items()),
    )  # fmt: skip


@bp.post("/automation/runs/<int:run_id>/watch-rules/revert")
def trade_watch_rules_revert(run_id):
    deps = bot_loops.deps_for(services())
    return _settings_saved(
        run_id, lambda ok: bot_trade_settings.revert_watch(deps, run_id, confirmed=ok),
        "Go back to the bot's watch rules?",
        url_for("automation.trade_watch_rules_revert", run_id=run_id), [],
    )  # fmt: skip


# --- Closing a whole trade, and several (bots slice 18) ----------------------------------


def _return(default: str) -> str:
    """Where a run's form goes back to: the page it came from when it said so (the
    journal's Manage panel posts `next`, #174), only ever a path in this app."""
    return local_path(request.form.get("next")) or default


def _confirm(heading: str, sentence: str, action: str, fields, back: str):
    """A plain confirm step (bots slice 18): the sentence, then Confirm posts the same
    form again with confirm=1. Nothing has been done yet. A form from the Manage panel
    keeps its `next` through it, and Back goes there."""
    if local_path(request.form.get("next")):
        fields = [*(f for f in fields if f[0] != "next"), ("next", request.form["next"])]
        back = request.form["next"]
    return render_template("automation_confirm.html", heading=heading, sentence=sentence,
                           action=action, fields=fields, back=back)  # fmt: skip


def _kind_words(kind: str) -> str:
    return "at market (0DTE only)" if kind == "market" else "from the mid, chasing toward a fill"


@bp.post("/automation/runs/<int:run_id>/close-trade")
def close_trade(run_id):
    """Close entire trade (Tracker2's manual close with no group): every open group, one
    after another, Limit or Market."""
    kind = request.form.get("order_type", "limit")
    groups = request.form.get("groups", "")  # one side of a split condor: "2,3"
    only = {int(g) for g in groups.split(",") if g.strip().isdigit()} or None
    if request.form.get("confirm") != "1":
        what = "this side" if only else "the whole trade"
        return _confirm(
            f"Close {what}?",
            f"Close every open group of {what} (run {run_id}), one after another, "
            f"{_kind_words(kind)}. It stops at the first group that doesn't close; the rest "
            "keep their stop and target.",
            url_for("automation.close_trade", run_id=run_id),
            [("order_type", kind), ("groups", groups)],
            url_for("automation.run_page", run_id=run_id, _anchor="manage"),
        )  # fmt: skip
    deps = bot_loops.deps_for(services())
    try:
        outcome = bot_manage.close_trade(deps, run_id, kind, only)
    except Exception:  # noqa: BLE001 -- the page always answers
        logger.exception("closing run %s failed", run_id)
        outcome = bot_manage.Outcome(False, bot_manage.CHECK_BROKER)
    flash(outcome.message, "success" if outcome.ok else "warning")
    return redirect(_return(url_for("automation.run_page", run_id=run_id, _anchor="manage")))


@bp.post("/automation/close-selected")
def close_selected():
    """Close selected (Tracker2's /api/bots/close-selected): each ticked 0DTE trade
    closed in full; one refusal never blocks the rest."""
    kind = request.form.get("order_type", "limit")
    ids = request.form.getlist("run_id")
    if request.form.get("confirm") != "1":
        if not ids:
            flash("Tick at least one trade to close.", "warning")
            return redirect(url_for("automation.automation_page", _anchor="open-trades"))
        return _confirm(
            "Close the selected trades?",
            f"Close {len(ids)} trade{'s' if len(ids) != 1 else ''} in full (run "
            f"{', '.join(ids)}), each on its own, {_kind_words(kind)}.",
            url_for("automation.close_selected"),
            [("order_type", kind), *(("run_id", i) for i in ids)],
            url_for("automation.automation_page", _anchor="open-trades"),
        )  # fmt: skip
    deps = bot_loops.deps_for(services())
    results = bot_manage.close_selected(
        deps, request.form.getlist("run_id"), request.form.get("order_type", "limit")
    )
    for run_id, outcome in results:
        where = f"Run {run_id}: " if run_id else ""
        flash(f"{where}{outcome.message}", "success" if outcome.ok else "warning")
    return redirect(url_for("automation.automation_page", _anchor="open-trades"))


# --- Restore missing exits (bots slice 18) -----------------------------------------------


def _item_words(item, with_run: bool) -> str:
    where = f"{item.bot_name}, run {item.run_id}: " if with_run else ""
    what = f"{item.group}'s {item.what}" if item.kind != "group" else item.group
    if item.status == "missing":
        size = f" for {item.contracts}" if item.contracts else ""
        return f"{where}{what} is missing: it goes back at {item.price}{size}."
    tail = f" ({item.detail})" if item.detail else ""
    return f"{where}{what}: {item.status}{tail}."


def _flash_items(items, apply: bool, with_run: bool) -> None:
    if not items:
        flash("Nothing is missing: every open group has the exits its bot sets.", "success")
        return
    for item in items:
        good = item.status == "placed" or (not apply and item.status == "missing")
        flash(_item_words(item, with_run), "success" if good else "warning")
    if not apply and any(i.status == "missing" for i in items):
        flash("That was a check: nothing was sent. Press Restore to put these back.", "success")


@bp.post("/automation/runs/<int:run_id>/restore")
def restore_run(run_id):
    """Check (apply=0) or restore (apply=1) one trade's missing exits (Tracker2's
    rearm.rearm_trade with its dry run)."""
    apply = request.form.get("apply") == "1"
    items = bot_rearm.rearm_trade(bot_loops.deps_for(services()), run_id, dry_run=not apply)
    _flash_items(items, apply, with_run=False)
    return redirect(_return(url_for("automation.run_page", run_id=run_id, _anchor="manage")))


@bp.post("/automation/restore")
def restore_all():
    """Check or restore every open bot trade's missing exits (Tracker2's Settings ->
    Re-arm expired protections, config_bp.rearm_protections)."""
    apply = request.form.get("apply") == "1"
    items = bot_rearm.rearm_all(bot_loops.deps_for(services()), dry_run=not apply)
    _flash_items(items, apply, with_run=True)
    return redirect(url_for("automation.automation_page", _anchor="open-trades"))


# --- The record, clone, archive, the bot file (bots slice 8) ---------------------------

RECORD_PAGE = 50  # trades a page (the strategy pages spec)
RESULTS = {"all", "winners", "losers"}


def _bot_or_404(bot_id):
    bot = bot_store.load(services().session_factory, bot_id)
    if bot is None:
        abort(404)
    return bot


@bp.get("/automation/<int:bot_id>/filters-now")
def filters_now(bot_id):
    """Bots slice 20 (Tracker2's _preview_vix_level, _preview_vix9d_ratio): the saved bot's
    VIX level and VIX9D/VIX ratio filters as they read now. Reads two quotes through the
    filters' own feed; writes nothing and sends nothing."""
    bot = _bot_or_404(bot_id)
    market = bot_loops.deps_for(services()).market
    return jsonify(filters=bot_filters.preview(market, bot, _now()))


@bp.route("/automation/<int:bot_id>/record")
def record_page(bot_id):
    """The bot's record (Tracker2's bot detail page): its numbers and its trades."""
    s = services()
    bot = _bot_or_404(bot_id)
    result = request.args.get("result", "all")
    result = result if result in RESULTS else "all"
    record = _safe(
        "the bot's record",
        lambda: bot_stats.record(s.session_factory, bot, s.positions_cache),
        None,
    )
    closed = record.closed if record else []
    if result == "winners":
        closed = [e for e in closed if e.position.realized_pl > 0]
    elif result == "losers":
        closed = [e for e in closed if e.position.realized_pl <= 0]  # a zero is a loss, as OO
    now = _now()
    figures = {
        e.position.key: (
            trade_rows.realized_percent(e.position),
            trade_rows.days_in_trade(e.position, now),
        )  # fmt: skip
        for e in [*(record.open if record else []), *closed]
    }
    return render_template(
        "automation_record.html",
        bot=bot,
        record=record,
        result=result,
        figures=figures,  # bots slice 20 (_trade_row): P/L % and days, per trade
        page=paginate(closed, RECORD_PAGE),
        bot_tag=_tag_name(bot.tag_id),
        journal_args={"tag": bot.tag_id, "account": f"{bot.broker}:{bot.account_number}"},
    )


# --- Match past trades (#164) -------------------------------------------------------

_LAST_MATCH: dict[int, bot_match.Batch] = {}  # bot id -> its last batch, for Undo


@bp.get("/automation/<int:bot_id>/match")
def match_page(bot_id):
    """The journal's trades that look like this bot's, to link to it and give its tag."""
    s = services()
    bot = _bot_or_404(bot_id)
    found = _safe("the journal", lambda: bot_match.find(s.session_factory, bot, s.positions_cache),
                  None)  # fmt: skip
    return render_template("automation_match.html", bot=bot, found=found,
                           undo=bot_id in _LAST_MATCH)  # fmt: skip


@bp.post("/automation/<int:bot_id>/match")
def match_save(bot_id):
    s = services()
    bot = _bot_or_404(bot_id)
    keys = request.form.getlist("key")
    if not keys:
        flash("Nothing was ticked, so nothing was filed.", "warning")
        return redirect(url_for("automation.match_page", bot_id=bot_id))
    _LAST_MATCH[bot_id] = bot_match.file(s.session_factory, bot, keys)
    n = len(_LAST_MATCH[bot_id].before)
    flash(f"Linked {n} trade{'' if n == 1 else 's'} to this bot and gave them its tag: "
          "they're in its record now.", "success")  # fmt: skip
    return redirect(url_for("automation.match_page", bot_id=bot_id))


@bp.post("/automation/<int:bot_id>/match/undo")
def match_undo(bot_id):
    s = services()
    _bot_or_404(bot_id)
    batch = _LAST_MATCH.pop(bot_id, None)
    if batch is None:
        flash("There's nothing to undo.", "warning")
    else:
        n = bot_match.undo(s.session_factory, batch)
        flash(f"Undone: {n} trade{'' if n == 1 else 's'} back as they were.", "success")
    return redirect(url_for("automation.match_page", bot_id=bot_id))


# --- Match all bots (#169) ----------------------------------------------------------

_LAST_MATCH_ALL: list[list[bot_match.Batch]] = []  # the last batch, for Undo


@bp.get("/automation/match")
def match_all_page():
    """Every unlinked journal trade under the live bot it matches best."""
    s = services()
    found = _safe("the journal", lambda: bot_match.find_all(s.session_factory, s.positions_cache),
                  None)  # fmt: skip
    return render_template("automation_match_all.html", found=found,
                           undo=bool(_LAST_MATCH_ALL))  # fmt: skip


@bp.post("/automation/match")
def match_all_save():
    s = services()
    ticked: dict[int, list[str]] = {}
    for value in request.form.getlist("key"):
        bot_id, _, key = value.partition(":")
        if bot_id.isdigit() and key:
            ticked.setdefault(int(bot_id), []).append(key)
    if not ticked:
        flash("Nothing was ticked, so nothing was matched.", "warning")
        return redirect(url_for("automation.match_all_page"))
    batches = bot_match.file_all(s.session_factory, ticked,
                                 bot_match.matchable_bots(s.session_factory))  # fmt: skip
    _LAST_MATCH_ALL[:] = [batches]
    n = sum(len(b.before) for b in batches)
    flash(f"Matched {n} trade{'' if n == 1 else 's'} to their bots: the journal shows each "
          "bot's name and they're in its record now.", "success")  # fmt: skip
    return redirect(url_for("automation.match_all_page"))


@bp.post("/automation/match/undo")
def match_all_undo():
    s = services()
    if not _LAST_MATCH_ALL:
        flash("There's nothing to undo.", "warning")
    else:
        n = bot_match.undo_all(s.session_factory, _LAST_MATCH_ALL.pop())
        flash(f"Undone: {n} trade{'' if n == 1 else 's'} back as they were.", "success")
    return redirect(url_for("automation.match_all_page"))


@bp.post("/automation/<int:bot_id>/clone")
def clone(bot_id):
    """A copy, always off, in the original's mode (Tracker2's api_clone_bot)."""
    _bot_or_404(bot_id)
    new_id, problems = bot_store.clone(services().session_factory, bot_id, _now())
    if new_id is None:
        flash("Couldn't copy this bot: " + " ".join(problems.values()), "warning")
        return redirect(url_for("automation.bot_page", bot_id=bot_id))
    flash("Copied. The copy is off: change what you want, then arm it.", "success")
    return redirect(url_for("automation.bot_page", bot_id=new_id))


@bp.post("/automation/<int:bot_id>/archive")
def archive(bot_id):
    """Archive: refused while anything is open (Tracker2's api_archive_bot)."""
    _bot_or_404(bot_id)
    reasons = bot_store.archive(services().session_factory, bot_id, _now())
    if reasons:
        flash("Can't archive it while anything is open: " + "; ".join(reasons) + ".", "warning")
        return redirect(url_for("automation.bot_page", bot_id=bot_id))
    flash("Archived. It's off and won't trade until you unarchive it.", "success")
    return redirect(url_for("automation.automation_page"))


@bp.post("/automation/<int:bot_id>/unarchive")
def unarchive(bot_id):
    _bot_or_404(bot_id)
    bot_store.unarchive(services().session_factory, bot_id, _now())
    flash("Unarchived. It's off: arm it when you're ready.", "success")
    return redirect(url_for("automation.bot_page", bot_id=bot_id))


@bp.get("/automation/<int:bot_id>/export")
def export(bot_id):
    """The bot file: its definition only, never an account, size or tag."""
    bot = _bot_or_404(bot_id)
    if bot.archived:
        flash("An archived bot can't be shared: unarchive it first.", "warning")
        return redirect(url_for("automation.bot_page", bot_id=bot_id))
    name = bot_file.file_name(bot.name)
    return Response(
        bot_file.dumps(bot, _now()),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _import_page(stage, status=200, **values):
    with services().session_factory() as session:
        journal_tags = _journal_tags(session)
        accounts = _accounts(session)
    values.setdefault("problems", {})
    page = render_template(
        "automation_import.html",
        stage=stage,
        journal_tags=journal_tags,
        accounts=accounts,
        broker_names=BROKER_NAMES,
        **values,
    )
    return page, status


def _uploaded() -> str | bytes:
    """The file chosen, else the JSON pasted. Never more than the file limit + 1 byte
    is read, so a huge upload is refused, not held."""
    upload = request.files.get("file")
    if upload is not None and upload.filename:
        return upload.read(bot_file.MAX_BYTES + 1)
    return request.form.get("json", "")


def _text(raw: str | bytes) -> str:
    """The file as text: UTF-8, with a byte-order mark (some editors add one) dropped."""
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    return text.removeprefix("\ufeff")


@bp.route("/automation/import")
def import_page():
    return _import_page("start")[0]


# What the importer fills in, so the preview leaves validation's word on them out.
_IMPORTER_FIELDS = ("tag", "broker", "contracts", "limits")


def _preview_values(imported, raw_text, choices=None, source_url=None):
    bot = imported.bot
    choices = choices or {
        "name": bot_store.imported_name(services().session_factory, bot.name),
        "tag_id": "", "account": "", "contracts": "1", "max_per_trade": "",
        "max_per_day": "",  # decision 0058: blank, worked out
    }  # fmt: skip
    trial = replace(bot, tag_id=0, account_number="x")
    bot_problems = {
        key: text for key, text in validate(trial, {0}).items() if key not in _IMPORTER_FIELDS
    }
    return {"bot": bot, "exported_at": imported.exported_at, "json": raw_text,
            "choices": choices, "bot_problems": bot_problems,
            "source_url": source_url, "from_tracker2": imported.source == "tracker2",
            "notes": imported.notes}  # fmt: skip


PLACE_ONLY = ("Place only is built into this app: choose it under Manage with on the "
              "Trade page")  # fmt: skip


def _analyzer_file(text):
    """The Tracker2 file in the text when it is an analyzer bot (it imports as a manual
    bot, #158, and needs no bot behind it), else None. Never raises."""
    try:
        data = json.loads(text)
    except ValueError, RecursionError:
        return None
    return data if tracker2_file.is_analyzer(data) else None


def _manual_import(stage, data, text, name=None, source_url=None):
    """A Tracker2 analyzer bot: Place only is refused with its message; any other is
    shown as the manual bot it becomes (stage "preview") or saved (stage "save")."""
    try:
        rules, notes = tracker2_file.manual_of(data)
    except bot_file.FileError as error:
        return _import_page("start", 400, error=str(error))
    if rules is None:
        return _import_page("start", 400, error=PLACE_ONLY)
    if stage == "save":
        rules = replace(rules, name=(name or "").strip()[:100] or rules.name)
        try:
            if not rules.name:
                raise ValueError("no name")
            manual_bots.save(services().session_factory, None, rules, _now())
        except ValueError:
            problem = ("Give the manual bot a name." if not rules.name
                       else "Another manual bot already has that name.")  # fmt: skip
            return _import_page(
                "manual",
                400,
                manual=rules,
                notes=notes,
                json=text,
                problems={"name": problem},
                source_url=source_url,
            )
        flash("Imported as a manual bot. Attach it to a trade from the Trade page.", "success")
        return redirect(url_for("automation.automation_page"))
    base, n = rules.name, 2
    taken = set(manual_bots.names(services().session_factory).values())
    if base in taken:
        rules = replace(rules, name=f"{base[:88]} (imported)")
        while rules.name in taken:
            rules = replace(rules, name=f"{base[:80]} (imported {n})")
            n += 1
    return _import_page("manual", manual=rules, notes=notes, json=text,
                        source_url=source_url)  # fmt: skip


@bp.post("/automation/import/preview")
def import_preview():
    """Read the link, file or pasted text and show what it holds; saves nothing
    (api_import_bot_preview). A link is fetched here only: the preview carries
    the text on to Import."""
    url = request.form.get("url", "").strip()
    try:
        if url:
            text = bot_fetch.fetch(url)
        else:
            raw = _uploaded()
            if len(raw) > bot_file.MAX_BYTES:
                raise bot_file.FileError("This file is too large to be a bot file.")
            text = _text(raw)
        analyzer = _analyzer_file(text)
        if analyzer is not None:
            return _manual_import("preview", analyzer, text, source_url=url or None)
        imported = bot_file.parse(text)
    except bot_file.FileError as error:
        return _import_page("start", 400, error=str(error), url=url)
    except UnicodeDecodeError:
        return _import_page("start", 400, error="This bot file isn't plain text (UTF-8).")
    return _import_page("preview", **_preview_values(imported, text, source_url=url or None))


def _source_url(text) -> str | None:
    """The link a previewed bot was fetched from, if it's one bot_link accepts."""
    if not text:
        return None
    try:
        return bot_link.check(str(text))
    except bot_file.FileError:
        return None


def _whole(text) -> int | None:
    text = str(text or "").strip()
    return int(text) if text.isdigit() else None


_CHOICES = ("name", "tag_id", "account", "contracts", "max_per_trade", "max_per_day")


@bp.post("/automation/import/save")
def import_save():
    """Create the bot from the same JSON, with the importer's choices: always off
    and Live, validated like any save (api_import_bot)."""
    raw = _text(request.form.get("json", ""))
    analyzer = _analyzer_file(raw)
    if analyzer is not None:
        return _manual_import("save", analyzer, raw, request.form.get("name"),
                              source_url=_source_url(request.form.get("source_url")))  # fmt: skip
    try:
        imported = bot_file.parse(raw)
    except bot_file.FileError as error:
        return _import_page("start", 400, error=str(error))
    form = request.form
    source_url = _source_url(form.get("source_url"))
    choices = {key: str(form.get(key, "")).strip() for key in _CHOICES}
    form_problems = {}
    sizes = {}
    for key, words in (("contracts", "Contracts per entry"),
                       ("max_per_trade", "Max contracts per trade"),
                       ("max_per_day", "Max contracts per day")):  # fmt: skip
        sizes[key] = _whole(choices[key])
        if key != "contracts" and not choices[key]:
            continue  # decision 0058: a blank limit is worked out
        if sizes[key] is None:
            blank = "" if key == "contracts" else ", or blank"
            form_problems["contracts" if key == "contracts" else "limits"] = (
                f"{words} must be a whole number, 1 or more{blank}."
            )
    broker, _, number = choices["account"].partition(":")
    bot = replace(
        imported.bot,
        name=choices["name"] or bot_store.imported_name(services().session_factory,
                                                        imported.bot.name),
        tag_id=_whole(choices["tag_id"]), broker=broker or "tastytrade",
        account_number=number, contracts=sizes["contracts"] or 0,
        max_per_trade=sizes["max_per_trade"], max_per_day=sizes["max_per_day"],
    )  # fmt: skip
    bot_id, problems = bot_store.import_bot(
        services().session_factory,
        bot,
        imported.exported_at,
        _now(),
        form_problems,
        listed=_listed,
        source_url=source_url,
    )
    if bot_id is None:
        values = _preview_values(imported, raw, choices, source_url)
        return _import_page("preview", 400, problems=problems, **values)
    flash("Imported. It's off and Live: check it, then arm it when you're ready.", "success")
    return redirect(url_for("automation.bot_page", bot_id=bot_id, imported=1))
