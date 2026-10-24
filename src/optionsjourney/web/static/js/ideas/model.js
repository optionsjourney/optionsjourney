// The Ideas drawer's pure parts (#292): the status chip, price and age text, and what one idea's
// card shows. The server decides an idea's status and P/L; this only words them.

const CHIPS = { live: 'Live', partly_expired: 'Partly expired', expired: 'Expired' };

export function statusChip(idea) {
  if (idea.status === 'expired' && idea.settlement_na) return 'Expired · settlement n/a';
  return CHIPS[idea.status] || '';
}

// "1.25 cr" / "2.00 db" from a signed per-unit price (+ credit); a dash when there is none.
export function priceText(unit) {
  if (unit == null) return '—';
  return `${Math.abs(unit).toFixed(2)} ${unit < 0 ? 'db' : 'cr'}`;
}

// "just now", "5 min ago", "3 h ago", "2 d ago"; empty when the time can't be read.
export function ageText(created, now) {
  const t = new Date(created).getTime();
  if (Number.isNaN(t)) return '';
  const min = Math.floor((now.getTime() - t) / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  if (min < 1440) return `${Math.floor(min / 60)} h ago`;
  return `${Math.floor(min / 1440)} d ago`;
}

const ET = { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' };

const tone = n => (n > 0 ? 'positive' : n < 0 ? 'negative' : '');
// A loss gets a real minus sign (−), which lines up with the plus.
const sign = n => (n > 0 ? '+' : n < 0 ? '−' : '');
const dollars = n => (n == null ? '—' : `${sign(n)}$${Math.abs(n).toFixed(2)}`);
const percent = n => (n == null ? '' : `${sign(n)}${Math.abs(n).toFixed(1)}%`);

// One idea as its card shows it. Only a live idea can be traded (ruling 12).
export function cardView(idea, now) {
  const saved = new Date(idea.created_at);
  return {
    id: idea.id,
    ticker: idea.ticker,
    label: idea.label,
    note: idea.note || '',
    chip: statusChip(idea),
    status: idea.status,
    canTrade: idea.status === 'live',
    pnl: dollars(idea.pnl),
    pct: percent(idea.pnl_pct),
    tone: tone(idea.pnl),
    saved: Number.isNaN(saved.getTime()) ? '' : `${saved.toLocaleString('en-US', ET)} ET`,
    age: ageText(idea.created_at, now),
    spot: idea.spot_at_entry == null ? '' : `at ${idea.spot_at_entry.toFixed(2)}`,
    cards: idea.cards.map(c => ({
      label: c.label, qty: c.qty, entry: priceText(c.entry), locked: !!c.locked,
      now: priceText(c.now), pnl: dollars(c.pnl), tone: tone(c.pnl),
    })),
  };
}
