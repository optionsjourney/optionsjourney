// The bot form's Watch exits (#136; Tracker2's renderDeltaExits and its short-tested rows).
// Every leg has a delta row; only those with a level show, Add leg delta exit shows the
// next, ✕ clears and hides one, and picking another leg moves the levels to that leg. The
// short put and short call rows show only while a leg sells one (or the row has a number),
// and unticking one leaves its box out of the form, which turns it off. Without this script
// every row shows and a blank box is off.

// Whether a leg's delta row shows: while either level is filled in.
export function rowShows(below, above) {
  return String(below ?? '').trim() !== '' || String(above ?? '').trim() !== '';
}

// The legs a row may switch to: its own, and any leg with no row showing.
export function choices(positions, own, showing) {
  return positions.filter(p => p === own || !showing.includes(p));
}

// Which shorts the legs sell, from [{side, right}].
export function shortsOf(legs) {
  return new Set(legs.filter(l => l.side === 'sell').map(l => l.right));
}

function installDeltas(doc) {
  const host = doc.querySelector('[data-leg-deltas]');
  if (!host) return;
  const rows = [...host.querySelectorAll('[data-leg-delta]')];
  const add = host.querySelector('[data-leg-delta-add]');
  const pos = row => Number(row.dataset.legDelta);
  const boxes = row => row.querySelectorAll('input[type=text]');
  const positions = rows.map(pos);
  const refresh = () => {
    const showing = rows.filter(r => !r.hidden).map(pos);
    rows.forEach(row => {
      const allowed = choices(positions, pos(row), showing);
      row.querySelectorAll('[data-leg-delta-pick] option').forEach(o => {
        o.disabled = !allowed.includes(Number(o.value));
      });
    });
    add.hidden = showing.length === rows.length;
  };
  rows.forEach(row => {
    const [below, above] = boxes(row);
    row.hidden = !('keep' in host.dataset) && !rowShows(below.value, above.value);
    row.querySelector('[data-leg-delta-remove]').hidden = false;
    row.querySelector('[data-leg-delta-remove]').addEventListener('click', () => {
      boxes(row).forEach(b => { b.value = ''; });
      row.hidden = true;
      refresh();
    });
    const pick = row.querySelector('[data-leg-delta-pick]');
    pick.addEventListener('change', () => {
      const to = rows.find(r => pos(r) === Number(pick.value));
      pick.value = String(pos(row));
      if (!to || to === row) return;
      const [toBelow, toAbove] = boxes(to);
      [toBelow.value, toAbove.value] = [below.value, above.value];
      below.value = above.value = '';
      row.hidden = true;
      to.hidden = false;
      refresh();
      to.querySelector('[data-leg-delta-pick]').focus();
    });
  });
  add.addEventListener('click', () => {
    const next = rows.find(r => r.hidden);
    if (next) next.hidden = false;
    refresh();
    next?.querySelector('input[type=text]')?.focus();
  });
  refresh();
}

function installShorts(doc) {
  const rows = [...doc.querySelectorAll('[data-short-tested]')];
  if (!rows.length) return;
  const none = doc.querySelector('[data-no-shorts]');
  const legs = () => [...doc.querySelectorAll('[data-leg-row]')].map(tr => ({
    side: tr.querySelector('select[name$="-side"]')?.value,
    right: tr.querySelector('select[name$="-right"]')?.value,
  }));
  const show = () => {
    const shorts = shortsOf(legs());
    rows.forEach(row => {
      const box = row.querySelector('input[type=text]');
      const use = row.querySelector('[data-tested-use]');
      row.hidden = !shorts.has(row.dataset.shortTested) && box.value.trim() === '';
      box.disabled = !use.checked;
    });
    if (none) none.hidden = shorts.size > 0;
  };
  rows.forEach(row => row.querySelector('[data-tested-use]').addEventListener('change', show));
  doc.querySelectorAll('[data-leg-row] select').forEach(s => s.addEventListener('change', show));
  show();
}

export function install(doc) {
  installDeltas(doc);
  installShorts(doc);
}

if (typeof document !== 'undefined') install(document);
