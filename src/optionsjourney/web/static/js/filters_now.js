// The bot form's "Check the filters now" (bots slice 20; Tracker2's strategy_preview.js
// vixLine). Reads GET /automation/<id>/filters-now, which only reads two quotes.

// One filter's line: {text, cls}.
export function filterLine(f) {
  if (f.error) return { text: `${f.label}: ${f.error}`, cls: 'negative' };
  const when = ` (as of ${f.as_of} ET${f.stale ? ', stale: a live entry would skip' : ''})`;
  return f.ok
    ? { text: `✓ ${f.detail}${when}`, cls: f.stale ? 'muted' : 'positive' }
    : { text: `✗ Would not trade: ${f.detail}${when}`, cls: 'negative' };
}

export function install(doc, win) {
  const box = doc.getElementById('filters-now');
  if (!box) return;
  const button = box.querySelector('[data-filters-now]');
  const out = box.querySelector('[data-filters-out]');
  button.addEventListener('click', async () => {
    button.disabled = true;
    out.replaceChildren(Object.assign(doc.createElement('li'), { textContent: 'Reading…' }));
    try {
      const res = await win.fetch(box.dataset.url, { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const lines = (data.filters || []).map(filterLine);
      if (!lines.length) lines.push({ text: 'Neither VIX filter is on with a number set (save first).', cls: 'muted' });
      out.replaceChildren(...lines.map(l => Object.assign(doc.createElement('li'), { textContent: l.text, className: l.cls })));
    } catch (e) {
      out.replaceChildren(Object.assign(doc.createElement('li'), { textContent: `Couldn't read them: ${e.message}`, className: 'negative' }));
    } finally {
      button.disabled = false;
    }
  });
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
