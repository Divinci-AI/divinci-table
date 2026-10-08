// Tabs along the top of every player page: jump between the views (Me, Stage, Round table, 2D Cards, Log), with "Me" red
// and pulsing when it is your turn to pass priority and amber on your own turn, plus a 🔊 Voice toggle that reads out what
// the AI seats announce (including a seat's end-of-turn summary) in this browser's own voices. The cloud rooms have no
// laptop `say` behind them, so without this nothing is spoken. Needs seat.js; takes "who must act" from urgent.js.
(function () {
  "use strict";
  const KEY = "divinci-tablebar";
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
  const save = v => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch {} };
  const prefs = Object.assign({ voice: true, alerts: false }, load());
  const VIEWS = [["/me", "📱", "Me", "Me"], ["/stage", "🎭", "Stage", "Stage"], ["/xr", "🔮", "Round table", "Round"], ["/board", "🃏", "Cards", "Cards"], ["/log", "📜", "", ""]];
  const here = location.pathname.replace(/\/+$/, "") || "/";
  const synth = window.speechSynthesis || null;
  const css = document.createElement("style");
  css.textContent = `
  .tb{position:fixed;left:0;right:0;top:0;z-index:9996;display:flex;gap:6px;align-items:center;justify-content:center;
    padding:6px 8px;padding-top:calc(6px + env(safe-area-inset-top,0px));background:rgba(14,16,21,.94);border-bottom:1px solid #3a3f4a;
    box-shadow:0 2px 12px rgba(0,0,0,.45);font:700 14px system-ui,-apple-system,sans-serif;overflow-x:auto;white-space:nowrap}
  .tb a,.tb button{display:inline-block;padding:7px 12px;border-radius:10px;border:1px solid #3a3f4a;background:#23262e;color:#fff;
    text-decoration:none;font:inherit;cursor:pointer;white-space:nowrap;flex:0 0 auto}
  .tb a.tb-here{background:#1f4d33;border-color:#2e8b57;pointer-events:none}
  .tb a.tb-need{background:#5a1210;border-color:#ff3b30;animation:tb-pulse 1s ease-in-out infinite}
  .tb a.tb-turn{background:#4a3a0e;border-color:#f2b705}
  .tb .tb-sp{flex:1 1 0}
  @keyframes tb-pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,59,48,.0)}50%{box-shadow:0 0 14px 4px rgba(255,59,48,.9)}}
  @media (prefers-reduced-motion:reduce){.tb a.tb-need{animation:none;box-shadow:0 0 12px 3px rgba(255,59,48,.9)}}
  .tb .s{display:none}
  .tb .tb-who .e{display:inline}
  @media (max-width:520px){.tb{justify-content:flex-start;gap:3px;padding-left:4px;padding-right:4px}.tb a,.tb button{padding:7px 6px;font-size:12.5px}
    .tb .l{display:none}.tb .s{display:inline}}
  @media (max-width:430px){.tb a .e,.tb .tb-who .e{display:none}.tb a[data-h="/log"]{display:none}}`;
  document.head.appendChild(css);
  const bar = document.createElement("div");
  bar.className = "tb";
  bar.setAttribute("role", "navigation");
  bar.setAttribute("aria-label", "table views");
  bar.innerHTML = VIEWS.map(([h, e, l, sh]) => `<a href="${h}" data-h="${h}"${h === here ? ' class="tb-here" aria-current="page"' : ""} aria-label="${l || "Log"}"><span class="e">${e}</span>${l ? ` <span class="l">${l}</span><span class="s">${sh}</span>` : ""}</a>`).join("") +
    '<span class="tb-sp"></span>' +
    '<button type="button" class="tb-who"></button>' +
    '<button type="button" class="tb-v" aria-label="table voice on or off"></button>' +
    '<button type="button" class="tb-n" aria-label="notifications on or off"></button>';
  const who = bar.querySelector(".tb-who");
  const me = bar.querySelector('[data-h="/me"]'), vb = bar.querySelector(".tb-v"), nb = bar.querySelector(".tb-n");
  const baseTitle = document.title;
  let prev = { mine: false, turn: false }, flip = false;
  // make room for the tabs: push the page down, and hide seat.js's own "👤 Who are you?" chip (the bar has the same button)
  function makeRoom() {
    const h = bar.offsetHeight || 44;
    document.body.style.paddingTop = h + "px";
    document.documentElement.style.setProperty("--tb-h", h + "px");
    for (const b of document.querySelectorAll("body > button")) {
      const cs = getComputedStyle(b);
      if (cs.position === "fixed" && /👤/.test(b.textContent)) b.style.display = "none";    // the bar carries the seat button now
    }
  }
  const mount = () => { document.body.appendChild(bar); refresh(); makeRoom(); setTimeout(makeRoom, 400); setTimeout(makeRoom, 1500); addEventListener("resize", makeRoom); };

  // ── "My page" shows when to act ──
  function refresh() {
    const s = window.__urgent ? window.__urgent.state() : {};
    me.classList.toggle("tb-need", !!s.mine && here !== "/me");
    me.classList.toggle("tb-turn", !!s.turn && !s.mine && here !== "/me");
    const urgent = s.mine || s.turn;
    me.innerHTML = urgent ? `<span class="e">${s.mine ? "✋" : "▶"}</span> <span class="l">${s.mine ? "Pass priority" : "Your turn"}</span><span class="s">${s.mine ? "Pass" : "Turn"}</span>`
      : '<span class="e">📱</span> <span class="l">Me</span><span class="s">Me</span>';
    const nm = window.TableSeat && window.TableSeat.name && window.TableSeat.name();
    who.innerHTML = nm ? `<span class="e">👤 </span>${nm.replace(/[<>&]/g, "")}` : '<span class="e">👤 </span>Who?';
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
  who.onclick = () => { if (window.TableSeat && window.TableSeat.pick) window.TableSeat.pick(); };
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
