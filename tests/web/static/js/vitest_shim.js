// Just enough of vitest's API for Tracker2's copied tests to run on Node's built-in runner,
// with no npm packages: describe/it, the expect matchers those tests use, and vi.spyOn.
import { describe, it, mock } from 'node:test';
import assert from 'node:assert/strict';

export { describe, it };

function matchers(actual, negate) {
  const check = (ok, message) => assert.ok(negate ? !ok : ok, (negate ? 'not: ' : '') + message);
  return {
    toBe: e => check(Object.is(actual, e), `expected ${actual} to be ${e}`),
    toEqual: e => {
      let same = true;
      try { assert.deepStrictEqual(actual, e); } catch { same = false; }
      check(same, `expected ${JSON.stringify(actual)} to equal ${JSON.stringify(e)}`);
    },
    toBeCloseTo: (e, digits = 2) =>
      check(Math.abs(actual - e) < 10 ** -digits / 2, `expected ${actual} to be close to ${e}`),
    toBeNull: () => check(actual === null, `expected ${actual} to be null`),
    toBeDefined: () => check(actual !== undefined, 'expected a value'),
    toBeGreaterThan: e => check(actual > e, `expected ${actual} > ${e}`),
    toBeGreaterThanOrEqual: e => check(actual >= e, `expected ${actual} >= ${e}`),
    toBeLessThan: e => check(actual < e, `expected ${actual} < ${e}`),
    toBeLessThanOrEqual: e => check(actual <= e, `expected ${actual} <= ${e}`),
    toContain: e => check(actual.includes(e), `expected ${actual} to contain ${e}`),
  };
}

export function expect(actual) {
  return { ...matchers(actual, false), not: matchers(actual, true) };
}

export const vi = {
  spyOn(obj, name) {
    const m = mock.method(obj, name);
    return { mock: { get calls() { return m.mock.calls; } }, mockRestore: () => m.mock.restore() };
  },
};
