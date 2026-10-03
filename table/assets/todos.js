// "At the table" — what the physical cards have to do to match the game (posted by the AI brain:
// "Sam: move Hunted Horror to your graveyard"). Any page can include it:
//   <script src="/assets/todos.js" defer></script>
// It floats bottom-left, shows the open items, and anyone can tick one off for everybody.
(() => {
  if (window.__todos) return; window.__todos = true;
  const esc = t => String(t ?? "").replace(/[<>&"']/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:50;max-width:min(380px,calc(100vw - 24px));" +
    "font:13px/1.35 system-ui,-apple-system,sans-serif;color:#eee;background:rgba(14,16,22,.92);border:1px solid #3a3f4a;" +
    "border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.4);display:none";
  document.body.appendChild(box);
  let collapsed = false;
  try { collapsed = localStorage.getItem("todos.collapsed") === "1"; } catch {}
  const who = () => { try { return localStorage.getItem("me") || localStorage.getItem("name") || ""; } catch { return ""; } };
  let last = "";
  async function refresh() {
    try {
      const t = await (await fetch("/api/todos")).text();
      if (t === last) return; last = t;
      const d = JSON.parse(t);
      if (!d.open.length && !d.done.length) { box.style.display = "none"; return; }
      box.style.display = "block";
      if (collapsed) {                       // tucked away: a small badge with the count; tap it to bring the list back
        box.style.borderRadius = "999px";
        box.innerHTML = `<div data-act="fold" title="At the table: tap to open" style="padding:7px 12px;cursor:pointer;font-weight:700">🃏 ${d.open.length || "✓"}</div>`;
        return;
      }
      box.style.borderRadius = "12px";
      const head = `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 8px 8px 12px;font-weight:700">
        <span>🃏 At the table${d.open.length ? ` · ${d.open.length} open` : " · all done"}${d.open.some(x => x.kind === "question") ? ` · ❓ ${d.open.filter(x => x.kind === "question").length}` : ""}</span>
        <button data-act="fold" title="tuck it away (tap the 🃏 badge to bring it back)" style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:2px 9px;cursor:pointer;font:inherit">–</button></div>`;
      const COL = { Claude: "#f0a868", Fusion: "#d8b45a", Michael: "#5fd3ff", Sam: "#b58cff" };
      const ask = (x, done) => `<div style="padding:7px 12px;border-top:1px solid #23262e;${done ? "opacity:.5" : ""}">
        <div>❓ <b style="color:${COL[x.ask] || "#eee"}">${esc(x.ask)} asks${x.for ? " " + esc(x.for) : ""}:</b> ${esc(x.text)}</div>
        ${(x.answers || []).map(a => `<div style="margin:3px 0 0 18px;opacity:.9">💬 <b>${esc(a.by || "someone")}</b>: ${esc(a.text)}</div>`).join("")}
        ${done ? `<div style="margin:3px 0 0 18px;opacity:.7">✓ resolved${x.by ? " by " + esc(x.by) : ""}</div>` : `
        <div style="display:flex;flex-wrap:wrap;gap:5px;margin:6px 0 0 18px">
          ${(x.options || []).map(o => `<button data-answer="${x.id}" data-text="${esc(o)}" style="border:1px solid #3a3f4a;background:#23262e;color:#eee;border-radius:7px;padding:2px 8px;cursor:pointer;font:inherit;font-size:12px">${esc(o)}</button>`).join("")}
        </div>
        <div style="display:flex;gap:5px;margin:6px 0 0 18px">
          <input data-reply="${x.id}" placeholder="answer or comment…" style="flex:1;min-width:0;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:7px;padding:4px 7px;font:inherit">
          <button data-send="${x.id}" style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:3px 9px;cursor:pointer;font:inherit">send</button>
          <button data-resolve="${x.id}" title="mark it resolved" style="border:0;background:#1f4d33;color:#9be3b5;border-radius:7px;padding:3px 9px;cursor:pointer;font:inherit">resolve</button>
        </div>`}</div>`;
      const row = (x, done) => x.kind === "question" ? ask(x, done) : `<label style="display:flex;gap:8px;align-items:flex-start;padding:5px 12px;${done ? "opacity:.45;text-decoration:line-through" : ""}">
        <input type="checkbox" data-id="${x.id}" ${done ? "checked" : ""} style="margin-top:2px">
        <span>${x.for ? `<b>${esc(x.for)}:</b> ` : ""}${esc(x.text)}</span></label>`;
      box.innerHTML = head + `<div style="max-height:40vh;overflow:auto;padding-bottom:6px">${d.open.map(x => row(x, false)).join("")}${d.done.slice(-3).map(x => row(x, true)).join("")}</div>`;
    } catch {}
  }
  box.addEventListener("click", async ev => {
    if (ev.target.closest("[data-act=fold]")) {
      collapsed = !collapsed; try { localStorage.setItem("todos.collapsed", collapsed ? "1" : "0"); } catch {} last = ""; refresh(); return;
    }
    const answer = async (id, text, resolve = false) => {
      await fetch("/api/todo/answer", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, text, by: who(), resolve }) });
      last = ""; refresh();
    };
    const opt = ev.target.closest("[data-answer]");
    if (opt) return answer(+opt.dataset.answer, opt.dataset.text);
    const send = ev.target.closest("[data-send]");
    if (send) { const i = box.querySelector(`[data-reply="${send.dataset.send}"]`); if (i && i.value.trim()) answer(+send.dataset.send, i.value.trim()); return; }
    const res = ev.target.closest("[data-resolve]");
    if (res) {
      const i = box.querySelector(`[data-reply="${res.dataset.resolve}"]`);
      if (i && i.value.trim()) return answer(+res.dataset.resolve, i.value.trim(), true);
      await fetch("/api/todo/done", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: +res.dataset.resolve, by: who() }) });
      last = ""; refresh(); return;
    }
    const cb = ev.target.closest("input[data-id]"); if (!cb) return;
    await fetch("/api/todo/done", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: +cb.dataset.id, toggle: true, by: who() }) });
    last = ""; refresh();
  });
  box.addEventListener("keydown", ev => {               // Enter sends a reply
    const i = ev.target.closest("[data-reply]"); if (!i || ev.key !== "Enter" || !i.value.trim()) return;
    box.querySelector(`[data-send="${i.dataset.reply}"]`)?.click();
  });
  // don't redraw under someone typing a reply
  setInterval(() => { if (!(box.contains(document.activeElement) && document.activeElement.dataset.reply)) refresh(); }, 2000);
  refresh();
})();
