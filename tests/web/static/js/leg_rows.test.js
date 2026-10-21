import { test } from 'node:test';
import assert from 'node:assert/strict';
import { uses, unitFor, groupsOp } from '../../../../src/optionsjourney/web/static/js/leg_rows.js';

test('an offset reads From and Exact, never a max offset', () => {
  assert.deepEqual(uses('offset', '0'), { from: true, exact: true, max: false });
});

test('a premium leg reads a max offset only with a From leg', () => {
  assert.deepEqual(uses('premium', ''), { from: true, exact: false, max: false });
  assert.deepEqual(uses('premium', '1'), { from: true, exact: false, max: true });
});

test('delta and spot read none of them', () => {
  for (const pick of ['delta', 'spot']) {
    assert.deepEqual(uses(pick, '0'), { from: false, exact: false, max: false });
  }
});

test('the unit names what the target is in', () => {
  assert.equal(unitFor('premium', 'SPX'), '$');
  assert.equal(unitFor('delta', 'SPX'), 'Δ');
  assert.equal(unitFor('spot', 'XSP'), 'pts from XSP');
  assert.equal(unitFor('offset', 'SPX'), 'pts from its leg');
});

test('ticking Use leg groups adds a group; unticking merges them', () => {
  assert.equal(groupsOp(true), 'use-groups');
  assert.equal(groupsOp(false), 'one-group');
});
