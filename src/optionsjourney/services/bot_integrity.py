"""Integrity checks over live bot runs, and their alerts (spec: bots slice 8,
section 6; Tracker2's integrity.py check_and_alert).

Run from the monitor loop, last, on its own. Each check runs on its own per
run, so a bug in one never silences the others. Each finding is keyed by run,
check and subject (bot_integrity_alerts): alerted once, cleared when it goes
away so a recurrence alerts again. Alert only, never repair: nothing here
places, cancels or replaces an order or changes a run."""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select

from optionsjourney.bots import caps, integrity
from optionsjourney.bots.definition import MAX_DTE
from optionsjourney.bots.integrity import ExitFact
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.db.models import Bot, BotExit, BotIntegrityAlert, BotRun, BotTradeOverride
from optionsjourney.services import bot_entry, bot_exits, bot_store

logger = logging.getLogger(__name__)
# Runs older than this can't still be live: a leg is at most MAX_DTE days out (slice 14)
LOOKBACK = timedelta(days=MAX_DTE + 10)


@dataclass(frozen=True)
class RunFinding:
    run_id: int
    bot_id: int
    bot_name: str
    check: str
    subject: str
    summary: str


@dataclass(frozen=True)
class _Run:
    run_id: int
    bot_id: int
    bot_name: str
    names: dict
    exits: tuple
    # The sides whose stop you switched off (slice 6, slice 18): True when the target is
    # off too (exits off).
    off: dict = field(default_factory=dict)


def _fact(row: BotExit) -> ExitFact:
    """What the checks need of a row: its legs (bot_exits.exit_legs; a wing row's own
    leg) and its anchor (None for a group the app watches)."""
    if row.kind == "wing":
        anchor, leg = bot_exits._wing_leg(row)
        legs = (leg.symbol,)
    else:
        legs = tuple(leg.symbol for leg in bot_exits.exit_legs(row))
        anchor = None if row.watched else (row.anchor_symbol or row.short_symbol or None)
    return ExitFact(row.id, row.group_position, row.kind, row.state, row.broker_order_id,
                    legs, anchor, row.updated_at)  # fmt: skip


def _live_runs(deps, now) -> list[_Run]:
    """Runs still holding fills whose legs haven't all expired, with their exits."""
    since = (now.astimezone(MARKET_TZ) - LOOKBACK).date()
    with deps.session_factory() as session:
        runs = [
            (run.id, run.bot_id, [leg.get("symbol", "") for leg in run.legs])
            for run in session.scalars(
                select(BotRun).where(
                    BotRun.status.in_(caps.HOLDING),
                    BotRun.trading_date >= since,
                    BotRun.paper.is_(False),  # a paper run holds nothing at the broker
                )
            )
            if run.filled_legs
        ]
        runs = [r for r in runs if integrity.is_live(r[2], now)]
        ids = [run_id for run_id, _, _ in runs]
        exits: dict[int, list[ExitFact]] = {}
        rows = session.scalars(select(BotExit).where(BotExit.run_id.in_(ids))) if ids else ()
        for row in rows:
            exits.setdefault(row.run_id, []).append(_fact(row))
        # {run: {side: True if both switches are off, False if only the stop}}: a side
        # whose stop you switched off is reported as off, never as unprotected.
        off: dict[int, dict[int, bool]] = {}
        overrides = (
            session.scalars(
                select(BotTradeOverride).where(
                    BotTradeOverride.run_id.in_(ids), BotTradeOverride.stop_off.is_(True)
                )
            )
            if ids
            else ()
        )
        for row in overrides:
            off.setdefault(row.run_id, {})[row.group_position] = bool(row.target_off)
    bots: dict[int, object] = {}
    out = []
    for run_id, bot_id, _ in runs:
        if bot_id not in bots:
            bots[bot_id] = bot_store.load(deps.session_factory, bot_id)
        bot = bots[bot_id]
        names = {g.position: g.name for g in bot.groups} if bot else {}
        name = bot.name if bot else "A bot"
        out.append(_Run(run_id, bot_id, name, names, tuple(exits.get(run_id, ())),
                        off.get(run_id, {})))  # fmt: skip
    return out


def run_checks(deps, now) -> tuple[list[RunFinding], set[tuple[int, str]]]:
    """Every finding, and the (run, check) pairs whose check raised: what those
    would say is unknown, so their findings are kept, not cleared."""
    found, failed = [], set()
    for run in _live_runs(deps, now):
        for check in integrity.CHECKS:
            try:
                for f in check(list(run.exits), run.names, now, run.off):
                    found.append(RunFinding(run.run_id, run.bot_id, run.bot_name, f.check,
                                            f.subject, f.summary))  # fmt: skip
            except Exception:  # noqa: BLE001 -- one check never silences the others
                failed.add((run.run_id, getattr(check, "__name__", str(check))))
                logger.exception(
                    "integrity check %s failed for run %s", getattr(check, "__name__", check),
                    run.run_id,
                )  # fmt: skip
    return found, failed


def check(deps) -> list[RunFinding]:
    """Run every check, alert each new finding once, clear the ones gone. Never raises."""
    now = deps.now()
    try:
        found, failed = run_checks(deps, now)
    except Exception:  # noqa: BLE001 -- the monitor's other steps matter more
        logger.exception("integrity checks failed")
        return []
    try:
        _record(deps, found, now, failed)
    except Exception:  # noqa: BLE001
        logger.exception("integrity alerting failed")
    return found


def _record(deps, found: list[RunFinding], now, failed=frozenset()) -> None:
    seen = set()
    for finding in found:
        key = (finding.run_id, finding.check, finding.subject)
        seen.add(key)
        with deps.session_factory() as session:
            row = session.scalar(
                select(BotIntegrityAlert).where(
                    BotIntegrityAlert.run_id == finding.run_id,
                    BotIntegrityAlert.check_key == finding.check,
                    BotIntegrityAlert.subject == finding.subject,
                )
            )
            if row is None:
                row = BotIntegrityAlert(
                    run_id=finding.run_id, check_key=finding.check, subject=finding.subject,
                    summary=finding.summary, first_seen_at=now,
                )  # fmt: skip
                session.add(row)
            if row.cleared_at is not None:  # gone and back: a new occurrence
                row.cleared_at, row.notified_at, row.first_seen_at = None, None, now
            row.summary = finding.summary
            session.commit()
            if row.notified_at is not None:
                continue
            bot_entry.alert(
                deps,
                f"Bot integrity: {finding.bot_name}",
                finding.summary,
                f"/automation/runs/{finding.run_id}",
            )
            row.notified_at = now
            session.commit()
    with deps.session_factory() as session:
        for row in session.scalars(
            select(BotIntegrityAlert).where(BotIntegrityAlert.cleared_at.is_(None))
        ):
            if (row.run_id, row.check_key, row.subject) in seen:
                continue
            if (row.run_id, row.check_key) in failed:
                continue  # its check raised this pass: not known to be gone
            row.cleared_at = now
        session.commit()


def open_findings(session_factory) -> list[dict]:
    """The findings not yet cleared, newest first, for the Automation page."""
    with session_factory() as session:
        rows = session.execute(
            select(BotIntegrityAlert, BotRun.bot_id, Bot.name)
            .join(BotRun, BotRun.id == BotIntegrityAlert.run_id)
            .join(Bot, Bot.id == BotRun.bot_id)
            .where(BotIntegrityAlert.cleared_at.is_(None))
            .order_by(BotIntegrityAlert.first_seen_at.desc(), BotIntegrityAlert.id.desc())
        ).all()
        return [
            {"run_id": row.run_id, "bot_id": bot_id, "bot": name, "check": row.check_key,
             "summary": row.summary, "since": row.first_seen_at}
            for row, bot_id, name in rows
        ]  # fmt: skip


def findings_by_run(session_factory, run_ids) -> dict[int, list[str]]:
    """{run id: its findings not yet cleared, oldest first} for the given runs: the
    journal's red bot badge on an open trade that needs a look (#242; Tracker2's
    protection_alerts.alerts_for_runs). A finding clears itself once fixed."""
    run_ids = {r for r in run_ids if r is not None}
    if not run_ids:
        return {}
    with session_factory() as session:
        rows = session.scalars(
            select(BotIntegrityAlert)
            .where(BotIntegrityAlert.run_id.in_(run_ids), BotIntegrityAlert.cleared_at.is_(None))
            .order_by(BotIntegrityAlert.first_seen_at, BotIntegrityAlert.id)
        )
        found: dict[int, list[str]] = {}
        for row in rows:
            found.setdefault(row.run_id, []).append(row.summary)
        return found
