// The Automation list (#216; Tracker2's automation_board.js, 81d4475c): grouping by strategy
// or day, expanding rows, the row and account-chip menus, ticking a row's bots, and today's
// entries on a time axis from the schedule drawer's data (GET /automation/schedule).
//
// Every action goes through a bot's own control in its row's expanded table (data-act:
// preview, arm, mode, trade, clone, open, record), so the forms, the Live page and the
// bot page keep every confirmation. A menu item only clicks that control.

const DAY_NAMES = ['', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];
const GROUP_KEY = 'automation.groupBy';
export const LABELS = {
  done: 'Filled', running: 'Working', unfilled: 'Not filled', skipped: 'Skipped', error: 'Error',
  missed: 'Missed', blocked: 'Blocked', pending: 'To come', watching: 'Watching', unscheduled: 'Not scheduled',
};
// When one dot stands for several bots, the state that most needs a look shows.
const RANK = ['error', 'unscheduled', 'missed', 'unfilled', 'running', 'watching', 'pending', 'blocked', 'skipped', 'done'];

export const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export function minutes(hhmm) {
  const m = /^(\d{1,2}):(\d{2})/.exec(hhmm || '');
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/** The group a row goes under: its strategy, or the weekday it fires (Tracker2's groupOf).
 *  Rows with nothing armed go last, under Off. */
export function groupOf(row, by, todayDow) {
  if (!row.armed) return { key: 'zz', label: 'Off', muted: true };
  if (by === 'day') {
    if (!row.weekly) return { key: '6', label: 'Specific dates & monthly' };
    if (row.days.length >= 5) return { key: '0', label: 'Every weekday' };
    if (!row.days.length) return { key: '7', label: 'No days ticked' };
    const d = row.days[0];
    return { key: String(d), label: DAY_NAMES[d] + (d === todayDow ? ' · today' : '') };
  }
  return { key: `a${row.strategy.toLowerCase()}`, label: row.strategy };
}

export function stateOf(entries) {
  return entries.map((e) => e.state).sort((a, b) => RANK.indexOf(a) - RANK.indexOf(b))[0];
}

/** The command bar's Today: filled, still to come, and any that need a look. */
export function todayText(counts = {}) {
  const toGo = (counts.pending || 0) + (counts.watching || 0) + (counts.running || 0);
  const bad = (counts.unfilled || 0) + (counts.missed || 0) + (counts.error || 0) + (counts.unscheduled || 0);
  return `${counts.done || 0} filled <small>· ${toGo} to come${bad ? ` · <span class="negative">${bad} need a look</span>` : ''}</small>`;
}

/** The command bar's Next entry: the first entry still to come, and how long until it. */
export function nextText(rows, now, paused, nameOf = (r) => r.bot_name) {
  if (paused) return '<span class="negative">Entries paused</span>';
  const next = rows.filter((r) => r.state === 'pending' && minutes(r.time) > now)
    .sort((a, b) => minutes(a.time) - minutes(b.time))[0];
  if (!next) return rows.length ? 'Done for today' : 'Nothing today';
  const wait = minutes(next.time) - now;
  const inText = wait >= 60 ? `${Math.floor(wait / 60)}h ${wait % 60}m` : `${wait}m`;
  return `<span class="st-num">${esc(next.time.slice(0, 5))}</span> ${esc(nameOf(next))} <b class="st-num">· ${inText}</b>`;
}

/** Today's entries on a time axis: one lane per strategy, one dot per row and time.
 *  `dots` are {lane, rowKey, name, time, entries}. */
export function tapeHtml(dots, now, nowLabel) {
  const times = dots.map((d) => minutes(d.time));
  const t0 = Math.min(9 * 60 + 15, Math.min(...times) - 15);
  const t1 = Math.max(16 * 60 + 15, Math.max(...times) + 15);
  const frac = (m) => Math.max(0, Math.min(1, (m - t0) / (t1 - t0)));
  const pct = (m) => `${(frac(m) * 100).toFixed(3)}%`;
  const LANE = 96;
  const x = (m) => `calc(${LANE}px + (100% - ${LANE}px) * ${frac(m).toFixed(4)})`;
  const lanes = [...new Set(dots.map((d) => d.lane))].sort((a, b) => a.localeCompare(b));
  let html = '';
  for (let h = Math.ceil(t0 / 60); h * 60 <= t1; h++) html += `<div class="st-gridline" style="left:${x(h * 60)}"></div>`;
  lanes.forEach((lane) => {
    html += `<div class="st-lane"><span class="st-lane-name" title="${esc(lane)}">${esc(lane)}</span><div class="st-track">`;
    dots.filter((d) => d.lane === lane).forEach((d) => {
      const state = stateOf(d.entries);
      const lines = d.entries.map((e) => `${e.account} · ${e.size}: ${LABELS[e.state] || e.state}${e.note ? ` · ${e.note}` : ''}`);
      html += `<button type="button" class="st-ev st-dot ${esc(state)}" style="left:${pct(minutes(d.time))}"`
        + `${d.entries.length > 1 ? ` data-lots="${d.entries.length}"` : ''} data-row="${esc(d.rowKey)}"`
        + ` data-tip="${esc(JSON.stringify({ n: d.name, t: d.time, l: lines }))}"`
        + ` aria-label="${esc(`${d.name} ${d.time} ${LABELS[state] || state}`)}"></button>`;
    });
    html += '</div></div>';
  });
  html += '<div class="st-axis"><span></span><div class="st-track">';
  for (let h = Math.ceil(t0 / 60); h * 60 <= t1; h++) html += `<span style="left:${pct(h * 60)}">${String(h).padStart(2, '0')}:00</span>`;
  html += '</div></div>';
  if (now != null && now >= t0 && now <= t1) html += `<div class="st-now" style="left:${x(now)}" data-label="${esc(nowLabel)}"></div>`;
  return html;
}

export function installAutomationBoard(doc, win) {
  const root = doc.getElementById('st-root');
  if (!root) return;
  const groupsBox = doc.getElementById('st-groups');
  const items = () => [...doc.querySelectorAll('.st-item')];
  const todayDow = Number(root.dataset.todayDow);
  const rowOf = (item) => ({
    armed: item.dataset.armed === '1',
    weekly: item.dataset.weekly === '1',
    days: (item.dataset.days || '').split(',').filter(Boolean).map(Number),
    strategy: item.dataset.strategy || '',
  });
  const nameOf = (item) => item.querySelector('.st-name a')?.textContent || '';
  const controls = (item) => [...item.querySelectorAll('tr[data-bot-id]')].map((tr) => ({
    id: tr.dataset.botId,
    account: tr.querySelector('[data-act=open]')?.textContent.trim() || '',
    name: tr.querySelector('[data-act=open]')?.title || '',
    act: (what) => tr.querySelector(`[data-act="${what}"]`),
    box: tr.querySelector('input[type=checkbox][name=bot_id]'),
  }));

  // ---------- grouping ----------
  let groupBy = 'strategy';
  try { groupBy = win.localStorage.getItem(GROUP_KEY) || 'strategy'; } catch { /* default */ }
  items().forEach((item, i) => { item.dataset.order = String(i); });

  function regroup() {
    if (!groupsBox) return;
    const groups = new Map();
    items().sort((a, b) => a.dataset.order - b.dataset.order).forEach((item) => {
      const g = groupOf(rowOf(item), groupBy, todayDow);
      if (!groups.has(g.key)) groups.set(g.key, { ...g, items: [] });
      groups.get(g.key).items.push(item);
    });
    const sections = [...groups.values()].sort((a, b) => a.key.localeCompare(b.key)).map((g) => {
      const sec = doc.createElement('section');
      sec.className = 'st-group';
      const bots = g.items.reduce((n, i) => n + i.querySelectorAll('tr[data-bot-id]').length, 0);
      sec.innerHTML = `<div class="st-group-h${g.muted ? ' muted' : ''}"><h3>${esc(g.label)}</h3>`
        + `<span class="meta">${bots} bot${bots === 1 ? '' : 's'}</span></div><div class="st-group-body"></div>`;
      const body = sec.querySelector('.st-group-body');
      g.items.forEach((i) => body.appendChild(i));
      return sec;
    });
    groupsBox.replaceChildren(...sections);
    doc.querySelectorAll('[data-group]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.group === groupBy)));
  }
  doc.querySelectorAll('[data-group]').forEach((btn) => btn.addEventListener('click', () => {
    groupBy = btn.dataset.group;
    try { win.localStorage.setItem(GROUP_KEY, groupBy); } catch { /* not remembered */ }
    regroup();
  }));

  // ---------- expanding a row ----------
  function toggleDetail(item, open) {
    const row = item.querySelector('.st-row');
    const next = open ?? !item.classList.contains('is-open');
    item.classList.toggle('is-open', next);
    row.setAttribute('aria-expanded', String(next));
  }
  doc.addEventListener('click', (e) => {
    const row = e.target.closest?.('.st-row');
    if (!row || e.target.closest('a, button, input, select, textarea, label')) return;
    toggleDetail(row.closest('.st-item'));
  });
  doc.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.classList?.contains('st-row')) toggleDetail(e.target.closest('.st-item'));
  });

  // ---------- ticking a row ticks all of its bots ----------
  function syncRowBoxes() {
    items().forEach((item) => {
      const boxes = controls(item).map((c) => c.box).filter(Boolean);
      const n = boxes.filter((b) => b.checked).length;
      const rowBox = item.querySelector('.st-row-select');
      if (!rowBox) return;
      rowBox.checked = n > 0 && n === boxes.length;
      rowBox.indeterminate = n > 0 && n < boxes.length;
      item.classList.toggle('row-selected', n > 0);
    });
  }
  doc.addEventListener('change', (e) => {
    const rowBox = e.target.closest?.('.st-row-select');
    if (rowBox) {
      const on = rowBox.checked;  // each box's change re-syncs the row box
      controls(rowBox.closest('.st-item')).forEach((c) => {
        if (!c.box) return;
        c.box.checked = on;
        c.box.dispatchEvent(new win.Event('change', { bubbles: true }));
      });
    }
    syncRowBoxes();
  });
  doc.addEventListener('bots-ticked', syncRowBoxes);
  syncRowBoxes();

  // ---------- menus ----------
  let menu = null;
  const closeMenu = () => { menu?.remove(); menu = null; };
  /* entries: {hd} | {sep} | {label, el} where el is the control it clicks */
  function openMenu(anchor, entries, forKey) {
    if (menu && menu.dataset.for === forKey) { closeMenu(); return; }
    closeMenu();
    menu = doc.createElement('div');
    menu.className = 'st-menu';
    menu.dataset.for = forKey;
    menu.setAttribute('role', 'menu');
    entries.forEach((en) => {
      if (en.sep) { menu.appendChild(Object.assign(doc.createElement('div'), { className: 'sep' })); return; }
      if (en.hd) { menu.appendChild(Object.assign(doc.createElement('div'), { className: 'hd', textContent: en.hd })); return; }
      const b = doc.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'menuitem');
      b.textContent = en.label;
      b.addEventListener('click', () => { closeMenu(); (en.run || (() => en.el.click()))(); });
      menu.appendChild(b);
    });
    root.appendChild(menu);
    const r = anchor.getBoundingClientRect();
    let top = r.bottom + 4;
    if (top + menu.offsetHeight > win.innerHeight - 8) top = Math.max(8, r.top - menu.offsetHeight - 4);
    menu.style.left = `${Math.max(12, Math.min(r.left, win.innerWidth - menu.offsetWidth - 12))}px`;
    menu.style.top = `${top}px`;
    menu.querySelector('button')?.focus({ preventScroll: true });
  }
  doc.addEventListener('click', (e) => {
    if (menu && !menu.contains(e.target) && !e.target.closest('[data-row-act], .st-acct')) closeMenu();
  });
  doc.addEventListener('keydown', (e) => { if (e.key === 'Escape' && menu) closeMenu(); }, true);
  win.addEventListener('scroll', closeMenu, { passive: true });

  const label = (el, fallback) => el?.dataset.label || fallback;
  function botEntries(c) {
    const out = [];
    const add = (what, text) => { const el = c.act(what); if (el) out.push({ label: label(el, text), el }); };
    add('open', 'Open');
    add('record', 'Record');
    add('preview', 'Preview order');
    add('arm', 'Arm');
    add('mode', 'Paper or Live');
    add('trade', 'Trade now…');
    add('clone', 'Clone');
    return out;
  }

  doc.addEventListener('click', (e) => {
    const act = e.target.closest?.('[data-row-act]');
    if (act) {
      e.stopPropagation();
      const item = act.closest('.st-item');
      const cs = controls(item);
      const kind = act.dataset.rowAct;
      const forKey = `${kind}${item.dataset.rowKey}`;
      if (kind === 'more') {
        if (cs.length === 1) { openMenu(act, botEntries(cs[0]), forKey); return; }
        const entries = [{ label: 'Show accounts', run: () => toggleDetail(item, true) }];
        cs.forEach((c) => entries.push({ sep: true }, { hd: c.account }, ...botEntries(c)));
        openMenu(act, entries, forKey);
        return;
      }
      // Preview, or Trade now (its page, once you type TRADE): straight away for one bot,
      // otherwise pick the account. Never one click for several accounts.
      const able = cs.filter((c) => c.act(kind));
      if (able.length === 1) { able[0].act(kind).click(); return; }
      openMenu(act, [{ hd: kind === 'trade' ? 'Trade now' : 'Preview order' },
        ...able.map((c) => ({ label: c.account, el: c.act(kind) }))], forKey);
      return;
    }
    const chip = e.target.closest?.('.st-acct');
    if (chip && !e.metaKey && !e.ctrlKey && !e.shiftKey) {
      e.preventDefault();
      const c = controls(chip.closest('.st-item')).find((x) => x.id === chip.dataset.botId);
      if (c) openMenu(chip, [{ hd: c.name }, ...botEntries(c)], `chip${c.id}`);
    }
  });

  // ---------- today's entries ----------
  const tapePlot = doc.getElementById('st-tape-plot');
  const tapeMsg = doc.getElementById('st-tape-msg');
  const itemByBot = new Map();
  items().forEach((item) => (item.dataset.botIds || '').split(',').forEach((id) => itemByBot.set(id, item)));

  function paint(s) {
    const now = minutes(s.now);
    const title = doc.getElementById('st-tape-title');
    if (title) title.textContent = `Today · ${new Date(`${s.date}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}`;
    const todayV = doc.getElementById('st-today-v');
    if (todayV) todayV.innerHTML = s.rows.length ? todayText(s.counts) : 'Nothing today';
    const nextV = doc.getElementById('st-next-v');
    if (nextV) {
      nextV.innerHTML = nextText(s.rows, now, root.dataset.paused === '1', (r) => {
        const item = itemByBot.get(String(r.bot_id));
        return item ? nameOf(item) : r.bot_name;
      });
    }

    const dots = new Map();
    const perItem = new Map();
    s.rows.forEach((r) => {
      const item = itemByBot.get(String(r.bot_id));
      if (!item || minutes(r.time) == null) return;
      const key = `${item.dataset.rowKey}|${r.time}`;
      if (!dots.has(key)) dots.set(key, { lane: item.dataset.strategy, rowKey: item.dataset.rowKey, name: nameOf(item), time: r.time, entries: [] });
      dots.get(key).entries.push(r);
      if (!perItem.has(item)) perItem.set(item, []);
      perItem.get(item).push(r);
    });

    items().forEach((item) => {
      const cell = item.querySelector('[data-today]');
      const entries = (perItem.get(item) || []).slice().sort((a, b) => minutes(a.time) - minutes(b.time));
      if (cell) {
        cell.innerHTML = entries.length
          ? `<div class="st-pips">${entries.map((e) => `<i class="st-dot ${esc(e.state)}" title="${esc(`${e.time} · ${e.account} · ${LABELS[e.state] || e.state}${e.note ? ` · ${e.note}` : ''}`)}"></i>`).join('')}</div>`
            + `<span class="st-num">${entries.filter((e) => e.state === 'done').length}/${entries.length}</span>`
          : '<span class="none">—</span>';
      }
      // Past entry times struck through, the next one outlined: per line, since accounts
      // with their own times each have one.
      item.querySelectorAll('.st-times').forEach((line) => {
        const chips = [...line.querySelectorAll('span[data-time]')];
        chips.forEach((ch) => {
          ch.classList.remove('done', 'next');
          if (entries.length && minutes(ch.dataset.time) <= now) ch.classList.add('done');
        });
        if (entries.length) {
          chips.filter((ch) => minutes(ch.dataset.time) > now)
            .sort((a, b) => minutes(a.dataset.time) - minutes(b.dataset.time))[0]?.classList.add('next');
        }
      });
    });

    if (!tapePlot) return;
    if (!dots.size) {
      tapePlot.replaceChildren();
      tapeMsg.textContent = s.market_closed ? 'The market is closed today.' : 'No entries today.';
      return;
    }
    tapeMsg.textContent = root.dataset.paused === '1' ? 'New entries are paused: these won’t fire.' : '';
    tapePlot.innerHTML = tapeHtml([...dots.values()], now, s.now.slice(0, 5));
  }

  async function load() {
    try {
      const res = await win.fetch(root.dataset.scheduleUrl, { headers: { Accept: 'application/json' } });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      paint(data);
    } catch (err) {
      if (tapeMsg) tapeMsg.textContent = `Couldn't load today's schedule: ${err.message}`;
    }
  }

  // Tooltip and jump-to-row for the dots.
  let tip = null;
  doc.addEventListener('mouseover', (e) => {
    const dot = e.target.closest?.('.st-ev');
    if (!dot) return;
    const d = JSON.parse(dot.dataset.tip);
    tip?.remove();
    tip = doc.createElement('div');
    tip.className = 'st-tip';
    tip.innerHTML = `<b>${esc(d.n)}</b> <span class="st-num">${esc(d.t)}</span>${d.l.map((l) => `<div class="r">${esc(l)}</div>`).join('')}`;
    root.appendChild(tip);
    const r = dot.getBoundingClientRect();
    tip.style.left = `${Math.max(12, Math.min(win.innerWidth - tip.offsetWidth - 12, r.left - 20))}px`;
    tip.style.top = `${Math.max(8, r.top - tip.offsetHeight - 8)}px`;
  });
  doc.addEventListener('mouseout', (e) => {
    if (e.target.closest?.('.st-ev')) { tip?.remove(); tip = null; }
  });
  doc.addEventListener('click', (e) => {
    const dot = e.target.closest?.('.st-ev');
    if (!dot) return;
    const item = doc.querySelector(`.st-item[data-row-key="${dot.dataset.row}"]`);
    const row = item?.querySelector('.st-row');
    if (!row) return;
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    row.classList.remove('flash');
    void row.offsetWidth;
    row.classList.add('flash');
  });

  if (groupBy !== 'strategy') regroup();
  if (tapePlot || doc.getElementById('st-next-v')) {
    load();
    win.setInterval(() => { if (!doc.hidden) load(); }, 60000);
  }
}
