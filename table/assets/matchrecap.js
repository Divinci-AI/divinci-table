// A match (best two out of three) on one table: the recap card between games, on every page that loads this (hand, me, board, table, 3d,
// and /watch, the stream's view). It reads GET /api/match (public, no key) and nothing else for what it shows: the winner, the final
// life totals, the number of turns, the match score, a line or two from the server's summary of the PUBLIC log. Never a hand or a library.
//
//   * It never blocks anything: no backdrop, no focus trap. The next game starts in the background on a countdown; the card can stay up over
//     the new game's opening hand (it shrinks to two lines so Keep / Mulligan stay in view) until it is closed, and closes by itself later.
//   * Close it with ✕, Escape, or a tap anywhere outside it. (While it is asking YOU to choose play or draw, a tap outside does not close it:
//     a stray tap must not throw the question away. ✕ and Escape still do, and the pill at the bottom brings it back.)
//   * A seated player gets "Start now" and "Wait"; the loser of the last game gets "Play first" / "Draw first" (no answer: play first).
//   * On /watch there are no buttons and it always closes by itself (nobody is there to press ✕ on a stream).
// A table that plays single games answers {"on": false}: this file then does nothing, and assets/gameover.js shows its banner as before.
(function () {
  "use strict";
  if (window.__matchRecapLoaded) return; window.__matchRecapLoaded = true;
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const WATCH = location.pathname === "/watch";
  const seat = () => (!WATCH && window.TableSeat && TableSeat.name && TableSeat.name()) || "";
  const key = () => (!WATCH && window.TableSeat && TableSeat.body && TableSeat.body().key) || "";
  const css = document.createElement("style");
  css.textContent = `
  .mr-card{position:fixed;right:12px;top:calc(var(--tb-h,0px) + 52px);z-index:10001;width:min(400px,calc(100vw - 16px));max-height:calc(100vh - var(--tb-h,0px) - var(--hud-h,0px) - 70px);
    overflow:auto;padding:12px 14px 12px;border-radius:14px;background:#1d1606;color:#fff6dc;border:2px solid #d9b46a;font:14.5px/1.4 system-ui,-apple-system,sans-serif;
    box-shadow:0 8px 30px rgba(0,0,0,.6)}
  .mr-card h2{margin:0 30px 4px 0;font-size:19px;color:#f4dc9b} .mr-card .mr-score{font-weight:700;font-size:16px;margin:2px 0 6px}
  .mr-card .mr-t{opacity:.88;font-size:13px;margin-top:4px} .mr-card table{border-collapse:collapse;margin:2px 0} .mr-card td{padding:1px 12px 1px 0}
  .mr-card .mr-x{position:absolute;right:6px;top:6px;width:34px;height:34px;border-radius:9px;border:1px solid #d9b46a;background:#3a2c0a;color:#fff;font-size:17px;cursor:pointer}
  .mr-card .mr-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
  .mr-card button.mr-b{background:#5a4410;color:#fff;border:1px solid #d9b46a;border-radius:9px;padding:8px 12px;font:600 14px system-ui,sans-serif;cursor:pointer;min-height:40px}
  .mr-card button.mr-b.mr-go{background:#1f5a33;border-color:#7fd39a} .mr-card button.mr-b[disabled]{opacity:.5;cursor:default}
  .mr-card .mr-next{margin-top:8px;padding-top:7px;border-top:1px solid #6b5520} .mr-card .mr-err{color:#ffb3b3;font-size:13px;min-height:1em;margin-top:4px}
  .mr-card.mr-small{padding:8px 12px} .mr-card.mr-small h2{font-size:15px;margin-bottom:0} .mr-card.mr-small .mr-score{font-size:14px;margin:0}
  .mr-card.mr-small .mr-more{display:none}
  .mr-pill{position:fixed;right:10px;bottom:calc(var(--hud-h,0px) + 46px);z-index:10001;background:#5a4410;color:#fff;border:1px solid #d9b46a;border-radius:12px;padding:6px 10px;
    font:600 13px system-ui,sans-serif;cursor:pointer;max-width:calc(100vw - 20px)}
  @media (max-width:700px){.mr-card{left:8px;right:8px;width:auto;top:calc(var(--tb-h,0px) + 8px)}.mr-card button.mr-b{padding:10px 14px;font-size:15px;min-height:44px}}`;
  document.head.appendChild(css);

  let D = null, card = null, pill = null, shownId = null, shownAt = 0, hidden = false, expanded = false, lastGameId = null, busy = false, err = "", sig = "", liveAt = 0;
  const store = { get: k => { try { return sessionStorage.getItem(k) || ""; } catch { return ""; } }, set: (k, v) => { try { sessionStorage.setItem(k, v); } catch {} } };
  const recapId = g => `${g.game_id || ""}#${g.n}`;
  const last = d => (d.games || [])[(d.games || []).length - 1] || null;
  const mineToChoose = d => { const nx = d.next; return !!(nx && !nx.choice && seat() && nx.chooser === seat()); };
  const mmss = s => { s = Math.max(0, Math.ceil(s)); return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : `${s} s`; };

  function nextHtml(d) {
    const nx = d.next, me = seat(), isPlayer = !!me && (d.seats || []).includes(me) && !!key();
    if (d.over) return `<div class="mr-next"><b>🏆 ${esc(d.score)}.</b><div class="mr-t">${(d.games || []).map(g => `Game ${esc(g.n)}: ${g.draw ? "a draw" : esc(g.winner)}${g.turns ? ` (${esc(g.turns)} turn${g.turns === 1 ? "" : "s"})` : ""}`).join(" · ")}</div><div class="mr-t">The match is over: no further game starts.</div></div>`;
    if (!nx) return d.current ? `<div class="mr-next mr-more"><div class="mr-t">Game ${esc(d.current.n)} is under way: ${esc(d.current.first)} plays first${d.current.chooser && d.current.choice ? ` (${esc(d.current.chooser)} chose to ${esc(d.current.choice)} first)` : ""}. This closes by itself.</div></div>` : "";
    const holding = (nx.holds || []).length, iHold = (nx.holds || []).includes(me);
    const first = nx.why === "replay" ? `Played again: ${esc(nx.plays_first)} plays first, as before`
      : nx.choice ? `${esc(nx.plays_first)} plays first (${esc(nx.chooser)} chose to ${esc(nx.choice)} first)`
      : `${esc(nx.chooser)} ${nx.why === "drawn" ? "chose for the drawn game and" : "lost and"} chooses who plays first (no roll)`;
    const when = holding ? `Game ${esc(nx.n)} is waiting for ${esc((nx.holds || []).join(", "))} (at most ${esc(mmss(nx.seconds_left))} more)` : `Game ${esc(nx.n)} starts in <b data-mr-secs>${esc(mmss(nx.seconds_left))}</b>, here, by itself`;
    const choose = mineToChoose(d) ? `<div class="mr-row" data-mr-choice><button class="mr-b mr-go" data-mr="play">Play first</button><button class="mr-b" data-mr="draw">Draw first</button></div>
        <div class="mr-t">You lost the last game, so the choice is yours. No answer: you play first.</div>` : "";
    const ctl = isPlayer ? `<div class="mr-row"><button class="mr-b" data-mr="now">Start now</button><button class="mr-b" data-mr="wait" ${iHold ? "disabled" : ""}>${iHold ? "Waiting…" : "Wait"}</button></div>` : "";
    return `<div class="mr-next"><div>${when}.</div><div class="mr-t">${first}.</div>${choose}${ctl}<div class="mr-err" data-mr-err>${esc(err)}</div></div>`;
  }
  function render(d, g) {
    const tot = g.totals || {};
    const rows = (d.seats || []).map(n => `<tr><td>${g.winner === n ? "🏆" : ""}</td><td>${esc(n)}</td><td>${esc(tot[n] ?? "?")} life</td></tr>`).join("");
    const title = g.draw ? `Game ${esc(g.n)} is a draw` : `🏆 ${esc(g.winner)} wins game ${esc(g.n)}`;
    return `<button class="mr-x" data-mr="close" aria-label="Close the recap" title="Close (Esc)">✕</button>
      <h2>${title}</h2><div class="mr-score" data-mr-score>${esc(d.score)}</div>
      <div class="mr-more"><table>${rows}</table>
        <div class="mr-t">${esc(g.turns)} turn${g.turns === 1 ? "" : "s"}${g.first ? ` · ${esc(g.first)} played first` : ""}</div>
        ${(g.lines || []).map(l => `<div class="mr-t">${esc(l)}</div>`).join("")}</div>
      ${nextHtml(d)}`;
  }
  function drop() { if (card) { card.remove(); card = null; } if (pill) { pill.remove(); pill = null; } shownId = null; sig = ""; publish(); }
  function publish() { window.__matchRecap = { data: D, id: shownId, visible: !!card && card.style.display !== "none", small: !!card && card.classList.contains("mr-small"), watch: WATCH }; }
  function hide(forGood) { hidden = true; if (forGood && shownId) store.set("mr.closed", shownId); draw(); }
  function draw() {
    const d = D; if (!d || !d.on) return drop();
    const g = last(d); if (!g) return drop();
    const id = recapId(g), now = d.now || Date.now() / 1000, age = now - (g.ended || now);
    const live = !!d.current && !d.next && !d.over;                       // the next game is being played
    if (id !== shownId) { shownId = id; shownAt = Date.now(); hidden = store.get("mr.closed") === id; expanded = false; err = ""; sig = ""; }
    // closes by itself: on a seat's page a while after the next game began; on /watch always (nobody can press ✕ on a stream)
    if (live && !liveAt) { liveAt = Date.now(); expanded = false; }       // the next game has just begun: shrink, out of the opening hand's way
    if (!live) liveAt = 0;
    const up = (Date.now() - shownAt) / 1000, secs = d.recap_secs || 90;
    const expired = WATCH ? up > (d.watch_recap_secs || 20) * (d.over ? 3 : 1) || age > 900
      : live && ((Date.now() - liveAt) / 1000 > secs || age > secs + 300 || store.get("mr.expired") === id);   // (not again after a reload, nor on a page opened long after)
    if (expired) { if (!WATCH) store.set("mr.expired", id); if (card) { card.remove(); card = null; } if (pill) { pill.remove(); pill = null; } sig = ""; return publish(); }
    if (!card) { card = document.createElement("div"); card.className = "mr-card"; card.setAttribute("role", "status"); card.setAttribute("aria-live", "polite"); document.body.appendChild(card); }
    if (!pill) { pill = document.createElement("div"); pill.className = "mr-pill"; pill.setAttribute("role", "button"); document.body.appendChild(pill); }
    const s2 = JSON.stringify([id, d.score, d.over, d.next && [d.next.choice, d.next.holds, d.next.chooser, Math.ceil(d.next.seconds_left)], d.current && d.current.n, seat(), err, live]);
    if (s2 !== sig) { sig = s2; card.innerHTML = render(d, g); }
    card.classList.toggle("mr-small", live && !expanded);
    pill.textContent = (mineToChoose(d) ? "❓ Play or draw? · " : "🏆 ") + d.score;
    card.style.display = hidden ? "none" : ""; pill.style.display = hidden && !WATCH ? "" : "none";
    publish();
  }
  async function send(action) {
    if (busy) return; busy = true; err = "";
    try {
      const r = await fetch("/api/match/next", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": key() }, body: JSON.stringify({ seat: seat(), action }) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) err = d.error || "the table did not take that"; else if (d.on) D = d;
    } catch (e) { err = "the table did not answer; try again"; }
    busy = false; sig = ""; draw();
  }
  document.addEventListener("click", e => {
    const t = e.target, b = t.closest && t.closest("[data-mr]");
    if (b && card && card.contains(b)) { const a = b.dataset.mr; if (a === "close") hide(true); else send(a); return; }
    if (pill && t.closest && t.closest(".mr-pill")) { hidden = false; expanded = true; store.set("mr.closed", ""); draw(); return; }
    if (card && card.contains(t)) { if (card.classList.contains("mr-small")) { expanded = true; draw(); } return; }
    if (card && card.style.display !== "none" && D && !mineToChoose(D)) hide(true);      // a tap outside closes it (and still does whatever it tapped)
  }, true);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && card && card.style.display !== "none") hide(true); });
  async function poll() {
    let wait = 2000;
    try {
      const r = await fetch("/api/match", { cache: "no-store" }); const d = await r.json();
      if (!d.on) { D = d; drop(); wait = 30000; }                         // a single-game table: nothing to do (asked again rarely, in case the table is replaced)
      else {
        if (lastGameId !== null && d.game_id && d.game_id !== lastGameId) { try { window.dispatchEvent(new CustomEvent("table-new-game", { detail: { game_id: d.game_id, game: d.game } })); } catch (e) {} }
        lastGameId = d.game_id || lastGameId; D = d; draw();
        if (d.next) wait = 1000;
      }
    } catch (e) { window.__matchRecapError = String(e); }
    setTimeout(poll, wait);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", poll); else poll();
})();
