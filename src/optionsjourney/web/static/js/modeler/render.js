// render.js — canvas payoff-curve painter.
//
// Adapted from ntflux's modeler_graph.js painting technique. Unlike modeler_graph.js,
// this module takes a minimal caller-owned options object instead of reading
// card/builder/compare-mode global state — no `state.cards`, no compare-mode overlays,
// no strike-drag, no price-slice probes (those remain out of scope for this engine
// layer; sub-projects consuming this module add their own interaction wiring on top).
import { buildPayoffFns, curveStats, defaultIV, lnProbBelow, expiryMs } from './pricing.js';

// Every entry below corresponds to one colour literal in ntflux's source painter.
export const PALETTE = {
  dark: {
    bg:            '#111827',   // the app's --bg (gray-900), so the chart sits flush in the page
    gridLine:      'rgba(255,255,255,0.07)',
    zeroLine:      'rgba(255,255,255,0.22)',
    axisLine:      'rgba(255,255,255,0.2)',
    xGrid:         'rgba(255,255,255,0.05)',
    strikeLine:    'rgba(255,255,255,0.09)',
    labelPos:      'rgba(74,222,128,0.85)',
    labelNeg:      'rgba(239,68,68,0.85)',
    labelZero:     'rgba(255,255,255,0.5)',
    xLabel:        'rgba(255,255,255,0.5)',
    profitFill:    'rgba(34,197,94,0.07)',
    lossFill:      'rgba(255,60,60,0.08)',
    t0Line:        '#FF5600',
    expiryLine:    '#22c55e',
    spotLine:      'rgba(255,179,0,0.7)',
    spotMarker:    'rgba(255,179,0,0.9)',
    probText:      'rgba(255,179,0,0.85)',
    sdFill:        'rgba(255,255,255,0.05)',
    sdEdge:        'rgba(255,255,255,0.18)',
    tooltipBg:     'rgba(17,24,39,0.92)',
    tooltipEdge:   'rgba(255,255,255,0.12)',
    tooltipLabel:  'rgba(180,180,180,0.85)',
    pnlPos:        '#4ade80',
    pnlNeg:        '#ff4444',
  },
  light: {
    bg:            '#ffffff',
    gridLine:      'rgba(0,0,0,0.07)',
    zeroLine:      'rgba(0,0,0,0.28)',
    axisLine:      'rgba(0,0,0,0.25)',
    xGrid:         'rgba(0,0,0,0.05)',
    strikeLine:    'rgba(0,0,0,0.12)',
    labelPos:      'rgba(21,128,61,0.9)',
    labelNeg:      'rgba(185,28,28,0.9)',
    labelZero:     'rgba(0,0,0,0.55)',
    xLabel:        'rgba(0,0,0,0.55)',
    profitFill:    'rgba(34,197,94,0.10)',
    lossFill:      'rgba(220,38,38,0.10)',
    t0Line:        '#EA4E00',
    expiryLine:    '#16a34a',
    spotLine:      'rgba(180,120,0,0.75)',
    spotMarker:    'rgba(180,120,0,0.95)',
    probText:      'rgba(146,97,0,0.95)',
    sdFill:        'rgba(0,0,0,0.04)',
    sdEdge:        'rgba(0,0,0,0.20)',
    tooltipBg:     'rgba(255,255,255,0.95)',
    tooltipEdge:   'rgba(0,0,0,0.12)',
    tooltipLabel:  'rgba(70,70,70,0.9)',
    pnlPos:        '#15803d',
    pnlNeg:        '#b91c1c',
  },
};

export function activePalette() {
  return document.documentElement.classList.contains('dark') ? PALETTE.dark : PALETTE.light;
}

// Ported from ntflux's modeler_graph.js -- a <input type="color"> only speaks #rrggbb,
// but this module's fills are low-alpha rgba(). A caller's chosen hex is reapplied at
// paint time with the palette's own alpha (hexToRgba); rgbToHex/rgbaAlpha go the other
// way, for seeding a color picker from one of this module's own palette rgba() defaults.
export function hexToRgba(hex, alpha) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
  if (!m) return null;
  return `rgba(${parseInt(m[1], 16)},${parseInt(m[2], 16)},${parseInt(m[3], 16)},${alpha})`;
}
export function rgbToHex(rgba) {
  const m = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(rgba || '');
  if (!m) return '#000000';
  return '#' + [1, 2, 3].map(i => (+m[i]).toString(16).padStart(2, '0')).join('');
}
export function rgbaAlpha(rgba) {
  const m = /rgba?\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\s*\)/.exec(rgba || '');
  return m ? parseFloat(m[1]) : 0.1;
}

// Exported so external code (e.g. a right-click structure builder) can convert a
// pixel position to the same price the chart is CURRENTLY showing -- i.e. respecting
// any pan/zoom the user has applied, not just the auto-fit window. Mirrors the
// effectiveViewport derivation renderPayoffChart uses internally (see below).
export function currentViewport(canvasEl, autoSmin, autoSmax) {
  const pzEntry = _panZoomState.get(canvasEl);
  const viewport = pzEntry?.viewport;
  return {
    Smin: viewport?.Smin ?? autoSmin,
    Smax: viewport?.Smax ?? autoSmax,
  };
}

const RATE = 0.045;

// Tracks canvases that already have a hover listener attached, so calling
// renderPayoffChart repeatedly (every slider tick, every leg edit) never double-binds.
// Maps canvas -> the most recent caller-supplied opts, so the listener (bound once) always
// redraws with the CURRENT slider/leg state rather than whatever opts were in scope the
// first time this canvas was rendered. A WeakMap so a removed/garbage-collected canvas
// doesn't leak an entry.
const _hoverState = new WeakMap();

// Tracks each canvas's own pan/zoom viewport override, independently of the hover state
// above and independently of whatever opts.viewport a caller passes. Unlike hover (which is
// transient — cleared the instant the mouse leaves), a pan/zoom viewport must SURVIVE across
// every subsequent redraw (a slider tick, a leg edit) until the user double-clicks to reset —
// so it can't live in the same latestOpts-snapshot mechanism hover uses. Maps
// canvas -> { viewport: {Smin,Smax,yOffset} | null, opts: <latest caller opts>, drag: null | {...} }.
const _panZoomState = new WeakMap();

// Strike-drag handle positions, published fresh by every paint (below) and consumed by the
// pan/zoom block's hit-test -- mirrors ntflux's own "only the painter knows the y-range it
// just drew with" comment. A WeakMap so a removed canvas doesn't leak an entry; each entry
// is a plain array of {K, x, y} rebuilt from scratch on every render, never mutated in place.
const _strikeHandles = new WeakMap();
export const STRIKE_GRAB_R = 20;

// Render a payoff chart into canvasEl from a legs array already in
// {ls, type, strike, expiry, qty, premium} shape (run tracker2 API legs through
// pricing.js's toModelerLeg first).
//
// Returns { stats, teardown }. stats is null when there is nothing to graph (no legs) —
// mirrors buildPayoffFns's own null-when-empty contract; callers must not assume stats
// is always populated.
//
// Hover crosshair: unless opts.hoverEnabled === false, a mousemove/mouseleave listener is
// attached to canvasEl (once, idempotently) that redraws the chart with a crosshair line and
// a small tooltip showing the underlying price and both curves' P&L at the cursor's x-position.
// This is entirely self-contained — callers (journal.html, modeler_page.js, the plan-adjustment
// preview) need no changes; every consumer of this shared engine gets hover for free. The
// listener re-invokes renderPayoffChart itself with the same opts plus an internal hoverX, so
// hover state never leaks into the caller-owned opts object.
// x-axis price labels (7 evenly spaced), plus the current price to the cent on a spot-colored
// tag under the spot line. A tick label the tag would overlap is skipped. No tag when spot is
// outside [Smin, Smax] -- the spot line isn't drawn then either.
// Plot-area insets. The left one is a wide gutter on a roomy canvas, but no y-axis labels
// are drawn in it, so a narrow (phone) canvas keeps only enough for the first x label.
function plotPad(cW) {
  return { t: 20, b: 38, l: cW < 520 ? 28 : 72, r: 20 };
}

function drawXAxisLabels(ctx, P, Smin, Smax, toX, y, spot, plotL, plotR) {
  ctx.textAlign = 'center';
  let tag = null;
  if (spot != null && spot > 0 && spot >= Smin && spot <= Smax) {
    ctx.font = 'bold 11px sans-serif';
    const text = spot.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const w = ctx.measureText(text).width + 10;
    // Kept inside the plot so a spot near either edge doesn't push the tag off the canvas.
    const x = Math.min(Math.max(toX(spot), plotL + w / 2), plotR - w / 2);
    tag = { text, x, w };
  }
  ctx.font = '11px sans-serif';
  ctx.fillStyle = P.xLabel;
  const xTickCount = 6;
  for (let i = 0; i <= xTickCount; i++) {
    const S = Smin + (Smax - Smin) * i / xTickCount;
    const x = toX(S);
    const label = S >= 100 ? Math.round(S).toLocaleString() : S.toFixed(2);
    if (tag && Math.abs(x - tag.x) < (tag.w + ctx.measureText(label).width) / 2 + 4) continue;
    ctx.fillText(label, x, y);
  }
  if (tag) {
    ctx.fillStyle = P.spotMarker;
    ctx.fillRect(tag.x - tag.w / 2, y - 12, tag.w, 16);
    ctx.font = 'bold 11px sans-serif';
    ctx.fillStyle = P.bg;
    ctx.fillText(tag.text, tag.x, y);
  }
}

export function renderPayoffChart(canvasEl, opts) {
  const {
    legs, spot, ticker, asOfMin = 0, flatIV = null, viewport = null,
    palette = 'auto', showSD = false, showBreakevens = true, hoverEnabled = true,
    panZoomEnabled = true,
    // Net cost basis of the WHOLE position (a paid/long premium is a debit, negative; a
    // received/short premium is a credit, positive). Optional — see the fallback below for
    // when a caller should omit it.
    net: netOpt,
    // Internal: pixel x-position of an active hover, set only by this module's own
    // mousemove handler below — never passed in by a caller.
    _hoverPxX: _hoverOpt,
    // Price slices: caller-owned draggable price markers (a la ntflux's Analyzer). This
    // module is the controller for hit-testing/dragging/removing (it already owns the
    // canvas's mousedown/mousemove/mouseup/dblclick for pan/zoom, so slice-drag has to
    // live here too rather than as a second, competing listener set) — but the DATA is
    // entirely caller-owned: `slices` is `[{id, price}]`, and a drag/remove gesture is
    // reported back via onSliceChange(id, price)/onSliceRemove(id) rather than mutated
    // in place, so the caller's own state (and its persistence) stays the source of truth.
    sliceEnabled = false,
    slices = [],
    onSliceChange = null,
    onSliceRemove = null,
    // Strike-drag handles: a draggable dot at each strike's kink on the EXPIRATION curve
    // (ntflux's Analyzer). Unlike slices, this module doesn't own a strike identity beyond
    // its price -- `gs.strikes` (every option strike currently in `legs`) is used directly
    // as the handle list, and a drag reports the OLD strike + the new (increment-snapped)
    // price via onStrikeChange(oldStrike, newStrike); the caller resolves which leg(s)
    // carried that old strike and re-prices/redraws. opts.strikeIncrement (read directly off
    // the raw opts object inside the drag handler below, so it's always this render's latest
    // value) sets the snap grid; a caller that omits it gets a $5 default. onStrikeDragEnd()
    // fires once on mouseup, for a caller that wants to persist/re-render only once per drag
    // rather than on every mousemove tick (e.g. renderCardList()).
    strikeDragEnabled = false,
    onStrikeChange = null,
    onStrikeDragEnd = null,
    // Optional per-caller color overrides for the T+0/expiry lines and the profit/loss
    // fills, layered on top of whatever `palette` already resolved to -- e.g.
    // { t0Color: '#hex', expColor: '#hex', profitColor: '#hex'|null, lossColor: '#hex'|null,
    //   profitShade: true|false, lossShade: true|false }. Any field left unset/undefined
    // falls back to the resolved palette's own value, so a caller that never passes this
    // (journal.html's modal, the old default) draws byte-identical to before this option
    // existed. `profitColor`/`lossColor` are hex strings the palette's own fill alpha gets
    // re-applied to (hexToRgba below) -- a raw <input type="color"> only speaks #rrggbb,
    // the fills are low-alpha rgba().
    colorOverrides = null,
  } = opts;

  // A caller's own redraw (a slider tick, a live-quote push) carries no _hoverPxX; keep the
  // crosshair and tooltip where the mouse still is rather than wiping them until it moves.
  const _hoverPxX = '_hoverPxX' in opts
    ? _hoverOpt
    : (hoverEnabled ? (_hoverState.get(canvasEl)?.hoverX ?? null) : null);

  // A user-driven pan/zoom viewport (see _panZoomState below) takes priority over whatever
  // opts.viewport the caller passed — once someone has dragged/zoomed the chart, that's the
  // view they're looking at until they double-click to reset, regardless of what the caller's
  // own opts.viewport happens to be on subsequent redraws (no current caller sets it, but this
  // keeps the precedence explicit rather than accidental).
  const pzEntry = panZoomEnabled ? _panZoomState.get(canvasEl) : null;
  const effectiveViewport = pzEntry?.viewport ?? viewport;

  const P = palette === 'auto' ? activePalette() : palette;
  // `legs[i].premium` is whatever price toModelerLeg mapped in — for a REAL open position
  // that's the LIVE market price (toModelerLeg prefers live_price over entry_price), not what
  // the position actually paid at entry. Using it as the position's net cost basis would price
  // "cost to open this position right now," which then gets subtracted from a T+0 curve ALSO
  // priced off those same live premiums — the two nearly cancel, making every existing
  // position's theoretical P&L read close to $0 regardless of its real unrealized P&L. This
  // was a real, confirmed bug (a 2-leg double calendar showed "Theoretical: -$6" against a
  // reference tool's +$395 for the same spot).
  //
  // Callers that know the position's true entry cost basis (journal.html's _plLegs, sourced
  // from each leg's entry_price) must pass it explicitly via opts.net. Only the from-scratch
  // scratch-pad page (sub-project B, modeler_page.js) — which has no "entry price" concept,
  // since the user is typing hypothetical premiums for a trade they haven't placed — should
  // omit opts.net and fall back to the live/typed-premium sum below, which is the correct
  // "cost to open this hypothetical trade" reading for that page.
  const net = netOpt != null ? netOpt : (legs || []).reduce((sum, l) => {
    if (l.type !== 'call' && l.type !== 'put') return sum;
    const sign = l.ls === 'short' ? 1 : -1;
    return sum + sign * (l.premium || 0) * (l.qty || 1) * (l.multiplier || 100);
  }, 0);
  const pos = { ticker, legs, net };
  const liveIVs = null; // per-leg IV is back-solved from each leg's own premium inside buildPayoffFns
  const gs = buildPayoffFns(pos, spot, liveIVs, asOfMin, flatIV);

  const ctx = canvasEl.getContext('2d');
  const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
  const cW = canvasEl.clientWidth || 800, cH = canvasEl.clientHeight || 360;
  canvasEl.width = Math.round(cW * dpr);
  canvasEl.height = Math.round(cH * dpr);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, cW, cH);

  if (!gs) {
    // No legs yet -- still refresh the hover/pan-zoom listeners' cached opts (mirrors the
    // "update existing entry" branches below, just without the curve-dependent code around
    // them). Without this, deleting the last card/leg left those listeners holding the LAST
    // non-empty render's opts forever, since this early return used to skip past their
    // registration entirely -- the next mousemove/wheel/drag redrew the STALE curve (a real,
    // confirmed bug: the graph "flashed" a phantom position back on hover after the card
    // list had gone empty).
    if (hoverEnabled) {
      const { _hoverPxX: _drop, ...latestOpts } = opts;
      const entry = _hoverState.get(canvasEl);
      if (entry) entry.opts = latestOpts;
    }
    if (panZoomEnabled) {
      const { _hoverPxX: _drop2, ...latestOptsPZ } = opts;
      const pz = _panZoomState.get(canvasEl);
      if (pz) pz.opts = latestOptsPZ;
    }
    // Same staleness issue for strike-drag handles -- clear them so a click after the last
    // leg is removed can't hit-test against a handle that no longer corresponds to anything.
    _strikeHandles.set(canvasEl, []);
    // No legs yet — still draw an empty, axis-labeled grid rather than a blank canvas, so a
    // right-click has a visual reference point for where it's landing (matching ntflux's
    // Analyzer, whose graph always shows gridlines/axes even before any position exists).
    if (spot != null && spot > 0) {
      const pad = plotPad(cW);
      const chartW = cW - pad.l - pad.r;
      const halfRange = spot * 0.03;
      const Smin = spot - halfRange, Smax = spot + halfRange;
      const toX = S => pad.l + (S - Smin) / (Smax - Smin) * chartW;

      ctx.fillStyle = P.bg;
      ctx.fillRect(0, 0, cW, cH);

      drawXAxisLabels(ctx, P, Smin, Smax, toX, cH - pad.b + 16, spot, pad.l, cW - pad.r);

      ctx.strokeStyle = P.axisLine;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(pad.l, cH - pad.b); ctx.lineTo(cW - pad.r, cH - pad.b); ctx.stroke();

      const x = toX(spot);
      ctx.strokeStyle = P.spotLine;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, cH - pad.b); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = P.spotMarker;
      ctx.beginPath(); ctx.moveTo(x - 5, pad.t); ctx.lineTo(x + 5, pad.t); ctx.lineTo(x, pad.t + 8); ctx.closePath(); ctx.fill();
    }
    return { stats: null, teardown() {} };
  }

  const { payoff, t0payoff, strikes, autoSmin, autoSmax, frontExpiry, greeksAt } = gs;
  const Smin = effectiveViewport?.Smin ?? autoSmin;
  const Smax = effectiveViewport?.Smax ?? autoSmax;
  const yOffset = effectiveViewport?.yOffset ?? 0;

  const N = 300;
  const ePts = Array.from({ length: N + 1 }, (_, i) => { const S = Smin + (Smax - Smin) * i / N; return { S, y: payoff(S) }; });
  const tPts = t0payoff ? Array.from({ length: N + 1 }, (_, i) => { const S = Smin + (Smax - Smin) * i / N; return { S, y: t0payoff(S) }; }) : null;

  const stats = curveStats(payoff, Smin, Smax, strikes);

  const allY = [...ePts.map(p => p.y), ...(tPts ? tPts.map(p => p.y) : []), 0];
  const rawMin = Math.min(...allY), rawMax = Math.max(...allY);
  const yPad = Math.max((rawMax - rawMin) * 0.10, 50);
  const minY = rawMin - yPad + yOffset, maxY = rawMax + yPad + yOffset;

  const pad = plotPad(cW);
  const chartW = cW - pad.l - pad.r, chartH = cH - pad.t - pad.b;
  const yRange = maxY - minY || 1;
  const toX = S => pad.l + (S - Smin) / (Smax - Smin) * chartW;
  const toY = y => pad.t + (1 - (y - minY) / yRange) * chartH;
  const zY = Math.min(Math.max(toY(0), pad.t), cH - pad.b);

  ctx.fillStyle = P.bg;
  ctx.fillRect(0, 0, cW, cH);

  // Model IV / time-to-expiry from the as-of moment, shared by the ±1 SD band below and
  // the region-probability labels further down -- both are lognormal-at-expiry reads off
  // the SAME front-expiry vol/time, so they must agree with each other (and re-tighten
  // together as the Time slider moves forward), matching ntflux's single sigmaG/TyrsG/
  // volOK derivation reused by both features.
  const sigmaG = flatIV != null ? flatIV : defaultIV(ticker);
  const TyrsG = frontExpiry ? Math.max(0, (expiryMs(frontExpiry) - (Date.now() + asOfMin * 60000)) / (365 * 24 * 60 * 60 * 1000)) : 0;
  const volOK = spot != null && sigmaG > 0 && TyrsG > 0;

  // 1-SD expected-move band, using the front expiry's model IV and time-to-expiry from
  // the as-of moment (so it tightens as the time slider moves forward).
  if (showSD && volOK) {
    const sdLo = spot * Math.exp(-sigmaG * Math.sqrt(TyrsG));
    const sdHi = spot * Math.exp(sigmaG * Math.sqrt(TyrsG));
    const bx0 = Math.max(pad.l, toX(sdLo)), bx1 = Math.min(cW - pad.r, toX(sdHi));
    if (bx1 > bx0) {
      ctx.save();
      ctx.fillStyle = P.sdFill;
      ctx.fillRect(bx0, pad.t, bx1 - bx0, chartH);
      ctx.strokeStyle = P.sdEdge;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      [sdLo, sdHi].forEach(K => { const x = toX(K); if (x >= pad.l && x <= cW - pad.r) { ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, cH - pad.b); ctx.stroke(); } });
      ctx.setLineDash([]);
      ctx.restore();
    }
  }

  // Profit/loss zone shading, clipped above/below the zero line.
  const drawShadedCurve = (pts, color, isAbove) => {
    ctx.save();
    ctx.beginPath();
    if (isAbove) { ctx.rect(pad.l, pad.t, chartW, zY - pad.t); } else { ctx.rect(pad.l, zY, chartW, (cH - pad.b) - zY); }
    ctx.clip();
    ctx.beginPath();
    pts.forEach((p, i) => { const x = toX(p.S), y = toY(p.y); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
    ctx.lineTo(toX(Smax), zY); ctx.lineTo(toX(Smin), zY); ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.restore();
  };
  // Overrides layer on top of the resolved palette -- any field left unset falls back to
  // the palette's own value, so a caller that never passes colorOverrides (journal.html's
  // modal, or this page before a color is chosen) draws byte-identical to before this
  // option existed.
  const profitShadeOn = colorOverrides?.profitShade !== false;
  const lossShadeOn = colorOverrides?.lossShade !== false;
  const profitFillColor = (colorOverrides?.profitColor && hexToRgba(colorOverrides.profitColor, rgbaAlpha(P.profitFill))) || P.profitFill;
  const lossFillColor = (colorOverrides?.lossColor && hexToRgba(colorOverrides.lossColor, rgbaAlpha(P.lossFill))) || P.lossFill;
  if (profitShadeOn) drawShadedCurve(ePts, profitFillColor, true);
  if (lossShadeOn) drawShadedCurve(ePts, lossFillColor, false);

  // Zero line.
  ctx.strokeStyle = P.zeroLine;
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad.l, zY); ctx.lineTo(cW - pad.r, zY); ctx.stroke();

  // Expiry curve.
  ctx.strokeStyle = colorOverrides?.expColor || P.expiryLine;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ePts.forEach((p, i) => { const x = toX(p.S), y = toY(p.y); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
  ctx.stroke();

  // T+0 curve.
  if (tPts) {
    ctx.strokeStyle = colorOverrides?.t0Color || P.t0Line;
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    tPts.forEach((p, i) => { const x = toX(p.S), y = toY(p.y); if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Spot marker.
  if (spot != null && spot >= Smin && spot <= Smax) {
    const x = toX(spot);
    ctx.strokeStyle = P.spotLine;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, cH - pad.b); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = P.spotMarker;
    ctx.beginPath(); ctx.moveTo(x - 5, pad.t); ctx.lineTo(x + 5, pad.t); ctx.lineTo(x, pad.t + 8); ctx.closePath(); ctx.fill();
  }

  // Region probabilities across the top (lognormal, at expiry) -- shown whenever there's
  // at least one slice, splitting the price axis into below/between/above zones (a single
  // slice gives a left % and a right %; N slices give N+1 zones). Matches ntflux's Analyzer.
  if (sliceEnabled && slices.length >= 1 && volOK) {
    const sorted = [...slices].map(s => s.price).sort((a, b) => a - b);
    const edges = [Smin, ...sorted, Smax];
    const cum = sorted.map(x => lnProbBelow(x, spot, sigmaG, TyrsG));
    ctx.save();
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = P.probText;
    // The slice tags drawn below span pad.t+2..pad.t+30: a zone too narrow to fit its % between
    // them (a phone-width canvas) drops the % beneath the tags rather than printing over one.
    ctx.font = '10px sans-serif';
    const tagSpans = sliceEnabled ? slices.filter(({ price }) => toX(price) >= pad.l && toX(price) <= cW - pad.r).map(({ price }) => {
      const pl = payoff(price);
      const tw = Math.max(ctx.measureText(price >= 100 ? Math.round(price).toLocaleString() : price.toFixed(2)).width,
                          ctx.measureText((pl >= 0 ? '+$' : '-$') + Math.abs(Math.round(pl)).toLocaleString()).width) + 12;
      const tx = Math.min(Math.max(toX(price), pad.l + tw / 2), cW - pad.r - tw / 2);
      return [tx - tw / 2, tx + tw / 2];
    }) : [];
    ctx.font = '11px sans-serif';
    for (let i = 0; i < edges.length - 1; i++) {
      const pLo = i === 0 ? 0 : cum[i - 1], pHi = i === edges.length - 2 ? 1 : cum[i];
      if (pLo == null || pHi == null) continue;
      const pct = Math.max(0, (pHi - pLo)) * 100;
      const midX = (toX(Math.max(Smin, edges[i])) + toX(Math.min(Smax, edges[i + 1]))) / 2;
      if (midX < pad.l || midX > cW - pad.r) continue;
      const text = pct.toFixed(1) + '%';
      const half = ctx.measureText(text).width / 2 + 2;
      const hitsTag = tagSpans.some(([a, b]) => midX + half > a && midX - half < b);
      ctx.fillText(text, midX, pad.t + (hitsTag ? 46 : 24));
    }
    ctx.restore();
  }

  // Price slices — draggable dashed vertical lines with a price + at-expiry-P&L tag,
  // matching ntflux's Analyzer. Hit-testing/dragging is wired below in the pan/zoom block
  // (it shares that block's mousedown/mousemove/mouseup/dblclick listeners).
  if (sliceEnabled && slices.length) {
    const dragId = _panZoomState.get(canvasEl)?.sliceDrag?.id ?? null;
    slices.forEach(({ id, price }) => {
      const x = toX(price);
      if (x < pad.l || x > cW - pad.r) return;
      const active = id === dragId;
      ctx.save();
      ctx.strokeStyle = active ? 'rgba(255,179,0,0.9)' : 'rgba(180,180,180,0.6)';
      ctx.lineWidth = active ? 1.5 : 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, cH - pad.b); ctx.stroke();
      ctx.setLineDash([]);
      const pl = payoff(price);
      const priceLbl = price >= 100 ? Math.round(price).toLocaleString() : price.toFixed(2);
      const plLbl = (pl >= 0 ? '+$' : '-$') + Math.abs(Math.round(pl)).toLocaleString();
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'center';
      const tw = Math.max(ctx.measureText(priceLbl).width, ctx.measureText(plLbl).width) + 12;
      const tx = Math.min(Math.max(x, pad.l + tw / 2), cW - pad.r - tw / 2);
      ctx.fillStyle = P.tooltipBg;
      ctx.strokeStyle = P.tooltipEdge;
      ctx.lineWidth = 0.5;
      ctx.beginPath(); ctx.roundRect(tx - tw / 2, pad.t + 2, tw, 28, 3); ctx.fill(); ctx.stroke();
      ctx.fillStyle = P.tooltipLabel;
      ctx.fillText(priceLbl, tx, pad.t + 13);
      ctx.fillStyle = pl >= 0 ? P.pnlPos : P.pnlNeg;
      ctx.fillText(plLbl, tx, pad.t + 26);
      ctx.restore();
    });
  }

  // Strike-drag handles — a draggable dot at each strike's kink on the EXPIRATION curve.
  // Published here (the painter is the only thing that knows the y-range it just drew
  // with, matching ntflux's own comment) for the pan/zoom block's hit-test to consume.
  // A strike whose kink is panned/zoomed out of the plot area simply gets no handle, and
  // is not draggable until scrolled back into view — mirrors ntflux exactly.
  if (strikeDragEnabled) {
    const seen = new Set();
    const handles = [];
    for (const K of strikes) {
      if (seen.has(K)) continue;
      seen.add(K);
      const hx = toX(K), hy = toY(payoff(K));
      if (hx < pad.l || hx > cW - pad.r || hy < pad.t || hy > cH - pad.b) continue;
      handles.push({ K, x: hx, y: hy });
    }
    _strikeHandles.set(canvasEl, handles);

    const pzDrag = _panZoomState.get(canvasEl)?.strikeDrag ?? null;
    const activeK = pzDrag ? pzDrag.K : null;
    if (activeK != null) {
      const h = handles.find(v => v.K === activeK);
      if (h) {
        ctx.save();
        ctx.beginPath(); ctx.arc(h.x, h.y, STRIKE_GRAB_R, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255,214,0,0.20)';
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = 'rgba(255,214,0,0.85)';
        ctx.stroke();
        const lbl = h.K >= 100 ? Math.round(h.K).toLocaleString() : h.K.toFixed(2);
        ctx.font = 'bold 11px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillStyle = P.tooltipLabel;
        ctx.fillText(lbl, h.x, h.y - STRIKE_GRAB_R - 6);
        ctx.restore();
      }
    }
  }

  // Breakeven markers.
  if (showBreakevens && stats) {
    ctx.fillStyle = P.labelZero;
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'center';
    for (const be of stats.breakevens) {
      if (be < Smin || be > Smax) continue;
      const x = toX(be);
      ctx.beginPath(); ctx.arc(x, zY, 3, 0, Math.PI * 2); ctx.fill();
      ctx.fillText(be >= 100 ? Math.round(be).toLocaleString() : be.toFixed(2), x, zY - 8);
    }
  }

  drawXAxisLabels(ctx, P, Smin, Smax, toX, cH - pad.b + 16, spot, pad.l, cW - pad.r);

  // Hover crosshair + tooltip: only drawn when a mousemove handler (below) has recorded a
  // pixel x-position inside the plot area. Shows the underlying price under the cursor and
  // both curves' P&L there, reusing the already-built payoff/t0payoff closures — no extra
  // pricing work beyond two function calls.
  if (_hoverPxX != null && _hoverPxX >= pad.l && _hoverPxX <= cW - pad.r) {
    const hoverS = Smin + (_hoverPxX - pad.l) / chartW * (Smax - Smin);
    const hoverExpiry = payoff(hoverS);
    const hoverTheo = t0payoff ? t0payoff(hoverS) : null;

    ctx.save();
    ctx.strokeStyle = P.axisLine;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(_hoverPxX, pad.t); ctx.lineTo(_hoverPxX, cH - pad.b); ctx.stroke();
    ctx.setLineDash([]);
    ctx.restore();

    const fmtPrice = S => (S >= 100 ? Math.round(S).toLocaleString() : S.toFixed(2));
    // % of max risk -- Math.abs(stats.maxLoss) is the position's worst-case dollar loss
    // (already correct for both debit and credit structures, unlike net alone). Omitted
    // for an undefined-risk position (maxLoss is -Infinity, or 0 for a delta-neutral-only
    // curve where "% of risk" is meaningless).
    const riskBasis = Math.abs(stats.maxLoss);
    const fmtPnl = v => {
      const dollars = (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(0);
      if (!(riskBasis > 0) || !isFinite(riskBasis)) return dollars;
      return dollars + ' (' + (v >= 0 ? '+' : '-') + Math.abs(v / riskBasis * 100).toFixed(0) + '%)';
    };
    const lines = [fmtPrice(hoverS)];
    lines.push('At expiry: ' + fmtPnl(hoverExpiry));
    if (hoverTheo != null) lines.push('Theoretical: ' + fmtPnl(hoverTheo));

    ctx.font = '11px sans-serif';
    const lineH = 14;
    const boxW = Math.max(...lines.map(l => ctx.measureText(l).width)) + 16;
    const boxH = lines.length * lineH + 10;
    // Flip the tooltip to the left of the crosshair once it would overflow the right edge.
    const boxX = (_hoverPxX + 10 + boxW > cW - pad.r) ? _hoverPxX - 10 - boxW : _hoverPxX + 10;
    const boxY = pad.t + 4;

    ctx.save();
    ctx.fillStyle = P.tooltipBg;
    ctx.strokeStyle = P.tooltipEdge;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.rect(boxX, boxY, boxW, boxH);
    ctx.fill();
    ctx.stroke();
    ctx.textAlign = 'left';
    lines.forEach((line, i) => {
      ctx.fillStyle = i === 0 ? P.tooltipLabel : (line.includes('+$') ? P.pnlPos : (line.includes('-$') ? P.pnlNeg : P.tooltipLabel));
      ctx.fillText(line, boxX + 8, boxY + 14 + i * lineH);
    });
    ctx.restore();
  }

  // Always record the latest opts this canvas was rendered with (minus any stale _hoverPxX),
  // so a hover event firing after a slider/leg-edit redraw uses fresh state, not whatever was
  // captured when the listener was first bound.
  if (hoverEnabled) {
    const { _hoverPxX: _drop, ...latestOpts } = opts;
    let entry = _hoverState.get(canvasEl);
    if (!entry) {
      entry = { opts: latestOpts };
      entry.onMove = e => {
        const rect = canvasEl.getBoundingClientRect();
        entry.hoverX = e.clientX - rect.left;
        renderPayoffChart(canvasEl, { ...entry.opts, _hoverPxX: entry.hoverX });
      };
      entry.onLeave = () => {
        entry.hoverX = null;
        renderPayoffChart(canvasEl, { ...entry.opts, _hoverPxX: null });
      };
      canvasEl.addEventListener('mousemove', entry.onMove);
      canvasEl.addEventListener('mouseleave', entry.onLeave);
      _hoverState.set(canvasEl, entry);
    } else {
      entry.opts = latestOpts;
    }
  }

  // Pan (drag inside the plot area), x-axis-drag zoom, wheel/trackpad zoom, two-finger pinch
  // zoom, one-finger touch pan, and double-click/double-tap reset — mirrors ntflux's
  // modeler_graph.js interaction set (mouse drag+wheel, touch pinch/pan/tap), reimplemented
  // against this module's own state.viewport-equivalent (_panZoomState) rather than ntflux's
  // shared card/builder state. Listeners are bound once per canvas (idempotent, same pattern
  // as the hover block above); each handler recomputes pad/chartW/chartH itself from the
  // canvas's CURRENT size rather than closing over this render pass's values, since the
  // canvas can resize between when the listener was bound and when it fires.
  if (panZoomEnabled) {
    const { _hoverPxX: _drop2, ...latestOptsPZ } = opts;
    let pz = _panZoomState.get(canvasEl);
    if (!pz) {
      pz = { viewport: null, opts: latestOptsPZ, drag: null, touch: null, sliceDrag: null, strikeDrag: null };

      // Re-derive the plot area geometry and current Smin/Smax/yOffset fresh, using
      // whatever gs.autoSmin/autoSmax the CURRENT legs/spot/asOfMin/flatIV would produce —
      // needed because a drag/zoom/pinch gesture must convert pixels to prices using the
      // chart as it looks RIGHT NOW, not as it looked when the listener was first bound.
      const currentGeometry = () => {
        const cWc = canvasEl.clientWidth || 800, cHc = canvasEl.clientHeight || 360;
        const padc = plotPad(cWc);
        const chartWc = cWc - padc.l - padc.r, chartHc = cHc - padc.t - padc.b;
        const posc = { ticker: pz.opts.ticker, legs: pz.opts.legs, net: 0 };
        const gsc = buildPayoffFns(posc, pz.opts.spot, null, pz.opts.asOfMin || 0, pz.opts.flatIV ?? null);
        const vp = pz.viewport;
        const Sminc = vp?.Smin ?? gsc?.autoSmin ?? 0;
        const Smaxc = vp?.Smax ?? gsc?.autoSmax ?? 1;
        const yOffc = vp?.yOffset ?? 0;
        // The auto-fit window's own range, scaled to whatever this position/spot actually
        // looks like -- the floor below is a fraction of THIS, not a hardcoded dollar
        // amount, so it stays sane whether the ticker is a $5 stock or SPX at 7,700.
        const autoRangec = (gsc?.autoSmax ?? 1) - (gsc?.autoSmin ?? 0);
        return { cWc, cHc, padc, chartWc, chartHc, Sminc, Smaxc, yOffc, autoRangec };
      };

      // Minimum allowed zoomed-in range, as a fraction of the position's own auto-fit
      // window. Without a floor, repeated zoom-in gestures (wheel ticks, or an axis drag)
      // compound geometrically toward zero with no limit -- a real, confirmed bug: enough
      // scroll-to-zoom (an accidental trackpad gesture easily fires dozens of wheel events)
      // collapsed Smax-Smin down to a fraction of a point, leaving the chart a single flat
      // color block with meaningless axis labels. 1% keeps real close-in zooming useful
      // (SPX's auto window is roughly 900 points wide, so 1% is still ~9 points -- multiple
      // strikes' worth of room) while making the collapse impossible.
      const MIN_ZOOM_FRACTION = 0.01;
      const minRangeFor = autoRangec => Math.max(autoRangec * MIN_ZOOM_FRACTION, 0.01);

      const redraw = () => renderPayoffChart(canvasEl, { ...pz.opts, viewport: pz.viewport });

      const clientXY = (e, rect) => ({ x: e.clientX - rect.left, y: e.clientY - rect.top });

      // Nearest slice within a few px of cx (draggable/removable), or null. Only consulted
      // when the caller has opted into slices (pz.opts.sliceEnabled) — a caller that never
      // passes slices sees no change in pan/zoom behavior.
      const sliceHit = cx => {
        if (!pz.opts.sliceEnabled || !pz.opts.slices || !pz.opts.slices.length) return null;
        const { padc, chartWc, Sminc, Smaxc } = currentGeometry();
        let best = null, bestD = 6;
        pz.opts.slices.forEach(s => {
          const x = padc.l + (s.price - Sminc) / (Smaxc - Sminc) * chartWc;
          const d = Math.abs(x - cx);
          if (d < bestD) { bestD = d; best = s; }
        });
        return best;
      };

      // Nearest strike-drag handle within STRIKE_GRAB_R px (2-D: x AND y), or null. Reads
      // the handle positions the painter published, so the grab point is by construction
      // the dot that gets drawn -- same rationale as ntflux's own strikeHit.
      const strikeHit = (cx, cy) => {
        if (!pz.opts.strikeDragEnabled) return null;
        const handles = _strikeHandles.get(canvasEl);
        if (!handles || !handles.length) return null;
        let best = null, bestD = STRIKE_GRAB_R;
        for (const h of handles) {
          const d = Math.hypot(h.x - cx, h.y - cy);
          if (d < bestD) { bestD = d; best = h; }
        }
        return best;
      };

      pz.onMouseDown = e => {
        const rect = canvasEl.getBoundingClientRect();
        const { x: cx, y: cy } = clientXY(e, rect);
        const { padc, chartWc, chartHc, Sminc, Smaxc, yOffc, cHc, autoRangec } = currentGeometry();
        if (cx < padc.l || cx > (rect.width - padc.r)) return; // outside the plot area entirely
        // On a price slice → drag just that slice (priority over pan), matching ntflux.
        if (cy <= cHc - padc.b) {
          const hit = sliceHit(cx);
          if (hit) {
            pz.sliceDrag = { id: hit.id };
            canvasEl.style.cursor = 'ew-resize';
            e.preventDefault();
            redraw();
            return;
          }
        }
        // On a strike handle → drag just that strike (priority over pan, same as slices).
        if (cy <= cHc - padc.b) {
          const hit = strikeHit(cx, cy);
          if (hit) {
            pz.strikeDrag = { K: hit.K };
            canvasEl.style.cursor = 'ew-resize';
            e.preventDefault();
            redraw();
            return;
          }
        }
        // Dragging on the x-axis label strip zooms; dragging anywhere else in the plot pans —
        // matches ntflux's cy > cH - pad.b check for the same gesture split.
        const isAxisZoneDrag = cy > cHc - padc.b;
        pz.drag = {
          type: isAxisZoneDrag ? 'xzoom' : 'pan',
          startX: cx, startY: cy,
          startSmin: Sminc, startSmax: Smaxc, startYOffset: yOffc,
          chartW: chartWc, chartH: chartHc, autoRangec,
        };
        canvasEl.style.cursor = isAxisZoneDrag ? 'ew-resize' : 'grabbing';
        e.preventDefault();
      };

      pz.onMouseMove = e => {
        if (pz.sliceDrag) {
          const rect = canvasEl.getBoundingClientRect();
          const { x: cx } = clientXY(e, rect);
          const { padc, chartWc, Sminc, Smaxc } = currentGeometry();
          const price = Sminc + (cx - padc.l) / chartWc * (Smaxc - Sminc);
          if (price > 0 && pz.opts.onSliceChange) pz.opts.onSliceChange(pz.sliceDrag.id, price);
          return;
        }
        if (pz.strikeDrag) {
          const rect = canvasEl.getBoundingClientRect();
          const { x: cx } = clientXY(e, rect);
          const { padc, chartWc, Sminc, Smaxc } = currentGeometry();
          const inc = pz.opts.strikeIncrement || 5;
          const S = Math.round((Sminc + (cx - padc.l) / chartWc * (Smaxc - Sminc)) / inc) * inc;
          // Report the strike as it currently stands (K, updated on every tick) as the
          // "old" value to look up -- not the drag's original strike -- so the caller's
          // leg lookup finds the leg wherever a PRIOR tick this same drag already moved
          // it to, not where the drag started.
          if (S > 0 && S !== pz.strikeDrag.K) {
            if (pz.opts.onStrikeChange) pz.opts.onStrikeChange(pz.strikeDrag.K, S);
            pz.strikeDrag.K = S;
          }
          return;
        }
        if (!pz.drag) return;
        const rect = canvasEl.getBoundingClientRect();
        const { x: cx, y: cy } = clientXY(e, rect);
        const { startX, startY, startSmin, startSmax, startYOffset, chartW: cW0, chartH: cH0, autoRangec: startAutoRangec } = pz.drag;
        const dx = cx - startX, dy = cy - startY;
        if (pz.drag.type === 'xzoom') {
          // Drag right narrows the range (zoom in), drag left widens it (zoom out) — a
          // horizontal-only gesture on the axis strip, anchored on the drag's starting center.
          const range = startSmax - startSmin;
          const zoomFactor = Math.max(0.1, 1 - dx / cW0);
          // Floored against the position's own auto-fit range (see minRangeFor above), not
          // just "5% of wherever this drag started" -- the old floor alone still let a drag
          // narrow the range on top of an ALREADY-zoomed-in state, compounding toward zero
          // across repeated drags.
          const newRange = Math.max(minRangeFor(startAutoRangec), range * 0.05, range * zoomFactor);
          const center = (startSmin + startSmax) / 2;
          pz.viewport = { Smin: center - newRange / 2, Smax: center + newRange / 2, yOffset: startYOffset };
        } else {
          const priceShift = -dx / cW0 * (startSmax - startSmin);
          // yRange isn't known precisely mid-drag without re-deriving the full y-scale; a pan's
          // vertical shift only needs to feel proportionate to the drag, not pixel-exact, since
          // the very next render recomputes minY/maxY from the shifted curve anyway — reusing
          // the horizontal price range as the vertical shift's scale keeps pan speed sane across
          // wildly different position sizes (a $50 vertical vs a $6000 SPX condor) without a
          // second, separate curve-sampling pass just to seed a drag gesture.
          const yShift = dy / cH0 * (startSmax - startSmin);
          pz.viewport = { Smin: startSmin + priceShift, Smax: startSmax + priceShift, yOffset: startYOffset + yShift };
        }
        redraw();
      };

      pz.endDrag = () => {
        pz.drag = null;
        canvasEl.style.cursor = 'default';
        if (pz.sliceDrag) { pz.sliceDrag = null; redraw(); }
        if (pz.strikeDrag) {
          pz.strikeDrag = null;
          if (pz.opts.onStrikeDragEnd) pz.opts.onStrikeDragEnd();
          redraw();
        }
      };

      pz.onWheel = e => {
        const rect = canvasEl.getBoundingClientRect();
        const { x: cx } = clientXY(e, rect);
        const { padc, chartWc, Sminc, Smaxc, yOffc, autoRangec } = currentGeometry();
        if (cx < padc.l || cx > (rect.width - padc.r)) return;
        e.preventDefault();
        // Zoom centered on the cursor: the price under the cursor stays under the cursor.
        const cursorPrice = Sminc + (cx - padc.l) / chartWc * (Smaxc - Sminc);
        // Wheel down = zoom out, up = zoom in, scaled by how far the wheel moved: a mouse notch
        // (~100px) zooms 5%, and a trackpad's many small deltas zoom gently instead of 10% each.
        const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY; // Firefox reports lines
        const zoomFactor = Math.exp(Math.max(-100, Math.min(100, dy)) * 0.0005);
        // Floored against the position's own auto-fit range (see minRangeFor above) -- each
        // wheel tick multiplies the CURRENT range by 0.9 with no floor otherwise, so repeated
        // zoom-in ticks (an accidental trackpad scroll easily fires dozens in a row) compound
        // geometrically toward zero. A real, confirmed bug: the chart ended up a single flat
        // color block with meaningless axis labels after enough scroll-to-zoom.
        const range = Math.max(minRangeFor(autoRangec), (Smaxc - Sminc) * zoomFactor);
        const frac = (cursorPrice - Sminc) / (Smaxc - Sminc);
        pz.viewport = { Smin: cursorPrice - range * frac, Smax: cursorPrice + range * (1 - frac), yOffset: yOffc };
        redraw();
      };

      pz.onDblClick = e => {
        const rect = canvasEl.getBoundingClientRect();
        const { x: cx } = clientXY(e, rect);
        const hit = sliceHit(cx);
        if (hit) {
          if (pz.opts.onSliceRemove) pz.opts.onSliceRemove(hit.id);
          return;
        }
        pz.viewport = null; // reset to the auto-computed window
        redraw();
      };

      // Touch: two-finger pinch zoom (centered on the pinch midpoint), one-finger pan,
      // double-tap reset (a second tap within 350ms of the first, close to the same spot).
      const touchDist = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      let lastTapAt = 0, lastTapX = 0, lastTapY = 0;

      pz.onTouchStart = e => {
        const rect = canvasEl.getBoundingClientRect();
        const { padc, chartWc, chartHc, Sminc, Smaxc, yOffc, autoRangec } = currentGeometry();
        if (e.touches.length === 2) {
          const [t0, t1] = [e.touches[0], e.touches[1]];
          const midX = (t0.clientX + t1.clientX) / 2 - rect.left;
          pz.touch = {
            mode: 'pinch', dist: touchDist(t0, t1),
            midPrice: Sminc + (midX - padc.l) / chartWc * (Smaxc - Sminc),
            Smin: Sminc, Smax: Smaxc, yOffset: yOffc, chartW: chartWc, chartH: chartHc, autoRangec,
          };
        } else if (e.touches.length === 1) {
          const t0 = e.touches[0];
          const now = Date.now();
          const tx = t0.clientX - rect.left, ty = t0.clientY - rect.top;
          if (now - lastTapAt < 350 && Math.hypot(tx - lastTapX, ty - lastTapY) < 24) {
            pz.viewport = null; // double-tap reset
            lastTapAt = 0;
            redraw();
            pz.touch = null;
            return;
          }
          lastTapAt = now; lastTapX = tx; lastTapY = ty;
          pz.touch = {
            mode: 'pan', startX: tx, startY: ty,
            Smin: Sminc, Smax: Smaxc, yOffset: yOffc, chartW: chartWc, chartH: chartHc,
          };
        }
      };

      pz.onTouchMove = e => {
        if (!pz.touch) return;
        e.preventDefault();
        const rect = canvasEl.getBoundingClientRect();
        if (pz.touch.mode === 'pinch' && e.touches.length === 2) {
          const [t0, t1] = [e.touches[0], e.touches[1]];
          const newDist = touchDist(t0, t1);
          const { midPrice, Smin: S0, Smax: S1, yOffset: y0, autoRangec: touchAutoRangec } = pz.touch;
          const scale = pz.touch.dist / Math.max(newDist, 1);
          const range = Math.max(minRangeFor(touchAutoRangec), (S1 - S0) * scale);
          const frac = (midPrice - S0) / (S1 - S0);
          pz.viewport = { Smin: midPrice - range * frac, Smax: midPrice + range * (1 - frac), yOffset: y0 };
          redraw();
        } else if (pz.touch.mode === 'pan' && e.touches.length === 1) {
          const t0 = e.touches[0];
          const tx = t0.clientX - rect.left, ty = t0.clientY - rect.top;
          const { startX, startY, Smin: S0, Smax: S1, yOffset: y0, chartW: cW0, chartH: cH0 } = pz.touch;
          const priceShift = -(tx - startX) / cW0 * (S1 - S0);
          const yShift = (ty - startY) / cH0 * (S1 - S0);
          pz.viewport = { Smin: S0 + priceShift, Smax: S1 + priceShift, yOffset: y0 + yShift };
          redraw();
        }
      };

      pz.onTouchEnd = () => { pz.touch = null; };

      canvasEl.addEventListener('mousedown', pz.onMouseDown);
      window.addEventListener('mousemove', pz.onMouseMove);
      window.addEventListener('mouseup', pz.endDrag);
      canvasEl.addEventListener('wheel', pz.onWheel, { passive: false });
      canvasEl.addEventListener('dblclick', pz.onDblClick);
      canvasEl.addEventListener('touchstart', pz.onTouchStart, { passive: true });
      canvasEl.addEventListener('touchmove', pz.onTouchMove, { passive: false });
      canvasEl.addEventListener('touchend', pz.onTouchEnd);
      canvasEl.addEventListener('touchcancel', pz.onTouchEnd);

      _panZoomState.set(canvasEl, pz);
    } else {
      pz.opts = latestOptsPZ;
    }
  }

  return {
    stats,
    theoPl: (t0payoff && spot) ? t0payoff(spot) : null,
    greeks: (greeksAt && spot) ? greeksAt(spot) : null,
    teardown() {
      ctx.clearRect(0, 0, cW, cH);
    },
  };
}
