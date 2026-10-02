// The whole board in 3D: every seat's battlefield, command zone and graveyard, laid on the table in
// front of its avatar. Public information only — the server sends face-down cards without names.
//
//   import { createBoard } from "/assets/board3d.js";
//   const board = createBoard(THREE, { parent: table, y: 0.751, radius: 0.70, angleOf: name => … });
//   board.update(await (await fetch("/api/board3d")).json());   // cheap: textures are cached by card
//   board.pick(raycaster) → the card under the pointer (for a tooltip), or null
//
// Cards are drawn on canvases (frame colour from the mana cost, name, cost, type, rules text, P/T),
// not Scryfall art: offline, and readable at a glance. Rows from the centre out: creatures, other
// permanents, lands (identical lands stack with a ×N badge). The commander sits to the owner's right
// while it's in the command zone; the graveyard pile to the left, its top card face up.

const W = 0.086, H = 0.12, GAP = 0.008;        // ~1.15× a real card, in table units (an avatar is 1.72)
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
    if (g.measureText(t).width > w && line) {
      g.fillText(line, x, y + n * lh); n++; line = word;
      if (n >= maxLines) return;
    } else line = t;
  }
  if (line && n < maxLines) g.fillText(line, x, y + n * lh);
}

export function createBoard(THREE, { parent, y, radius, angleOf }) {
  const root = new THREE.Group(); root.name = "board3d"; parent.add(root);
  const texCache = new Map(), geo = new THREE.BoxGeometry(W, 0.0015, H);
  const edge = new THREE.MeshStandardMaterial({ color: 0x111111, roughness: 0.8 });
  let lastSig = "";

  function canvasTex(draw, key) {
    if (texCache.has(key)) return texCache.get(key);
    const c = document.createElement("canvas"); c.width = 360; c.height = 500;
    draw(c.getContext("2d"), c.width, c.height);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
    texCache.set(key, t); return t;
  }
  const backTex = () => canvasTex((g, w, h) => {
    g.fillStyle = "#2a1a10"; g.fillRect(0, 0, w, h);
    g.fillStyle = "#7a4a26"; g.beginPath(); g.ellipse(w / 2, h / 2, w * 0.36, h * 0.4, 0, 0, 7); g.fill();
    g.strokeStyle = "#d8b45a"; g.lineWidth = 8; g.stroke();
    g.fillStyle = "#f3e2b5"; g.font = "bold 34px Georgia"; g.textAlign = "center"; g.fillText("?", w / 2, h / 2 + 12);
  }, "__back");
  const cardTex = c => canvasTex((g, w, h) => {
    const cols = colorsOf(c), land = /Land/.test(c.type || "");
    const fill = cols.length === 1 ? COLORS[cols[0]] : cols.length > 1 ? "#c8a64a" : land ? "#8a7a62" : "#9aa0a6";
    g.fillStyle = "#111"; g.fillRect(0, 0, w, h);
    g.fillStyle = fill; g.fillRect(10, 10, w - 20, h - 20);
    g.fillStyle = "rgba(255,255,255,0.88)"; g.fillRect(20, 20, w - 40, 54);              // name bar
    g.fillStyle = "#111"; g.font = "bold 27px Georgia"; g.textBaseline = "middle";
    g.fillText(String(c.name).slice(0, 22), 28, 47, w - 120);
    g.textAlign = "right"; g.font = "bold 22px Georgia"; g.fillText((c.cost || "").replace(/[{}]/g, ""), w - 28, 47, 90);
    g.textAlign = "left";
    // the art box: just a big glyph for the card's kind, in the frame colour
    const glyph = land ? "⛰" : /Creature/.test(c.type) ? "⚔" : /Planeswalker/.test(c.type) ? "✦" : /Artifact/.test(c.type) ? "⚙" : /Enchantment/.test(c.type) ? "❂" : "✷";
    g.fillStyle = "rgba(0,0,0,0.18)"; g.fillRect(20, 82, w - 40, 150);
    g.fillStyle = "rgba(255,255,255,0.75)"; g.font = "96px serif"; g.textAlign = "center"; g.fillText(glyph, w / 2, 160);
    g.textAlign = "left"; g.fillStyle = "rgba(255,255,255,0.88)"; g.fillRect(20, 240, w - 40, 40);   // type line
    g.fillStyle = "#111"; g.font = "italic 19px Georgia"; g.fillText(String(c.type || "").slice(0, 34), 28, 261, w - 56);
    g.fillStyle = "rgba(255,255,255,0.92)"; g.fillRect(20, 288, w - 40, h - 308);           // rules text
    g.fillStyle = "#111"; g.font = "16px Georgia"; g.textBaseline = "top"; wrap(g, c.text, 28, 296, w - 56, 19, 8);
    if (c.pt) {
      g.fillStyle = "#f4f1e8"; g.fillRect(w - 112, h - 64, 92, 46); g.strokeStyle = "#111"; g.lineWidth = 3; g.strokeRect(w - 112, h - 64, 92, 46);
      g.fillStyle = "#111"; g.font = "bold 28px Georgia"; g.textBaseline = "middle"; g.textAlign = "center"; g.fillText(c.pt, w - 66, h - 41);
    }
  }, `${c.name}|${c.pt}|${c.type}`);

  function badge(text, color = "#111") {
    const key = "badge|" + text + color;
    const tex = canvasTex((g, w, h) => {
      g.clearRect(0, 0, w, h); g.fillStyle = color; g.beginPath(); g.roundRect(20, 150, w - 40, 200, 90); g.fill();
      g.fillStyle = "#fff"; g.font = "bold 120px system-ui"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(text, w / 2, 252, w - 70);
    }, key);
    const m = new THREE.Mesh(new THREE.PlaneGeometry(W * 0.55, W * 0.55 * 500 / 360),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
    m.rotation.x = -Math.PI / 2; return m;
  }

  function cardMesh(c, n = 1) {
    const top = new THREE.MeshStandardMaterial({ map: c.face_down ? backTex() : cardTex(c), roughness: 0.6 });
    const m = new THREE.Mesh(geo, [edge, edge, top, edge, edge, edge]);
    m.castShadow = true; m.receiveShadow = true; m.userData.card = c;
    if (c.tapped) m.rotation.y = -Math.PI / 2;
    const tags = [];
    if (n > 1) tags.push(["×" + n, "#222"]);
    if (c.counters) tags.push([(c.counters > 0 ? "+" : "") + c.counters, "#2a7a3a"]);
    if (c.token) tags.push(["T", "#7a5a1a"]);
    if (c.zone) tags.push([c.zone.slice(0, 4), "#5a2a7a"]);
    if (c.face_down && c.how) tags.push([c.how.split(" ")[0].slice(0, 5), "#5a3a20"]);
    if (c.face_down && c.ward) tags.push(["W2", "#1a4a7a"]);
    tags.forEach(([t, col], i) => { const b = badge(t, col); b.position.set(W * 0.32, 0.002 + i * 0.0004, -H * 0.36 + i * W * 0.42); m.add(b); });
    return m;
  }

  // one row of cards along an arc in front of a seat; returns nothing, adds to `g`
  function row(g, cards, r, ang, span) {
    const n = cards.length; if (!n) return;
    const step = Math.min(W + GAP, (r * span) / Math.max(1, n));
    cards.forEach(([c, count], i) => {
      const a = ang + (i - (n - 1) / 2) * (step / r);
      const m = cardMesh(c, count);
      m.position.set(Math.cos(a) * r, i * 0.0006, Math.sin(a) * r);
      m.rotation.y += -a - Math.PI / 2;                     // the card's bottom edge faces its owner
      g.add(m);
    });
  }

  function seatGroup(s) {
    const g = new THREE.Group(), ang = angleOf(s.name);
    if (ang == null) return g;
    const span = Math.PI / 2 * 0.86;                       // a quarter of the table each, with a margin
    const perms = s.permanents || [];
    const isLand = c => /Land/.test(c.type || "") && !c.face_down;
    const isCreature = c => c.face_down || /Creature/.test(c.type || "");
    const lands = [], landIdx = new Map();
    for (const c of perms.filter(isLand)) {                // identical untapped lands stack
      const k = c.name + "|" + !!c.tapped;
      if (landIdx.has(k)) lands[landIdx.get(k)][1]++; else { landIdx.set(k, lands.length); lands.push([c, 1]); }
    }
    const attached = new Set(perms.filter(c => c.attached_to != null).map(c => c));
    row(g, perms.filter(c => isCreature(c) && !attached.has(c)).map(c => [c, 1]), radius * 0.40, ang, span);
    row(g, perms.filter(c => !isLand(c) && !isCreature(c) && !attached.has(c)).map(c => [c, 1]), radius * 0.60, ang, span);
    row(g, lands, radius * 0.80, ang, span);
    // command zone (right of the owner) and graveyard (left), at the table's edge
    const side = (da, c, n) => { const m = cardMesh(c, n); const a = ang + da;
      m.position.set(Math.cos(a) * radius * 0.92, 0, Math.sin(a) * radius * 0.92); m.rotation.y += -a - Math.PI / 2; g.add(m); return m; };
    if (s.commander && s.commander_in_zone) {
      const cc = s.commander_card || { name: s.commander, type: "Commander", text: "", cost: "" };
      const m = side(-span / 2 - 0.12, cc);
      m.userData.card = { ...cc, note: "in the command zone" + (s.commander_tax ? ` · tax ${s.commander_tax}` : "") };
    }
    const gy = s.graveyard || [];
    if (gy.length) {
      const n = s.graveyard_count ?? gy.length;
      const m = side(span / 2 + 0.12, { name: gy[gy.length - 1], type: "Graveyard", text: gy.slice().reverse().join(" · "), cost: "" }, n);
      m.userData.card = { name: `${s.name}'s graveyard (${n})`, text: gy.slice().reverse().join(" · ") };
    }
    return g;
  }

  return {
    group: root,
    update(data) {
      const sig = JSON.stringify(data.seats);
      if (sig === lastSig) return; lastSig = sig;
      while (root.children.length) {
        const ch = root.children.pop();
        ch.traverse(o => { if (o.isMesh && o.geometry !== geo) o.geometry.dispose(); });
      }
      for (const s of data.seats) { const g = seatGroup(s); g.position.y = y; root.add(g); }
    },
    pick(raycaster) {
      const hit = raycaster.intersectObjects(root.children, true).find(h => h.object.userData.card);
      return hit ? hit.object.userData.card : null;
    },
  };
}
