// The alert rows (#158): + Add alert copies the last row as a blank one (up to the form's
// data-alert-most) and counts it in the form. Without this script the form shows the saved
// rows and one blank row, and a saved form adds another.
import { showsFor } from './filter_fields.js';

// A row's field name moved to row `to`: da-<row>-<part>.
export function renamed(name, to) {
  return name.replace(/^da-\d+-/, `da-${to}-`);
}

export function install(doc) {
  doc.querySelectorAll('[data-alerts]').forEach(box => {
    const button = box.querySelector('[data-add-alert]');
    const body = box.querySelector('[data-alert-rows]');
    const count = box.querySelector('[data-alert-count]');
    if (!button || !body || !count) return;
    const most = Number(box.dataset.alertMost) || 20;
    const rows = () => body.querySelectorAll('[data-alert-row]');
    const refresh = () => { button.hidden = rows().length >= most; };
    button.addEventListener('click', () => {
      const all = rows();
      const row = all[all.length - 1].cloneNode(true);
      const at = all.length;
      row.querySelectorAll('input, select').forEach(field => {
        field.name = renamed(field.name, at);
        if (field.tagName === 'SELECT') field.selectedIndex = 0;
        else field.value = '';
        const label = field.getAttribute('aria-label');
        if (label) field.setAttribute('aria-label', label.replace(/\d+/, String(at + 1)));
      });
      // The copy's kind picks its boxes, as filter_fields does for the rows it found
      const by = row.querySelector('[data-choice-by]');
      const boxes = row.querySelectorAll('[data-when]');
      const show = () => boxes.forEach(b => { b.hidden = !showsFor(b.dataset.when, by.value); });
      if (by) { by.addEventListener('change', show); show(); }
      body.append(row);
      count.value = String(at + 1);
      refresh();
      row.querySelector('select, input')?.focus();
    });
    refresh();
  });
}

if (typeof document !== 'undefined') install(document);
