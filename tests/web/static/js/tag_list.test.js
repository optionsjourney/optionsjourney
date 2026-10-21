import { test } from 'node:test';
import assert from 'node:assert/strict';
import { labelFor, parseTags } from '../../../../src/optionsjourney/web/static/js/tag_list.js';

test('no tags ticked shows the box name', () => {
  assert.equal(labelFor([], 'Tags'), 'Tags');
});

test('one or two ticked tags are named, more are counted', () => {
  assert.equal(labelFor(['0DTE', 'Wings'], 'Tags'), '0DTE, Wings');
  assert.equal(labelFor(['0DTE', 'Wings', 'Beta'], 'Tags'), '3 tags');
});

test('the comma box reads as names, blanks dropped', () => {
  assert.deepEqual(parseTags(' 0DTE, Wings ,, '), ['0DTE', 'Wings']);
  assert.deepEqual(parseTags(''), []);
});
