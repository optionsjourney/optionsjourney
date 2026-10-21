// The bot form's Schedule (#114; Tracker2's applyEntryToggles and renderEntryTimes): only
// what the choices use shows, entry times are added and removed by button, and a typed
// time is tidied the way the server reads it. Hidden fields still go with the form, so
// nothing typed is lost; without this script every field shows.

// bots/schedule.py normalize_time: 945 and 9:45 are 09:45; with seconds, 93015 and
// 9:30:15 are 09:30:15 and zero seconds are dropped. null when it isn't a time.
export function normalizeTime(value, seconds = false) {
  const raw = String(value ?? '').trim();
  let parts;
  if (raw.includes(':')) {
    parts = raw.split(':');
    if (!(parts.length === 2 || (seconds && parts.length === 3))) return null;
  } else if (/^\d+$/.test(raw) && ([3, 4].includes(raw.length) || (seconds && [5, 6].includes(raw.length)))) {
    const padded = raw.padStart(raw.length <= 4 ? 4 : 6, '0');
    parts = padded.match(/../g);
  } else {
    return null;
  }
  if (!/^\d{1,2}$/.test(parts[0]) || !parts.slice(1).every(p => /^\d{2}$/.test(p))) return null;
  const [hour, minute, second = 0] = parts.map(Number);
  if (hour > 23 || minute > 59 || second > 59) return null;
  const pad = n => String(n).padStart(2, '0');
  return `${pad(hour)}:${pad(minute)}` + (second ? `:${pad(second)}` : '');
}

// Which of the marked parts show, given the boxes ticked and the frequency picked.
export function shown(el, ticked, frequency) {
  const { shownBy, hiddenBy, frequency: wanted } = el.dataset;
  if (shownBy !== undefined) return ticked.has(shownBy);
  if (hiddenBy !== undefined) return !ticked.has(hiddenBy);
  if (wanted !== undefined) return wanted === frequency;
  return true;
}

export function install(doc) {
  const root = doc.querySelector('[data-schedule-choice]');
  if (!root) return;
  const form = root.closest('form');
  const marked = '[data-shown-by], [data-hidden-by], [data-frequency]';

  const apply = () => {
    const ticked = new Set([...root.querySelectorAll('input[type=checkbox]:checked')].map(cb => cb.name));
    const frequency = root.querySelector('select[name="frequency"]')?.value;
    root.querySelectorAll(marked).forEach(el => { el.hidden = !shown(el, ticked, frequency); });
  };
  root.addEventListener('change', apply);
  apply();

  // Tidy a time on leaving its box, so it shows what will be stored
  root.addEventListener('change', e => {
    const box = e.target.closest('[data-clock]');
    if (!box) return;
    const tidy = normalizeTime(box.value, box.dataset.clock === 'seconds');
    if (tidy) box.value = tidy;
  });

  // Entry time rows. The server reads time-0 to time-(times - 1) and drops blank ones,
  // so a removed row is simply gone and a new one takes the next number.
  const list = root.querySelector('.entry-times');
  const count = form.querySelector('input[name="times"]');
  const add = root.querySelector('[data-add-time]');
  if (!list || !count || !add) return;
  const rows = () => [...list.querySelectorAll('[data-entry-time]')];
  const blank = row => !row.querySelector('[data-clock]').value.trim();
  const sizing = form.querySelector('select[name="sizing"]');

  const clear = row => {
    row.querySelectorAll('input[type=text], input[type=hidden]').forEach(input => { input.value = ''; });
    row.querySelector('input[type=checkbox]').checked = true;
  };
  const addRow = () => {
    const k = Number(count.value);
    const row = rows()[0].cloneNode(true);
    row.querySelectorAll('[name]').forEach(el => { el.name = el.name.replace(/^time-\d+/, `time-${k}`); });
    row.querySelectorAll('[aria-label]').forEach(el => {
      el.setAttribute('aria-label', el.getAttribute('aria-label').replace(/\d+$/, String(k + 1)));
    });
    clear(row);
    list.appendChild(row);
    count.value = String(k + 1);
    sizing?.dispatchEvent(new Event('change'));  // sizing_choice.js hides its Contracts box
    row.querySelector('[data-clock]').focus();
  };

  // The server's spare blank rows give way to the button: keep one only when there's no time
  const filled = rows().filter(row => !blank(row));
  rows().filter(blank).forEach((row, i) => { if (filled.length || i > 0) row.remove(); });
  rows().forEach(row => { row.querySelector('[data-remove-time]').hidden = false; });
  add.hidden = false;

  add.addEventListener('click', addRow);
  list.addEventListener('click', e => {
    const button = e.target.closest('[data-remove-time]');
    if (!button) return;
    const row = button.closest('[data-entry-time]');
    if (rows().length > 1) row.remove();
    else clear(row);  // the last row empties rather than going, so Add has a row to copy
  });
}

if (typeof document !== 'undefined') install(document);
