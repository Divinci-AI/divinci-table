// The end of the game, on every page that loads it (hand, me, board, table, 3d): a winner banner with the final life totals, and a short
// summary of what happened, built by the server from the PUBLIC log only (/api/game/summary). A rematch is the host's call: nothing here
// restarts anything. Everything shown goes through esc(): names and lines come from players and cards.
(function () {
  "use strict";
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = document.createElement("style");
  css.textContent = `
  .go-banner{position:fixed;left:50%;top:calc(var(--tb-h,0px) + 10px);transform:translateX(-50%);z-index:10000;max-width:min(680px,calc(100vw - 16px));
    max-height:calc(100vh - var(--tb-h,0px) - var(--hud-h,0px) - 24px);overflow:auto;width:max-content;padding:14px 18px;border-radius:16px;background:#1d1606;color:#fff6dc;border:2px solid #d9b46a;
    font:15px system-ui,-apple-system,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.6)}
  .go-banner h2{margin:0 0 6px;font-size:22px;color:#f4dc9b} .go-banner .go-t{opacity:.85;font-size:13px;margin-top:6px}
  .go-banner table{border-collapse:collapse;margin-top:6px} .go-banner td{padding:2px 10px 2px 0}
  .go-banner button{margin-top:8px;background:#5a4410;color:#fff;border:1px solid #d9b46a;border-radius:8px;padding:4px 10px;cursor:pointer}
  .go-pill{position:fixed;right:10px;bottom:calc(var(--hud-h,0px) + 10px);z-index:10000;background:#5a4410;color:#fff;border:1px solid #d9b46a;border-radius:12px;padding:6px 10px;
    font:600 13px system-ui,sans-serif;cursor:pointer}
  @media (max-width:700px){.go-banner{left:8px;right:8px;transform:none;width:auto;max-width:none}.go-banner button{padding:10px 16px;font-size:16px}
    .go-pill{bottom:calc(var(--hud-h,0px) + 10px)}}`;
  document.head.appendChild(css);
  let shownAt = null, hidden = false;
  function render(d) {
    const s = d.summary || {}, tot = d.totals || {}, who = d.team || d.winner;
    const rows = (d.order || Object.keys(tot)).map((n, i) => `<tr><td>${i === 0 ? "🏆" : i + 1 + "."}</td><td>${esc(n)}</td><td>${esc(tot[n])} life</td>
      <td class="go-t">${esc((s.life && s.life[n] || []).map(p => p.life).join(" → "))}</td></tr>`).join("");
    const fin = s.finishing_blow;
    const line = (k, v) => v ? `<div class="go-t"><b>${esc(k)}:</b> ${v}</div>` : "";
    const dealt = Object.entries(s.damage_dealt_on_their_turn || {}).map(([n, v]) => `${esc(n)} ${esc(v)}`).join(", ");
    const died = (s.died || []).map(x => esc(x.name)).join(", ");
    const key = (s.key_spells || []).map(x => `${esc(x.name)} (${esc(x.player)})`).join(", ");
    return `<h2>🏆 ${esc(who)} wins</h2>
      <table>${rows}</table>
      ${line("Turns", esc(s.turns))}
      ${line("Damage dealt (on their own turns)", dealt)}
      ${line("Creatures that died", died)}
      ${line("Key spells", key)}
      ${fin ? line("The finishing blow", `${esc(fin.player)} lost ${esc(fin.amount)}${fin.by ? " (" + esc(fin.by) + ")" : ""} on ${esc(fin.on_turn_of || "")}'s turn${fin.line ? ": “" + esc(fin.line) + "”" : ""}`) : ""}
      <div class="go-t">${esc(d.rematch || "")}</div>
      <button data-go-hide>Hide</button>`;
  }
  function show(d) {
    let b = document.querySelector(".go-banner"), pill = document.querySelector(".go-pill");
    if (!b) { b = document.createElement("div"); b.className = "go-banner"; b.setAttribute("role", "status"); document.body.appendChild(b); }
    if (!pill) { pill = document.createElement("div"); pill.className = "go-pill"; document.body.appendChild(pill); }
    if (shownAt !== d.at) { b.innerHTML = render(d); shownAt = d.at; hidden = false; }
    pill.textContent = "🏆 " + (d.team || d.winner || "") + " wins";
    b.style.display = hidden ? "none" : ""; pill.style.display = hidden ? "" : "none";
    window.__gameOver = d;
  }
  function clear() {
    for (const el of document.querySelectorAll(".go-banner,.go-pill")) el.remove();
    shownAt = null; window.__gameOver = null;
  }
  document.addEventListener("click", e => {
    if (e.target.closest && e.target.closest("[data-go-hide]")) { hidden = true; const b = document.querySelector(".go-banner"); if (b) b.style.display = "none"; const p = document.querySelector(".go-pill"); if (p) p.style.display = ""; }
    else if (e.target.closest && e.target.closest(".go-pill")) { hidden = false; const b = document.querySelector(".go-banner"); if (b) b.style.display = ""; e.target.closest(".go-pill").style.display = "none"; }
  });
  async function poll() {
    try { const r = await fetch("/api/game/summary", { cache: "no-store" }); const d = await r.json(); if (d.over && !d.match) show(d); else if (shownAt !== null) clear();   /* a match: assets/matchrecap.js shows the recap card instead */ } catch (e) { window.__gameOverError = String(e); }
    setTimeout(poll, 4000);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", poll); else poll();
})();
