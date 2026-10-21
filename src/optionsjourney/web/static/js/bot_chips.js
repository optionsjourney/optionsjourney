// The journal's bot chips, redrawn live (bots slice 20; Tracker2's journal.html
// refreshBotTags). The server pushes bot_tags_changed {run_id} over Socket.IO when a
// run's exits or overrides change; each cell of that run reads its chips again from
// GET /journal/bot-chips/<run_id>?symbols=… and rings the ones that are new for a while.

export const RING_MS = 6000;

// [{label, title, className, isNew}] for a cell that showed `before` (labels).
export function chipViews(chips, before) {
  const had = new Set(before || []);
  return (chips || []).map(c => ({
    label: c.label,
    title: c.title || '',
    className: `chip chip--${c.warning ? 'off' : 'changed'}`,
    isNew: !had.has(c.label),
  }));
}

export async function refresh(doc, win, runId) {
  const cells = doc.querySelectorAll(`.bot-chips[data-run-id="${runId}"]`);
  for (const cell of cells) {
    let data;
    try {
      const url = `/journal/bot-chips/${runId}?symbols=${encodeURIComponent(cell.dataset.symbols || '')}`;
      const res = await win.fetch(url, { headers: { Accept: 'application/json' } });
      if (!res.ok) continue;
      data = await res.json();
    } catch (e) {
      continue; // the cell keeps the chips it has
    }
    const before = [...cell.children].map(c => c.textContent);
    cell.replaceChildren(...chipViews(data.chips, before).map(v => {
      const span = doc.createElement('span');
      span.className = v.className;
      span.title = v.title;
      span.textContent = v.label;
      if (v.isNew) {
        span.classList.add('chip--new');
        win.setTimeout(() => span.classList.remove('chip--new'), RING_MS);
      }
      return span;
    }));
  }
}

export function install(doc, win) {
  if (typeof win.io !== 'function' || !doc.querySelector('.bot-chips')) return;
  const socket = win.io({ transports: ['websocket', 'polling'] });
  socket.on('bot_tags_changed', msg => msg && refresh(doc, win, msg.run_id));
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
