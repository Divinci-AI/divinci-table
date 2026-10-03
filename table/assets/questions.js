// ❓ Open questions as a panel: who asked, the question, quick answers, a reply box, Resolve.
// Any page can include it:  <script src="/assets/questions.js" defer></script>
// then call window.openQuestions() (e.g. from "❓ 1 open question" on the stage). NEXT waits until
// every open question is answered and resolved, so this is where the table un-sticks the game.
(() => {
  if (window.openQuestions) return;
  const esc = t => String(t ?? "").replace(/[<>&"']/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  const COL = { Claude: "#f0a868", Fusion: "#d8b45a", Michael: "#5fd3ff", Sam: "#b58cff" };
  const me = () => new URLSearchParams(location.search).get("player") || (() => { try { return localStorage.getItem("chat.as") || ""; } catch { return ""; } })();
  const wrap = document.createElement("div");
  wrap.style.cssText = "position:fixed;inset:0;z-index:90;background:rgba(0,0,0,.55);display:none;align-items:flex-start;justify-content:center;" +
    "padding:70px 12px;font:14px/1.45 system-ui,-apple-system,sans-serif;color:#eee";
  document.body.appendChild(wrap);
  async function draw() {
    let d; try { d = await (await fetch("/api/todos")).json(); } catch { return; }
    const qs = d.open.filter(x => x.kind === "question");
    if (!qs.length) { wrap.innerHTML = panel(`<p style="opacity:.8">No open questions — NEXT is free. ✓</p>`); return; }
    wrap.innerHTML = panel(qs.map(x => `
      <div style="padding:10px 0;border-top:1px solid #2a2e37">
        <div>❓ <b style="color:${COL[x.ask] || "#eee"}">${esc(x.ask)} asks${x.for ? " " + esc(x.for) : ""}:</b> ${esc(x.text)}</div>
        ${(x.answers || []).map(a => `<div style="margin:4px 0 0 20px;opacity:.9">💬 <b>${esc(a.by || "someone")}</b>: ${esc(a.text)}</div>`).join("")}
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0 20px">
          ${(x.options || []).map(o => `<button data-ans="${x.id}" data-text="${esc(o)}" style="border:1px solid #3a3f4a;background:#23262e;color:#eee;border-radius:8px;padding:5px 10px;cursor:pointer;font:inherit;font-size:13px">${esc(o)}</button>`).join("")}
        </div>
        <div style="display:flex;gap:6px;margin:8px 0 0 20px">
          <input data-reply="${x.id}" placeholder="answer or comment…" style="flex:1;min-width:0;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:8px;padding:6px 8px;font:inherit">
          <button data-send="${x.id}" style="border:0;background:#2d5a8a;color:#fff;border-radius:8px;padding:5px 12px;cursor:pointer;font:inherit">send</button>
        </div>
        <div style="display:flex;gap:6px;margin:8px 0 0 20px">
          <button data-resolve="${x.id}" style="flex:1;border:0;background:#1f4d33;color:#9be3b5;border-radius:8px;padding:7px;cursor:pointer;font:600 13px system-ui">✓ Resolve${x.options?.length ? " (with the answer above, or as is)" : ""}</button>
        </div>
      </div>`).join(""));
  }
  const panel = inner => `<div style="width:min(520px,100%);background:#14161b;border:1px solid #3a3f4a;border-radius:14px;padding:14px 16px;box-shadow:0 10px 40px rgba(0,0,0,.55)">
      <div style="display:flex;justify-content:space-between;align-items:center"><b style="font-size:16px">❓ Open questions</b>
        <button data-close style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:3px 10px;cursor:pointer">✕</button></div>
      <div style="opacity:.65;font-size:12.5px;margin:2px 0 4px">NEXT waits until these are answered and resolved.</div>${inner}</div>`;
  const answer = (id, text, resolve) => fetch("/api/todo/answer", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, text, by: me(), resolve }) });
  let picked = {};
  wrap.addEventListener("click", async ev => {
    if (ev.target === wrap || ev.target.closest("[data-close]")) { wrap.style.display = "none"; return; }
    const a = ev.target.closest("[data-ans]");
    if (a) { picked[a.dataset.ans] = a.dataset.text; await answer(+a.dataset.ans, a.dataset.text, false); return draw(); }
    const s = ev.target.closest("[data-send]");
    if (s) { const i = wrap.querySelector(`[data-reply="${s.dataset.send}"]`); if (i?.value.trim()) { await answer(+s.dataset.send, i.value.trim(), false); draw(); } return; }
    const r = ev.target.closest("[data-resolve]");
    if (r) {
      const id = +r.dataset.resolve, i = wrap.querySelector(`[data-reply="${id}"]`);
      if (i?.value.trim()) await answer(id, i.value.trim(), true);
      else await fetch("/api/todo/done", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, by: me() }) });
      draw(); return;
    }
  });
  wrap.addEventListener("keydown", ev => { const i = ev.target.closest("[data-reply]"); if (i && ev.key === "Enter") wrap.querySelector(`[data-send="${i.dataset.reply}"]`)?.click(); });
  window.openQuestions = () => { wrap.style.display = "flex"; draw(); };
})();
