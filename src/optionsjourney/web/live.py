"""The journal's bot chips, pushed live (bots slice 20; Tracker2's
override_tags.notify_changed and journal.html refreshBotTags).

Tracker2 calls notify_changed after each save that can change a chip. Here one
listener on the app's session factory does it for every path at once (the run page,
the watch rules, profit and time actions, restoring exits): `after_flush` notes the
run of each changed trade override and of each exit row that is new, deleted or whose
state changed; `after_commit` pushes `bot_tags_changed {run_id}` once per run; a
rollback forgets them. A push is only "re-read this run's chips": the page fetches them
(GET /journal/bot-chips/<run_id>). It never raises: a missed refresh must not fail the
save that caused it."""

import logging
from collections.abc import Callable

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session, sessionmaker

from optionsjourney.db.models import (
    BotExit,
    BotTradeOverride,
    BrokerTransaction,
    ExpirySettlement,
    PaperFill,
)

logger = logging.getLogger(__name__)
EVENT = "bot_tags_changed"
_KEY = "optionsjourney.chips_changed"
JOURNAL_EVENT = "journal_changed"
_JOURNAL_KEY = "optionsjourney.journal_changed"
PAPER_EVENT = "paper_journal_changed"
_PAPER_KEY = "optionsjourney.paper_journal_changed"


def _changed_runs(session: Session) -> set[int]:
    runs = set()
    for obj in (*session.new, *session.deleted):
        if isinstance(obj, BotTradeOverride | BotExit):
            runs.add(obj.run_id)
    for obj in session.dirty:
        override = isinstance(obj, BotTradeOverride)
        state_moved = isinstance(obj, BotExit) and inspect(obj).attrs.state.history.has_changes()
        if override or state_moved:
            runs.add(obj.run_id)
    return {r for r in runs if r is not None}


def watch_chips(session_factory: sessionmaker, emit: Callable[[int], None]) -> None:
    """Call `emit(run_id)` after each commit that changed a run's overrides or exits."""

    def after_flush(session, _context):
        try:
            session.info.setdefault(_KEY, set()).update(_changed_runs(session))
        except Exception:  # noqa: BLE001 -- never fails the flush
            logger.exception("couldn't note the runs whose chips changed")

    def after_commit(session):
        for run_id in sorted(session.info.pop(_KEY, set())):
            try:
                emit(run_id)
            except Exception:  # noqa: BLE001 -- a missed refresh never fails the save
                logger.warning("%s push failed for run %s", EVENT, run_id, exc_info=True)

    def after_rollback(session, previous_transaction):
        if previous_transaction.nested:
            return  # a savepoint: the outer transaction's changes still stand
        session.info.pop(_KEY, None)

    event.listen(session_factory, "after_flush", after_flush)
    event.listen(session_factory, "after_commit", after_commit)
    event.listen(session_factory, "after_soft_rollback", after_rollback)


def watch_journal(session_factory: sessionmaker, emit: Callable[[], None]) -> None:
    """Call `emit()` once after each commit that added, changed or removed a broker
    transaction or an expiry settlement: the journal's trades may have changed (a fill
    the account stream reported, a timed sync, Sync now). The page reloads itself."""
    _watch(session_factory, (BrokerTransaction, ExpirySettlement), _JOURNAL_KEY, JOURNAL_EVENT,
           emit)  # fmt: skip


def watch_paper(session_factory: sessionmaker, emit: Callable[[], None]) -> None:
    """#183: call `emit()` once after each commit that saved a pretend fill: the Paper
    trades view reloads itself, as the journal does on journal_changed."""
    _watch(session_factory, (PaperFill,), _PAPER_KEY, PAPER_EVENT, emit)


def _watch(session_factory: sessionmaker, types: tuple, key: str, name: str, emit) -> None:
    def after_flush(session, _context):
        try:
            if any(isinstance(obj, types)
                   for obj in (*session.new, *session.dirty, *session.deleted)):  # fmt: skip
                session.info[key] = True
        except Exception:  # noqa: BLE001 -- never fails the flush
            logger.exception("couldn't note a %s change", name)

    def after_commit(session):
        if session.info.pop(key, False):
            try:
                emit()
            except Exception:  # noqa: BLE001 -- a missed refresh never fails the save
                logger.warning("%s push failed", name, exc_info=True)

    def after_rollback(session, previous_transaction):
        if not previous_transaction.nested:
            session.info.pop(key, None)

    event.listen(session_factory, "after_flush", after_flush)
    event.listen(session_factory, "after_commit", after_commit)
    event.listen(session_factory, "after_soft_rollback", after_rollback)
