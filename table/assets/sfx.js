// 🔊 Table sounds, synthesized in the browser (no audio files): a unique sound for every step of a
// turn, a soft click on every button, a ping when it's your turn to pass, and sounds for life
// changes, attacks, chat, photos and the high roll. 🔊/🔇 toggles them on this device.
//   <script src="/assets/sfx.js" defer></script>
(() => {
  if (window.TableSfx) return;
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch {} return null; };
  let on = store("sfx.on") !== "0", ctx = null;
  const ac = () => { if (!ctx) { try { ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch { return null; } }
                     if (ctx.state === "suspended") ctx.resume(); return ctx; };
  addEventListener("pointerdown", () => ac(), { once: true, capture: true });   // browsers need one tap first

  // ── building blocks ──────────────────────────────────────────────────────────────────────
  function tone(f, t0, dur, { type = "sine", vol = 0.18, glide = 0, attack = 0.008 } = {}) {
    const a = ac(); if (!a || !on) return;
    const o = a.createOscillator(), g = a.createGain(), t = a.currentTime + t0;
    o.type = type; o.frequency.setValueAtTime(f, t);
    if (glide) o.frequency.exponentialRampToValueAtTime(Math.max(30, f * glide), t + dur);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(vol, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(a.destination); o.start(t); o.stop(t + dur + 0.05);
  }
  function noise(t0, dur, { vol = 0.12, freq = 2000, q = 1, type = "bandpass" } = {}) {
    const a = ac(); if (!a || !on) return;
    const n = Math.max(1, Math.floor(a.sampleRate * dur)), buf = a.createBuffer(1, n, a.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
    const s = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain(), t = a.currentTime + t0;
    s.buffer = buf; f.type = type; f.frequency.value = freq; f.Q.value = q; g.gain.value = vol;
    s.connect(f).connect(g).connect(a.destination); s.start(t);
  }
  const chord = (fs, t0, dur, o = {}) => fs.forEach((f, i) => tone(f, t0 + i * (o.strum || 0), dur, o));

  // ── one sound per step ───────────────────────────────────────────────────────────────────
  const STEP = {
    "untap":               () => { [392, 494, 587, 784].forEach((f, i) => tone(f, i * 0.05, 0.25, { type: "triangle", vol: 0.12 })); },
    "upkeep":              () => { tone(880, 0, 0.5, { vol: 0.12 }); tone(1320, 0.12, 0.6, { vol: 0.08 }); },
    "draw":                () => { noise(0, 0.09, { freq: 4500, vol: 0.18, q: 2 }); tone(1200, 0.02, 0.08, { type: "triangle", vol: 0.06, glide: 1.6 }); },
    "main 1":              () => chord([262, 330, 392, 523], 0, 0.8, { type: "triangle", vol: 0.07, strum: 0.035 }),
    "beginning of combat": () => { tone(110, 0, 0.35, { vol: 0.3, glide: 0.5 }); noise(0, 0.12, { freq: 180, vol: 0.2, type: "lowpass" }); },
    "declare attackers":   () => { chord([220, 277, 330], 0, 0.55, { type: "sawtooth", vol: 0.05 }); tone(440, 0.18, 0.4, { type: "sawtooth", vol: 0.05 }); },
    "declare blockers":    () => { [1568, 2093, 2637].forEach((f, i) => tone(f, 0, 0.45 - i * 0.1, { type: "square", vol: 0.03 })); noise(0, 0.06, { freq: 3000, vol: 0.1 }); },
    "combat damage":       () => { tone(80, 0, 0.3, { vol: 0.35, glide: 0.4 }); noise(0, 0.18, { freq: 600, vol: 0.18, type: "lowpass" }); },
    "main 2":              () => chord([294, 370, 440, 587], 0, 0.8, { type: "triangle", vol: 0.07, strum: 0.035 }),
    "end step":            () => { [784, 659, 523, 392].forEach((f, i) => tone(f, i * 0.07, 0.3, { vol: 0.1 })); },
    "cleanup":             () => noise(0, 0.25, { freq: 1200, vol: 0.06, q: 0.5 }),
    "turn over":           () => chord([196, 247, 294, 392], 0, 1.4, { vol: 0.08, strum: 0.06 }),
  };
  const FX = {
    click:   () => { tone(1800, 0, 0.04, { type: "square", vol: 0.04 }); },
    pass:    () => { tone(660, 0, 0.08, { type: "triangle", vol: 0.12 }); tone(990, 0.06, 0.1, { type: "triangle", vol: 0.1 }); },
    yours:   () => { tone(1047, 0, 0.18, { vol: 0.14 }); tone(1397, 0.12, 0.25, { vol: 0.12 }); },
    lifeUp:  () => { tone(523, 0, 0.12, { vol: 0.1, glide: 1.5 }); },
    lifeDown:() => { tone(330, 0, 0.18, { vol: 0.14, glide: 0.6 }); },
    attack:  () => { noise(0, 0.25, { freq: 900, vol: 0.18, q: 3 }); tone(196, 0.05, 0.4, { type: "sawtooth", vol: 0.06, glide: 1.3 }); },
    chat:    () => { tone(1568, 0, 0.09, { vol: 0.06 }); tone(2093, 0.07, 0.12, { vol: 0.05 }); },
    photo:   () => { noise(0, 0.05, { freq: 5000, vol: 0.2 }); noise(0.09, 0.05, { freq: 3500, vol: 0.15 }); },
    dice:    () => { for (let i = 0; i < 7; i++) noise(i * 0.06 + Math.random() * 0.02, 0.04, { freq: 2500 + Math.random() * 1500, vol: 0.14 }); },
    win:     () => chord([523, 659, 784, 1047], 0, 1.2, { type: "triangle", vol: 0.09, strum: 0.08 }),
  };

  // every button press: a soft click (the pass button gets its own)
  addEventListener("click", ev => {
    const b = ev.target.closest("button,[data-act],[data-mode],[data-enter]");
    if (!b || b.closest("[data-sfx-toggle]")) return;
    (/pass|NEXT|START|END TURN/i.test(b.textContent || "") ? FX.pass : FX.click)();
  }, true);

  // follow the table: phases, passes, life, attacks, chat, photos, dice
  const me = () => (window.TableSeat && window.TableSeat.name()) || new URLSearchParams(location.search).get("player") || "";
  let since = null, wasMine = false;
  async function poll() {
    try {
      if (since === null) { since = (await (await fetch("/api/events?since=latest")).json()).last || 0; }
      const d = await (await fetch(`/api/events?since=${since}`)).json();
      if (d.restarted) since = 0;
      for (const e of d.events || []) {
        since = e.id;
        if (e.type === "phase" && STEP[e.step]) STEP[e.step]();
        else if (e.type === "life") (e.delta > 0 ? FX.lifeUp : FX.lifeDown)();
        else if (e.type === "declare") FX.attack();
        else if (e.type === "chat") (e.photo ? FX.photo : FX.chat)();
        else if (e.type === "highroll") (e.winner ? FX.win : FX.dice)();
      }
      const p = await (await fetch("/api/phase")).json();          // a ping when it becomes your turn to pass
      const mine = !!me() && p.passes && p.passes.next === me();
      if (mine && !wasMine) FX.yours();
      wasMine = mine;
    } catch {}
  }
  setInterval(poll, 900); poll();

  // 🔊 / 🔇 toggle, top right under the 👤 seat chip
  const btn = document.createElement("button");
  btn.dataset.sfxToggle = "1";
  btn.style.cssText = "position:fixed;right:12px;top:46px;z-index:55;width:34px;height:34px;border-radius:999px;border:1px solid #3a3f4a;" +
    "background:#1b1e25;color:#eee;font-size:17px;cursor:pointer;opacity:.85";
  const label = () => { btn.textContent = on ? "🔊" : "🔇"; btn.title = on ? "sounds on (tap to mute)" : "sounds off (tap to turn on)"; };
  btn.onclick = () => { on = !on; store("sfx.on", on ? "1" : "0"); label(); if (on) { ac(); STEP["main 1"](); } };
  label();
  const mount = () => document.body.appendChild(btn);
  if (document.body) mount(); else addEventListener("DOMContentLoaded", mount);
  window.TableSfx = { step: s => STEP[s] && STEP[s](), fx: n => FX[n] && FX[n](), on: () => on };
})();
