import { test } from 'node:test';
import assert from 'node:assert/strict';
import { addTag, mergeTags, parseTags } from '../../../../src/optionsjourney/web/static/js/tag_picker.js';

const RESERVED = ['Armed', 'Off', 'Live', 'Preview'];

test('the comma box reads as the server reads it', () => {
  assert.deepEqual(parseTags(' mornings ,0DTE,, Mornings,  big   ones '), ['0DTE', 'big ones', 'mornings']);
  assert.deepEqual(parseTags(''), []);
});

test('tag lists join without duplicates, in order', () => {
  assert.deepEqual(mergeTags(['Wheel', 'b'], ['WHEEL', 'A']), ['A', 'b', 'Wheel']);
});

test('a typed tag takes the spelling your bots already use', () => {
  assert.deepEqual(addTag(['0DTE'], ['Mornings'], ' mornings ', RESERVED, 20), { tags: ['0DTE', 'Mornings'] });
  assert.deepEqual(addTag(['0DTE'], [], 'Fridays', RESERVED, 20), { tags: ['0DTE', 'Fridays'] });
});

test('a tag the bot has already changes nothing', () => {
  assert.deepEqual(addTag(['0DTE'], ['0DTE'], '0dte', RESERVED, 1), { tags: ['0DTE'] });
});

test('blank, comma, state words and a 21st tag are refused', () => {
  assert.match(addTag([], [], '  ', RESERVED, 20).error, /Type a tag/);
  assert.match(addTag([], [], 'a, b', RESERVED, 20).error, /no commas/);
  assert.match(addTag([], [], 'live', RESERVED, 20).error, /list's own filters/);
  assert.match(addTag(['a', 'b'], [], 'c', RESERVED, 2).error, /up to 2 tags/);
});
