import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renamed } from '../../../../src/optionsjourney/web/static/js/alert_rows.js';

test('a copied alert row takes the next number', () => {
  assert.equal(renamed('da-0-type', 1), 'da-1-type');
  assert.equal(renamed('da-12-points', 13), 'da-13-points');
});
