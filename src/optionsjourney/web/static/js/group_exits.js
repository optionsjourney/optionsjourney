// The bot form's stops and targets (#118; Tracker2's renderGroups, renderExitUnits and
// applyStopLossCoMode): a box shows only while the choice it belongs to is made. Hidden
// boxes still go with the form, so nothing typed is lost; without this script every box
// shows.

// Whether a box marked data-exit-show="<part>" shows, given its choices.
export function shows(part, { stop = '', remainder = '', useProfit = false, coMode = 'off', pairedTo = false,
  stopUnit = 'pct', basis = '' } = {}) {
  return {
    'rest-stop': String(stop).trim() !== '',  // #126: only a stop can rest
    'watched-note': String(stop).trim() !== '',
    basis: stopUnit !== 'usd',               // #124: a % stop names what it's a % of
    'co-hint': stopUnit !== 'usd' && basis === 'closing_order',
    remainder: pairedTo,                     // read only when another group's stop closes it
    limit: remainder === 'close_limit',      // a typed limit is read only then
    pair: String(stop).trim() !== '',        // only a stop that fills closes another group
    profit: useProfit,
    'co-amount': coMode !== 'off',
  }[part] ?? true;
}

// What a group is called wherever it's named (the template's `name or 'Group N'`).
export function groupLabel(name, position) {
  const typed = String(name ?? '').trim();
  return typed || `Group ${Number(position) + 1}`;
}

// #119: a renamed group shows its new name as it's typed, in Belongs to on the legs,
// in the other groups' "also close" and over its profit actions.
function installNames(doc) {
  doc.querySelectorAll('[data-group-name]').forEach(box => {
    const position = box.dataset.groupName;
    box.addEventListener('input', () => {
      const label = groupLabel(box.value, position);
      doc.querySelectorAll(`[data-group-label="${position}"]`).forEach(el => { el.textContent = label; });
    });
  });
}

// Tracker2's closing-order line: what a $5.00 entry would stop at.
export function coHint(amount) {
  const off = Number(String(amount ?? '').trim());
  const typed = String(amount ?? '').trim() !== '' && Number.isFinite(off);
  return typed
    ? `Dollars off the whole trade's entry credit: a $5.00 entry would stop at $${(5 - off).toFixed(2)}.`
    : "Dollars off the whole trade's entry credit: a $5.00 entry would stop at $4.80 with 0.20.";
}

// Some group's "When its stop fills, also close" names the group at `position`.
function pairedTo(doc, position) {
  return [...doc.querySelectorAll('select[name$="-pair"]')].some(pick => pick.value === position);
}

function apply(scope, choices) {
  scope.querySelectorAll('[data-exit-show]').forEach(el => {
    el.hidden = !shows(el.dataset.exitShow, choices);
  });
  const hint = scope.querySelector('[data-co-hint]');
  if (hint) hint.textContent = coHint(choices.stop);
}

export function install(doc) {
  installNames(doc);
  const value = (scope, name) => scope.querySelector(`[data-exit="${name}"]`);
  const parts = [
    ...[...doc.querySelectorAll('[data-group-row]')].map(row => [row, () => ({
      stop: value(row, 'stop')?.value ?? '',
      remainder: value(row, 'remainder')?.value ?? '',
      pairedTo: pairedTo(doc, row.dataset.groupRow),
      stopUnit: value(row, 'stop-unit')?.value ?? 'pct',
      basis: value(row, 'stop-basis')?.value ?? '',
      // #120: a panel holds its group's profit actions, so it reads their box too
      useProfit: value(row, 'use-profit')?.checked ?? false,
    })]),
    ...[...doc.querySelectorAll('[data-profit]')].map(box => [box, () => ({
      useProfit: value(box, 'use-profit').checked,
    })]),
    ...[...doc.querySelectorAll('[data-closing-order]')].map(box => [box, () => ({
      coMode: value(box, 'co-mode').value,
    })]),
  ];
  for (const [scope, read] of parts) {
    const update = () => apply(scope, read());
    scope.addEventListener('change', update);
    scope.addEventListener('input', update);
    update();
  }
  // A pairing picked on one group shows the other's remainder: redraw every row.
  const table = doc.querySelector('[data-group-exits]');
  table?.addEventListener('change', () => {
    for (const [scope, read] of parts) if (scope.matches('[data-group-row]')) apply(scope, read());
  });
}

if (typeof document !== 'undefined') install(document);
