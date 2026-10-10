// The table's check-in, before the game starts: who is seated, who has claimed their seat, each seat's commander, who is ready, and a
// Start button that wakes up once everyone is. Include with <script src="/assets/checkin.js" defer></script>; it shows only while no
// game is under way and hides itself when the first turn begins (the opening high roll follows START). Needs seat.js (this device's
// seat and key).
// A person's seat declares its commander here (required before Ready when the room requires the check-in) and may add a deck list:
// pasted, or a link. Virtual decks (pilots, table AIs) are declared by their deck files. Deck lists are public once declared.
(function () {
  "use strict";
  if (window.__checkin) return; window.__checkin = true;
  if (new URLSearchParams(location.search).get("nocheckin") === "1") return;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const box = document.createElement("div");
  box.id = "checkin";
  box.style.cssText = "position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:70;width:min(94vw,440px);background:#0d1d25f2;color:#ece4cf;" +
    "border-radius:14px;box-shadow:0 0 0 1px #d9b46a55,0 10px 40px #000a;padding:12px 14px;font:14px/1.4 system-ui,-apple-system,sans-serif;display:none;" +
    "max-height:calc(100vh - 28px);overflow:auto";
  const btn = "font:600 14px system-ui;border-radius:8px;border:1px solid #d9b46a66;background:#10242d;color:#ece4cf;padding:7px 12px;cursor:pointer;margin:6px 6px 0 0";
  const field = "width:100%;box-sizing:border-box;background:#10242d;color:#ece4cf;border:1px solid #d9b46a55;border-radius:8px;padding:6px 8px;font:14px system-ui";
  box.innerHTML = `<div style="font:700 12px Cinzel,Georgia,serif;letter-spacing:.12em;color:#d9b46a;text-transform:uppercase;margin-bottom:6px">Table check-in</div>
    <div id="ci-rows"></div><div id="ci-mine"></div>
    <div><span id="ci-claim" style="color:#9fb0b0;font-size:13px;display:none">Claim your seat (👤 at the top) to check in.</span>
      <button id="ci-ready" style="${btn};display:none">I'm ready</button>
      <button id="ci-start" style="${btn};opacity:.45" disabled>Start: roll for first</button></div>
    <div id="ci-msg" style="color:#ffb38f;min-height:1.2em;font-size:13px"></div>`;
  const $ = id => box.querySelector("#" + id);
  let err = "", lastRows = "", mineName = "", cur = null, need = false;
  const me = () => (window.TableSeat && TableSeat.name()) || "";
  const body = x => (window.TableSeat ? TableSeat.body(x) : x);
  const post = async (path, b) => { const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
    let d = {}; try { d = await r.json(); } catch {} return { ok: r.ok, d }; };
  const failText = d => (d.error || "") + (d.suggestions && d.suggestions.length ? " — " + d.suggestions.join(" · ") : "");

  function mineForm(s) {                            // built once per seat, so polling never wipes what you are typing
    if (s.kind !== "human") return `<div style="color:#9fb0b0;font-size:13px;margin:6px 0 0">${esc(s.name)} plays a virtual deck: its commander and list come from its deck file.</div>`;
    return `<div style="margin:8px 0 0"><label for="ci-cmd" style="font-size:13px;color:#d9b46a">Your commander</label>
      <input id="ci-cmd" list="ci-cmd-list" maxlength="80" autocomplete="off" placeholder="e.g. Inspirit, Flagship Vessel" style="${field}">
      <datalist id="ci-cmd-list"></datalist>
      <details id="ci-deck-box" style="margin-top:6px"><summary style="cursor:pointer;font-size:13px;color:#d9b46a">Deck list (optional)</summary>
        <textarea id="ci-deck" rows="5" placeholder="Paste your list (1 Sol Ring, one card per line) or a link to it" style="${field};margin-top:4px;resize:vertical;font-size:13px"></textarea>
        <div style="color:#9fb0b0;font-size:12px">Deck lists are public once declared. A link is stored as you typed it, never opened by the table.</div>
      </details>
      <button id="ci-declare" style="${btn}">Declare</button><span id="ci-decl-state" style="font-size:12.5px;color:#9fb0b0"></span></div>`;
  }
  function readyState() {                         // Ready is disabled until a commander is declared (or typed) when the check-in is required
    const r = $("ci-ready"); if (!cur) return;
    const typed = ($("ci-cmd")?.value || "").trim();
    const blocked = need && cur.kind === "human" && !cur.ready && !cur.declared && !typed;
    r.disabled = blocked; r.style.opacity = blocked ? ".45" : "";
    r.title = blocked ? "Name your commander first" : "";
    r.textContent = cur.ready ? "Not ready" : blocked ? "I'm ready (name your commander first)" : "I'm ready";
  }
  function draw(p) {
    const ci = p.checkin; if (p.player || !ci) { box.style.display = "none"; return; }
    need = !!p.checkin_required;
    cur = ci.seats.find(s => s.name.toLowerCase() === me().toLowerCase()) || null;
    const rows = ci.seats.map(s => {
      const state = s.ready ? '<span style="color:#7be0a0">✓ ready</span>' : s.claimed ? '<span style="color:#f4dc9b">here, not ready yet</span>' : '<span style="color:#9fb0b0">empty: waiting for a player to claim it (👤)</span>';
      const kind = s.kind === "pilot" ? " · virtual deck" : s.kind === "ai" ? " · table AI" : "";
      const deck = s.deck_url ? " · 🔗 list" : s.deck_cards ? ` · ${s.deck_cards} cards` : "";
      const cmd = s.commander ? `<span style="font-weight:400;color:#f4dc9b"> · ${esc(s.commander)}</span>` : s.kind === "human" ? '<span style="font-weight:400;color:#9fb0b0"> · no commander yet</span>' : "";
      return `<div style="display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px solid #ffffff12"><b data-seat="${esc(s.name)}">${esc(s.name)}${cmd}<span style="font-weight:400;color:#9fb0b0">${esc(kind + deck)}</span></b><span style="white-space:nowrap">${state}</span></div>`;
    }).join("");
    if (rows !== lastRows) { $("ci-rows").innerHTML = rows; lastRows = rows; }
    const nm = cur ? cur.name : "";
    if (nm !== mineName) { $("ci-mine").innerHTML = cur ? mineForm(cur) : ""; mineName = nm; }
    const inp = $("ci-cmd");
    if (inp && cur && cur.commander && document.activeElement !== inp && !inp.dataset.edited) inp.value = cur.commander;
    const st = $("ci-decl-state");
    if (st && cur) st.textContent = cur.declared ? `declared: ${cur.commander}${cur.deck_url ? " · list link" : cur.deck_cards ? ` · ${cur.deck_cards}-card list` : ""}` : "";
    $("ci-claim").style.display = cur ? "none" : "";
    $("ci-ready").style.display = cur ? "" : "none";
    readyState();
    const canStart = !need || (ci.all_ready && !(ci.undeclared || []).length);
    const s = $("ci-start"); s.disabled = !canStart;
    s.style.cssText = btn + (canStart ? ";background:linear-gradient(180deg,#f4dc9b,#d9b46a);color:#1a1206" : ";opacity:.45");
    const undecl = (ci.undeclared || []).length ? "Waiting for " + ci.undeclared.join(", ") + " to declare a commander" : "";
    $("ci-msg").textContent = err || (need && !ci.all_ready ? "Waiting for " + ci.waiting.join(", ") : need ? undecl : "");
    box.style.display = "";
  }
  function declaration() {                        // what the form adds to a Ready / Declare: only what changed, so a re-press does not re-log
    const out = {}, typed = ($("ci-cmd")?.value || "").trim(), deck = $("ci-deck")?.value;
    if (typed && (!cur || typed !== cur.commander)) out.commander = typed;
    if (deck !== undefined && deck.trim() && deck !== box.dataset.sentDeck) out.decklist = deck;
    return out;
  }
  box.addEventListener("input", e => {
    if (e.target.id === "ci-cmd") { e.target.dataset.edited = "1"; readyState(); complete(e.target.value); }
  });
  let tmo = 0, lastQ = "";
  function complete(q) {                          // name completion from the card file the table already has (offline)
    clearTimeout(tmo); q = q.trim(); if (q.length < 2 || q === lastQ) return;
    tmo = setTimeout(async () => {
      lastQ = q;
      try { const d = await (await fetch("/api/cardnames?q=" + encodeURIComponent(q))).json();
        const dl = $("ci-cmd-list"); if (dl) dl.innerHTML = (d.names || []).map(n => `<option value="${esc(n)}"></option>`).join(""); } catch {}
    }, 180);
  }
  async function send(path, extra) {
    const decl = declaration();
    const r = await post(path, body({ by: me(), ...decl, ...extra }));
    err = r.ok ? "" : (failText(r.d) || "couldn't check in");
    if (r.ok) { if (decl.decklist !== undefined) box.dataset.sentDeck = decl.decklist; const i = $("ci-cmd"); if (i) delete i.dataset.edited; }
    poll(true);
  }
  box.addEventListener("click", async e => {
    if (e.target.id === "ci-ready") {
      if (e.target.disabled) return;
      await send("/api/ready", { ready: !(cur && cur.ready) });
    } else if (e.target.id === "ci-declare") {
      const d = declaration();
      if (!Object.keys(d).length) { err = cur && cur.declared ? "" : "name your commander first"; poll(true); return; }
      await send("/api/declare-deck", {});
    } else if (e.target.id === "ci-start") {
      const r = await post("/api/phase/next", body()); err = r.ok ? "" : (r.d.error || "couldn't start"); poll(true);
    }
  });
  async function poll(once) {
    try { draw(await (await fetch("/api/phase")).json()); } catch {}
    if (once !== true) setTimeout(poll, 2000);
  }
  function mount() { document.body.appendChild(box); poll(); }
  if (document.body) mount(); else addEventListener("DOMContentLoaded", mount);
})();
