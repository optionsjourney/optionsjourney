import { test } from 'node:test';
import assert from 'node:assert/strict';
import { searchUrl } from '../../../../src/optionsjourney/web/static/js/transactions_search.js';

test('the search keeps the other filters and starts at page 1', () => {
  const params = [['q', ' spx put '], ['account', 'tastytrade:5WX01234'], ['days', '30'], ['page', '3']];
  assert.equal(searchUrl('/transactions', params),
    '/transactions?q=spx+put&account=tastytrade%3A5WX01234&days=30');
});

test('an empty search leaves q out', () => {
  assert.equal(searchUrl('/transactions', [['q', '  '], ['view', 'fills']]), '/transactions?view=fills');
  assert.equal(searchUrl('/transactions', [['q', '']]), '/transactions');
});
