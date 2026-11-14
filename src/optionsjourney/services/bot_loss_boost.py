"""The loss boost's lookup (#328; Tracker2's loss_boost.multiplier_for): whether a bot's
previous filled trade hit a stop, read from its runs, exits and paper fills. The rules
are bots/loss_boost.py's."""

from datetime import date

from sqlalchemy import select

from optionsjourney.bots import loss_boost, schedule
from optionsjourney.bots.definition import BotDef, loss_boost_multiplier
from optionsjourney.db.models import BotExit, BotRun, PaperFill

FILLED = ("filled", "partial")  # Tracker2's FILLED_RUN


def previous_run(session, bot_id: int, day: date, paper: bool) -> BotRun | None:
    """The bot's most recent filled entry before `day` of this kind (paper or live), never
    a tranche, a Preview or a Trade-page entry (Tracker2 leaves out its analyzer's)."""
    ticket = schedule.BY_HAND_PREFIXES[0]  # "ticket-": the Trade page's entries
    return session.scalars(
        select(BotRun)
        .where(
            BotRun.bot_id == bot_id,
            BotRun.parent_run_id.is_(None),
            BotRun.trading_date < day,
            BotRun.status.in_(FILLED),
            BotRun.paper.is_(paper),
            ~BotRun.key.startswith(ticket),
        )
        .order_by(BotRun.trading_date.desc(), BotRun.id.desc())
        .limit(1)
    ).first()


def stopped(session, run: BotRun) -> bool:
    """Any group of `run`, or of its tranches, closed by its stop (loss_boost's rules)."""
    ids = [run.id, *session.scalars(select(BotRun.id).where(BotRun.parent_run_id == run.id))]
    if run.paper:
        fills = session.execute(
            select(PaperFill.kind, PaperFill.reason).where(PaperFill.run_id.in_(ids))
        )
        return any(loss_boost.paper_stop(kind, reason or "") for kind, reason in fills)
    exits = session.scalars(select(BotExit).where(BotExit.run_id.in_(ids)))
    return any(loss_boost.stop_exit(row.kind, row.state, _by(row.log)) for row in exits)


def _by(log) -> str | None:
    """The rule that started a close, from its first log entry (bot_exits._by_of)."""
    first = next(iter(log or ()), None)
    return first.get("by") if isinstance(first, dict) else None


def boost_for(session_factory, bot: BotDef, day: date) -> loss_boost.Boost | None:
    """The boost for the bot's entry on `day`, or None: the boost is off, or its previous
    filled trade (paper for a Paper bot, live for a Live one) didn't hit a stop."""
    multiplier = loss_boost_multiplier(bot)
    if multiplier <= 1 or bot.id is None:
        return None
    with session_factory() as session:
        run = previous_run(session, bot.id, day, paper=not bot.live)
        if run is None or not stopped(session, run):
            return None
        return loss_boost.Boost(multiplier, run.id, run.trading_date)
