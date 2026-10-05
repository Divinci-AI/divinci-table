// Sound instead of pictures (docs/THEATER-GOAL.md T5): each location's ambience, synthesized in the browser from
// noise and oscillators. No recordings, so nothing to license or download: the table's own code (Apache-2.0).
// It sits quietly under the story and ducks (−12 dB) whenever a voice speaks.
//
//   const amb = createAmbience();  amb.setTheme("cave");  amb.start();  amb.duck(true / false);
export function createAmbience() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return { start() {}, stop() {}, setTheme() {}, duck() {}, theme: null, available: false };
  const ctx = new AC();
  const master = ctx.createGain(); master.gain.value = 0;
  const analyser = ctx.createAnalyser(); analyser.fftSize = 2048;
  master.connect(analyser); analyser.connect(ctx.destination);
  const LEVEL = 0.16, DUCKED = LEVEL * 0.25;            // 0.25 of the level = −12 dB under speech
  let bed = null, timers = [], theme = null, on = false, speaking = 0;

  const noiseBuf = (() => {                             // two seconds of white noise, looped by every noise source
    const b = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate), d = b.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
    return b;
  })();
  const noise = () => { const s = ctx.createBufferSource(); s.buffer = noiseBuf; s.loop = true; s.start(); return s; };
  const filt = (type, f, q = 0.7) => { const n = ctx.createBiquadFilter(); n.type = type; n.frequency.value = f; n.Q.value = q; return n; };
  const gain = v => { const g = ctx.createGain(); g.gain.value = v; return g; };
  const lfo = (target, rate, depth) => { const o = ctx.createOscillator(), g = gain(depth); o.frequency.value = rate; o.connect(g); g.connect(target); o.start(); return o; };
  const every = (min, max, fn) => { const t = { stop: false }; const go = () => { if (t.stop) return; fn(); setTimeout(go, min + Math.random() * (max - min)); }; setTimeout(go, Math.random() * max); timers.push(t); };

  // ── the ingredients ─────────────────────────────────────────────────────────────────────────
  function wind(out, base = 450, amount = 0.5) {
    const n = noise(), f = filt("bandpass", base, 0.6), g = gain(amount);
    lfo(f.frequency, 0.07, base * 0.5); lfo(g.gain, 0.11, amount * 0.45);
    n.connect(f); f.connect(g); g.connect(out);
  }
  function water(out, amount = 0.35) {
    const n = noise(), lp = filt("lowpass", 1100), hp = filt("highpass", 180), g = gain(amount);
    lfo(lp.frequency, 0.3, 300); n.connect(hp); hp.connect(lp); lp.connect(g); g.connect(out);
  }
  function drone(out, f0 = 55, amount = 0.12) {
    for (const f of [f0, f0 * 1.498, f0 * 2.01]) {
      const o = ctx.createOscillator(), g = gain(amount / 3); o.type = "sine"; o.frequency.value = f; lfo(g.gain, 0.05 + Math.random() * 0.05, amount / 8);
      o.connect(g); g.connect(out); o.start();
    }
  }
  function murmur(out, amount = 0.3) {                  // a room full of low talk: band-limited noise, gently restless
    const n = noise(), f = filt("bandpass", 420, 1.2), g = gain(amount);
    lfo(g.gain, 3.1, amount * 0.25); lfo(f.frequency, 0.6, 140);
    n.connect(f); f.connect(g); g.connect(out);
  }
  function ping(out, freq, dur, level, echo = false) {
    const o = ctx.createOscillator(), g = gain(0), t = ctx.currentTime;
    o.frequency.setValueAtTime(freq, t); o.frequency.exponentialRampToValueAtTime(freq * 0.8, t + dur);
    g.gain.setValueAtTime(level, t); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g);
    if (echo) { const d = ctx.createDelay(1), fb = gain(0.45); d.delayTime.value = 0.23; g.connect(d); d.connect(fb); fb.connect(d); fb.connect(out); }
    g.connect(out); o.start(t); o.stop(t + dur + 1.5);
  }
  function crackle(out, level = 0.25) {                 // a burst of high, short noise: fire
    const s = ctx.createBufferSource(), g = gain(level), hp = filt("highpass", 2500), t = ctx.currentTime;
    s.buffer = noiseBuf; g.gain.setValueAtTime(level, t); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.06);
    s.connect(hp); hp.connect(g); g.connect(out); s.start(t, Math.random()); s.stop(t + 0.08);
  }
  function chirp(out) {                                 // a small bird: two quick upward sweeps
    for (const k of [0, 0.12]) {
      const o = ctx.createOscillator(), g = gain(0), t = ctx.currentTime + k, f = 2600 + Math.random() * 1500;
      o.frequency.setValueAtTime(f, t); o.frequency.exponentialRampToValueAtTime(f * 1.4, t + 0.08);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.12, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.1);
      o.connect(g); g.connect(out); o.start(t); o.stop(t + 0.12);
    }
  }

  // ── each place ──────────────────────────────────────────────────────────────────────────────
  const THEMES = {
    tavern: out => { murmur(out, 0.35); every(250, 900, () => crackle(out, 0.12)); every(2500, 7000, () => ping(out, 3200 + Math.random() * 1600, 0.25, 0.05)); },
    road: out => { wind(out, 380, 0.45); every(3000, 9000, () => chirp(out)); },
    forest: out => { wind(out, 600, 0.3); every(1200, 4000, () => chirp(out)); },
    cave: out => { drone(out, 41, 0.18); every(1400, 4200, () => ping(out, 1400 + Math.random() * 1200, 0.18, 0.12, true)); },
    crypt: out => { drone(out, 49, 0.2); wind(out, 250, 0.15); every(4000, 11000, () => ping(out, 900, 0.4, 0.05, true)); },
    keep: out => { drone(out, 65, 0.08); murmur(out, 0.08); every(500, 1600, () => crackle(out, 0.06)); },
    bridge: out => { water(out, 0.4); wind(out, 520, 0.2); every(4000, 10000, () => chirp(out)); },
    ruins: out => { wind(out, 330, 0.5); every(6000, 14000, () => ping(out, 700, 0.6, 0.03, true)); },
  };

  function build() {
    if (bed) { bed.disconnect(); timers.forEach(t => (t.stop = true)); timers = []; }
    bed = gain(1); bed.connect(master);
    (THEMES[theme] || THEMES.tavern)(bed);
  }
  function level() { return on ? (speaking ? DUCKED : LEVEL) : 0; }
  function ramp() { master.gain.cancelScheduledValues(ctx.currentTime); master.gain.setTargetAtTime(level(), ctx.currentTime, 0.12); }
  return {
    available: true, ctx, master, analyser, LEVEL, DUCKED,
    get theme() { return theme; },
    get on() { return on; },
    setTheme(t) { if (t === theme) return; theme = THEMES[t] ? t : "tavern"; if (on) build(); },
    async start() { on = true; await ctx.resume().catch(() => {}); if (!bed) build(); ramp(); },
    stop() { on = false; ramp(); },
    duck(isSpeaking) { speaking = Math.max(0, speaking + (isSpeaking ? 1 : -1)); ramp(); },
    rms() {                                             // the level actually coming out, in dBFS (for the T5 check)
      const d = new Float32Array(analyser.fftSize); analyser.getFloatTimeDomainData(d);
      return 10 * Math.log10(d.reduce((a, x) => a + x * x, 0) / d.length + 1e-12);
    },
  };
}
