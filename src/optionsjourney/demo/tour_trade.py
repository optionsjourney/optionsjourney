"""The put spread the tour's Trade step opens with: a short put near 15 delta and
a long put 25 points below it, today's SPX expiration. Cells are shaped like the
chain's own (static/js/trade/chain_tray.js) so the page can drop them in the ticket."""

from __future__ import annotations

from decimal import Decimal

from optionsjourney.core.black_scholes import greeks
from optionsjourney.core.quotes import dxlink_symbol
from optionsjourney.core.symbols import OptionDetails, occ_symbol
from optionsjourney.demo.clock import NY
from optionsjourney.demo.market import RATE

_STEP = 5
_WIDTH = 25
_TARGET_DELTA = -0.15
_WINDOW = 400  # points below spot to look through


def _delta(market, occ: str, spot: float) -> float:
    strike, years, vol, put_call = market.option_inputs(occ, spot, market.now)
    return greeks(spot, strike, years, RATE, vol, put_call)["delta"]


def _cell(market, expiration, strike: int, side: str, spot: float) -> dict:
    occ = occ_symbol("SPXW", OptionDetails("PUT", Decimal(strike), expiration))
    bid, ask, _ = market.quote(occ)
    return {
        "expiration": expiration.isoformat(),
        "strike": strike,
        "type": "put",
        "side": side,
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "stream_symbol": dxlink_symbol(occ),
        "delta": round(_delta(market, occ, spot), 4),
    }


def put_spread_tray(market, clock) -> dict:
    expiration = clock.now().astimezone(NY).date()
    spot = market.last("SPX")
    top = int(spot // _STEP) * _STEP
    strikes = range(top - _WINDOW, top + 1, _STEP)

    def gap(k: int) -> float:
        occ = occ_symbol("SPXW", OptionDetails("PUT", Decimal(k), expiration))
        return abs(_delta(market, occ, spot) - _TARGET_DELTA)

    short = min(strikes, key=gap)
    return {
        "ticker": "SPX",
        "cells": [
            _cell(market, expiration, short, "sell", spot),
            _cell(market, expiration, short - _WIDTH, "buy", spot),
        ],
    }
