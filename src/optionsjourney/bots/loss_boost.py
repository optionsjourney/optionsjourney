"""The loss boost (#328; Tracker2's services/bots/loss_boost.py): a bot that opts in sizes
its entry up when its previous filled trade hit a stop. Pure: services/bot_loss_boost.py
reads the runs and exits.

"Previous trade" is the bot's most recent filled entry (not a tranche) on an EARLIER New
York trading day, of the same kind as this entry: a Paper bot looks at its paper trades,
a Live bot at its live ones (decision 0132). Today's own trades never count, and neither
does one sent from the Trade page (Tracker2's analyzer entries). It hit a stop when any
of its groups was closed by its stop: a resting stop that filled, a close the app started
for a stop it watches, or a paper stop.

Only what is known at the entry counts: a stop that fills later the same day boosts
nothing until the next day's entry.
"""

from dataclasses import dataclass
from datetime import date

STOP = "stop"
CLOSED = ("filled", "reduced")  # a close that closed all or part of its side


def stop_exit(kind: str, state: str, by: str | None) -> bool:
    """A bot_exits row that closed a group on its stop: a resting stop that filled, or a
    close started by a watched stop (its first log entry's "by" is "stop")."""
    if kind == "stop":
        return state == "filled"
    return kind == "close" and by == STOP and state in CLOSED


def paper_stop(kind: str, reason: str) -> bool:
    """A paper fill that closed a group on its stop ("stop", or "stop (last price)")."""
    return kind == "exit" and (reason == STOP or reason.startswith(f"{STOP} ("))


@dataclass(frozen=True)
class Boost:
    """A boost that applies: the multiplier, and the stopped trade's run and day."""

    multiplier: int
    after_run: int
    after_day: date

    def record(self) -> dict:
        """The entry's log step (bots/timeline.py words it)."""
        return {"boost": self.multiplier, "after_run": self.after_run,
                "after_day": self.after_day.isoformat()}  # fmt: skip


def words(step: dict) -> str:
    """A boost's log step in words, for the run page and the alert."""
    run, day = step.get("after_run"), step.get("after_day")
    return f"×{step.get('boost')}: run {run} ({day}) hit its stop"
