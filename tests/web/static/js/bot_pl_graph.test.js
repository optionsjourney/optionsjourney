import { test } from 'node:test';
import assert from 'node:assert/strict';
import { KEY, install, remember, wasOpen } from '../../../../src/optionsjourney/web/static/js/bot_pl_graph.js';

function memory() {
  const values = new Map();
  return {
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

const broken = {
  getItem() { throw new Error('blocked'); },
  setItem() { throw new Error('blocked'); },
  removeItem() { throw new Error('blocked'); },
};

function fold() {
  const listeners = {};
  return {
    open: false,
    addEventListener: (name, fn) => { listeners[name] = fn; },
    toggle(open) { this.open = open; listeners.toggle?.(); },
  };
}

test('the graph starts folded and remembers being left open', () => {
  const storage = memory();
  assert.equal(wasOpen(storage), false);
  remember(storage, true);
  assert.equal(storage.getItem(KEY), '1');
  assert.equal(wasOpen(storage), true);
  remember(storage, false);
  assert.equal(wasOpen(storage), false);
});

test('storage that throws never breaks the page', () => {
  assert.equal(wasOpen(broken), false);
  remember(broken, true);  // no throw
});

test('install opens the fold when it was left open, and saves each toggle', () => {
  const storage = memory();
  const box = fold();
  const doc = { getElementById: (id) => (id === 'pl-graph' ? box : null) };
  install(doc, { localStorage: storage });
  assert.equal(box.open, false);
  box.toggle(true);
  assert.equal(wasOpen(storage), true);

  const again = fold();
  install({ getElementById: () => again }, { localStorage: storage });
  assert.equal(again.open, true);

  const blocked = fold();
  const win = { get localStorage() { throw new Error('blocked'); } };
  install({ getElementById: () => blocked }, win);
  blocked.toggle(true);  // no throw
  assert.equal(blocked.open, true);
  install({ getElementById: () => null }, win);  // a page without the graph
});
