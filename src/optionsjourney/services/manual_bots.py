"""Manual bots (#158): the store. A deleted manual bot keeps its row (deleted_at), so
trades already using it keep their copy and run history keeps its name."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from optionsjourney.bots.manual import ManualRules
from optionsjourney.db.models import ManualBot
from optionsjourney.services.bot_store import _alert_rule_of, _alert_rule_row


def _rules(row: ManualBot) -> ManualRules:
    def amount(value):
        return None if value is None else Decimal(value)

    return ManualRules(
        name=row.name, target_value=amount(row.target_value), target_unit=row.target_unit,
        stop_value=amount(row.stop_value), stop_unit=row.stop_unit,
        close_time=row.close_time, close_dte=row.close_dte,
        alerts=tuple(_alert_rule_of(raw) for raw in row.alerts or ()),
    )  # fmt: skip


def listing(session_factory: sessionmaker) -> list[tuple[int, ManualRules]]:
    """The manual bots not deleted, by name."""
    with session_factory() as session:
        rows = session.scalars(
            select(ManualBot).where(ManualBot.deleted_at.is_(None)).order_by(ManualBot.name)
        )
        return [(row.id, _rules(row)) for row in rows]


def load(session_factory: sessionmaker, bot_id: int) -> ManualRules | None:
    with session_factory() as session:
        row = session.get(ManualBot, bot_id)
        return None if row is None or row.deleted_at is not None else _rules(row)


def save(session_factory: sessionmaker, bot_id: int | None, rules: ManualRules,
         now: datetime) -> int:  # fmt: skip
    """Adds (bot_id None) or changes a manual bot; its id. A name another manual bot has,
    deleted ones included (the name stays unique), raises ValueError("name taken")."""
    with session_factory() as session:
        taken = session.scalar(
            select(ManualBot.id).where(ManualBot.name == rules.name, ManualBot.id != (bot_id or 0))
        )
        if taken is not None:
            raise ValueError("name taken")
        row = None if bot_id is None else session.get(ManualBot, bot_id)
        if row is None or row.deleted_at is not None:
            if bot_id is not None:
                raise ValueError("no such manual bot")
            row = ManualBot(created_at=now)
            session.add(row)
        row.name = rules.name
        row.target_value, row.target_unit = rules.target_value, rules.target_unit
        row.stop_value, row.stop_unit = rules.stop_value, rules.stop_unit
        row.close_time, row.close_dte = rules.close_time, rules.close_dte
        row.alerts = [_alert_rule_row(rule) for rule in rules.alerts]
        row.updated_at = now
        session.commit()
        return row.id


def delete(session_factory: sessionmaker, bot_id: int, now: datetime) -> None:
    """Hides a manual bot; trades already using it keep their own copy of its rules."""
    with session_factory() as session:
        row = session.get(ManualBot, bot_id)
        if row is not None and row.deleted_at is None:
            row.deleted_at = row.updated_at = now
            session.commit()


def names(session_factory: sessionmaker) -> dict[int, str]:
    """Every manual bot's name by id, deleted ones included (for run history)."""
    with session_factory() as session:
        return dict(session.execute(select(ManualBot.id, ManualBot.name)).all())
