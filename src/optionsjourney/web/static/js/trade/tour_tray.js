// The demo tour's pre-filled put spread: pure helpers, so node:test covers them.
// The server puts the legs in data-tour-tray on #trade-ticket (demo mode, tour step 4 only).
import * as T from './chain_tray.js';

// The attribute's object, or null when it is missing, not JSON, or has no cells.
export function tourTray(el) {
  const raw = el && el.dataset && el.dataset.tourTray;
  if (!raw) return null;
  try {
    const v = JSON.parse(raw);
    return v && Array.isArray(v.cells) ? v : null;
  } catch { return null; }
}

// The cells toggled into an empty tray, as the chain would build it.
export function tourTrayFor(tour) {
  return tour.cells.reduce((tray, cell) => T.toggleCell(tray, cell), T.emptyTray());
}
