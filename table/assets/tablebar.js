// Tabs along the top of every player page: jump between the views (Me, Stage, Round table, 2D Cards, Log), with "Me" red
// and pulsing when it is your turn to pass priority and amber on your own turn, plus a 🔊 Voice toggle that reads out what
// the AI seats announce (including a seat's end-of-turn summary) in this browser's own voices. The cloud rooms have no
// laptop `say` behind them, so without this nothing is spoken. Needs seat.js; takes "who must act" from urgent.js.
(function () {
  "use strict";
  const KEY = "divinci-tablebar";
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
  const save = v => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch {} };
  const prefs = Object.assign({ voice: false, alerts: false }, load());      // quiet by default: the 🔈 button turns the table voice on
  let muted = false;                                                       // ?mute=1: silent in this tab, whatever was saved (test games)
  try { muted = new URLSearchParams(location.search).get("mute") === "1"; } catch {}
  const VIEWS = [["/me", "📱", "Me", "Me"], ["/hand", "✋", "Hand", "Hand"], ["/stage", "🎭", "Stage", "Stage"], ["/log", "📜", "", ""]];
  const here = location.pathname.replace(/\/+$/, "") || "/";
  const tabHere = here === "/xr" ? "/stage" : here;                      // the round table is the Stage tab's other flavour
  const synth = window.speechSynthesis || null;
  const css = document.createElement("style");
  css.textContent = `
  .tb{position:fixed;left:0;right:0;top:0;z-index:9996;display:flex;gap:6px;align-items:center;justify-content:center;
    padding:6px 8px;padding-top:calc(6px + env(safe-area-inset-top,0px));background:rgba(14,16,21,.94);border-bottom:1px solid #3a3f4a;
    box-shadow:0 2px 12px rgba(0,0,0,.45);font:700 14px system-ui,-apple-system,sans-serif;overflow-x:auto;white-space:nowrap}
  .tb a,.tb button{display:inline-block;padding:7px 12px;border-radius:10px;border:1px solid #3a3f4a;background:#23262e;color:#fff;
    text-decoration:none;font:inherit;cursor:pointer;white-space:nowrap;flex:0 0 auto}
  .tb a.tb-here{background:#1f4d33;border-color:#2e8b57;pointer-events:none}
  .tb a.tb-need{background:#4a3a0e;border-color:#f2b705}
  body.u-urgent .tb a.tb-need{background:#5a1210;border-color:#ff3b30;animation:tb-pulse 1s ease-in-out infinite}
  .tb a.tb-turn{background:#4a3a0e;border-color:#f2b705}
  .tb .tb-sp{flex:1 1 0}
  @keyframes tb-pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,59,48,.0)}50%{box-shadow:0 0 14px 4px rgba(255,59,48,.9)}}
  @media (prefers-reduced-motion:reduce){.tb a.tb-need{animation:none;box-shadow:0 0 12px 3px rgba(255,59,48,.9)}}
  .tb .s{display:none}
  .tb .tb-who .e{display:inline}
  @media (max-width:520px){.tb{justify-content:flex-start;gap:3px;padding-left:4px;padding-right:4px}.tb a,.tb button{padding:7px 6px;font-size:12.5px}
    .tb .l{display:none}.tb .s{display:inline}}

  .tbs{position:fixed;left:0;right:0;bottom:0;z-index:9995;display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;justify-content:center;
    padding:6px 8px;padding-bottom:calc(6px + env(safe-area-inset-bottom,0px));background:rgba(10,12,16,.96);border-top:1px solid #3a3f4a;
    box-shadow:0 -2px 12px rgba(0,0,0,.45);font:600 13px system-ui,-apple-system,sans-serif;color:#eee;white-space:nowrap}
  .tbs .grp{display:flex;gap:8px;align-items:center;flex-wrap:nowrap;overflow-x:auto;max-width:100%}
  .tbs .mana{gap:5px}.tbs .mp{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;font:800 11px system-ui;
    color:#111;border:1px solid #0008}.tbs .mp.W{background:#f4efc9}.tbs .mp.U{background:#3a8fe0;color:#fff}.tbs .mp.B{background:#5b4a63;color:#fff}
  .tbs .mp.R{background:#e0523a;color:#fff}.tbs .mp.G{background:#3f9d52;color:#fff}.tbs .mp.C{background:#9aa3ab}
  .tbs .mp.any{background:conic-gradient(#f4efc9 0 20%,#3a8fe0 0 40%,#5b4a63 0 60%,#e0523a 0 80%,#3f9d52 0)}.tbs .mp.multi{background:linear-gradient(135deg,#f4efc9,#3f9d52)}
  .tb .tb-lives{display:inline-flex;gap:6px}.tb .tb-life{display:inline-block;padding:7px 10px;border-radius:10px;border:1px solid #3a3f4a;background:#1b1e25;color:#fff;font-weight:700}
  .tb .tb-life.act{border-color:#f2b705;background:#3a2f0e}.tb .tb-who.act{border-color:#f2b705}
  .tbs b{color:#fff}.tbs .tbd{display:inline-block;width:8px;height:8px;border-radius:50%;background:#ffffff33;margin-right:4px}
  .tbs .tbd.done{background:#ffffff88}.tbs .tbd.on{background:#4ad8cf;box-shadow:0 0 8px #4ad8cf}
  .tbs button,.tbs a.tbs-a{border:1px solid #6b5a2e;background:#8c7440;color:#14110a;border-radius:999px;padding:5px 12px;font:inherit;font-weight:700;cursor:pointer;
    text-decoration:none;flex:0 0 auto}
  .tbs button.q{background:#2b2f38;color:#ddd;border-color:#3a3f4a}.tbs button:disabled{opacity:.45;cursor:default}
  .tbs .w{color:#f2b705}.tbs .m{color:#ffb38f}
  @media (max-width:520px){.tbs{justify-content:flex-start;font-size:12px;gap:6px}.tbs .tbd{width:6px;height:6px;margin-right:2px}
    .tbs .grp{flex-wrap:wrap;overflow-x:visible;gap:5px 6px;white-space:normal}.tbs button,.tbs a.tbs-a{padding:6px 12px;min-height:40px;max-width:100%;white-space:normal;text-align:left}}
  @media (max-width:430px){.tb a .e,.tb .tb-who .e{display:none}.tb a[data-h="/log"]{display:none}}`;
  document.head.appendChild(css);
  const bar = document.createElement("div");
  bar.className = "tb";
  bar.setAttribute("role", "navigation");
  bar.setAttribute("aria-label", "table views");
  bar.innerHTML = VIEWS.map(([h, e, l, sh]) => `<a href="${h}" data-h="${h}"${h === tabHere ? ' class="tb-here" aria-current="page"' : ""} aria-label="${l || "Log"}"><span class="e">${e}</span>${l ? ` <span class="l">${l}</span><span class="s">${sh}</span>` : ""}</a>`).join("") +
    '<span class="tb-sp"></span>' +
    '<span class="tb-lives"></span><button type="button" class="tb-who"></button>' +
    '<button type="button" class="tb-v" aria-label="table voice on or off"></button>' +
    '<button type="button" class="tb-n" aria-label="notifications on or off"></button>';
  const who = bar.querySelector(".tb-who");
  const me = bar.querySelector('[data-h="/me"]'), vb = bar.querySelector(".tb-v"), nb = bar.querySelector(".tb-n");
  const baseTitle = document.title;
  let prev = { mine: false, turn: false }, flip = false;
  // make room for the tabs: push the page down, and hide seat.js's own "👤 Who are you?" chip (the bar has the same button)
  function makeRoom() {
    const bh = bar.offsetHeight || 44;
    const h = bh;
    document.body.style.paddingBottom = (strip && strip.style.display !== "none" ? strip.offsetHeight + 8 : 0) + "px";
    document.documentElement.style.scrollPaddingTop = (h + 8) + "px";               // a scrolled-to button never hides under the bars
    document.documentElement.style.scrollPaddingBottom = (strip && strip.style.display !== "none" ? strip.offsetHeight + 8 : 0) + "px";
    document.documentElement.style.setProperty("--hud-h", (strip && strip.style.display !== "none" ? strip.offsetHeight : 0) + "px");
    document.body.style.paddingTop = h + "px";
    document.documentElement.style.setProperty("--tb-h", h + "px");
    for (const b of document.querySelectorAll("body > button")) {
      const cs = getComputedStyle(b);
      if (cs.position === "fixed" && /👤/.test(b.textContent)) b.style.display = "none";    // the bar carries the seat button now
    }
  }

  // ── the stage manager: the table's phase controls on every page (the stage page keeps its own, so it is skipped there) ──
  let strip = null, PI = null, stripMsg = "", stripMsgT = 0;
  const E = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const seatName = () => (window.TableSeat && window.TableSeat.name && window.TableSeat.name()) || "";
  function say2(t) { stripMsg = t; stripMsgT = Date.now() + 5000; stripDraw(); }
  async function stripAct(kind) {
    const p = PI; if (!p) return;
    const me2 = seatName();
    try {
      let r;
      if (kind === "more") { stripMore = !stripMore; stripDraw(); return; }
      if (kind === "auto") { cycleAuto(); return; }
      if (kind === "concede") { concedeAsk = true; stripDraw(); return; }
      if (kind === "concede-no") { concedeAsk = false; stripDraw(); return; }
      if (kind === "concede-yes") {                            // say it, then your own life to 0: the table counts a player at 0 as out
        concedeAsk = false;
        const pilot = (p.ai || []).includes(me2), L0 = (window.__tbLife || {})[me2];
        if (pilot) await post("/api/brain/say", { seat: me2, text: "I concede the game.", force: true });
        else await post("/api/chat", { by: me2, text: "I concede the game.", key: (window.TableSeat && window.TableSeat.body().key) || "" });
        if (typeof L0 === "number" && L0 > 0)
          r = pilot ? await post("/api/brain/life", { seat: me2, player: "me", delta: -L0 })
                    : await post("/api/life", { player: me2, delta: -L0, by: me2, key: (window.TableSeat && window.TableSeat.body().key) || "" });
        try { window.__tbLife = await (await fetch("/api/life", { cache: "no-store" })).json(); } catch {}
        say2("you conceded"); stripDraw();
      }
      if (kind === "life") {                                   // your own life: a virtual deck through its brain seat, a person through /api/life
        const dd = +lifeDelta, pilot = (p.ai || []).includes(me2);
        r = pilot ? await post("/api/brain/life", { seat: me2, player: "me", delta: dd })
                  : await post("/api/life", { player: me2, delta: dd, by: me2, key: (window.TableSeat && window.TableSeat.body().key) || "" });
        if (r.ok) { try { window.__tbLife = await (await fetch("/api/life", { cache: "no-store" })).json(); } catch {} stripDraw(); }
      }
      if (kind === "hold") r = await post("/api/phase/hold", { on: !p.hold, by: me2 || "stage" });
      else if (kind === "win") r = await post("/api/phase/windows", { on: p.windows === false, by: me2 || "stage" });
      else if (kind === "back") {
        r = await post("/api/phase/back", { by: me2 || p.player || "stage" });
        if (r && r.status === 202) { const d = await r.json().catch(() => ({})); const q = d.back_request || {};
          say2(`asked to go back to ${q.to || "the last step"}: waiting for ${(q.confirm_by || []).join(" or ") || "the other player"} to confirm`); return; }
      }
      else if (kind === "backok") r = await post("/api/phase/back/confirm", { by: me2 });
      else if (kind === "begin") {                                         // a virtual-deck seat starts its turn (untap, upkeep, draw)
        r = await post("/api/brain/begin", { seat: me2 });
        if (r && r.status === 409) { const d = await r.json().catch(() => ({})); say2(d.error || "waiting on passes"); stripPoll(); return; }
      }
      else if (kind === "end" && window.__endCheck && window.__endCheck() && Date.now() - (window.__endArmed || 0) > 8000) {
        window.__endArmed = Date.now(); say2(window.__endCheck()); return;                       // something still open: warn once
      }
      else if (kind === "end") {                                          // a virtual-deck seat ends its own turn, as its hand page does
        r = await post("/api/brain/end", { seat: me2, text: "That's my turn." });
        if (r && r.status === 409) { const d = await r.json().catch(() => ({})); say2(d.error || "waiting on passes"); stripPoll(); return; }
      }
      else if (kind === "next") {
        if (!me2) { if (window.TableSeat && window.TableSeat.pick) window.TableSeat.pick(); return; }
        const pilot = (p.ai || []).includes(me2);
        r = pilot && p.player ? await post("/api/brain/pass", { seat: me2, player: p.player, step: p.step })     // a virtual-deck seat passes the way its hand page does
                              : await post("/api/phase/next", window.TableSeat && window.TableSeat.body ? window.TableSeat.body() : { by: me2 });
      }
      if (r && !r.ok) { const d = await r.json().catch(() => ({})); if (!(d.stale || /^stale pass/i.test(d.error || ""))) say2(d.error || "can't do that"); if (d.need_seat && window.TableSeat && window.TableSeat.pick) window.TableSeat.pick(); }
    } catch { say2("table not reachable"); }
    stripPoll();
  }
  function stripDraw() {
    if (!strip) return;
    const p = PI;
    if (!p || p.error || !p.steps) { strip.style.display = "none"; makeRoom(); return; }
    const was = strip.style.display;
    strip.style.display = "flex";
    const me2 = seatName(), pilot = !!me2 && (p.ai || []).includes(me2);
    const ps = p.passes || { need: [], passed: [], next: null }, left = ps.need.filter(n => !ps.passed.includes(n)).length;
    const waiting = (p.waiting || []).length > 0 && p.seconds_left > 0;
    const dots = p.steps.map((_, i) => `<span class="tbd ${i < p.index ? "done" : i === p.index ? "on" : ""}"></span>`).join("");
    let main = "";
    if (!p.player) main = p.mulligan && p.mulligan.on ? `<span class="w">opening hands: waiting on ${E((p.mulligan.waiting || []).join(", "))}</span>` : `<button data-s="next">START ▸</button>`;
    else if (ps.next && ps.next === me2) main = `<button data-s="next">✋ ${left > 1 || pilot ? "Pass" : "NEXT"} ▸</button>`;
    else if (ps.next) main = `<span class="w">waiting on ${E(ps.next)}</span>`;
    else if (p.player === me2 && pilot) main = (here === "/hand" ? "" : `<a class="tbs-a" href="/hand">Hand ▸</a>`) +
      (p.step === "untap" ? `<button data-s="begin">Begin turn ▸</button>` : `<button data-s="end">End turn ▸</button>`);
    else if (p.player === me2) main = `<button data-s="next" ${waiting ? "disabled" : ""}>${p.index >= p.steps.length - 1 ? "END TURN" : "NEXT"} ▸</button>`;
    const pips = (MANA || []).map(src => { const m = /\(([WUBRGC]+)\)\s*$/.exec(src), cols = m ? m[1] : "C";
      const cls = cols.length >= 5 ? "any" : cols.length > 1 ? "multi" : cols, lab = cols.length >= 5 ? "★" : cols.length > 1 ? cols.length : cols;
      return `<span class="mp ${cls}" title="${E(src)}">${E(lab)}</span>`; }).join("");
    const pool = (window.__manaPool || []).map(c => `<span class="mp ${E(c)}">${E(c)}</span>`).join("");
    const manaGrp = MANA === null ? "" : `<span class="grp mana" title="untapped mana sources: Cast taps them for you">Mana ${MANA.length ? pips : '<span class="w">none untapped</span>'}` +
      (pool ? ` · <b>Pool</b> ${pool}` : "") + `</span>`;
    const L = window.__tbLife || {}, myLife = me2 && L[me2] != null ? L[me2] : null;
    // a phone (<= 520px): one compact row (life, mana, the step, the main button, "⋯"); the rest (life +/-, auto-pass, timer, back, concede) opens with ⋯
    const narrow = matchMedia("(max-width:520px)").matches, full = !narrow || stripMore;
    const lifeGrp = myLife === null ? "" : `<span class="grp" title="your life: only you change it">♥ <b>${E(myLife)}</b>` +
      (full ? [-5, -1, 1, 5].map(d => `<button class="q" data-s="life" data-d="${d}">${d > 0 ? "+" : "−"}${Math.abs(d)}</button>`).join("") : "") + `</span>`;
    strip.innerHTML = lifeGrp + manaGrp + `<span class="grp"><span>${narrow && p.player && p.player === me2 ? "" : `<b>${E(p.player || "—")}</b> · `}${E(p.step || "not started")}</span>${full ? `<span>${dots}</span>` : ""}` +
      (waiting ? `<span class="w">${E(p.waiting.join(", "))} may respond… ${Math.ceil(p.seconds_left)}s</span>` : "") +
      (p.hold ? `<span class="w">⏸ on hold</span>` : "") +
      (p.back_request && me2 && p.back_request.by !== me2 ? `<button data-s="backok" title="${E(p.back_request.by)} asked to go back">↶ Confirm BACK to ${E(p.back_request.to)} (${E(p.back_request.by)} asked)</button>` : "") +
      (p.open_questions ? `<span class="w">❓ ${p.open_questions}</span>` : "") +
      (stripMsg && Date.now() < stripMsgT ? `<span class="m">${E(stripMsg)}</span>` : "") +
      (full && me2 ? `<button class="q" data-s="auto" title="press Pass for you in other players' steps (click to change)">🤖 ${AUTO_LABEL[autoMode()].replace("auto-pass: ", "")}</button>` : "") +
      (full ? `<button class="q" data-s="hold" title="freeze NEXT while the table sorts something out">${p.hold ? "▶ release" : "⏸"}</button>` +
      `<button class="q" data-s="win" title="step timeouts on or off">⏱ ${p.windows === false ? "off" : (p.window_secs || "") + "s"}</button>` +
      (p.player ? `<button class="q" data-s="back" title="back a step">◂</button>` : "") : "") + main +
      (narrow ? `<button class="q" data-s="more" aria-expanded="${stripMore}" title="${stripMore ? "fewer" : "more"} controls">${stripMore ? "▾" : "⋯"}</button>` : "") + `</span>` +
      (full && me2 && p.player && myLife !== null && myLife > 0 ? (concedeAsk ? `<span class="grp"><span class="w">Concede the game?</span><button data-s="concede-yes" style="background:#a33;color:#fff;border-color:#a33">Yes, concede</button><button class="q" data-s="concede-no">No</button></span>`
        : `<button class="q" data-s="concede" title="forfeit this game (asks first)">🏳 Concede</button>`) : "");
    if (was !== "flex") makeRoom();
    else if (strip.offsetHeight !== lastHud) { lastHud = strip.offsetHeight;      // the strip grows and shrinks (a phone wraps it): keep the room under it, and the pins above it, exact
      document.documentElement.style.setProperty("--hud-h", lastHud + "px"); document.body.style.paddingBottom = (lastHud + 8) + "px";
      document.documentElement.style.scrollPaddingBottom = (lastHud + 8) + "px"; }
  }
  // Auto-pass, per device: the table's own Auto Pass only exists for seats with physical cards, and a virtual-deck seat has none, so this
  // presses Pass for you in OTHER players' steps (never your own). "no combat" stops at combat so you can block or respond.
  const AUTO = ["off", "others", "others-no-combat"], AUTO_LABEL = { off: "auto-pass: off", others: "auto-pass: others", "others-no-combat": "auto-pass: stops at combat" };
  const COMBAT_STEPS = ["beginning of combat", "declare attackers", "declare blockers", "combat damage"];
  let lastAuto = "", autoT = 0, autoAt = 0;
  const AUTO_DELAY = 4000;
  const autoMode = () => { try { return localStorage.getItem("divinci-autopass:" + seatName()) || "off"; } catch { return "off"; } };
  function cycleAuto() {
    const nx = AUTO[(AUTO.indexOf(autoMode()) + 1) % AUTO.length];
    try { localStorage.setItem("divinci-autopass:" + seatName(), nx); } catch {}
    lastAuto = ""; say2(AUTO_LABEL[nx]); stripAuto();
  }
  function stripAuto() {
    const p = PI, me2 = seatName(), m = autoMode();
    if (!p || !me2 || !p.player || m === "off") return;
    const ps = p.passes || {};
    if (p.player === me2 || ps.next !== me2 || p.hold || p.open_questions) return;
    if (m === "others-no-combat" && COMBAT_STEPS.includes(p.step)) return;
    const k = [p.player, p.index, p.step, (ps.passed || []).length].join("|");
    if (k === lastAuto) return;
    lastAuto = k;
    autoAt = Date.now() + AUTO_DELAY; clearTimeout(autoT);         // a few seconds first: time to look, and to respond
    autoT = setTimeout(() => { autoAt = 0; const q = PI, qs = (q && q.passes) || {};
      if (q && [q.player, q.index, q.step, (qs.passed || []).length].join("|") === k && qs.next === seatName()) stripAct("next"); }, AUTO_DELAY);
    say2(`auto-passing in ${AUTO_DELAY / 1000}s: click anywhere to stop`);
  }
  addEventListener("pointerdown", e => {                            // any click while it counts down holds it for this step
    if (autoAt && !(e.target.closest && e.target.closest('[data-s="auto"]'))) { clearTimeout(autoT); autoAt = 0; say2("auto-pass held: pass when you're ready"); }
  }, true);
  addEventListener("keydown", () => { if (autoAt) { clearTimeout(autoT); autoAt = 0; say2("auto-pass held: pass when you're ready"); } }, true);
  let MANA = null, lifeDelta = 0, concedeAsk = false, stripMore = false, lastHud = -1;
  async function stripPoll() {
    try { PI = await (await fetch("/api/phase", { cache: "no-store" })).json(); } catch { PI = null; }
    try { window.__tbLife = await (await fetch("/api/life", { cache: "no-store" })).json(); window.__tbActive = (PI && PI.player) || ""; } catch {}
    const me3 = seatName();
    if (me3 && PI && (PI.ai || []).includes(me3) && window.TableSeat && window.TableSeat.body) {     // a virtual deck: its own untapped mana
      try { const k = window.TableSeat.body().key || "";
        const r = await fetch("/api/brain/state?seat=" + encodeURIComponent(me3), { cache: "no-store", headers: { "X-Seat-Key": k } });
        MANA = r.ok ? ((await r.json()).mana_sources || []) : null; } catch { MANA = null; }
    } else MANA = null;
    stripDraw(); stripAuto();
  }
  function mountStrip() {
    if (here === "/stage") return;                                     // the stage page has its own, fuller one
    strip = document.createElement("div"); strip.className = "tbs"; strip.style.display = "none";
    strip.setAttribute("role", "toolbar"); strip.setAttribute("aria-label", "stage manager");
    strip.addEventListener("click", e => { const b = e.target.closest("[data-s]"); if (b && !b.disabled) { lifeDelta = b.dataset.d; stripAct(b.dataset.s); } });
    document.body.appendChild(strip);
    stripPoll(); setInterval(stripPoll, 1500);
  }
  function mountFlavour() {
    if (here !== "/stage" && here !== "/xr") return;
    const sw = document.createElement("div");
    sw.style.cssText = "position:fixed;left:50%;transform:translateX(-50%);top:calc(var(--tb-h,52px) + 6px);z-index:9994;display:flex;gap:0;border:1px solid #3a3f4a;border-radius:999px;overflow:hidden;font:700 13px system-ui";
    const b = (h, l) => `<a href="${h}" style="padding:6px 14px;text-decoration:none;color:#fff;background:${here === h ? "#1f4d33" : "#23262e"}">${l}</a>`;
    sw.innerHTML = b("/stage", "🎭 Stage") + b("/xr", "🔮 Round table");
    document.body.appendChild(sw);
  }
  const mount = () => { document.body.appendChild(bar); mountStrip(); mountFlavour(); refresh(); makeRoom(); setTimeout(makeRoom, 400); setTimeout(makeRoom, 1500); addEventListener("resize", makeRoom); };

  // ── "My page" shows when to act ──
  function refresh() {
    const s = window.__urgent ? window.__urgent.state() : {};
    me.classList.toggle("tb-need", !!s.mine && here !== "/me");
    me.classList.toggle("tb-turn", !!s.turn && !s.mine && here !== "/me");
    const urgent = s.mine || s.turn;
    me.innerHTML = urgent ? `<span class="e">${s.mine ? "✋" : "▶"}</span> <span class="l">${s.mine ? "Pass priority" : "Your turn"}</span><span class="s">${s.mine ? "Pass" : "Turn"}</span>`
      : '<span class="e">📱</span> <span class="l">Me</span><span class="s">Me</span>';
    const nm = window.TableSeat && window.TableSeat.name && window.TableSeat.name();
    const L = window.__tbLife || {}, act = window.__tbActive || "", clean = x => String(x).replace(/[<>&]/g, "");
    who.innerHTML = nm ? `<span class="e">👤 </span>${clean(nm)}${L[nm] != null ? ` ♥ ${clean(L[nm])}` : ""}` : '<span class="e">👤 </span>Who?';
    who.classList.toggle("act", !!nm && nm === act);
    const lv = bar.querySelector(".tb-lives");
    if (lv) { const html = Object.entries(L).filter(([n]) => n !== nm).map(([n, v]) => `<span class="tb-life${n === act ? " act" : ""}" title="${clean(n)}'s life">${clean(n)} ♥ ${clean(v)}</span>`).join("");
      if (lv.innerHTML !== html) lv.innerHTML = html; }
    vb.textContent = !synth || muted ? "🔇" : prefs.voice ? "🔊" : "🔈";
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
    vb.title = !synth ? "this browser can't speak" : muted ? "muted for this tab (?mute=1)" : prefs.voice ? "table voice on (tap to mute)" : "table voice off (tap to turn on)";
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
    if (!synth || !prefs.voice || muted || !text) return;
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
