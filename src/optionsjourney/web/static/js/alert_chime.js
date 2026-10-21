// The three-note alert chime (was the Analyzer's; now shared by every page via
// trade_alerts_client.js). Browsers only let audio start after a user gesture, so
// unlockAudio() runs on the first pointerdown.
let ctx = null;

export function unlockAudio() {
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === 'suspended') ctx.resume();
  } catch (e) { /* no audio */ }
}

// Whether a chime would be heard now: the page has had its click or key press
export function audioReady() {
  return !!ctx && ctx.state === 'running';
}

export function playChime() {
  try {
    unlockAudio();
    if (!ctx) return;
    const t0 = ctx.currentTime;
    [880, 1174.66, 1567.98].forEach((f, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine'; o.frequency.value = f;
      const s = t0 + i * 0.16;
      g.gain.setValueAtTime(0.0001, s);
      g.gain.exponentialRampToValueAtTime(0.35, s + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, s + 0.4);
      o.connect(g); g.connect(ctx.destination);
      o.start(s); o.stop(s + 0.45);
    });
  } catch (e) { /* audio blocked */ }
}
