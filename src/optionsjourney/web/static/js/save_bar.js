// The bot form's Save row floats at the bottom of the window once anything in the
// form is changed, so Save is in reach without scrolling to the end.

const form = document.querySelector('form.bot-form');
const bar = form?.querySelector('.bot-save-bar');
if (form && bar) {
  const changed = () => bar.classList.add('bot-save-bar--floating');
  form.addEventListener('input', changed);
  form.addEventListener('change', changed);
}
