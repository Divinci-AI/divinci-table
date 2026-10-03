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
  const post = (p, b) => fetch(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) })
    .then(async r => ({ ok: r.ok, d: await r.json().catch(() => ({})) }));
  async function claim(name, key) {
    const r = await post("/api/seat/claim", { name, key });
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
      ${Object.entries(c.humans || {}).map(([n, taken]) => `<button data-claim="${esc(n)}" ${taken && n !== mine.name ? "disabled" : ""}
          style="display:block;width:100%;margin:6px 0;padding:9px;border-radius:9px;border:1px solid #3a3f4a;background:${n === mine.name ? "#1f4d33" : "#23262e"};color:#eee;font:600 14px system-ui;cursor:pointer;opacity:${taken && n !== mine.name ? .45 : 1}">
          ${esc(n)}${n === mine.name ? " — this device ✓" : taken ? " — claimed on another device" : ""}</button>`).join("")}
      ${link ? `<div style="margin-top:10px;font-size:12.5px;opacity:.85">Use ${esc(mine.name)} on another device too: open this link there (keep it private).<br>
          <input readonly value="${esc(link)}" style="width:100%;margin-top:4px;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:6px;padding:4px 6px;font:12px ui-monospace,monospace"></div>` : ""}
      <div data-msg style="margin-top:8px;color:#ffb3b3;min-height:1em"></div></div>`;
    sheet.style.display = "flex";
  }
  sheet.addEventListener("click", async ev => {
    if (ev.target === sheet || ev.target.closest("[data-x]")) { sheet.style.display = "none"; return; }
    const b = ev.target.closest("[data-claim]");
    if (b) {
      const r = await claim(b.dataset.claim);
      if (r.ok) { sheet.style.display = "none"; location.reload(); } else sheet.querySelector("[data-msg]").textContent = r.d.error || "couldn't claim that seat";
    }
  });
  chipEl.onclick = pick;
  function mount() { document.body.appendChild(chipEl); document.body.appendChild(sheet); chip(); }
  if (document.body) mount(); else addEventListener("DOMContentLoaded", mount);
  // a phone link names its player: claim it (or add this device with ?key=)
  const q = new URLSearchParams(location.search), qp = q.get("player"), qk = q.get("key");
  const ready = (async () => {
    if (qp && (mine.name !== qp || qk)) {
      const r = await claim(qp, qk || undefined);
      if (qk && r.ok) history.replaceState(null, "", location.pathname + "?player=" + encodeURIComponent(qp));   // don't leave the key in the bar
    }
  })();
  window.TableSeat = { name: () => mine.name || "", body: (extra = {}) => ({ by: mine.name || "", key: mine.key || "", ...extra }), pick, ready };
})();
