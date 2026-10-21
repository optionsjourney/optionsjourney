"""A bot's side of alerts (spec: bots slice 9): what an alert about a bot says
about its trade, read when it's sent (Tracker2's alert_format._context), and the
bot's default alerts (section 4)."""

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from optionsjourney.bots import caps, default_alerts, timing
from optionsjourney.core import alert_format
from optionsjourney.core.alert_format import SideProtection
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import Bot, BotExit, BotLegGroup, BotRun, UserAlert
from optionsjourney.services import bot_paper, bot_store

logger = logging.getLogger(__name__)
TARGET_ON = ("working", "armed")  # a target resting at the broker, or watched by the app


@dataclass(frozen=True)
class BotView:
    name: str
    alert_name: str | None
    channel: str
    account: str


def bot_view(session_factory, bot_id: int | None) -> BotView | None:
    """The bot's name, alert name and channel, or None (no bot, or it can't be read:
    the alert then goes everywhere, under its plain title)."""
    if bot_id is None:
        return None
    try:
        with session_factory() as session:
            bot = session.get(Bot, bot_id)
            if bot is None:
                return None
            return BotView(bot.name, (bot.alert_name or "").strip() or None,
                           bot.alert_channel or "all", bot.account_number or "")  # fmt: skip
    except Exception:  # noqa: BLE001 -- the alert goes without it
        logger.warning("alert: bot %s couldn't be read", bot_id, exc_info=True)
        return None


def context(session_factory, bot: BotView, run_id: int | None) -> list[tuple[str, str | None]]:
    """The Trade, Legs and Protection lines. Best effort, each on its own: a line
    that can't be read is left out, never the alert. Its own session, so it never
    sees or writes a bot's half-made changes."""

    def trade(account: str) -> str:
        return " · ".join(
            part for part in (bot.name, f"••{account[-4:]}" if account else "",
                              f"run {run_id}" if run_id else "") if part
        )  # fmt: skip

    if run_id is None:
        return [("Trade", trade(bot.account))]
    try:
        with session_factory() as session:
            run = session.get(BotRun, run_id)
            if run is None:
                return [("Trade", trade(bot.account))]
            account = run.account_number or bot.account  # the run's, at its claim
            legs = list(run.legs or [])
            contracts, status = run.contracts, run.status
            exits = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
            names = {g.position: g.name for g in session.scalars(
                select(BotLegGroup).where(BotLegGroup.bot_id == run.bot_id))}  # fmt: skip
    except Exception:  # noqa: BLE001 -- the alert goes without them
        logger.warning("alert: run %s couldn't be read", run_id, exc_info=True)
        return [("Trade", trade(bot.account))]
    lines: list[tuple[str, str | None]] = [("Trade", trade(account))]
    readers = (("Legs", lambda: _legs(legs, contracts)),
               ("Protection", lambda: _protection(legs, exits, names, status)))  # fmt: skip
    for label, read in readers:
        try:
            lines.append((label, read()))
        except Exception:  # noqa: BLE001 -- that line is left out
            logger.warning("alert: the %s line for run %s failed", label, run_id, exc_info=True)
    return lines


def _legs(legs: list[dict], contracts: int) -> str | None:
    units = max(int(contracts or 0), 1)
    out = []
    for leg in legs:
        parsed = parse_occ(leg.get("symbol", ""))
        root, details = parsed if parsed else ("", None)
        qty = int(leg.get("ratio", 1)) * units * (-1 if leg.get("side") == "sell" else 1)
        strike = details.strike if details else leg.get("strike")
        kind = (details.put_call if details else str(leg.get("right", "")).upper())[:1]
        out.append((qty, root, details.expiration if details else None, strike, kind))
    return alert_format.legs_line(out) or None


def _protection(legs, exits, names, status) -> str | None:
    """Each side's working stop and its target; only once the run holds a fill."""
    if status not in caps.HOLDING:
        return None
    sides = []
    for position in sorted({leg.get("group", 0) for leg in legs}):
        rows = [row for row in exits if row.group_position == position]
        if timing.side_state((row.kind, row.state) for row in rows) == "closed":
            sides.append(SideProtection(names.get(position, f"Group {position + 1}"), None, None,
                                        closed=True))  # fmt: skip
            continue
        stop = next((row.trigger for row in reversed(rows)
                     if row.kind == "stop" and row.state == "working"), None)  # fmt: skip
        targets = [row for row in rows if row.kind == "target" and row.state in TARGET_ON]
        target = targets[-1] if targets else None
        sides.append(SideProtection(
            names.get(position, f"Group {position + 1}"), stop,
            target.limit if target is not None else None,
            target is not None and target.state == "armed",
        ))  # fmt: skip
    return alert_format.protection_line(sides) or None


# --- Default alerts (trade_alerts/defaults.py) -------------------------------------------


def _spot(deps, underlying: str = "SPX") -> Decimal | None:
    """The underlying's price now (its own, XSP's for XSP: defaults._spot reads the legs'
    underlying), only to skip a strike alert already true; None if there's no price,
    and the alert is made anyway."""
    try:
        quote = deps.hub.latest(underlying)
    except Exception:  # noqa: BLE001
        return None
    if quote is None:
        return None
    return quote.last if quote.last is not None else quote.mark


def apply_defaults(deps, run_id: int) -> int:
    """Make the default alerts of the entry `run_id` belongs to, once per entry and
    rule (defaults._apply_run): only for a live fill of a bot that has them on.
    Returns how many were made. A rule that can't apply is logged and skipped."""
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or not run.live or not run.filled_legs:
            return 0
        root_id = run.parent_run_id or run.id
        root = session.get(BotRun, root_id) or run
        family = list(session.scalars(select(BotRun).where(
            (BotRun.id == root_id) | (BotRun.parent_run_id == root_id))))  # fmt: skip
        legs = list(root.legs or run.legs or [])
        credit = run.fill_credit if root.fill_credit is None else root.fill_credit
        contracts = sum(r.contracts or 0 for r in family if r.filled_legs)
        bot_id = run.bot_id
        have = set(session.scalars(select(UserAlert.rule_key).where(
            UserAlert.bot_run_id == root_id)))  # fmt: skip
    bot = bot_store.load(deps.session_factory, bot_id)
    if bot is None or not bot.use_default_alerts or not bot.default_alerts:
        return 0
    wants_spot = any(rule.type == "strike_offset" for rule in bot.default_alerts)
    specs, skipped = default_alerts.specs(
        bot.name, root_id, bot.default_alerts, credit, contracts, legs,
        _spot(deps, bot.underlying) if wants_spot else None, underlying=bot.underlying,
    )  # fmt: skip
    for why in skipped:
        logger.warning("bot %s run %s: default alert skipped: %s", bot_id, root_id, why)
    made = 0
    for spec in specs:
        if spec.key in have:
            continue
        with deps.session_factory() as session:
            session.add(UserAlert(
                kind=spec.kind, target=spec.target or f"run {root_id}", label=spec.name[:160],
                direction=spec.direction, level=spec.level, note=spec.note or None, active=True,
                created_at=deps.now(), dismissed=False, legs=spec.legs or None,
                bot_run_id=root_id, rule_key=spec.key, quiet=bot.default_alerts_in_app_only,
            ))  # fmt: skip
            try:
                session.commit()
                made += 1
            except IntegrityError:  # made meanwhile: one per entry and rule
                session.rollback()
    return made


def sweep(session_factory, now) -> int:
    """Switch off and clear the default alerts of entries with no side open any more,
    fired ones too (service.sweep_closed). Returns how many."""
    with session_factory() as session:
        rows = list(session.scalars(select(UserAlert).where(
            UserAlert.bot_run_id.is_not(None),
            UserAlert.active | (UserAlert.triggered_at.is_not(None) & ~UserAlert.dismissed),
        )))  # fmt: skip
        if not rows:
            return 0
        roots = {row.bot_run_id for row in rows}
        runs = list(session.scalars(select(BotRun).where(
            BotRun.id.in_(roots) | BotRun.parent_run_id.in_(roots))))  # fmt: skip
        exits: dict[int, list] = {}
        for row in session.scalars(select(BotExit).where(BotExit.run_id.in_([r.id for r in runs]))):
            exits.setdefault(row.run_id, []).append(caps.ExitRow.of(row))
        closed = bot_paper.closed_groups(session, runs)
        held = [
            caps.RunRow(run.parent_run_id or run.id, run.id, run.status, tuple(run.legs),
                        tuple(exits.get(run.id, ())), bool(run.filled_legs),
                        closed.get(run.id, frozenset()))
            for run in runs if run.status in caps.HOLDING
        ]  # fmt: skip
        open_ids = set(caps.open_runs(held, now))
        still = {row.root_id for row in held if row.run_id in open_ids}
        done = 0
        for row in rows:
            if row.bot_run_id not in still:
                row.active, row.dismissed = False, True
                done += 1
        session.commit()
    return done


def bot_of_run(session_factory, run_id: int) -> int | None:
    with session_factory() as session:
        run = session.get(BotRun, run_id)
        return run.bot_id if run is not None else None
