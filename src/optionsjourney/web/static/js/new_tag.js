// "+ New tag" under a bot's Tag picker (#108, Tracker2's initNewStrategy; a journal tag
// since decision 0072): makes the tag (or finds the one with that name) and picks it, so
// a first bot never has to leave for the journal.

// Where a new name goes among the picker's names, which are in name order.
export function placeFor(names, name) {
  const key = name.toLowerCase();
  const at = names.findIndex(n => n.toLowerCase() > key);
  return at === -1 ? names.length : at;
}

export function install(doc, win) {
  const box = doc.querySelector('.new-tag');
  const select = box && box.closest('form').querySelector(`select[name="${box.dataset.select}"]`);
  if (!select) return;
  const part = key => box.querySelector(`[data-${key}]`);
  const [open, form, name, create, error, hint] =
    ['open', 'form', 'name', 'create', 'error', 'form-hint'].map(part);
  const showError = msg => { error.textContent = msg || ''; error.hidden = !msg; };
  const setOpen = isOpen => {
    form.hidden = hint.hidden = !isOpen;
    open.hidden = isOpen;
    showError('');
    if (!isOpen) return;
    // Start from the bot's own name, as Tracker2 does.
    const botName = select.form.querySelector('input[name="name"]');
    if (!name.value && botName) name.value = botName.value.trim().slice(0, 60);
    name.focus();
  };
  const submit = async () => {
    const wanted = name.value.trim();
    if (!wanted) { showError('Give it a name.'); return; }
    create.disabled = true;
    try {
      const res = await win.fetch(box.dataset.url, {
        method: 'POST',
        headers: { Accept: 'application/json' },
        body: new win.URLSearchParams({ name: wanted }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.id) { showError(data.error || `Couldn't add it (${res.status}).`); return; }
      const options = [...select.options].slice(1);  // after "Pick one"
      if (!options.some(o => o.value === String(data.id))) {  // a tag that exists is picked
        const option = new win.Option(data.name, data.id);
        select.insertBefore(option, options[placeFor(options.map(o => o.text), data.name)] || null);
      }
      select.value = String(data.id);
      name.value = '';
      setOpen(false);
    } catch (e) {
      showError("Couldn't add it: the app didn't answer.");
    } finally {
      create.disabled = false;
    }
  };
  open.addEventListener('click', () => setOpen(true));
  part('cancel').addEventListener('click', () => setOpen(false));
  create.addEventListener('click', submit);
  name.addEventListener('keydown', e => {
    // Enter here adds the tag; it never saves the bot.
    if (e.key === 'Enter') { e.preventDefault(); submit(); }
    if (e.key === 'Escape') setOpen(false);
  });
  box.hidden = false;
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
