// ⚔ Declare attack — from a phone or the board: pick your attackers (your creatures on the board, power
// filled in) and whom each one attacks. An AI being attacked gets its block decision, exactly as when the
// attack is said aloud; everyone sees it in the game log.
//   <script src="/assets/declare.js" defer></script>
(() => {
  if (window.__declare) return; window.__declare = true;
  const esc = t => String(t ?? "").replace(/[<>&"']/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch {} return null; };
  const btn = document.createElement("button");
  btn.textContent = "⚔ Declare attack";
  btn.style.cssText = "position:fixed;left:12px;top:12px;z-index:60;padding:7px 12px;border-radius:9px;border:1px solid #6b3a3a;" +
    "background:#3a1d1d;color:#ffd9d9;font:600 13px system-ui,-apple-system,sans-serif;cursor:pointer;display:none";
  const slot = () => document.getElementById("declareSlot");   // a page can give it a place in its layout
  const belowBar = () => { const tb = document.querySelector(".tb"); return Math.ceil(tb ? tb.getBoundingClientRect().bottom : 0) + 8 + "px"; };   // never under the tab bar: Sam could not find the button there
  const place = () => { if (!slot()) btn.style.top = belowBar(); };
  addEventListener("resize", place); setInterval(place, 1000);
  (slot() || document.body).appendChild(btn);
  const sheet = document.createElement("div");
  sheet.style.cssText = "position:fixed;inset:0;z-index:70;background:rgba(0,0,0,.55);display:none;align-items:flex-start;justify-content:center;" +
    "padding:60px 12px;font:13px/1.4 system-ui,-apple-system,sans-serif;color:#eee";
  document.body.appendChild(sheet);
  let board = null, phase = null;
  const isCreature = c => !c.face_down ? /Creature/.test(c.type || "") || (c.pt && !/Land/.test(c.type || "")) : true;
  const powerOf = c => { const m = /^(-?\d+)\//.exec(c.pt || ""); return m ? +m[1] : null; };
  async function refresh() {
    try {
      phase = await (await fetch("/api/phase")).json();
      const human = phase.player && !(phase.ai || []).includes(phase.player);
      const seatName = (window.TableSeat && window.TableSeat.name()) || "";
      const yours = seatName ? phase.player === seatName : human;  // a claimed device: only on its own turn
      const combat = ["beginning of combat", "declare attackers"].includes(phase.step);   // only when attacks happen
      btn.style.display = human && yours && combat ? "block" : "none";
    } catch {}
  }
  function open() {
    fetch("/api/board3d").then(r => r.json()).then(d => {
      board = d;
      const me = phase?.player || store("me") || "";
      const seats = d.seats.filter(s => s.life == null || s.life > 0);
      const mine = (d.seats.find(s => s.name === me)?.permanents || []).filter(isCreature);
      const others = seats.filter(s => s.name !== me).map(s => s.name);
      const last = store("declare.target") || others[0] || "";
      sheet.innerHTML = `<div style="width:min(460px,100%);background:#14161b;border:1px solid #3a3f4a;border-radius:14px;padding:14px 16px;box-shadow:0 10px 40px rgba(0,0,0,.5)">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <b style="font-size:16px">⚔ ${esc(me)} attacks</b>
          <button data-x style="border:0;background:#2a2e37;color:#eee;border-radius:7px;padding:3px 10px;cursor:pointer">✕</button></div>
        ${mine.length ? mine.map((c, i) => `
          <div style="display:flex;gap:8px;align-items:center;padding:6px 0;border-top:1px solid #23262e">
            <input type="checkbox" data-i="${i}" ${c.tapped ? "disabled" : ""}>
            <span style="flex:1">${c.face_down ? "Face-down 2/2" : esc(c.name)} <span style="opacity:.6">${esc(c.pt || "")}${c.tapped ? " · tapped" : ""}</span></span>
            <select data-t="${i}" style="background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:6px;padding:2px 4px">
              ${others.map(o => `<option ${o === last ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>
          </div>`).join("")
        : `<p style="opacity:.75">No creatures recorded on ${esc(me)}'s board. Add them (photo or chat) and try again, or name the attacker below.</p>`}
        <div style="display:flex;gap:6px;margin-top:8px;border-top:1px solid #23262e;padding-top:8px">
          <input data-other placeholder="another attacker (name)" style="flex:1;min-width:0;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:6px;padding:4px 6px">
          <input data-otherp placeholder="power" inputmode="numeric" style="width:58px;background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:6px;padding:4px 6px">
          <select data-ot style="background:#1b1e25;color:#eee;border:1px solid #3a3f4a;border-radius:6px">${others.map(o => `<option ${o === last ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>
        </div>
        <div data-msg style="margin-top:8px;min-height:1em;color:#ffb3b3"></div>
        <button data-go style="margin-top:6px;width:100%;padding:9px;border-radius:9px;border:0;background:#b8463f;color:#fff;font:700 14px system-ui;cursor:pointer">Declare</button>
      </div>`;
      sheet.style.display = "flex";
      sheet._mine = mine; sheet._me = me;
    });
  }
  btn.onclick = open;
  sheet.addEventListener("click", async ev => {
    if (ev.target === sheet || ev.target.closest("[data-x]")) { sheet.style.display = "none"; return; }
    if (!ev.target.closest("[data-go]")) return;
    const attacks = [];
    sheet.querySelectorAll("input[data-i]:checked").forEach(cb => {
      const c = sheet._mine[+cb.dataset.i], target = sheet.querySelector(`[data-t="${cb.dataset.i}"]`).value;
      attacks.push({ attacker: c.face_down ? "a face-down 2/2" : c.name, target, power: powerOf(c),
                     trample: /\btrample\b/i.test((c.text || "") + " " + (c.note || "")) });
    });
    const other = sheet.querySelector("[data-other]").value.trim();
    if (other) attacks.push({ attacker: other, target: sheet.querySelector("[data-ot]").value,
                              power: sheet.querySelector("[data-otherp]").value.trim() || null });
    if (!attacks.length) { sheet.querySelector("[data-msg]").textContent = "Tick an attacker (or name one)."; return; }
    store("declare.target", attacks[0].target);
    const r = await fetch("/api/declare/attack", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ by: sheet._me, attacks }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { sheet.querySelector("[data-msg]").textContent = d.error || "Couldn't declare that."; return; }
    sheet.style.display = "none";
  });
  refresh(); setInterval(refresh, 2000);
})();
