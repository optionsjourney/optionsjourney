"""The app's own alerts (#186): a settlement at the close, a correction to a P/L it
showed, a broker's record that differs. Each is stored once (`dedupe_key`), stays unread
until the user opens the Alerts page, and goes out through the notification channels.

`set_on_change` registers one callback (the live page's refresh) that runs after a new
alert and after alerts are marked read. A failing callback or channel never fails the
caller."""

import logging
from collections.abc import Callable, Iterable
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from optionsjourney.db.models import AppAlert
from optionsjourney.services import notify
from optionsjourney.services.user_alerts import Fired

logger = logging.getLogger(__name__)
_on_change: Callable[[], None] | None = None


def set_on_change(callback: Callable[[], None] | None) -> None:
    global _on_change  # noqa: PLW0603 -- one listener per process
    _on_change = callback


def _changed() -> None:
    if _on_change is None:
        return
    try:
        _on_change()
    except Exception:
        logger.exception("app alerts: the change callback failed")


def add(
    session_factory, *, dedupe_key: str, kind: str, title: str, detail: str, link: str,
    now: datetime,
) -> bool:  # fmt: skip
    """Store a new unread alert and send it on. False (nothing else done) if one with
    this key already exists."""
    with session_factory() as session:
        if session.scalar(select(AppAlert.id).where(AppAlert.dedupe_key == dedupe_key)):
            return False
        session.add(
            AppAlert(
                dedupe_key=dedupe_key,
                kind=kind,
                title=title,
                detail=detail,
                link=link,
                created_at=now,
            )
        )
        try:
            session.commit()
        except IntegrityError:  # another thread stored it first
            return False
    try:
        notify.send_soon(session_factory, Fired(0, title, detail, link))
    except Exception:
        logger.exception("app alerts: could not queue %r", title)
    _changed()
    return True


def unread_count(session_factory) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).where(AppAlert.read_at.is_(None))) or 0


def recent(session_factory, limit: int = 50) -> list[AppAlert]:
    """Newest first."""
    with session_factory() as session:
        return list(
            session.scalars(
                select(AppAlert)
                .order_by(AppAlert.created_at.desc(), AppAlert.id.desc())
                .limit(limit)
            )
        )


def mark_read(session_factory, ids: Iterable[int], now: datetime) -> int:
    """Mark these alerts read (the ones a view showed); returns how many were unread."""
    ids = list(ids)
    if not ids:
        return 0
    with session_factory() as session:
        count = session.execute(
            update(AppAlert)
            .where(AppAlert.id.in_(ids), AppAlert.read_at.is_(None))
            .values(read_at=now)
        ).rowcount
        session.commit()
    if count:
        _changed()
    return count
