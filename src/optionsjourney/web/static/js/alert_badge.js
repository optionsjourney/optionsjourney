// The top bar's Alerts badge stays right without a reload (#186): the server
// pushes `alerts_changed {unread}`, and the badge shows the problems that count
// until they're fixed (data-problem-count, #272) plus the unread alerts.

export function badgeText(problemCount, unread) {
  const total = (Number(problemCount) || 0) + (Number(unread) || 0);
  return total > 0 ? String(total) : '';
}

export function install(doc, win) {
  if (typeof win.io !== 'function') return;
  const socket = win.io({ transports: ['websocket', 'polling'] });
  socket.on('alerts_changed', payload => {
    const badge = doc.querySelector('[data-alert-badge]');
    if (!badge) return;
    const text = badgeText(badge.dataset.problemCount, payload && payload.unread);
    badge.textContent = text;
    badge.hidden = text === '';
    if (text) badge.setAttribute('aria-label', `${text} waiting`);
  });
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
