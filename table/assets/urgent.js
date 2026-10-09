// "It's your turn to pass priority": a red glow around the screen, a countdown when the table has a pass timer, beeps that
// speed up as time runs out, and a big Pass button that works from any page (the stage page has none of its own).
// Needs assets/seat.js (TableSeat: which player this device is). The table's own rule lives on the server
// (--human-pass-secs, passes.deadline in /api/phase): this file only shows it, so a device that never loads it still gets
// passed for on time.
//
// Care taken: the glow pulses once a second, far under the three-flashes-a-second photosensitivity limit, and holds steady
// under prefers-reduced-motion; the sound has an on/off button that is remembered; beeps need one tap on the page first
// (browsers refuse sound before that), and the bar says so.
(function () {
  "use strict";
  const KEY = "divinci-urgent";
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
  const save = v => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch {} };
  const prefs = Object.assign({ sound: true }, load());
  const URGENT_AT = 10;                                   // seconds left when the glow starts to pulse and beeping begins
  const css = document.createElement("style");
  css.textContent = `
  .u-glow{position:fixed;inset:0;pointer-events:none;z-index:9998;opacity:0;transition:opacity .25s;
    box-shadow:inset 0 0 0 5px rgba(255,59,48,.95),inset 0 0 70px 14px rgba(255,59,48,.55)}
  .u-on .u-glow{opacity:.85}
  .u-urgent .u-glow{opacity:1;animation:u-pulse 1s ease-in-out infinite}
  @keyframes u-pulse{0%,100%{opacity:.4}50%{opacity:1}}
  @media (prefers-reduced-motion:reduce){.u-urgent .u-glow{animation:none;opacity:1}}
  .u-bar{position:fixed;left:50%;top:calc(var(--tb-h,0px) + 8px);transform:translateX(-50%);z-index:9999;display:none;align-items:center;gap:10px;
    padding:8px 12px;border-radius:14px;background:#2a0f0f;color:#fff;border:2px solid #ff3b30;
    font:600 15px system-ui,-apple-system,sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.5);max-width:calc(100vw - 16px)}
  .u-on .u-bar{display:flex}
  .u-bar button{font:700 16px system-ui;padding:9px 16px;border-radius:10px;border:0;background:#ff3b30;color:#fff;cursor:pointer}
  .u-bar button.u-snd{background:#3a1b1b;padding:9px 11px}
  .u-bar .u-msg{font-weight:500;font-size:12.5px;opacity:.9}`;
  document.head.appendChild(css);
  const root = document.createElement("div");
  root.innerHTML = `<div class="u-glow" aria-hidden="true"></div>
    <div class="u-bar" role="alert"><span>✋ <b>Pass priority</b> <span class="u-secs"></span></span>
    <button class="u-pass" type="button">Pass</button><button class="u-snd" type="button" aria-label="beeps on or off"></button>
    <span class="u-msg"></span></div>`;
  const glow = root.firstElementChild, bar = root.lastElementChild;
  const $ = s => bar.querySelector(s);
  const mount = () => { document.body.appendChild(glow); document.body.appendChild(bar); render(); };

  // ── sound ──
  let ctx = null;
  const unlock = () => { try { ctx = ctx || new (window.AudioContext || window.webkitAudioContext)(); if (ctx.state === "suspended") ctx.resume(); } catch {} render(); };
  ["pointerdown", "keydown", "touchstart"].forEach(e => addEventListener(e, unlock, { passive: true }));
  function tone(freq, ms, vol) {
    if (!prefs.sound || !ctx || ctx.state !== "running") return;
    try {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine"; o.frequency.value = freq; g.gain.value = vol;
      o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + ms / 1000);
    } catch {}
  }
  const ding = () => { tone(660, 140, .25); setTimeout(() => tone(880, 180, .25), 170); };
  const beep = () => { tone(1000, 110, .3); try { navigator.vibrate && navigator.vibrate(120); } catch {} };

  // ── what the table says ──
  const st = { turn: false, mine: false, deadline: null, secs: 0, offset: 0, wasMine: false, lastBeep: 0, err: "", errUntil: 0,
    pilot: false, player: null, step: null };
  const me = () => ((window.TableSeat && window.TableSeat.name && window.TableSeat.name()) || "").toLowerCase();
  async function poll() {
    try {
      if (!me()) return;                                   // (also while the tab is in the background: that is when it matters)
      const p = await (await fetch("/api/phase", { cache: "no-store" })).json();
      const ps = p.passes || {};
      st.mine = !!ps.next && ps.next.toLowerCase() === me() && p.player !== null && p.player.toLowerCase() !== me();
      st.turn = !!p.player && p.player.toLowerCase() === me();       // my own turn: the page I act from is /me
      st.pilot = (p.ai || []).some(n => n.toLowerCase() === me());   // a virtual-deck seat: it passes with its own call, NEXT refuses it
      st.player = p.player; st.step = p.step;
      st.deadline = ps.deadline || null;
      st.secs = ps.secs || 0;
      if (ps.now) st.offset = ps.now * 1000 - Date.now();   // the server's clock, not this phone's
    } catch {}
  }
  const remaining = () => st.deadline ? (st.deadline * 1000 - (Date.now() + st.offset)) / 1000 : null;

  function render() {
    const rem = remaining();
    const on = st.mine, urgent = on && rem !== null && rem <= URGENT_AT;
    root.classList.toggle("u-on", on); document.body && document.body.classList.toggle("u-on", on);
    document.body && document.body.classList.toggle("u-urgent", urgent);
    $(".u-secs").textContent = on && rem !== null ? `· ${Math.max(0, Math.ceil(rem))}s` : "";
    $(".u-snd").textContent = prefs.sound ? "🔔" : "🔕";
    const needTap = on && prefs.sound && (!ctx || ctx.state !== "running");
    $(".u-msg").textContent = Date.now() < st.errUntil ? st.err : needTap ? "tap the screen once to turn on beeps" : "";
    return { on, urgent, rem };
  }
  function tick() {
    const { on, urgent, rem } = render();
    if (on && !st.wasMine) { ding(); st.lastBeep = Date.now(); }
    if (urgent) {
      const every = rem <= 3 ? 500 : 1000;
      if (Date.now() - st.lastBeep >= every) { beep(); st.lastBeep = Date.now(); }
    }
    st.wasMine = on;
  }
  $(".u-snd").onclick = () => { prefs.sound = !prefs.sound; save(prefs); unlock(); render(); };
  $(".u-pass").onclick = async () => {
    unlock();
    try {
      const seat = window.TableSeat && window.TableSeat.name && window.TableSeat.name();
      const r = st.pilot   // a pilot's pass is the same call /hand makes, for the step this bar was showing (a late press is refused as stale)
        ? await fetch("/api/brain/pass", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": window.TableSeat.body().key || "" },
            body: JSON.stringify({ seat, player: st.player, step: st.step }) })
        : await fetch("/api/phase/next", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(window.TableSeat ? window.TableSeat.body() : { by: me() }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { st.err = d.error || "couldn't pass"; st.errUntil = Date.now() + 4000; }
      await poll();
    } catch { st.err = "no connection"; st.errUntil = Date.now() + 4000; }
    render();
  };
  if (document.body) mount(); else addEventListener("DOMContentLoaded", mount);   // last: render() needs everything above
  setInterval(poll, 1000);
  setInterval(tick, 200);
  poll();
  window.__urgent = { state: () => ({ ...st, remaining: remaining() }), prefs };    // for the browser test
})();
