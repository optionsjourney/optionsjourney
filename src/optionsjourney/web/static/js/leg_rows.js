// The bot form's leg rows (#115; Tracker2's renderLegs): a row shows only the boxes its
// pick uses, a column only while some row uses it, Sell and Buy in their colours and
// the Target's unit beside it. Hidden boxes still go with the form, so nothing typed is
// lost; without this script every column shows.

// Which of From, Exact, Max offset and Fallback a leg picked this way reads
// (bots/strikes.py: Exact only for an offset; a max offset only on a premium leg with a
// From leg; #314: a fallback premium only on a sold premium leg while the bot has a
// min short width, and then it's required).
export function uses(pick, link, side = 'sell', minWidth = '') {
  const linked = link !== '' && link != null;
  return {
    from: pick === 'offset' || pick === 'premium',
    exact: pick === 'offset',
    max: pick === 'premium' && linked,
    fallback: pick === 'premium' && side === 'sell' && String(minWidth).trim() !== '',
  };
}

// The words beside the Target box.
export function unitFor(pick, underlying) {
  return { premium: '$', delta: 'Δ', offset: 'pts from its leg', spot: `pts from ${underlying}` }[pick] ?? '';
}

// #116 (Tracker2's Use leg groups): ticking it is + Add group, unticking merges every
// leg into the first group; either redraws the form and saves nothing.
export function groupsOp(checked) {
  return checked ? 'use-groups' : 'one-group';
}

function installGroups(doc) {
  const box = doc.querySelector('[data-use-groups]');
  if (!box) return;
  box.addEventListener('change', () => {
    const form = box.form;
    const op = Object.assign(doc.createElement('input'), { type: 'hidden', name: 'op', value: groupsOp(box.checked) });
    form.append(op);
    form.noValidate = true;  // like the form's other add and remove buttons (formnovalidate)
    form.requestSubmit();
  });
}

export function install(doc) {
  installGroups(doc);
  const table = doc.querySelector('[data-leg-rows]');
  if (!table) return;
  const underlying = table.dataset.underlying || '';
  const rows = [...table.querySelectorAll('[data-leg-row]')];
  const field = (row, name) => row.querySelector(`[data-leg="${name}"]`);
  const minWidth = doc.querySelector('[name="min_short_width"]');
  // #315: Min short width's switch; off hides its points and the Fallback column
  const widthSwitch = doc.querySelector('[data-min-width-switch]');
  const widthBox = doc.querySelector('[data-min-width-box]');
  const width = () => (!widthSwitch || widthSwitch.checked ? minWidth?.value ?? '' : '');

  const show = () => {
    const wanted = { from: false, exact: false, max: false, fallback: false };
    for (const row of rows) {
      const pick = field(row, 'pick').value;
      const link = field(row, 'link').value;
      const used = uses(pick, link, field(row, 'side').value, width());
      for (const col of Object.keys(wanted)) {
        wanted[col] ||= used[col];
        const cell = row.querySelector(`[data-leg-col="${col}"]`);
        for (const control of cell.querySelectorAll('select, input')) control.hidden = !used[col];
      }
      // Required only while shown: a hidden required box would block Save unseen.
      field(row, 'fallback').required = used.fallback;
      // Tracker2: a leg measured from another isn't also measured from the underlying.
      const spot = field(row, 'pick').querySelector('option[value="spot"]');
      spot.hidden = used.from && link !== '' && pick !== 'spot';
      const side = field(row, 'side');
      side.classList.toggle('leg-side--sell', side.value === 'sell');
      side.classList.toggle('leg-side--buy', side.value === 'buy');
      row.querySelector('[data-leg-unit]').textContent = unitFor(pick, underlying);
    }
    if (widthBox && widthSwitch) widthBox.hidden = !widthSwitch.checked;
    for (const [col, on] of Object.entries(wanted)) {
      table.querySelectorAll(`[data-leg-col="${col}"]`).forEach(el => { el.hidden = !on; });
    }
  };
  table.addEventListener('change', show);
  minWidth?.addEventListener('input', show);
  widthSwitch?.addEventListener('change', show);
  show();
}

if (typeof document !== 'undefined') install(document);
