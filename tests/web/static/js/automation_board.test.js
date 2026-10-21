import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  groupOf, minutes, nextText, stateOf, tapeHtml, todayText,
} from '../../../../src/optionsjourney/web/static/js/automation_board.js';

const ROW = { armed: true, weekly: true, days: [2], strategy: '0DTE' };

test('rows group by strategy, with the ones not armed last', () => {
  assert.deepEqual(groupOf(ROW, 'strategy', 2), { key: 'a0dte', label: '0DTE' });
  assert.deepEqual(groupOf({ ...ROW, armed: false }, 'strategy', 2), { key: 'zz', label: 'Off', muted: true });
});

test('or by the weekday they fire, today marked', () => {
  assert.equal(groupOf(ROW, 'day', 2).label, 'Tuesday · today');
  assert.equal(groupOf(ROW, 'day', 3).label, 'Tuesday');
  assert.equal(groupOf({ ...ROW, days: [1, 2, 3, 4, 5] }, 'day', 2).label, 'Every weekday');
  assert.equal(groupOf({ ...ROW, weekly: false, days: [] }, 'day', 2).label, 'Specific dates & monthly');
  const keys = [groupOf({ ...ROW, days: [1, 2, 3, 4, 5] }, 'day').key, groupOf(ROW, 'day').key,
    groupOf({ ...ROW, weekly: false }, 'day').key, groupOf({ ...ROW, armed: false }, 'day').key];
  assert.deepEqual([...keys].sort(), keys);  // every weekday, Tuesday, dated, off
});

test('the state that most needs a look stands for a dot', () => {
  assert.equal(stateOf([{ state: 'done' }, { state: 'missed' }, { state: 'pending' }]), 'missed');
  assert.equal(minutes('09:47'), 587);
  assert.equal(minutes('10:00–11:00'), 600);
  assert.equal(minutes(''), null);
});

test('the command bar says what is filled, to come and needs a look', () => {
  assert.match(todayText({ done: 2, pending: 1, watching: 1 }), /^2 filled <small>· 2 to come<\/small>$/);
  assert.match(todayText({ unfilled: 1, missed: 1 }), /2 need a look/);
});

test('next entry: the first one still to come, and how long until it', () => {
  const rows = [
    { time: '09:47', state: 'done', bot_name: 'A' },
    { time: '13:05', state: 'pending', bot_name: 'Late <b>' },
    { time: '10:30', state: 'pending', bot_name: 'Soon' },
  ];
  assert.match(nextText(rows, minutes('10:00'), false), /10:30.*Soon.*30m/);
  assert.match(nextText(rows, minutes('11:00'), false), /Late &lt;b&gt;.*2h 5m/);
  assert.equal(nextText(rows, minutes('14:00'), false), 'Done for today');
  assert.equal(nextText([], 600, false), 'Nothing today');
  assert.match(nextText(rows, 600, true), /Entries paused/);
});

test('the time axis has a lane per strategy, a dot per row and time, and the now line', () => {
  const entry = { account: 'Beta ••0001', size: '1 contract', state: 'done', note: null };
  const html = tapeHtml([
    { lane: 'Calendars', rowKey: '4', name: 'Cal', time: '13:00', entries: [entry] },
    { lane: '0DTE', rowKey: '1-2', name: 'MEIC <i>', time: '09:47', entries: [entry, { ...entry, state: 'pending' }] },
  ], minutes('10:00'), '10:00');
  assert.ok(html.indexOf('>0DTE<') < html.indexOf('>Calendars<'));
  assert.match(html, /class="st-ev st-dot pending"[^>]*data-lots="2" data-row="1-2"/);
  assert.match(html, /MEIC &lt;i&gt;/);
  assert.match(html, /data-label="10:00"/);
  assert.match(html, />10:00</);  // 09:15 to 16:15
  assert.match(html, />16:00</);
});
