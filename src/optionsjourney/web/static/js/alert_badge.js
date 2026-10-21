// The top bar's Alerts badge stays right without a reload (#186): the server
// pushes `alerts_changed {unread}`, and the badge shows the always-there
// system alerts (data-system-count) plus the unread ones.

export function badgeText(systemCount, unread) {
  const total = (Number(systemCount) || 0) + (Number(unread) || 0);
  return total > 0 ? String(total) : '';
}

export function install(doc, win) {
  if (typeof win.io !== 'function') return;
  const socket = win.io({ transports: ['websocket', 'polling'] });
  socket.on('alerts_changed', payload => {
    const badge = doc.querySelector('[data-alert-badge]');
    if (!badge) return;
    const text = badgeText(badge.dataset.systemCount, payload && payload.unread);
    badge.textContent = text;
    badge.hidden = text === '';
    if (text) badge.setAttribute('aria-label', `${text} waiting`);
  });
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
