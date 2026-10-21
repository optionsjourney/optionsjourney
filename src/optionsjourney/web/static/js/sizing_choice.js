// Size each entry by (#111, decision 0050; Tracker2's allocation type): only the chosen
// way's box and note show. Hidden boxes still go with the form, so nothing typed is lost.

export function install(doc) {
  const choice = doc.querySelector('[data-sizing-choice] select[name="sizing"]');
  if (!choice) return;
  const show = () => {
    doc.querySelectorAll('[data-sizing]').forEach(el => { el.hidden = el.dataset.sizing !== choice.value; });
  };
  choice.addEventListener('change', show);
  show();
}

if (typeof document !== 'undefined') install(document);
