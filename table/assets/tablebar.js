// A small bar at the bottom of every player page: jump between the views (My page, Stage, Board, Log), with "My page" red
// and pulsing when it is your turn to pass priority and amber on your own turn, plus a 🔊 Voice toggle that reads out what
// the AI seats announce (including a seat's end-of-turn summary) in this browser's own voices. The cloud rooms have no
// laptop `say` behind them, so without this nothing is spoken. Needs seat.js; takes "who must act" from urgent.js.
(function () {
  "use strict";
  const KEY = "divinci-tablebar";
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
  const save = v => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch {} };
  const prefs = Object.assign({ voice: true, alerts: false }, load());
  const VIEWS = [["/me", "📱 Me"], ["/stage", "🎭 Stage"], ["/board", "🃏 Board"], ["/log", "📜 Log"]];
  const here = location.pathname.replace(/\/+$/, "") || "/";
  const synth = window.speechSynthesis || null;
  const css = document.createElement("style");
  css.textContent = `
  .tb{position:fixed;left:50%;bottom:10px;transform:translateX(-50%);z-index:9996;display:flex;gap:6px;align-items:center;
    padding:6px;border-radius:999px;background:rgba(20,22,27,.92);border:1px solid #3a3f4a;box-shadow:0 4px 14px rgba(0,0,0,.5);
    font:700 14px system-ui,-apple-system,sans-serif;max-width:calc(100vw - 12px)}
  .tb a,.tb button{display:inline-block;padding:8px 12px;border-radius:999px;border:1px solid #3a3f4a;background:#23262e;color:#fff;
    text-decoration:none;font:inherit;cursor:pointer;white-space:nowrap}
  .tb a.tb-here{background:#1f4d33;border-color:#2e8b57;pointer-events:none}
  .tb a.tb-need{background:#5a1210;border-color:#ff3b30;animation:tb-pulse 1s ease-in-out infinite}
  .tb a.tb-turn{background:#4a3a0e;border-color:#f2b705}
  @keyframes tb-pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,59,48,.0)}50%{box-shadow:0 0 14px 4px rgba(255,59,48,.9)}}
  @media (prefers-reduced-motion:reduce){.tb a.tb-need{animation:none;box-shadow:0 0 12px 3px rgba(255,59,48,.9)}}
  @media (max-width:420px){.tb a,.tb button{padding:8px 9px;font-size:13px}}`;
  document.head.appendChild(css);
  const bar = document.createElement("div");
  bar.className = "tb";
  bar.setAttribute("role", "navigation");
  bar.setAttribute("aria-label", "table views");
  bar.innerHTML = VIEWS.map(([h, t]) => `<a href="${h}" data-h="${h}"${h === here ? ' class="tb-here" aria-current="page"' : ""}>${t}</a>`).join("") +
    '<button type="button" class="tb-v" aria-label="table voice on or off"></button>' +
    '<button type="button" class="tb-n" aria-label="notifications on or off"></button>';
  const me = bar.querySelector('[data-h="/me"]'), vb = bar.querySelector(".tb-v"), nb = bar.querySelector(".tb-n");
  const baseTitle = document.title;
  let prev = { mine: false, turn: false }, flip = false;
  const mount = () => { document.body.appendChild(bar); refresh(); };

  // ── "My page" shows when to act ──
  function refresh() {
    const s = window.__urgent ? window.__urgent.state() : {};
    me.classList.toggle("tb-need", !!s.mine && here !== "/me");
    me.classList.toggle("tb-turn", !!s.turn && !s.mine && here !== "/me");
    me.textContent = s.mine ? "✋ Pass — open Me" : s.turn ? "▶ Your turn — open Me" : "📱 Me";
    vb.textContent = !synth ? "🔇" : prefs.voice ? "🔊" : "🔈";
    const canNotify = "Notification" in window;
    nb.textContent = !canNotify ? "🚫" : prefs.alerts && Notification.permission === "granted" ? "🔔" : "🔕";
    nb.title = !canNotify ? "this browser can't show notifications (on an iPhone, add the page to the Home Screen first)"
      : Notification.permission === "denied" ? "notifications are blocked in this browser's settings for this site"
      : prefs.alerts ? "alerts on: a notification when it's your turn to pass or your turn (tap to turn off)" : "tap to get a notification when you need to act";
    flip = !flip;                                                    // the tab title flashes, on every device and browser
    document.title = s.mine ? (flip ? "✋ PASS NOW · " : "") + baseTitle : s.turn ? "▶ YOUR TURN · " + baseTitle : baseTitle;
    if (s.mine && !prev.mine) alertMe("✋ Your turn to pass priority", "The table is waiting on you.");
    if (s.turn && !prev.turn) alertMe("▶ It's your turn", "Open your page to play it.");
    prev = { mine: !!s.mine, turn: !!s.turn };
    vb.title = !synth ? "this browser can't speak" : prefs.voice ? "table voice on (tap to mute)" : "table voice off (tap to turn on)";
  }
  if (document.body) mount(); else addEventListener("DOMContentLoaded", mount);
  setInterval(refresh, 400);

  // ── the table's voice: AI seats' announcements, read in this browser ──
  let last = null, ai = [], voices = [], unlocked = false;
  const unlock = () => {
    if (unlocked || !synth) return;
    unlocked = true;
    try { synth.speak(new SpeechSynthesisUtterance("")); } catch {}                // iOS only speaks after one tap
  };
  ["pointerdown", "keydown", "touchstart"].forEach(e => addEventListener(e, unlock, { passive: true }));
  // A notification while this page is open in the background (any desktop browser; Android Chrome needs a service worker, and an
  // iPhone only for a page added to the Home Screen). Reaching a CLOSED browser needs a push service: not built yet.
  function alertMe(title, body) {
    if (!prefs.alerts || !document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
    try { const n = new Notification(title, { body, tag: "divinci-table" }); n.onclick = () => { window.focus(); n.close(); }; } catch {}
  }
  nb.onclick = async () => {
    if (!("Notification" in window)) return;
    if (prefs.alerts) { prefs.alerts = false; save(prefs); refresh(); return; }
    let perm = Notification.permission;
    if (perm === "default") { try { perm = await Notification.requestPermission(); } catch {} }
    prefs.alerts = perm === "granted"; save(prefs); refresh();
    if (prefs.alerts) try { new Notification("Alerts are on", { body: "You'll hear from the table when you need to act.", tag: "divinci-table" }); } catch {}
  };
  vb.onclick = () => { prefs.voice = !prefs.voice; save(prefs); unlock(); if (!prefs.voice && synth) synth.cancel(); refresh(); };
  const hash = s => [...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7);
  function voiceFor(name) {
    if (!voices.length && synth) voices = synth.getVoices().filter(v => /^en/i.test(v.lang));
    return voices.length ? voices[hash(name) % voices.length] : null;       // each seat keeps its own voice
  }
  function say(name, text) {
    if (!synth || !prefs.voice || !text) return;
    if (synth.pending && synth.speaking) synth.cancel();                    // a backlog is worse than skipping to the latest
    const u = new SpeechSynthesisUtterance(text.replace(/\s+/g, " ").slice(0, 300));
    const v = voiceFor(name); if (v) u.voice = v;
    u.rate = 1.04; u.lang = (v && v.lang) || "en-US";
    try { synth.speak(u); } catch {}
  }
  async function loop() {
    for (;;) {
      try {
        if (!synth) return;
        if (!ai.length) { const p = await (await fetch("/api/phase", { cache: "no-store" })).json(); ai = p.ai || []; }
        const q = last === null ? 0 : last, t0 = Date.now();
        const r = await (await fetch(`/api/events?since=${q}&wait=20`, { cache: "no-store" })).json();
        if (Date.now() - t0 < 800 && !(r.events || []).length) await new Promise(r_ => setTimeout(r_, 1200));   // a server that ignores ?wait: poll, don't spin
        if (r.restarted) { last = null; continue; }
        if (last !== null) for (const e of r.events || []) if (e.type === "say" && ai.includes(e.speaker)) say(e.speaker, e.text);
        last = r.last ?? last ?? 0;
      } catch { await new Promise(r => setTimeout(r, 2000)); }
    }
  }
  if (synth) { try { synth.onvoiceschanged = () => { voices = []; }; } catch {} loop(); }
  window.__tablebar = { prefs, say, alertMe };                                         // for the browser test
})();
