import { EMA } from './ema.js';
import { SMA } from './sma.js';
import { Bollinger } from './bollinger.js';
import { Keltner } from './keltner.js';
import { ExpectedMove } from './expected_move.js';
import { MACross } from './ma_cross.js';
import { VWAP } from './vwap.js';
import { RSI } from './rsi.js';
import { MACD } from './macd.js';
import { Volume } from './volume.js';
import { VolumeProfile } from './volume_profile.js';
import { SessionProfiles } from './session_profiles.js';
import { PrevDay } from './prev_day.js';
import { ORB } from './orb.js';
import { chartTipHtml } from '../chart_tips.js';

// Color palettes for multi-instance indicators. Index = instance order.
export const PALETTE = {
  ema: ['#42a5f5', '#26c6da', '#5e35b1', '#7e57c2', '#1e88e5', '#00acc1'],
  sma: ['#ff9800', '#fbc02d', '#fb8c00', '#f57c00', '#e65100', '#ffa726'],
  session_vp: ['#fbc02d'],  // the POC line
  orb: ['#a78bfa', '#f472b6', '#22d3ee', '#facc15', '#4ade80', '#fb7185'],
};

export const INDICATORS = {
  ema:            { label: 'EMA',            category: 'overlay', factory: (p) => new EMA(p),           defaults: { period: 20 },  multi: true,  param: 'period' },
  sma:            { label: 'SMA',            category: 'overlay', factory: (p) => new SMA(p),           defaults: { period: 50 },  multi: true,  param: 'period' },
  ma_cross:       { label: 'MA Cross Arrows', category: 'overlay', factory: () => new MACross(), defaults: {}, multi: false },
  bb:             { label: 'Bollinger Bands (20, 2)', category: 'overlay', factory: (p) => new Bollinger(p), defaults: { period: 20, mult: 2 }, multi: false },
  kc:             { label: 'Keltner Channels (20, 2)', category: 'overlay', factory: (p) => new Keltner(p), defaults: { period: 20, mult: 2, atrPeriod: 10 }, multi: false },
  exp_move:       { label: 'Expected Move ±1σ/±2σ', category: 'overlay', factory: (p) => new ExpectedMove(p), defaults: {}, multi: false },
  vwap:           { label: 'VWAP',           category: 'overlay', factory: ()  => new VWAP(),           defaults: {},              multi: false },
  prev_day:       { label: 'Prev Day Levels',category: 'overlay', factory: (p) => new PrevDay(p),       defaults: {},              multi: false },
  orb:            { label: 'ORB',            category: 'overlay', factory: (p) => new ORB(p),           defaults: { minutes: 30 }, multi: true,  param: 'minutes' },
  volume_profile: { label: 'Volume Profile', category: 'overlay', factory: (p) => new VolumeProfile(p), defaults: {},              multi: false },
  // Built by the pane (_newSessionProfiles): its sources are fed separately. `days` = sessions shown.
  session_vp:     { label: 'Volume Profile (per day)', category: 'overlay', factory: (p) => new SessionProfiles(p), defaults: { days: 5 }, multi: true, param: 'days' },
  volume:         { label: 'Volume',         category: 'subpane', factory: ()  => new Volume(),         defaults: {},              multi: false },
  rsi:            { label: 'RSI',            category: 'subpane', factory: (p) => new RSI(p),           defaults: { period: 14 },  multi: false },
  macd:           { label: 'MACD',           category: 'subpane', factory: (p) => new MACD(p),          defaults: { fast: 12, slow: 26, signal: 9 }, multi: false },
};

// Migrate legacy state shapes: ema/sma used to be booleans; now they're
// arrays of periods. Run on every panel build so localStorage from older
// versions doesn't break.
export function migrateActiveState(active) {
  if (!active) return {};
  const out = { ...active };
  for (const id of ['ema', 'sma']) {
    if (typeof out[id] === 'boolean') {
      out[id] = out[id] ? [INDICATORS[id].defaults.period] : [];
    } else if (!Array.isArray(out[id])) {
      out[id] = [];
    }
  }
  return out;
}

// A multi-instance line's colour: the one picked for it (state.indicators.colors, keyed by
// holderKey), else its palette slot.
export function lineColor(paneState, id, period) {
  const picked = paneState?.indicators?.colors?.[holderKey(id, period)];
  if (picked) return picked;
  const palette = PALETTE[id] || ['#888'];
  const idx = (paneState?.indicators?.active?.[id] || []).indexOf(period);
  return palette[Math.max(idx, 0) % palette.length];
}

// Holder key for a given indicator instance.
// Singletons: just the id. Multi-instance: `id:period`.
export function holderKey(id, period) {
  return INDICATORS[id]?.multi ? `${id}:${period}` : id;
}

export function buildIndicatorsPanel(rootEl, paneState, callbacks) {
  const { onToggleSingleton, onAddMulti, onRemoveMulti, onEditMulti, onPreviewColor, onSetColor } = callbacks;
  rootEl.innerHTML = '';
  paneState.indicators = paneState.indicators || {};
  paneState.indicators.active = migrateActiveState(paneState.indicators.active);
  const active = paneState.indicators.active;

  const groups = { overlay: 'Overlays', subpane: 'Sub-panes' };
  for (const [groupKey, groupLabel] of Object.entries(groups)) {
    const section = document.createElement('div');
    section.className = 'ind-group';
    const h = document.createElement('h4');
    h.textContent = groupLabel;
    section.appendChild(h);

    for (const [id, def] of Object.entries(INDICATORS)) {
      if (def.category !== groupKey) continue;
      if (def.multi) {
        // Header row with label + "+" button
        const header = document.createElement('div');
        header.className = 'ind-row ind-multi-header';
        const labelSpan = document.createElement('span');
        labelSpan.textContent = def.label;
        const multiTip = chartTipHtml(id, def.label);
        if (multiTip) header.classList.add('ind-has-tip');
        const addBtn = document.createElement('button');
        addBtn.className = 'ind-add';
        addBtn.textContent = '+';
        addBtn.title = `Add ${def.label}`;
        addBtn.addEventListener('click', () => {
          const input = prompt(`Add ${def.label} ${def.param}:`, String(def.defaults[def.param]));
          if (input === null) return;
          const period = parseInt(input, 10);
          if (!Number.isFinite(period) || period < 1) return;
          if ((active[id] || []).includes(period)) return; // dedupe
          onAddMulti(id, period);
        });
        header.appendChild(labelSpan);
        if (multiTip) labelSpan.insertAdjacentHTML('beforeend', multiTip);
        header.appendChild(addBtn);
        section.appendChild(header);

        // One child row per active instance
        for (const period of (active[id] || [])) {
          const row = document.createElement('div');
          row.className = 'ind-row ind-multi-instance';
          // The swatch is a colour picker: dragging previews on the chart, closing it saves.
          const swatch = document.createElement('label');
          swatch.className = 'ind-swatch';
          swatch.title = 'Change colour';
          const color = lineColor(paneState, id, period);
          swatch.style.background = color;
          const picker = document.createElement('input');
          picker.type = 'color';
          picker.value = color;
          picker.addEventListener('input', () => {
            swatch.style.background = picker.value;
            onPreviewColor?.(id, period, picker.value);
          });
          picker.addEventListener('change', () => onSetColor?.(id, period, picker.value));
          swatch.appendChild(picker);

          const periodSpan = document.createElement('span');
          periodSpan.className = 'ind-period';
          periodSpan.textContent = period;
          periodSpan.title = `Click to edit ${def.param}`;
          periodSpan.addEventListener('click', () => {
            const input = prompt(`${def.label} ${def.param}:`, String(period));
            if (input === null) return;
            const newPeriod = parseInt(input, 10);
            if (!Number.isFinite(newPeriod) || newPeriod < 1) return;
            if (newPeriod === period) return;
            if ((active[id] || []).includes(newPeriod)) return;
            onEditMulti(id, period, newPeriod);
          });

          const removeBtn = document.createElement('button');
          removeBtn.className = 'ind-remove';
          removeBtn.textContent = '×';
          removeBtn.title = 'Remove';
          removeBtn.addEventListener('click', () => onRemoveMulti(id, period));

          row.appendChild(swatch);
          row.appendChild(document.createTextNode(' ' + def.label + '('));
          row.appendChild(periodSpan);
          row.appendChild(document.createTextNode(') '));
          row.appendChild(removeBtn);
          section.appendChild(row);
        }
      } else {
        // Singleton checkbox row
        const row = document.createElement('label');
        row.className = 'ind-row';
        const checked = !!active[id];
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.dataset.id = id;
        cb.checked = checked;
        cb.addEventListener('change', (e) => onToggleSingleton(id, e.target.checked));
        row.appendChild(cb);
        row.appendChild(document.createTextNode(' ' + def.label));
        // The tip sits beside the label, not inside it, so a click on the tip doesn't tick the box.
        const tip = chartTipHtml(id, def.label);
        if (tip) {
          const wrap = document.createElement('div');
          wrap.className = 'ind-has-tip';
          wrap.appendChild(row);
          wrap.insertAdjacentHTML('beforeend', tip);
          section.appendChild(wrap);
        } else {
          section.appendChild(row);
        }
      }
    }
    rootEl.appendChild(section);
  }
}
