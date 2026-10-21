import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chipViews } from '../../../../src/optionsjourney/web/static/js/bot_chips.js';

test('chips keep the journal classes and ring only the new ones', () => {
  const views = chipViews([
    { label: 'Target 25%', title: 'changed from 50%', warning: false },
    { label: 'Exits off', title: 'no stop, no target', warning: true },
  ], ['Target 25%']);
  assert.deepEqual(views, [
    { label: 'Target 25%', title: 'changed from 50%', className: 'chip chip--changed', isNew: false },
    { label: 'Exits off', title: 'no stop, no target', className: 'chip chip--off', isNew: true },
  ]);
});

test('no chips clears the cell', () => {
  assert.deepEqual(chipViews([], ['Target 25%']), []);
});
