import { test } from 'node:test';
import assert from 'node:assert/strict';
import { busy } from '../../../../src/optionsjourney/web/static/js/journal_live.js';

// A stand-in document: just what busy() asks of it
function doc({ ticked = false, drawers = [], menu = false, focused = 'BODY' } = {}) {
  return {
    querySelector: sel => (sel === 'input.select-trade:checked' ? (ticked || null)
      : sel.includes('[open]') ? (menu || null) : null),
    querySelectorAll: () => drawers.map(open => ({ classList: { contains: c => c === 'hidden' && !open } })),
    activeElement: { tagName: focused },
  };
}

test('an idle page may reload', () => {
  assert.equal(busy(doc({ drawers: [false, false] })), '');
});

test('ticked trades, an open drawer, an open menu or typing wait for a reload link', () => {
  assert.equal(busy(doc({ ticked: {} })), 'ticked');
  assert.equal(busy(doc({ drawers: [false, true] })), 'drawer');
  assert.equal(busy(doc({ menu: {} })), 'menu');
  assert.equal(busy(doc({ focused: 'INPUT' })), 'typing');
});

test('other ticked boxes (Settle expirations, tag filters) never hold a reload', () => {
  const d = doc();
  const real = d.querySelector;
  d.querySelector = sel => (sel.includes(':checked') && sel !== 'input.select-trade:checked' ? {} : real(sel));
  assert.equal(busy(d), '');
});

test('a page reloads on journal_changed unless it names its own event', async () => {
  const { liveEvent } = await import('../../../../src/optionsjourney/web/static/js/journal_live.js');
  assert.equal(liveEvent({ querySelector: () => null }), 'journal_changed');
  const paper = { dataset: { liveEvent: 'paper_journal_changed' } };
  assert.equal(liveEvent({ querySelector: sel => (sel === '[data-live-event]' ? paper : null) }),
    'paper_journal_changed');
});
