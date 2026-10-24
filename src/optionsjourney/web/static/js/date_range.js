// The Journal's date picker (#284, Tracker2's daterangepicker): two months side by side
// beside the presets. A first click picks the first day, a second the last; the days
// fill the Custom Range boxes, and Apply sends them. Without this script the presets
// and the two date boxes still work.

const DAY = 86400000;
const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// "YYYY-MM-DD" for a UTC date, and back.
export function iso(d) {
  return d.toISOString().slice(0, 10);
}
export function parse(text) {
  return /^\d{4}-\d{2}-\d{2}$/.test(text || '') ? new Date(text + 'T00:00:00Z') : null;
}

// The 42 days (six weeks from a Sunday) a month's table shows, as "YYYY-MM-DD".
export function monthGrid(year, month) {
  const first = new Date(Date.UTC(year, month, 1));
  const start = first.getTime() - first.getUTCDay() * DAY;
  return Array.from({ length: 42 }, (_, i) => iso(new Date(start + i * DAY)));
}

// A click on `day`: the new [start, end]. The first click (or one after a whole range)
// starts again; a day before the start moves the start.
export function pick(start, end, day) {
  if (!start || end) return [day, ''];
  if (day < start) return [day, ''];
  return [start, day];
}

function setup(root) {
  const holder = root.querySelector('[data-calendars]');
  const startBox = root.querySelector('input[name="start"]');
  const endBox = root.querySelector('input[name="end"]');
  if (!holder || !startBox || !endBox) return;
  const initial = [startBox.value, endBox.value];
  // A preset's days (data-from/to), shaded until a day is clicked, as Tracker2 shows them
  let shownFrom = root.dataset.from || '';
  let shownTo = root.dataset.to || '';
  const today = new Date();
  const anchor = parse(endBox.value) || parse(startBox.value) || parse(shownTo) || parse(root.dataset.today) || new Date(Date.UTC(today.getFullYear(), today.getMonth(), 1));
  // The left month: the one before the range's end, so the end shows on the right
  let year = anchor.getUTCFullYear();
  let month = anchor.getUTCMonth() - 1;
  // The app's New York date (the demo's is made up), else this computer's
  const todayIso = root.dataset.today || iso(new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())));

  function table(y, m, arrows) {
    const wrap = document.createElement('div');
    wrap.className = 'date-range__month';
    const head = document.createElement('div');
    head.className = 'date-range__head';
    const title = document.createElement('span');
    const shown = new Date(Date.UTC(y, m, 1));
    title.textContent = `${MONTHS[shown.getUTCMonth()]} ${shown.getUTCFullYear()}`;
    for (const [side, step, label] of [['prev', -1, '‹'], ['next', 1, '›']]) {
      if (!arrows.includes(side)) continue;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `date-range__arrow date-range__arrow--${side}`;
      b.textContent = label;
      b.setAttribute('aria-label', side === 'prev' ? 'Earlier month' : 'Later month');
      b.addEventListener('click', () => { month += step; draw(); });
      head.appendChild(b);
    }
    head.appendChild(title);
    wrap.appendChild(head);
    const grid = document.createElement('div');
    grid.className = 'date-range__grid';
    WEEKDAYS.forEach((w) => {
      const c = document.createElement('span');
      c.className = 'date-range__weekday';
      c.textContent = w;
      grid.appendChild(c);
    });
    const inMonth = shown.getUTCMonth();
    const [start, end] = startBox.value || endBox.value ? [startBox.value, endBox.value] : [shownFrom, shownTo];
    monthGrid(shown.getUTCFullYear(), inMonth).forEach((day) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'date-range__day';
      b.textContent = String(Number(day.slice(8)));
      b.dataset.day = day;
      if (Number(day.slice(5, 7)) - 1 !== inMonth) b.classList.add('is-off');
      if (day === todayIso) b.classList.add('is-today');
      if (day === start || day === end) b.classList.add('is-picked');
      else if (start && end && day > start && day < end) b.classList.add('is-between');
      b.addEventListener('click', () => {
        [startBox.value, endBox.value] = pick(startBox.value, endBox.value, day);
        shownFrom = shownTo = '';
        draw();
      });
      grid.appendChild(b);
    });
    wrap.appendChild(grid);
    return wrap;
  }

  function draw() {
    holder.replaceChildren(table(year, month, ['prev']), table(year, month + 1, ['next']));
  }

  holder.hidden = false;
  draw();
  [startBox, endBox].forEach((box) => box.addEventListener('change', () => {
    const d = parse(endBox.value) || parse(startBox.value);
    if (d) { year = d.getUTCFullYear(); month = d.getUTCMonth() - 1; }
    draw();
  }));
  root.querySelector('[data-custom]')?.addEventListener('click', () => startBox.focus());
  root.querySelector('[data-cancel]')?.addEventListener('click', () => {
    [startBox.value, endBox.value] = initial;
    root.open = false;
    draw();
  });
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('[data-date-range]').forEach(setup);
}
