// The risk-graph page: fetch the trades named in ?trade=… and hand them to Tracker2's
// _mountRiskGraphGroup (risk_graph.js), which draws one trade, or several with a checkbox per
// trade and a tab per underlying. Module scripts run after the classic risk_graph.js and the
// inline module that sets window.__modeler, in document order.

const root = document.getElementById('risk-graph');
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function fail(message) {
  document.getElementById('pl-chart-loading').classList.add('hidden');
  document.getElementById('pl-chart-error-text').textContent = message;
  document.getElementById('pl-chart-error').classList.remove('hidden');
}

async function load() {
  const keys = root.dataset.trades;
  if (!keys) return fail('No trade picked.');
  let body;
  try {
    const res = await fetch('/api/risk-graph?trade=' + encodeURIComponent(keys));
    body = await res.json();
  } catch (e) {
    return fail("Couldn't load the trade.");
  }
  if (body.status !== 'success') return fail(body.message || "Couldn't load the trade.");
  const graphable = body.trades.filter(t => !t.error);
  if (!graphable.length) return fail(body.trades.map(t => t.error).join(' '));
  const skipped = body.trades.filter(t => t.error).map(t => t.error).join(' ');
  window._mountRiskGraphGroup(graphable, {
    theo: graphable.length > 1,
    title: view => view.items.length === 1 ? view.items[0].label : `${view.symbol} · ${view.items.length} trades`,
    footer: skipped ? `<p class="text-xs text-gray-500 dark:text-gray-400 px-1 pt-1">${esc(skipped)}</p>` : '',
  });
}

load();
