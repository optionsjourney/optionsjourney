// The bot form's profit actions (#128): + Add profit action copies the last row as a blank
// one (up to 10, bot_store._profit_count) and counts it in the form. Without this script
// the form shows the saved rows and one blank row, and a saved form adds another.

export const MOST = 10;

// A row's field name moved to row `to`: pa-<group>-<row>-<part>.
export function renamed(name, to) {
  return name.replace(/^(pa-\d+-)\d+(-)/, `$1${to}$2`);
}

export function install(doc) {
  doc.querySelectorAll('[data-profit]').forEach(box => {
    const button = box.querySelector('[data-add-profit]');
    const body = box.querySelector('[data-profit-rows]');
    const count = box.querySelector('[data-profit-count]');
    if (!button || !body || !count) return;
    const rows = () => body.querySelectorAll('[data-profit-row]');
    const refresh = () => { button.hidden = rows().length >= MOST; };
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
      body.append(row);
      count.value = String(at + 1);
      refresh();
      row.querySelector('input')?.focus();
    });
    refresh();
  });
}

if (typeof document !== 'undefined') install(document);
