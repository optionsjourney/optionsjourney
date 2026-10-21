// One chart-feed subscription per (symbol, interval) for the whole page, however many charts
// show it (services/chart_feed.py over Socket.IO: subscribe_candles -> chart_candles_snapshot,
// then chart_candle_update per change). The server sends a snapshot only when a socket
// subscribes, so the router keeps the latest bars per key and hands them to a chart that joins
// later. One subscribe per key also keeps subscription churn down; churn makes DXLink drop
// the stream. When a key's last chart goes, the unsubscribe waits LINGER_MS: a chart that
// comes back meanwhile (a layout switch destroys every pane and builds the new ones) reuses the
// subscription and its cached bars, and no unsubscribe/subscribe pair races on the server.
import { mergeBar } from './bars.js';

export const LINGER_MS = 5000;

const keyOf = (symbol, interval) => `${symbol}|${interval}`;

function safe(fn) {
  try { fn(); } catch (e) { console.error('CandleRouter listener failed', e); }
}

export class CandleRouter {
  constructor(socket, { lingerMs = LINGER_MS, setTimer = (f, ms) => setTimeout(f, ms),
                        clearTimer = id => clearTimeout(id) } = {}) {
    this.socket = socket || null;
    this._subs = new Map();   // key -> { symbol, interval, listeners: Map<id, {onSnapshot, onBar}>, bars, linger }
    this._nextId = 1;
    this._lingerMs = lingerMs;
    this._setTimer = setTimer;
    this._clearTimer = clearTimer;
    if (this.socket) {
      this.socket.on('chart_candles_snapshot', p => this._onSnapshot(p));
      this.socket.on('chart_candle_update', p => this._onUpdate(p));
      // The server forgets a socket's subscriptions when it disconnects.
      this.socket.on('connect', () => this._resubscribe());
    }
  }

  // onSnapshot(bars): the whole history, at once when the page already has it.
  // onBar(bar): each change to a bar after that. Returns unsubscribe().
  subscribe(symbol, interval, { onSnapshot, onBar } = {}) {
    const key = keyOf(symbol, interval);
    let sub = this._subs.get(key);
    if (!sub) {
      sub = { symbol, interval, listeners: new Map(), bars: null, linger: null };
      this._subs.set(key, sub);
      this.socket?.emit('subscribe_candles', { symbol, interval });
    } else if (sub.linger != null) {
      this._clearTimer(sub.linger);
      sub.linger = null;
    }
    const id = this._nextId++;
    sub.listeners.set(id, { onSnapshot, onBar });
    if (sub.bars && onSnapshot) safe(() => onSnapshot(sub.bars));
    let done = false;
    return () => {
      if (done) return;
      done = true;
      sub.listeners.delete(id);
      if (sub.listeners.size === 0 && this._subs.get(key) === sub && sub.linger == null) {
        sub.linger = this._setTimer(() => {
          sub.linger = null;
          if (sub.listeners.size || this._subs.get(key) !== sub) return;
          this._subs.delete(key);
          this.socket?.emit('unsubscribe_candles', { symbol, interval });
        }, this._lingerMs);
      }
    };
  }

  bars(symbol, interval) {
    return this._subs.get(keyOf(symbol, interval))?.bars || null;
  }

  _resubscribe() {
    for (const [key, sub] of [...this._subs]) {
      if (sub.linger != null) {
        // No chart wants it, and the server forgot it with the old connection: drop it.
        this._clearTimer(sub.linger);
        this._subs.delete(key);
        continue;
      }
      this.socket.emit('subscribe_candles', { symbol: sub.symbol, interval: sub.interval });
    }
  }

  _onSnapshot(p) {
    const sub = p && this._subs.get(keyOf(p.symbol, p.interval));
    if (!sub) return;
    sub.bars = Array.isArray(p.bars) ? p.bars : [];
    for (const l of [...sub.listeners.values()]) if (l.onSnapshot) safe(() => l.onSnapshot(sub.bars));
  }

  _onUpdate(p) {
    const sub = p && p.bar && this._subs.get(keyOf(p.symbol, p.interval));
    if (!sub || !sub.bars) return;      // wait for the snapshot
    sub.bars = mergeBar(sub.bars, p.bar);
    for (const l of [...sub.listeners.values()]) if (l.onBar) safe(() => l.onBar(p.bar));
  }
}
