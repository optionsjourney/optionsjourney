import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeTime, shown } from '../../../../src/optionsjourney/web/static/js/schedule_choice.js';

test('a typed time is read as the server reads it', () => {
  for (const typed of ['945', '0945', '9:45', ' 09:45 ']) assert.equal(normalizeTime(typed), '09:45');
  assert.equal(normalizeTime('1314'), '13:14');
  assert.equal(normalizeTime('130'), '01:30');
  for (const bad of ['', '9', '94500', '9:5', '24:00', '09:60', 'nine', '9:45:15']) {
    assert.equal(normalizeTime(bad), null, bad);
  }
});

test('entry times may carry seconds, and zero seconds are dropped', () => {
  for (const typed of ['9:30:15', '93015', '093015']) assert.equal(normalizeTime(typed, true), '09:30:15');
  assert.equal(normalizeTime('09:30:00', true), '09:30');
  assert.equal(normalizeTime('945', true), '09:45');
  assert.equal(normalizeTime('9:30:60', true), null);
});

test('each part shows by its box or by the frequency', () => {
  const el = dataset => ({ dataset });
  const ticked = new Set(['use_floating_entry']);
  assert.equal(shown(el({ shownBy: 'use_floating_entry' }), ticked, 'daily'), true);
  assert.equal(shown(el({ hiddenBy: 'use_floating_entry' }), ticked, 'daily'), false);
  assert.equal(shown(el({ shownBy: 'use_blackout_days' }), ticked, 'daily'), false);
  assert.equal(shown(el({ frequency: 'weekly' }), ticked, 'weekly'), true);
  assert.equal(shown(el({ frequency: 'monthly' }), ticked, 'weekly'), false);
});
