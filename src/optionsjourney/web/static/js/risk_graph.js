// Risk graph: the enlarged P&L chart (state, drawing, sliders, price slices, Greeks), its live
// quotes, and the leg-subtitle helpers. Drawn by the automation pages' strategy previews from the
// markup in templates/_risk_graph_panel.html, so the element ids below exist there.
//
// A classic script, not a module: its top-level declarations share the global scope with each page's
// own inline script (the journal's grid view reads _riskLive, _legSubtitle and _popoutUrl).
// Needs window.__modeler (modeler/pricing.js + render.js) at call time, and the Socket.IO client for
// live quotes.
//
// OptionsJourney: Tracker2's risk_graph.js lines 1-658 as they are; the Analyzer link and pop-out
// helpers after _mountRiskGraphGroup are left out (docs/developer/charts.md).

// ── P&L Chart Modal ─────────────────────────────────────────────────────

let _plLegs = [];
let _plRealizedPl = 0;  // P&L already locked in from closed legs
let _plUnderlyingPrice = 0;
let _plTicker = '';
let _plAsOfMin = 0;   // Time slider: minutes elapsed from now toward front expiry
let _plFlatIV = null; // IV slider: flat IV fraction override, null = use each leg's own IV
let _plShowSD = true;     // ±1 SD expected-move band toggle
let _plShowGreeks = true; // Delta/Gamma/Theta/Vega row toggle
let _plSlices = [];       // [{id, price, isBreakeven}] -- draggable price markers
let _plAutoBreakevens = true; // keep a slice at each break-even as the legs change; off once they're all removed
let _plSliceCounter = 0;
let _plSlicesTableOpen = false; // slice readout table under the chart: collapsed by default (the chart's own slice labels carry price + P/L)

// Populates the _pl* module state from already-known legs/realizedPl/titleText and renders the
// big P&L modal. Shared by the normal single-position fetch path and any caller that already has
// leg data in hand (e.g. a grid-view combined card's expand button, which has no single position id).
function _populatePlChartModal(legs, realizedPl, titleText, subtitleText, currentUnderlyingPrice, ticker) {
    _plTicker = ticker || '';
    document.getElementById('pl-chart-title').textContent = titleText;

    // Set underlying price: live market price, or midpoint of strikes as fallback
    const strikes = legs.map(l => l.strike);
    const midStrike = (Math.min(...strikes) + Math.max(...strikes)) / 2;
    _plUnderlyingPrice = currentUnderlyingPrice || midStrike;

    const timeSlider = document.getElementById('pl-time-slider');
    timeSlider.value = 0;
    _plAsOfMin = 0;

    const ivSlider = document.getElementById('pl-iv-slider');
    ivSlider.value = 0; // sentinel: 0 means "no override" (min="0" so this never gets clamped)
    _plFlatIV = null;
    document.getElementById('pl-time-label').textContent = 'now';
    document.getElementById('pl-iv-label').textContent = 'model';

    _plShowSD = true;
    _plShowGreeks = true;
    _plSlices = [];
    _plAutoBreakevens = true;   // every graph opens with its break-evens sliced (placed by _setPlLegs)
    _syncPlToolButtons();

    document.getElementById('pl-chart-loading').classList.add('hidden');
    document.getElementById('pl-chart-content').classList.remove('hidden');

    _setPlLegs(legs, realizedPl, subtitleText);
}

// Swaps the legs being graphed (e.g. an order checked/unchecked in the pop-out) without resetting
// the sliders, slices or zoom: refreshes the subtitle and the Time slider's range for the new front
// expiry, re-points the live-quote subscription at the new legs, and redraws.
function _setPlLegs(legs, realizedPl, subtitleText) {
    _plLegs = legs;
    _plRealizedPl = realizedPl || 0;
    document.getElementById('pl-chart-subtitle').textContent = subtitleText ?? (legs.length ? _legSubtitle(legs) : '');

    // Time slider's range: 0 (now) to minutes-until-front-expiry. Front expiry = earliest
    // expiration_date among the legs, matching buildPayoffFns's own frontExpiry convention.
    const frontExpiry = _plLegs.map(l => l.expiration_date).sort()[0];
    const minutesToFront = frontExpiry
        ? Math.max(0, Math.round((window.__modeler.expiryMs(frontExpiry) - Date.now()) / 60000))
        : 0;
    const timeSlider = document.getElementById('pl-time-slider');
    timeSlider.max = minutesToFront;
    if ((parseInt(timeSlider.value, 10) || 0) > minutesToFront) {
        timeSlider.value = minutesToFront;
        _onPlSliderInput();   // relabels and redraws at the clamped time
    }

    if (_plAutoBreakevens) {
        _placePlBreakevenSlices();
        _syncPlToolButtons();
    }
    _drawPlChart();
    _riskLive.track('modal', _plLegs, _plTicker, spot => {
        if (spot) _plUnderlyingPrice = spot;
        _drawPlChart();
    });
}

// Pure redraw: reads _pl* module state, draws the canvas, and updates all stat/breakeven text.
// Called once on modal open (via _populatePlChartModal) and on every slider drag (via _onPlSliderInput).
// No fetch, no DOM rebuild beyond the canvas and these text nodes.
function _drawPlChart() {
    if (!_plLegs.length) return;

    const canvas = document.getElementById('pl-chart-canvas');
    const modelerLegs = _plLegs.map(window.__modeler.toModelerLeg);
    // Net cost basis must come from each leg's actual entry_price (already signed: +
    // received for a credit, - paid for a debit) — NOT from toModelerLeg's premium field
    // (the live market price) — otherwise the theoretical curve prices "cost to open this
    // position right now" instead of "what this position actually cost," and the two
    // nearly cancel against the live-priced T+0 curve, making every open position's
    // theoretical P&L read close to $0 regardless of its real unrealized P&L. Same formula
    // as the Cost Basis stat tile a few lines below — entry_price needs no extra sign flip,
    // it's already signed correctly.
    const entryNet = _plLegs.reduce((sum, leg) => sum + (leg.entry_price || 0) * leg.quantity * (leg.multiplier || 100), 0);
    const { stats, theoPl, greeks } = window.__modeler.renderPayoffChart(canvas, {
        legs: modelerLegs,
        spot: _plUnderlyingPrice || null,
        ticker: _plTicker,
        asOfMin: _plAsOfMin,
        flatIV: _plFlatIV,
        showBreakevens: true,
        showSD: _plShowSD,
        net: entryNet,
        sliceEnabled: true, slices: _plSlices,
        onSliceChange: (id, price) => _onPlSliceDrag(id, price),
        onSliceRemove: id => _removePlSlice(id),
    });

    _renderPlSlicesStrip(modelerLegs, entryNet);

    const greeksRow = document.getElementById('pl-greeks-row');
    if (_plShowGreeks && greeks) {
        greeksRow.classList.remove('hidden');
        document.getElementById('pl-greek-delta').textContent = greeks.delta.toFixed(2);
        document.getElementById('pl-greek-gamma').textContent = greeks.gamma.toFixed(2);
        const thetaEl = document.getElementById('pl-greek-theta');
        thetaEl.textContent = (greeks.theta >= 0 ? '+$' : '-$') + Math.abs(greeks.theta).toFixed(0);
        thetaEl.className = 'font-mono text-sm ' + (greeks.theta >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500 dark:text-red-400');
        document.getElementById('pl-greek-vega').textContent = greeks.vega.toFixed(0);
    } else {
        greeksRow.classList.add('hidden');
    }

    const fmtMoney = (v, forceSign) => {
        if (v === Infinity) return 'Unlimited';
        if (v === -Infinity) return '-Unlimited';
        const sign = v >= 0 ? (forceSign ? '+' : '') : '-';
        return sign + '$' + Math.abs(v).toFixed(0);
    };

    // % of max risk -- same convention as the hover tooltip (render.js): Math.abs(stats.maxLoss)
    // is the position's worst-case dollar loss, correct for both debit and credit structures.
    // Omitted for an undefined-risk position (maxLoss is -Infinity) or when there's no risk
    // basis to divide by (maxLoss === 0).
    const riskBasis = stats ? Math.abs(stats.maxLoss) : 0;
    const fmtPct = v => (riskBasis > 0 && isFinite(riskBasis)) ? ` (${v >= 0 ? '+' : '-'}${Math.abs(v / riskBasis * 100).toFixed(0)}%)` : '';

    const theoPlEl = document.getElementById('pl-header-theo-pl');
    if (theoPl != null) {
        const total = theoPl + _plRealizedPl;
        theoPlEl.textContent = fmtMoney(total, true) + fmtPct(total);
        theoPlEl.className = 'font-mono text-sm font-semibold ' + (total >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500 dark:text-red-400');
    } else {
        theoPlEl.textContent = '—';
        theoPlEl.className = 'font-mono text-sm font-semibold text-gray-900 dark:text-white';
    }

    if (!stats) {
        document.getElementById('pl-stat-cost').textContent = '—';
        document.getElementById('pl-stat-maxprofit').textContent = '—';
        document.getElementById('pl-stat-maxloss').textContent = '—';
        document.getElementById('pl-stat-ratio').textContent = '—';
        document.getElementById('pl-breakevens').textContent = '';
        return;
    }

    // Realized P&L is a fixed historical amount folded additively into the displayed
    // profit/loss figures (matching prior behavior) — NOT part of the curve/breakeven math,
    // since renderPayoffChart computes its own net from live legs only.
    const basis = _plLegs.reduce((acc, leg) => acc + leg.entry_price * leg.quantity * (leg.multiplier || 100), 0) + _plRealizedPl;
    const maxProfit = stats.maxProfit === Infinity ? Infinity : stats.maxProfit + _plRealizedPl;
    const maxLoss = stats.maxLoss === -Infinity ? -Infinity : stats.maxLoss + _plRealizedPl;

    // Inlined (not via fmtMoney): byte-for-byte match to the old code's cost-basis format,
    // which never prints a leading '-' for debits — the ' db' suffix alone conveys sign.
    document.getElementById('pl-stat-cost').textContent = (basis >= 0 ? '+' : '') + '$' + Math.abs(basis).toFixed(0) + (basis >= 0 ? ' cr' : ' db');
    document.getElementById('pl-stat-maxprofit').textContent = fmtMoney(maxProfit, true);

    const maxLossEl = document.getElementById('pl-stat-maxloss');
    const maxLossLabel = maxLossEl.previousElementSibling;
    if (maxLoss >= 0) {
        maxLossEl.textContent = fmtMoney(maxLoss, true);
        maxLossEl.className = 'font-mono text-sm text-green-600 dark:text-green-400';
        if (maxLossLabel) maxLossLabel.textContent = 'Min Gain (at expiry)';
    } else {
        maxLossEl.textContent = fmtMoney(maxLoss, false);
        maxLossEl.className = 'font-mono text-sm text-red-500 dark:text-red-400';
        if (maxLossLabel) maxLossLabel.textContent = 'Max Loss (at expiry)';
    }

    const ratio = (maxProfit > 0 && maxLoss < 0 && isFinite(maxProfit) && isFinite(maxLoss))
        ? (maxProfit / Math.abs(maxLoss)).toFixed(2) : '—';
    document.getElementById('pl-stat-ratio').textContent = ratio;

    const beText = stats.breakevens.length
        ? 'Breakevens: ' + stats.breakevens.map(b => b >= 100 ? Math.round(b).toLocaleString() : b.toFixed(2)).join(', ')
        : '';
    document.getElementById('pl-breakevens').textContent = beText;
}

// Active/inactive styling for the σ (expected move) / Δ (greeks) toggle buttons —
// mirrors ntflux's modeler_graph.js toolbar button states.
function _syncPlToolButtons() {
    const activeCls = 'bg-blue-100 dark:bg-blue-900/40 border-blue-500 text-blue-600 dark:text-blue-400';
    const inactiveCls = 'bg-gray-50 dark:bg-gray-900 border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400';
    const sliceBtn = document.getElementById('pl-add-slice');
    if (sliceBtn) sliceBtn.className = 'w-6 h-6 rounded border text-xs font-bold leading-none flex items-center justify-center cursor-pointer ' + (_plSlices.length ? activeCls : inactiveCls);
    const sdBtn = document.getElementById('pl-toggle-sd');
    sdBtn.className = 'w-6 h-6 rounded border text-xs font-bold leading-none flex items-center justify-center cursor-pointer ' + (_plShowSD ? activeCls : inactiveCls);
    const greeksBtn = document.getElementById('pl-toggle-greeks');
    greeksBtn.className = 'w-6 h-6 rounded border text-xs font-bold leading-none flex items-center justify-center cursor-pointer ' + (_plShowGreeks ? activeCls : inactiveCls);
}

function _togglePlSD() {
    _plShowSD = !_plShowSD;
    _syncPlToolButtons();
    _drawPlChart();
}

function _togglePlGreeks() {
    _plShowGreeks = !_plShowGreeks;
    _syncPlToolButtons();
    _drawPlChart();
}

// ── Price slices ─────────────────────────────────────────────────────────
// Ported from modeler_page.js's toolbar (itself ported from ntflux's Analyzer) --
// same {id, price, isBreakeven} shape and refresh/drag-untags-breakeven rules.

function _addPlSlice() {
    if (!(_plUnderlyingPrice > 0)) return;
    const inc = window.__modeler.strikeIncrement(_plTicker, [], null) || 5;
    let at = Math.floor(_plUnderlyingPrice / inc) * inc + inc;
    while (_plSlices.some(s => Math.abs(s.price - at) < inc * 0.5)) at += inc;
    _plSliceCounter++;
    _plSlices.push({ id: `plslice${_plSliceCounter}`, price: at });
    _syncPlToolButtons();
    _drawPlChart();
}

// Snapshots the position's break-even prices as slices -- tagged isBreakeven so a
// re-click refreshes them (removes previously-added, untouched ones and re-adds the
// current set) rather than stacking duplicates. A dragged break-even slice loses the
// tag (in _onPlSliceDrag) and survives a later refresh as an ordinary slice.
// The slice menu's "Add break-even slices": also turns the automatic ones back on.
function _addPlBreakevenSlices() {
    _plAutoBreakevens = true;
    _placePlBreakevenSlices();
    _syncPlToolButtons();
    _drawPlChart();
}

// Replaces the untouched break-even slices with ones at the current legs' break-evens. No redraw.
function _placePlBreakevenSlices() {
    if (!(_plUnderlyingPrice > 0) || !_plLegs.length) return;
    const modelerLegs = _plLegs.map(window.__modeler.toModelerLeg);
    const entryNet = _plLegs.reduce((sum, leg) => sum + (leg.entry_price || 0) * leg.quantity * (leg.multiplier || 100), 0);
    const gs = window.__modeler.buildPayoffFns({ ticker: _plTicker, legs: modelerLegs, net: entryNet }, _plUnderlyingPrice, null, _plAsOfMin, _plFlatIV);
    if (!gs) return;
    const stats = window.__modeler.curveStats(gs.payoff, gs.autoSmin, gs.autoSmax, gs.strikes || []);
    // An empty/degenerate position has a flat $0 payoff, and curveStats' y0===0 branch
    // would then report a "break-even" at every gridpoint -- bail unless the curve has
    // a real profit or loss excursion.
    if (!(stats.maxProfit > 0.01) && !(stats.maxLoss < -0.01)) return;
    const bes = (stats.breakevens || []).filter(p => p > 0);
    _plSlices = _plSlices.filter(s => !s.isBreakeven);
    const inc = window.__modeler.strikeIncrement(_plTicker, modelerLegs, null) || 5;
    bes.forEach(p => {
        if (_plSlices.some(s => Math.abs(s.price - p) < inc * 0.5)) return;
        _plSliceCounter++;
        _plSlices.push({ id: `plslice${_plSliceCounter}`, price: p, isBreakeven: true });
    });
}

function _removePlSlice(id) {
    const removed = _plSlices.find(s => s.id === id);
    _plSlices = _plSlices.filter(s => s.id !== id);
    // Removing the last break-even slice means the user doesn't want them: stop re-adding.
    if (removed && removed.isBreakeven && !_plSlices.some(s => s.isBreakeven)) _plAutoBreakevens = false;
    _syncPlToolButtons();
    _drawPlChart();
}

function _onPlSliceDrag(id, price) {
    const s = _plSlices.find(x => x.id === id);
    if (!s) return;
    s.price = price;
    s.isBreakeven = false;
    _drawPlChart();
}

// The ⊹ button opens a small 2-item menu instead of adding a slice directly, matching
// modeler_page.js's openSliceMenu().
function _openPlSliceMenu(btn) {
    const existing = document.getElementById('pl-slice-pop');
    if (existing) { existing.remove(); return; }
    const pop = document.createElement('div');
    pop.id = 'pl-slice-pop';
    const r = btn.getBoundingClientRect();
    const popW = 168; // matches min-w-[168px] below
    // This button sits in a right-aligned toolbar row (ml-auto), close to the modal's
    // right edge -- opening to the right (r.right + 8) routinely pushed the popover off
    // the viewport. Flip to the left of the button whenever there isn't enough room.
    const left = (r.right + 8 + popW > window.innerWidth) ? Math.round(r.left - 8 - popW) : Math.round(r.right + 8);
    pop.style.cssText = `position:fixed;left:${Math.max(4, left)}px;top:${Math.round(r.top)}px;z-index:9999`;
    pop.className = 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-1 shadow-lg min-w-[168px]';
    const item = 'block w-full text-left text-xs px-2.5 py-1.5 rounded text-gray-900 dark:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer';
    pop.innerHTML =
        `<button class="${item}" onclick="_addPlSlice();document.getElementById('pl-slice-pop')?.remove()">Add price slice</button>` +
        `<button class="${item}" onclick="_addPlBreakevenSlices();document.getElementById('pl-slice-pop')?.remove()">Add break-even slices</button>`;
    document.body.appendChild(pop);
    setTimeout(() => {
        const close = e => {
            if (!pop.contains(e.target) && e.target !== btn) { pop.remove(); document.removeEventListener('mousedown', close); }
        };
        document.addEventListener('mousedown', close);
    }, 0);
}

function _togglePlSlicesTable() {
    _plSlicesTableOpen = !_plSlicesTableOpen;
    _drawPlChart();
}

// Renders the price-slice readout strip below the Greeks row -- price + at-expiry P&L
// per slice, matching modeler_page.js's own strip (minus the Greeks columns, since this
// modal already has its own dedicated spot-only Greeks row above).
function _renderPlSlicesStrip(modelerLegs, entryNet) {
    const el = document.getElementById('pl-slices-strip');
    if (!el) return;
    if (!_plSlices.length) { el.classList.add('hidden'); el.innerHTML = ''; return; }
    el.classList.remove('hidden');
    const toggle = `<button onclick="_togglePlSlicesTable()" title="${_plSlicesTableOpen ? 'Hide' : 'Show'} the price slice table"`
        + ` class="text-[9px] uppercase tracking-wide text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 cursor-pointer ${_plSlicesTableOpen ? 'mb-1' : ''}">`
        + `${_plSlicesTableOpen ? '▾' : '▸'} Price slices (${_plSlices.length})</button>`;
    if (!_plSlicesTableOpen) { el.innerHTML = toggle; return; }
    const gs = window.__modeler.buildPayoffFns({ ticker: _plTicker, legs: modelerLegs, net: entryNet }, _plUnderlyingPrice, null, _plAsOfMin, _plFlatIV);
    const fmt$ = v => isFinite(v) ? (v >= 0 ? '+$' : '-$') + Math.abs(Math.round(v)).toLocaleString() : '—';
    const th = 'px-2 py-1 text-right text-gray-500 dark:text-gray-400 font-medium';
    const td = 'px-2 py-1 text-right tabular-nums';
    const rows = [...(_plSlices || [])].sort((a, b) => a.price - b.price);
    let html = toggle;
    html += `<table class="w-full border-collapse text-xs"><tr class="border-b border-gray-200 dark:border-gray-700">`
        + `<th class="${th} text-left">Price</th><th class="${th}">P/L</th></tr>`;
    rows.forEach(({ price, id }) => {
        const pl = gs ? gs.payoff(price) : null;
        const pxl = price >= 100 ? Math.round(price).toLocaleString() : (+price).toFixed(2);
        html += `<tr><td class="${td} text-left"><span class="whitespace-nowrap">${pxl}`
            + `<button onclick="_removePlSlice('${id}')" title="Remove this price slice" class="ml-1.5 px-1 leading-none text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400 cursor-pointer">&times;</button></span></td>`
            + `<td class="${td} ${pl >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}">${pl != null ? fmt$(pl) : '—'}</td></tr>`;
    });
    html += `</table>`;
    el.innerHTML = html;
}

// Minutes remaining until front expiry, formatted like Option Omega's slider label
// ("9h 16m", "2d 3h") -- the slider's raw value is minutes ELAPSED from now (asOfMin,
// matching buildPayoffFns's own convention), but showing elapsed time forces the user to
// do (max - value) math in their head to know how close to expiry they're looking at.
function _fmtTimeRemaining(minutesRemaining) {
    if (minutesRemaining <= 0) return 'at expiry';
    const days = Math.floor(minutesRemaining / 1440);
    const hours = Math.floor((minutesRemaining % 1440) / 60);
    const mins = Math.round(minutesRemaining % 60);
    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins}m`;
}

function _onPlSliderInput() {
    const timeSlider = document.getElementById('pl-time-slider');
    const timeVal = parseInt(timeSlider.value, 10) || 0;
    const ivRaw = parseInt(document.getElementById('pl-iv-slider').value, 10) || 0;

    _plAsOfMin = timeVal;
    _plFlatIV = ivRaw > 0 ? ivRaw / 100 : null;

    const minutesToFront = parseInt(timeSlider.max, 10) || 0;
    document.getElementById('pl-time-label').textContent =
        timeVal <= 0 ? 'now' : _fmtTimeRemaining(minutesToFront - timeVal);
    document.getElementById('pl-iv-label').textContent = _plFlatIV != null ? `${ivRaw}%` : 'model';

    _drawPlChart();
}

function _plChartTitleSubtitle(legs, symbol, subcategory) {
    const titleParts = ['Risk Graph', symbol || ''];
    if (subcategory) titleParts.push(subcategory);
    return { titleText: titleParts.filter(Boolean).join(' — '), subtitleText: _legSubtitle(legs) };
}


// ── Live quotes for risk graphs ──────────────────────────────────────────────
// Every open risk graph (grid cards, enlarged chart) registers its legs here. One Socket.IO
// subscription covers all their stream symbols plus the underlying; the server pushes only the
// quotes that changed, about once a second, off the same market stream the bots price from.
// A push updates each affected leg's live_price (mid, else last) and the spot, then redraws that
// chart once per animation frame. Each chart's .live-dot[data-live-key] goes green while quotes
// for it have arrived within LIVE_STALE_MS, grey otherwise.
const _riskLive = (() => {
    const LIVE_STALE_MS = 15000;
    const tracked = new Map();   // key -> { legs, underlying, redraw, lastAt }
    const quotes = {};           // stream symbol -> {bid, ask, last}
    const pending = new Set();
    let socket = null, frame = null, dotTimer = null;

    const symbolsOf = t => [...t.legs.map(l => l.stream_symbol).filter(Boolean), ...(t.underlying ? [t.underlying] : [])];
    const allSymbols = () => [...new Set([...tracked.values()].flatMap(symbolsOf))];

    function ensureSocket() {
        if (socket || typeof io === 'undefined') return socket;
        socket = io({ transports: ['websocket', 'polling'] });
        socket.on('connect', resubscribe);          // also re-sends after a reconnect
        socket.on('risk_quotes', onQuotes);
        socket.on('risk_quotes_status', st => { if (st && st.available === false) console.warn('Risk graph live quotes unavailable: market stream not running'); });
        return socket;
    }

    function resubscribe() {
        if (!socket || !socket.connected) return;
        const symbols = allSymbols();
        if (symbols.length) socket.emit('subscribe_risk_quotes', { symbols });
        else socket.emit('unsubscribe_risk_quotes', {});
    }

    function onQuotes(msg) {
        const changed = new Set(Object.keys((msg && msg.quotes) || {}));
        Object.assign(quotes, msg.quotes || {});
        const now = Date.now();
        for (const [key, t] of tracked) {
            if (!symbolsOf(t).some(sym => changed.has(sym))) continue;
            t.lastAt = now;
            pending.add(key);
        }
        if (pending.size && !frame) frame = requestAnimationFrame(flush);
        paintDots();
    }

    function flush() {
        frame = null;
        for (const key of pending) {
            const t = tracked.get(key);
            if (!t) continue;
            for (const leg of t.legs) {
                const q = quotes[leg.stream_symbol];
                if (!q) continue;
                const mid = q.bid > 0 && q.ask > 0 ? (q.bid + q.ask) / 2 : null;
                const px = mid ?? (q.last > 0 ? q.last : null);
                if (px != null) leg.live_price = px;
            }
            const u = t.underlying && quotes[t.underlying];
            const spot = u ? (u.last > 0 ? u.last : (u.bid > 0 && u.ask > 0 ? (u.bid + u.ask) / 2 : null)) : null;
            try { t.redraw(spot); } catch (err) { console.error('risk graph live redraw failed', err); }
        }
        pending.clear();
    }

    function paintDots() {
        const now = Date.now();
        document.querySelectorAll('.live-dot[data-live-key]').forEach(dot => {
            const t = tracked.get(dot.dataset.liveKey);
            const live = t && t.lastAt && now - t.lastAt < LIVE_STALE_MS;
            dot.className = 'live-dot inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle ' + (live ? 'bg-green-500' : 'bg-gray-400 dark:bg-gray-600');
            dot.title = live ? 'Live' : (t ? 'Waiting for live quotes' : '');
        });
    }

    return {
        // redraw(spot) re-renders the chart from the (mutated) legs; spot is null when unchanged
        track(key, legs, underlying, redraw) {
            tracked.set(key, { legs, underlying: (underlying || '').toUpperCase(), redraw, lastAt: null });
            if (!dotTimer) dotTimer = setInterval(paintDots, 5000);
            paintDots();
            if (ensureSocket()) resubscribe();
        },
        untrack(prefix) {
            for (const key of [...tracked.keys()]) if (key.startsWith(prefix)) tracked.delete(key);
            if (!tracked.size && dotTimer) { clearInterval(dotTimer); dotTimer = null; }
            resubscribe();
        },
    };
})();


// Helpers shared between journal modal and grid cards
const _fmtExp = iso => {
    const [, m, d] = iso.split('-');
    return `${parseInt(d)} ${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][parseInt(m)-1]}`;
};
const _dteFor = iso => {
    const today = new Date(); today.setHours(0,0,0,0);
    return Math.max(0, Math.round((new Date(iso + 'T00:00:00') - today) / 86400000));
};
// "1x  |  25 Sep (0d) BTO: 7535P 7635P · STO: 2×7585P  →  28 Sep (3d) BTO: 7675C"
// Each expiration lists its bought and sold strikes separately (a butterfly mixes both).
// Legs at the same expiration/side/strike/type are merged (a combined card repeats them),
// the leading Nx is the smallest leg size, and larger legs carry their ratio to it.
const _legSubtitle = legs => {
    const merged = new Map();
    for (const l of legs) {
        const key = [l.expiration_date, l.side, l.strike, l.leg_type].join('|');
        const m = merged.get(key) || { ...l, qty: 0 };
        m.qty += Math.abs(l.quantity || 1);
        merged.set(key, m);
    }
    const unit = Math.min(...[...merged.values()].map(l => l.qty));
    const expiries = [...new Set([...merged.values()].map(l => l.expiration_date))].sort();
    const groups = expiries.map(exp => {
        const sides = ['LONG', 'SHORT'].map(side => {
            const strikes = [...merged.values()]
                .filter(l => l.expiration_date === exp && l.side === side)
                .sort((a, b) => a.strike - b.strike)
                .map(l => `${l.qty !== unit ? `${+(l.qty / unit).toFixed(2)}×` : ''}${l.strike}${l.leg_type[0]}`);
            return strikes.length ? `${side === 'SHORT' ? 'STO' : 'BTO'}: ${strikes.join(' ')}` : '';
        }).filter(Boolean);
        return `${_fmtExp(exp)} (${_dteFor(exp)}d) ${sides.join(' · ')}`;
    });
    return `${unit}x  |  ${groups.join('  →  ')}`;
};

// ── Order group: a checkbox per order, a tab per underlying ─────────────
// Graphs several orders in the panel with a checkbox row per order (legs, label, cost, and with
// opts.theo its own live theoretical P&L) in #pl-orders, and, when they span more than one
// underlying, a tab per underlying (SPX first, then most orders first) in #pl-symbols. Each tab
// keeps its own checked orders and spot; at least one order always stays checked. Used by the
// pop-out window and the strategies page's multi-strategy preview.
//
// items: [{ label, legs, realized_pl, symbol, spot, checked }] -- checked defaults to true, and a
// tab with none checked starts with all of them checked.
// opts.title(view) -> the header title; opts.footer -> html under the rows (the pop-out's skipped
// note); opts.onShow(view) runs after each tab is drawn. Returns { views, show, get view }.
function _mountRiskGraphGroup(items, opts = {}) {
    const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const money = v => (v >= 0 ? '+' : '-') + '$' + Math.abs(v).toFixed(0);
    const plCls = v => v >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-500 dark:text-red-400';
    const legsCost = legs => legs.reduce((sum, l) => sum + (l.entry_price || 0) * l.quantity * (l.multiplier || 100), 0);
    const title = v => (opts.title ? opts.title(v) : 'Risk Graph');
    const footer = opts.footer || '';

    const views = [];
    for (const item of items) {
        const symbol = item.symbol || '';
        let v = views.find(x => x.symbol === symbol);
        if (!v) views.push(v = { symbol, items: [], checked: new Set(), spot: 0 });
        if (item.checked !== false) v.checked.add(v.items.length);
        v.items.push(item);
        if (!v.spot && item.spot) v.spot = item.spot;
    }
    views.sort((a, b) => (b.symbol === 'SPX') - (a.symbol === 'SPX') || b.items.length - a.items.length);
    views.forEach(v => { if (!v.checked.size) v.items.forEach((_, i) => v.checked.add(i)); });
    let view = null;

    const selection = () => {
        const idx = [...view.checked].sort((a, b) => a - b);
        return {
            legs: idx.flatMap(i => view.items[i].legs),
            realizedPl: idx.reduce((sum, i) => sum + (view.items[i].realized_pl || 0), 0),
        };
    };

    // Underlying tabs, styled like the journal's Cards/Table toggle. Only when there's more than one.
    const symbolsEl = document.getElementById('pl-symbols');
    const paintSymbolTabs = () => {
        if (views.length < 2) {
            symbolsEl.innerHTML = '';
            symbolsEl.classList.add('hidden');
            return;
        }
        symbolsEl.innerHTML = `
            <div class="inline-flex items-center h-8 p-0.5 rounded-md bg-gray-100 dark:bg-gray-800 border border-gray-200 dark:border-gray-700" role="group" aria-label="Underlying">
                ${views.map((v, i) => `
                    <button data-view="${i}" class="inline-flex items-center gap-1 h-full px-3 text-xs font-medium rounded transition-colors ${v === view
                        ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                        : 'text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'}">
                        ${esc(v.symbol || '?')} <span class="text-gray-400 dark:text-gray-500">${v.items.length}</span>
                    </button>`).join('')}
            </div>`;
        symbolsEl.classList.remove('hidden');
        symbolsEl.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => {
            const next = views[Number(b.dataset.view)];
            if (next !== view) show(next);
        }));
    };

    // One row per order: checkbox, legs, label, cost, and (opts.theo) the order's own theoretical
    // P&L, repriced on every live push.
    const orders = document.getElementById('pl-orders');
    const paintOrderTheos = () => orders.querySelectorAll('.order-theo').forEach(el => {
        const d = view.items[Number(el.dataset.i)];
        const gs = _plUnderlyingPrice && window.__modeler.buildPayoffFns(
            { ticker: view.symbol, legs: d.legs.map(window.__modeler.toModelerLeg), net: legsCost(d.legs) },
            _plUnderlyingPrice, null, 0, null);
        const theo = gs && gs.t0payoff ? gs.t0payoff(_plUnderlyingPrice) + (d.realized_pl || 0) : null;
        el.textContent = theo == null ? '—' : money(theo);
        el.className = `order-theo font-mono font-semibold whitespace-nowrap w-16 text-right ${theo == null ? '' : plCls(theo)}`;
    });
    const paintOrders = () => {
        if (view.items.length < 2) {
            orders.innerHTML = footer;
            orders.classList.toggle('hidden', !footer);
            _riskLive.untrack('group-orders');
            return;
        }
        orders.innerHTML = view.items.map((d, i) => {
            const cost = legsCost(d.legs);
            return `
                <label class="flex items-center gap-3 px-1 py-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer text-xs">
                    <input type="checkbox" class="order-toggle accent-blue-500" data-i="${i}" ${view.checked.has(i) ? 'checked' : ''}>
                    <span class="font-mono text-gray-700 dark:text-gray-300 truncate flex-1 min-w-0">${esc(_legSubtitle(d.legs))}</span>
                    <span class="text-gray-400 dark:text-gray-500 whitespace-nowrap">${esc(d.label || '')}</span>
                    <span class="font-mono text-gray-600 dark:text-gray-300 whitespace-nowrap w-20 text-right">$${Math.abs(cost).toFixed(0)} ${cost >= 0 ? 'cr' : 'db'}</span>
                    ${opts.theo ? `<span class="order-theo font-mono font-semibold whitespace-nowrap w-16 text-right" data-i="${i}">—</span>` : ''}
                </label>`;
        }).join('') + footer;
        // A long book scrolls in its own box instead of pushing the chart off the window.
        orders.classList.toggle('max-h-48', view.items.length > 8);
        orders.classList.toggle('overflow-y-auto', view.items.length > 8);
        orders.classList.remove('hidden');
        if (opts.theo) {
            paintOrderTheos();
            // Every order's legs stay subscribed, so unchecked orders keep their own P&L current too.
            _riskLive.track('group-orders', view.items.flatMap(d => d.legs), view.symbol, spot => {
                if (spot) _plUnderlyingPrice = spot;
                paintOrderTheos();
            });
        }
        orders.querySelectorAll('.order-toggle').forEach(cb => cb.addEventListener('change', () => {
            const i = Number(cb.dataset.i);
            if (!cb.checked && view.checked.size === 1) { cb.checked = true; return; }
            if (cb.checked) view.checked.add(i); else view.checked.delete(i);
            const sel = selection();
            _setPlLegs(sel.legs, sel.realizedPl);
            document.getElementById('pl-chart-title').textContent = title(view);
            if (opts.onToggle) opts.onToggle(view);
        }));
    };

    function show(next) {
        if (view) {
            view.spot = _plUnderlyingPrice;
            // Pan/zoom is kept per canvas; a fresh canvas starts the new underlying auto-fitted.
            const canvas = document.getElementById('pl-chart-canvas');
            if (canvas) canvas.replaceWith(canvas.cloneNode(false));
        }
        view = next;
        const { legs, realizedPl } = selection();
        _populatePlChartModal(legs, realizedPl, title(view), _legSubtitle(legs), view.spot, view.symbol);
        paintSymbolTabs();
        paintOrders();
        if (opts.onShow) opts.onShow(view);
    }
    if (views.length) show(views[0]);

    return { views, show, get view() { return view; } };
}
