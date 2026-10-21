// Stop streaming while the page can't be seen. Once the page has been hidden (another browser
// tab, a minimised window) for graceMs, onSuspend runs: the grid tears its panes down, so the
// CandleRouter unsubscribes and the server stops sending bars. Showing the page again runs
// onResume, which rebuilds them from a fresh snapshot. A quick look at another tab costs nothing.
export const HIDDEN_GRACE_MS = 30000;

export function suspendWhenHidden({ onSuspend, onResume, doc = globalThis.document, graceMs = HIDDEN_GRACE_MS,
                                    setTimer = (f, ms) => setTimeout(f, ms), clearTimer = id => clearTimeout(id) }) {
  if (!doc?.addEventListener) return () => {};
  let timer = null;
  let suspended = false;
  const onChange = () => {
    if (doc.visibilityState === 'hidden') {
      if (timer == null && !suspended) {
        timer = setTimer(() => { timer = null; suspended = true; onSuspend(); }, graceMs);
      }
      return;
    }
    if (timer != null) { clearTimer(timer); timer = null; }
    if (suspended) { suspended = false; onResume(); }
  };
  doc.addEventListener('visibilitychange', onChange);
  return () => {
    doc.removeEventListener('visibilitychange', onChange);
    if (timer != null) clearTimer(timer);
  };
}
