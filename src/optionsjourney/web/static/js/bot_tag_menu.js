// The bots' select bar Tags menu: a tick per tag showing the ticked bots' tags (ticked:
// all have it; a dash: only some do). Tick to add, untick to remove, type a new one, then
// Apply sends just the changes as the add / remove lists automation.tag_many reads.

// How a tag stands across the ticked bots: 'all', 'some' or 'none'.
export function stateOf(tag, botTags) {
  const key = tag.toLowerCase();
  const n = botTags.filter(tags => tags.some(t => t.toLowerCase() === key)).length;
  return n === 0 ? 'none' : n === botTags.length ? 'all' : 'some';
}

// The changes: rows the user moved off where they started. A dash left alone is no change.
export function changes(rows) {
  const add = [], remove = [];
  for (const { tag, start, now } of rows) {
    if (now === 'all' && start !== 'all') add.push(tag);
    if (now === 'none' && start !== 'none') remove.push(tag);
  }
  return { add, remove };
}

export function install(doc) {
  const form = doc.getElementById('tag-form');
  const menu = doc.getElementById('tag-menu');
  if (!form || !menu) return;
  const list = menu.querySelector('.dropdown__menu');
  const newTag = menu.querySelector('[data-new-tag]');
  const ticks = () => [...list.querySelectorAll('input[type=checkbox]')];
  const botTags = () => [...doc.querySelectorAll('input[type=checkbox][form=skip-form]:checked')]
    .map(b => (b.dataset.tags || '').split(',').filter(Boolean));

  const show = () => {
    const bots = botTags();
    ticks().forEach(t => {
      t.dataset.start = bots.length ? stateOf(t.value, bots) : 'none';
      t.checked = t.dataset.start === 'all';
      t.indeterminate = t.dataset.start === 'some';
    });
  };
  menu.addEventListener('toggle', () => { if (menu.open) show(); });

  const addNew = () => {
    const name = newTag.value.trim();
    newTag.value = '';
    if (!name) return;
    const have = ticks().find(t => t.value.toLowerCase() === name.toLowerCase());
    if (have) { have.indeterminate = false; have.checked = true; return; }
    const label = doc.createElement('label');
    label.className = 'check';
    const tick = Object.assign(doc.createElement('input'), { type: 'checkbox', value: name, checked: true });
    tick.dataset.start = 'none';
    label.append(tick, doc.createTextNode(` ${name}`));
    newTag.before(label);
  };
  // Enter in New tag adds it to the list; Apply sends.
  newTag.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addNew(); } });

  form.addEventListener('submit', () => {
    addNew();
    const rows = ticks().map(t => ({
      tag: t.value, start: t.dataset.start || 'none',
      now: t.indeterminate ? 'some' : t.checked ? 'all' : 'none',
    }));
    const { add, remove } = changes(rows);
    form.elements.add.value = add.join(',');
    form.elements.remove.value = remove.join(',');
  });
  doc.addEventListener('click', e => { if (!menu.contains(e.target)) menu.open = false; });
}

if (typeof document !== 'undefined') install(document);
