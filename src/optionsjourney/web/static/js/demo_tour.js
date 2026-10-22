// The demo's tour box: one step at a time, polled from GET /demo/tour. Next and Skip tour
// post to /demo/tour/next and /demo/tour/skip. Escape hides the box until the page loads
// again (the banner's Restart tour posts a form, so it reloads). Server side:
// web/routes/demo.py, demo/tour.py.

const POLL_MS = 3000;
const HIGHLIGHT = 'demo-tour-highlight';

function esc(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// A link is used only when it stays on this site ("/path", not "//host") or goes to https.
function safeLink(link) {
  const s = String(link || '');
  return (s.startsWith('/') && !s.startsWith('//')) || s.startsWith('https://');
}

export function boxHtml(view) {
  if (!view || view.skipped) return '';
  const parts = [];
  if (!view.finished) {
    parts.push(`<p class="demo-tour__step">Step ${esc(view.step)} of ${esc(view.total)}</p>`);
  }
  parts.push(`<h2 class="demo-tour__title">${esc(view.title)}</h2>`);
  parts.push(`<p class="demo-tour__text">${esc(view.text)}</p>`);
  if (safeLink(view.link)) {
    parts.push(
      `<p class="demo-tour__link"><a href="${esc(view.link)}">${esc(view.link_label || view.link)}</a></p>`
    );
  }
  const buttons = [];
  if (!view.finished) {
    buttons.push('<button type="button" class="demo-tour__button" data-tour-next>Next</button>');
  }
  buttons.push(
    '<button type="button" class="demo-tour__button demo-tour__button--quiet" data-tour-skip>Skip tour</button>'
  );
  parts.push(`<div class="demo-tour__buttons">${buttons.join('')}</div>`);
  return parts.join('');
}

function pathOf(link) {
  return String(link || '').split(/[?#]/)[0];
}

// The page element to point at: always on steps 1 and 2 (the nav, the bot list and the
// bot's own page), otherwise only on the page the step's link goes to.
export function highlightSelector(view, pathname) {
  if (!view || view.skipped || view.finished || !view.highlight) return null;
  if (view.step === 1 || view.step === 2) return view.highlight;
  return pathOf(view.link) === pathname ? view.highlight : null;
}

export function install(doc, win) {
  const box = doc.getElementById('demo-tour');
  if (!box || typeof win.fetch !== 'function') return;
  const url = box.dataset.tourUrl;
  let view = null;
  let shown = null; // the JSON last drawn
  let dismissed = false;

  function applyHighlight() {
    const selector = dismissed ? null : highlightSelector(view, win.location.pathname);
    const target = selector ? doc.querySelector(selector) : null;
    for (const el of doc.querySelectorAll(`.${HIGHLIGHT}`)) {
      if (el !== target) el.classList.remove(HIGHLIGHT);
    }
    if (target) target.classList.add(HIGHLIGHT);
  }

  function render() {
    const html = dismissed ? '' : boxHtml(view);
    if (html !== shown) {
      shown = html;
      box.innerHTML = html;
      box.hidden = html === '';
    }
    applyHighlight();
  }

  async function send(path, body) {
    try {
      const response = await win.fetch(path, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
      if (response.ok) {
        view = await response.json();
        render();
      }
    } catch {
      // The demo is going away or restarting; the next poll tries again.
    }
  }

  async function poll() {
    try {
      const response = await win.fetch(url, { credentials: 'same-origin' });
      if (!response.ok) return;
      view = await response.json();
      render();
    } catch {
      // Try again at the next poll.
    }
  }

  box.addEventListener('click', event => {
    const target = event.target.closest ? event.target.closest('button') : null;
    if (!target || !view) return;
    if (target.hasAttribute('data-tour-next')) send('/demo/tour/next', { from: view.step });
    else if (target.hasAttribute('data-tour-skip')) send('/demo/tour/skip');
  });
  doc.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || box.hidden) return;
    dismissed = true;
    render();
  });
  // Opening a bot's card on the list moves the highlight from the card to its Arm button.
  doc.addEventListener('click', () => win.setTimeout(applyHighlight, 0));

  poll();
  win.setInterval(poll, POLL_MS);
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
