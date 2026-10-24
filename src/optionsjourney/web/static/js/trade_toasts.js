// In-app trade alerts (#178; Tracker2's trade_alerts_client.js): when a sync stores a
// fill, the server sends trade_event over Socket.IO (and alert_fired for an alert that
// went off, #275), and every open page shows a popup at
// the top right: newest on top, gone after 20 s (hovering pauses it), × closes it, at
// most 5 at once with "+N more" linking to the Alerts page. One chime per burst. Each
// event is also re-sent as a window 'trade-event' so the Alerts page can add it live.
import { audioReady, playChime, unlockAudio } from './alert_chime.js';

export const SHOW_MS = 20000;
export const MAX_SHOWN = 5;
const CHIME_GAP_MS = 1000;
const KEPT = 'trade-toasts'; // sessionStorage: popups still up, so a reload (the Journal's) keeps them

// The kept popups whose time isn't up: [{e, until}]
export function stillUp(kept, now) {
  return (Array.isArray(kept) ? kept : []).filter(k => k && k.e && k.until > now);
}

function readKept(win) {
  try { return JSON.parse(win.sessionStorage.getItem(KEPT) || '[]'); } catch (e) { return []; }
}

function writeKept(win, kept) {
  try { win.sessionStorage.setItem(KEPT, JSON.stringify(kept)); } catch (e) { /* not kept */ }
}

// What a popup says, from the event the server sent.
export function toastView(e) {
  if (e.kind === 'alert') {
    return { badge: e.label, tone: 'alert', title: e.name, detail: e.detail || '', amount: '', link: e.link, lines: [] };
  }
  return {
    badge: e.label,
    tone: e.kind === 'open' ? 'open' : e.kind === 'roll' ? 'roll' : 'close',
    title: e.name,
    detail: [e.bot_name, e.account].filter(Boolean).join(' · '),
    amount: e.amount,
    link: e.link,
    lines: (e.detail || '').split('\n').filter(Boolean), // a close's strikes and rolls (#212)
  };
}

// An alert from the server, as a popup event
export function alertEvent(a) {
  return { id: `alert:${a.id}`, kind: 'alert', label: 'Alert', name: a.title, detail: a.detail, link: a.link || '/alerts' };
}

export function createStack(doc, win) {
  const seen = new Set();
  let box = null;
  let lastChime = 0;
  let chimeWaiting = false; // a popup came while sound was locked: chime at the next click

  // Sound is locked until the page's first click or key press (each reload locks it again)
  function unlock() {
    unlockAudio();
    win.setTimeout(() => {
      if (!audioReady()) return;
      container().querySelectorAll('.trade-toast__sound').forEach(n => n.remove());
      if (chimeWaiting && container().querySelector('.trade-toast')) playChime();
      chimeWaiting = false;
    }, 50);
  }

  function container() {
    if (box && doc.body.contains(box)) return box;
    box = doc.createElement('div');
    box.className = 'trade-toasts';
    box.setAttribute('role', 'status');
    box.setAttribute('aria-live', 'polite');
    doc.body.append(box);
    return box;
  }

  function more() {
    const cards = [...container().querySelectorAll('.trade-toast')];
    cards.forEach((c, i) => { c.hidden = i >= MAX_SHOWN; });
    let link = container().querySelector('.trade-toasts__more');
    const hidden = cards.length - MAX_SHOWN;
    if (hidden <= 0) { link?.remove(); return; }
    if (!link) {
      link = doc.createElement('a');
      link.className = 'trade-toasts__more link';
      link.href = '/alerts';
      container().append(link);
    }
    link.textContent = `+${hidden} more on the Alerts page`;
  }

  function keep(e, until) {
    writeKept(win, [...stillUp(readKept(win), Date.now()).filter(k => k.e.id !== e.id), ...(until ? [{ e, until }] : [])]);
  }

  // `left`: ms still to show (a popup kept over a reload); `quiet`: no chime
  function show(e, left = SHOW_MS, quiet = false) {
    if (!e || seen.has(e.id)) return false;
    seen.add(e.id);
    const v = toastView(e);
    const card = doc.createElement('div');
    card.className = `trade-toast trade-toast--${v.tone}`;
    const head = doc.createElement('div');
    head.className = 'trade-toast__head';
    const badge = doc.createElement('span');
    badge.className = 'trade-toast__badge';
    badge.textContent = v.badge;
    const close = doc.createElement('button');
    close.type = 'button';
    close.className = 'trade-toast__close';
    close.setAttribute('aria-label', 'Close');
    close.textContent = '×';
    head.append(badge, close);
    const title = doc.createElement('a');
    title.className = 'trade-toast__title link';
    title.href = v.link;
    title.textContent = v.title;
    const detail = doc.createElement('div');
    detail.className = 'muted small';
    detail.textContent = [v.detail, v.amount].filter(Boolean).join(' · ');
    card.append(head, title, detail);
    v.lines.forEach(text => {
      const line = doc.createElement('div');
      line.className = 'muted small';
      line.textContent = text;
      card.append(line);
    });
    container().prepend(card);

    let started = Date.now(), timer = null;
    const remove = () => { win.clearTimeout(timer); card.remove(); more(); keep(e, 0); };
    const run = () => { started = Date.now(); keep(e, started + left); timer = win.setTimeout(remove, left); };
    card.addEventListener('mouseenter', () => { win.clearTimeout(timer); left -= Date.now() - started; keep(e, Date.now() + SHOW_MS); });
    card.addEventListener('mouseleave', run);
    close.addEventListener('click', remove);
    run();
    more();

    const now = Date.now();
    if (!audioReady()) {
      chimeWaiting = true;
      const hint = doc.createElement('div');
      hint.className = 'trade-toast__sound small';
      hint.textContent = 'Click anywhere for sound';
      card.append(hint);
    } else if (!quiet && now - lastChime > CHIME_GAP_MS) { lastChime = now; playChime(); }
    return true;
  }

  // Popups that were up when the page reloaded, for the rest of their time
  function restore() {
    const now = Date.now();
    stillUp(readKept(win), now).sort((a, b) => a.until - b.until).forEach(k => show(k.e, k.until - now, true));
  }

  return { show, restore, unlock };
}

export function install(doc, win) {
  if (typeof win.io !== 'function') return;
  const stack = createStack(doc, win);
  ['pointerdown', 'keydown', 'touchstart'].forEach(type => doc.addEventListener(type, stack.unlock));
  stack.restore();
  const socket = win.io({ transports: ['websocket', 'polling'] });
  // #275: an alert that went off (a price, trade or bot alert, a P/L correction) pops up too
  socket.on('alert_fired', a => {
    if (a && a.id != null) stack.show(alertEvent(a));
  });
  socket.on('trade_event', e => {
    win.dispatchEvent(new win.CustomEvent('trade-event', { detail: e }));
    if (e && e.popup) stack.show(e);
  });
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
