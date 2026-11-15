// The story in the headset (docs/DND-3D-GOAL.md D6). In a headset you can't see the desktop's scene log, so what the DM
// and the table just said floats on a parchment beyond the map, always facing you: the narration as it streams in
// (sentence by sentence, with who is speaking), the last thing a player did, the last roll, whose turn it is. With
// Voice on, the headset itself speaks it, as the desktop page does: each device its own voices, no audio over the network.
//
//   const story = createStory(THREE, { me: () => seatName });   scene.add(story.mesh)
//   story.push(event)      every event from /api/events, in order (backfill with { quiet: true })
//   story.setState(S)      the public state (scene title, initiative)
//   story.voice.on / story.voice.toggle()
export function createStory(THREE, opts = {}) {
  const me = opts.me || (() => "");
  const W = 1024, H = 600;
  const c = document.createElement("canvas"); c.width = W; c.height = H;
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(0.9, 0.9 * H / W),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false, side: THREE.DoubleSide }));
  mesh.renderOrder = 998; mesh.raycast = () => {};
  const st = { parts: [], turn: null, said: "", roll: "", scene: "", footer: "", dirty: true };
  const spokenTurns = new Set();

  // ── voice: the same choices as the desktop page (dnd.html), kept under the same setting ───────────────────────
  const store = { get() { try { return localStorage.getItem("dnd-voice"); } catch { return null; } },
                  set(v) { try { localStorage.setItem("dnd-voice", v); } catch {} } };
  const voice = {
    get on() { return store.get() === "on"; },
    toggle() {
      store.set(this.on ? "off" : "on");
      if (window.speechSynthesis) { if (this.on) speechSynthesis.speak(new SpeechSynthesisUtterance("")); else speechSynthesis.cancel(); }
      return this.on;
    },
    log: [],
  };
  let pool = [];
  if (window.speechSynthesis) speechSynthesis.onvoiceschanged = () => { pool = []; };
  const hash = s => [...String(s)].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 7);
  function voiceFor(who) {
    if (!pool.length && window.speechSynthesis) {
      const all = speechSynthesis.getVoices(), en = all.filter(v => /^en/i.test(v.lang));
      pool = (en.length ? en : all).filter(v => !/(Bad News|Bells|Bubbles|Cellos|Jester|Organ|Whisper|Zarvox|Trinoids|Boing|Albert|Wobble|Good News|Superstar|Bahh|Junior)/.test(v.name));
    }
    const narr = pool.find(v => /Daniel|Google UK English Male|Arthur|Oliver/.test(v.name)) || pool[0];
    if (who === "narrator") return { voice: narr, pitch: 1, rate: 1 };
    if (who === "table") return { voice: pool.find(v => /Samantha|Google US English|Karen/.test(v.name)) || narr, pitch: 1.05, rate: 1.1 };
    const others = pool.filter(v => v !== narr), h = hash(who);
    return { voice: others.length ? others[h % others.length] : narr, pitch: 0.8 + (h % 9) * 0.05, rate: 0.95 + (h % 3) * 0.05 };
  }
  function say(text, who) {
    voice.log.push({ t: performance.now(), who, text });
    if (!voice.on || !window.speechSynthesis || !text) return;
    const u = new SpeechSynthesisUtterance(text), v = voiceFor(who);
    if (v.voice) u.voice = v.voice;
    u.pitch = v.pitch; u.rate = v.rate;
    speechSynthesis.speak(u);
  }

  // ── what happened ───────────────────────────────────────────────────────────────────────────────────────
  const who = v => !v || v === "narrator" ? "The DM" : String(v).replace(/^NPC\s+/, "");
  function push(e, { quiet = false } = {}) {
    if (e.type === "dm_part") {
      if (e.turn !== st.turn) { st.turn = e.turn; st.parts = []; }
      const last = st.parts[st.parts.length - 1], w = who(e.voice);
      if (last && last.who === w) last.text += " " + e.text; else st.parts.push({ who: w, text: e.text });
      spokenTurns.add(e.turn);
      if (!quiet) say(e.text, e.voice || "narrator");
    } else if (e.type === "dm") {
      if (!(e.turn && e.turn === st.turn && st.parts.length)) { st.turn = e.turn || null; st.parts = [{ who: "The DM", text: e.text }]; }
      if (!quiet && !(e.turn && spokenTurns.has(e.turn))) say(e.text, "narrator");
    } else if (e.type === "say") {
      if (e.ai && e.turn && e.turn === st.turn) {        // an AI companion's line from the same reply: part of the story
        if (!st.parts.some(p => p.who === e.by && p.text.includes(e.text.slice(0, 20)))) st.parts.push({ who: e.by, text: e.text });
      } else st.said = `${e.by}${e.ic === false ? " (aside)" : ""}: ${e.text}`;
      if (!quiet && e.by !== me() && !(e.turn && spokenTurns.has(e.turn))) say(e.text, e.by);
    } else if (e.type === "roll") {
      st.roll = String(e.text || "").replace(/^ROLL\s+/, "🎲 ");
      if (!quiet && e.roll) {
        const m = String(e.text).match(/^ROLL ([^:]+):/), why = (String(e.text).match(/\(([^)]*)\)$/) || [])[1];
        say(`${m ? m[1] : e.by}${why && why !== "physical die" ? ", " + why : ""}: ${e.roll.total}${e.roll.crit ? ", a critical!" : e.roll.fumble ? ", a fumble." : ""}`, "table");
      }
    } else if (e.type === "dnd" && ["turn", "initiative", "hp", "condition"].includes(e.kind) && e.text) {
      if (!quiet && e.kind === "turn") say(e.text.replace(/^Round \d+:\s*/, ""), "table");
    } else return;
    st.dirty = true;
  }
  function setState(S) {
    const ini = S && S.initiative, now = ini && ini.active && ini.order.length && !ini.pending.length ? ini.order[ini.turn].name : null;
    const scene = (S && (S.scene || (S.map && S.map.name))) || "";
    const footer = ini && ini.active ? (ini.pending.length ? `Roll initiative: ${ini.pending.join(", ")}` : `Round ${ini.round} · ${now === me() ? "your turn" : now + "'s turn"}`)
      : S && S.dm_busy ? "The DM is thinking…" : "";
    if (scene !== st.scene || footer !== st.footer) { st.scene = scene; st.footer = footer; st.dirty = true; }
  }

  // ── the parchment ───────────────────────────────────────────────────────────────────────────────────────
  function wrap(g, text, width) {
    const out = [];
    for (const para of String(text).split(/\n+/)) {
      let line = "";
      for (const word of para.split(/\s+/).filter(Boolean)) {
        const t = line ? line + " " + word : word;
        if (g.measureText(t).width > width && line) { out.push(line); line = word; } else line = t;
      }
      if (line) out.push(line);
    }
    return out;
  }
  function draw() {
    const g = c.getContext("2d"); g.clearRect(0, 0, W, H);
    g.fillStyle = "rgba(20,14,8,.9)"; g.beginPath(); g.roundRect(6, 6, W - 12, H - 12, 34); g.fill();
    g.strokeStyle = "#d9b46a"; g.lineWidth = 4; g.stroke();
    g.fillStyle = "#d9b46a"; g.font = "700 36px Georgia, serif"; g.fillText((st.scene || "The story").slice(0, 30), 36, 60);
    g.fillStyle = "#f4dc9b"; g.font = "700 26px system-ui"; g.textAlign = "right"; g.fillText(st.footer, W - 40, 58); g.textAlign = "left";
    let y = 112;
    const rows = [];
    g.font = "32px Georgia, serif";
    for (const p of st.parts) {
      if (p.who !== "The DM" || st.parts.length > 1) rows.push({ head: p.who });
      for (const l of wrap(g, p.text, W - 80)) rows.push({ line: l, npc: p.who !== "The DM" });
    }
    if (!rows.length) rows.push({ line: "The DM will speak here.", npc: true });
    const fit = rows.slice(-9);                        // the latest lines: the story scrolls as it's told
    if (rows.length > fit.length && fit[0].line) fit[0] = { ...fit[0], line: "… " + fit[0].line };
    for (const r of fit) {
      if (r.head) { g.fillStyle = "#7fe0d6"; g.font = "700 26px system-ui"; g.fillText(r.head, 40, y); y += 36; continue; }
      g.fillStyle = r.npc ? "#e8d9b0" : "#f4ead2"; g.font = (r.npc ? "italic " : "") + "32px Georgia, serif"; g.fillText(r.line, 40, y); y += 42;
    }
    g.font = "26px system-ui"; g.fillStyle = "#b9c4c0";
    if (st.said) g.fillText(st.said.length > 72 ? st.said.slice(0, 71) + "…" : st.said, 40, H - 88);
    g.fillStyle = "#7fe0d6"; if (st.roll) g.fillText(st.roll.length > 72 ? st.roll.slice(0, 71) + "…" : st.roll, 40, H - 40);
    tex.needsUpdate = true; st.dirty = false;
  }
  draw();
  return {
    mesh, push, setState, voice,
    tick() { if (st.dirty) draw(); },
    debug: () => ({ parts: st.parts.map(p => ({ ...p })), said: st.said, roll: st.roll, scene: st.scene, footer: st.footer }),
  };
}
