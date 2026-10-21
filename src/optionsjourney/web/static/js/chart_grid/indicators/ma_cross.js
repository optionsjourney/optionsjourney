// Moving-average cross arrows: for every pair of active EMA/SMA lines, a marker where the
// faster line crosses the slower one. Faster = shorter period; on a tie the EMA (it reacts
// sooner). Up arrow below the bar when fast crosses above slow, down arrow above it when it
// crosses below; arrows only, no label. A bar where the two are exactly equal carries the
// earlier side, so a touch that turns back is not a cross. `level` is the outer of the two
// lines at the cross (lowest for an up arrow, highest for a down one): the arrow is drawn
// beyond it as well as beyond the candle, so it doesn't sit on the lines.

export const UP_COLOR = '#22c39a';
export const DOWN_COLOR = '#f2545b';

const faster = (a, b) => (a.period !== b.period ? a.period < b.period : a.id === 'ema');

// lines: [{id: 'ema'|'sma', period, points: [{time, value}]}]. Returns markers sorted by time.
export function findCrosses(lines) {
  const markers = [];
  for (let i = 0; i < lines.length; i++) {
    for (let j = i + 1; j < lines.length; j++) {
      const [fast, slow] = faster(lines[i], lines[j]) ? [lines[i], lines[j]] : [lines[j], lines[i]];
      const slowAt = new Map(slow.points.filter(p => p.value != null).map(p => [p.time, p.value]));
      let side = 0;
      for (const p of fast.points) {
        if (p.value == null || !slowAt.has(p.time)) continue;
        const s = slowAt.get(p.time);
        const d = p.value - s;
        const now = d > 0 ? 1 : d < 0 ? -1 : side;
        if (side !== 0 && now !== side) {
          const up = now > 0;
          markers.push({
            time: p.time,
            position: up ? 'belowBar' : 'aboveBar',
            shape: up ? 'arrowUp' : 'arrowDown',
            color: up ? UP_COLOR : DOWN_COLOR,
            level: up ? Math.min(p.value, s) : Math.max(p.value, s),
          });
        }
        side = now;
      }
    }
  }
  return markers.sort((a, b) => a.time - b.time);
}

// The registry entry's instance: the arrows are drawn by the pane from the MA lines it
// already holds, so this computes nothing itself.
export class MACross {
  init() { return {}; }
  update() { return {}; }
}
