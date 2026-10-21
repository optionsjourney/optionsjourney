// The bot form's tag picker (#109, Tracker2's initTags): the bot's tags as chips, a menu
// of every tag your bots and your journal use (#110), and a New tag box. The comma box stays in the form, hidden,
// and is kept in step, so the form sends tags the same way with or without this.

const MAX_LEN = 40;  // definition.TAG_MAX_LEN

// Tag lists joined, duplicates ignoring case dropped (first spelling wins), sorted.
export function mergeTags(a, b) {
  const seen = new Map();
  for (const t of [...a, ...b]) if (t && !seen.has(t.toLowerCase())) seen.set(t.toLowerCase(), t);
  return [...seen.values()].sort((x, y) => x.toLowerCase().localeCompare(y.toLowerCase()));
}

// The tags in a comma box, as the server reads them.
export function parseTags(text) {
  return mergeTags([], text.split(',').map(clean));
}

function clean(raw) {
  return String(raw || '').split(/\s+/).filter(Boolean).join(' ').slice(0, MAX_LEN).trim();
}

// Adding a typed tag: an existing tag's spelling wins. Returns {tags} or {error}.
export function addTag(tags, known, raw, reserved, max) {
  const tag = clean(raw);
  if (!tag) return { error: 'Type a tag first.' };
  if (tag.includes(',')) return { error: 'One tag at a time: no commas.' };
  if (reserved.some(r => r.toLowerCase() === tag.toLowerCase())) {
    return { error: `${tag} is one of the list's own filters, so it can't be a tag.` };
  }
  const name = known.find(t => t.toLowerCase() === tag.toLowerCase())
    || tags.find(t => t.toLowerCase() === tag.toLowerCase()) || tag;
  if (tags.some(t => t.toLowerCase() === name.toLowerCase())) return { tags };
  if (tags.length >= max) return { error: `A bot can have up to ${max} tags.` };
  return { tags: mergeTags(tags, [name]) };
}

export function install(doc) {
  const root = doc.querySelector('.tag-picker');
  const box = root && root.querySelector('input[name="tags"]');
  if (!box) return;
  const read = key => { try { return JSON.parse(root.dataset[key] || '[]'); } catch (_) { return []; } };
  const reserved = read('reserved');
  const max = Number(root.dataset.max) || 20;
  let tags = parseTags(box.value);
  let known = mergeTags(read('known'), tags);

  const el = (tag, props = {}, ...kids) => {
    const e = Object.assign(doc.createElement(tag), props);
    e.append(...kids);
    return e;
  };
  const chips = el('div', { className: 'chips' });
  const menu = el('details', { className: 'tag-picker__menu' });
  const summary = el('summary', { className: 'link-button', textContent: 'Pick tags' });
  const list = el('div', { className: 'tag-picker__list' });
  menu.append(summary, list);
  const name = el('input', { type: 'text', maxLength: MAX_LEN, placeholder: 'New tag' });
  name.setAttribute('aria-label', 'New tag');
  const add = el('button', { type: 'button', className: 'button button--quiet', textContent: 'Add' });
  const error = el('p', { className: 'notice notice--warning small', hidden: true });
  const row = el('div', { className: 'inline-form' }, name, add);
  const showError = msg => { error.textContent = msg || ''; error.hidden = !msg; };

  const set = next => {
    tags = next;
    known = mergeTags(known, tags);
    box.value = tags.join(', ');
    render();
  };
  const render = () => {
    chips.replaceChildren(...(tags.length ? tags.map(tag => {
      const x = el('button', { type: 'button', className: 'chip__remove', textContent: '×' });
      x.setAttribute('aria-label', `Remove ${tag}`);
      x.addEventListener('click', () => { showError(''); set(tags.filter(t => t !== tag)); });
      return el('span', { className: 'chip chip--picked' }, tag, x);
    }) : [el('span', { className: 'muted small', textContent: 'No tags' })]));
    if (!known.length) {
      list.replaceChildren(el('span', { className: 'muted small', textContent: 'No tags yet. Type one below.' }));
      return;
    }
    list.replaceChildren(...known.map(tag => {
      const on = tags.some(t => t.toLowerCase() === tag.toLowerCase());
      const tick = el('input', { type: 'checkbox', checked: on });
      tick.addEventListener('change', () => {
        if (!tick.checked) { showError(''); set(tags.filter(t => t.toLowerCase() !== tag.toLowerCase())); return; }
        const result = addTag(tags, known, tag, reserved, max);
        showError(result.error);
        if (result.tags) set(result.tags); else tick.checked = false;
      });
      return el('label', { className: 'check' }, tick, ` ${tag}`);
    }));
  };
  const submit = () => {
    const result = addTag(tags, known, name.value, reserved, max);
    showError(result.error);
    if (!result.tags) return;
    name.value = '';
    set(result.tags);
  };
  add.addEventListener('click', submit);
  // Enter in the New tag box adds the tag; it never saves the bot.
  name.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); submit(); } });
  doc.addEventListener('click', e => { if (!menu.contains(e.target)) menu.open = false; });
  doc.addEventListener('keydown', e => { if (e.key === 'Escape') menu.open = false; });

  box.closest('label').hidden = true;
  root.append(chips, menu, row, error);  // after the Tags name and its ⓘ
  render();
}

if (typeof document !== 'undefined') install(document);
