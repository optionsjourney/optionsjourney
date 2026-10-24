import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bellView, timeAgo } from '../../../../src/optionsjourney/web/static/js/alert_bell.js';

const NOW = Date.parse('2026-10-23T15:00:00Z');

test('ages read as minutes, hours and days', () => {
  assert.equal(timeAgo('2026-10-23T14:59:40+00:00', NOW), 'just now');
  assert.equal(timeAgo('2026-10-23T14:57:00+00:00', NOW), '3m ago');
  assert.equal(timeAgo('2026-10-23T12:00:00+00:00', NOW), '3h ago');
  assert.equal(timeAgo('2026-10-21T15:00:00+00:00', NOW), '2d ago');
  assert.equal(timeAgo('nope', NOW), '');
});

test('the list shows problems and notes, then the alerts with the new ones marked', () => {
  const view = bellView({
    standing: [
      { level: 'problem', title: 'Schwab ••9999 didn’t sync', detail: 'Log in.', link: '/transactions' },
      { level: 'warning', title: 'Schwab last synced 3 days ago', detail: '', link: '/transactions' },
    ],
    recent: [
      { title: 'SPX went below 7,700.00', detail: 'now 7,600.00', link: '/alerts', created_at: '2026-10-23T14:57:00+00:00', unread: true },
      { title: 'Settled', detail: 'd', link: '', created_at: '2026-10-22T15:00:00+00:00', unread: false },
    ],
  }, NOW);
  assert.deepEqual(view.standing.map(a => a.tone), ['problem', 'note']);
  assert.deepEqual(view.recent.map(a => [a.when, a.unread]), [['3m ago', true], ['1d ago', false]]);
  assert.equal(view.recent[1].link, '/alerts');
  assert.equal(view.empty, false);
});

test('an empty reply says so', () => {
  assert.equal(bellView({ standing: [], recent: [] }).empty, true);
  assert.equal(bellView(null).empty, true);
});
