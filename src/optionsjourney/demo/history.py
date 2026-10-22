"""The demo accounts' made-up past: about thirteen weeks of trades before the demo day.

Every trade is written into the pretend accounts (``Book``) as its broker's own raw
records: TastyTrade's orders and transactions (through ``Book.fill`` and
``Book.settle``, as the pretend broker writes them) and Schwab's transactions in
Schwab's own JSON (what ``brokers.schwab.parse_transaction`` reads). The app brings
them in with its real sync, so the journal, its groups and its numbers are the app's
own. Prices come from the made-up market's daily bars and its option model, so a
trade's prices fit the chart. Pure: no database. ``seed`` writes the runs and the rest.

The two Live bots' trades are opened at their legs and closed at the exits the bot's
own exit math sets for that fill (``bots.adopt.plan_exits``), so their runs, written
by ``seed``, agree with the transactions. Made-up accounts, prices and results only.
"""

from __future__ import annotations

import heapq
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from optionsjourney.bots import adopt as rules
from optionsjourney.bots import schedule
from optionsjourney.bots.definition import BotDef
from optionsjourney.core.accounts import DailyValue
from optionsjourney.core.market import market_date
from optionsjourney.core.sessions import session_close
from optionsjourney.core.symbols import occ_symbol, parse_occ
from optionsjourney.core.transactions import OptionDetails, RawTransaction
from optionsjourney.demo.book import MULTIPLIER, START_CASH, Book, leg_prices
from optionsjourney.demo.clock import CLOSE, DEMO_DAY, NY
from optionsjourney.demo.levels import VIX_BASE
from optionsjourney.demo.market import PretendMarket
from optionsjourney.trading.orders import Side, plain

FIRST_DAY = date(2026, 7, 20)  # a Monday, thirteen weeks before the demo week
LAST_DAY = DEMO_DAY - timedelta(days=1)
CALENDAR_DAY = DEMO_DAY - timedelta(days=2)  # the Weekly calendar's open trade
CONDOR = "SPX 0DTE condor"
CALENDAR = "Weekly calendar"
PAPER = "QQQ put spread"

CENT = Decimal("0.01")
NICKEL = Decimal("0.05")
DIME = Decimal("0.10")
_OPEN = time(9, 30)
_SESSION = 6.5 * 3600

# Outcomes, chosen so the whole history wins about two trades in three, with losing
# weeks, and each Live bot the tour shows ends a little ahead (final review I-4b). A
# condor side wins half its credit and a stop loses about twice it, so the condor needs
# most of its sides to win; the trades you made yourself win about half the time.
CONDOR_SHORTS = 60  # SPX points from spot to the condor's short strikes (about 15 delta)
CONDOR_STOP_CHANCE = 0.35  # one side of the day's condor is stopped
CALENDAR_TARGET_CHANCE = 0.4  # else the timed close takes it, at a small loss
CALENDAR_KEPT = (0.8, 1.0)  # what a timed close gets back, as a share of the debit
SPREAD_WIN_CHANCE = 0.45
RNG_SALT = 14  # picks the made-up outcomes; the market's path has its own seed
SCHWAB_COMMISSION = Decimal("0.65")  # per option contract
SCHWAB_REG_FEE = Decimal("0.02")


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


@dataclass
class Exit:
    """What one side of a bot's trade did, for its run's exit rows."""

    group: int
    kind: str  # stop, target, wing, timed
    state: str  # filled, cancelled, working
    at: datetime
    order_id: str | None = None
    trigger: Decimal | None = None
    limit: Decimal | None = None
    fill_price: Decimal | None = None


@dataclass
class BotTrade:
    """A Live bot's trade: its entry, as the run holds it, and its exits."""

    bot: str
    day: date
    at: datetime
    hhmm: str
    spot: Decimal
    held: list[rules.HeldLeg]
    order_id: str = ""
    exits: list[Exit] = field(default_factory=list)
    is_open: bool = False
    sides: list[rules.SidePlan] = field(default_factory=list)


@dataclass
class Manual:
    """A trade you made yourself: what the journal files it under, and your note."""

    broker: str
    account: str
    day: date
    tags: str
    note: str = ""
    order_id: str = ""


@dataclass
class PaperTrade:
    """A Paper run of the QQQ bot: its pretend entry and how it ended."""

    day: date
    at: datetime
    hhmm: str
    spot: Decimal
    legs: list[dict]  # symbol, strike, right, side, ratio, group, expiration, mid
    credit: Decimal  # per unit, + a credit
    exit_kind: str  # "exit" or "expiry"
    reason: str  # "target", "stop", "expired"
    exit_at: datetime
    exit_price: Decimal  # per unit, signed like a fill: + a credit
    exit_mids: list[Decimal] = field(default_factory=list)


@dataclass
class History:
    bot_trades: list[BotTrade] = field(default_factory=list)
    manual: list[Manual] = field(default_factory=list)
    paper: list[PaperTrade] = field(default_factory=list)
    days: list[date] = field(default_factory=list)  # the trading days covered


def trading_days(first: date = FIRST_DAY, last: date = LAST_DAY) -> list[date]:
    days, day = [], first
    while day <= last:
        if session_close(day) is not None:
            days.append(day)
        day += timedelta(days=1)
    return days


def ny(day: date, hhmm: str, second: int = 0) -> datetime:
    """`day` at HH:MM New York, in UTC."""
    hour, minute = (int(p) for p in hhmm.split(":"))
    return datetime.combine(day, time(hour, minute, second), tzinfo=NY).astimezone(UTC)


def _money(value: float | Decimal, step: Decimal = CENT) -> Decimal:
    return (Decimal(str(value)) / step).quantize(Decimal(1), ROUND_HALF_UP) * step


def _spx_tick(price: Decimal) -> Decimal:
    return NICKEL if price < 3 else DIME


class _Maker:
    def __init__(self, market: PretendMarket, tasty: Book, schwab: Book, bots: dict[str, BotDef],
                 seed: int) -> None:  # fmt: skip
        self.market = market
        self.tasty = tasty
        self.schwab = schwab
        self.bots = bots
        self.seed = seed
        self.rng = random.Random(seed * 7907 + RNG_SALT)
        self.history = History(days=trading_days())
        self._bars: dict[str, dict[date, Bar]] = {}
        self._todo: list[tuple[datetime, int, Callable[[], None]]] = []
        self._seq = 0
        self._snapshots: dict[str, list[tuple[datetime, Decimal, dict, dict]]] = {
            tasty.account: [],
            schwab.account: [],
        }

    # ----- the made-up market's past -----
    def bars(self, ticker: str) -> dict[date, Bar]:
        if ticker not in self._bars:
            candles = self.market.candles(ticker, 1440, 10_000)
            self._bars[ticker] = {
                datetime.fromtimestamp(c["time"] / 1000, UTC).date(): Bar(
                    c["open"], c["high"], c["low"], c["close"]
                )
                for c in candles
            }
        return self._bars[ticker]

    def spot(self, ticker: str, at: datetime) -> float:
        """The price at `at`: the day's open drifting to its close through the session."""
        local = at.astimezone(NY)
        bar = self.bars(ticker)[local.date()]
        opens = datetime.combine(local.date(), _OPEN, tzinfo=NY)
        part = min(1.0, max(0.0, (local - opens).total_seconds() / _SESSION))
        return bar.open + (bar.close - bar.open) * part

    def vix(self, at: datetime) -> float:
        """The day's VIX: near its usual level, a little higher or lower each day."""
        day = at.astimezone(NY).date()
        return VIX_BASE * math.exp(0.12 * random.Random(self.seed * 31 + day.toordinal()).gauss())

    def mark(self, symbol: str, at: datetime, spot: float | None = None,
             vix: float | None = None) -> float:  # fmt: skip
        parsed = parse_occ(symbol)
        if spot is None:
            spot = self.spot(parsed[0], at)
        return self.market.option_mark_at(symbol, spot, at, self.vix(at) if vix is None else vix)

    # ----- the schedule: everything happens in time order, so ids run in time order -----
    def at(self, when: datetime, fn: Callable[[], None]) -> None:
        heapq.heappush(self._todo, (when, self._seq, fn))
        self._seq += 1

    def run(self) -> None:
        while self._todo:
            _, _, fn = heapq.heappop(self._todo)
            fn()

    # ----- TastyTrade -----
    def _snapshot(self, book: Book, at: datetime) -> None:
        self._snapshots[book.account].append(
            (at, book.cash, dict(book.options), {t: s for t, (s, _) in book.stocks.items()})
        )

    def order(self, legs: list[tuple[str, str, int]], at: datetime, *, kind: str = "Limit",
              price: Decimal | None = None, effect: str | None = None, tif: str = "Day",
              trigger: Decimal | None = None) -> dict:  # fmt: skip
        """A TastyTrade order as the pretend broker keeps it, working from `at`."""
        oid = self.tasty.new_order_id()
        stamp = at.isoformat()
        raw = {
            "id": int(oid),
            "account-number": self.tasty.account,
            "status": "Live",
            "time-in-force": tif,
            "order-type": kind,
            "underlying-symbol": legs[0][0][:6].strip(),
            "received-at": stamp,
            "updated-at": stamp,
            "cancellable": True,
            "editable": True,
            "legs": [
                {
                    "instrument-type": "Equity Option",
                    "symbol": symbol,
                    "quantity": qty,
                    "remaining-quantity": qty,
                    "action": action,
                    "fills": [],
                }
                for symbol, action, qty in legs
            ],  # fmt: skip
            "size": 1,
        }
        if price is not None:
            raw["price"] = f"{price:f}"
            raw["price-effect"] = effect
        if trigger is not None:
            raw["stop-trigger"] = f"{trigger:f}"
        self.tasty.orders[oid] = raw
        return raw

    def fill(self, raw: dict, prices: list[Decimal], at: datetime) -> None:
        self.tasty.fill(raw, prices, at)
        self._snapshot(self.tasty, at)

    def cancel(self, raw: dict, at: datetime) -> None:
        raw["status"] = "Cancelled"
        raw["terminal-at"] = raw["updated-at"] = at.isoformat()
        raw["cancellable"] = raw["editable"] = False

    def settle(self, symbols: list[str], at: datetime) -> None:
        """Options still held at their expiry's close leave at their intrinsic value."""
        for symbol in symbols:
            key = plain(symbol)
            if key not in self.tasty.options:
                continue
            underlying, d = parse_occ(key)
            spot = Decimal(str(round(self.bars(underlying)[d.expiration].close, 2)))
            gap = spot - d.strike if d.put_call == "CALL" else d.strike - spot
            self.tasty.settle(key, max(gap, Decimal(0)).quantize(CENT), at)
        self._snapshot(self.tasty, at)

    def priced(self, legs: list[tuple[str, bool]], at: datetime, step: Decimal,
               spot: float | None = None, vix: float | None = None,
               ) -> tuple[Decimal, list[Decimal]]:  # fmt: skip
        """(net per unit, + a credit, on `step`; each leg's price) at the marks."""
        marks = [self.mark(symbol, at, spot, vix) for symbol, _ in legs]
        net = sum((-m if buy else m) for (_, buy), m in zip(legs, marks, strict=True))
        net = _money(net, step)
        return net, leg_prices([(buy, 1) for _, buy in legs], marks, -net)

    def close_prices(self, legs: list[tuple[str, bool]], at: datetime, cost: Decimal,
                     spot: float | None = None) -> list[Decimal]:  # fmt: skip
        """Closing legs (buy?) priced at the marks, coming to `cost` (+ a debit) a unit."""
        marks = [self.mark(symbol, at, spot) for symbol, _ in legs]
        prices = leg_prices([(buy, 1) for _, buy in legs], marks, cost)
        return [_money(p) for p in prices]

    # ----- the bots' trades -----
    def condor(self, day: date) -> None:
        bot = self.bots[CONDOR]
        second = self.rng.randrange(5, 50)
        at = ny(day, "09:40", second)
        spot = self.spot("SPX", at)
        call = (Decimal(str(spot + CONDOR_SHORTS)) / 5).to_integral_value(ROUND_CEILING) * 5
        put = (Decimal(str(spot - CONDOR_SHORTS)) / 5).to_integral_value(ROUND_FLOOR) * 5
        sym = {
            "sc": occ_symbol("SPXW", OptionDetails("CALL", call, day)),
            "lc": occ_symbol("SPXW", OptionDetails("CALL", call + 10, day)),
            "sp": occ_symbol("SPXW", OptionDetails("PUT", put, day)),
            "lp": occ_symbol("SPXW", OptionDetails("PUT", put - 10, day)),
        }
        order = [(sym["sc"], False), (sym["lc"], True), (sym["sp"], False), (sym["lp"], True)]
        credit, prices = self.priced(order, at, NICKEL)
        held = [
            rules.HeldLeg(symbol, "call" if i < 2 else "put", parse_occ(symbol)[1].strike, day,
                          Side.BUY if buy else Side.SELL, 1, price)
            for i, ((symbol, buy), price) in enumerate(zip(order, prices, strict=True))
        ]  # fmt: skip
        sides = rules.plan_exits(bot, rules.match(bot, held, today=day))
        trade = BotTrade(CONDOR, day, at, "09:40", _money(spot), held, sides=sides)
        self.history.bot_trades.append(trade)
        stopped = None
        if self.rng.random() < CONDOR_STOP_CHANCE:
            bar = self.bars("SPX")[day]
            stopped = 0 if bar.close >= bar.open else 1  # the side the day moved toward
        stop_at = ny(day, f"{self.rng.randrange(10, 14):02d}:{self.rng.randrange(60):02d}")
        target_at = [
            ny(day, f"{self.rng.randrange(11, 15):02d}:{self.rng.randrange(60):02d}") for _ in sides
        ]

        def opened() -> None:
            raw = self.order([(s, "Buy to Open" if b else "Sell to Open", 1) for s, b in order],
                             at, price=credit, effect="Credit")  # fmt: skip
            self.fill(raw, prices, at)
            trade.order_id = str(raw["id"])
            for side in sides:
                self.at(at + timedelta(seconds=3), lambda side=side: resting_stop(side))

        def resting_stop(side: rules.SidePlan) -> None:
            raw = self.order([(side.short, "Buy to Close", 1)], at + timedelta(seconds=3),
                             kind="Stop", trigger=side.stop.trigger)  # fmt: skip
            if side.position == stopped:
                self.at(stop_at, lambda: stop_fills(side, raw))
            else:
                when = target_at[side.position]
                self.at(when, lambda: target_fills(side, raw, when))

        def stop_fills(side: rules.SidePlan, raw: dict) -> None:
            price = side.stop.trigger + _spx_tick(side.stop.trigger) * self.rng.randrange(0, 2)
            self.fill(raw, [price], stop_at)
            # the wing is sold where the market stood when the short reached its stop
            moved = self._spot_at_mark(side.short, stop_at, float(price))
            wing_at = stop_at + timedelta(seconds=2)
            wing = _money(max(0.05, self.mark(side.long, wing_at, moved)), NICKEL)
            wing_raw = self.order([(side.long, "Sell to Close", 1)], wing_at, kind="Market")
            self.fill(wing_raw, [wing], wing_at)
            trade.exits += [
                Exit(side.position, "stop", "filled", stop_at, str(raw["id"]),
                     trigger=side.stop.trigger, fill_price=price),
                Exit(side.position, "wing", "filled", wing_at, str(wing_raw["id"]),
                     fill_price=wing),
                Exit(side.position, "target", "cancelled", stop_at, limit=side.target),
            ]  # fmt: skip

        def target_fills(side: rules.SidePlan, stop_raw: dict, when: datetime) -> None:
            legs = [(side.short, True), (side.long, False)]
            raw = self.order([(side.short, "Buy to Close", 1), (side.long, "Sell to Close", 1)],
                             when, price=side.target, effect="Debit")  # fmt: skip
            self.fill(raw, self.close_prices(legs, when, side.target), when)
            self.cancel(stop_raw, when)
            trade.exits += [
                Exit(side.position, "stop", "cancelled", when, str(stop_raw["id"]),
                     trigger=side.stop.trigger),
                Exit(side.position, "target", "filled", when, str(raw["id"]),
                     limit=side.target, fill_price=side.target),
            ]  # fmt: skip

        self.at(at, opened)

    def _spot_at_mark(self, symbol: str, at: datetime, price: float) -> float:
        """The underlying's price at which `symbol` is worth `price` at `at`."""
        underlying, d = parse_occ(symbol)
        low, high = self.spot(underlying, at) * 0.8, self.spot(underlying, at) * 1.2
        rising = d.put_call == "CALL"
        for _ in range(60):
            middle = (low + high) / 2
            above = self.mark(symbol, at, middle) > price
            if above == rising:
                high = middle
            else:
                low = middle
        return (low + high) / 2

    def calendar(self, day: date, *, still_open: bool = False) -> None:
        bot = self.bots[CALENDAR]
        near, far = day + timedelta(days=4), day + timedelta(days=11)
        if session_close(near) is None or session_close(far) is None:
            return  # an expiration on a holiday: the bot would have found no listing
        at = ny(day, "10:00")
        spot = self.spot("SPX", at)
        vix = None
        if still_open:
            # The open trade fits the live market's own minutes for that morning
            spot, vix = self._minute("SPX", at), self._minute("VIX", at)
        strike = (Decimal(str(spot)) / 5).to_integral_value(ROUND_HALF_UP) * 5
        short = occ_symbol("SPXW", OptionDetails("PUT", strike, near))
        long = occ_symbol("SPXW", OptionDetails("PUT", strike, far))
        order = [(short, False), (long, True)]
        net, prices = self.priced(order, at, NICKEL, spot, vix)
        held = [
            rules.HeldLeg(short, "put", strike, near, Side.SELL, 1, prices[0]),
            rules.HeldLeg(long, "put", strike, far, Side.BUY, 1, prices[1]),
        ]
        sides = rules.plan_exits(bot, rules.match(bot, held, today=day))
        [side] = sides
        trade = BotTrade(CALENDAR, day, at, "10:00", _money(spot), held, is_open=still_open,
                         sides=sides)  # fmt: skip
        self.history.bot_trades.append(trade)
        debit = -net
        hit = self.rng.random() < CALENDAR_TARGET_CHANCE
        hit_day = self.rng.choice([d for d in trading_days(day + timedelta(days=1), near)])
        hit_at = ny(hit_day, f"{self.rng.randrange(10, 15):02d}:{self.rng.randrange(60):02d}")
        kept = Decimal(str(self.rng.uniform(*CALENDAR_KEPT)))
        timed_at = ny(near, "15:45")

        def opened() -> None:
            raw = self.order([(short, "Sell to Open", 1), (long, "Buy to Open", 1)], at,
                             price=debit, effect="Debit")  # fmt: skip
            self.fill(raw, prices, at)
            trade.order_id = str(raw["id"])
            if not still_open:  # the open trade's target is placed by the bot itself (seed)
                self.at(at + timedelta(seconds=3), resting_target)

        # The exit math signs a group's value as the fills are (+ a debit to close); a
        # calendar's target is a credit
        target = -side.target

        def resting_target() -> None:
            raw = self.order([(long, "Sell to Close", 1), (short, "Buy to Close", 1)],
                             at + timedelta(seconds=3), price=target, effect="Credit",
                             tif="GTC")  # fmt: skip
            if hit:
                self.at(hit_at, lambda: target_fills(raw))
            else:
                self.at(timed_at, lambda: timed_close(raw))

        def target_fills(raw: dict) -> None:
            legs = [(long, False), (short, True)]
            self.fill(raw, self.close_prices(legs, hit_at, -target), hit_at)
            trade.exits.append(Exit(0, "target", "filled", hit_at, str(raw["id"]),
                                    limit=side.target, fill_price=side.target))  # fmt: skip

        def timed_close(target_raw: dict) -> None:
            self.cancel(target_raw, timed_at)
            value = _money(debit * kept, NICKEL)
            legs = [(long, False), (short, True)]
            raw = self.order([(long, "Sell to Close", 1), (short, "Buy to Close", 1)], timed_at,
                             kind="Market")  # fmt: skip
            self.fill(raw, self.close_prices(legs, timed_at, -value), timed_at)
            trade.exits += [
                Exit(0, "target", "cancelled", timed_at, str(target_raw["id"]),
                     limit=side.target),
                Exit(0, "timed", "filled", timed_at, str(raw["id"]), fill_price=value),
            ]  # fmt: skip

        self.at(at, opened)

    def _minute(self, ticker: str, at: datetime) -> float:
        stamp = int(at.timestamp()) // 60 * 60 * 1000
        for candle in self.market.candles(ticker, 1, 10_000):
            if candle["time"] == stamp:
                return candle["open"]
        raise LookupError(f"no {ticker} minute at {at}")

    # ----- trades you made -----
    def put_spread(self, day: date, note: str = "") -> None:
        at = ny(day, "11:00")
        expiry = day + timedelta(days=9)
        spot = self.spot("SPX", at)
        strike = (Decimal(str(spot * 0.975)) / 5).to_integral_value(ROUND_FLOOR) * 5
        short = occ_symbol("SPXW", OptionDetails("PUT", strike, expiry))
        long = occ_symbol("SPXW", OptionDetails("PUT", strike - 25, expiry))
        order = [(short, False), (long, True)]
        credit, prices = self.priced(order, at, NICKEL)
        manual = Manual("tastytrade", self.tasty.account, day, "Credit Spreads", note)
        self.history.manual.append(manual)
        win = self.rng.random() < SPREAD_WIN_CHANCE
        expires = win and self.rng.random() < 0.4
        later = trading_days(day + timedelta(days=2), expiry - timedelta(days=2))
        close_at = ny(self.rng.choice(later), f"{self.rng.randrange(10, 15):02d}:30")
        cost = _money(credit * (Decimal("0.5") if win else Decimal("1.8")), NICKEL)

        def opened() -> None:
            raw = self.order([(short, "Sell to Open", 1), (long, "Buy to Open", 1)], at,
                             price=credit, effect="Credit")  # fmt: skip
            self.fill(raw, prices, at)
            manual.order_id = str(raw["id"])

        def closed() -> None:
            legs = [(short, True), (long, False)]
            raw = self.order([(short, "Buy to Close", 1), (long, "Sell to Close", 1)], close_at,
                             price=cost, effect="Debit")  # fmt: skip
            self.fill(raw, self.close_prices(legs, close_at, cost), close_at)

        self.at(at, opened)
        if expires:
            self.at(ny(expiry, "16:00"), lambda: self.settle([short, long], ny(expiry, "16:00")))
        else:
            self.at(close_at, closed)

    def earnings(self, ticker: str, day: date, expiry: date, note: str) -> None:
        """A put spread sold the afternoon before a report and bought back the next
        morning, after the volatility has gone out of it."""
        at, after = ny(day, "15:30"), ny(day + timedelta(days=1), "10:00")
        spot = self.spot(ticker, at)
        width = 5 if spot > 150 else 2
        strike = (Decimal(str(spot * 0.94)) / width).to_integral_value(ROUND_FLOOR) * width
        short = occ_symbol(ticker, OptionDetails("PUT", strike, expiry))
        long = occ_symbol(ticker, OptionDetails("PUT", strike - width, expiry))
        order = [(short, False), (long, True)]
        before = VIX_BASE * 1.8  # the report's volatility, priced in
        credit, prices = self.priced(order, at, CENT, vix=before)
        manual = Manual("tastytrade", self.tasty.account, day, "Earnings, Credit Spreads", note)
        self.history.manual.append(manual)
        next_open = self.bars(ticker)[after.astimezone(NY).date()].open
        cost, closing = self.priced([(short, True), (long, False)], after, CENT, spot=next_open)
        cost = max(-cost, CENT)

        def opened() -> None:
            raw = self.order([(short, "Sell to Open", 1), (long, "Buy to Open", 1)], at,
                             price=credit, effect="Credit")  # fmt: skip
            self.fill(raw, prices, at)
            manual.order_id = str(raw["id"])

        def closed() -> None:
            legs = [(short, True), (long, False)]
            raw = self.order([(short, "Buy to Close", 1), (long, "Sell to Close", 1)], after,
                             price=cost, effect="Debit")  # fmt: skip
            self.fill(raw, self.close_prices(legs, after, cost, next_open), after)

        self.at(at, opened)
        self.at(after, closed)

    # ----- Schwab, in Schwab's own records -----
    def _schwab(self, at: datetime, items: list[dict], fees: Decimal, *, kind: str = "TRADE",
                description: str | None = None) -> str:  # fmt: skip
        book = self.schwab
        activity = book.new_transaction_id()
        order_id = int(book.new_order_id())
        net = sum((Decimal(str(i.get("cost", 0))) for i in items), Decimal(0)) - fees
        transfer = list(items)
        if fees:
            transfer += [
                {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
                 "feeType": name, "amount": -float(amount)}
                for name, amount in (("COMMISSION", fees - SCHWAB_REG_FEE),
                                     ("OPT_REG_FEE", SCHWAB_REG_FEE))
            ]  # fmt: skip
        data = {
            "activityId": activity,
            "time": at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "type": kind,
            "status": "VALID",
            "subAccount": "MARGIN",
            "orderId": order_id,
            "netAmount": float(net),
            "transferItems": transfer,
        }
        if description:
            data["description"] = description
        book.cash += net
        book.transactions.append(
            RawTransaction(transaction_id=str(activity), executed_at=at, transaction_type=kind,
                           data=data)
        )  # fmt: skip
        return str(order_id)

    def stock(self, ticker: str, shares: int, at: datetime) -> str:
        """Buy (+) or sell (-) shares at the price then."""
        price = _money(self.spot(ticker, at))
        held, average = self.schwab.stocks.get(ticker, (Decimal(0), Decimal(0)))
        after = held + shares
        if shares > 0:
            average = (held * average + shares * price) / after
        if after:
            self.schwab.stocks[ticker] = (after, average.quantize(Decimal("0.0001")))
        else:
            self.schwab.stocks.pop(ticker)
        item = {
            "instrument": {"assetType": "EQUITY", "symbol": ticker},
            "amount": float(shares),
            "price": float(price),
            "cost": float(-price * shares),
            "positionEffect": "OPENING" if shares > 0 else "CLOSING",
        }
        order_id = self._schwab(at, [item], Decimal(0))
        self._snapshot(self.schwab, at)
        return order_id

    @staticmethod
    def _schwab_option(symbol: str) -> dict:
        underlying, d = parse_occ(symbol)
        return {
            "assetType": "OPTION",
            "symbol": symbol,
            "underlyingSymbol": underlying,
            "putCall": d.put_call,
            "strikePrice": float(d.strike),
            "expirationDate": f"{d.expiration.isoformat()}T20:00:00.000+00:00",
            "optionPremiumMultiplier": MULTIPLIER,
        }

    def schwab_option(self, symbol: str, contracts: int, price: Decimal, at: datetime,
                      opening: bool) -> str:  # fmt: skip
        """Buy (+) or sell (-) `contracts` of one option at `price`."""
        key = plain(symbol)
        held = self.schwab.options.get(key, 0) + contracts
        if held:
            self.schwab.options[key] = held
        else:
            self.schwab.options.pop(key, None)
        item = {
            "instrument": self._schwab_option(symbol),
            "amount": float(contracts),
            "price": float(price),
            "cost": float(-price * contracts * MULTIPLIER),
            "positionEffect": "OPENING" if opening else "CLOSING",
        }
        fees = (SCHWAB_COMMISSION + SCHWAB_REG_FEE) * abs(contracts)
        order_id = self._schwab(at, [item], fees)
        self._snapshot(self.schwab, at)
        return order_id

    def schwab_expires(self, symbol: str, at: datetime) -> None:
        key = plain(symbol)
        held = self.schwab.options.pop(key)
        item = {
            "instrument": self._schwab_option(symbol),
            "amount": float(-held),
            "price": 0.0,
            "cost": 0.0,
            "positionEffect": "CLOSING",
        }
        self._schwab(at, [item], Decimal(0), kind="RECEIVE_AND_DELIVER",
                     description="OPTION EXPIRATION")  # fmt: skip
        self._snapshot(self.schwab, at)

    def covered_call(self, day: date, expiry: date, note: str = "") -> None:
        at = ny(day, "10:30")
        spot = self.spot("AAPL", at)
        strike = (Decimal(str(spot * 1.05)) / 5).to_integral_value(ROUND_CEILING) * 5
        symbol = occ_symbol("AAPL", OptionDetails("CALL", strike, expiry))
        price = _money(self.mark(symbol, at))
        manual = Manual("schwab", self.schwab.account, day, "Covered Calls", note)
        self.history.manual.append(manual)
        close = self.bars("AAPL")[expiry].close

        def sold() -> None:
            manual.order_id = self.schwab_option(symbol, -1, price, at, True)

        self.at(at, sold)
        if close < float(strike):
            when = ny(expiry, "16:00")
            self.at(when, lambda: self.schwab_expires(symbol, when))
        else:
            when = ny(expiry, "15:00")
            back = _money(self.mark(symbol, when))
            self.at(when, lambda: self.schwab_option(symbol, 1, back, when, False))

    def stock_trade(self, ticker: str, shares: int, buy: date, sell: date | None,
                    note: str = "") -> None:  # fmt: skip
        manual = Manual("schwab", self.schwab.account, buy, "", note)
        self.history.manual.append(manual)

        def bought() -> None:
            manual.order_id = self.stock(ticker, shares, ny(buy, "10:05"))

        self.at(ny(buy, "10:05"), bought)
        if sell is not None:
            self.at(ny(sell, "14:10"), lambda: self.stock(ticker, -shares, ny(sell, "14:10")))

    def swing(
        self,
        ticker: str,
        shares: int,
        buy_in: tuple[date, date],
        sell_in: tuple[date, date],
        win: bool,
    ) -> None:
        """A stock bought on the window's low (or high) close and sold on the first
        day it closes 3% above (or below) it, or the window's last: a trader's good and
        bad timing, on the made-up chart's own closes."""
        closes = self.bars(ticker)
        buys = trading_days(*buy_in)
        buy = (min if win else max)(buys, key=lambda d: closes[d].close)
        goal = closes[buy].close * (1.03 if win else 0.97)
        sells = trading_days(*sell_in)
        sell = next((d for d in sells if (closes[d].close >= goal) == win), sells[-1])
        self.stock_trade(ticker, shares, buy, sell)

    # ----- the QQQ bot's paper runs (nothing at the broker) -----
    def paper(self, day: date) -> None:
        at = ny(day, "10:30")
        expiry = day + timedelta(days=3)
        spot = self.spot("QQQ", at)
        strike = Decimal(str(spot * 0.98)).to_integral_value(ROUND_FLOOR)
        symbols = [occ_symbol("QQQ", OptionDetails("PUT", k, expiry)) for k in (strike, strike - 5)]
        mids = [_money(self.mark(s, at)) for s in symbols]
        credit = mids[0] - mids[1]
        legs = [
            {"position": i, "symbol": s, "strike": f"{k:f}", "right": "put",
             "side": side, "ratio": 1, "group": 0, "mid": f"{m:f}",
             "expiration": expiry.isoformat(), "dte": 3}
            for i, (s, k, side, m) in enumerate(
                zip(symbols, (strike, strike - 5), ("sell", "buy"), mids, strict=True)
            )
        ]  # fmt: skip
        roll = self.rng.random()
        if roll < 0.55:
            when = ny(day + timedelta(days=self.rng.randrange(1, 3)), "11:15")
            trade = PaperTrade(day, at, "10:30", _money(spot), legs, credit, "exit", "target",
                               when, -_money(credit / 2))  # fmt: skip
        elif roll < 0.8:
            when = ny(expiry, "16:00")
            trade = PaperTrade(day, at, "10:30", _money(spot), legs, credit, "expiry", "expired",
                               when, Decimal(0))  # fmt: skip
        else:
            when = ny(day + timedelta(days=1), "13:20")
            trade = PaperTrade(day, at, "10:30", _money(spot), legs, credit, "exit", "stop",
                               when, -_money(credit * 2))  # fmt: skip
        if trade.exit_kind == "exit":
            exit_mids = [_money(self.mark(s, when)) for s in symbols]
            trade.exit_mids = exit_mids
        self.history.paper.append(trade)

    # ----- end-of-day values -----
    def values(self, book: Book) -> list[DailyValue]:
        snapshots = self._snapshots[book.account]
        out, i = [], 0
        cash, options, stocks = START_CASH, {}, {}
        for day in self.history.days:
            while i < len(snapshots) and market_date(snapshots[i][0]) <= day:
                _, cash, options, stocks = snapshots[i]
                i += 1
            close_at = datetime.combine(day, CLOSE, tzinfo=NY)
            value = cash
            for key, quantity in options.items():
                underlying = parse_occ(key)[0]
                spot = self.bars(underlying)[day].close
                mark = self.market.option_mark_at(key, spot, close_at, self.vix(close_at))
                value += _money(mark) * quantity * MULTIPLIER
            for ticker, shares in stocks.items():
                value += shares * _money(self.bars(ticker)[day].close)
            out.append(DailyValue(day, value.quantize(CENT), cash.quantize(CENT)))
        return out


_SPREAD_NOTES = {
    2: "Sold into a quiet morning. Took half the credit early.",
    6: "VIX was up a bit, so I went a little further out.",
    10: "Closed early: no reason to hold into the weekend.",
}
_EARNINGS = (
    ("MSFT", date(2026, 7, 28), date(2026, 7, 31), "Report after the close; sold the dip zone."),
    ("AAPL", date(2026, 7, 30), date(2026, 7, 31), "Bought back at the open once IV came in."),
    ("NVDA", date(2026, 8, 26), date(2026, 8, 28), "Small size: NVDA moves a lot on reports."),
)


def make(market: PretendMarket, tasty: Book, schwab: Book, bots: dict[str, BotDef],
         seed: int = 1) -> History:  # fmt: skip
    """Write the made-up past into `tasty` and `schwab`. `bots` are the saved Live bots
    by name; their trades get the exits their own exit math sets."""
    maker = _Maker(market, tasty, schwab, bots, seed)
    days = maker.history.days
    for day in days:
        weekday = day.weekday()
        if schedule.runs_on(bots[CONDOR], day):  # the days its own schedule says
            maker.condor(day)
        if weekday in (0, 3) and day >= FIRST_DAY + timedelta(weeks=2):  # Mondays, Thursdays
            maker.calendar(day, still_open=day == CALENDAR_DAY)
        if weekday == 1 and day >= date(2026, 8, 25) and day + timedelta(days=3) <= LAST_DAY:
            maker.paper(day)  # the QQQ bot's Tuesdays, each over by the Friday
    wednesdays = [d for d in days if d.weekday() == 2 and d + timedelta(days=9) <= LAST_DAY]
    for i, day in enumerate(wednesdays):
        maker.put_spread(day, _SPREAD_NOTES.get(i, ""))
    for ticker, day, expiry, note in _EARNINGS:
        maker.earnings(ticker, day, expiry, note)
    maker.stock_trade("AAPL", 100, date(2026, 7, 21), None, "Long-term holding; I sell calls.")
    maker.swing(
        "NVDA",
        60,
        (date(2026, 7, 20), date(2026, 7, 22)),
        (date(2026, 7, 27), date(2026, 7, 31)),
        win=True,
    )
    maker.swing(
        "MSFT",
        20,
        (date(2026, 7, 27), date(2026, 7, 29)),
        (date(2026, 8, 3), date(2026, 8, 7)),
        win=True,
    )
    maker.swing(
        "MSFT",
        20,
        (date(2026, 9, 1), date(2026, 9, 4)),
        (date(2026, 9, 21), date(2026, 9, 25)),
        win=False,
    )
    maker.swing(
        "NVDA",
        80,
        (date(2026, 9, 21), date(2026, 9, 25)),
        (date(2026, 10, 12), date(2026, 10, 16)),
        win=True,
    )
    for sold, expiry in ((date(2026, 7, 24), date(2026, 8, 21)),
                         (date(2026, 8, 24), date(2026, 9, 18)),
                         (date(2026, 9, 21), date(2026, 10, 16))):  # fmt: skip
        maker.covered_call(sold, expiry)
    maker.run()
    tasty.history = maker.values(tasty)
    schwab.history = maker.values(schwab)
    return maker.history
