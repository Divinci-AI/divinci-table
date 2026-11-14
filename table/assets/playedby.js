// Who plays each seat: a person, the rules autopilot, or a model. On every page that loads this (hand, me, board, table, 3d, and /watch,
// the stream's view). It reads GET /api/played-by (public, no key) and shows ONE line for every seat that a person is not playing:
//     Sam's seat: the rules autopilot        Claude's seat: the rules autopilot · talks as anthropic:claude-…
// so nobody watching is told a person is playing when one is not (2026-11-13: a player went to bed, the autopilot played his seat for two
// rounds, and every page still showed a person). A seat a person plays gets no line. Nothing here blocks anything.
//
// On the seat's OWN page, while the autopilot plays it, the line has a button: "I'm back: I play my seat". Any move made from that page does
// the same (the table gives the seat back to the person at their first move, and the autopilot stops). The table's host hands a seat
// to the autopilot and back (launch/table_tools.py stand-in).
(function () {
  "use strict";
  if (window.__playedByLoaded) return; window.__playedByLoaded = true;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const WATCH = location.pathname === "/watch";
  const seat = () => (!WATCH && window.TableSeat && TableSeat.name && TableSeat.name()) || "";
  const key = () => (!WATCH && window.TableSeat && TableSeat.body && TableSeat.body().key) || "";
  const css = document.createElement("style");
  css.textContent = `
  .pb-strip{position:fixed;left:10px;bottom:calc(var(--hud-h,0px) + 10px);z-index:9000;display:flex;flex-direction:column;gap:4px;max-width:min(460px,calc(100vw - 20px));pointer-events:none}
  .pb-line{pointer-events:auto;background:#241a3a;color:#efe6ff;border:1px solid #9b7fd6;border-radius:10px;padding:5px 10px;font:600 13px/1.35 system-ui,-apple-system,sans-serif;box-shadow:0 3px 12px rgba(0,0,0,.45)}
  .pb-line small{font-weight:400;opacity:.85}
  .pb-line button{margin-left:8px;background:#1f5a33;color:#fff;border:1px solid #7fd39a;border-radius:8px;padding:4px 9px;font:600 13px system-ui,sans-serif;cursor:pointer;min-height:30px}
  .pb-line.pb-unknown{background:#2a2a2a;border-color:#777;color:#ddd}`;
  document.head.appendChild(css);
  let strip = null, sig = "", err = "";

  function what(s) {
    if (s.kind === "autopilot") return "the rules autopilot (no model)" + (s.stand_in ? `, standing in for ${esc(s.name)}` : "");
    if (s.kind === "model") return esc(s.label);
    if (s.kind === "table") return "the table's own AI";
    return "nobody has played it yet";
  }
  function render(seats) {
    const lines = (seats || []).filter(s => s.kind !== "person");
    const mine = seat();
    const html = lines.map(s => {
      const back = s.name === mine && s.kind === "autopilot" && s.stand_in && key() ? `<button data-pb-back>I'm back: I play my seat</button>` : "";
      return `<div class="pb-line${s.kind === "unknown" ? " pb-unknown" : ""}" data-pb-seat="${esc(s.name)}" data-pb-kind="${esc(s.kind)}">${esc(s.name)}'s seat: ${what(s)}`
        + (s.talk ? ` <small>· talks as ${esc(s.talk)}</small>` : "") + back + `</div>`;
    }).join("") + (err ? `<div class="pb-line pb-unknown">${esc(err)}</div>` : "");
    if (html === sig) return;
    sig = html;
    if (!html) { if (strip) { strip.remove(); strip = null; } return; }
    if (!strip) { strip = document.createElement("div"); strip.className = "pb-strip"; strip.setAttribute("aria-live", "polite"); document.body.appendChild(strip); }
    strip.innerHTML = html;
    const b = strip.querySelector("[data-pb-back]");
    if (b) b.onclick = takeBack;
  }
  async function takeBack() {
    try {
      const r = await fetch("/api/seat/played-by", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": key() },
        body: JSON.stringify({ seat: seat(), kind: "person" }) });
      const d = await r.json().catch(() => ({}));
      err = r.ok ? "" : (d.error || "The table did not take that.");
    } catch { err = "The table did not answer."; }
    poll();
  }
  async function poll() {
    try {
      const r = await fetch("/api/played-by", { cache: "no-store" });
      if (!r.ok) return;                                // an older table has no such route: this file then shows nothing
      render((await r.json()).seats);
    } catch { /* the table is restarting: keep what is shown */ }
  }
  window.TablePlayedBy = { poll };
  poll(); setInterval(poll, 5000);
})();
