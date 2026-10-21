import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stepWeekday, dayLabel, stateLabel, render } from '../../../../src/optionsjourney/web/static/js/schedule_drawer.js';

const ROW = {
  time: '09:47', bot_id: 3, bot_name: 'MEIC <b>', bot_url: '/automation/3', live: true,
  underlying: 'SPX', size: '1 contract', account: 'Joint ···1234', tags: ['0dte'],
  state: 'pending', note: null, run_url: null, floating: false, can_skip: true,
};
const DAY = {
  date: '2026-10-20', today: '2026-10-20', is_today: true, now: '09:18:42', clock_ok: true,
  market_closed: false, counts: { pending: 1, blocked: 0 }, notes: [], rows: [ROW],
};

test('the arrows step over the weekend', () => {
  assert.equal(stepWeekday('2026-10-16', 1), '2026-10-19');  // Fri -> Mon
  assert.equal(stepWeekday('2026-10-19', -1), '2026-10-16');
  assert.equal(dayLabel('2026-10-20'), 'Tue 2026-10-20');
});

test('pending on a later day only means scheduled', () => {
  assert.equal(stateLabel('pending', true), 'scheduled');
  assert.equal(stateLabel('pending', false), 'pending');
  assert.equal(stateLabel('unscheduled', false), 'NOT scheduled');
});

test('a row shows the time, Skip day, the name escaped, the badge and the chips', () => {
  const { counts, body } = render(DAY);
  assert.match(counts, /1 pending/);
  assert.match(body, /Every entry below has a live scheduler job/);
  assert.match(body, /data-skip-bot="3"/);
  assert.match(body, /MEIC &lt;b&gt;/);
  assert.match(body, /badge--live/);
  assert.match(body, /SPX · 1 contract/);
  assert.match(body, /Joint ···1234/);
  assert.match(body, /0dte/);
});

test('no check line when the clock is not known, and warnings stand out', () => {
  const { body } = render({ ...DAY, clock_ok: null, notes: [{ text: 'Freeze is on', warn: true }] });
  assert.doesNotMatch(body, /live scheduler job/);
  assert.match(body, /notice--warning small">Freeze is on/);
});

test('a closed market says so', () => {
  const out = render({ ...DAY, market_closed: true, rows: [], counts: {} });
  assert.match(out.counts, /Market closed/);
  assert.match(out.body, /Nothing trades on this day/);
});

test('a closed market names the holiday', () => {
  const out = render({ ...DAY, market_closed: true, holiday: 'Thanksgiving <Day>', rows: [], counts: {} });
  assert.match(out.body, /Market closed: Thanksgiving &lt;Day&gt;\. Nothing trades/);
});

test('a day past the holiday calendar says it is treated as open', () => {
  assert.match(render({ ...DAY, calendar_covers: false }).body, /aren't known this far ahead/);
  assert.doesNotMatch(render({ ...DAY, calendar_covers: true }).body, /this far ahead/);
});

test("the day's economic events: high first, figures in the tooltip", () => {
  const econ_events = [
    { time: '10:00', label: 'ISM <PMI>', impact: 'medium', key: null,
      prints: [{ title: 'ISM <PMI>', forecast: null, previous: '49.1' }] },
    { time: '08:30', label: 'CPI', impact: 'high', key: 'CPI',
      prints: [{ title: 'CPI m/m', forecast: '0.3%', previous: '0.4%' },
               { title: 'Core CPI m/m', forecast: '0.3%', previous: null }] },
  ];
  const { body } = render({ ...DAY, econ_events });
  assert.match(body, /Economic events/);
  assert.ok(body.indexOf('🟥 08:30 CPI') < body.indexOf('10:00 ISM &lt;PMI&gt;'));
  assert.match(body, /econ-chip--high" title="CPI m\/m · fcst 0\.3% · prev 0\.4%\nCore CPI m\/m · fcst 0\.3%"/);
  assert.match(body, /title="ISM &lt;PMI&gt; · prev 49\.1"/);
  assert.doesNotMatch(render(DAY).body, /Economic events/);
});

test('Skip day and the not-trading count carry tips that link to the guide', async () => {
  const { SKIP_TIP, SKIP_MORE, BLOCKED_TIP, BLOCKED_MORE } = await import(
    '../../../../src/optionsjourney/web/static/js/schedule_drawer.js');
  assert.ok(SKIP_TIP.length > 0 && BLOCKED_TIP.length > 0);
  const { body } = render({ ...DAY, counts: { pending: 1, blocked: 2 } });
  assert.match(body, /id="tip-schedule-skip-3-09:47"/);
  assert.match(body, /aria-describedby="tip-schedule-skip-3-09:47"/);
  assert.ok(body.includes(`href="${SKIP_MORE}"`));
  assert.match(body, /id="tip-schedule-blocked"/);
  assert.ok(body.includes(`href="${BLOCKED_MORE}"`));
  assert.ok(!body.includes('title="Adds this day'));
});

test('the Live and Paper tabs show one kind of bot, with their own counts', async () => {
  const { tabCounts } = await import('../../../../src/optionsjourney/web/static/js/schedule_drawer.js');
  const paper = { ...ROW, bot_id: 4, bot_name: 'Practice', live: false, state: 'done' };
  const day = { ...DAY, counts: { pending: 1, done: 1, blocked: 3, blocked_live: 1 }, rows: [ROW, paper] };
  assert.deepEqual(tabCounts(day), { all: 2, live: 1, paper: 1 });

  const live = render(day, 'live');
  assert.match(live.counts, /1 pending/);
  assert.doesNotMatch(live.counts, /done/);
  assert.match(live.body, /MEIC/);
  assert.doesNotMatch(live.body, /Practice/);
  assert.match(live.body, / 1 more not trading that day/);

  const practice = render(day, 'paper');
  assert.match(practice.counts, /1 done/);
  assert.doesNotMatch(practice.body, /MEIC|live scheduler job/);
  assert.match(practice.body, / 2 more not trading that day/);

  assert.match(render(day).body, / 3 more not trading/);
  assert.match(render({ ...DAY, rows: [paper] }, 'live').body, /No armed Live bot has an entry time today/);
});
