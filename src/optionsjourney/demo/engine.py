"""The demo engine: one heartbeat that moves the pretend market and everything fed by it.

Each step moves the market to `now`, fills the working orders it crossed, syncs the
fills into the journal (only when something filled), and pushes the new prices into
the app's quote hub and chart feed. At the close the pretend broker rings the bell
once: Day orders expire and the day's expiring options settle. A Skip ahead steps
straight away, so the jump's fills and prices show at once.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from optionsjourney.demo import feeds
from optionsjourney.demo.broker import PretendTasty
from optionsjourney.demo.clock import CLOSE, DEMO_DAY, NY, DemoClock
from optionsjourney.demo.feeds import DemoStream
from optionsjourney.demo.market import PretendMarket
from optionsjourney.services.sync import sync_all

logger = logging.getLogger(__name__)

STEP_EVERY = 1.0  # seconds


class DemoEngine:
    def __init__(
        self,
        services,
        market: PretendMarket,
        tasty: PretendTasty,
        clock: DemoClock,
        stream: DemoStream,
    ) -> None:
        self.services = services
        self.market = market
        self.tasty = tasty
        self.clock = clock
        self.stream = stream
        self.director = None  # the tour's director (demo/director.py), set by create_app
        self._close = datetime.combine(DEMO_DAY, CLOSE, tzinfo=NY)
        self._closed = False
        self._lock = threading.Lock()  # the loop and a Skip ahead never step at once
        clock.listeners.append(self._skipped)

    def _skipped(self, before: datetime, after: datetime) -> None:
        try:
            self.step(after)
        except Exception:  # noqa: BLE001 -- the skip itself still happened
            logger.exception("demo engine step after a skip failed")

    def step(self, now: datetime) -> None:
        with self._lock:
            ticks = self.market.advance_to(now)
            changed = bool(self.tasty.match(ticks))
            if not self._closed and now >= self._close:  # a skip may land just past 16:00
                self._closed = True
                changed = self.tasty.close_the_day(self.market.now) or changed
            if changed:
                sync_all(self.services.broker_clients(), self.services.session_factory)
            feeds.push_quotes(self.services.quote_hub, self.stream, self.market)
            if self.services.chart_feed is not None:
                feeds.push_candles(self.services.chart_feed, self.market)
            if self.director is not None:
                self.director.step(now)

    def start(self) -> None:
        def run() -> None:
            while True:
                try:
                    self.step(self.clock.now())
                except Exception:  # noqa: BLE001 -- the demo keeps ticking
                    logger.exception("demo engine step failed")
                time.sleep(STEP_EVERY)

        threading.Thread(target=run, name="demo-engine", daemon=True).start()
