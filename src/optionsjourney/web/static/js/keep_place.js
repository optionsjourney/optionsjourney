// The bot form's edit buttons (#125): ▲/▼, + Add leg, 🔗, ✕ and the rest post the form
// with an "op" and get the form back redrawn, which a browser shows from the top. This
// remembers where the page was and goes back there. Save (no op) is left alone.

const KEY = 'bot-form-place';
const FRESH_MS = 15000;  // a redraw that comes back later than this is something else

// The op a submit carries: the pressed button's, or a hidden one a script added
// (leg_rows.js's Use leg groups box). Empty for Save.
export function opOf(form, submitter) {
  if (submitter?.name === 'op') return submitter.value || '';
  return form.querySelector('input[type="hidden"][name="op"]')?.value || '';
}

// Where to go back to: the stored place, if it's this fresh.
export function placeFrom(raw, now) {
  try {
    const place = JSON.parse(raw);
    if (!place || typeof place.y !== 'number' || now - place.at > FRESH_MS) return null;
    return place.y;
  } catch {
    return null;
  }
}

export function install(doc, win) {
  const form = doc.querySelector('form.bot-form');
  if (!form) return;
  let stored = null;
  try {
    stored = win.sessionStorage.getItem(KEY);
    win.sessionStorage.removeItem(KEY);
  } catch { /* no storage: the page opens at the top, as before */ }
  const y = placeFrom(stored, Date.now());
  if (y !== null) win.scrollTo(0, y);
  form.addEventListener('submit', (event) => {
    if (!opOf(form, event.submitter)) return;
    try {
      win.sessionStorage.setItem(KEY, JSON.stringify({ y: win.scrollY, at: Date.now() }));
    } catch { /* nothing to keep */ }
  });
}

if (typeof document !== 'undefined') install(document, window);
