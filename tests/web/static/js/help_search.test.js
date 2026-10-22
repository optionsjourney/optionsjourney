import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nextStep, searchUrl } from '../../../../src/optionsjourney/web/static/js/help_search.js';

test('the search address carries the words', () => {
  assert.equal(searchUrl('/help/search', 'stop loss'), '/help/search?q=stop%20loss');
  assert.equal(searchUrl('/help/search', 'P/L & ≈'), '/help/search?q=P%2FL%20%26%20%E2%89%88');
});

test('it searches from two letters, once per change', () => {
  assert.equal(nextStep('m', '', true), 'wait');
  assert.equal(nextStep('me', '', true), 'search');
  assert.equal(nextStep('menu ', 'menu', false), 'wait');  // same words as shown
  assert.equal(nextStep('menu', 'holidays', false), 'search');
});

test('clearing the box goes back to what you were reading', () => {
  assert.equal(nextStep('', 'menu', true), 'restore');   // a Help page
  assert.equal(nextStep('  ', 'menu', false), 'search');  // the search page: its hint
  assert.equal(nextStep('', '', true), 'wait');
});
