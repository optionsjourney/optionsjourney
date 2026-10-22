"""What is live right now that stopping the app for an update would interrupt."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from optionsjourney.core.versions import market_open
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import bot_exits, bot_store


@dataclass(frozen=True)
class Risk:
    key: str
    title: str
    meaning: str
    count: int = 1


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def live_risks(session_factory: sessionmaker, now: datetime) -> list[Risk]:
    risks = []
    if market_open(now):
        risks.append(Risk("market_open", "The market is open.", ""))
    armed = len(bot_store.armed(session_factory))
    with session_factory() as session:
        stops = session.scalars(
            select(BotExit).join(BotRun, BotRun.id == BotExit.run_id)
            .where(BotRun.live.is_(True), BotExit.kind == "stop",
                   BotExit.state.in_(("pending", bot_exits.WATCHING)))
        ).all()  # fmt: skip
        watched = sum(1 for row in stops if bot_exits.app_watches(row))
        working = len(session.scalars(
            select(BotRun.id).where(BotRun.live.is_(True), BotRun.status == "running")
        ).all())  # fmt: skip
        working += len(session.scalars(
            select(BotExit.id).join(BotRun, BotRun.id == BotExit.run_id)
            .where(BotRun.live.is_(True), BotRun.status != "running",
                   BotExit.state.in_(("placing", "firing", "chasing")))
        ).all())  # fmt: skip
    if armed:
        word = _plural(armed, "bot", "bots")
        risks.append(Risk(
            "armed_bots",
            f"{armed} {word} armed",
            "they won't enter, manage or exit while the app is stopped.",
            armed,
        ))  # fmt: skip
    if watched:
        word = _plural(watched, "open trade has", "open trades have")
        risks.append(Risk(
            "watched_stops",
            f"{watched} {word} a stop the app watches, not your broker",
            "while the app is stopped, nothing closes them if the price moves through the stop.",
            watched,
        ))  # fmt: skip
    if working:
        word = _plural(working, "bot order", "bot orders")
        risks.append(Risk(
            "working_orders",
            f"{working} {word} still working",
            "the app won't follow them while it's stopped.",
            working,
        ))  # fmt: skip
    return risks
