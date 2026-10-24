// The Ideas drawer (#292): the trades you saved from the Trade page with Idea ☆, and how they
// would have done. Opened by any [data-ideas-open] button, built like the Pending drawer.
//
// Reads GET /api/ideas when it opens (the list), then /api/ideas/markets every 3 s while open and
// visible, each request given up after 5 s; an id the markets answer has that the list doesn't
// reloads the list, as does ↻. The ✎ note edit and Delete are inline (no browser dialogs) and send PATCH and
// DELETE /api/ideas/<id>. Trade it opens the Trade page with the idea. The toolbar button's count
// comes from /api/ideas?count=1 on load and after a delete.

import { cardView } from './model.js';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const EMPTY_HTML = 'No ideas yet. Build a trade on the Trade page and press <strong>Idea ☆</strong>.';
export const REMOVED = 'Removed from Ideas.';
const COLUMNS = ['Card', 'Entry', 'Now', 'P/L'];
const MARKETS_MS = 3000, GIVE_UP_MS = 5000;

export function installIdeasDrawer(doc, win) {
  const drawer = doc.getElementById('ideas-drawer');
  if (!drawer) return;
  const backdrop = drawer.querySelector('[data-ideas-backdrop]');
  const panel = drawer.querySelector('[data-ideas-panel]');
  const body = doc.getElementById('ideas-body');
  const statusEl = doc.getElementById('ideas-status');   // the header's Loading… / error
  const openers = [...doc.querySelectorAll('[data-ideas-open]')];

  let ideas = [];
  let loaded = false;
  let error = null;
  let edit = null;       // {id, kind: 'note'|'delete', value}: the one card with an inline control open
  let notice = '';       // the last action's message
  let token = 0;
  let timer = null;
  let opener = null;
  let hideTimer = null;

  const isOpen = () => !drawer.classList.contains('hidden');
  const visible = () => doc.visibilityState !== 'hidden';
  const getJson = async (url, opts = {}) => {
    const res = await win.fetch(url, { headers: { Accept: 'application/json' }, ...opts });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || data.message || `HTTP ${res.status}`);
    return data;
  };

  function setButton(count) {
    for (const b of openers) b.textContent = count > 0 ? `💡 Ideas (${count})` : '💡 Ideas';
  }

  async function refreshCount() {
    try { setButton((await getJson(`${drawer.dataset.ideasUrl}?count=1`)).count); } catch { /* keep the last label */ }
  }

  async function load() {
    const mine = ++token;
    try {
      const d = await getJson(drawer.dataset.ideasUrl);
      if (mine !== token) return;
      ideas = d.ideas;
      error = null;
      setButton(ideas.length);
    } catch (e) {
      if (mine !== token) return;
      error = e.message;
    }
    loaded = true;
    draw();
  }

  // The live cells keyed "id" (total P/L), "id:pct", "id:i:now", "id:i:pnl" are rewritten in place, so an
  // open note input keeps its focus.
  function paint() {
    const now = new Date();
    for (const cell of body.querySelectorAll('[data-live]')) {
      const parts = cell.dataset.live.split(':');
      const [id] = parts;
      const idea = ideas.find(x => String(x.id) === id);
      if (!idea) continue;
      const v = cardView(idea, now);
      const card = parts.length === 3;
      const what = card ? parts[2] : parts[1] || 'pnl';
      const row = card ? v.cards[Number(parts[1])] : v;
      if (!row) continue;
      cell.textContent = what === 'now' ? row.now : what === 'pct' ? row.pct : row.pnl;
      if (what !== 'now') cell.className = toneClass(card ? 'idea-num' : what === 'pct' ? 'idea-pnl__pct' : 'idea-pnl', row.tone);
    }
  }

  async function refreshMarkets() {
    if (!isOpen() || !visible() || !loaded || !ideas.length) return;
    const ctl = new AbortController();
    const giveUp = win.setTimeout(() => ctl.abort(), GIVE_UP_MS);
    try {
      const d = (await getJson(drawer.dataset.marketsUrl, { signal: ctl.signal })).ideas || {};
      if (Object.keys(d).some(id => !ideas.some(x => String(x.id) === id))) { load(); return; }
      ideas = ideas.map(x => {
        const m = d[x.id];
        if (!m) return x;
        return { ...x, pnl: m.pnl, pnl_pct: m.pnl_pct, cards: x.cards.map((c, i) => ({ ...c, ...(m.cards[i] || {}) })) };
      });
      paint();
    } catch { /* the cells keep their last values */ } finally { win.clearTimeout(giveUp); }
  }

  const toneClass = (base, tone) => `${base} ${tone || 'idea-flat'}`;

  function actionsHtml(v) {
    const attrs = act => `data-act="${act}" data-id="${esc(v.id)}"`;
    const trade = v.canTrade ? `<button type="button" class="idea-link" ${attrs('trade')}>Trade it ↗</button>` : '';
    if (edit && edit.id === v.id && edit.kind === 'delete') {
      return `${trade}<span class="idea-foot__right"><span class="muted">Delete this idea?</span>
        <button type="button" class="idea-text-btn idea-text-btn--danger" ${attrs('delete-confirm')}>Confirm delete</button>
        <button type="button" class="idea-text-btn" ${attrs('delete-keep')}>Keep</button></span>`;
    }
    return `${trade}<span class="idea-foot__right"><button type="button" class="idea-text-btn idea-text-btn--delete" ${attrs('delete')}>Delete</button></span>`;
  }

  function noteHtml(v) {
    if (edit && edit.id === v.id && edit.kind === 'note') {
      return `<div class="idea-note idea-note--edit"><input type="text" maxlength="500" value="${esc(edit.value)}" data-act="note-input"
          data-id="${esc(v.id)}" aria-label="Note" placeholder="Add a note">
        <button type="button" class="mini-btn mini-btn--blue" data-act="note-save" data-id="${esc(v.id)}">Save</button>
        <button type="button" class="mini-btn mini-btn--gray" data-act="note-cancel" data-id="${esc(v.id)}">Cancel</button></div>`;
    }
    return `<div class="idea-note">${v.note ? esc(v.note) : '<span class="idea-faint">No note</span>'}
      <button type="button" class="idea-pencil" data-act="note" data-id="${esc(v.id)}" title="Edit the note" aria-label="Edit the note">✎</button></div>`;
  }

  function cardHtml(idea, now) {
    const v = cardView(idea, now);
    const rows = v.cards.map((c, i) => `<tr>
      <td data-label="${COLUMNS[0]}">${esc(c.label)} ×${esc(c.qty)}</td>
      <td data-label="${COLUMNS[1]}" class="idea-num">${esc(c.entry)}${c.locked ? ' <span title="Your limit price">🔒</span>' : ''}</td>
      <td data-label="${COLUMNS[2]}" class="idea-num" data-live="${esc(v.id)}:${i}:now">${esc(c.now)}</td>
      <td data-label="${COLUMNS[3]}" class="${toneClass('idea-num', c.tone)}" data-live="${esc(v.id)}:${i}:pnl">${esc(c.pnl)}</td></tr>`).join('');
    const meta = [v.saved, v.age, v.spot].filter(Boolean).map(esc).join(' · ');
    return `<article class="idea-card">
      <div class="idea-card__top">
        <span class="idea-card__ticker">${esc(v.ticker)}</span>
        <span class="idea-card__label">${esc(v.label)}</span>
        <span class="idea-chip idea-chip--${esc(v.status)}">${esc(v.chip)}</span>
        <span class="idea-card__pnl"><span class="${toneClass('idea-pnl', v.tone)}" data-live="${esc(v.id)}">${esc(v.pnl)}</span>
          <span class="${toneClass('idea-pnl__pct', v.tone)}" data-live="${esc(v.id)}:pct">${esc(v.pct)}</span></span>
      </div>
      <div class="idea-card__meta">${meta}</div>
      ${noteHtml(v)}
      <table class="idea-table"><thead><tr>${COLUMNS.map((c, i) => `<th${i ? ' class="idea-num"' : ''}>${c}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>
      <div class="idea-foot">${actionsHtml(v)}</div>
    </article>`;
  }

  function setStatus(text, bad = false) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle('negative', bad);
  }

  function draw() {
    if (!loaded) { setStatus('Loading…'); body.innerHTML = '<p class="muted small">Loading…</p>'; return; }
    if (error) {
      setStatus("Couldn't load ideas", true);
      body.innerHTML = `<p class="pending-error">Couldn't load your ideas: ${esc(error)}</p>`;
      return;
    }
    setStatus(ideas.length === 1 ? '1 idea' : `${ideas.length} ideas`);
    const now = new Date();
    const note = notice ? `<p class="muted small">${esc(notice)}</p>` : '';
    body.innerHTML = note + (ideas.length ? `<div class="idea-list">${ideas.map(i => cardHtml(i, now)).join('')}</div>` : `<p class="muted">${EMPTY_HTML}</p>`);
  }

  async function send(id, method, payload, done) {
    edit = null;
    try {
      const res = await win.fetch(`${drawer.dataset.ideasUrl}/${encodeURIComponent(id)}`, {
        method, headers: { 'Content-Type': 'application/json' }, ...(payload ? { body: JSON.stringify(payload) } : {}),
      });
      const d = await res.json().catch(() => ({}));
      notice = res.ok && d.status !== 'error' ? done : (d.message || d.error || `Couldn't do that (HTTP ${res.status}).`);
    } catch (e) {
      notice = `Couldn't do that: ${e.message}`;
    }
    await load();
    refreshCount();
  }

  body.addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const { act: what, id } = b.dataset;
    const idea = ideas.find(x => String(x.id) === id);
    notice = '';
    if (what === 'note' && idea) edit = { id: idea.id, kind: 'note', value: idea.note || '' };
    else if (what === 'delete' && idea) edit = { id: idea.id, kind: 'delete' };
    else if (what === 'note-cancel' || what === 'delete-keep') edit = null;
    else if (what === 'note-save' && edit) return send(id, 'PATCH', { note: edit.value.trim() }, 'Note saved.');
    else if (what === 'delete-confirm') return send(id, 'DELETE', null, REMOVED);
    else if (what === 'trade' && idea && idea.status === 'live') {
      win.location.href = `/trade?idea=${encodeURIComponent(idea.id)}&ticker=${encodeURIComponent(idea.ticker)}`;
      return;
    } else return;
    draw();
  });
  body.addEventListener('input', e => {
    if (e.target.dataset && e.target.dataset.act === 'note-input' && edit) edit.value = e.target.value;
  });

  function stopPolling() { if (timer != null) win.clearInterval(timer); timer = null; }

  function open(e) {
    opener = e.currentTarget;
    edit = null; notice = ''; loaded = false;
    win.clearTimeout(hideTimer);
    drawer.classList.remove('hidden');
    win.requestAnimationFrame(() => {
      backdrop.classList.remove('opacity-0');
      panel.classList.remove('translate-x-full');
    });
    draw();
    load();
    stopPolling();
    timer = win.setInterval(refreshMarkets, MARKETS_MS);
  }

  function close() {
    if (!isOpen()) return;
    stopPolling();
    backdrop.classList.add('opacity-0');
    panel.classList.add('translate-x-full');
    hideTimer = win.setTimeout(() => drawer.classList.add('hidden'), 300);
    if (opener) opener.focus();
  }

  openers.forEach(btn => btn.addEventListener('click', open));
  drawer.querySelector('[data-ideas-close]').addEventListener('click', close);
  const refreshBtn = drawer.querySelector('[data-ideas-refresh]');
  if (refreshBtn) refreshBtn.addEventListener('click', () => { notice = ''; load(); });
  backdrop.addEventListener('click', close);
  doc.addEventListener('visibilitychange', () => { if (visible() && isOpen()) load(); });
  doc.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !isOpen()) return;
    close();
    e.stopImmediatePropagation();
  }, true);

  refreshCount();
}
