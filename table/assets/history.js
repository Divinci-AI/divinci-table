// 📜 Game log — the whole game's public history (casts, mills with every card, attacks, life, turns,
// graveyard moves, captain lines), from /api/history. Any page can include it:
//   <script src="/assets/history.js" defer></script>
// Floats bottom-right; filter by player, search ("Sol Ring"), fold into a badge. Newest at the top.
(() => {
  if (window.__history) return; window.__history = true;
  const esc = t => String(t ?? "").replace(/[<>&"']/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch {} return null; };
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:50;width:min(420px,calc(100vw - 24px));" +
    "font:13px/1.35 system-ui,-apple-system,sans-serif;color:#eee;background:#0e1016;border:1px solid #3a3f4a;" +
    "border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.4)";
  document.body.appendChild(box);
  const PAGE = !!window.HISTORY_PAGE;                  // /log: the log is the whole window
  let open = PAGE || store("history.open") === "1", tall = store("history.tall") === "1";
  let who = store("history.who") || "", query = "", next = 0, drawn = -1;
  const rows = [];                                     // [{seq, ts, who, kind, html, text}]
  const COL = { Claude: "#f0a868", Fusion: "#d8b45a", Michael: "#5fd3ff", Sam: "#b58cff" };
  const name = n => `<b style="color:${COL[n] || "#ddd"}">${esc(n)}</b>`;
  function describe(e) {
    const t = e.type;
    if (t === "say") {
      if (e.action === "filler") return null;
      const kind = e.action === "mill" ? "mill" : e.action === "cast" ? "cast" : /attack|damage/.test(e.action || "") ? "combat"
        : e.action === "begin" ? "turn" : e.action === "say" ? "talk" : "play";
      if (kind === "mill" && e.cards && e.cards.length)        // each card a chip the table ticks once it's moved
        return { who: e.speaker, kind, mill: { seq: e.seq, cards: e.cards }, html: "", text: `${e.speaker} milled ${e.cards.join(", ")}` };
      return { who: e.speaker, kind, html: `${name(e.speaker)}: ${esc(e.text)}` };
    }
    if (t === "life") return { who: e.player, kind: "life",
      html: `❤ ${name(e.player)} ${e.delta > 0 ? "+" : ""}${esc(e.delta)} → <b>${esc(e.life)}</b>${e.by && e.by !== e.player ? ` <span style="opacity:.6">(${esc(e.by)})</span>` : ""}` };
    if (t === "phase" && e.kind === "windows") return { who: "", kind: "table", html: `⏱ step timeouts ${e.on ? "on" : "off"}` };
    if (t === "phase" && (e.index === 0 || e.back)) return { who: e.player, kind: "turn",
      html: e.back ? `◂ back to ${name(e.player)} · ${esc(e.step)}` : `── ${name(e.player)}'s turn ──` };
    if (t === "declare" && e.kind === "attack") return { who: e.by, kind: "combat",
      html: `⚔ ${name(e.by)} attacks: ` + (e.attacks || []).map(a => `${esc(a.attacker)}${a.power != null ? ` (${esc(a.power)})` : ""} → ${name(a.target)}${a.trample ? " · trample" : ""}`).join(", ") };
    if (t === "chat") return { who: e.by, kind: "chat", html: `${e.talk ? "🗨" : "💬"} ${name(e.by)}: ${e.talk ? "<i>" + esc(e.text) + "</i>" : esc(e.text)}` +
      (e.photo ? `<br><a href="${esc(e.photo)}" target="_blank"><img src="${esc(e.photo)}" style="max-width:100%;max-height:200px;border-radius:8px;margin-top:4px"></a>` : "") };
    if (t === "captain") return { who: e.seat, kind: "captain", html: `⚓ <i>${esc(e.captain)}</i>: ${esc(e.text)}` };
    if (t === "todo" && e.kind === "added") return { who: "", kind: "table",
      html: (e.items || []).map(x => `🃏 ${x.for ? name(x.for) + ": " : ""}${esc(x.text)}`).join("<br>") };
    if (t === "attention" && e.kind === "turn") return null;
    if (t === "fair") return { who: "", kind: "table", html: `🎲 fairness: ${esc(e.kind)}` };
    if (t === "new-game") return { who: "", kind: "table", html: "── new game ──" };
    return null;
  }
  const KINDS = { chat: "chat", cast: "casts", mill: "mills", combat: "combat", life: "life", turn: "turns", play: "other plays",
    talk: "talk", captain: "captains", table: "table" };
  let hidden = new Set(JSON.parse(store("history.hidden") || '["talk"]'));
  let placed = new Set(), placedSig = "", seatNames = [];
  const urlPlayer = new URLSearchParams(location.search).get("player");
  const seatsForChat = () => seatNames.length ? seatNames : [urlPlayer || "someone"];
  const chatAs = () => urlPlayer || store("chat.as") || seatsForChat()[0];
  fetch("/api/phase").then(r => r.json()).then(p => {
    seatNames = (p.order || []).filter(n => !(p.ai || []).includes(n));          // the people at the table
    drawn = -1; render();
  }).catch(() => {});
  async function sayIt() {
    const i = box.querySelector("[data-chat]"), as = box.querySelector("[data-as]")?.value || chatAs();
    const text = (i?.value || "").trim(); if (!text) return;
    store("chat.as", as); i.value = ""; i.placeholder = "sent ✓";
    await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ by: as, text }) });
    setTimeout(poll, 400);
  }
  function rowHtml(r) {
    if (!r.mill) return r.html;
    const { seq, cards } = r.mill, done = cards.filter((_, i) => placed.has(`${seq}:${i}`)).length;
    return `🪦 ${name(r.who)} milled <b>${cards.length}</b> <span style="opacity:.6;font-size:11.5px">· ${done}/${cards.length} in the graveyard — tap a card once it's moved</span><br>` +
      cards.map((c, i) => { const on = placed.has(`${seq}:${i}`);
        return `<span data-placed="${seq}:${i}" title="${on ? "in the graveyard — tap to undo" : "tap once it's in the graveyard"}" style="display:inline-block;margin:3px 4px 0 0;padding:1px 8px;border-radius:999px;cursor:pointer;user-select:none;` +
          (on ? "background:#1f4d33;color:#9be3b5;border:1px solid #2e7a4f;text-decoration:line-through" : "background:#23262e;color:#cfd6e4;border:1px solid #3a3f4a") +
          `">${on ? "✓ " : ""}${esc(c)}</span>`; }).join("");
  }
  const phone = () => innerWidth < 640;                // a phone: open = full screen, filters folded away
  let showFilters = false;
  function place() {                                   // compact (bottom-right), tall (full-height right), or the page
    const s = box.style;
    if (PAGE) { Object.assign(s, { top: "0", bottom: "0", right: "0", left: "0", width: "auto", borderRadius: "0", border: "0" }); return; }
    if (open && phone()) { Object.assign(s, { top: "0", bottom: "0", right: "0", left: "0", width: "auto", borderRadius: "0", zIndex: "95" }); return; }
    Object.assign(s, { left: "auto", right: "12px", zIndex: "50" });
    if (open && tall) Object.assign(s, { top: "12px", bottom: "12px", width: "min(460px,calc(100vw - 24px))" });
    else Object.assign(s, { top: "auto", bottom: "12px" });
  }
  function render() {
    const list0 = box.querySelector("[data-list]"), keep = list0 ? list0.scrollTop : 0;   // keep the reader's place
    place();
    if (!open) {
      box.style.borderRadius = "999px"; box.style.width = "auto";
      box.innerHTML = `<div style="display:flex;align-items:center">
        <div data-act="chat" style="padding:7px 12px;cursor:pointer;font-weight:700;border-right:1px solid #3a3f4a" title="tell the table what you're doing (or ask)">💬 Chat</div>
        <div data-act="open" style="padding:7px 12px;cursor:pointer;font-weight:700" title="the game log">📜 ${rows.length}</div></div>`;
      return;
    }
    if (!PAGE) { box.style.borderRadius = "12px"; if (!tall) box.style.width = "min(420px,calc(100vw - 24px))"; }
    box.style.display = "flex"; box.style.flexDirection = "column";
    const players = [...new Set(rows.map(r => r.who).filter(Boolean))];
    const q = query.toLowerCase();
    const shown = rows.filter(r => (!who || r.who === who) && !hidden.has(r.kind) && (!q || r.text.toLowerCase().includes(q)));
    box.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 8px 6px 12px;font-weight:700">
        <span style="flex:1">📜 Game log <span style="opacity:.55;font-weight:400">${shown.length}/${rows.length}</span></span>
        <button data-act="filters" title="who and what to show" style="border:0;background:${showFilters ? "#3a3f4a" : "#2a2e37"};color:#eee;border-radius:7px;padding:2px 8px;cursor:pointer;font:inherit;font-size:12px">Filters ${showFilters ? "▴" : "▾"}</button>
        ${PAGE ? "" : phone() ? `<button data-act="close" style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:4px 12px;cursor:pointer;font:inherit">✕ Close</button>` : `<button data-act="tall" title="${tall ? "back to compact" : "full height"}" style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:2px 8px;cursor:pointer;font:inherit">${tall ? "⤡" : "⤢"}</button>
        <button data-act="pop" title="pop out into its own window (/log)" style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:2px 8px;cursor:pointer;font:inherit">↗</button>
        <button data-act="close" title="tuck away" style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:2px 9px;cursor:pointer;font:inherit">–</button>`}</div>
      <div style="display:${showFilters ? "flex" : "none"};flex-wrap:wrap;gap:5px;padding:0 12px 6px">
        ${["", ...players].map(p => `<button data-who="${esc(p)}" style="border:1px solid ${p === who ? "#888" : "#3a3f4a"};background:${p === who ? "#3a3f4a" : "transparent"};color:${COL[p] || "#ddd"};border-radius:7px;padding:1px 8px;cursor:pointer;font:inherit;font-size:12px">${p ? esc(p) : "everyone"}</button>`).join("")}
      </div>
      <div style="display:${showFilters ? "flex" : "none"};flex-wrap:wrap;gap:5px;padding:0 12px 6px">
        ${Object.entries(KINDS).map(([k, l]) => `<label style="font-size:11.5px;white-space:nowrap;opacity:${hidden.has(k) ? .45 : 1};cursor:pointer"><input type="checkbox" data-kind="${k}" ${hidden.has(k) ? "" : "checked"} style="vertical-align:-2px;width:14px;height:14px;margin:0 2px 0 0"> ${l}</label>`).join("")}
      </div>
      <div style="padding:0 12px 6px"><input data-act="q" value="${esc(query)}" placeholder="search: a card, a player…" style="width:100%;box-sizing:border-box;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:7px;padding:5px 8px;font:inherit"></div>
      <div style="display:flex;gap:5px;padding:0 12px 8px">
        <select data-as title="who's talking" style="background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:7px;padding:3px">
          ${seatsForChat().map(n => `<option ${n === chatAs() ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
        <input data-chat placeholder="tell the table a move — or start with ~ to just talk" style="flex:1;min-width:0;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:7px;padding:5px 8px;font:inherit">
        <button data-act="say" style="border:0;background:#2d5a8a;color:#fff;border-radius:7px;padding:3px 10px;cursor:pointer;font:inherit">send</button>
        <label title="send a photo of the table" style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:3px 9px;cursor:pointer">📷<input data-photo type="file" accept="image/*" capture="environment" hidden></label>
      </div>
      <div data-openq style="display:none;margin:0 12px 8px;padding:7px 10px;border-radius:9px;background:#3b2a0c;border:1px solid #f0a868;cursor:pointer;font-weight:600"></div>
      <div data-list style="${PAGE || tall ? "flex:1;min-height:0" : "max-height:min(52vh,460px)"};overflow:auto;padding:0 12px 10px">
        ${shown.length ? shown.slice().reverse().map(r => `<div style="padding:4px 0;border-top:1px solid #23262e"><span style="opacity:.45;font-size:11px">${new Date(r.ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span> ${rowHtml(r)}</div>`).join("")
          : `<div style="opacity:.6;padding:6px 0">Nothing matches.</div>`}
      </div>`;
    const list = box.querySelector("[data-list]"); if (list) list.scrollTop = keep;
    const oq = box.querySelector("[data-openq]");
    if (oq) fetch("/api/phase").then(r => r.json()).then(p => {
      if (p.open_questions) { oq.style.display = "block"; oq.textContent = `❓ ${p.open_questions} open question${p.open_questions > 1 ? "s" : ""} — NEXT is waiting. Tap to answer or resolve.`; }
      else oq.style.display = "none";
    }).catch(() => {});
    drawn = rows.length;
  }
  box.addEventListener("click", ev => {
    const a = ev.target.closest("[data-act]")?.dataset.act;
    if (a === "open" || a === "close") { open = a === "open"; store("history.open", open ? "1" : "0"); render(); return; }
    if (a === "chat") { open = true; store("history.open", "1"); render(); box.querySelector("[data-chat]")?.focus(); return; }
    if (ev.target.closest("[data-openq]")) { window.openQuestions?.(); return; }
    if (a === "say") { sayIt(); return; }
    if (a === "filters") { showFilters = !showFilters; render(); return; }
    if (a === "tall") { tall = !tall; store("history.tall", tall ? "1" : "0"); render(); return; }
    if (a === "pop") { window.open("/log", "game-log", "width=480,height=900"); open = false; store("history.open", "0"); render(); return; }
    const pc = ev.target.closest("[data-placed]");
    if (pc) {
      const key = pc.dataset.placed, on = !placed.has(key);
      on ? placed.add(key) : placed.delete(key); render();                 // instant here; shared via the server
      fetch("/api/placed", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key, on }) });
      return;
    }
    const w = ev.target.closest("[data-who]");
    if (w) { who = w.dataset.who; store("history.who", who); render(); return; }
    const k = ev.target.closest("[data-kind]");
    if (k) { k.checked ? hidden.delete(k.dataset.kind) : hidden.add(k.dataset.kind); store("history.hidden", JSON.stringify([...hidden])); render(); }
  });
  box.addEventListener("keydown", ev => { if (ev.target.dataset.chat !== undefined && ev.key === "Enter") sayIt(); });
  box.addEventListener("change", async ev => {
    if (ev.target.dataset.photo === undefined) return;
    const f = ev.target.files[0]; if (!f || !window.sendTablePhoto) return;
    const i = box.querySelector("[data-chat]"), as = box.querySelector("[data-as]")?.value || chatAs();
    i.placeholder = "sending the photo…";
    const r = await window.sendTablePhoto(f, as, i.value.trim());
    i.value = ""; i.placeholder = r.error ? "photo failed: " + r.error : "photo sent ✓"; setTimeout(poll, 400);
  });
  box.addEventListener("input", ev => {
    if (ev.target.dataset.act !== "q") return;
    query = ev.target.value; const pos = ev.target.selectionStart;
    render(); const qi = box.querySelector("[data-act=q]"); qi.focus(); qi.setSelectionRange(pos, pos);
  });
  async function poll() {
    try {
      let more = true;
      while (more) {
        const d = await (await fetch(`/api/history?from=${next}`)).json();
        for (const e of d.events) {
          const r = describe(e); if (!r) continue;
          rows.push({ ...r, seq: e.seq, ts: e.ts, text: r.text || r.html.replace(/<[^>]+>/g, "") });
        }
        more = d.next > next && d.events.length > 0 && d.next - next >= 2000;
        if (d.next < next) { rows.length = 0; }       // a new game: the log starts over
        next = d.next;
      }
      try { const p = await (await fetch("/api/placed")).text();
            if (p !== placedSig) { placedSig = p; placed = new Set(JSON.parse(p)); drawn = -1; } } catch {}
      const typing = box.contains(document.activeElement) && (document.activeElement.dataset.act === "q" || document.activeElement.dataset.chat !== undefined);
      if (rows.length !== drawn && !typing) render();   // only when something changed: no scroll jumps
    } catch {}
  }
  // ⚡ a spell cast in someone else's turn (an instant, a response) is easy to miss: a banner on every screen
  const toast = document.createElement("div");
  toast.style.cssText = "position:fixed;left:50%;top:18px;transform:translateX(-50%);z-index:80;max-width:min(560px,calc(100vw - 24px));" +
    "padding:12px 18px;border-radius:12px;background:#4a2d0c;border:2px solid #f0a868;color:#fff;font:600 15px/1.35 system-ui,-apple-system,sans-serif;" +
    "box-shadow:0 8px 30px rgba(0,0,0,.5);display:none;cursor:pointer;text-align:center";
  toast.title = "tap to dismiss"; toast.onclick = () => (toast.style.display = "none");
  document.body.appendChild(toast);
  let turnOf = null, alertFrom = null, tt = 0;
  async function watchCasts() {
    try {
      const r = await (await fetch(`/api/events?since=${alertFrom ?? "latest"}`)).json();
      if (alertFrom === null || r.restarted) { alertFrom = r.last; return; }
      for (const e of r.events) {
        if (e.type === "phase" && e.player) turnOf = e.player;
        if (e.type === "say" && e.action === "cast" && turnOf && e.speaker !== turnOf) {
          toast.innerHTML = `⚡ ${esc(e.speaker)} responds in ${esc(turnOf)}'s turn<br><span style="font-weight:400">${esc(e.text)}</span>`;
          toast.style.display = "block"; clearTimeout(tt); tt = setTimeout(() => (toast.style.display = "none"), 15000);
        }
      }
      alertFrom = r.last;
    } catch {}
  }
  fetch("/api/phase").then(r => r.json()).then(p => { turnOf = p.player; }).catch(() => {});
  setInterval(watchCasts, 1500);
  render(); poll(); setInterval(poll, 2500);
})();
