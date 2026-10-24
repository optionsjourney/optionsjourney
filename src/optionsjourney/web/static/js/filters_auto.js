// The Journal's filters apply as they change (#285): a menu (account, status) at once,
// the Tags and Accounts tick lists when their dropdown closes (a click outside closes
// it), the search on Enter or on leaving the box. The date picker sends itself.

export function wire(form) {
  const submit = () => (form.requestSubmit ? form.requestSubmit() : form.submit());
  const inPicker = (el) => el.closest('[data-date-range]');
  form.addEventListener('change', (event) => {
    const el = event.target;
    if (inPicker(el)) return;
    const menu = el.closest('details.dropdown');
    if (menu) { menu.dataset.changed = '1'; return; } // applied when it closes
    submit();
  });
  form.querySelectorAll('details.dropdown').forEach((menu) => {
    if (inPicker(menu)) return;
    menu.addEventListener('toggle', () => {
      if (!menu.open && menu.dataset.changed) submit();
    });
  });
  document.addEventListener('click', (event) => {
    form.querySelectorAll('details.dropdown[open]').forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
}

if (typeof document !== 'undefined') {
  document.querySelectorAll('form[data-auto-apply]').forEach(wire);
}
