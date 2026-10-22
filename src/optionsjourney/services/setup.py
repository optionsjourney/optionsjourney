"""The setup wizard's state (#15, docs/decisions/0007): whether it's been
finished, which brokers the user said they use, and the trading-style
answers that set the starter tags (decision 0072: tags, not strategies)."""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from optionsjourney.db.models import Setting, Tag
from optionsjourney.services import journal

# Internal keys in the settings table; not shown on the Settings page.
_COMPLETED = "_setup_completed"
_BROKERS = "_setup_brokers"
# The risk acknowledgments (decision 0093): at first run, the first time a broker is
# connected, and the first time a bot goes Live.
_RISK = "_risk_accepted"
_BROKER_RISK = "_broker_risk_accepted"
_LIVE_RISK = "_live_risk_accepted"

# The trading-style question: each answer is the tag it creates.
STYLES = (
    ("0DTE", "0DTE: options that expire the day you open them"),
    ("Credit Spreads", "Credit spreads"),
    ("Iron Condors", "Iron condors"),
    ("Strangles", "Strangles and straddles"),
    ("Calendars", "Calendars and diagonals"),
    ("Wheel", "The wheel: cash-secured puts, then covered calls"),
    ("Covered Calls", "Covered calls on stocks you hold"),
    ("Earnings", "Earnings trades"),
    ("Futures Options", "Futures options"),
)


def _get(session_factory: sessionmaker, key: str) -> str | None:
    with session_factory() as session:
        row = session.get(Setting, key)
    return row.value if row else None


def _set(session_factory: sessionmaker, key: str, value: str) -> None:
    with session_factory() as session:
        row = session.get(Setting, key) or Setting(key=key)
        row.value = value
        row.updated_at = datetime.now(UTC)
        session.add(row)
        session.commit()


def is_complete(session_factory: sessionmaker) -> bool:
    return _get(session_factory, _COMPLETED) is not None


def mark_complete(session_factory: sessionmaker) -> None:
    _set(session_factory, _COMPLETED, datetime.now(UTC).isoformat())


def risk_accepted(session_factory: sessionmaker) -> bool:
    return _get(session_factory, _RISK) is not None


def accept_risk(session_factory: sessionmaker) -> None:
    _set(session_factory, _RISK, datetime.now(UTC).isoformat())


def broker_risk_accepted(session_factory: sessionmaker) -> bool:
    return _get(session_factory, _BROKER_RISK) is not None


def accept_broker_risk(session_factory: sessionmaker) -> None:
    _set(session_factory, _BROKER_RISK, datetime.now(UTC).isoformat())


def live_risk_accepted(session_factory: sessionmaker) -> bool:
    return _get(session_factory, _LIVE_RISK) is not None


def accept_live_risk(session_factory: sessionmaker) -> None:
    _set(session_factory, _LIVE_RISK, datetime.now(UTC).isoformat())


def chosen_brokers(session_factory: sessionmaker) -> list[str]:
    return json.loads(_get(session_factory, _BROKERS) or "[]")


def choose_brokers(session_factory: sessionmaker, brokers: list[str]) -> None:
    _set(
        session_factory, _BROKERS, json.dumps([b for b in ("tastytrade", "schwab") if b in brokers])
    )


def apply_styles(session_factory: sessionmaker, picked: list[str]) -> None:
    """Create a tag for each picked style. Starter tags that weren't picked, are on
    no trade and no bot files under are removed; the others stay, whatever was
    picked."""
    known = {name for name, _ in STYLES}
    picked_set = {name for name in picked if name in known}
    counts = journal.tag_counts(session_factory)
    bots = journal.bot_tags(session_factory)
    with session_factory() as session:
        existing = {t.name: t.id for t in session.scalars(select(Tag))}
    for name in sorted(picked_set - set(existing)):
        journal.tag_for(session_factory, name)
    for name, tag_id in existing.items():
        unused = not counts.get(tag_id) and tag_id not in bots
        if name in known and name not in picked_set and unused:
            journal.delete_tag(session_factory, tag_id)
