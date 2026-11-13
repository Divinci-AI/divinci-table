// T9: a choice the table is waiting on (a target to name, a surveil or scry to sort) is impossible to miss: a banner pinned to the top of the
// hand and me pages, on a phone too, a chime and a buzz when one appears, and a link to the hand page where it is answered. It reads the seat's
// own state with its own key (only that seat sees its choices); nothing else.
(function () {
  "use strict";
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = document.createElement("style");
  css.textContent = `.ca-bar{position:fixed;left:0;right:0;top:calc(var(--tb-h,0px));z-index:10001;display:none;padding:10px 14px;background:#7a1010;color:#fff;
    font:600 16px system-ui,-apple-system,sans-serif;border-bottom:3px solid #ff3b30;box-shadow:0 4px 16px rgba(0,0,0,.5)}
    .ca-bar a{color:#ffe08a;margin-left:8px} @media (max-width:700px){.ca-bar{font-size:17px;padding:12px}}
    .ca-bar button{margin-left:8px;font:700 15px system-ui,sans-serif;padding:7px 12px;border-radius:9px;border:1px solid #ffe08a;background:#ffe08a;color:#3a0808;cursor:pointer}
    .ca-space{height:0} .ca-flash{animation:ca-flash 1.1s ease-out 2} @keyframes ca-flash{0%{box-shadow:0 0 0 7px #ffe08a}100%{box-shadow:0 0 0 0 #ffe08a00}}`;
  document.head.appendChild(css);
  let bar = null, space = null, lastSig = "", audio = null, where = [];
  // 2026-11-13 retro (B2): the bar said "Waiting on you: Q Continuum: ..." and offered "show it" only for an attack, a held spell and the mulligan.
  // For a pending CHOICE (and a surveil or a scry) there was no link, and the player sat for eight minutes not knowing where to answer. Every kind
  // of wait the bar can announce now names the panel on the hand page that answers it, most urgent first; "show it" scrolls to the first one that
  // is on the page and flashes it. (A BACK request is answered by the bar's own button, so it names no panel.)
  const PANEL = { attack: ["#blockpanel", "#alert"], window: ["#alert"], deal: ["#dmg", "#alert"], held: ["#heldpanel", "#heldbox"], pay: ["#choicepanel", "#heldpanel", "#heldbox"],
    mulligan: ["#mullpanel"], choice: ["#choicepanel"], surveil: ["#effpanel"], scry: ["#effpanel"] };
  function waits(d, ph, n) {                             // [{kind, text}] in the order the bar says them
    const items = [];
    for (const c of d.pending_choices || []) items.push({ kind: "choice", text: `${c.source}: ${String(c.text || "").replace(" ||then|| ", ". If you do, ")}` });
    if (d.pending_surveil) items.push({ kind: "surveil", text: `surveil ${d.pending_surveil}: choose what goes to the graveyard` });
    if (d.pending_scry) items.push({ kind: "scry", text: `scry ${d.pending_scry}: choose what goes to the bottom` });
    if (d.mulligan && d.mulligan.open && !d.mulligan.kept) items.push({ kind: "mulligan", text: "your opening hand: keep or mulligan" });
    const inc = d.incoming || [];
    if (inc.length) items.unshift({ kind: "attack", text: `you are attacked by ${inc.map(i => i.creature).join(", ")}: declare your blocks (one answer for all), or take it` });
    const you = (d.combat || {}).you || {};
    if (!inc.length && you.window) items.unshift({ kind: "window", text: "blocks are declared: act before damage if you want to, or pass" });
    else if (!inc.length && you.deal) items.unshift({ kind: "deal", text: "blocks are in: deal combat damage when you are ready" });
    const back = ph && ph.back_request && ph.back_request.by !== n ? ph.back_request : null;
    if (back) items.push({ kind: "back", text: `${back.by} asks to go BACK to ${back.to}` });
    const held = ph && ph.held && (ph.held.waiting || []).includes(n) && !ph.held.paying ? ph.held : null;      // a spell held for my answer (a counterspell window)
    if (held) items.unshift({ kind: "held", text: `${held.by} casts ${held.spell}: respond, or pass` });
    if (ph && ph.held && ph.held.paying && ph.held.by === n) items.unshift({ kind: "pay", text: `${ph.held.spell} is countered unless you pay: ${Math.max(0, Math.ceil(ph.held.seconds_left || 0))}s` });
    return items;
  }
  const panelsFor = items => items.flatMap(i => PANEL[i.kind] || []);
  function showIt() {                                    // scroll to the panel that holds the first thing being waited on, and flash it
    const filled = el => el && (el.id === "mullpanel" || el.id === "choicepanel" || el.id === "effpanel" || el.id === "alert" || el.id === "heldbox" ? el.firstElementChild : el);
    let el = null; for (const sel of where) { el = filled(document.querySelector(sel)); if (el) break; }
    try { window.dispatchEvent(new CustomEvent("ca-show", { detail: { where } })); } catch {}     // the hand page closes its cast panel: nothing stays on top
    if (!el) return scrollTo(0, 0);
    scrollTo(0, Math.max(0, scrollY + el.getBoundingClientRect().top - bar.getBoundingClientRect().bottom - 6));
    el.classList.remove("ca-flash"); void el.offsetWidth; el.classList.add("ca-flash"); setTimeout(() => el.classList.remove("ca-flash"), 2400);
  }
  window.ChoiceAlert = { waits, panelsFor, PANEL, show: showIt };
  const seat = () => (window.TableSeat && window.TableSeat.name && window.TableSeat.name()) || "";
  const key = () => (window.TableSeat && window.TableSeat.body && window.TableSeat.body().key) || "";
  document.addEventListener("pointerdown", () => { try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); } catch {} }, { once: true });
  function chime() {
    try {
      if (window.TableSfx && TableSfx.fx) TableSfx.fx("attention");
      if (audio) { for (const [f, t0] of [[880, 0], [1320, 0.18]]) { const o = audio.createOscillator(), g = audio.createGain(); o.frequency.value = f;
        g.gain.setValueAtTime(0.2, audio.currentTime + t0); g.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + t0 + 0.3);
        o.connect(g); g.connect(audio.destination); o.start(audio.currentTime + t0); o.stop(audio.currentTime + t0 + 0.32); } }
      if (navigator.vibrate) navigator.vibrate([150, 80, 150]);
    } catch {}
  }
  async function poll() {
    const n = seat();
    if (window.TableSeat && window.TableSeat.dead && window.TableSeat.dead()) { if (bar) { bar.style.display = "none"; space.style.height = "0"; } return; }   // (B4) a key from an earlier table: stop asking with it
    if (n) {
      try {
        const r = await fetch("/api/brain/state?seat=" + encodeURIComponent(n), { headers: { "X-Seat-Key": key() }, cache: "no-store" });
        if (r.ok) {
          const d = await r.json();
          let ph = null;
          try { ph = await (await fetch("/api/phase", { cache: "no-store" })).json(); } catch {}
          const list = waits(d, ph, n), items = list.map(i => i.text), back = list.some(i => i.kind === "back");
          where = panelsFor(list);
          const sig = JSON.stringify(items);
          if (!bar) { bar = document.createElement("div"); bar.className = "ca-bar"; bar.setAttribute("role", "alert"); document.body.appendChild(bar);
            space = document.createElement("div"); space.className = "ca-space"; document.body.prepend(space);
            bar.addEventListener("click", async ev => {
              if (ev.target.closest("[data-ca-top]")) { ev.preventDefault(); showIt(); }
              if (ev.target.closest("[data-ca-back]")) { try { await fetch("/api/phase/back/confirm", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": key() }, body: JSON.stringify({ by: seat() }) }); } catch {} lastSig = ""; } }); }
          if (items.length) {
            bar.innerHTML = `⚠ Waiting on you: ${items.map(esc).join(" · ")}` + (back ? `<button data-ca-back>↶ Confirm BACK</button>` : "") +
              (location.pathname === "/hand" ? (where.length ? `<a href="#" data-ca-top>show it ▴</a>` : "") : (items.length > (back ? 1 : 0) ? `<a href="/hand">answer it on your Hand page ▸</a>` : ""));
            bar.style.display = "block"; space.style.height = bar.offsetHeight + "px"; document.documentElement.style.setProperty("--ca-h", bar.offsetHeight + "px");
            if (sig !== lastSig) chime();
          } else { bar.style.display = "none"; space.style.height = "0"; document.documentElement.style.setProperty("--ca-h", "0px"); }
          lastSig = sig;
        }
      } catch {}
    }
    setTimeout(poll, 2500);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", poll); else poll();
})();
