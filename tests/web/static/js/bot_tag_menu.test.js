import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stateOf, changes } from '../../../../src/optionsjourney/web/static/js/bot_tag_menu.js';

test('a tag is on all, some or none of the ticked bots, ignoring case', () => {
  const bots = [['0DTE', 'FOMC'], ['0dte']];
  assert.equal(stateOf('0DTE', bots), 'all');
  assert.equal(stateOf('FOMC', bots), 'some');
  assert.equal(stateOf('Wings', bots), 'none');
});

test('only rows moved from where they started are sent', () => {
  assert.deepEqual(changes([
    { tag: '0DTE', start: 'all', now: 'all' },
    { tag: 'FOMC', start: 'some', now: 'some' },
    { tag: 'Wings', start: 'none', now: 'all' },
    { tag: 'MEIC', start: 'some', now: 'none' },
    { tag: 'Beta', start: 'some', now: 'all' },
  ]), { add: ['Wings', 'Beta'], remove: ['MEIC'] });
});
