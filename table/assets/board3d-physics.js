// The board in 3D, with physics: every card is a persistent body (assets/cardphysics.js) pulled to where the game says it lies.
// New cards are dealt from the owner's side and flip face up as they land; a tap swings the card round with momentum; a card
// turned face down or up flips over in 3D; a card that leaves flies to its owner's graveyard. Same data as /api/board3d (public
// information only: face-down cards come without names), same interface as board3d.js plus step() and say():
//
//   <script src="/assets/cardphysics.js"></script>   (before the module)
//   import { createPhysicsBoard } from "/assets/board3d-physics.js";
//   const board = createPhysicsBoard(THREE, { parent: scene, y: 0, radius: 0.72, angleOf: name => …, art: true });
//   board.update(boardJson);      // whenever the game changes
//   board.step(dt);               // every frame
//   board.say(seat, text);        // a speech bubble over that seat
//   board.pick(raycaster)         // the card under the pointer, or null
//
// Card faces: the card's art when this table has it (/art/<id>.jpg, local and private: card art is copyrighted), otherwise a
// readable drawn card.

const W = 0.086, H = 0.12, T = 0.0016, GAP = 0.008;
const COLORS = { W: "#efe6c8", U: "#3f78c4", B: "#4a4148", R: "#c9503a", G: "#3f8c55" };

function colorsOf(c) {
  if (!c || !c.name) return [];
  const src = (c.cost || "") + (/^Land|Land\b/.test(c.type || "") ? " " + (c.text || "") : "");
  return [...new Set((src.match(/\{([WUBRG])(?:\/[WUBRGP])?\}/g) || []).map(m => m[1]))];
}

function wrap(g, text, x, y, w, lh, maxLines) {
  const words = String(text || "").replace(/\n/g, " ¶ ").split(/\s+/);
  let line = "", n = 0;
  for (const word of words) {
    if (word === "¶") { if (line) { g.fillText(line, x, y + n * lh); n++; line = ""; } continue; }
    const t = line ? line + " " + word : word;
    if (g.measureText(t).width > w && line) { g.fillText(line, x, y + n * lh); n++; line = word; if (n >= maxLines) return; }
    else line = t;
  }
  if (line && n < maxLines) g.fillText(line, x, y + n * lh);
}

export function createPhysicsBoard(THREE, { parent, y = 0, radius = 0.72, angleOf, colorOf = () => "#e6e6e6", art = true }) {
  const CP = window.CardPhysics;
  if (!CP) throw new Error("load /assets/cardphysics.js before board3d-physics.js");
  const root = new THREE.Group(); root.name = "board3d-physics"; root.position.y = y; parent.add(root);
  const geo = new THREE.BoxGeometry(W, T, H);
  const edgeMat = new THREE.MeshStandardMaterial({ color: 0x15120f, roughness: 0.85 });
  const texCache = new Map(), matCache = new Map(), loader = new THREE.TextureLoader();
  const cards = new Map();                 // key -> {mesh, body, seat, card, leaving}
  const piles = new Map();                 // seat -> {mesh, top, n}
  const labels = new Map();                // seat -> {mesh, text}
  const bubbles = [];
  let first = true;

  function canvasTex(draw, key, w = 360, h = 500) {
    if (texCache.has(key)) return texCache.get(key);
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    draw(c.getContext("2d"), w, h);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
    texCache.set(key, t); return t;
  }
  const backTex = () => canvasTex((g, w, h) => {
    const grd = g.createLinearGradient(0, 0, w, h); grd.addColorStop(0, "#2b1a10"); grd.addColorStop(1, "#4a2c18");
    g.fillStyle = grd; g.fillRect(0, 0, w, h);
    g.strokeStyle = "#d8b45a"; g.lineWidth = 10; g.strokeRect(14, 14, w - 28, h - 28);
    g.fillStyle = "#7a4a26"; g.beginPath(); g.ellipse(w / 2, h / 2, w * 0.34, h * 0.38, 0, 0, 7); g.fill();
    g.strokeStyle = "#e8c66a"; g.lineWidth = 6; g.stroke();
    g.fillStyle = "#f3e2b5"; g.font = "bold 44px Georgia"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText("✦", w / 2, h / 2);
  }, "__back");
  const drawnTex = c => canvasTex((g, w, h) => {
    const cols = colorsOf(c), land = /Land/.test(c.type || "");
    const fill = cols.length === 1 ? COLORS[cols[0]] : cols.length > 1 ? "#c8a64a" : land ? "#8a7a62" : "#9aa0a6";
    g.fillStyle = "#111"; g.fillRect(0, 0, w, h); g.fillStyle = fill; g.fillRect(10, 10, w - 20, h - 20);
    g.fillStyle = "rgba(255,255,255,0.9)"; g.fillRect(20, 20, w - 40, 54);
    g.fillStyle = "#111"; g.font = "bold 27px Georgia"; g.textBaseline = "middle"; g.fillText(String(c.name).slice(0, 22), 28, 47, w - 120);
    g.textAlign = "right"; g.font = "bold 22px Georgia"; g.fillText((c.cost || "").replace(/[{}]/g, ""), w - 28, 47, 90); g.textAlign = "left";
    const glyph = land ? "⛰" : /Creature/.test(c.type) ? "⚔" : /Artifact/.test(c.type) ? "⚙" : /Enchantment/.test(c.type) ? "❂" : "✷";
    g.fillStyle = "rgba(0,0,0,0.18)"; g.fillRect(20, 82, w - 40, 150);
    g.fillStyle = "rgba(255,255,255,0.75)"; g.font = "96px serif"; g.textAlign = "center"; g.fillText(glyph, w / 2, 160); g.textAlign = "left";
    g.fillStyle = "rgba(255,255,255,0.9)"; g.fillRect(20, 240, w - 40, 40);
    g.fillStyle = "#111"; g.font = "italic 19px Georgia"; g.fillText(String(c.type || "").slice(0, 34), 28, 261, w - 56);
    g.fillStyle = "rgba(255,255,255,0.93)"; g.fillRect(20, 288, w - 40, h - 308);
    g.fillStyle = "#111"; g.font = "16px Georgia"; g.textBaseline = "top"; wrap(g, c.text, 28, 296, w - 56, 19, 8);
  }, `drawn|${c.name}|${c.type}`);
  function faceTex(c) {
    if (!c || !c.name) return backTex();
    const id = art && window.CARD_ART && window.CARD_ART[c.name];
    if (!id) return drawnTex(c);
    const key = "art|" + id;
    if (!texCache.has(key)) {
      const t = loader.load(`/art/${id}.jpg`); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8; texCache.set(key, t);
    }
    return texCache.get(key);
  }
  function mat(tex) {
    if (!matCache.has(tex)) matCache.set(tex, new THREE.MeshStandardMaterial({ map: tex, roughness: 0.55, metalness: 0.0 }));
    return matCache.get(tex);
  }
  // a badge for what the picture can't show: P/T (it changes), counters, charge, token
  function badgeTex(text, color) {
    return canvasTex((g, w, h) => {
      g.clearRect(0, 0, w, h); g.fillStyle = color; g.beginPath(); g.roundRect(4, 4, w - 8, h - 8, 26); g.fill();
      g.fillStyle = "#fff"; g.font = "bold 60px system-ui"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, w / 2, h / 2 + 3, w - 16);
    }, "badge|" + text + color, 220, 110);
  }
  function setBadges(mesh, c) {
    for (const ch of [...mesh.children]) if (ch.userData.badge) { mesh.remove(ch); ch.material.dispose(); }
    const tags = [];
    if (c.pt) tags.push([c.pt, "#1d1d22"]);
    if (c.counters) tags.push([(c.counters > 0 ? "+" : "") + c.counters, "#2a7a3a"]);
    if (c.charge) tags.push(["⚡" + c.charge, "#2a4f8a"]);
    if (c.token) tags.push(["token", "#7a5a1a"]);
    tags.forEach(([t, col], i) => {
      const b = new THREE.Mesh(new THREE.PlaneGeometry(W * 0.5, W * 0.25), new THREE.MeshBasicMaterial({ map: badgeTex(t, col), transparent: true, depthWrite: false }));
      b.rotation.x = -Math.PI / 2; b.position.set(W * 0.22, T / 2 + 0.0004 + i * 0.0002, H * 0.36 - i * W * 0.27);
      b.userData.badge = true; b.renderOrder = 3; mesh.add(b);
    });
  }
  function makeMesh(c) {
    // BoxGeometry faces: +x, -x, +y (the face), -y (the back), +z, -z. Face down = half a turn about the long axis.
    const m = new THREE.Mesh(geo, [edgeMat, edgeMat, mat(faceTex(c)), mat(backTex()), edgeMat, edgeMat]);
    m.castShadow = true; m.receiveShadow = true; m.userData.card = c; root.add(m); return m;
  }

  // where everything goes: rows in front of each seat (creatures nearest the centre, other permanents, lands at the back;
  // identical lands share a slot as a small, slightly scattered stack), graveyard pile to the owner's left
  function layout(data) {
    const slots = [];
    for (const s of data.seats || []) {
      const ang = angleOf(s.name); if (ang == null || s.out) continue;
      const span = Math.min(Math.PI / 2 * 0.86, 2 * Math.PI / Math.max(2, (data.seats || []).length) * 0.86);
      const perms = (s.permanents || []).filter(c => c.attached_to == null);
      const isLand = c => !c.face_down && /Land/.test(c.type || "");
      const isCreature = c => c.face_down || /Creature/.test(c.type || "");
      const groups = (list, stack) => {
        const out = [], idx = new Map();
        list.forEach((c, i) => {
          const k = stack ? c.name + "|" + !!c.tapped : "#" + (c.id ?? i) + "|" + i;
          if (!idx.has(k)) { idx.set(k, out.length); out.push([]); }
          out[idx.get(k)].push(c);
        });
        return out;
      };
      const row = (grps, r) => {
        const n = grps.length; if (!n) return;
        const wide = grps.some(g => g.some(c => c.tapped)) ? H : W;          // a tapped card lies sideways: give it room
        const st = Math.min(wide + GAP, (r * span) / Math.max(1, n));
        grps.forEach((grp, i) => {
          const a = ang + (i - (n - 1) / 2) * (st / r);
          grp.forEach((c, k) => {
            const toward = [-Math.cos(a), -Math.sin(a)];                       // fan a stack toward the table's centre
            slots.push({ seat: s.name, card: c, key: `${s.name}|${c.id ?? c.name + "#" + i + "." + k}`,
                         p: [Math.cos(a) * r + toward[0] * k * 0.006, k * T * 1.05, Math.sin(a) * r + toward[1] * k * 0.006],
                         yaw: -a - Math.PI / 2 + (c.tapped ? -Math.PI / 2 : 0), faceDown: !!c.face_down });
          });
        });
      };
      row(groups(perms.filter(c => isCreature(c)), false), radius * 0.42);
      row(groups(perms.filter(c => !isLand(c) && !isCreature(c)), false), radius * 0.62);
      row(groups(perms.filter(isLand), true), radius * 0.82);
    }
    return slots;
  }
  const pileAt = (seat, n) => { const a = angleOf(seat) + Math.PI / 4 * 0.98; return [Math.cos(a) * radius * 0.95, Math.max(1, Math.min(n, 30)) * T * 0.6, Math.sin(a) * radius * 0.95]; };
  const deckAt = seat => { const a = angleOf(seat) - Math.PI / 4 * 0.98; return [Math.cos(a) * radius * 0.98, 0.16, Math.sin(a) * radius * 0.98]; };

  function seatLabel(s, turn) {
    // an upright tag over the seat's edge that always faces the camera: readable from any angle, for players and spectators
    const text = `${s.name} · ♥ ${s.life ?? "—"}${s.name === turn ? "  ◆" : ""}${s.out ? "  (out)" : ""}`;
    const prev = labels.get(s.name);
    if (prev && prev.text === text) return;
    if (prev) root.remove(prev.mesh);
    const col = colorOf(s.name);
    const tex = canvasTex((g, w, h) => {
      g.clearRect(0, 0, w, h); g.fillStyle = s.name === turn ? "rgba(36,30,14,0.92)" : "rgba(10,10,14,0.85)";
      g.beginPath(); g.roundRect(6, 6, w - 12, h - 12, 46); g.fill();
      g.strokeStyle = col; g.lineWidth = s.name === turn ? 10 : 6; g.stroke();
      g.fillStyle = col; g.font = "bold 54px system-ui"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, w / 2, h / 2 + 2, w - 40);
    }, "label|" + text + col, 640, 120);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
    const a = angleOf(s.name);
    sp.scale.set(0.3, 0.3 * 120 / 640, 1); sp.renderOrder = 8;
    sp.position.set(Math.cos(a) * radius * 0.97, 0.035, Math.sin(a) * radius * 0.97);
    root.add(sp); labels.set(s.name, { mesh: sp, text });
  }

  function updatePile(s) {
    const gy = s.graveyard || [], n = s.graveyard_count ?? gy.length, top = gy[gy.length - 1];
    const cur = piles.get(s.name);
    if (!n) { if (cur) { root.remove(cur.mesh); piles.delete(s.name); } return; }
    const card = { name: top, type: "", text: "" };
    if (!cur) {
      const m = makeMesh(card); m.userData.card = { name: `${s.name}'s graveyard (${n})`, text: gy.slice().reverse().join(" · ") };
      const p = pileAt(s.name, n), b = CP.makeBody("pile:" + s.name, { p, yaw: -angleOf(s.name) - Math.PI / 2 });
      piles.set(s.name, { mesh: m, body: b, top, n });
    } else if (cur.top !== top || cur.n !== n) {
      cur.mesh.material[2] = mat(faceTex(card)); cur.top = top; cur.n = n;
      cur.mesh.userData.card = { name: `${s.name}'s graveyard (${n})`, text: gy.slice().reverse().join(" · ") };
      CP.setTarget(cur.body, { p: pileAt(s.name, n), yaw: -angleOf(s.name) - Math.PI / 2 });
    }
  }

  function update(data) {
    const slots = layout(data), seen = new Set();
    for (const sl of slots) {
      seen.add(sl.key);
      let e = cards.get(sl.key);
      if (!e) {
        const deal = !first;                                               // mid-game arrivals are dealt; the first load just lays out
        const body = CP.makeBody(sl.key, { p: deal ? deckAt(sl.seat) : sl.p, yaw: sl.yaw, faceDown: deal ? true : sl.faceDown });
        e = { mesh: makeMesh(sl.card), body, seat: sl.seat, card: sl.card };
        cards.set(sl.key, e);
        if (!deal) CP.setTarget(body, { p: sl.p, yaw: sl.yaw, faceDown: sl.faceDown });
      }
      if (e.leaving) { e.leaving = false; e.mesh.visible = true; }
      const c = sl.card;
      if (e.card.name !== c.name && c.name) e.mesh.material[2] = mat(faceTex(c));     // turned face up: now it has a name
      if (JSON.stringify([e.card.pt, e.card.counters, e.card.charge, e.card.token]) !== JSON.stringify([c.pt, c.counters, c.charge, c.token]) || !e.badged) {
        setBadges(e.mesh, c); e.badged = true;
      }
      e.card = c; e.mesh.userData.card = c;
      CP.setTarget(e.body, { p: sl.p, yaw: sl.yaw, faceDown: sl.faceDown });
    }
    for (const [k, e] of cards) {                                          // gone from the battlefield: off to the graveyard
      if (seen.has(k) || e.leaving) continue;
      e.leaving = true; e.leftAt = performance.now();
      CP.setTarget(e.body, { p: pileAt(e.seat, 30), yaw: -angleOf(e.seat) - Math.PI / 2, faceDown: false });
    }
    for (const s of data.seats || []) { if (angleOf(s.name) == null) continue; seatLabel(s, data.turn); updatePile(s); }
    first = false;
  }

  function say(seat, text) {
    const a = angleOf(seat); if (a == null || !text) return;
    const lines = [], words = String(text).split(/\s+/); let line = "";
    for (const w of words) { const t = line ? line + " " + w : w; if (t.length > 34) { lines.push(line); line = w; } else line = t; }
    if (line) lines.push(line);
    const shown = lines.slice(0, 5); if (lines.length > 5) shown[4] += " …";
    const c = document.createElement("canvas"); c.width = 640; c.height = 70 + shown.length * 52;
    const g = c.getContext("2d");
    g.fillStyle = "rgba(255,255,255,0.95)"; g.beginPath(); g.roundRect(8, 8, c.width - 16, c.height - 16, 30); g.fill();
    g.strokeStyle = colorOf(seat); g.lineWidth = 6; g.stroke();
    g.fillStyle = "#0e0e12"; g.font = "500 40px system-ui"; g.textBaseline = "top";
    shown.forEach((l, i) => g.fillText(l, 34, 34 + i * 52, c.width - 60));
    const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
    const wdt = 0.44; sp.scale.set(wdt, wdt * c.height / c.width, 1); sp.renderOrder = 10;
    const ab = a + 0.55;                                     // beside the seat's cards, not over its name plate
    sp.position.set(Math.cos(ab) * radius * 0.66, 0.13, Math.sin(ab) * radius * 0.66);
    for (const b of bubbles) if (b.seat === seat) b.until = Math.min(b.until, performance.now() + 300);   // one bubble per seat
    root.add(sp);
    bubbles.push({ sp, seat, born: performance.now(), until: performance.now() + 2500 + 380 * words.length });
  }

  function step(dt) {
    for (const [k, e] of cards) {
      CP.step(e.body, dt);
      e.mesh.position.set(e.body.p[0], e.body.p[1] + T / 2, e.body.p[2]);
      e.mesh.quaternion.set(e.body.q[0], e.body.q[1], e.body.q[2], e.body.q[3]);
      if (e.leaving && (e.body.asleep || performance.now() - e.leftAt > 2200)) {   // it reached the pile: the pile shows it now
        root.remove(e.mesh); setBadges(e.mesh, {}); cards.delete(k);
      }
    }
    for (const [, pl] of piles) {
      CP.step(pl.body, dt);
      pl.mesh.position.set(pl.body.p[0], pl.body.p[1] + T / 2, pl.body.p[2]);
      pl.mesh.quaternion.set(pl.body.q[0], pl.body.q[1], pl.body.q[2], pl.body.q[3]);
      pl.mesh.scale.y = Math.max(1, Math.min(pl.n, 30) * 0.6);
    }
    const now = performance.now();
    for (let i = bubbles.length - 1; i >= 0; i--) {
      const b = bubbles[i], age = now - b.born, left = b.until - now;
      b.sp.position.y = 0.13 + Math.min(1, age / 400) * 0.03;
      b.sp.material.opacity = Math.max(0, Math.min(1, age / 250, left / 500));
      if (left <= 0) { root.remove(b.sp); b.sp.material.map.dispose(); b.sp.material.dispose(); bubbles.splice(i, 1); }
    }
  }

  return {
    group: root, update, step, say,
    pick(raycaster) {
      const hit = raycaster.intersectObjects(root.children, true).find(h => h.object.userData.card);
      return hit ? hit.object.userData.card : null;
    },
    get count() { return cards.size; },
    bodies: () => [...cards.values()].map(e => ({ key: e.body.id, asleep: e.body.asleep, p: e.body.p.slice(), faceDown: e.body.faceDown })),
  };
}
