import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fieldsShow, showsFor } from '../../../../src/optionsjourney/web/static/js/filter_fields.js';

test("a setting's boxes show only while it is on", () => {
  assert.equal(fieldsShow({ on: true }), true);
  assert.equal(fieldsShow({ on: false }), false);
  assert.equal(fieldsShow({}), false);
});

test("a setting with a problem keeps its boxes shown, even off", () => {
  assert.equal(fieldsShow({ on: false, problem: true }), true);
});

test("an average's second box shows only for compare", () => {
  assert.equal(showsFor('compare', 'compare'), true);
  for (const mode of ['', 'above', 'below']) assert.equal(showsFor('compare', mode), false);
});

test("a box shows for any of the choices it names", () => {
  assert.equal(showsFor('pnl_pct', 'pnl_pct'), true);
  assert.equal(showsFor('pnl_pct', 'strike_offset'), false);
  assert.equal(showsFor('a b', 'b'), true);
  assert.equal(showsFor(undefined, ''), false);
});
