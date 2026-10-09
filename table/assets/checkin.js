// The table's check-in, before the game starts: who is seated, who has claimed their seat, who is ready, and a Start button that
// wakes up once everyone is. Include with <script src="/assets/checkin.js" defer></script>; it shows only while no game is under way
// and hides itself when the first turn begins (the opening high roll follows START). Needs seat.js (this device's seat and key).
(function () {
  "use strict";
  if (window.__checkin) return; window.__checkin = true;
  if (new URLSearchParams(location.search).get("nocheckin") === "1") return;
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const box = document.createElement("div");
  box.id = "checkin";
  box.style.cssText = "position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:70;width:min(94vw,440px);background:#0d1d25f2;color:#ece4cf;" +
    "border-radius:14px;box-shadow:0 0 0 1px #d9b46a55,0 10px 40px #000a;padding:12px 14px;font:14px/1.4 system-ui,-apple-system,sans-serif;display:none";
  const btn = "font:600 14px system-ui;border-radius:8px;border:1px solid #d9b46a66;background:#10242d;color:#ece4cf;padding:7px 12px;cursor:pointer;margin:6px 6px 0 0";
  let last = "", err = "";
  const me = () => (window.TableSeat && TableSeat.name()) || "";
  const body = x => (window.TableSeat ? TableSeat.body(x) : x);
  const post = async (path, b) => { const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
    let d = {}; try { d = await r.json(); } catch {} return { ok: r.ok, d }; };
  function draw(p) {
    const ci = p.checkin; if (p.player || !ci) { box.style.display = "none"; return; }
    const mine = ci.seats.find(s => s.name.toLowerCase() === me().toLowerCase());
    const need = p.checkin_required;
    const rows = ci.seats.map(s => {
      const state = s.ready ? '<span style="color:#7be0a0">✓ ready</span>' : s.claimed ? '<span style="color:#f4dc9b">here, not ready yet</span>' : '<span style="color:#9fb0b0">empty: waiting for a player to claim it (👤)</span>';
      return `<div style="display:flex;justify-content:space-between;gap:10px;padding:3px 0;border-bottom:1px solid #ffffff12"><b>${esc(s.name)}${s.kind === "pilot" ? ' <span style="font-weight:400;color:#9fb0b0">· virtual deck</span>' : s.kind === "ai" ? ' <span style="font-weight:400;color:#9fb0b0">· table AI</span>' : ""}</b><span>${state}</span></div>`;
    }).join("");
    const canStart = !need || ci.all_ready;
    const html = `<div style="font:700 12px Cinzel,Georgia,serif;letter-spacing:.12em;color:#d9b46a;text-transform:uppercase;margin-bottom:6px">Table check-in</div>${rows}
      <div>${mine ? `<button id="ci-ready" style="${btn}">${mine.ready ? "Not ready" : "I'm ready"}</button>` : `<span style="color:#9fb0b0;font-size:13px">Claim your seat (👤 at the top) to check in.</span>`}
      <button id="ci-start" style="${btn};${canStart ? "background:linear-gradient(180deg,#f4dc9b,#d9b46a);color:#1a1206" : "opacity:.45"}" ${canStart ? "" : "disabled"}>Start: roll for first</button></div>
      <div style="color:#ffb38f;min-height:1.2em;font-size:13px">${esc(err) || (need && !ci.all_ready ? "Waiting for " + esc(ci.waiting.join(", ")) : "")}</div>`;
    if (html !== last) { box.innerHTML = html; last = html; }
    box.style.display = "";
  }
  box.addEventListener("click", async e => {
    if (e.target.id === "ci-ready") {
      const cur = last.includes("Not ready</button>");
      const r = await post("/api/ready", body({ by: me(), ready: !cur })); err = r.ok ? "" : (r.d.error || "couldn't check in"); poll(true);
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
