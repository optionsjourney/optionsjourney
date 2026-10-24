// The Pending drawer's pure parts (#292): the toolbar label, the chip filters and what each
// order row shows. The leg lines, the owner labels and the price sign are the Trade page's
// working-orders helpers (trade/ticket_core.js), so both screens read an order the same way.

import { workingRows, readableSymbol, optionParts } from '../trade/ticket_core.js';

export const FILTERS = ['all', '0dte', 'calendars', 'options'];

// The toolbar button's text: nothing when no order waits and no account failed (the button is hidden).
export function buttonLabel(count, errors) {
  if (!count && !errors) return '';
  return `${errors ? '⚠' : '⏳'} ${count} pending`;
}

const shown = (o, outsideOnly) => !outsideOnly || (o.owner && o.owner.kind === 'outside');

// The accounts with only the orders the chip (and "Outside the app only") lets through. An
// account that failed stays, so its error line is not lost.
export function filterOrders(accounts, filter, outsideOnly) {
  return accounts
    .map(a => ({ ...a, orders: a.orders.filter(o => shown(o, outsideOnly) && (filter === 'all' || o.category === filter)) }))
    .filter(a => a.orders.length || a.error);
}

// How many orders each chip would show.
export function chipCounts(accounts, outsideOnly) {
  const counts = Object.fromEntries(FILTERS.map(f => [f, 0]));
  for (const a of accounts) {
    for (const o of a.orders) {
      if (!shown(o, outsideOnly)) continue;
      counts.all += 1;
      if (o.category in counts) counts[o.category] += 1;
    }
  }
  return counts;
}

// "1.25 cr" / "2.00 db" from a signed per-unit price (+ credit); a dash when there is none.
export function money(signed) {
  if (signed == null) return '—';
  return `${Math.abs(signed).toFixed(2)} ${signed < 0 ? 'db' : 'cr'}`;
}

// The number and its side apart, for a cell that colours the side: {value: '1.25', side: 'cr'};
// null when there is no price.
export function moneyParts(signed) {
  if (signed == null) return null;
  return { value: Math.abs(signed).toFixed(2), side: signed < 0 ? 'db' : 'cr' };
}

// "as of 10:42" in New York from the listing's as_of; empty when it can't be read.
export function asOfText(iso) {
  const d = new Date(iso || '');
  if (Number.isNaN(d.getTime())) return '';
  return `as of ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York' })}`;
}

// "WORKING" -> "Working", "PENDING_CHANGE" -> "Pending change".
export function statusText(status) {
  const s = String(status || '').replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// One leg as its Description line shows it: signed quantity (minus for a sell), "Oct 24", the
// strike, C/P and the BTO/STO/BTC/STC badge (Buy/Sell when the broker didn't say opening or
// closing). A leg that isn't an option keeps its symbol. `left` is set on a partly filled leg.
export function legView(leg) {
  const sell = leg.side === 'sell';
  const p = optionParts(leg.symbol);
  const action = leg.opening == null ? (sell ? 'Sell' : 'Buy')
    : `${sell ? 'S' : 'B'}${leg.opening ? 'TO' : 'TC'}`;
  return {
    qty: sell ? -leg.quantity : leg.quantity,
    left: leg.remaining != null && leg.remaining !== leg.quantity ? leg.remaining : null,
    side: sell ? 'sell' : 'buy',
    action,
    expiry: p ? `${p.month} ${p.day}` : null,
    strike: p ? String(p.strike) : null,
    type: p ? p.right : null,
    symbol: p ? null : leg.symbol,
  };
}

// "9:47 AM" in New York, the market's clock; empty when the broker gave no time.
function clock(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' });
}

// One order as the drawer's row shows it. `mark` is the order's current signed per-unit mid
// (null when it can't be priced yet).
export function rowView(order, mark) {
  const r = workingRows([order])[0];
  const first = order.legs[0] ? readableSymbol(order.legs[0].symbol) : '';
  return {
    id: order.order_id,
    from: r.from,
    link: (order.owner && order.owner.link) || null,
    symbol: first.split(' ')[0] || '',
    status: order.status,
    statusText: statusText(order.status),
    ownerKind: (order.owner && order.owner.kind) || 'outside',
    market: money(mark),
    marketParts: moneyParts(mark),
    priceParts: moneyParts(order.price),
    price: money(order.price),
    effect: r.effect,
    time: clock(order.entered_at),
    description: r.lines,
    legs: order.legs.map(legView),
    actions: { change: r.canChange && order.price != null, cancel: r.canCancel, reason: r.reason },
  };
}
