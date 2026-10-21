// Manual bots (#158): the ticket's Manage with select, under Send (Place only by default).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as C from '../../../../../src/optionsjourney/web/static/js/trade/ticket_core.js';

const PAGE = new URL('../../../../../src/optionsjourney/web/static/js/trade/trade_page.js', import.meta.url);

test('manageOptions: Place only first, then the manual bots', () => {
  const opts = C.manageOptions([{ id: 3, name: 'Half off' }, { id: 5, name: 'Tight stop' }]);
  assert.deepEqual(opts, [
    { value: '', label: 'Place only' },
    { value: '3', label: 'Half off' },
    { value: '5', label: 'Tight stop' },
  ]);
  assert.deepEqual(C.manageOptions(null), [{ value: '', label: 'Place only' }]);
});

test('confirmRequest sends manual_bot_id only when one is chosen', () => {
  assert.deepEqual(C.confirmRequest(7, 'abc', null), { account_id: 7, code: 'abc' });
  assert.deepEqual(C.confirmRequest(7, 'abc', ''), { account_id: 7, code: 'abc' });
  assert.deepEqual(C.confirmRequest(7, 'abc', '3'), { account_id: 7, code: 'abc', manual_bot_id: 3 });
});

test('the page keeps "Or manage it with a bot" and adds Manage with under Send', () => {
  const src = readFileSync(PAGE, 'utf8');
  assert.match(src, /Or manage it with a bot/);
  assert.match(src, /data-act="manage-with"/);
  assert.match(src, /\/api\/trade\/manual-bots/);
});
