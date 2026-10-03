// 🎲 Who goes first? A high roll before the first turn, either with real dice at the table (each player
// enters their roll; someone rolls for an AI seat) or from ANU quantum randomness (published, checkable).
// Ties re-roll among the tied players; the winner goes first and play continues around the table.
//   <script src="/assets/highroll.js" defer></script>
(() => {
  if (window.__highroll) return; window.__highroll = true;
  const esc = t => String(t ?? "").replace(/[<>&"']/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  const me = () => new URLSearchParams(location.search).get("player") || (() => { try { return localStorage.getItem("chat.as") || ""; } catch { return ""; } })();
  const btn = document.createElement("button");
  btn.textContent = "🎲 Who goes first?";
  btn.style.cssText = "position:fixed;left:50%;transform:translateX(-50%);top:12px;z-index:61;padding:8px 14px;border-radius:10px;" +
    "border:1px solid #4a5a7a;background:#1d2840;color:#dfe8ff;font:600 14px system-ui,-apple-system,sans-serif;cursor:pointer;display:none";
  document.body.appendChild(btn);
  const sheet = document.createElement("div");
  sheet.style.cssText = "position:fixed;inset:0;z-index:72;background:rgba(0,0,0,.55);display:none;align-items:flex-start;justify-content:center;" +
    "padding:60px 12px;font:14px/1.45 system-ui,-apple-system,sans-serif;color:#eee";
  document.body.appendChild(sheet);
  const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(async r => ({ ok: r.ok, d: await r.json().catch(() => ({})) }));
  let started = false, h = { mode: null }, msg = "";
  function rollsTable() {
    const rounds = Object.keys(h.rolls || {}).sort((a, b) => a - b);
    if (!rounds.length) return "";
    return rounds.map(r => `<div style="margin-top:6px"><b>${rounds.length > 1 ? `Round ${r}${r > 1 ? " (tie-break)" : ""}` : "Rolls"}</b>: ` +
      Object.entries(h.rolls[r]).map(([n, v]) => `${esc(n)} <b>${v}</b>${h.entered_by?.[r]?.[n] && h.entered_by[r][n] !== n ? ` <span style="opacity:.6">(rolled by ${esc(h.entered_by[r][n])})</span>` : ""}`).join(" · ") + "</div>").join("");
  }
  function draw() {
    const physicalWaiting = h.mode === "physical" && !h.winner;
    sheet.innerHTML = `<div style="width:min(480px,100%);background:#14161b;border:1px solid #3a3f4a;border-radius:14px;padding:14px 16px;box-shadow:0 10px 40px rgba(0,0,0,.55)">
      <div style="display:flex;justify-content:space-between;align-items:center"><b style="font-size:16px">🎲 Who goes first?</b>
        <button data-x style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:3px 10px;cursor:pointer">✕</button></div>
      <div style="opacity:.7;font-size:12.5px;margin:2px 0 8px">Highest roll goes first; play continues around the table. Ties roll again.</div>
      ${!h.mode || h.winner ? `<div style="display:flex;gap:8px;flex-wrap:wrap">
          <button data-mode="physical" style="flex:1;padding:9px;border-radius:9px;border:0;background:#2d5a8a;color:#fff;font:600 14px system-ui;cursor:pointer">🎲 Real dice (d20)</button>
          <button data-mode="quantum" style="flex:1;padding:9px;border-radius:9px;border:0;background:#4b2d8a;color:#fff;font:600 14px system-ui;cursor:pointer">⚛️ Quantum (ANU)</button>
        </div>` : ""}
      ${physicalWaiting ? `<div style="margin-top:8px">Round ${h.round}: roll a d${h.sides} and enter it. ${(h.contenders || []).filter(n => (h.ai || []).includes(n)).length ? "Someone at the table rolls for the AI seats." : ""}</div>
        ${(h.waiting_on || []).map(n => `<div style="display:flex;gap:6px;align-items:center;margin-top:6px">
          <span style="flex:1">${esc(n)}</span>
          <input data-roll="${esc(n)}" inputmode="numeric" placeholder="1-${h.sides}" style="width:70px;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:7px;padding:5px 7px;font:inherit">
          <button data-enter="${esc(n)}" style="border:0;background:#2d5a8a;color:#fff;border-radius:7px;padding:5px 10px;cursor:pointer">enter</button></div>`).join("")}` : ""}
      ${rollsTable()}
      ${h.mode === "quantum" ? `<div style="margin-top:8px;font-size:12px;opacity:.75">Source: ${esc(h.source)}<br>Entropy: <code style="word-break:break-all">${esc(h.entropy)}</code><br>Each roll = ${esc(h.formula)} — anyone can recompute it.</div>` : ""}
      ${h.winner ? `<div style="margin-top:10px;padding:8px;border-radius:9px;background:#1f4d33;color:#c9f2d7"><b>${esc(h.winner)} goes first.</b> Turn order: ${(h.order || []).map(esc).join(" → ")}</div>` : ""}
      <div style="margin-top:8px;color:#ffb3b3;min-height:1em">${esc(msg)}</div></div>`;
  }
  async function refresh() {
    try {
      const p = await (await fetch("/api/phase")).json();
      started = !!p.player;
      h = await (await fetch("/api/highroll")).json();
      h.ai = p.ai || [];
      btn.style.display = started ? "none" : "block";
      if (started && sheet.style.display !== "none") sheet.style.display = "none";
      if (sheet.style.display === "flex" && !sheet.contains(document.activeElement)) draw();
    } catch {}
  }
  btn.onclick = () => { msg = ""; sheet.style.display = "flex"; draw(); };
  sheet.addEventListener("click", async ev => {
    if (ev.target === sheet || ev.target.closest("[data-x]")) { sheet.style.display = "none"; return; }
    const m = ev.target.closest("[data-mode]");
    if (m) {
      msg = m.dataset.mode === "quantum" ? "Asking ANU for quantum randomness (can take up to a minute)…" : ""; draw();
      const r = await post("/api/highroll/start", { mode: m.dataset.mode, sides: 20, by: me() });
      msg = r.ok ? "" : r.d.error || "couldn't start the roll"; h = { ...r.d, ai: h.ai }; return draw();
    }
    const e = ev.target.closest("[data-enter]");
    if (e) {
      const v = sheet.querySelector(`[data-roll="${CSS.escape(e.dataset.enter)}"]`)?.value;
      const r = await post("/api/highroll/roll", { player: e.dataset.enter, value: v, by: me() });
      msg = r.ok ? "" : r.d.error || "couldn't enter that"; if (r.ok) h = { ...r.d, ai: h.ai }; draw();
    }
  });
  sheet.addEventListener("keydown", ev => { const i = ev.target.closest("[data-roll]"); if (i && ev.key === "Enter") sheet.querySelector(`[data-enter="${CSS.escape(i.dataset.roll)}"]`)?.click(); });
  refresh(); setInterval(refresh, 2000);
})();
