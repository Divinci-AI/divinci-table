// The director's page (and /pov): draws what /api/program (or /api/pov) says may be shown. It never holds a key: the data it gets
// has already been through the integrity rules on the server. Text is always set with textContent or esc().
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const q = new URLSearchParams(location.search);
  const POV = location.pathname === "/pov";
  const URL_ = POV ? "/api/pov?seat=" + encodeURIComponent(q.get("seat") || "") + (q.get("table") ? "&table=" + encodeURIComponent(q.get("table")) : "") : "/api/program";
  const ART = window.CARD_ART || {};
  let namesOnly = q.get("art") === "0";       // the names-only switch on the page; the server's names_only wins too
  let focusI = 0, lastHandKey = "", played = new Set(), fails = 0, reloaded = false;

  function card(c, cls) {
    if (!c.name) return `<div class="card fd ${cls || ""}${c.tapped ? " t" : ""}"><div class="n">face-down</div>${c.pt ? `<div class="pt">${esc(c.pt)}</div>` : ""}</div>`;
    const art = !namesOnly && ART[c.name];
    return `<div class="card ${cls || ""}${c.tapped ? " t" : ""}${art ? " hasart" : ""}" title="${esc(c.name)}">` +
      (art ? `<img src="/art/${esc(art)}.jpg" alt="${esc(c.name)}">` : "") +
      `<div class="n">${esc(c.name)}</div>${c.cost ? `<div class="c">${esc(String(c.cost).replace(/[{}]/g, ""))}</div>` : ""}` +
      `<div class="ty">${esc(c.type || "")}</div>${c.pt ? `<div class="pt">${esc(c.pt)}</div>` : ""}` +
      (c.counters ? `<div class="tag">+${esc(c.counters)}</div>` : c.token ? `<div class="tag">token</div>` : "") + `</div>`;
  }
  const isLand = p => /\bLand\b/.test(p.type || "");
  function seatBlock(s, small) {
    const perms = s.permanents || [], lands = perms.filter(isLand), rest = perms.filter(p => !isLand(p));
    const landCount = {};
    lands.forEach(l => { const k = (l.name || "Land") + (l.tapped ? "|t" : ""); landCount[k] = (landCount[k] || 0) + 1; });
    const backs = Math.min(10, s.hand || 0);
    return `<div class="seathead"><span class="nm">${esc(s.name)}</span><span class="kind ${s.kind === "ai" ? "ai" : ""}">${s.kind === "ai" ? "AI" : "person"}</span>` +
      `<span class="life">${esc(s.life == null ? "–" : s.life)}</span>` +
      (small ? `<span class="backs">${"<i></i>".repeat(backs)}</span>` : "") +
      `<span class="meta">${esc(s.hand == null ? "?" : s.hand)} in hand · ${esc(s.library == null ? "?" : s.library)} in library · ${esc(s.graveyard_count || 0)} in graveyard</span></div>` +
      `<div class="lands">${Object.keys(landCount).map(k => `<span class="land${k.endsWith("|t") ? " t" : ""}">${esc(k.split("|")[0])}${landCount[k] > 1 ? " ×" + landCount[k] : ""}</span>`).join("")}</div>` +
      `<div class="row">${rest.slice(0, small ? 12 : 11).map(p => card(p, small ? "sm" : "")).join("")}</div>`;
  }

  function draw(d) {
    namesOnly = d.names_only || q.get("art") === "0";
    const st = d.stage;
    $("blank").hidden = d.mode !== "blank";
    if (!st) { ["opp", "mine", "hand", "focus", "lower", "thinkbody", "logbody", "pip"].forEach(i => { $(i).innerHTML = ""; }); return; }
    $("tname").textContent = st.table + (st.over ? " · game over" : "");
    $("steps").innerHTML = (st.steps || []).map(x => `<span class="${x === st.step && !st.over ? "on" : ""}">${esc(x)}</span>`).join("");
    const m = st.match;
    $("score").textContent = m && m.wins ? Object.keys(m.wins).map(k => `${k} ${m.wins[k]}`).join(" – ") + (m.game ? `  ·  game ${m.game}` : "") : "";
    $("delay").textContent = st.delay || "";
    $("delay").className = /^live|LIVE/.test(st.delay || "") ? "live" : "";
    $("opp").innerHTML = (st.opponents || []).map(s => seatBlock(s, true)).join("");
    const me = st.me;
    $("mine").innerHTML = me ? seatBlock(me, false) : "";
    // the hand: shown only if the server released it
    const pr = me && me.private;
    const hand = $("hand");
    if (pr && pr.shown && pr.hand) {
      const key = pr.hand.map(c => c.name).join("|");
      if (key !== lastHandKey) { lastHandKey = key; focusI = 0; }
      hand.className = pr.hand.length > 7 ? "many" : "";
      hand.innerHTML = pr.hand.map((c, i) => card(c, i === focusI % Math.max(1, pr.hand.length) ? "f" : "")).join("") || `<span class="none">an empty hand</span>`;
      const f = pr.hand[focusI % Math.max(1, pr.hand.length)];
      $("focus").innerHTML = f ? `<b>${esc(f.name)}</b><i>${esc(f.type || "")}${f.cost ? " · " + esc(String(f.cost).replace(/[{}]/g, "")) : ""}${f.pt ? " · " + esc(f.pt) : ""}</i><p>${esc(f.text || "")}</p>` : "";
    } else {
      lastHandKey = "";
      const n = Math.min(10, (me && me.hand) || 0);
      hand.className = "";
      hand.innerHTML = `<div class="hidden"><span class="backs">${"<i></i>".repeat(n)}</span><span class="why">Hand not shown${pr && pr.reason ? ": " + esc(pr.reason) : ""}.</span></div>`;
      $("focus").innerHTML = `<b>${esc((me && me.hand) || 0)} cards</b><i>hidden</i>`;
    }
    // thinking: only what the seat itself said, or the table's own reason for a move it took. Never made up.
    const items = (pr && pr.shown ? pr.thinking : []) || [];
    const label = { thought: "the player's own words", note: "a note the player typed", move: "the move, and the table's reason" };
    $("thinkbody").innerHTML = items.length ? items.slice(-4).reverse().map((it, i) =>
      `<div class="th${i ? " old" : ""}"><span class="src">${esc(label[it.source] || it.source)}</span>${esc(it.text)}${it.why ? `<span class="why">why: ${esc(it.why)}</span>` : ""}</div>`).join("")
      : `<div class="none">${me && me.kind === "human" ? "Nothing: a person's thoughts are their own unless they type a note." : pr && pr.shown ? "No reasons yet." : "Not shown."}</div>`;
    $("logbody").innerHTML = (st.log || []).slice(-9).map(l => `<div><b>${esc(l.who)}</b> ${esc(l.text)}</div>`).join("");
    const lt = st.lower_third;
    $("lower").innerHTML = lt ? `<b>${esc(lt.name)}</b><span>${esc(lt.player)} · ${esc(lt.table)}${lt.game ? " · game " + esc(lt.game) : ""} · hand ${esc(lt.hand === "shown" ? "shown " + (lt.delay || "") : "hidden")}</span>` : "";
    $("pip").innerHTML = (d.pip || []).slice(0, 1).map(p =>
      `<h3>Also playing</h3><div class="pt"><b>${esc(p.name)}</b><span>${esc(p.over ? "game over" : (p.turn || "") + (p.step ? " · " + p.step : ""))}</span></div>` +
      p.seats.map(s => `<div class="ps"><b>${esc(s.name)}</b> <span class="l">♥ ${esc(s.life == null ? "–" : s.life)}</span> · ${esc(s.hand == null ? "?" : s.hand)} cards · ${esc(s.lands)} lands<small>${esc(s.board.join(", ") || "no creatures yet")}</small></div>`).join("")).join("")
      || `<h3>Also playing</h3><div class="none">One table tonight.</div>`;
    // recap
    const rc = st.recap, show = d.mode === "recap" && rc;
    $("recap").hidden = !show;
    if (show) {
      const tot = rc.totals || {};
      $("recap").innerHTML = `<h2>${esc(st.table)} · final</h2><h1>${esc(rc.winner || "?")} wins</h1><table>` +
        Object.keys(tot).map(k => `<tr><td>${esc(k)}</td><td>${esc(tot[k])} life</td><td>${rc.life_lost && rc.life_lost[k] != null ? esc(rc.life_lost[k]) + " lost" : ""}</td></tr>`).join("") +
        `</table><div class="ks">${rc.turns ? esc(rc.turns) + " turns. " : ""}${(rc.key_spells || []).length ? "Key spells: " + rc.key_spells.map(k => esc(k.name) + " (" + esc(k.player) + ")").join(", ") + "." : ""}</div>`;
    }
    // commentary: always labelled as AI
    const cap = d.caption, cs = (d.commentary || {}).state || "off", el = $("caption");
    if (cap) {
      el.className = "";
      el.innerHTML = `<span class="who">AI commentary · ${esc(cap.voice)} (${esc(cap.model)})</span><span class="tx">${esc(cap.text)}</span>`;
      if (cap.audio && !played.has(cap.id)) { played.add(cap.id); try { new Audio(cap.audio).play().catch(() => {}); } catch (e) {} }
    } else {
      el.className = "idle";
      el.innerHTML = cs === "off" ? "" : `<span class="who">AI commentary</span><span class="tx">${esc(/^paused/.test(cs) ? "commentary paused (budget)" : /^off/.test(cs) ? "commentary off" : /^waiting/.test(cs) ? "commentary will be back shortly" : "")}</span>`;
    }
  }

  async function tick() {
    try {
      const r = await fetch(URL_, { cache: "no-store", credentials: "same-origin" });
      if (r.status === 403 && !reloaded) { reloaded = true; location.reload(); return; }     // the session cookie was lost: get the page again, once
      if (!r.ok) throw new Error(String(r.status));
      fails = 0; $("offline").hidden = true;
      draw(await r.json());
    } catch (e) {
      if (++fails > 6) { $("offline").hidden = false; $("offline").textContent = "The director is not answering. (Switch to the Games scene.)"; }
    }
  }
  setInterval(tick, 500); tick();
  setInterval(() => { focusI++; }, 5000);
})();
