// 👍/🙄 on an AI's UNPROMPTED line (open mic v2). Ratings tune each personality's threshold, so the table
// teaches the AIs when talking is welcome. Seated people only (POST /api/openmic/rate with their seat key).
(() => {
  const me = new URLSearchParams(location.search).get("player");
  if (!me) return;
  let since = null;
  const bar = document.createElement("div");
  bar.style.cssText = "position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:70;display:none;gap:8px;align-items:center;" +
    "padding:10px 14px;border-radius:12px;background:#0d1d25;box-shadow:0 0 0 1px #d9b46a66,0 10px 30px #000a;color:#ece4cf;font:15px system-ui";
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(bar));
  async function rate(id, r) {
    const body = Object.assign(window.TableSeat ? TableSeat.body() : { by: me }, { event: id, rating: r });
    await fetch("/api/openmic/rate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
    bar.style.display = "none";
  }
  function ask(e) {
    bar.innerHTML = `<span>${String(e.seat).replace(/[<>&"]/g, "")} chimed in. Welcome?</span>`;
    for (const [r, label] of [["up", "👍"], ["roll", "🙄"]]) {
      const b = document.createElement("button"); b.textContent = label; b.style.cssText = "font-size:20px;padding:4px 10px;border-radius:8px;border:1px solid #d9b46a55;background:#10242d;color:inherit";
      b.onclick = () => rate(e.id, r); bar.appendChild(b);
    }
    bar.style.display = "flex"; clearTimeout(bar._t); bar._t = setTimeout(() => (bar.style.display = "none"), 45000);
  }
  async function poll() {
    try {
      if (since === null) since = (await (await fetch("/api/events?since=latest")).json()).last || 0;
      const d = await (await fetch("/api/events?since=" + since)).json();
      for (const e of d.events || []) { since = e.id; if (e.type === "openmic" && e.kind === "interject") ask(e); }
    } catch {}
  }
  setInterval(poll, 3000);
})();
