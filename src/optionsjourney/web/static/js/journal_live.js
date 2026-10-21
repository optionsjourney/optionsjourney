// The journal reloads itself when its trades may have changed: the server pushes
// journal_changed over Socket.IO after a sync stores new or changed fills (a fill the
// account stream reported, a timed sync, Sync now). While you're in the middle of
// something (trades ticked, a drawer open, typing) it shows a reload link instead.
// A page can name another event to reload on with data-live-event (#183: the Paper
// trades view reloads on paper_journal_changed, after each pretend fill).

export const SETTLE_MS = 1500; // fills come in bursts: one reload for them

// Why the page shouldn't reload by itself now, or '' when it may.
export function busy(doc) {
  if (doc.querySelector('input.select-trade:checked')) return 'ticked'; // trades, not other boxes
  if ([...doc.querySelectorAll('.drawer')].some(d => !d.classList.contains('hidden'))) return 'drawer';
  if (doc.querySelector('details.dropdown[open]')) return 'menu';
  const el = doc.activeElement;
  if (el && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return 'typing';
  return '';
}

// The event this page reloads on.
export function liveEvent(doc) {
  return doc.querySelector('[data-live-event]')?.dataset.liveEvent || 'journal_changed';
}

function showNote(doc, win) {
  if (doc.getElementById('journal-changed')) return;
  const note = doc.createElement('div');
  note.id = 'journal-changed';
  note.className = 'notice notice--quiet journal-changed';
  const link = doc.createElement('a');
  link.href = '#';
  link.className = 'link';
  link.textContent = 'Reload';
  link.addEventListener('click', e => { e.preventDefault(); win.location.reload(); });
  note.append('New fills came in. ', link);
  doc.body.append(note);
}

export function install(doc, win) {
  if (typeof win.io !== 'function') return;
  const socket = win.io({ transports: ['websocket', 'polling'] });
  let timer = null;
  socket.on(liveEvent(doc), () => {
    win.clearTimeout(timer);
    timer = win.setTimeout(() => {
      if (busy(doc)) showNote(doc, win);
      else win.location.reload();
    }, SETTLE_MS);
  });
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
