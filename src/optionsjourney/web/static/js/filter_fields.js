// The bot form's on/off settings (#134 Filters, #135 Exits, Alerts and Chase; Tracker2's
// toggles): a setting's boxes show only while it's on, and a box that belongs to one choice
// of a select (data-when="a b") only while that choice is picked, like an average's second
// box for Compare. Hidden boxes still go with the form, so nothing typed is lost; without
// this script every box shows.

// Whether a setting's boxes show: while it's on, or while it has a problem to fix.
export function fieldsShow({ on = false, problem = false } = {}) {
  return on || problem;
}

// Whether a box marked data-when="<choices>" shows, given the select's value.
export function showsFor(when, value) {
  return (when || '').split(' ').filter(Boolean).includes(value);
}

export function install(doc) {
  doc.querySelectorAll('[data-filter]').forEach(box => {
    const use = box.querySelector('[data-filter-use]');
    const fields = box.querySelector('[data-filter-fields]');
    if (!use || !fields) return;
    const show = () => {
      fields.hidden = !fieldsShow({ on: use.checked, problem: 'filterProblem' in box.dataset });
    };
    use.addEventListener('change', show);
    show();
  });
  doc.querySelectorAll('[data-choice]').forEach(row => {
    const by = row.querySelector('[data-choice-by]');
    const boxes = row.querySelectorAll('[data-when]');
    if (!by || !boxes.length) return;
    const show = () => boxes.forEach(box => { box.hidden = !showsFor(box.dataset.when, by.value); });
    by.addEventListener('change', show);
    show();
  });
}

if (typeof document !== 'undefined') install(document);
