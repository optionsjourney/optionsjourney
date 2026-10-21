// The run page's watch-exit banner (bots slice 20; Tracker2's automation_watch_banner.js
// and its 2 s tick in _run_detail_body.html). Reads GET …/watch-status, which says the
// banner's tone and text; it only reads. Every tick sets the element's whole class, so a
// class one state set never leaks into the next.

export const BASE = 'notice watch-banner';

// {hidden, text, className} for a watch-status payload's banner.
export function bannerView(banner) {
  if (!banner) return { hidden: true, text: '', className: `${BASE} notice--quiet` };
  const tone = ['danger', 'warning', 'quiet'].includes(banner.tone) ? banner.tone : 'warning';
  return { hidden: false, text: banner.text || '', className: `${BASE} notice--${tone}` };
}

export function install(doc, win) {
  const el = doc.getElementById('watch-banner');
  if (!el || el.dataset.live !== '1') return;
  let timer = null;
  async function tick() {
    if (doc.hidden) return;
    try {
      const res = await win.fetch(el.dataset.url, { headers: { Accept: 'application/json' } });
      if (!res.ok) return;
      const data = await res.json();
      const view = bannerView(data.banner);
      el.hidden = view.hidden;
      el.textContent = view.text;
      el.className = view.className;
      if (!data.open && timer) { win.clearInterval(timer); timer = null; }
    } catch (e) { /* the next tick tries again; the last banner stays */ }
  }
  timer = win.setInterval(tick, 2000);
  tick();
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
