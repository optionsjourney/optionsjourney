"""A fake TastyTrade, analyzer and quote hub for the bot tests."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots.definition import (
    DEFAULT_EXIT_CHASE,
    EntryTimeDef,
    GroupDef,
    LegDef,
    iron_condor_skeleton,
    largest_ratio,
    shape,
)
from optionsjourney.core.quotes import Quote
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import Bot, Tag
from optionsjourney.services import bot_store
from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus, Side, plain

D = Decimal
SPOT = D("5800")
OPEN_TIME = datetime(2026, 10, 13, 13, 47, 30, tzinfo=UTC)  # 09:47:30 New York


TODAY = "2026-10-13"


def sym(strike, right, expiration=TODAY, root="SPXW"):
    day = date.fromisoformat(expiration).strftime("%y%m%d")
    return f"{root:<6}{day}{right[0].upper()}{int(D(strike) * 1000):08d}"


def mid_for(strike, right, expiration=TODAY):
    """A made-up mid: 30 at the money, 0.25 less a point out, and (bots slice 14) 0.50
    more for each day to expiration, so a calendar opens for a debit."""
    strike = D(strike)
    distance = (strike - SPOT) if right == "call" else (SPOT - strike)
    days = (date.fromisoformat(expiration) - date.fromisoformat(TODAY)).days
    return max(D("30") - distance * D("0.25"), D("0.05")) + D("0.5") * days


def rows(expiration=TODAY, root="SPXW", scale=1):
    """The chain, on `root` (bots slice 16: another underlying's, same made-up prices).
    `scale` divides the strikes (10: XSP's, spot 580) and keeps each mid as its SPX
    strike's; scaled_prices gives a FakeTasty those mids to fill at."""
    out = {}
    for k in range(5500, 6105, 5):
        strike = D(k) / scale
        out[float(strike)] = {
            right: {
                "bid": float(mid_for(k, right, expiration) - D("0.05")),
                "ask": float(mid_for(k, right, expiration) + D("0.05")),
                "symbol": sym(strike, right, expiration, root),
            }
            for right in ("call", "put")
        }
    return out


def scaled_prices(chain) -> dict:
    """{symbol: mid} for a FakeTasty's fills on a scaled chain (rows(scale=...))."""
    return {
        side["symbol"]: (D(str(side["bid"])) + D(str(side["ask"]))) / 2
        for row in chain.values()
        for side in row.values()
    }


def make(factory, live=False, enabled=True, exhaust="give_up", **changes):
    """Save an iron condor bot on the test account; returns its id. Its closes give
    up after their last try (what bots saved before slice 3 do) unless `exhaust` says.
    `changes` are set on the bot as saved (bots slice 16: another underlying)."""
    with factory() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    bot = iron_condor_skeleton("IC")
    targets = {0: D("2.70"), 1: D("75"), 2: D("2.90"), 3: D("-75")}
    base = {
        "tag_id": sid, "account_number": "5WX01234", "max_per_trade": 1, "max_per_day": 3,
        "legs": tuple(replace(leg, target=targets[leg.position]) for leg in bot.legs),
        "groups": tuple(replace(g, stop_pct=D("95")) for g in bot.groups),
        "entry_times": (EntryTimeDef("09:47"),),
        "exit_chase": replace(bot.exit_chase, exhaust_mode=exhaust),
    }  # fmt: skip
    bot = replace(bot, **(base | changes))
    bot_id, problems = bot_store.save(factory, bot, OPEN_TIME)
    assert problems == {}, problems
    bot_store.set_live(factory, bot_id, live, OPEN_TIME)
    bot_store.set_armed(factory, bot_id, enabled, OPEN_TIME)
    return bot_id


SHAPE_MATRIX = (
    "iron_condor",
    "put_credit_spread",
    "call_debit_spread",
    "short_put",
    "long_call",
    "iron_butterfly",
    "long_call_butterfly",
)
# Numbers that pick on rows(): premium targets are mids, offsets are points from the
# linked leg, the iron butterfly's body is at the spot (its target is already 0).
_SHAPE_TARGETS = {
    "iron_condor": {0: "2.70", 1: "75", 2: "2.90", 3: "-75"},
    "put_credit_spread": {0: "2.90", 1: "-75"},
    "call_debit_spread": {0: "20", 1: "75"},
    "short_put": {0: "2.90"},
    "long_call": {0: "2.50"},
    "iron_butterfly": {2: "75", 3: "-75"},
    "long_call_butterfly": {0: "10", 1: "-75", 2: "75"},
    "long_straddle": {},
    "short_strangle": {0: "2.70", 1: "2.90"},
}


# The credit shapes, which rested their stops before decision 0054
LEGACY_STOP = {"put_credit_spread", "call_credit_spread", "short_put", "short_call", "iron_condor",
               "iron_butterfly", "short_straddle", "short_strangle", "blank"}  # fmt: skip


def make_shape(factory, key, *, live=False, enabled=True, exhaust="give_up",
               stop=None, target=None):  # fmt: skip
    """Save a bot of any preset shape (definition.SHAPES) on the test account; returns
    its id. Every blank target is filled with numbers that pick on rows(); `stop` and
    `target` (percent) are set on every group when given. The limits are raised to
    fit the largest leg's ratio."""
    with factory() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    bot = shape(key, iron_condor_skeleton(key))
    fills = _SHAPE_TARGETS[key]
    ratio = largest_ratio(bot)
    bot = replace(
        bot, tag_id=sid, account_number="5WX01234",
        max_per_trade=max(bot.max_per_trade or 1, bot.contracts * ratio),
        max_per_day=max(3, bot.contracts * ratio),
        legs=tuple(replace(leg, target=D(fills[leg.position])) if leg.position in fills else leg
                   for leg in bot.legs),
        # A credit shape set to rest its stop, as before decision 0054: the runtime still
        # rests a spread's stop for trades opened then, so these tests keep that path.
        groups=tuple(replace(g, stop_pct=stop, target_pct=target,
                             broker_exit="stop" if key in LEGACY_STOP else g.broker_exit)
                     for g in bot.groups),
        entry_times=(EntryTimeDef("09:47"),),
        exit_chase=replace(bot.exit_chase, exhaust_mode=exhaust),
    )  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, OPEN_TIME)
    assert problems == {}, problems
    bot_store.set_live(factory, bot_id, live, OPEN_TIME)
    bot_store.set_armed(factory, bot_id, enabled, OPEN_TIME)
    return bot_id


NEAR, FAR = "2026-10-20", "2026-10-27"  # 7 and 14 DTE from OPEN_TIME's day
HELD_EXPIRATIONS = (TODAY, "2026-10-14", "2026-10-16", NEAR, "2026-10-21", FAR)


def make_calendar(factory, *, live=False, enabled=True, near=7, far=14, offset="0",
                  right="put", credit=False, stop="50", target=None, exact=True,
                  exhaust="give_up", close_time="15:45", **extra):  # fmt: skip
    """Save a calendar (offset 0) or diagonal bot (bots slice 14): sell the near option
    at about 2.90 + its time value, buy the far one `offset` points from it, in one group.
    It rests its target (none unless `target`), so on its debit fill the app watches its
    stop on the group's P&L; unless `credit` (a credit diagonal: its stop rests on the near
    short)."""
    with factory() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    basis = "leg_premium" if credit else "group_entry"
    sell = LegDef(0, Side.SELL, right, dte=near, pick="premium",
                  target=D("2.90") + D("0.5") * near)  # fmt: skip
    buy = LegDef(1, Side.BUY, right, dte=far, pick="offset", target=D(offset), link=0)
    group = GroupDef(
        0, "Calendar", stop_pct=None if stop is None else D(stop),
        target_pct=None if target is None else D(target), stop_basis=basis,
        broker_exit="stop" if credit else "target",
    )  # fmt: skip
    bot = replace(
        iron_condor_skeleton("Cal"), tag_id=sid, account_number="5WX01234", max_per_day=3,
        legs=(sell, buy), groups=(group,), use_exact_dte=exact,
        close_time=close_time,
        entry_times=(EntryTimeDef("09:47"),),
        exit_chase=replace(DEFAULT_EXIT_CHASE, exhaust_mode=exhaust), **extra,
    )  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, OPEN_TIME)
    assert problems == {}, problems
    bot_store.set_live(factory, bot_id, live, OPEN_TIME)
    bot_store.set_armed(factory, bot_id, enabled, OPEN_TIME)
    return bot_id


def force_contracts(factory, bot_id, contracts):
    """Size a bot past validation's 1-contract limit, straight in the table, to
    exercise the multi-contract paths the code keeps (partial fills, sizing)."""
    with factory() as session:
        bot = session.get(Bot, bot_id)
        bot.contracts, bot.max_per_trade, bot.max_per_day = contracts, contracts, 20
        session.commit()


class FakeChain:
    """`chain` is today's chain; another listed expiration gets its own (rows(day)),
    unless `chains` names one for it (bots slice 14)."""

    def __init__(self, expirations=(TODAY,), spot=5800.0, chain=None, root="SPXW"):
        self.exps, self.spot, self.chain = (
            list(expirations),
            spot,
            rows(root=root) if chain is None else chain,
        )
        self.root = root  # bots slice 16: another underlying's chains
        self.chains: dict[str, dict] = {}
        self.asked: list[str] = []

    def expirations(self, clients, ticker):
        if ticker.startswith("/"):  # bots slice 17: the futures series' dates
            return {"expirations": sorted({o.expiration.isoformat() for o in self._listed()})}
        return {"expirations": self.exps}

    nested = None  # bots slice 17: TastyTrade's nested futures chain (futures_nested)

    def futures_nested(self, clients, ticker):
        self.asked.append(ticker)
        return self.nested if ticker == "/ES" else None

    def _listed(self):
        from optionsjourney.bots import futures

        return futures.listed(self.nested)[0].values()

    def option_rows(self, clients, hub, ticker, expiration):
        self.asked.append(expiration)
        if expiration == TODAY:
            return self.chain
        if expiration in self.chains:
            return self.chains[expiration]
        return rows(expiration, self.root) if expiration in self.exps else None

    def underlying_last(self, clients, hub, ticker):
        self.priced = [*getattr(self, "priced", []), ticker]  # bots slice 16: which ticker
        return getattr(self, "spots", {}).get(ticker, self.spot)


class FakeHub:
    def __init__(self):
        self.quotes: dict[str, Quote] = {}
        self.deltas: dict[str, tuple] = {}
        self.subscribed: set[str] = set()
        self.streams = {"tastytrade": "streaming"}

    def status(self):
        return dict(self.streams)

    def subscribe(self, keys):
        self.subscribed |= set(keys)

    def latest(self, key):
        return self.quotes.get(key)

    def set(self, key, bid, ask, at):
        self.quotes[key] = Quote(key, D(str(bid)), D(str(ask)), None, "tastytrade", at)

    def delta(self, key):
        return self.deltas.get(key)

    def set_delta(self, key, delta, at):
        self.deltas[key] = (D(str(delta)), at)

    def set_last(self, key, last, at):
        self.quotes[key] = Quote(key, None, None, D(str(last)), "tastytrade", at, at)


def stream_chain(hub, at=OPEN_TIME):
    """Every leg of today's chain (rows()) on the stream at its made-up bid and ask, as a
    Paper bot's pretend fill reads them (services/bot_paper.py)."""
    for strike, sides in rows().items():
        for right, side in sides.items():
            mid = mid_for(D(str(strike)), right)
            hub.set(side["symbol"], mid - D("0.05"), mid + D("0.05"), at)


def _expires_today(symbol: str) -> bool:
    parsed = None if symbol.startswith("./") else parse_occ(symbol)
    return parsed is not None and parsed[1].expiration.isoformat() == TODAY


def _a_target(body) -> bool:
    """Whether a closing order is a resting profit target: a GTC limit closing a whole
    group, which buys something back, or sells a long-only group expiring today (an
    overnight wing sale is one GTC sell too, but only of a leg that doesn't expire today
    (_resting_tif); a long-only target on a later expiration can't be told from it, so it's
    booked by `close`)."""
    if body.get("time-in-force") != "GTC" or body["order-type"] != "Limit":
        return False
    return any(leg["action"] == "Buy to Close" for leg in body["legs"]) or all(
        leg["action"] == "Sell to Close" and _expires_today(leg["symbol"]) for leg in body["legs"]
    )


class FakeTasty:
    """Orders follow `self.behaviour`: entry ("fill", "work", "partial"), close ("fill", "work").

    `fills` is a per-order fill plan: while it has entries, each successive opening
    limit order (a send or a replace) fills the next number of units from it, e.g.
    [1, 2] fills 1 unit of the first order and 2 of the next. Once it's empty,
    `entry` decides. `stuck` holds order ids whose cancel the broker ignores."""

    broker, name = "tastytrade", "TastyTrade"

    def __init__(self, entry="fill", close="fill"):
        self.entry, self.close = entry, close
        self.calls, self.orders, self.held, self.errors = [], {}, {}, {}
        self.next_id = 100
        self.prices: dict[str, Decimal] = {}
        self.fills: list[int] = []
        self.stuck: set[str] = set()
        self.market_works: set[str] = set()  # plain symbols whose market orders don't fill
        self.resting = "work"  # a GTC closing limit (a resting target) waits for its price

    def _new_id(self):
        self.next_id += 1
        return str(self.next_id)

    def _raise(self, name):
        if name in self.errors:
            raise self.errors[name]

    # Decision 0050: what a dry run says one unit needs on its own. `margin` is the
    # isolated requirement a unit; the price paid or received comes from the body.
    margin = Decimal(0)
    fees = Decimal(0)

    def preview_order(self, account, body):
        self.calls.append(("preview", body))
        self._raise("preview")
        units = Decimal(str(body.get("legs", [{}])[0].get("quantity", 1)))
        return {
            "buying-power-effect": {
                "isolated-order-margin-requirement": str(self.margin * units),
                "isolated-order-margin-requirement-effect": "Debit",
            },
            "fee-calculation": {"total-fees": str(self.fees * units), "total-fees-effect": "Debit"},
        }

    def _default_price(self, symbol):
        if symbol.startswith("./"):  # bots slice 17: a futures option fills at its price
            return self.prices.get(plain(symbol), D("12.50"))
        _, details = parse_occ(symbol)
        return mid_for(details.strike, details.put_call.lower(), details.expiration.isoformat())

    def _legs(self, body, behaviour):
        legs = []
        smallest = min(int(leg["quantity"]) for leg in body["legs"])
        for leg in body["legs"]:
            action, qty = leg["action"], int(leg["quantity"])
            side = Side.BUY if action.startswith("Buy") else Side.SELL
            opening = action.endswith("Open")
            if isinstance(behaviour, int):  # units from the fill plan, at each leg's ratio
                done = min(qty, behaviour * (qty // smallest))
            else:
                done = {"fill": qty, "work": 0, "partial": qty // 2}[behaviour]
            price = self.prices.get(leg["symbol"], self._default_price(leg["symbol"]))
            legs.append(
                BrokerLeg(leg["symbol"], side, opening, qty, qty - done, price if done else None)
            )
        return tuple(legs)

    def _book(self, oid, body):
        kind = body["order-type"]
        opening = body["legs"][0]["action"].endswith("Open")
        if kind in ("Stop", "Stop Limit"):
            behaviour = "work"
        elif kind == "Market":
            symbol = plain(body["legs"][0]["symbol"])
            behaviour = "work" if symbol in self.market_works else "fill"
        elif not opening and _a_target(body):
            behaviour = self.resting
        elif opening and self.fills:
            behaviour = self.fills.pop(0)
        else:
            behaviour = self.entry if opening else self.close
        legs = self._legs(body, behaviour)
        filled = all(leg.remaining == 0 for leg in legs)
        any_done = any(leg.remaining < leg.quantity for leg in legs)
        status = (
            OrderStatus.FILLED
            if filled
            else (OrderStatus.PARTIAL if any_done else OrderStatus.WORKING)
        )
        self.orders[oid] = BrokerOrder(
            oid, status, legs, body.get("external-identifier"), datetime.now(UTC)
        )
        for leg in legs:
            done = leg.quantity - leg.remaining
            self.held[plain(leg.symbol)] = self.held.get(plain(leg.symbol), 0) + (
                done if leg.side is Side.BUY else -done
            )

    def place_order(self, account, body):
        self._raise("place")
        oid = self._new_id()
        self.calls.append(("place", oid, body))
        self._book(oid, body)
        return oid

    def replace_order(self, account, order_id, body):
        self._raise("replace")
        old = self.orders[order_id]
        self.orders[order_id] = replace(old, status=OrderStatus.CANCELLED)
        oid = self._new_id()
        self.calls.append(("replace", order_id, oid, body))
        self._book(oid, body)
        return oid

    def cancel_order(self, account, order_id):
        self.calls.append(("cancel", order_id))
        order = self.orders[order_id]
        if order.may_be_working and order_id not in self.stuck:
            self.orders[order_id] = replace(order, status=OrderStatus.CANCELLED)

    def part_fill(self, order_id, units):
        """Fill `units` whole units of a working order, the rest still working (a resting
        target part-filled)."""
        order = self.orders[order_id]
        smallest = min(leg.quantity for leg in order.legs)
        legs = []
        for leg in order.legs:
            done = units * (leg.quantity // smallest)
            legs.append(replace(leg, remaining=leg.quantity - done,
                                fill_price=leg.fill_price or D("1")))  # fmt: skip
            self.held[plain(leg.symbol)] = self.held.get(plain(leg.symbol), 0) + (
                done if leg.side is Side.BUY else -done
            )
        self.orders[order_id] = replace(order, status=OrderStatus.PARTIAL, legs=tuple(legs))

    def fill(self, order_id, price=None):
        """Make a working order fill now (a stop triggering, a close filling)."""
        order = self.orders[order_id]
        legs = tuple(
            replace(leg, remaining=0, fill_price=price or leg.fill_price or D("1"))
            for leg in order.legs
        )
        self.orders[order_id] = replace(order, status=OrderStatus.FILLED, legs=legs)
        for leg in legs:
            self.held[plain(leg.symbol)] = self.held.get(plain(leg.symbol), 0) + (
                leg.quantity if leg.side is Side.BUY else -leg.quantity
            )

    def get_order(self, account, order_id):
        self._raise("get_order")
        return self.orders[order_id]

    def recent_orders(self, account, since=None):
        return list(self.orders.values())

    def working_orders(self, account):
        return [o for o in self.orders.values() if o.may_be_working]

    def option_positions(self, account):
        self._raise("positions")
        return {k: v for k, v in self.held.items() if v}

    def equity_tick_sizes(self, symbol):
        """A stock's single-leg increments (bots slice 16): `ticks[symbol]`, else a penny
        at every price (SPY, QQQ, IWM)."""
        return getattr(self, "ticks", {}).get(symbol, [{"value": "0.01"}])

    def future_tick_sizes(self, contract):
        """A futures contract's spread increments (bots slice 17): `fut_ticks`, else none."""
        self.calls.append(("future_ticks", contract))
        return getattr(self, "fut_ticks", {}).get(contract, [])

    def futures_option_tick_sizes(self, product):
        """Each series' increments (bots slice 17): `fop_ticks`, else none."""
        self.calls.append(("fop_ticks", product))
        return dict(getattr(self, "fop_ticks", {}).get(product, {}))


_SCHWAB_ACTIONS = {
    "BUY_TO_OPEN": "Buy to Open",
    "SELL_TO_OPEN": "Sell to Open",
    "BUY_TO_CLOSE": "Buy to Close",
    "SELL_TO_CLOSE": "Sell to Close",
}
_SCHWAB_TYPES = {
    "LIMIT": "Limit", "NET_CREDIT": "Limit", "NET_DEBIT": "Limit", "NET_ZERO": "Limit",
    "MARKET": "Market", "STOP": "Stop", "STOP_LIMIT": "Stop Limit",
}  # fmt: skip


class FakeSchwab(FakeTasty):
    """FakeTasty's book behind Schwab's order bodies (trading/schwab.order_body): the
    calls keep Schwab's JSON, so a test can see exactly what went to Schwab."""

    broker, name = "schwab", "Schwab"
    unit_cost = Decimal("275")  # what one more unit takes off projectedAvailableFund

    def preview_order(self, account, body):
        self.calls.append(("preview", body))
        self._raise("preview")
        units = Decimal(str(body["orderLegCollection"][0]["quantity"]))
        fund = Decimal("10000") - self.unit_cost * units
        return {"orderStrategy": {"orderBalance": {"projectedAvailableFund": float(fund)}}}

    def _book(self, oid, body):
        legs = [
            {"symbol": leg["instrument"]["symbol"], "quantity": leg["quantity"],
             "action": _SCHWAB_ACTIONS[leg["instruction"]]}
            for leg in body["orderLegCollection"]
        ]  # fmt: skip
        super()._book(oid, {"order-type": _SCHWAB_TYPES[body["orderType"]], "legs": legs})


def make_schwab(factory, live=False, enabled=True, exhaust="give_up"):
    """make()'s iron condor, trading through Schwab (saved past the switch)."""
    bot_id = make(factory, live=live, enabled=enabled, exhaust=exhaust)
    with factory() as session:
        session.get(Bot, bot_id).broker = "schwab"
        session.commit()
    return bot_id


def rest_the_target(deps, factory, run_id, position=0):
    """Make a run's group what a group that chose Profit target gets (decision 0048): its
    resting stop cancelled at the fake and held by the app on its short (a watched group's
    stays watched on its P&L), its armed target sent to rest as a GTC closing limit.
    Returns (stop id, target id)."""
    from optionsjourney.db.models import BotExit
    from optionsjourney.services import bot_exits

    with factory() as session:
        rows = list(session.scalars(select(BotExit).where(
            BotExit.run_id == run_id, BotExit.group_position == position)))  # fmt: skip
        stop = max((r for r in rows if r.kind == "stop"), key=lambda r: r.id)
        target = max((r for r in rows if r.kind == "target"), key=lambda r: r.id)
        stop_id, target_id, stop_order = stop.id, target.id, stop.broker_order_id
        legs, units, limit = bot_exits.exit_legs(target), target.contracts, target.limit
    if stop_order:
        deps.tasty.cancel_order("5WX01234", stop_order)
    run, bot = bot_exits._run_and_bot(deps, run_id)
    state, order_id, rested, _ = bot_exits._send_target(deps, bot, run, legs, units, limit, True)
    assert state == "working", state
    with factory() as session:
        stop = session.get(BotExit, stop_id)
        stop.state, stop.broker_order_id = bot_exits.WATCHING, None
        stop.app_held = bool(stop.anchor_symbol) and not stop.watched
        target = session.get(BotExit, target_id)
        target.state, target.broker_order_id, target.limit = "working", order_id, rested
        target.log = [*target.log, {"rests": "target"}]
        session.commit()
    return stop_id, target_id


def arm_the_target(deps, factory, run_id, position=0):
    """Make a run's group what a trade opened before decision 0048 still has (migration
    0033 leaves an open trade as it started): its resting target cancelled at the fake and
    armed instead, for the watch pass to fire. Returns the target's id."""
    from optionsjourney.db.models import BotExit

    with factory() as session:
        target = max(session.scalars(select(BotExit).where(
            BotExit.run_id == run_id, BotExit.group_position == position,
            BotExit.kind == "target")), key=lambda r: r.id)  # fmt: skip
        assert target.state == "working", target.state
        deps.tasty.cancel_order("5WX01234", target.broker_order_id)
        target.state, target.broker_order_id = "armed", None
        target.limit = Decimal(target.log[0]["close_at"])  # unrounded, as it was armed
        target.log = [{k: v for k, v in e.items() if k != "rests"} for e in target.log]
        session.commit()
        return target.id


def on_underlying(deps, underlying):
    """Point the fakes at `underlying`'s chain (bots slice 16), on its own root: XSP's at a
    tenth of the strikes (spot 580, read as SPX ÷ 10), filled at the chain's mids. Returns
    what make() needs for it: the underlying, and for XSP wings 8 points out."""
    scale = 10 if underlying == "XSP" else 1
    root = "SPXW" if underlying == "SPX" else underlying
    chain = rows(root=root, scale=scale)
    deps.chain = FakeChain(chain=chain, root=root)
    deps.tasty.prices.update(scaled_prices(chain))
    changes = {"underlying": underlying}
    if scale != 1:
        targets = {0: D("2.70"), 1: D("8"), 2: D("2.90"), 3: D("-8")}
        changes["legs"] = tuple(
            replace(leg, target=targets[leg.position]) for leg in iron_condor_skeleton("IC").legs
        )
    return changes


# --- Bots slice 17: futures options --------------------------------------------------

FUT_EXP = "2026-11-20"  # 38 days from OPEN_TIME's day
ES_TICKS = [{"threshold": "5.0", "value": "0.05"}, {"value": "0.25"}]  # /ES: 0.05, 0.25 at 5


def fut_series(day=FUT_EXP, code="EW3X6", contract="/ESZ6", nv="0.5",
               strikes=tuple(range(6800, 7205, 25)), expires="21:00", settles="PM"):  # fmt: skip
    """One series in TastyTrade's nested shape (made-up)."""
    return {
        "expiration-date": day, "option-root-symbol": code[:-2], "option-contract-symbol": code,
        "underlying-symbol": contract, "settlement-type": settles, "notional-value": nv,
        "expires-at": f"{day}T{expires}:00.000+00:00", "tick-sizes": ES_TICKS,
        "strikes": [
            {"strike-price": f"{k}.0", "call": fut_sym(k, "call", code, contract, day),
             "put": fut_sym(k, "put", code, contract, day),
             "call-streamer-symbol": fut_stream(k, "call", code),
             "put-streamer-symbol": fut_stream(k, "put", code)}
            for k in strikes
        ],
    }  # fmt: skip


def fut_sym(strike, right, code="EW3X6", contract="/ESZ6", day=FUT_EXP):
    """TastyTrade's order symbol: "./ESZ6 EW3X6 261120P7000"."""
    return f".{contract} {code} {day.replace('-', '')[2:]}{right[0].upper()}{strike}"


def fut_stream(strike, right, code="EW3X6"):
    """DXLink's: "./EW3X26P7000:XCME"."""
    return f"./{code[:-2]}{code[-2]}2{code[-1]}{right[0].upper()}{strike}:XCME"


def es_nested(*more):
    return {
        "futures": [{"symbol": "/ESZ6", "streamer-symbol": "/ESZ26:XCME"},
                    {"symbol": "/ESH7", "streamer-symbol": "/ESH27:XCME"}],
        "option-chains": [{"expirations": [fut_series(), *more]}],
    }  # fmt: skip


def on_futures(deps):
    """Point the fakes at /ES: the analyzer's nested chain, TastyTrade's series increments."""
    deps.chain.nested = es_nested()
    deps.tasty.fop_ticks = {"ES": {"EW3X6": ES_TICKS}}
    deps.tasty.fut_ticks = {"/ESZ6": [{"value": "0.05"}]}


def make_futures(factory, legs=(("sell", "put"),), *, live=True, target="50", groups=None,
                 **changes):  # fmt: skip
    """Save a /ES futures bot (bots slice 17): its legs a shape (side, right), each in its
    own group unless `groups` says (a list of group positions), a profit target of
    `target`% on every group and no stop; live, never armed. Returns its id."""
    with factory() as session:
        sid = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    positions = list(range(len(legs))) if groups is None else list(groups)
    leg_defs = tuple(
        LegDef(n, Side(side), right, dte=38, group=positions[n])
        for n, (side, right) in enumerate(legs)
    )
    names = {0: "Put", 1: "Call", 2: "Group 3", 3: "Group 4"}
    group_defs = tuple(
        GroupDef(p, names[p], stop_pct=None, target_pct=None if target is None else D(target))
        for p in sorted(set(positions))
    )
    bot = replace(
        iron_condor_skeleton("ES"), tag_id=sid, account_number="5WX01234",
        underlying="/ES", legs=leg_defs, groups=group_defs, entry_times=(),
        max_per_trade=4, max_per_day=8, **changes,
    )  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, OPEN_TIME)
    assert problems == {}, problems
    bot_store.set_live(factory, bot_id, live, OPEN_TIME)
    return bot_id
