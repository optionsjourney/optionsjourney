import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shows, groupLabel, coHint } from '../../../../src/optionsjourney/web/static/js/group_exits.js';

test('the limit price shows only when the other legs are sold at a limit', () => {
  assert.equal(shows('limit', { remainder: 'close_limit' }), true);
  for (const remainder of ['close_market', 'close_chase', 'leave']) {
    assert.equal(shows('limit', { remainder }), false);
  }
});

test('also closing another group shows only once the group has a stop', () => {
  assert.equal(shows('pair', { stop: '95' }), true);
  assert.equal(shows('pair', { stop: '  ' }), false);
  assert.equal(shows('pair', {}), false);
});

test('profit actions show only while ticked', () => {
  assert.equal(shows('profit', { useProfit: true }), true);
  assert.equal(shows('profit', { useProfit: false }), false);
});

test('the closing order amount shows unless it is off', () => {
  assert.equal(shows('co-amount', { coMode: 'off' }), false);
  assert.equal(shows('co-amount', { coMode: 'both' }), true);
  assert.equal(shows('co-amount', { coMode: 'tighter' }), true);
});

test('an unknown part always shows', () => {
  assert.equal(shows('anything', {}), true);
});

test('a group is called what was typed, or Group N when blank', () => {
  assert.equal(groupLabel('Call foo', '0'), 'Call foo');
  assert.equal(groupLabel('  ', '1'), 'Group 2');
  assert.equal(groupLabel(undefined, 0), 'Group 1');
});

test('the remainder shows only on a group another group closes', () => {
  assert.equal(shows('remainder', { pairedTo: true }), true);
  assert.equal(shows('remainder', { pairedTo: false }), false);
  assert.equal(shows('remainder', {}), false);
});

test('a % stop shows its basis, and the closing order its worked example', () => {
  assert.equal(shows('basis', { stopUnit: 'pct' }), true);
  assert.equal(shows('basis', { stopUnit: 'usd' }), false);
  assert.equal(shows('co-hint', { stopUnit: 'pct', basis: 'closing_order' }), true);
  assert.equal(shows('co-hint', { stopUnit: 'pct', basis: 'leg_premium' }), false);
  assert.equal(shows('co-hint', { stopUnit: 'usd', basis: 'closing_order' }), false);
  assert.match(coHint('0.20'), /stop at \$4\.80\.$/);
  assert.match(coHint('0.35'), /stop at \$4\.65\.$/);
  assert.match(coHint(''), /\$4\.80 with 0\.20/);
});

test('the resting-stop box and the watched note show once there is a stop', () => {
  for (const part of ['rest-stop', 'watched-note']) {
    assert.equal(shows(part, { stop: '95' }), true, part);
    assert.equal(shows(part, { stop: '' }), false, part);
  }
});
