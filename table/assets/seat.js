// 👤 Which player is this device? A device claims one person's seat and gets a secret key (kept in
// this browser); NEXT only counts with that key, and only for that player's own pass.
//   window.TableSeat.name()       → "Sam" or ""
//   window.TableSeat.body(extra)  → {by, key, ...extra} for /api/phase/next
//   window.TableSeat.pick()       → open the picker
// A phone link "?player=Sam" claims Sam on first open; "?player=Sam&key=…" adds another device.
(() => {
  if (window.TableSeat) return;
  const KEY = "table.seat";
  const esc = t => String(t ?? "").replace(/[<>&"']/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  const load = () => { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; } };
  const save = v => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch {} };
  let mine = load();
  // Every tab of this site shares one localStorage entry, but each tab used to copy it into memory once and never look again:
  // a tab opened before you claimed kept asking who you are, and a tab holding an old key kept using it. Follow the shared entry.
  addEventListener("storage", e => { if (e.key === KEY) { mine = load(); chip(); } });
  const post = (p, b) => fetch(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) })
    .then(async r => ({ ok: r.ok, d: await r.json().catch(() => ({})) }));
  async function claim(name, key, invite) {
    const r = await post("/api/seat/claim", { name, key, invite });
    if (r.ok) { mine = { name: r.d.name, key: r.d.key }; save(mine); chip(); }
    return r;
  }
  const chipEl = document.createElement("button");
  chipEl.style.cssText = "position:fixed;right:12px;top:12px;z-index:62;padding:5px 10px;border-radius:999px;border:1px solid #3a3f4a;" +
    "background:#1b1e25;color:#eee;font:600 12.5px system-ui,-apple-system,sans-serif;cursor:pointer;opacity:.9";
  function chip() { chipEl.textContent = mine.name ? `👤 ${mine.name}` : "👤 Who are you?"; chipEl.title = mine.name ? "this device plays as " + mine.name : "claim your seat to pass priority from this device"; }
  const sheet = document.createElement("div");
  sheet.style.cssText = "position:fixed;inset:0;z-index:80;background:rgba(0,0,0,.55);display:none;align-items:flex-start;justify-content:center;padding:70px 12px;font:14px/1.45 system-ui,-apple-system,sans-serif;color:#eee";
  async function pick() {
    let c = {}; try { c = await (await fetch("/api/seat/claims")).json(); } catch {}
    const link = mine.name && mine.key ? `${location.origin}/me?player=${encodeURIComponent(mine.name)}&key=${encodeURIComponent(mine.key)}` : "";
    sheet.innerHTML = `<div style="width:min(420px,100%);background:#14161b;border:1px solid #3a3f4a;border-radius:14px;padding:14px 16px">
      <div style="display:flex;justify-content:space-between"><b style="font-size:16px">👤 Who is playing on this device?</b>
        <button data-x style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:3px 10px;cursor:pointer">✕</button></div>
      <div style="opacity:.7;font-size:12.5px;margin:4px 0 10px">You can only pass priority for the player you claim. A claimed seat can't be taken by another device.</div>
      ${Object.entries(c.humans || {}).map(([n, taken]) => { const here = (c.this_device || []).includes(n), off = taken && n !== mine.name && !here;
          return `<button data-claim="${esc(n)}" ${off ? "disabled" : ""}
          style="display:block;width:100%;margin:6px 0;padding:9px;border-radius:9px;border:1px solid #3a3f4a;background:${n === mine.name ? "#1f4d33" : "#23262e"};color:#eee;font:600 14px system-ui;cursor:pointer;opacity:${off ? .45 : 1}">
          ${esc(n)}${n === mine.name ? " — this window ✓" : here ? " — yours on this device (tap to use here)" : taken ? " — claimed on another device" : ""}</button>`; }).join("")}
      ${link ? `<div style="margin-top:10px;font-size:12.5px;opacity:.85">Use ${esc(mine.name)} on another device too: open this link there (keep it private).<br>
          <input readonly value="${esc(link)}" style="width:100%;margin-top:4px;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:6px;padding:4px 6px;font:12px ui-monospace,monospace"></div>` : ""}
      ${mine.name && !proxied ? `<div style="margin-top:12px;border-top:1px solid #2a2e37;padding-top:10px"><b>🤝 Hand off ${esc(mine.name)}'s seat</b>
          <div style="opacity:.7;font-size:12.5px;margin:2px 0 6px">Someone else plays it from their own device; you can take it back.</div>
          <div style="display:flex;flex-wrap:wrap;gap:6px">
          ${Object.keys(c.humans || {}).filter(n => n !== mine.name).map(n => `<button data-handoff="${esc(n)}" style="padding:7px 10px;border-radius:8px;border:1px solid #3a3f4a;background:#23262e;color:#eee;cursor:pointer">to ${esc(n)}</button>`).join("")}
          <button data-handoff="open" style="padding:7px 10px;border-radius:8px;border:1px solid #3a3f4a;background:#23262e;color:#eee;cursor:pointer">🌐 open invite link</button>
          <button data-handoff="back" style="padding:7px 10px;border-radius:8px;border:1px solid #3a3f4a;background:#1f4d33;color:#c9f2d7;cursor:pointer">take it back</button></div>
          <div data-handout style="margin-top:6px;font-size:12.5px"></div></div>` : ""}
      <div data-msg style="margin-top:8px;color:#ffb3b3;min-height:1em"></div></div>`;
    sheet.style.display = "flex";
  }
  sheet.addEventListener("click", async ev => {
    if (ev.target === sheet || ev.target.closest("[data-x]")) { sheet.style.display = "none"; return; }
    const h = ev.target.closest("[data-handoff]");
    if (h) {
      const r = await post("/api/seat/handoff", { by: mine.name, key: mine.key, to: h.dataset.handoff });
      const out = sheet.querySelector("[data-handout]");
      if (!r.ok) { out.textContent = r.d.error || "couldn't hand off"; return; }
      out.innerHTML = r.d.invite ? `One-use link — whoever opens it first takes the seat:<br><input readonly value="${esc(location.origin + r.d.path)}" style="width:100%;margin-top:4px;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:6px;padding:4px 6px;font:12px ui-monospace,monospace">`
        : r.d.to ? `Handed to ${esc(r.d.to)} — their device can now play ${esc(r.d.seat)} (open /me?player=${esc(r.d.seat)}).` : "Taken back: only your devices play this seat.";
      return;
    }
    const b = ev.target.closest("[data-claim]");
    if (b) {
      const r = await claim(b.dataset.claim);
      if (r.ok) { sheet.style.display = "none"; location.reload(); } else sheet.querySelector("[data-msg]").textContent = r.d.error || "couldn't claim that seat";
    }
  });
  const alert0 = m => { chipEl.textContent = "👤 " + m; };
  chipEl.onclick = pick;
  function mount() { document.body.appendChild(chipEl); document.body.appendChild(sheet); chip(); }
  if (document.body) mount(); else addEventListener("DOMContentLoaded", mount);
  // a phone link names its player: claim it (or add this device with ?key=)
  const q = new URLSearchParams(location.search), qp = q.get("player"), qk = q.get("key"), qi = q.get("invite");
  let proxied = false;                                  // this page plays a seat someone lent to my player
  const ready = (async () => {
    if (qp && qi) {                                   // an open-market invite: take the seat, hide the token
      const r = await claim(qp, undefined, qi);
      history.replaceState(null, "", location.pathname + "?player=" + encodeURIComponent(qp));
      if (!r.ok) alert0(r.d.error || "that invite was already used");
      return;
    }
    if (qp && mine.name && qp !== mine.name && !qk) {
      try {
        const c = await (await fetch("/api/seat/claims")).json();
        if ((c.proxy || {})[qp] === mine.name) { proxied = true; chipEl.textContent = `👤 ${mine.name} as ${qp}`; return; }
      } catch {}
    }
    if (mine.name && mine.key && !qk) {               // check in with my key: the server records this device,
      const sent = mine.key, r = await post("/api/seat/claim", { name: mine.name, key: mine.key });   // so this laptop's other windows
      if (r.ok && r.d.key && r.d.key !== sent) {      // the table did not know that key (an old room's, one site shares them all) and issued a new one: keep it
        if (load().key === sent || !load().key) { mine = { name: r.d.name, key: r.d.key }; save(mine); } else mine = load();   // unless another tab already did
        chip();
      } else if (!r.ok && r.d && /claimed on another device|only a person/.test(r.d.error || "")) {
        if (load().key === sent) { mine = {}; save(mine); } else mine = load();          // forget it only if no other tab has replaced it meanwhile
        chip();
      }
    }                                                 // (any address, any tab) pick up the same seat
    if (!mine.name && !qp) {
      try {
        const c = await (await fetch("/api/seat/claims")).json();
        if ((c.this_device || []).length === 1) await claim(c.this_device[0]);   // this device's player: same seat here
      } catch {}
    }
    if (qp && (mine.name !== qp || qk)) {
      const r = await claim(qp, qk || undefined);
      if (qk && r.ok) history.replaceState(null, "", location.pathname + "?player=" + encodeURIComponent(qp));   // don't leave the key in the bar
    }
  })();
  // Every POST this page makes to its own table carries this device's seat key (X-Seat-Key), so the table can
  // check that a change comes from a seated player, and for their own seat. Same origin only: never sent elsewhere.
  const _fetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    try {
      const url = new URL(typeof input === "string" ? input : input.url, location.href);
      const method = (init.method || (typeof input === "string" ? "GET" : input.method) || "GET").toUpperCase();
      if (mine.key && url.origin === location.origin && method !== "GET" && method !== "HEAD") {
        const h = new Headers(init.headers || (typeof input === "string" ? undefined : input.headers));
        if (!h.has("X-Seat-Key")) h.set("X-Seat-Key", mine.key);
        init = { ...init, headers: h };
      }
    } catch {}
    return _fetch(input, init);
  };
  window.TableSeat = { name: () => (proxied ? qp : mine.name) || "",
                       body: (extra = {}) => ({ by: (proxied ? qp : mine.name) || "", key: mine.key || "", ...extra }), pick, ready };
})();
