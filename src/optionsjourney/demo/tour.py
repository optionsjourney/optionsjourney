"""The demo's guided tour: seven steps, and what moves you from one to the next.

Pure logic. The caller works out the facts (what has happened in the demo) and
hands them in; this decides which step you are on and what the box says.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

TOTAL = 7
FINISHED = TOTAL + 1
INSTALL_URL = "https://optionsjourney.com/install"
_CONDOR = '[data-bot-name="SPX 0DTE condor"]'
_DAY_OVER_TEXT = "The demo's trading day is over. Start a new day to see this step."
_DAY_OVER_STEPS = (2, 3, 5, 7)
# Step 5 is something to see, not something the visitor does: its target fills about
# 6 minutes into the demo day, often before the visitor gets there. Arriving after it
# filled, the tour stops on it anyway, with these words, until Next.
_CALENDAR_STEP = 5
_CALENDAR_FILLED_TEXT = (
    "Your Weekly calendar reached its profit target while you were trading. Open its "
    "runs to see how it closed, then press Next."
)


@dataclass(frozen=True)
class Step:
    """One stop on the tour."""

    n: int
    title: str
    text: str
    link: str | None
    link_label: str | None
    highlight: str | None  # a CSS selector to point at, or None


STEPS: tuple[Step, ...] = (
    Step(
        1,
        "Your journal",
        "Months of made-up trades. Your 0DTE bot is about to trade: open Automation.",
        "/automation",
        "Open Automation",
        'nav a[href="/automation"]',
    ),
    Step(
        2,
        "Arm a bot",
        "Open the SPX 0DTE condor (under Off) and arm it. It enters within a minute "
        "and a toast shows the trade.",
        "/automation",
        "Automation",
        # On the list the arm button sits in the card's detail, which is folded until the
        # card is opened: point at the card first, then at the button once it shows. On
        # the bot's own page the arm form is always there.
        f'{_CONDOR}:not(.is-open) .st-row, {_CONDOR}.is-open [data-act="arm"], '
        f"{_CONDOR} #arm button",
    ),
    Step(
        3,
        "The stop",
        "In about a minute the price runs into the call side's stop and the trade "
        "closes. Then open its Run page.",
        "/automation",
        "Open the Run page",
        None,
    ),
    Step(
        4,
        "Trade by hand",
        "Send this put spread. It's filled in: review it, choose Put spread exits "
        "under Manage with, and send.",
        "/trade",
        "Open the Trade page",
        "#trade-ticket",
    ),
    Step(
        5,
        "A profit target",
        "Your Weekly calendar is near its profit target. It takes about two minutes: "
        "watch it close and the alert arrive.",
        "/automation",
        "Weekly calendar runs",
        None,
    ),
    Step(
        6,
        "Charts",
        "Add a ticker to your watchlist. Candles move and the news panel shows headlines.",
        "/charts",
        "Open Charts",
        None,
    ),
    Step(
        7,
        "Build a bot",
        "Save this put spread bot, arm it, then Skip ahead. It enters and later exits.",
        "/automation/new?shape=put_credit_spread",
        "New bot",
        None,
    ),
)

_FINISH = Step(
    FINISHED,
    "That's the tour",
    "Look around as long as you like. Install OptionsJourney to use it with your own broker.",
    INSTALL_URL,
    "Install",
    None,
)


@dataclass(frozen=True)
class TourFacts:
    """What has happened in the demo so far."""

    opened_automation: bool
    condor_armed: bool
    condor_stopped: bool
    condor_run_id: int | None
    opened_condor_run: bool
    spread_managed: bool
    calendar_target_filled: bool
    calendar_bot_id: int | None
    watchlist_changed: bool
    own_bot_entered: bool
    day_over: bool


DONE: dict[int, Callable[[TourFacts], bool]] = {
    1: lambda f: f.opened_automation,
    2: lambda f: f.condor_armed,
    3: lambda f: f.opened_condor_run and f.condor_stopped,
    4: lambda f: f.spread_managed,
    5: lambda f: f.calendar_target_filled,
    6: lambda f: f.watchlist_changed,
    7: lambda f: f.own_bot_entered,
}


class Tour:
    """Where one visitor is on the tour. It only ever moves forward, except restart."""

    def __init__(self) -> None:
        self.current = 1  # 1-7, or 8 once finished
        self.furthest = 1
        self.skipped = False
        self._held: frozenset[int] = frozenset()  # steps already done when restarted

    def _move_to(self, step: int) -> None:
        self.current = step
        self.furthest = max(self.furthest, step)

    def update(self, facts: TourFacts) -> int | None:
        """Advance past every consecutive done step; the new step number, or None."""
        start = self.current
        step = start
        while step <= TOTAL and step not in self._held and DONE[step](facts):
            if step == _CALENDAR_STEP and step != start:
                self._held = self._held | {step}  # done before it was seen: show it
                break
            step += 1
        if step == start:
            return None
        self._move_to(step)
        return step

    def next(self, from_step: int) -> None:
        """Move on one step, but only if the visitor is still on ``from_step``."""
        if self.current == from_step and self.current <= TOTAL:
            self._move_to(self.current + 1)

    def skip(self) -> None:
        self.skipped = True

    def restart(self, facts: TourFacts) -> None:
        """Back to step 1. Steps already done stay put until the visitor moves past them
        with Next (otherwise the next update would skip them all); a step whose fact
        becomes true later still advances."""
        self.current = 1
        self.skipped = False
        self._held = frozenset(n for n in range(1, TOTAL + 1) if DONE[n](facts))

    def view(self, facts: TourFacts) -> dict:
        """What the tour box shows."""
        finished = self.current > TOTAL
        step = _FINISH if finished else STEPS[self.current - 1]
        text, link = step.text, step.link
        if self.current == 3 and facts.condor_run_id is not None:
            link = f"/automation/runs/{facts.condor_run_id}"
        elif self.current == 5 and facts.calendar_bot_id is not None:
            link = f"/automation/{facts.calendar_bot_id}/runs"
        if self.current == _CALENDAR_STEP and facts.calendar_target_filled:
            text = _CALENDAR_FILLED_TEXT
        elif facts.day_over and not finished and self.current in _DAY_OVER_STEPS:
            text, link = _DAY_OVER_TEXT, None
        return {
            "step": self.current,
            "total": TOTAL,
            "title": step.title,
            "text": text,
            "link": link,
            "link_label": step.link_label,
            "highlight": step.highlight,
            "finished": finished,
            "skipped": self.skipped,
            "day_over": facts.day_over,
        }
