// Tag boxes on the select bars (the bots', the Journal's): the comma box becomes a
// dropdown of every known tag with a tick box each, so several tags are picked, not
// typed. The comma box stays in the form, hidden, and is kept in step, so the form
// sends tags the same way with or without this. Tags already in the box start ticked;
// data-tag-fixed names tags the trade has by rule (its bot's, Stocks): ticked, locked,
// counted in the summary, but never sent.

// The summary for the ticked tags.
export function labelFor(picked, empty) {
  if (!picked.length) return empty;
  return picked.length <= 2 ? picked.join(', ') : `${picked.length} tags`;
}

// The comma box's tags, as names.
export function parseTags(value) {
  return value.split(',').map(t => t.trim()).filter(Boolean);
}

export function install(doc) {
  doc.querySelectorAll('input[data-tag-list]').forEach(box => {
    const names = box.list ? [...box.list.options].map(o => o.value) : [];
    if (!names.length) return;
    const empty = box.dataset.tagList || 'Tags';
    const start = new Set(parseTags(box.value).map(t => t.toLowerCase()));
    let fixed = [];
    try { fixed = JSON.parse(box.dataset.tagFixed || '[]'); } catch { fixed = []; }
    const menu = doc.createElement('details');
    menu.className = 'dropdown tag-list';
    const summary = doc.createElement('summary');
    summary.className = 'button button--quiet';
    const list = doc.createElement('div');
    list.className = 'dropdown__menu';
    const ticks = names.map(name => {
      const label = doc.createElement('label');
      label.className = 'check';
      const tick = Object.assign(doc.createElement('input'), { type: 'checkbox', value: name });
      tick.checked = start.has(name.toLowerCase()) || fixed.includes(name);
      if (fixed.includes(name)) {
        tick.disabled = true;
        label.title = 'Its bot files its trades under this tag';
      }
      label.append(tick, doc.createTextNode(` ${name}`));
      list.append(label);
      return tick;
    });
    const sync = () => {
      const picked = ticks.filter(t => t.checked).map(t => t.value);
      box.value = picked.filter(t => !fixed.includes(t)).join(', ');
      summary.textContent = labelFor(picked, empty);
    };
    list.addEventListener('change', sync);
    box.form?.addEventListener('reset', () => { ticks.forEach(t => { t.checked = t.disabled; }); sync(); });
    // Close when clicking elsewhere.
    doc.addEventListener('click', e => { if (!menu.contains(e.target)) menu.open = false; });
    menu.append(summary, list);
    box.hidden = true;
    box.after(menu);
    sync();
  });
}

if (typeof document !== 'undefined') install(document);
