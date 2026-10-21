// The ticker drawer (#236): a stock's daily closes, a yellow dashed line at your cost per share, and
// ▲ buys and ▼ sells on the day you made them. Opened by any [data-stock-chart] button; reads
// GET /api/stocks/<ticker>/chart?account=&range=. Closes like schedule_drawer.js.

import { tipHtml } from './tip.js';

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const money = n => Number(n).toFixed(2);

export const COST_TIP = "Your broker's average cost per share. Above it a long position is in profit; for a short, below it is.";

// The chart as an SVG string. Pure: data is the route's JSON.
export function chartSvg(data, width = 560, height = 280) {
  const closes = data.closes || [];
  if (!closes.length) {
    return `<p class="muted small stock-chart__empty">${esc(data.note || 'No prices for this range.')}</p>`;
  }
  const padL = 8, padR = 52, padT = 14, padB = 14;
  const cost = data.cost_basis;
  const values = closes.map(c => c.close).concat(cost == null ? [] : [cost]);
  let lo = Math.min(...values), hi = Math.max(...values);
  if (hi === lo) { hi += 1; lo -= 1; }
  const x = i => padL + (closes.length === 1 ? (width - padL - padR) / 2 : i * (width - padL - padR) / (closes.length - 1));
  const y = v => padT + (hi - v) * (height - padT - padB) / (hi - lo);
  const index = new Map(closes.map((c, i) => [c.day, i]));
  const points = closes.map((c, i) => `${x(i).toFixed(1)},${y(c.close).toFixed(1)}`).join(' ');
  const right = width - padR + 4;
  let out = `<svg class="stock-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(data.ticker)} closing prices">`;
  out += `<polyline class="stock-chart__line" points="${points}"/>`;
  out += `<text class="stock-chart__label" x="${right}" y="${padT + 4}">${money(hi)}</text>`;
  out += `<text class="stock-chart__label" x="${right}" y="${height - padB}">${money(lo)}</text>`;
  if (cost != null) {
    const cy = y(cost).toFixed(1);
    out += `<line class="stock-chart__cost" x1="${padL}" x2="${width - padR}" y1="${cy}" y2="${cy}"/>`;
    // Inside the plot, at the left, just above the line (below it near the top of the chart).
    const label = `your cost ${money(cost)}`;
    const w = label.length * 6.5 + 8;
    const below = Number(cy) < padT + 16;
    const ty = below ? Number(cy) + 14 : Number(cy) - 5;
    out += `<rect class="stock-chart__bg" x="${padL}" y="${ty - 11}" width="${w.toFixed(1)}" height="14" rx="2"/>`;
    out += `<text class="stock-chart__label" x="${padL + 4}" y="${ty}">${label}</text>`;
  }
  for (const m of data.marks || []) {
    const i = index.get(m.day);
    if (i === undefined) continue;   // a trade on a day with no close is left off
    const px = x(i).toFixed(1), py = y(closes[i].close);
    const buy = m.side === 'buy';
    const tri = buy
      ? `${px},${(py - 9).toFixed(1)} ${(px - 5).toFixed(1)},${(py - 1).toFixed(1)} ${(+px + 5).toFixed(1)},${(py - 1).toFixed(1)}`
      : `${px},${(py + 9).toFixed(1)} ${(px - 5).toFixed(1)},${(py + 1).toFixed(1)} ${(+px + 5).toFixed(1)},${(py + 1).toFixed(1)}`;
    out += `<polygon class="stock-chart__${buy ? 'buy' : 'sell'}" points="${tri}">`
      + `<title>${buy ? '▲ Bought' : '▼ Sold'} ${esc(m.quantity)} at ${money(m.price)} on ${esc(m.day)}</title></polygon>`;
  }
  return out + '</svg>';
}

function legend(data) {
  return `<p class="muted small">▲ bought · ▼ sold · yellow dashed line: your cost per share `
    + `${tipHtml('stocks-cost-line', COST_TIP, 'your cost per share')}</p>`;
}

export function installStockDrawer(doc, win) {
  const drawer = doc.getElementById('stock-drawer');
  if (!drawer) return;
  const backdrop = drawer.querySelector('[data-stock-backdrop]');
  const panel = drawer.querySelector('[data-stock-panel]');
  const body = doc.getElementById('stock-body');
  let ticker = null, range = '1Y', token = 0, opener = null;

  const isOpen = () => !drawer.classList.contains('hidden');

  function markRange() {
    drawer.querySelectorAll('[data-stock-range]').forEach(b => {
      const on = b.dataset.stockRange === range;
      b.classList.toggle('segmented__item--active', on);
      if (on) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
  }

  async function load() {
    const mine = ++token;
    markRange();
    body.innerHTML = '<p class="muted small">Loading…</p>';
    try {
      const base = drawer.dataset.chartUrl.replace('TICKER', encodeURIComponent(ticker));
      const q = new URLSearchParams({ range });
      if (drawer.dataset.account) q.set('account', drawer.dataset.account);
      const res = await win.fetch(`${base}?${q}`, { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (mine !== token) return;
      doc.getElementById('stock-subtitle').textContent =
        `${data.quantity} shares · cost ${money(data.cost_basis)} each`;
      body.innerHTML = chartSvg(data) + legend(data);
    } catch (e) {
      if (mine !== token) return;
      body.innerHTML = `<p class="notice notice--warning small">Couldn't load the chart: ${esc(e.message)}</p>`;
    }
  }

  function open(e) {
    opener = e.currentTarget;
    ticker = opener.dataset.stockChart;
    range = '1Y';
    doc.getElementById('stock-title').textContent = ticker;
    doc.getElementById('stock-subtitle').textContent = '';
    drawer.classList.remove('hidden');
    win.requestAnimationFrame(() => {
      backdrop.classList.remove('opacity-0');
      panel.classList.remove('translate-x-full');
    });
    load();
  }

  function close() {
    if (!isOpen()) return;
    backdrop.classList.add('opacity-0');
    panel.classList.add('translate-x-full');
    win.setTimeout(() => drawer.classList.add('hidden'), 300);
    if (opener) opener.focus();
  }

  doc.querySelectorAll('[data-stock-chart]').forEach(btn => btn.addEventListener('click', open));
  drawer.querySelector('[data-stock-close]').addEventListener('click', close);
  backdrop.addEventListener('click', close);
  drawer.querySelectorAll('[data-stock-range]').forEach(b => b.addEventListener('click', () => {
    range = b.dataset.stockRange;
    load();
  }));
  doc.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !isOpen()) return;
    close();
    e.stopImmediatePropagation();
  }, true);
}
