// 🗂 My board: a person records their own battlefield from their phone (POST /api/my-board, their seat key).
// The stage, the XR table and the AI players read it from /api/board3d, the same as a referee-recorded board.
//   <div id="myBoard"></div>  +  <script src="/assets/myboard.js" defer></script>
(() => {
  const box = document.getElementById("myBoard");
  const me = new URLSearchParams(location.search).get("player");
  if (!box || !me) return;
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  let perms = [], grave = [], timer = 0, loaded = false;
  async function load() {
    try {
      const b = await (await fetch("/api/board3d")).json();
      const s = (b.seats || []).find(x => x.name === me);
      if (!s) { box.closest(".card").hidden = true; return; }          // not a human seat here
      perms = (s.permanents || []).map(p => ({ name: p.name, tapped: !!p.tapped, token: !!p.token,
        counters: p.counters || 0, pt: p.pt || "", note: p.note || "" }));
      grave = s.graveyard || [];
      loaded = true; draw();
    } catch { box.innerHTML = '<span class="warn">Couldn\'t load your board.</span>'; }
  }
  function draw(msg) {
    box.innerHTML = (perms.length ? perms.map((p, i) => `
      <div class="mb-row${p.tapped ? " tapped" : ""}">
        <button class="mb-tap" data-i="${i}" title="tap / untap">${p.tapped ? "↩" : "⤵"}</button>
        <span class="mb-name">${esc(p.name)}${p.token ? ' <span class="muted">token</span>' : ""}${p.pt ? ` <span class="muted">${esc(p.pt)}</span>` : ""}</span>
        <span class="mb-cnt"><button data-c="${i}" data-d="-1">−</button><b>${p.counters || 0}</b><button data-c="${i}" data-d="1">+</button></span>
        <button class="mb-x" data-x="${i}" title="remove">✕</button></div>`).join("")
      : '<div class="muted">Nothing recorded yet. Add what you have on the battlefield.</div>') + `
      <div class="mb-add"><input id="mbName" placeholder="Card name, e.g. Sol Ring" autocomplete="off">
        <label class="muted"><input id="mbTok" type="checkbox"> token</label><button id="mbAdd">Add</button></div>
      <div class="muted" id="mbMsg">${msg || ""}</div>`;
  }
  function save() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const body = (window.TableSeat ? TableSeat.body() : { by: me });
      body.permanents = perms; body.graveyard = grave;
      try {
        const r = await fetch("/api/my-board", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const d = await r.json().catch(() => ({}));
        const m = document.getElementById("mbMsg");
        if (m) m.innerHTML = r.ok ? '<span class="ok">Saved: everyone at the table sees it.</span>' : `<span class="warn">${esc(d.error || "Couldn't save")}</span>`;
      } catch { /* offline: the next change retries */ }
    }, 500);
  }
  box.addEventListener("click", ev => {
    const t = ev.target.closest("button"); if (!t || !loaded) return;
    if (t.id === "mbAdd") {
      const n = document.getElementById("mbName").value.trim().slice(0, 80);
      if (!n) return;
      perms.push({ name: n, tapped: false, token: document.getElementById("mbTok").checked, counters: 0 });
    } else if (t.dataset.i) { const p = perms[+t.dataset.i]; p.tapped = !p.tapped; }
    else if (t.dataset.c) { const p = perms[+t.dataset.c]; p.counters = (p.counters || 0) + (+t.dataset.d); }
    else if (t.dataset.x) { perms.splice(+t.dataset.x, 1); }
    else return;
    draw(); save();
  });
  box.addEventListener("keydown", ev => { if (ev.target.id === "mbName" && ev.key === "Enter") document.getElementById("mbAdd").click(); });
  const css = document.createElement("style");
  css.textContent = `.mb-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--line)}
    .mb-row.tapped .mb-name{opacity:.6;font-style:italic}.mb-name{flex:1;min-width:0;overflow-wrap:anywhere}
    .mb-row button{min-width:36px;padding:6px 8px;font-size:16px}.mb-cnt{display:flex;align-items:center;gap:4px}
    .mb-cnt b{min-width:22px;text-align:center}.mb-add{display:flex;gap:6px;align-items:center;margin-top:10px;flex-wrap:wrap}
    .mb-add input[type=text],#mbName{flex:1;min-width:150px;margin:0}`;
  document.head.appendChild(css);
  load();
})();
