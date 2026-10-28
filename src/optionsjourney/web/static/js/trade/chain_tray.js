// Tracker-owned (not vendored -- VENDORED.md patch 16): the Option chain overlay's leg
// tray and the pure helpers around it. No DOM, so node:test covers it
// (tests/web/static/js/trade/chain_tray.test.js). chain_panel.js owns the DOM.
// Spec: docs/superpowers/specs/2026-09-27-analyzer-chain-charts-design.md

export const MAX_TRAY_LEGS = 12;   // analyzer_trade.PREVIEW_MAX_LEGS
const MAX_QTY = 1000;

export function emptyTray() { return { legs: [] }; }

export function legKey(l) { return `${l.expiration}|${l.strike}|${l.type}`; }

// The expiration the chain shows for a loaded trade (a pasted order, an idea): the one shown
// if a leg is in it, else the trade's nearest one the chain lists, else the one shown.
export function trayExpiration(tray, expirations, current) {
  const exps = tray.legs.map(l => l.expiration);
  if (exps.includes(current)) return current;
  return expirations.find(e => exps.includes(e)) || current;
}

// A bid cell sells, an ask cell buys. The same cell again removes the leg; the other cell
// of the same contract flips it (keeping its quantity) rather than adding a second leg.
export function toggleCell(tray, cell) {
  const key = legKey(cell);
  const i = tray.legs.findIndex(l => legKey(l) === key);
  if (i >= 0) {
    if (tray.legs[i].side === cell.side) return { legs: tray.legs.filter((_, j) => j !== i) };
    return { legs: tray.legs.map((l, j) => (j === i ? { ...l, side: cell.side } : l)) };
  }
  if (tray.legs.length >= MAX_TRAY_LEGS) return tray;
  const { expiration, strike, type, side, bid, ask, stream_symbol, delta } = cell;
  return { legs: [...tray.legs, { expiration, strike, type, side, qty: 1, bid, ask, stream_symbol, delta }] };
}

export function setQty(tray, key, qty) {
  const q = Math.min(MAX_QTY, Math.max(1, Math.round(Number(qty) || 1)));
  return { legs: tray.legs.map(l => (legKey(l) === key ? { ...l, qty: q } : l)) };
}

export function removeLeg(tray, key) {
  return { legs: tray.legs.filter(l => legKey(l) !== key) };
}

export function legMid(l) {
  const bid = Number(l.bid), ask = Number(l.ask);
  if (!(ask > 0)) return 0;
  return bid >= 0 && l.bid != null ? (bid + ask) / 2 : ask;
}

export function netMid(tray) {
  return tray.legs.reduce((s, l) => s + legMid(l) * l.qty * (l.side === 'sell' ? 1 : -1), 0);
}

// The tray's net price per spread, TastyTrade-style: positive = credit. Quantities are
// divided by their common factor, so a 3x roll prices one unit (unit = 3). Nat fills
// sells at the bid and buys at the ask; Opp the reverse.
export function netPrices(tray) {
  const legs = tray.legs;
  if (!legs.length) return null;
  const gcd = (a, b) => (b ? gcd(b, a % b) : a);
  const unit = legs.reduce((g, l) => gcd(g, l.qty), 0) || 1;
  const sum = f => r2(legs.reduce((s, l) => s + f(l) * (l.qty / unit) * (l.side === 'sell' ? 1 : -1), 0));
  const bid = l => (Number(l.bid) > 0 ? Number(l.bid) : 0), ask = l => Number(l.ask) || 0;
  return { mid: sum(legMid), nat: sum(l => (l.side === 'sell' ? bid(l) : ask(l))),
    opp: sum(l => (l.side === 'sell' ? ask(l) : bid(l))), unit };
}

export function withQuotes(tray, quotes) {
  return { legs: tray.legs.map(l => {
    const q = quotes && quotes.get(l.stream_symbol);
    return q && q.ask > 0 ? { ...l, bid: q.bid, ask: q.ask } : l;
  }) };
}

const r2 = n => Math.round(n * 100) / 100;

export function bpRequest(tray, accountId, ticker) {
  if (!tray.legs.length) return null;
  return { account_id: accountId, ticker, legs: tray.legs.map(l => ({
    expiration: l.expiration, strike: l.strike, type: l.type, side: l.side, qty: l.qty, mid: r2(legMid(l)),
  })) };
}

// label '' keeps the detected name (VENDORED.md patch 9).
export function previewTrade(tray, ticker) {
  return { ticker, trades: [{ label: '', legs: tray.legs.map(l => ({
    action: l.side, option_type: l.type.toUpperCase(), strike: l.strike,
    expiration: l.expiration, quantity: l.qty, mid: r2(legMid(l)),
  })) }] };
}

export function spreadInfo(bid, ask) {
  if (!(ask > 0) || bid == null || bid < 0) return null;
  const mid = (bid + ask) / 2;
  return { abs: r2(ask - bid), pct: mid > 0 ? (ask - bid) / mid : Infinity };
}

export function spreadTone(pct) {
  if (!(pct < 0.25)) return 'very-wide';
  return pct < 0.10 ? 'ok' : 'wide';
}

function atmIndex(rows, spot) {
  if (!rows.length) return -1;
  if (spot == null || !isFinite(spot)) return Math.floor((rows.length - 1) / 2);
  let best = 0;
  rows.forEach((r, i) => { if (Math.abs(r.strike - spot) < Math.abs(rows[best].strike - spot)) best = i; });
  return best;
}

export function visibleRows(rows, spot, showAll, n = 20) {
  if (showAll) return rows;
  const i = atmIndex(rows, spot);
  return i < 0 ? [] : rows.slice(Math.max(0, i - n), i + n + 1);
}

// The bridge takes 64 symbols per client: nearest-to-spot strikes first, put then call.
export function streamWindow(rows, spot, max = 64) {
  const centre = spot == null || !isFinite(spot) ? (rows[atmIndex(rows, spot)] || {}).strike : spot;
  const out = [];
  for (const r of [...rows].sort((a, b) => Math.abs(a.strike - centre) - Math.abs(b.strike - centre))) {
    for (const s of [r.put, r.call]) {
      if (s && s.stream_symbol && out.length < max) out.push(s.stream_symbol);
    }
  }
  return out;
}

export function bpGate() {
  let seq = 0;
  return { next: () => ++seq, isCurrent: s => s === seq };
}

const usd = n => '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function bpView(res) {
  if (!res) return { tone: 'error', lines: ["Couldn't preview this trade"] };
  if (res.status !== 'success') return { tone: 'error', lines: [res.message || "Couldn't preview this trade"] };
  if (res.available === false) return { tone: 'muted', lines: [res.message] };
  if (!res.ok) return { tone: 'error', lines: res.errors && res.errors.length ? res.errors : ['Preview rejected'] };
  const lines = [];
  if (res.bp_change != null) lines.push(`${res.bp_change < 0 ? 'Uses' : 'Frees'} ${usd(res.bp_change)} BP`);
  if (res.new_buying_power != null) lines.push(`BP after ${usd(res.new_buying_power)}`);
  if (res.net_price != null) lines.push(`Net ${res.net_price >= 0 ? 'credit' : 'debit'} ${usd(res.net_price)}`);
  if (res.fees != null) lines.push(`Fees ${usd(res.fees)}`);
  return { tone: 'ok', lines };
}
