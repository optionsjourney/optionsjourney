// The schedule drawer (#195; Tracker2's schedule_drawer.js, in this app's styles): what the
// armed bots do on a day, and whether each entry will actually trade. Opened by any
// [data-schedule-open] button; reads GET /automation/schedule?date=YYYY-MM-DD.
//
// Today by default; ‹ › step a weekday at a time and the date box jumps anywhere. Another
// day is a forecast: Freeze and the market filters are only decided when each entry fires.
// The All / Live / Paper tabs show one kind of bot at a time; the choice is remembered.

import { tipHtml } from './tip.js';

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// Tips (checked against routes/automation.py skip_day and the schedule's blocked count).
export const SKIP_TIP = "Adds this day to the bot's blackout days, so none of its entries open that day. Open trades keep being managed. You can undo it right after.";
export const SKIP_MORE = '/help/automation#blackout-days-and-skip-a-day';
export const BLOCKED_TIP = "Entries that won't trade that day: the market is shut, it's one of the bot's blackout days, a date list the bot blacks out can't tell about that day, Freeze or No new trades is on today, or the entry is past the cut-off. They're counted here but not listed.";
export const BLOCKED_MORE = '/help/faq#why-didnt-a-bot-enter';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MODE_KEY = 'schedule_mode';
export const MODES = ['all', 'live', 'paper'];
const MODE_NAME = { live: 'Live', paper: 'Paper' };

// YYYY-MM-DD <-> Date at UTC midnight, so no local timezone shifts the day.
const parse = iso => new Date(`${iso}T00:00:00Z`);
const fmt = d => d.toISOString().slice(0, 10);

// The next (dir 1) or previous (dir -1) weekday after `iso`.
export function stepWeekday(iso, dir) {
  const d = parse(iso);
  do d.setUTCDate(d.getUTCDate() + dir); while (d.getUTCDay() === 0 || d.getUTCDay() === 6);
  return fmt(d);
}

// "Mon 2026-09-28"
export function dayLabel(iso) {
  return `${DAYS[parse(iso).getUTCDay()]} ${iso}`;
}

// What a state is called: nothing on a future day has fired, so "pending" there only
// means scheduled.
export function stateLabel(state, future) {
  if (state === 'unscheduled') return 'NOT scheduled';
  return future && state === 'pending' ? 'scheduled' : state;
}

// The account, then the bot's own tags.
function chips(r) {
  const out = [
    ...(r.account ? [`<span class="chip schedule-chip-account" title="Account">${esc(r.account)}</span>`] : []),
    ...(r.tags || []).map(t => `<span class="chip schedule-chip-tag">${esc(t)}</span>`),
  ];
  return out.length ? `<div class="chips">${out.join('')}</div>` : '';
}

// The day's economic events (#221; Tracker2's econChips): high impact first, each release's
// forecast and previous in the tooltip.
function econChips(items) {
  if (!items || !items.length) return '';
  const ordered = [...items.filter(i => i.impact === 'high'), ...items.filter(i => i.impact !== 'high')];
  const chips = ordered.map(i => {
    const tip = (i.prints || []).map(p => [p.title, p.forecast ? `fcst ${p.forecast}` : '',
      p.previous ? `prev ${p.previous}` : ''].filter(Boolean).join(' · ')).join('\n');
    const text = `${i.impact === 'high' ? '🟥 ' : ''}${i.time ? `${i.time} ` : ''}${i.label}`;
    return `<span class="econ-chip econ-chip--${esc(i.impact)}" title="${esc(tip)}">${esc(text)}</span>`;
  }).join('');
  return `<div class="schedule-econ"><div class="muted small">Economic events</div>
    <div class="chips">${chips}</div></div>`;
}

function rows(list) {
  // A floating window (11:30–11:45) needs a wider time column than HH:MM.
  const wide = list.some(r => r.floating) ? ' schedule-row__time--wide' : '';
  return list.map(r => {
    // A run is not a trade: a filter refusal (skipped) or a failure (error) is recorded
    // too, so the dot and the words follow the outcome.
    const what = r.run_url
      ? `<a class="link" href="${esc(r.run_url)}">${esc(r.note || 'the run')}</a>`
      : esc(r.note || '');
    return `
      <div class="schedule-row">
        <span class="schedule-dot schedule-dot--${esc(r.state)}" title="${esc(r.state)}"></span>
        <div class="schedule-row__time${wide}">
          <div class="num">${esc(r.time)}</div>
          ${r.can_skip
            ? `<span class="tip tip--start"><button type="button" class="link-button small" data-skip-bot="${esc(r.bot_id)}" data-skip-name="${esc(r.bot_name)}"
                 aria-describedby="tip-schedule-skip-${esc(r.bot_id)}-${esc(r.time)}">Skip day</button>`
              + `<span class="tip__text" role="tooltip" id="tip-schedule-skip-${esc(r.bot_id)}-${esc(r.time)}">${esc(SKIP_TIP)}`
              + `<a class="tip__more" href="${esc(SKIP_MORE)}" target="_blank" rel="noopener">Learn more</a></span></span>`
            : ''}
        </div>
        <div class="schedule-row__main">
          <div class="schedule-row__name">
            <a class="link" href="${esc(r.bot_url)}" title="Open this bot">${esc(r.bot_name)}</a>
            ${r.live ? '<span class="badge badge--live">Live</span>' : '<span class="badge badge--preview">Paper</span>'}
          </div>
          <div class="muted small">
            ${r.floating ? 'window · ' : ''}${esc(r.underlying)} · ${esc(r.size)}${what
              ? ` · ${r.state === 'unscheduled' ? `<strong class="negative">${what}</strong>` : what}` : ''}
          </div>
          ${chips(r)}
        </div>
      </div>`;
  }).join('');
}

// The rows a tab shows, and how many entries it leaves out as not trading that day.
export function forMode(s, mode) {
  if (mode !== 'live' && mode !== 'paper') return { rows: s.rows, blocked: s.counts.blocked || 0 };
  const live = s.counts.blocked_live || 0;
  return {
    rows: s.rows.filter(r => Boolean(r.live) === (mode === 'live')),
    blocked: mode === 'live' ? live : (s.counts.blocked || 0) - live,
  };
}

// The tabs' labels: "All 5", "Live 2", "Paper 3" (the rows each shows).
export function tabCounts(s) {
  return Object.fromEntries(MODES.map(m => [m, forMode(s, m).rows.length]));
}

// The counts row and the body for a schedule (the JSON from /automation/schedule), for the
// All, Live or Paper tab.
export function render(s, mode = 'all') {
  const future = !s.is_today && s.date > s.today;
  const shown = forMode(s, mode);
  const counts = Object.keys(s.counts)
    .filter(state => state !== 'blocked' && state !== 'blocked_live')
    .map(state => [state, shown.rows.filter(r => r.state === state).length])
    .filter(([, n]) => n > 0)
    .map(([state, n]) => `<span class="schedule-count schedule-count--${esc(state)}">${n} ${esc(stateLabel(state, future))}</span>`)
    .join('') || `<span class="muted small">${s.market_closed ? 'Market closed' : 'Nothing scheduled'}</span>`;

  const notes = [];
  // The schedule check (bot_audit): rows come from the bots, only a running clock fires them.
  if (s.clock_ok && shown.rows.some(r => r.state === 'pending' || r.state === 'watching')) {
    notes.push('<p class="small positive">✓ Every entry below has a live scheduler job.</p>');
  }
  for (const n of s.notes) {
    notes.push(n.warn
      ? `<p class="notice notice--warning small">${esc(n.text)}</p>`
      : `<p class="muted small">${esc(n.text)}</p>`);
  }
  // A day past the holiday calendar is treated as open: say so.
  if (s.calendar_covers === false) {
    notes.push('<p class="muted small">Market holidays aren\'t known this far ahead: this day is treated as a trading day.</p>');
  }
  if (shown.blocked) {
    notes.push(`<p class="muted small">${tipHtml('schedule-blocked', BLOCKED_TIP, 'entries not trading that day', { more: BLOCKED_MORE })}`
      + ` ${shown.blocked} more not trading that day (market shut, blackout, Freeze or past the cut-off).</p>`);
  }

  let body;
  const when = s.is_today ? 'today' : 'on this day';
  if (s.market_closed) {
    body = `<p class="muted">Market closed${s.holiday ? `: ${esc(s.holiday)}` : ''}. Nothing trades on this day.</p>`;
  } else if (!s.rows.length) {
    body = `<p class="muted small">No armed bot has an entry time ${when}.
      Arm a bot and its entry times show here.</p>`;
  } else if (!shown.rows.length) {
    body = `<p class="muted small">No armed ${MODE_NAME[mode]} bot has an entry time ${when}.</p>`;
  } else {
    body = rows(shown.rows);
  }
  return { counts, body: notes.join('') + econChips(s.econ_events) + body };
}

function undoNote(u) {
  return `<p class="notice notice--warning small">Skipped ${esc(u.name)} on this day (added to its blackout days).
    <button type="button" class="link" data-skip-undo>Undo</button></p>`;
}

export function installScheduleDrawer(doc, win) {
  const drawer = doc.getElementById('schedule-drawer');
  if (!drawer) return;
  const backdrop = drawer.querySelector('[data-schedule-backdrop]');
  const panel = drawer.querySelector('[data-schedule-panel]');
  const body = doc.getElementById('schedule-body');
  const dateInput = doc.getElementById('schedule-date');
  let day = null;       // the shown day, YYYY-MM-DD; null until the first load says what today is
  let token = 0;        // a slow answer for an earlier day must not overwrite a later one
  let undo = null;      // {botId, name, date} of the last skip, for its Undo note
  let opener = null;
  let shownDay = null;  // the last schedule loaded, so a tab redraws without asking again
  let mode = 'all';
  try { mode = win.localStorage.getItem(MODE_KEY) || 'all'; } catch { /* default */ }
  if (!MODES.includes(mode)) mode = 'all';
  const tabs = [...drawer.querySelectorAll('[data-schedule-mode]')];

  const isOpen = () => !drawer.classList.contains('hidden');

  async function load(date) {
    const mine = ++token;
    body.innerHTML = '<p class="muted small">Loading…</p>';
    try {
      const url = drawer.dataset.scheduleUrl + (date ? `?date=${encodeURIComponent(date)}` : '');
      const res = await win.fetch(url, { headers: { Accept: 'application/json' } });
      const s = await res.json();
      if (!res.ok) throw new Error(s.error || `HTTP ${res.status}`);
      if (mine !== token) return;
      day = s.date;
      dateInput.value = day;
      doc.getElementById('schedule-title').textContent = s.is_today ? "Today's schedule" : 'Schedule';
      doc.getElementById('schedule-subtitle').textContent =
        s.is_today ? `${dayLabel(s.date)} · now ${s.now} ET` : dayLabel(s.date);
      doc.getElementById('schedule-today').hidden = s.is_today;
      shownDay = s;
      draw();
    } catch (e) {
      if (mine !== token) return;
      body.innerHTML = `<p class="notice notice--warning small">Couldn't load the schedule: ${esc(e.message)}</p>`;
    }
  }

  // The shown day in the chosen tab, with each tab's count.
  function draw() {
    const n = tabCounts(shownDay);
    for (const tab of tabs) {
      const on = tab.dataset.scheduleMode === mode;
      tab.classList.toggle('tabs__tab--active', on);
      tab.setAttribute('aria-selected', String(on));
      tab.querySelector('[data-schedule-mode-count]').textContent = n[tab.dataset.scheduleMode];
    }
    const out = render(shownDay, mode);
    doc.getElementById('schedule-counts').innerHTML = out.counts;
    body.innerHTML = (undo && undo.date === day ? undoNote(undo) : '') + out.body;
  }

  tabs.forEach(tab => tab.addEventListener('click', () => {
    mode = tab.dataset.scheduleMode;
    try { win.localStorage.setItem(MODE_KEY, mode); } catch { /* not remembered */ }
    if (shownDay) draw();
  }));

  // A skip is a blackout day (POST / DELETE /automation/<id>/skip-day): the bot's rows drop
  // out of the day, and the note offers the Undo.
  async function skip(botId, name, method) {
    try {
      const url = drawer.dataset.skipUrl.replace('/0/', `/${encodeURIComponent(botId)}/`) + `?date=${day}`;
      const res = await win.fetch(url, { method });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      undo = method === 'POST' ? { botId, name, date: day } : null;
    } catch (err) {
      undo = null;
      body.insertAdjacentHTML('afterbegin',
        `<p class="notice notice--warning small">Couldn't change the skip: ${esc(err.message)}</p>`);
      return;
    }
    load(day);
  }

  body.addEventListener('click', e => {
    const btn = e.target.closest('[data-skip-bot]');
    if (btn) {
      if (win.confirm(`Skip ${btn.dataset.skipName} on ${dayLabel(day)}?\n\n` +
                      'The day goes into its blackout days. Open trades keep being managed.')) {
        btn.disabled = true;
        skip(btn.dataset.skipBot, btn.dataset.skipName, 'POST');
      }
      return;
    }
    if (e.target.closest('[data-skip-undo]') && undo) skip(undo.botId, undo.name, 'DELETE');
  });

  function open(e) {
    opener = e.currentTarget;
    undo = null;
    drawer.classList.remove('hidden');
    win.requestAnimationFrame(() => {
      backdrop.classList.remove('opacity-0');
      panel.classList.remove('translate-x-full');
    });
    load(null);       // always reopens on today
    doc.getElementById('schedule-date').focus({ preventScroll: true });
  }

  function close() {
    if (!isOpen()) return;
    backdrop.classList.add('opacity-0');
    panel.classList.add('translate-x-full');
    win.setTimeout(() => drawer.classList.add('hidden'), 300);
    if (opener) opener.focus();
  }

  doc.querySelectorAll('[data-schedule-open]').forEach(btn => btn.addEventListener('click', open));
  drawer.querySelector('[data-schedule-close]').addEventListener('click', close);
  backdrop.addEventListener('click', close);
  doc.getElementById('schedule-prev').addEventListener('click', () => day && load(stepWeekday(day, -1)));
  doc.getElementById('schedule-next').addEventListener('click', () => day && load(stepWeekday(day, 1)));
  doc.getElementById('schedule-today').addEventListener('click', () => load(null));
  dateInput.addEventListener('change', () => { if (dateInput.value) load(dateInput.value); });

  // Capture phase, ahead of the page's own Escape handlers: an open drawer takes the Escape.
  doc.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !isOpen()) return;
    close();
    e.stopImmediatePropagation();
  }, true);
}
