// Chart drawings in Postgres (/api/chart_drawings), one list per symbol + interval, shared by
// every chart on any page: Multi-Chart Grid panes and their pop-outs. After a save
// the server emits chart_drawings_changed to the drawings:<symbol>:<interval> room; watch()
// joins it (once per key per page) and calls back so open charts reload. The echo of this
// page's own save is skipped while that save is in flight; instead, once a save succeeds and
// no other save of that key is in flight, the store calls back every watcher of the key on this
// page itself (two panes on SPY 5m stay the same). Leaving a room waits LINGER_MS, so a chart
// rebuilt at once keeps the room. A failed save is retried after RETRY_MS (1, 2, 5, 10, 30 s,
// then every 30 s) with the key's latest list; a newer save replaces the queued one.
import { cleanDrawings } from './chart_drawings.js';

export const LINGER_MS = 5000;
export const RETRY_MS = [1000, 2000, 5000, 10000, 30000];

const keyOf = (symbol, interval) => `${symbol}|${interval}`;

export class DrawingStore {
  constructor({ fetchImpl = (...a) => fetch(...a), socket = null, lingerMs = LINGER_MS,
                setTimer = (f, ms) => setTimeout(f, ms), clearTimer = id => clearTimeout(id) } = {}) {
    this._fetch = fetchImpl;
    this._socket = socket;
    this._lingerMs = lingerMs;
    this._setTimer = setTimer;
    this._clearTimer = clearTimer;
    this._watchers = new Map();   // key -> {symbol, interval, cbs: Set, linger}
    this._saving = new Map();     // key -> saves in flight
    this._seq = new Map();        // key -> number of the latest save()
    this._retry = new Map();      // key -> {symbol, interval, drawings, seq, attempt, timer}
    if (socket) {
      socket.on('chart_drawings_changed', p => this._changed(p));
      socket.on('connect', () => {
        for (const [key, w] of [...this._watchers]) {
          if (w.linger != null) { this._clearTimer(w.linger); this._watchers.delete(key); continue; }
          socket.emit('watch_drawings', { symbol: w.symbol, interval: w.interval });
        }
      });
    }
  }

  async load(symbol, interval) {
    try {
      const r = await this._fetch(`/api/chart_drawings?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}`);
      if (!r.ok) return [];
      const d = await r.json();
      return cleanDrawings(d && d.drawings);
    } catch (e) {
      return [];
    }
  }

  // Resolves to whether this attempt reached the server; a failed one is retried in the background.
  async save(symbol, interval, drawings) {
    const key = keyOf(symbol, interval);
    const seq = (this._seq.get(key) || 0) + 1;
    this._seq.set(key, seq);
    const entry = this._retry.get(key);
    if (entry?.timer != null) { this._clearTimer(entry.timer); entry.timer = null; }   // superseded
    return this._send(symbol, interval, drawings, seq);
  }

  async _send(symbol, interval, drawings, seq, { keepalive = false } = {}) {
    const key = keyOf(symbol, interval);
    this._saving.set(key, (this._saving.get(key) || 0) + 1);
    let ok = false;
    try {
      const r = await this._fetch('/api/chart_drawings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, keepalive,
        body: JSON.stringify({ symbol, interval, drawings }),
      });
      if (!r.ok) console.warn('drawings save failed', r.status);
      ok = r.ok;
    } catch (e) {
      console.warn('drawings save failed', e);
    } finally {
      const n = this._saving.get(key) - 1;
      if (n > 0) this._saving.set(key, n); else this._saving.delete(key);
    }
    const latest = seq === this._seq.get(key);   // else a newer save owns the outcome
    if (ok) {
      if (latest) this._dropRetry(key);
      if (!this._saving.get(key)) this._notify(key);
    } else if (latest) {
      this._queueRetry(key, symbol, interval, drawings, seq);
    }
    return ok;
  }

  _dropRetry(key) {
    const entry = this._retry.get(key);
    if (entry?.timer != null) this._clearTimer(entry.timer);
    this._retry.delete(key);
  }

  _queueRetry(key, symbol, interval, drawings, seq) {
    let entry = this._retry.get(key);
    if (!entry) { entry = { attempt: 0, timer: null }; this._retry.set(key, entry); }
    Object.assign(entry, { symbol, interval, drawings, seq });
    if (entry.timer != null) return;
    const ms = RETRY_MS[Math.min(entry.attempt, RETRY_MS.length - 1)];
    entry.attempt++;
    entry.timer = this._setTimer(() => {
      entry.timer = null;
      if (entry.seq === this._seq.get(key)) this._send(entry.symbol, entry.interval, entry.drawings, entry.seq);
    }, ms);
  }

  // Send queued retries now (page hide).
  async flush({ keepalive = false } = {}) {
    const due = [...this._retry.values()].filter(e => e.timer != null);
    due.forEach(e => { this._clearTimer(e.timer); e.timer = null; });
    await Promise.all(due.map(e => this._send(e.symbol, e.interval, e.drawings, e.seq, { keepalive })));
  }

  watch(symbol, interval, cb) {
    const key = keyOf(symbol, interval);
    let w = this._watchers.get(key);
    if (!w) {
      w = { symbol, interval, cbs: new Set(), linger: null };
      this._watchers.set(key, w);
      this._socket?.emit('watch_drawings', { symbol, interval });
    } else if (w.linger != null) {
      this._clearTimer(w.linger);
      w.linger = null;
    }
    w.cbs.add(cb);
    let done = false;
    return () => {
      if (done) return;
      done = true;
      w.cbs.delete(cb);
      if (!w.cbs.size && this._watchers.get(key) === w && w.linger == null) {
        w.linger = this._setTimer(() => {
          w.linger = null;
          if (w.cbs.size || this._watchers.get(key) !== w) return;
          this._watchers.delete(key);
          this._socket?.emit('unwatch_drawings', { symbol, interval });
        }, this._lingerMs);
      }
    };
  }

  _changed(p) {
    const key = p && keyOf(p.symbol, p.interval);
    if (!key || this._saving.get(key)) return;
    this._notify(key);
  }

  _notify(key) {
    const w = this._watchers.get(key);
    if (!w) return;
    for (const cb of [...w.cbs]) {
      try { cb(); } catch (e) { console.error('drawings watcher failed', e); }
    }
  }
}

let shared = null;
// One store per page, on the page's default Socket.IO connection when socket.io is loaded.
export function getDrawingStore() {
  if (!shared) {
    let socket = null;
    try {
      if (typeof io !== 'undefined') socket = io({ transports: ['websocket', 'polling'] });
    } catch (e) { socket = null; }
    shared = new DrawingStore({ socket });
    try { window.addEventListener('pagehide', () => shared.flush({ keepalive: true })); } catch (e) { /* no window */ }
  }
  return shared;
}

// The Analyzer used to keep drawings in localStorage (analyzer_drawings_<symbol>_<frame>).
// Upload each key once, unless the server already has drawings for it, then remove the key.
// Returns how many keys were uploaded.
const LEGACY_RE = /^analyzer_drawings_(.+)_(m5|daily)$/;
const LEGACY_INTERVAL = { m5: '5m', daily: '1d' };

export async function importLegacyDrawings(store, storage) {
  if (!storage) return 0;
  const keys = [];
  try {
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i);
      if (k && LEGACY_RE.test(k)) keys.push(k);
    }
  } catch (e) { return 0; }
  let uploaded = 0;
  for (const k of keys) {
    const [, symbol, frame] = k.match(LEGACY_RE);
    const interval = LEGACY_INTERVAL[frame];
    let local = [];
    try { local = cleanDrawings(JSON.parse(storage.getItem(k))); } catch (e) { local = []; }
    const existing = await store.load(symbol, interval);
    let done = true;
    if (local.length && !existing.length) {
      done = await store.save(symbol, interval, local);
      if (done) uploaded++;
    }
    if (done) { try { storage.removeItem(k); } catch (e) { /* private mode */ } }
  }
  return uploaded;
}
