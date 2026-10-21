import { test } from 'node:test';
import assert from 'node:assert/strict';
import { badgeText } from '../../../../src/optionsjourney/web/static/js/alert_badge.js';

test('the badge shows the total of system and unread alerts', () => {
  assert.equal(badgeText(1, 2), '3');
});

test('the badge is empty at zero', () => {
  assert.equal(badgeText(0, 0), '');
});
