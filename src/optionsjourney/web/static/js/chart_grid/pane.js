import { PaneFeed, isFutures, TIMEFRAMES, fetchContract } from './pane_feed.js';
import { mergeBar, futuresSessionKey } from '../charts/bars.js';
import { INDICATORS, buildIndicatorsPanel, migrateActiveState, holderKey, lineColor } from './indicators/index.js';
import { DayDividerPrimitive, computeDayBoundaries } from './day_dividers.js';
import { VolumeProfilePrimitive } from './volume_profile_renderer.js';
import { VolumeProfile, SourceVolumeProfile, VP_PROXY } from './indicators/volume_profile.js';
import { SessionProfiles } from './indicators/session_profiles.js';
import { SessionProfilesPrimitive } from './session_profiles_renderer.js';
import { ORBPrimitive } from './orb_renderer.js';
import { DrawingLayer } from '../charts/chart_drawings_layer.js';
import { getDrawingStore } from '../charts/drawing_store.js';
import { findCrosses } from './indicators/ma_cross.js';
import { CrossArrowsPrimitive } from './cross_arrows.js';
import { RsiBandPrimitive } from './rsi_band.js';
import { volIndexesFor, openByDate, barDate, SCHWAB_ONLY } from './indicators/expected_move.js';
import { attachCrosshairLegend, formatTick, formatBarTime, isDailyOrLonger } from './crosshair_legend.js';

// Day dividers only on intraday timeframes below 4h: 4h bars are two per session, so a line on
// every day is clutter, and 1d and longer candles are a day or more.
const showsDayDividers = tf => tf !== '4h' && !isDailyOrLonger(tf);
const TF_SECONDS = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400 };
// Indicators rebuilt (and their source feeds reloaded) when the symbol, timeframe or bucket size changes.
const SOURCED = new Set(['volume_profile', 'session_vp']);

const LWC = () => window.LightweightCharts;
const EXP_KEYS = ['up1', 'dn1', 'up2', 'dn2'];
const SUBPANE_SHARE = 0.15;
const MIN_PRICE_SHARE = 0.75;
// Indicator lines as Tracker2's analyzer charts draw them: 1px (the library's default is 3), with no
// dotted line at the last value and no label on the price axis -- only the ticker's price gets one.
// The crosshair dot on each line is 2px, not the library's 4px, so it doesn't cover the candles.
const NO_LABEL = { priceLineVisible: false, lastValueVisible: false };
const THIN = { lineWidth: 1, crosshairMarkerRadius: 2, ...NO_LABEL };
// RSI outside 35–65 takes the band colour; inside, the series' own grey.
const rsiPoint = (p) => (p.value > 65 ? { ...p, color: '#ff9800' }
  : p.value < 35 ? { ...p, color: '#f2545b' } : p);
const lastLevelDay = (points) => {
  for (let i = points.length - 1; i >= 0; i--) if (points[i].up1 != null) return barDate(points[i].time);
  return null;
};

export class ChartPane {
  // Options for a pane hosted outside the grid (Tracker2's analyzer charts; unused here since Analyze went):
  //   resolveContract(root) -> Promise<contract|null>: which contract a futures root charts
  //     (default: the active one, pane_feed.js).
  //   symbolLocked: the host sets the symbol (setSymbol); the input is read-only, no link button.
  //   priceLinesLabel: the drawer gets an on/off row of that name for setPriceLines' lines,
  //     saved as state.priceLines (on unless false).
  constructor({ root, state, router, onStateChange, getPrevClose, onPopout, drawingStore,
                onSymbolChange, onLinkToggle, resolveContract, symbolLocked, priceLinesLabel }) {
    this.root = root;
    this.state = state;
    this.router = router;
    this.onStateChange = onStateChange;
    this.getPrevClose = getPrevClose;
    this.onPopout = onPopout;
    this.onSymbolChange = onSymbolChange; // a linked pane's symbol changed: update the others
    this.onLinkToggle = onLinkToggle;     // this pane was linked or unlinked
    this.resolveContract = resolveContract;
    this.symbolLocked = !!symbolLocked;
    this.priceLinesLabel = priceLinesLabel || null;
    this._extLines = [];       // setPriceLines' lines
    this._extPriceLines = [];  // ...as drawn on the candle series
    this.drawingStore = drawingStore || getDrawingStore();
    this.indicators = new Map(); // holderKey -> { instance, series[], id, period }
    this._paneIndex = new Map();  // indicator id -> lightweight-charts pane index
    this._lastCandles = [];
    this._build();
    this._load();
  }

  // The bars as drawn (a new array on every change, so callers can cache by identity).
  get candles() { return this._lastCandles; }

  _build() {
    this.root.innerHTML = `
      <div class="pane-chrome">
        <span class="pane-symbol-wrap"><input class="pane-symbol" /><button class="pane-symbol-clear" type="button" title="Clear symbol" aria-label="Clear symbol">&times;</button></span>
        <button class="pane-link" type="button" aria-label="Link symbol">
          <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
            <path d="M6.5 9.5 L9.5 6.5"/>
            <path d="M7 4.5 L8.5 3 a2.5 2.5 0 0 1 3.5 3.5 L10.5 8"/>
            <path d="M9 11.5 L7.5 13 a2.5 2.5 0 0 1 -3.5 -3.5 L5.5 8"/>
          </svg>
        </button>
        <select class="pane-tf">
          ${TIMEFRAMES.map(t =>
            `<option ${t === this.state.timeframe ? 'selected' : ''}>${t}</option>`).join('')}
        </select>
        <button class="pane-indicators-btn" title="Indicators" aria-label="Indicators">
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
            <path d="M2 12 L5 8 L8 10 L11 4 L14 7"/>
            <circle cx="5" cy="8" r="1" fill="currentColor" stroke="none"/>
            <circle cx="8" cy="10" r="1" fill="currentColor" stroke="none"/>
            <circle cx="11" cy="4" r="1" fill="currentColor" stroke="none"/>
          </svg>
        </button>
        <span class="pane-price"></span>
        <span class="pane-change"></span>
        ${this.onPopout ? '<button class="pane-popout" title="Pop this chart out into its own window">⧉</button>' : ''}
      </div>
      <div class="pane-chart"><div class="pane-legend"></div></div>
      <aside class="pane-ind-drawer hidden">
        <div class="ind-group pane-draw-group"><h4>Drawing</h4><span class="pane-tools"></span></div>
        <div class="ind-group"><h4>Scale</h4>
          <button class="pane-fit" type="button">
            <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
              <path d="M8 1.5 V4 M8 12 V14.5 M5.5 3.5 L8 1.5 L10.5 3.5 M5.5 12.5 L8 14.5 L10.5 12.5"/>
              <rect x="6" y="5.5" width="4" height="5" rx="0.5"/>
            </svg>
            <span>Fit candles only</span>
          </button>
        </div>
        ${this.priceLinesLabel ? `<div class="ind-group"><h4>Position</h4>
          <label class="ind-row"><input type="checkbox" class="pane-ext-lines" />${this.priceLinesLabel}</label></div>` : ''}
        <div class="pane-ind-list"></div>
      </aside>
    `;
    this.root.querySelector('.pane-symbol').value = this.state.symbol ?? '';
    this.chartEl = this.root.querySelector('.pane-chart');
    this.plotEl = this.chartEl;
    this.priceEl = this.root.querySelector('.pane-price');
    this.changeEl = this.root.querySelector('.pane-change');
    this.drawerEl = this.root.querySelector('.pane-ind-drawer');
    this.indListEl = this.root.querySelector('.pane-ind-list');

    this.chart = LWC().createChart(this.chartEl, {
      // Colours and fonts follow the tokens in css/chart_grid.css (canvas can't read CSS vars).
      layout: {
        background: { color: '#0b0e14' }, textColor: '#7d8696',
        fontFamily: "'IBM Plex Mono', ui-monospace, Menlo, monospace", fontSize: 10,
        panes: { separatorColor: '#1a1f29', separatorHoverColor: 'rgba(62, 224, 164, 0.18)' },
      },
      grid:   { vertLines: { visible: false }, horzLines: { color: 'rgba(148, 163, 184, 0.045)' } },
      rightPriceScale: { borderColor: '#1a1f29' },
      timeScale: {
        borderColor: '#1a1f29',
        timeVisible: true,
        secondsVisible: false,
        // Breathing room between the last candle and the price-axis border.
        rightOffset: 5,
        // "MMM DD" at the first tick of each day, "HH:MM" otherwise, so sessions
        // are told apart. Read in UTC: bar times are ET wall clock in a UTC epoch.
        tickMarkFormatter: (timeSec, tickMarkType, locale) =>
          formatTick(timeSec, tickMarkType, this.state.timeframe, locale),
      },
      // The crosshair's time label on the axis, in the same zone as the ticks.
      localization: { timeFormatter: (timeSec) => formatBarTime(timeSec, this.state.timeframe) },
      crosshair: {
        mode: LWC().CrosshairMode.Normal,
        vertLine: { color: '#4a5466', labelBackgroundColor: '#1f2633' },
        horzLine: { color: '#4a5466', labelBackgroundColor: '#1f2633' },
      },
      autoSize: true,
    });
    this.candleSeries = this.chart.addSeries(LWC().CandlestickSeries, {
      upColor: '#22c39a', downColor: '#f2545b',
      borderVisible: false, wickUpColor: '#22c39a', wickDownColor: '#f2545b',
    });
    this.dayDividers = new DayDividerPrimitive();
    this.candleSeries.attachPrimitive(this.dayDividers);
    this._dividerTimes = [];
    this.legend = attachCrosshairLegend({
      chart: this.chart, series: this.candleSeries,
      el: this.root.querySelector('.pane-legend'),
      getTimeframe: () => this.state.timeframe,
      getLastBar: () => this._lastCandles[this._lastCandles.length - 1],
    });

    const symInput = this.root.querySelector('.pane-symbol');
    symInput.addEventListener('change', (e) => {
      this._setSymbol(e.target.value.toUpperCase().trim());
    });
    // The × only empties the box for typing; the chart keeps its symbol until a new
    // one is entered, so leaving the box blank puts the current symbol back.
    // mousedown is prevented so the click doesn't blur (and restore) the box first.
    const symClear = this.root.querySelector('.pane-symbol-clear');
    symClear.addEventListener('mousedown', (e) => e.preventDefault());
    symClear.addEventListener('click', () => { symInput.value = ''; symInput.focus(); });
    symInput.addEventListener('blur', () => {
      if (!symInput.value.trim()) symInput.value = this.state.symbol ?? '';
    });
    this.linkBtn = this.root.querySelector('.pane-link');
    this.linkBtn.addEventListener('click', () => this._toggleLink());
    this._renderLink();
    if (this.symbolLocked) {
      this.root.querySelector('.pane-symbol').readOnly = true;
      symClear.style.display = 'none';
      this.linkBtn.style.display = 'none';
    }
    const extToggle = this.root.querySelector('.pane-ext-lines');
    if (extToggle) {
      extToggle.checked = this._extLinesOn();
      extToggle.addEventListener('change', () => {
        if (extToggle.checked) delete this.state.priceLines;
        else this.state.priceLines = false;
        this._drawExtLines();
        this.onStateChange?.();
      });
    }
    this.fitBtn = this.root.querySelector('.pane-fit');
    this.fitBtn.addEventListener('click', () => this._toggleFit());
    this._renderFit();
    this.root.querySelector('.pane-tf').addEventListener('change', (e) => {
      this._setTimeframe(e.target.value);
    });
    const indBtn = this.root.querySelector('.pane-indicators-btn');
    indBtn.addEventListener('click', () => {
      this.drawerEl.classList.toggle('hidden');
      this._renderIndicatorsPanel();
    });
    // A press anywhere outside the drawer (and its button) closes it. pointerdown in the
    // capture phase runs before the drawer re-renders, so a press on a row being removed
    // still counts as inside.
    this._onOutsidePress = (e) => {
      if (this.drawerEl.classList.contains('hidden')) return;
      if (this.drawerEl.contains(e.target) || indBtn.contains(e.target)) return;
      this.drawerEl.classList.add('hidden');
    };
    document.addEventListener('pointerdown', this._onOutsidePress, true);
    this.root.querySelector('.pane-popout')?.addEventListener('click', () => this.onPopout());

    // The drawing tools live in the indicators drawer. Picking one closes the drawer so the
    // chart is free to draw on; the indicators button stays lit while a tool is picked.
    this.drawingLayer = new DrawingLayer(this, this.root.querySelector('.pane-tools'), {
      interval: this.state.timeframe, store: this.drawingStore,
      onToolChange: (tool) => {
        indBtn.classList.toggle('drawing', !!tool);
        if (tool) this.drawerEl.classList.add('hidden');
      },
    });
    this.drawingLayer.setTarget(this.state.symbol, this.state.timeframe);
  }

  _load() {
    this._feed?.stop();
    if (this._destroyed) return;
    this.priceEl.textContent = '…';
    this._renderChange(null);
    this._feed = new PaneFeed({
      ...(this.resolveContract ? { resolveContract: this.resolveContract } : {}),
      router: this.router,
      symbol: this.state.symbol,
      interval: this.state.timeframe,
      onBars: bars => this._setBars(bars),
      onStep: r => this._onStep(r),
      onError: msg => { this.priceEl.textContent = msg; },
    });
    this._feed.start().catch(e => {
      console.error('pane feed failed', e);
      this.priceEl.textContent = 'no data';
    });
  }

  _setBars(candles) {
    if (this._destroyed) return;
    const tf = this.state.timeframe;
    this.candleSeries.setData(candles);
    this._lastCandles = candles;
    this._dividerTimes = showsDayDividers(tf) ? computeDayBoundaries(candles) : [];
    this.dayDividers.setTimes(this._dividerTimes);
    this._rebuildIndicators(candles);
    // Canonical prev close: a stock's daily series, a future's prior settlement (cached in
    // GridDashboard so all panes on the same symbol share one fetch). Without one, the pane
    // walks its own bars.
    this._prevClose = null;
    const sym = this.state.symbol;
    const prevClosePromise = this.getPrevClose
      ? this.getPrevClose(sym)
      : Promise.resolve(null);
    prevClosePromise.then(v => {
      if (sym !== this.state.symbol) return;
      this._prevClose = v ?? this._computePrevClose(this._lastCandles);
      const lastCandle = this._lastCandles?.[this._lastCandles.length - 1];
      if (lastCandle) this._renderChange(lastCandle.close);
    });
    const last = candles[candles.length - 1];
    if (last) {
      this.priceEl.textContent = last.close.toFixed(2);
      this._renderChange(last.close);
    } else {
      this.priceEl.textContent = 'no data';
    }
    this._restoreIndicators();
    this._refreshCrosses();
    this.legend.refresh();
  }

  // Turn on the indicators saved in state. Idempotent: _enableInstance skips live ones.
  _restoreIndicators() {
    const active = this._ensureActive();
    for (const [id, val] of Object.entries(active)) {
      const def = INDICATORS[id];
      if (!def) continue;
      if (def.multi && Array.isArray(val)) {
        for (const period of val) this._enableInstance(id, period);
      } else if (!def.multi && val) {
        this._enableInstance(id);
      }
    }
  }

  _onStep(r) {
    if (this._destroyed) return;
    this._lastCandles = mergeBar(this._lastCandles, r.candle);
    const { time, open, high, low, close } = r.candle;
    this.candleSeries.update({ time, open, high, low, close });
    this.priceEl.textContent = r.candle.close.toFixed(2);
    this._renderChange(r.candle.close);
    this.legend.refresh();
    const isClose = (r.type === 'close+open');
    // If the new bar crossed an ET calendar date boundary, add a divider.
    if (isClose && showsDayDividers(this.state.timeframe) && r.prevCandle) {
      const newDivs = computeDayBoundaries([r.prevCandle, r.candle]);
      if (newDivs.length > 0) {
        this._dividerTimes = [...this._dividerTimes, ...newDivs];
        this.dayDividers.setTimes(this._dividerTimes);
      }
    }
    for (const [key, holder] of this.indicators) {
      try {
        const out = holder.instance.update(r.candle, { isClose });
        this._applyIndicatorUpdate(holder.id, holder, out);
      } catch (e) {
        console.error(`Indicator ${key} update failed; disabling`, e);
        holder.unsubVol?.();
        for (const s of holder.series) this.chart.removeSeries(s);
        if (holder._primitive) this.candleSeries.detachPrimitive(holder._primitive);
        if (holder._priceLines) for (const pl of holder._priceLines) this.candleSeries.removePriceLine(pl);
        this.indicators.delete(key);
      }
    }
    this._refreshCrosses();
  }

  // MA Cross Arrows: markers on the candles wherever two active EMA/SMA lines cross, read
  // from the lines as drawn. Called whenever those lines change.
  _refreshCrosses() {
    const h = this.indicators.get('ma_cross');
    if (!h) return;
    const lines = [...this.indicators.values()]
      .filter(x => x.id === 'ema' || x.id === 'sma')
      .map(x => ({ id: x.id, period: x.period, points: x.series[0]?.data?.() || [] }));
    if (!h._arrows) {
      h._arrows = new CrossArrowsPrimitive();
      this.candleSeries.attachPrimitive(h._arrows);
    }
    h._arrows.setArrows(findCrosses(lines), this._lastCandles);
  }

  _renderIndicatorsPanel() {
    buildIndicatorsPanel(this.indListEl, this.state, {
      onToggleSingleton: (id, on) => this._toggleSingleton(id, on),
      onAddMulti: (id, period) => this._addMulti(id, period),
      onRemoveMulti: (id, period) => this._removeMulti(id, period),
      onEditMulti: (id, oldP, newP) => {
        // A new period keeps the line's picked colour.
        const colors = this.state.indicators.colors;
        const picked = colors?.[holderKey(id, oldP)];
        this._removeMulti(id, oldP);
        if (picked) colors[holderKey(id, newP)] = picked;
        this._addMulti(id, newP);
      },
      onPreviewColor: (id, period, color) => this._applyLineColor(id, period, color),
      onSetColor: (id, period, color) => {
        this.state.indicators.colors = { ...(this.state.indicators.colors || {}), [holderKey(id, period)]: color };
        this._applyLineColor(id, period, color);
        this.onStateChange?.();
      },
    });
  }

  _ensureActive() {
    this.state.indicators = this.state.indicators || {};
    this.state.indicators.active = migrateActiveState(this.state.indicators.active);
    return this.state.indicators.active;
  }

  _toggleSingleton(id, on) {
    const def = INDICATORS[id];
    if (!def || def.multi) return;
    const active = this._ensureActive();
    active[id] = !!on;
    if (on) this._enableInstance(id);
    else this._disableInstance(id);
    this.onStateChange?.();
  }

  _addMulti(id, period) {
    const def = INDICATORS[id];
    if (!def || !def.multi) return;
    const active = this._ensureActive();
    active[id] = active[id] || [];
    if (active[id].includes(period)) return;
    active[id].push(period);
    active[id].sort((a, b) => a - b);
    this._enableInstance(id, period);
    this._renderIndicatorsPanel();
    this.onStateChange?.();
  }

  _removeMulti(id, period) {
    const def = INDICATORS[id];
    if (!def || !def.multi) return;
    const active = this._ensureActive();
    active[id] = (active[id] || []).filter(p => p !== period);
    if (this.state.indicators.colors) delete this.state.indicators.colors[holderKey(id, period)];
    this._disableInstance(id, period);
    this._renderIndicatorsPanel();
    this.onStateChange?.();
  }

  // Create the chart series + indicator instance for one indicator+optional period.
  _enableInstance(id, period) {
    const def = INDICATORS[id];
    if (!def) return;
    const key = holderKey(id, period);
    if (this.indicators.has(key)) return;
    let params = def.defaults;
    if (def.multi && period !== undefined) {
      params = { ...def.defaults, [def.param]: period };
    }
    if (id === 'prev_day' && isFutures(this.state.symbol)) {
      params = { ...params, dayKey: futuresSessionKey };
    }
    const inst = this._newSourced(id, period) || def.factory(params);
    const colorIdx = def.multi ? (this._ensureActive()[id] || []).indexOf(period) : 0;
    const series = this._createSeriesFor(id, colorIdx, period);
    const holder = { instance: inst, series, id, period };
    this.indicators.set(key, holder);
    const init = inst.init(this._lastCandles);
    this._seedIndicator(id, holder, init);
    if (def.category === 'subpane') this._sizeSubpanes();
    if (id === 'exp_move') this._loadExpectedMove(holder);
    if (id === 'volume_profile') this._loadVolumeProxy(holder);
    if (id === 'session_vp') this._loadSessionSources(holder);
    if (id === 'ma_cross' || id === 'ema' || id === 'sma') this._refreshCrosses();
  }

  // Volume Profile: intraday timeframes profile the current session (ETH: a future's from
  // 18:00 ET, a stock's calendar day) from 1m bars whatever the pane's timeframe, so every
  // timeframe agrees on the POC; 1d and longer profile every bar loaded. An index with a
  // future in VP_PROXY (SPX: /ES) takes that future's 1m bars on intraday timeframes, since
  // its own volume is only its ETF's and only in regular hours.
  _newVolumeProfile() {
    const sym = this.state.symbol;
    const tf = this.state.timeframe;
    const intraday = !isDailyOrLonger(tf);
    const proxy = intraday ? VP_PROXY[sym] : null;
    const bucketSize = this._autoBucketSize();
    const sessionKey = !intraday ? null
      : (proxy || isFutures(sym)) ? futuresSessionKey : barDate;
    const source = proxy ? proxy.root : (intraday && tf !== '1m' ? sym : null);
    const inst = source
      ? new SourceVolumeProfile({ bucketSize, sessionKey, basis: !!proxy, scale: proxy?.scale,
        paneSeconds: TF_SECONDS[tf] || 60 })
      : new VolumeProfile({ bucketSize, sessionKey });
    inst.builtFor = `${sym}|${tf}|${bucketSize}`;
    inst.source = source;
    return inst;
  }

  _newSourced(id, period) {
    if (id === 'volume_profile') return this._newVolumeProfile();
    if (id === 'session_vp') return this._newSessionProfiles(period);
    return null;
  }

  // Volume Profile (per day): the last `days` sessions on intraday timeframes, each from its
  // source's 1m bars or, further back than 1m reaches, 5m bars. The source is the symbol itself
  // or, for an index in VP_PROXY, its future. 1d and longer draw nothing.
  _newSessionProfiles(days) {
    const sym = this.state.symbol;
    const tf = this.state.timeframe;
    const intraday = !isDailyOrLonger(tf);
    const proxy = VP_PROXY[sym] || null;
    const bucketSize = this._autoBucketSize();
    const inst = new SessionProfiles({ days: intraday ? days : 0, bucketSize,
      sessionKey: (proxy || isFutures(sym)) ? futuresSessionKey : barDate,
      basis: !!proxy, scale: proxy?.scale ?? 1, paneSeconds: TF_SECONDS[tf] || 60 });
    inst.builtFor = `${sym}|${tf}|${bucketSize}`;
    inst.source = intraday ? (proxy ? proxy.root : sym) : null;
    return inst;
  }

  // The per-day profiles' 1m and 5m source bars through the CandleRouter (shared with any pane
  // on the same feed).
  _loadSessionSources(holder) {
    holder.unsubVol?.();
    const inst = holder.instance;
    if (!inst.source) return;
    let stopped = false;
    const unsubs = [];
    holder.unsubVol = () => { stopped = true; unsubs.forEach(u => u()); holder.unsubVol = null; };
    const redraw = snap => {
      if (this._destroyed || holder.instance !== inst) return;
      holder._primitive?.setSnapshot(snap);
    };
    const contract = isFutures(inst.source)
      ? (this.resolveContract || fetchContract)(inst.source) : Promise.resolve(inst.source);
    contract.then(sym => {
      if (stopped || !sym) return;
      unsubs.push(this.router.subscribe(sym, '1m', {
        onSnapshot: b => redraw(inst.setFine(b)),
        onBar: b => redraw(inst.updateFine(b)),
      }));
      unsubs.push(this.router.subscribe(sym, '5m', {
        onSnapshot: b => redraw(inst.setCoarse(b)),
        onBar: b => redraw(inst.updateCoarse(b)),
      }));
    }).catch(e => console.error('per-day volume profile source failed', e));
  }

  // A sourced profile's 1m bars through the page's CandleRouter (a futures root on its
  // active contract). Each snapshot or bar redraws the profile.
  _loadVolumeProxy(holder) {
    holder.unsubVol?.();
    const inst = holder.instance;
    if (!inst.source) return;
    let stopped = false, unsub = null, bars = [];
    holder.unsubVol = () => { stopped = true; unsub?.(); holder.unsubVol = null; };
    const redraw = () => {
      if (this._destroyed || holder.instance !== inst) return;
      this._applyVolumeProfile(holder, inst.setSourceBars(bars));
    };
    const contract = isFutures(inst.source)
      ? (this.resolveContract || fetchContract)(inst.source) : Promise.resolve(inst.source);
    contract.then(sym => {
      if (stopped || !sym) return;
      unsub = this.router.subscribe(sym, '1m', {
        onSnapshot: b => { bars = b || []; redraw(); },
        onBar: b => { bars = mergeBar(bars, b); redraw(); },
      });
    }).catch(e => console.error('volume profile source failed', e));
  }

  // The expected move needs each day's 09:30 open of its volatility indexes (VIX1D, VIX9D,
  // VIX for the S&P). They come off the chart feed like any pane's bars: 1h bars are session
  // aligned (09:30 first) and go back 45 days, every grid timeframe's span. Today's 09:30 vol
  // bar arrives live, so today's bands appear at the open without a reload.
  // Symbols without an index draw nothing.
  _loadExpectedMove(holder) {
    holder.unsubVol?.();
    const symbol = this.state.symbol;
    holder.loadedFor = symbol;
    const indexes = volIndexesFor(symbol);
    const sources = indexes.map(() => new Map());
    const redraw = () => {
      if (this._destroyed || this.indicators.get('exp_move') !== holder) return;
      holder.instance.setSources(sources);
      this._seedIndicator('exp_move', holder, holder.instance.init(this._lastCandles));
    };
    const unsubs = indexes.map((index, i) => (SCHWAB_ONLY[index]
      ? this._pollSchwabOpens(SCHWAB_ONLY[index], opens => { sources[i] = opens; redraw(); })
      : this.router.subscribe(index, '1h', {
      onSnapshot: bars => { sources[i] = openByDate(bars); redraw(); },
      onBar: bar => {
        // Only a day's first 09:30+ bar matters, once: later ticks never move its open.
        const day = openByDate([bar]);
        const [date, open] = [...day][0] || [];
        if (date && !sources[i].has(date)) { sources[i].set(date, open); redraw(); }
      },
    })));
    holder.unsubVol = () => { unsubs.forEach(u => u()); holder.unsubVol = null; };
  }

  // A vol index the chart feed lacks: Schwab's 30m history (09:30 first, ~60 days, times in
  // the same ET-wall-clock form). Fetched again every minute while the latest chart bar's
  // day has no open yet, so today's appears soon after 09:30. Returns stop().
  _pollSchwabOpens(schwabSymbol, onOpens, everyMs = 60000) {
    let timer = null, stopped = false;
    const run = async () => {
      timer = null;
      try {
        const r = await fetch(`/api/chart/pricehistory/${encodeURIComponent(schwabSymbol)}?frequency=30&period=60`);
        const d = await r.json();
        if (stopped) return;
        if (!d.error) onOpens(openByDate(d.candles || []));
        const last = this._lastCandles[this._lastCandles.length - 1];
        if (!d.error && last && openByDate(d.candles || []).has(barDate(last.time))) return;
      } catch (e) {
        console.warn(`expected move: ${schwabSymbol} history failed`, e);
      }
      if (!stopped) timer = setTimeout(run, everyMs);
    };
    run();
    return () => { stopped = true; if (timer != null) clearTimeout(timer); };
  }

  _disableInstance(id, period) {
    const key = holderKey(id, period);
    const h = this.indicators.get(key);
    if (!h) return;
    h.unsubVol?.();
    if (h._arrows) this.candleSeries.detachPrimitive(h._arrows);
    for (const s of h.series) this.chart.removeSeries(s);
    if (h._primitive) this.candleSeries.detachPrimitive(h._primitive);
    if (h._priceLines) for (const pl of h._priceLines) this.candleSeries.removePriceLine(pl);
    this.indicators.delete(key);
    if (id === 'ema' || id === 'sma') this._refreshCrosses();
    if (INDICATORS[id]?.category === 'subpane') this._sizeSubpanes();
  }

  // Subpanes default to an equal split with the price pane; shrink each to
  // SUBPANE_SHARE of the chart, keeping at least MIN_PRICE_SHARE for price.
  // Setting the price pane's height spreads the rest evenly across the
  // subpanes, and LWC stores it as stretch factors, so it survives resizes.
  // Panes laid out before the chart has a size retry on the next frames.
  _sizeSubpanes(tries = 10) {
    const panes = this.chart.panes?.() ?? [];
    if (panes.length < 2) return;
    const total = panes.reduce((sum, p) => sum + p.getHeight(), 0);
    if (!total) {
      if (tries > 0) requestAnimationFrame(() => this._sizeSubpanes(tries - 1));
      return;
    }
    const subs = panes.length - 1;
    const share = Math.min(SUBPANE_SHARE, (1 - MIN_PRICE_SHARE) / subs);
    panes[0].setHeight(Math.round(total * (1 - share * subs)));
  }

  // The last close of the session before the latest bar's: a future's session opens 18:00 ET
  // the day before, a stock's is its date. Bar times are ET wall clock written as UTC.
  // Null without bars from two sessions.
  _computePrevClose(candles) {
    if (!candles || candles.length === 0) return null;
    const key = isFutures(this.state.symbol) ? futuresSessionKey : barDate;
    const lastKey = key(candles[candles.length - 1].time);
    for (let i = candles.length - 1; i >= 0; i--) {
      if (key(candles[i].time) !== lastKey) return candles[i].close;
    }
    return null;
  }

  _renderChange(currentPrice) {
    if (currentPrice == null || this._prevClose == null) {
      this.changeEl.textContent = '';
      this.changeEl.className = 'pane-change';
      this.priceEl.classList.remove('up', 'down');
      return;
    }
    const delta = currentPrice - this._prevClose;
    const pct = (delta / this._prevClose) * 100;
    const sign = delta >= 0 ? '+' : '';
    const dir = delta >= 0 ? 'up' : 'down';
    // A narrow pane hides the $ change and keeps the % (chart_grid.css).
    this.changeEl.innerHTML = `<span class="chg-abs">${sign}${delta.toFixed(2)}</span>`
      + `<span class="chg-pct">${sign}${pct.toFixed(2)}%</span>`;
    this.changeEl.className = 'pane-change ' + dir;
    this.priceEl.classList.remove('up', 'down');
    this.priceEl.classList.add(dir);
  }

  _allocPane(id) {
    if (this._paneIndex.has(id)) return this._paneIndex.get(id);
    const next = this._paneIndex.size + 1; // pane 0 = price
    this._paneIndex.set(id, next);
    return next;
  }

  // Pick a bucket size that yields roughly the same number of buckets across
  // the visible price range for any symbol. Use the recent high-low range
  // (more stable than a single close) divided by a target bucket count, then
  // snap to a "nice" increment. Falls back to 0.25 if no candles.
  _autoBucketSize() {
    if (!this._lastCandles?.length) return 0.25;
    // Look at the last ~80 bars (roughly a session for 5m intraday).
    const recent = this._lastCandles.slice(-80);
    let hi = -Infinity, lo = Infinity;
    for (const c of recent) {
      if (c.high > hi) hi = c.high;
      if (c.low < lo) lo = c.low;
    }
    const range = Math.max(hi - lo, 0.01);
    const target = range / 60;  // aim for ~60 buckets across the recent range
    // Snap DOWN to the largest "nice" increment ≤ target. Gives finer
    // resolution and aligns to natural price increments per symbol:
    //   SPX (80pt range)  → 1pt buckets
    //   SPY (7pt range)   → 0.1 buckets
    //   SOFI (1pt range)  → 0.01 buckets
    //   NDX (400pt range) → 5pt buckets
    const NICE = [0.01, 0.05, 0.1, 0.25, 0.5, 1, 5, 10, 25, 50, 100];
    let chosen = NICE[0];
    for (const v of NICE) {
      if (v <= target) chosen = v;
      else break;
    }
    return chosen;
  }

  _applyLineColor(id, period, color) {
    const h = this.indicators.get(holderKey(id, period));
    if ((id === 'orb' || id === 'session_vp') && h?._primitive) {
      h._primitive.setColor(color); // the range box; the level lines keep their fixed colours
      return;
    }
    h?.series[0]?.applyOptions({ color });
  }

  _createSeriesFor(id, colorIdx = 0, period) {
    // Main-pane overlays: read live, so the toggle needs no re-apply; null drops the line from the fit.
    const fit = { autoscaleInfoProvider: (base) => (this._fitCandles() ? null : base()) };
    if (id === 'volume') {
      const idx = this._allocPane('volume');
      return [this.chart.addSeries(LWC().HistogramSeries, {
        priceFormat: { type: 'volume' }, ...NO_LABEL,
      }, idx)];
    }
    if (id === 'macd') {
      const idx = this._allocPane('macd');
      return [
        this.chart.addSeries(LWC().LineSeries,      { color: '#42a5f5', ...THIN }, idx),
        this.chart.addSeries(LWC().LineSeries,      { color: '#ff9800', ...THIN }, idx),
        this.chart.addSeries(LWC().HistogramSeries, { color: '#888', ...NO_LABEL }, idx),
      ];
    }
    if (id === 'rsi') {
      const idx = this._allocPane('rsi');
      // RSI keeps its value on its own pane's axis.
      const line = this.chart.addSeries(LWC().LineSeries, { color: '#9ca3af', ...THIN, lastValueVisible: true }, idx);
      line.attachPrimitive(new RsiBandPrimitive(35, 65)); // removed with the series
      return [line];
    }
    if (id === 'vwap') {
      return [
        this.chart.addSeries(LWC().LineSeries, { color: '#fbc02d', ...THIN, ...fit }),
        this.chart.addSeries(LWC().LineSeries, { color: '#888', lineStyle: 2, ...THIN, ...fit }),
        this.chart.addSeries(LWC().LineSeries, { color: '#888', lineStyle: 2, ...THIN, ...fit }),
      ];
    }
    if (id === 'exp_move') {
      const line = (color, lineStyle, title) => this.chart.addSeries(LWC().LineSeries, {
        color, lineStyle, title, crosshairMarkerVisible: false, ...THIN, ...fit,
      });
      return [
        line('#f59e0b', 2, '+1σ'), line('#f59e0b', 2, '-1σ'),
        line('#f87171', 2, '+2σ'), line('#f87171', 2, '-2σ'),
      ];
    }
    if (id === 'bb') {
      // Same look as Tracker2's analyzer charts: grey basis, dashed blue bands.
      return [
        this.chart.addSeries(LWC().LineSeries, { color: '#9ca3af', ...THIN, ...fit }),
        this.chart.addSeries(LWC().LineSeries, { color: '#60a5fa', lineStyle: 2, ...THIN, ...fit }),
        this.chart.addSeries(LWC().LineSeries, { color: '#60a5fa', lineStyle: 2, ...THIN, ...fit }),
      ];
    }
    if (id === 'kc') {
      // Purple, so it reads apart from Bollinger's grey and blue when both are on.
      return [
        this.chart.addSeries(LWC().LineSeries, { color: '#ab47bc', ...THIN, ...fit }),
        this.chart.addSeries(LWC().LineSeries, { color: '#ce93d8', lineStyle: 2, ...THIN, ...fit }),
        this.chart.addSeries(LWC().LineSeries, { color: '#ce93d8', lineStyle: 2, ...THIN, ...fit }),
      ];
    }
    if (id === 'ema' || id === 'sma') {
      return [this.chart.addSeries(LWC().LineSeries, { color: lineColor(this.state, id, period), ...THIN, ...fit })];
    }
    if (id === 'volume_profile') {
      // Histogram via primitive + POC/VA price lines. Both are attached
      // directly to the candle series and are cleaned up in _disableInstance
      // via the holder's _primitive/_priceLines references.
      return [];
    }
    return [];
  }

  _seedIndicator(id, holder, init) {
    if (id === 'macd') {
      holder.series[0].setData(init.initialPoints.filter(p => p.macd !== null)
        .map(p => ({ time: p.time, value: p.macd })));
      holder.series[1].setData(init.initialPoints.filter(p => p.signal !== null)
        .map(p => ({ time: p.time, value: p.signal })));
      holder.series[2].setData(init.initialPoints.filter(p => p.histogram !== null)
        .map(p => ({ time: p.time, value: p.histogram,
                     color: p.histogram >= 0 ? '#22c39a' : '#f2545b' })));
      return;
    }
    if (id === 'vwap') {
      holder.series[0].setData(init.initialPoints
        .filter(p => p.vwap !== null).map(p => ({ time: p.time, value: p.vwap })));
      holder.series[1].setData(init.initialPoints
        .filter(p => p.upper1 != null).map(p => ({ time: p.time, value: p.upper1 })));
      holder.series[2].setData(init.initialPoints
        .filter(p => p.lower1 != null).map(p => ({ time: p.time, value: p.lower1 })));
      return;
    }
    if (id === 'exp_move') {
      // Bars outside a day with levels stay whitespace so each day's lines stand alone.
      // gapAfter: the segment from a day's last point to the next day is drawn transparent.
      EXP_KEYS.forEach((k, i) => holder.series[i].setData(init.initialPoints
        .map(p => (p[k] == null ? { time: p.time }
          : p.gapAfter ? { time: p.time, value: p[k], color: 'transparent' } : { time: p.time, value: p[k] }))));
      holder._lastDay = lastLevelDay(init.initialPoints);
      return;
    }
    if (id === 'bb' || id === 'kc') {
      ['middle', 'upper', 'lower'].forEach((k, i) => holder.series[i].setData(init.initialPoints
        .filter(p => p[k] !== null).map(p => ({ time: p.time, value: p[k] }))));
      return;
    }
    if (id === 'prev_day') {
      this._applyPrevDay(holder, init);
      return;
    }
    if (id === 'orb') {
      if (!holder._primitive) {
        holder._primitive = new ORBPrimitive(lineColor(this.state, 'orb', holder.period));
        this.candleSeries.attachPrimitive(holder._primitive);
      }
      this._applyORB(holder, init);
      return;
    }
    if (id === 'volume_profile') {
      // First-time seed: attach the primitive lazily so its first paint sees
      // the snapshot. Lines are (re)created from the snapshot.
      if (!holder._primitive) {
        holder._primitive = new VolumeProfilePrimitive(holder.instance.bucketSize);
        this.candleSeries.attachPrimitive(holder._primitive);
      }
      this._applyVolumeProfile(holder, init);
      return;
    }
    if (id === 'session_vp') {
      if (!holder._primitive) {
        holder._primitive = new SessionProfilesPrimitive(lineColor(this.state, 'session_vp', holder.period));
        this.candleSeries.attachPrimitive(holder._primitive);
      }
      holder._primitive.setSnapshot(init);
      return;
    }
    if (init.initialPoints) {
      const pts = init.initialPoints.filter(p => p.value !== null && p.value !== undefined);
      holder.series[0].setData(id === 'rsi' ? pts.map(rsiPoint) : pts);
    }
  }

  _applyVolumeProfile(holder, snapshot) {
    holder._primitive.setSnapshot(snapshot);
    // Recreate price lines from scratch each time — simpler than diffing and
    // the snapshot only changes on candle close (cheap).
    if (holder._priceLines) {
      for (const pl of holder._priceLines) this.candleSeries.removePriceLine(pl);
    }
    holder._priceLines = [];
    if (snapshot && snapshot.poc !== null) {
      holder._priceLines.push(this.candleSeries.createPriceLine({
        price: snapshot.poc, color: '#fbc02d', lineWidth: 1, lineStyle: 0,
        axisLabelVisible: false, title: 'POC',
      }));
    }
    if (snapshot && snapshot.valueAreaHigh !== null) {
      holder._priceLines.push(this.candleSeries.createPriceLine({
        price: snapshot.valueAreaHigh, color: '#60a5fa', lineWidth: 1, lineStyle: 2,
        axisLabelVisible: false, title: 'VAH',
      }));
    }
    if (snapshot && snapshot.valueAreaLow !== null) {
      holder._priceLines.push(this.candleSeries.createPriceLine({
        price: snapshot.valueAreaLow, color: '#60a5fa', lineWidth: 1, lineStyle: 2,
        axisLabelVisible: false, title: 'VAL',
      }));
    }
    // createPriceLine triggers a chart repaint, which queries the primitive's
    // paneViews()/renderer() and picks up the new snapshot. No explicit
    // repaint nudge needed.
  }

  _applyPrevDay(holder, snapshot) {
    if (holder._priceLines) {
      for (const pl of holder._priceLines) this.candleSeries.removePriceLine(pl);
    }
    holder._priceLines = [];
    if (!snapshot) return;
    const lines = [
      { key: 'high',  label: 'PDH', color: '#f2545b', style: 2 },  // red dashed
      { key: 'low',   label: 'PDL', color: '#22c39a', style: 2 },  // green dashed
      { key: 'close', label: 'PDC', color: '#fbc02d', style: 0 },  // yellow solid
      { key: 'open',  label: 'PDO', color: '#9ca3af', style: 1 },  // gray dotted
    ];
    for (const ln of lines) {
      const price = snapshot[ln.key];
      if (price === undefined || price === null) continue;
      holder._priceLines.push(this.candleSeries.createPriceLine({
        price, color: ln.color, lineWidth: 1, lineStyle: ln.style,
        axisLabelVisible: false, title: ln.label,
      }));
    }
  }

  // ORB: the box/lines/arrows on every day, all drawn by the primitive (no price-axis labels).
  _applyORB(holder, { days }) {
    holder._primitive.setDays(days);
  }

  _applyIndicatorUpdate(id, holder, out) {
    if (id === 'macd') {
      if (out.point.macd !== null) holder.series[0].update({ time: out.point.time, value: out.point.macd });
      if (out.point.signal !== null) holder.series[1].update({ time: out.point.time, value: out.point.signal });
      if (out.point.histogram !== null) holder.series[2].update({
        time: out.point.time, value: out.point.histogram,
        color: out.point.histogram >= 0 ? '#22c39a' : '#f2545b',
      });
      return;
    }
    if (id === 'vwap') {
      if (out.point.vwap !== null) {
        holder.series[0].update({ time: out.point.time, value: out.point.vwap });
        holder.series[1].update({ time: out.point.time, value: out.point.upper1 });
        holder.series[2].update({ time: out.point.time, value: out.point.lower1 });
      }
      return;
    }
    if (id === 'exp_move') {
      const p = out.point;
      // A new day's first bar: redraw so the previous day's last segment is hidden.
      if (p.up1 != null && holder._lastDay && barDate(p.time) !== holder._lastDay) {
        this._seedIndicator('exp_move', holder, holder.instance.init(this._lastCandles));
        return;
      }
      if (p.up1 != null) holder._lastDay = barDate(p.time);
      EXP_KEYS.forEach((k, i) => holder.series[i].update(p[k] != null ? { time: p.time, value: p[k] } : { time: p.time }));
      return;
    }
    if (id === 'bb' || id === 'kc') {
      if (out.point.middle !== null) {
        ['middle', 'upper', 'lower'].forEach((k, i) =>
          holder.series[i].update({ time: out.point.time, value: out.point[k] }));
      }
      return;
    }
    if (id === 'volume_profile') {
      // VolumeProfile.update returns the full snapshot directly (not wrapped
      // in {point:...} like the other indicators).
      this._applyVolumeProfile(holder, out);
      return;
    }
    if (id === 'prev_day') {
      this._applyPrevDay(holder, out);
      return;
    }
    if (id === 'session_vp') {
      holder._primitive.setSnapshot(out);
      return;
    }
    if (id === 'orb') {
      this._applyORB(holder, out);
      return;
    }
    if (out.point && out.point.value !== null) {
      holder.series[0].update(id === 'rsi' ? rsiPoint(out.point) : out.point);
    }
  }

  _rebuildIndicators(candles) {
    for (const [key, holder] of this.indicators) {
      // VolumeProfile bucket size depends on the symbol's price range, so
      // recompute it on every rebuild (symbol/TF change) and recreate the
      // instance + primitive if it shifted.
      // A new symbol or timeframe can also change the session rule and the proxy future.
      let vpNew = false;
      if (SOURCED.has(holder.id)) {
        const inst = this._newSourced(holder.id, holder.period);
        if (inst.builtFor !== holder.instance.builtFor) {
          holder.instance = inst;
          vpNew = true;
          if (holder._primitive) {
            this.candleSeries.detachPrimitive(holder._primitive);
            holder._primitive = null;  // _seedIndicator will recreate it
          }
        }
      }
      // A new symbol may have another volatility index (or none): forget the old closes.
      const expStale = holder.id === 'exp_move' && holder.loadedFor !== this.state.symbol;
      if (expStale) holder.instance.setSources([]);
      const init = holder.instance.init(candles);
      this._seedIndicator(holder.id, holder, init);
      if (expStale) this._loadExpectedMove(holder);
      if (vpNew && holder.id === 'volume_profile') this._loadVolumeProxy(holder);
      if (vpNew && holder.id === 'session_vp') this._loadSessionSources(holder);
    }
  }

  _setSymbol(symbol) {
    if (!this.setSymbol(symbol)) return;
    if (this.state.linked) this.onSymbolChange?.(this);
    this.onStateChange?.();
  }

  // Price lines the host draws on the candles (Tracker2's analyzer strikes): [{price, color, label,
  // dashed}]. They stay through reloads and symbol changes until replaced.
  setPriceLines(lines) {
    this._extLines = Array.isArray(lines) ? lines : [];
    this._drawExtLines();
  }

  _extLinesOn() { return this.state.priceLines !== false; }

  _drawExtLines() {
    for (const pl of this._extPriceLines) this.candleSeries.removePriceLine(pl);
    this._extPriceLines = [];
    if (this._destroyed || !this._extLinesOn()) return;
    for (const l of this._extLines) {
      this._extPriceLines.push(this.candleSeries.createPriceLine({
        price: l.price, color: l.color, title: l.label, lineWidth: 1,
        lineStyle: l.dashed ? 2 : 0, axisLabelVisible: true,
      }));
    }
  }

  // Load the bars again (the host's futures contract changed).
  reload() { this._load(); }

  // The contract or symbol the feed is charting, once resolved.
  get feedSymbol() { return this._feed?.feedSymbol ?? null; }

  // Show `symbol` without saving (the caller saves). False when there was nothing to change.
  setSymbol(symbol) {
    if (!symbol || symbol === this.state.symbol) return false;
    this.state.symbol = symbol;
    this.root.querySelector('.pane-symbol').value = symbol;
    this.drawingLayer.setTarget(this.state.symbol, this.state.timeframe);
    this._load();
    return true;
  }

  // Candles only is the default; a saved `fitCandles: false` lets overlays stretch the scale.
  _fitCandles() { return this.state.fitCandles !== false; }

  _toggleFit() {
    if (this._fitCandles()) this.state.fitCandles = false;
    else delete this.state.fitCandles;
    this._renderFit();
    this.onStateChange?.();
  }

  _renderFit() {
    const on = this._fitCandles();
    this.fitBtn.classList.toggle('on', on);
    this.fitBtn.setAttribute('aria-pressed', String(on));
    this.fitBtn.title = on
      ? 'Scale fits the candles only; indicator lines outside it are off-screen. Click to fit indicators too.'
      : 'Scale fits candles and indicator lines. Click to fit the candles only.';
    // Back to auto scale so the new fit takes effect (also undoes a manual axis drag).
    this.chart.priceScale?.('right')?.applyOptions({ autoScale: true });
  }

  _toggleLink() {
    this.state.linked = !this.state.linked;
    this._renderLink();
    this.onLinkToggle?.(this);
    this.onStateChange?.();
  }

  _renderLink() {
    const on = !!this.state.linked;
    this.linkBtn.classList.toggle('on', on);
    this.linkBtn.setAttribute('aria-pressed', String(on));
    this.linkBtn.title = on
      ? 'Linked: a new symbol here changes every linked chart. Click to unlink.'
      : 'Link this chart\'s symbol with the other linked charts';
  }

  _setTimeframe(tf) {
    if (tf === this.state.timeframe) return;
    this.state.timeframe = tf;
    this.drawingLayer.setTarget(this.state.symbol, this.state.timeframe);
    this._load();
    this.onStateChange?.();
  }

  // Another window changed this pane (grid <-> pop-out). Apply it without saving it again.
  applyState(next) {
    if (!!next.linked !== !!this.state.linked) {
      this.state.linked = !!next.linked;
      this._renderLink();
    }
    if ((next.fitCandles !== false) !== this._fitCandles()) {
      if (next.fitCandles === false) this.state.fitCandles = false;
      else delete this.state.fitCandles;
      this._renderFit();
    }
    const symbolOrTf = next.symbol !== this.state.symbol || next.timeframe !== this.state.timeframe;
    const indicatorsChanged = JSON.stringify(next.indicators || {}) !== JSON.stringify(this.state.indicators || {});
    if (indicatorsChanged) {
      for (const h of [...this.indicators.values()]) this._disableInstance(h.id, h.period);
      this.state.indicators = JSON.parse(JSON.stringify(next.indicators || { active: {} }));
    }
    if (symbolOrTf) {
      this.state.symbol = next.symbol;
      this.state.timeframe = next.timeframe;
      this.root.querySelector('.pane-symbol').value = next.symbol;
      this.root.querySelector('.pane-tf').value = next.timeframe;
      this.drawingLayer.setTarget(this.state.symbol, this.state.timeframe);
      this._load();
    } else if (indicatorsChanged) {
      this._restoreIndicators();
    }
    if (!this.drawerEl.classList.contains('hidden')) this._renderIndicatorsPanel();
  }

  destroy() {
    this._destroyed = true;
    for (const h of this.indicators.values()) h.unsubVol?.();
    this.legend?.detach();
    document.removeEventListener('pointerdown', this._onOutsidePress, true);
    this._feed?.stop();
    this.drawingLayer?.destroy();
    this.chart.remove();
  }
}
