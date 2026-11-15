// The Hold'em table in 3D: an oval felt, the players around it, chips, cards and the dealer button. Scene content
// only; poker.html owns the renderer, camera and input. Everything is drawn here (canvas textures, primitives): no
// photos, no third-party card art. The scene only ever receives public state plus this device's own two cards,
// so another player's face-down card has no face to draw.
//
// Frame: y up, the felt at y = 0, its centre at the origin. Seats sit on an ellipse; "me" is always at the front
// (+z, toward the camera), and play goes clockwise seen from above (to my left on screen).
import * as THREE from "three";

const A = 4.3, B = 2.45;                                  // the felt's half-axes
const CARD_W = 0.5, CARD_H = 0.7;
const CHIP_R = 0.12, CHIP_H = 0.034;
const DENOMS = [[1000, "#d9b46a", "#5a3d10"], [500, "#7b52cc", "#efe6ff"], [100, "#16161c", "#d9b46a"],
                [25, "#2f9a6a", "#f4f1e6"], [5, "#c0392b", "#f4f1e6"], [1, "#ece6d6", "#2a7f7a"]];
const RED = new Set(["h", "d"]);
const SUIT = { s: "♠", h: "♥", d: "♦", c: "♣" };
const RANK = r => (r === "T" ? "10" : r);
const FLY = 0.45;                                         // seconds for chips or a card to cross the table

// ── canvas helpers ────────────────────────────────────────────────────────────────────────────
function canvasTex(w, h, draw) {
  const c = document.createElement("canvas"); c.width = w; c.height = h;
  draw(c.getContext("2d"), w, h);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
  return t;
}
function lcg(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

const faceCache = new Map();
function cardFace(code) {                                 // rank and suit in the corners, a big pip in the middle
  if (faceCache.has(code)) return faceCache.get(code);
  const t = canvasTex(256, 358, (g, w, h) => {
    const ink = RED.has(code[1]) ? "#b8322a" : "#15161c";
    g.fillStyle = "#fbf6ea"; g.beginPath(); g.roundRect(2, 2, w - 4, h - 4, 22); g.fill();
    g.strokeStyle = "#d9b46a"; g.lineWidth = 4; g.beginPath(); g.roundRect(10, 10, w - 20, h - 20, 16); g.stroke();
    g.fillStyle = ink; g.textAlign = "center"; g.textBaseline = "middle";
    const r = RANK(code[0]), s = SUIT[code[1]];
    for (const flip of [false, true]) {
      g.save(); if (flip) { g.translate(w, h); g.rotate(Math.PI); }
      g.font = `800 ${r.length > 1 ? 58 : 66}px Georgia, serif`; g.fillText(r, 46, 52);
      g.font = "56px Georgia, serif"; g.fillText(s, 46, 108);
      g.restore();
    }
    g.font = "170px Georgia, serif"; g.fillText(s, w / 2, h / 2 + 8);
  });
  faceCache.set(code, t); return t;
}
let backTex = null;
function cardBack() {                                     // the Divinci back: deep teal, a gold lattice, the relic sigil
  return backTex ||= canvasTex(256, 358, (g, w, h) => {
    g.fillStyle = "#fbf6ea"; g.beginPath(); g.roundRect(2, 2, w - 4, h - 4, 22); g.fill();
    const grd = g.createLinearGradient(0, 0, w, h); grd.addColorStop(0, "#0d4b52"); grd.addColorStop(1, "#06222a");
    g.fillStyle = grd; g.beginPath(); g.roundRect(14, 14, w - 28, h - 28, 14); g.fill();
    g.save(); g.beginPath(); g.roundRect(22, 22, w - 44, h - 44, 10); g.clip();
    g.strokeStyle = "rgba(217,180,106,.35)"; g.lineWidth = 2;
    for (let k = -h; k < w + h; k += 22) { g.beginPath(); g.moveTo(k, 0); g.lineTo(k + h, h); g.moveTo(k, h); g.lineTo(k + h, 0); g.stroke(); }
    g.restore();
    g.strokeStyle = "#d9b46a"; g.lineWidth = 4; g.beginPath(); g.roundRect(22, 22, w - 44, h - 44, 10); g.stroke();
    const cx = w / 2, cy = h / 2;
    g.fillStyle = "#06222a"; g.beginPath(); g.arc(cx, cy, 58, 0, Math.PI * 2); g.fill();
    g.lineWidth = 5; g.beginPath(); g.arc(cx, cy, 52, 0, Math.PI * 2); g.stroke();
    g.beginPath();
    for (let k = 0; k < 6; k++) { const a = Math.PI / 6 + k * Math.PI / 3; g[k ? "lineTo" : "moveTo"](cx + 34 * Math.cos(a), cy + 34 * Math.sin(a)); }
    g.closePath(); g.stroke();
    g.fillStyle = "#7ff3ea"; g.beginPath(); g.arc(cx, cy, 10, 0, Math.PI * 2); g.fill();
  });
}

function chipTextures(color, edge, value) {
  const side = canvasTex(256, 32, (g, w, h) => {
    g.fillStyle = color; g.fillRect(0, 0, w, h);
    g.fillStyle = edge; for (let k = 0; k < 8; k++) g.fillRect(k * 32 + 8, 0, 14, h);
  });
  const top = canvasTex(128, 128, (g, w) => {
    const c = w / 2;
    g.fillStyle = color; g.beginPath(); g.arc(c, c, 63, 0, Math.PI * 2); g.fill();
    g.strokeStyle = edge; g.lineWidth = 12;
    for (let k = 0; k < 8; k++) { g.beginPath(); g.arc(c, c, 55, k * Math.PI / 4, k * Math.PI / 4 + Math.PI / 9); g.stroke(); }
    g.fillStyle = edge; g.globalAlpha = .9; g.beginPath(); g.arc(c, c, 36, 0, Math.PI * 2); g.fill(); g.globalAlpha = 1;
    g.fillStyle = color; g.font = `800 ${value >= 1000 ? 26 : 32}px system-ui, sans-serif`; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(value >= 1000 ? value / 1000 + "K" : String(value), c, c + 1);
  });
  return [new THREE.MeshStandardMaterial({ map: side, roughness: .45 }), new THREE.MeshStandardMaterial({ map: top, roughness: .4 }),
          new THREE.MeshStandardMaterial({ map: top, roughness: .4 })];
}

// How a pile of chips breaks into colours: big chips while there are plenty, the last two denominations exactly.
export function breakdown(amount) {
  const out = []; let rem = Math.max(0, Math.round(amount));
  DENOMS.forEach(([v], k) => {
    let n = 0;
    if (k >= DENOMS.length - 2) { n = Math.floor(rem / v); } else { while (rem >= 3 * v) { rem -= v; n++; } }
    if (k >= DENOMS.length - 2) rem -= n * v;
    if (n) out.push([k, n]);
  });
  return out;
}

class Ring3 extends THREE.Curve {                          // an ellipse in the xz plane, for the rail and trims
  constructor(rx, rz, y) { super(); this.rx = rx; this.rz = rz; this.y = y; }
  getPoint(t, v = new THREE.Vector3()) { const a = t * Math.PI * 2; return v.set(this.rx * Math.cos(a), this.y, this.rz * Math.sin(a)); }
}

export function createPokerScene() {
  const root = new THREE.Group();
  const chipGeo = new THREE.CylinderGeometry(CHIP_R, CHIP_R, CHIP_H, 28);
  const chipMats = DENOMS.map(([v, c, e]) => chipTextures(c, e, v));
  const cardGeo = new THREE.PlaneGeometry(CARD_W, CARD_H);
  const backMat = new THREE.MeshStandardMaterial({ map: cardBack(), roughness: .6 });

  // ── the table ──
  const felt = (() => {
    const shape = new THREE.Shape(); shape.absellipse(0, 0, A, B, 0, Math.PI * 2, false, 0);
    const geo = new THREE.ShapeGeometry(shape, 120);
    const pos = geo.attributes.position, uv = geo.attributes.uv;
    for (let k = 0; k < pos.count; k++) uv.setXY(k, (pos.getX(k) + A) / (2 * A), (pos.getY(k) + B) / (2 * B));
    const W = 1536, H = Math.round(1536 * B / A), rnd = lcg(7);
    const toPx = (x, z) => [(x + A) / (2 * A) * W, (z + B) / (2 * B) * H];
    const tex = canvasTex(W, H, g => {
      const grd = g.createRadialGradient(W / 2, H / 2, 40, W / 2, H / 2, W * .55);
      grd.addColorStop(0, "#15685f"); grd.addColorStop(1, "#0a3934");
      g.fillStyle = grd; g.fillRect(0, 0, W, H);
      for (let k = 0; k < 26000; k++) { g.fillStyle = rnd() < .5 ? "rgba(255,255,255,.035)" : "rgba(0,0,0,.06)"; g.fillRect(rnd() * W, rnd() * H, 2, 2); }
      g.strokeStyle = "rgba(217,180,106,.55)"; g.lineWidth = 4;                 // the betting line
      g.beginPath(); g.ellipse(W / 2, H / 2, W / 2 * .64, H / 2 * .64, 0, 0, Math.PI * 2); g.stroke();
      g.lineWidth = 2; g.beginPath(); g.ellipse(W / 2, H / 2, W / 2 * .66, H / 2 * .66, 0, 0, Math.PI * 2); g.stroke();
      g.strokeStyle = "rgba(244,220,155,.38)"; g.lineWidth = 3;                // the five community-card spots
      for (let k = 0; k < 5; k++) {
        const [x0, y0] = toPx((k - 2) * 0.62 - CARD_W / 2 - .03, -0.32 - CARD_H / 2 - .03), [x1, y1] = toPx((k - 2) * 0.62 + CARD_W / 2 + .03, -0.32 + CARD_H / 2 + .03);
        g.beginPath(); g.roundRect(x0, y0, x1 - x0, y1 - y0, 10); g.stroke();
      }
      g.fillStyle = "rgba(244,220,155,.32)"; g.textAlign = "center"; g.textBaseline = "middle";
      g.font = "700 46px Georgia, serif"; g.letterSpacing = "14px";
      const [tx, ty] = toPx(0.55, -1.6); g.fillText("DIVINCI TABLE", tx, ty);
      g.font = "italic 26px Georgia, serif"; g.letterSpacing = "4px";
      const [ux, uy] = toPx(0, 1.0); g.fillText("Texas Hold'em · play chips only", ux, uy);
    });
    const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ map: tex, roughness: .95 }));
    m.rotation.x = -Math.PI / 2; m.receiveShadow = true;
    return m;
  })();
  const leather = new THREE.MeshStandardMaterial({ color: 0x0f2129, roughness: .5, metalness: .05 });
  const wood = new THREE.MeshStandardMaterial({ color: 0x5b3820, roughness: .55 });
  const gold = new THREE.MeshStandardMaterial({ color: 0xd9b46a, roughness: .3, metalness: .8 });
  const rail = new THREE.Mesh(new THREE.TubeGeometry(new Ring3(A + .2, B + .2, .1), 220, .23, 18, true), leather);
  const trim = new THREE.Mesh(new THREE.TubeGeometry(new Ring3(A + .47, B + .47, -.02), 220, .07, 10, true), wood);
  const inner = new THREE.Mesh(new THREE.TubeGeometry(new Ring3(A - .01, B - .01, .012), 220, .025, 8, true), gold);
  const body = (() => {
    const s = new THREE.Shape(); s.absellipse(0, 0, A + .5, B + .5, 0, Math.PI * 2, false, 0);
    const m = new THREE.Mesh(new THREE.ExtrudeGeometry(s, { depth: .42, bevelEnabled: false, curveSegments: 96 }), wood);
    m.rotation.x = Math.PI / 2; m.position.y = -.02; return m;
  })();
  const pedestal = new THREE.Mesh(new THREE.CylinderGeometry(.7, 1.1, 1.3, 32), new THREE.MeshStandardMaterial({ color: 0x2b1b10, roughness: .7 }));
  pedestal.position.y = -1.05;
  const floor = new THREE.Mesh(new THREE.CircleGeometry(18, 64), new THREE.MeshStandardMaterial({ color: 0x08161c, roughness: 1 }));
  floor.rotation.x = -Math.PI / 2; floor.position.y = -1.7; floor.receiveShadow = true;
  for (const o of [rail, trim, body]) { o.castShadow = true; o.receiveShadow = true; }
  root.add(felt, rail, trim, inner, body, pedestal, floor);

  const deckPos = new THREE.Vector3(-0.95, 0.04, -1.02);  // the dealer's deck, beyond the board
  const deck = new THREE.Group();
  for (let k = 0; k < 7; k++) {
    const c = new THREE.Mesh(cardGeo, backMat); c.rotation.x = -Math.PI / 2; c.rotation.z = 0.35 + k * 0.01; c.position.y = k * 0.008; c.castShadow = true; deck.add(c);
  }
  deck.position.copy(deckPos).setY(0.004); root.add(deck);

  // ── where things go ──
  let n = 0, meIdx = 0;
  const theta = i => Math.PI / 2 + 2 * Math.PI * (((i - meIdx) % n + n) % n) / n;
  const at = (th, f, y = 0) => new THREE.Vector3(A * f * Math.cos(th), y, B * f * Math.sin(th));
  const faceY = th => Math.PI / 2 - th;                   // turns something so it reads upright from that seat
  const side = (th, d) => new THREE.Vector3(-Math.sin(th) * A, 0, Math.cos(th) * B).normalize().multiplyScalar(-d);  // toward that seat's right
  const spot = (i, what) => {
    const th = theta(i), mine = i === meIdx;
    if (what === "stack") return at(th, mine ? .8 : .82).add(side(th, mine ? 1.15 : .78));
    if (what === "bet") return at(th, .5);
    if (what === "cards") return at(th, mine ? .74 : .7, mine ? .2 : .012);
    if (what === "plate") return at(th, 1.13, .62);
    if (what === "button") return at(th, .6).add(side(th, -.55));
  };
  const POT = new THREE.Vector3(0, 0, 0.62);
  const boardPos = k => new THREE.Vector3((k - 2) * 0.62, 0.012, -0.32);

  // ── chips ──
  function buildStack(amount) {
    const g = new THREE.Group(), parts = breakdown(amount), cols = [];
    for (const [k, cnt] of parts) for (let left = cnt; left > 0; left -= 12) cols.push([k, Math.min(12, left)]);
    const shown = cols.slice(0, 8), spacing = CHIP_R * 2.15;
    const rnd = lcg(Math.round(amount) + 3), m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler();
    const byDen = new Map();
    shown.forEach(([k, cnt], c) => {
      const row = c < 4 ? 0 : 1, col = c % 4, rowLen = Math.min(4, shown.length - row * 4);
      const x = (col - (rowLen - 1) / 2) * spacing, z = row * spacing * 0.95;
      for (let j = 0; j < cnt; j++) { if (!byDen.has(k)) byDen.set(k, []); byDen.get(k).push([x + (rnd() - .5) * .012, CHIP_H / 2 + j * (CHIP_H + .002), z + (rnd() - .5) * .012, rnd() * 6.28]); }
    });
    for (const [k, list] of byDen) {
      const im = new THREE.InstancedMesh(chipGeo, chipMats[k], list.length);
      list.forEach(([x, y, z, r], j) => { e.set(0, r, 0); q.setFromEuler(e); m4.compose(new THREE.Vector3(x, y, z), q, new THREE.Vector3(1, 1, 1)); im.setMatrixAt(j, m4); });
      im.castShadow = true; im.receiveShadow = true; g.add(im);
    }
    g.userData.amount = amount;
    return g;
  }
  const stacks = new Map();                               // key → {group, want, shown, applyAt, pos, rot}
  function setStack(key, want, pos, rot, delay = 0) {
    let s = stacks.get(key);
    if (!s) { s = { group: new THREE.Group(), want: 0, shown: -1, applyAt: 0 }; stacks.set(key, s); root.add(s.group); }
    s.group.position.copy(pos); s.group.rotation.y = rot;
    if (s.want !== want) { s.want = want; s.applyAt = performance.now() / 1000 + (instant ? 0 : delay); }
  }
  function refreshStacks(now) {
    for (const s of stacks.values()) if (s.shown !== s.want && now >= s.applyAt) {
      for (const c of [...s.group.children]) s.group.remove(c);
      if (s.want > 0) s.group.add(buildStack(s.want));
      s.shown = s.want;
    }
  }
  const flights = [];                                     // chips or cards on their way somewhere
  let instant = false;                                    // the first update (a page opened mid-hand) places things, no replay
  function fly(obj, from, to, { delay = 0, dur = FLY, arc = .45, done } = {}) {
    root.add(obj);
    if (instant) { obj.position.copy(to); obj.visible = true; done?.(obj); return; }
    obj.position.copy(from); obj.visible = delay <= 0;
    flights.push({ obj, from: from.clone(), to: to.clone(), t: -delay / dur, dur, arc, done });
  }
  const flyChips = (amount, from, to, delay) => amount > 0 && fly(buildStack(amount), from, to, { delay, done: o => root.remove(o) });

  // ── cards ──
  function makeCard() {
    const group = new THREE.Group(), pivot = new THREE.Group();
    const front = new THREE.Mesh(cardGeo, new THREE.MeshStandardMaterial({ map: cardBack(), roughness: .55 }));
    const back = new THREE.Mesh(cardGeo, backMat); back.rotation.y = Math.PI;
    front.castShadow = back.castShadow = true;
    pivot.add(front, back); group.add(pivot);
    pivot.rotation.x = Math.PI / 2;                       // face down
    const c = { group, pivot, front, code: null, up: false, tilt: 0, flip: null };
    c.set = (code, up, tilt = 0) => {
      if (code && code !== c.code) { c.front.material.map = cardFace(code); c.front.material.needsUpdate = true; }
      if (code) c.code = code;
      const want = (up ? -Math.PI / 2 : Math.PI / 2) + (up ? tilt : 0);
      if (instant) { c.pivot.rotation.x = want; c.flip = null; }
      else if (Math.abs(want - c.pivot.rotation.x) > 1e-3 && (!c.flip || c.flip.to !== want)) c.flip = { from: c.pivot.rotation.x, to: want, t: 0 };
      c.up = up; c.tilt = tilt;
    };
    return c;
  }
  const holes = new Map();                                // seat index → [card, card]
  const board = [];
  function clearCards() {
    for (const cs of holes.values()) for (const c of cs) root.remove(c.group);
    holes.clear(); for (const c of board) root.remove(c.group); board.length = 0;
  }

  // ── name plates, the button, the banner ──
  function plateTex(p) {
    return canvasTex(512, 170, (g, w, h) => {
      g.globalAlpha = p.dim ? .55 : 1;
      g.fillStyle = "rgba(6,18,26,.9)"; g.beginPath(); g.roundRect(6, 6, w - 12, h - 12, 26); g.fill();
      g.lineWidth = p.active ? 9 : 4; g.strokeStyle = p.active ? "#f4dc9b" : p.me ? "#3fd0c9" : "rgba(217,180,106,.5)"; g.stroke();
      g.textAlign = "center"; g.textBaseline = "middle";
      g.fillStyle = p.active ? "#f4dc9b" : "#ece4cf"; g.font = "700 46px Georgia, serif";
      const name = p.name.length > 14 ? p.name.slice(0, 13) + "…" : p.name;
      g.fillText(name + (p.ai ? " · AI" : "") + (p.me ? " (you)" : ""), w / 2, 52, w - 40);
      g.fillStyle = "#d9b46a"; g.font = "700 38px ui-monospace, Menlo, monospace"; g.fillText(p.chips.toLocaleString("en-US") + " chips", w / 2, 102);
      g.fillStyle = p.active ? "#3fd0c9" : "#9fb0b0"; g.font = "600 28px system-ui, sans-serif"; g.fillText(p.status || " ", w / 2, 140, w - 40);
    });
  }
  const plates = new Map();                               // seat index → {sprite, key}
  const ring = new THREE.Mesh(new THREE.TorusGeometry(.62, .03, 8, 64), new THREE.MeshBasicMaterial({ color: 0x3fd0c9, transparent: true, opacity: .8 }));
  ring.rotation.x = -Math.PI / 2; ring.scale.set(1, 0.62, 1); ring.position.y = .015; ring.visible = false; root.add(ring);

  const puck = new THREE.Mesh(new THREE.CylinderGeometry(.2, .2, .05, 32), [
    new THREE.MeshStandardMaterial({ color: 0xf1ece0, roughness: .4 }),
    new THREE.MeshStandardMaterial({ map: canvasTex(128, 128, (g, w) => {
      g.fillStyle = "#f6f1e4"; g.beginPath(); g.arc(w / 2, w / 2, 63, 0, Math.PI * 2); g.fill();
      g.strokeStyle = "#d9b46a"; g.lineWidth = 6; g.beginPath(); g.arc(w / 2, w / 2, 52, 0, Math.PI * 2); g.stroke();
      g.fillStyle = "#15161c"; g.font = "800 66px Georgia, serif"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText("D", w / 2, w / 2 + 3);
    }), roughness: .4 }),
    new THREE.MeshStandardMaterial({ color: 0xf1ece0 })]);
  puck.position.set(0, .025, 0); puck.castShadow = true; puck.visible = false; root.add(puck);
  const puckTo = new THREE.Vector3();

  const label = (w, h, sx) => { const s = new THREE.Sprite(new THREE.SpriteMaterial({ transparent: true, depthTest: false })); s.renderOrder = 10; s.userData.w = w; s.userData.h = h; s.scale.set(sx, sx * h / w, 1); return s; };
  const banner = label(1024, 230, 3.6); banner.position.set(0, -0.35, 3.35); banner.visible = false; root.add(banner);   // in front of the near rail: nothing else is there
  const potLabel = label(512, 96, 1.2); potLabel.position.set(0, .34, .98); potLabel.visible = false; root.add(potLabel);
  let bannerKey = "", potKey = "";
  function drawLabel(s, draw) { const old = s.material.map; s.material.map = canvasTex(s.userData.w, s.userData.h, draw); s.material.needsUpdate = true; old?.dispose(); }

  // ── state → scene ──
  let prev = null, dealtHand = null;
  function update(state, me) {
    if (!state || !state.seats) return;
    instant = !prev;
    n = state.seats.length;
    const mi = me ? state.seats.findIndex(s => s.name === me.name) : -1;
    meIdx = mi >= 0 ? mi : 0;
    const now = performance.now() / 1000;
    const newHand = !prev || state.hand !== prev.hand;
    const finished = state.street === "done" && state.result && (!prev || prev.street !== "done" || newHand);

    // cards: a new hand deals from the deck, two rounds starting left of the button
    if (newHand) { clearCards(); dealtHand = null; }
    if (state.hand && dealtHand !== state.hand && state.street !== "waiting") {
      dealtHand = state.hand;
      const order = [];
      for (let k = 1; k <= n; k++) { const i = (state.button + k) % n; if (state.seats[i].in_hand) order.push(i); }
      let d = 0;
      for (let round = 0; round < 2; round++) for (const i of order) {
        const c = makeCard(); if (!holes.has(i)) holes.set(i, []); holes.get(i).push(c);
        const th = theta(i), base = spot(i, "cards"), mine = i === meIdx;
        const off = side(th, (round - .5) * (mine ? .62 : .32));
        c.group.rotation.y = faceY(th) + (mine ? 0 : (round - .5) * .14); c.group.scale.setScalar(mine ? 1.3 : 1);
        fly(c.group, deckPos, base.clone().add(off).setY(base.y + round * .004), { delay: newHand && prev ? .5 + d * .09 : 0, dur: .4, arc: .3 });
        d++;
      }
    }
    // the board
    while (board.length < state.board.length) {
      const k = board.length, c = makeCard(); board.push(c);
      c.group.rotation.y = 0; c.set(state.board[k], true);
      fly(c.group, deckPos, boardPos(k), { delay: prev ? (k - (prev.board?.length || 0)) * .18 + .25 : 0, dur: .45, arc: .35 });
    }
    // faces: my own cards up and tilted toward me; others' down until they're shown; folded hands go to the muck
    for (const [i, cs] of holes) {
      const s = state.seats[i];
      const mine = i === meIdx && me && me.hole && me.hole.length === 2;
      if (s.folded && !cs[0].mucked) {
        for (const c of cs) { c.mucked = true; fly(c.group, c.group.position.clone(), deckPos, { dur: .5, arc: .2, done: o => { o.visible = false; } }); c.set(null, false); }
        continue;
      }
      if (cs[0].mucked) continue;
      cs.forEach((c, k) => {
        if (mine) c.set(me.hole[k], true, .55);
        else if (s.cards) c.set(s.cards[k], true);
        else c.set(null, false);
      });
    }

    // chips: stacks, bets and the pot, each moving the way chips really move
    state.seats.forEach((s, i) => {
      const th = theta(i), rot = faceY(th), p = prev && !newHand ? prev.seats[i] : null;
      const won = finished && s.chips > (prev ? prev.seats[i].chips + prev.seats[i].bet : 0);
      setStack("stack" + i, s.chips, spot(i, "stack"), rot, won ? FLY + .9 : 0);
      const pb = p ? p.bet : 0;
      if (s.bet > pb) { flyChips(s.bet - pb, spot(i, "stack"), spot(i, "bet"), 0); setStack("bet" + i, s.bet, spot(i, "bet"), rot, FLY); }
      else {
        if (s.bet < pb && !finished) flyChips(pb, spot(i, "bet"), POT, 0);
        setStack("bet" + i, s.bet, spot(i, "bet"), rot, 0);
      }
    });
    const prevPot = prev && !newHand ? prev.pot : 0;
    if (finished && state.result) {                      // bets still out go in, then the pot goes to its winners
      prev && prev.seats.forEach((s, i) => s.bet > 0 && flyChips(s.bet, spot(i, "bet"), POT, 0));
      for (const pot of state.result.pots) {
        const share = Math.floor(pot.amount / pot.winners.length);
        for (const w of pot.winners) { const i = state.seats.findIndex(s => s.name === w); if (i >= 0) flyChips(share, POT, spot(i, "stack"), .9); }
      }
      setStack("pot", 0, POT, 0, .9);
    } else setStack("pot", state.pot, POT, 0, state.pot > prevPot && prev ? FLY : 0);

    // plates, whose turn, the button
    state.seats.forEach((s, i) => {
      const active = state.to_act === i && state.street !== "done";
      const status = s.out ? "out of chips" : state.street === "done" && state.result?.names?.[s.name] ? state.result.names[s.name]
        : active ? "to act" : s.folded ? "folded" : s.allin ? "all-in" : s.last || (i === state.button ? "dealer" : "");
      const p = { name: s.name, ai: s.ai, me: me && i === meIdx, chips: s.chips, status, active, dim: s.folded || s.out };
      const key = JSON.stringify(p);
      let pl = plates.get(i);
      if (!pl) { pl = { sprite: new THREE.Sprite(new THREE.SpriteMaterial({ transparent: true })), key: "" }; pl.sprite.scale.set(1.95, 1.95 * 170 / 512, 1); plates.set(i, pl); root.add(pl.sprite); }
      pl.sprite.position.copy(spot(i, "plate"));
      if (pl.key !== key) { const old = pl.sprite.material.map; pl.sprite.material.map = plateTex(p); pl.sprite.material.needsUpdate = true; old?.dispose(); pl.key = key; }
    });
    if (state.to_act != null && state.street !== "done") { const th = theta(state.to_act); ring.position.copy(at(th, .74)).setY(.015); ring.visible = true; } else ring.visible = false;
    if (state.button != null) { puckTo.copy(spot(state.button, "button")).setY(.025); if (!puck.visible) puck.position.copy(puckTo); puck.visible = true; }

    // the pot's number and the result
    const pk = state.pot > 0 && state.street !== "done" ? "Pot " + state.pot.toLocaleString("en-US") : "";
    if (pk !== potKey) { potKey = pk; potLabel.visible = !!pk; if (pk) drawLabel(potLabel, (g, w, h) => {
      g.fillStyle = "rgba(6,18,26,.82)"; g.beginPath(); g.roundRect(4, 4, w - 8, h - 8, 40); g.fill();
      g.fillStyle = "#f4dc9b"; g.font = "700 50px Georgia, serif"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(pk, w / 2, h / 2 + 2); }); }
    const res = state.street === "done" && state.result && (state.result.pots || []).length ? state.result : null;   // a called-off hand has no pots
    const bk = res ? JSON.stringify(res.pots.map(p => [p.winners, p.amount, p.hand])) : "";
    if (bk !== bannerKey) {
      bannerKey = bk; banner.visible = !!bk;
      if (bk) {
        const top = res.pots[0], total = res.pots.reduce((a, p) => a + (p.winners.join() === top.winners.join() ? p.amount : 0), 0);
        const line1 = `${top.winners.join(" & ")} ${top.winners.length > 1 ? "split" : "wins"} ${total.toLocaleString("en-US")}`;
        const line2 = top.hand || (res.uncontested ? "everyone else folded" : "");
        drawLabel(banner, (g, w, h) => {
          g.fillStyle = "rgba(4,13,18,.88)"; g.beginPath(); g.roundRect(8, 8, w - 16, h - 16, 40); g.fill();
          g.strokeStyle = "#d9b46a"; g.lineWidth = 6; g.stroke();
          g.textAlign = "center"; g.textBaseline = "middle";
          g.fillStyle = "#f4dc9b"; g.font = "800 76px Georgia, serif"; g.fillText(line1, w / 2, 84, w - 60);
          g.fillStyle = "#3fd0c9"; g.font = "600 46px Georgia, serif"; g.fillText(line2, w / 2, 164, w - 60);
        });
      }
    }
    prev = JSON.parse(JSON.stringify(state));
    instant = false;
  }

  function tick(dt, camera) {
    const now = performance.now() / 1000;
    for (let k = flights.length - 1; k >= 0; k--) {
      const f = flights[k];
      f.t += dt / f.dur;
      if (f.t < 0) continue;
      f.obj.visible = true;
      const t = Math.min(1, f.t), e = t * t * (3 - 2 * t);
      f.obj.position.lerpVectors(f.from, f.to, e); f.obj.position.y += Math.sin(Math.PI * t) * f.arc;
      if (t >= 1) { flights.splice(k, 1); f.done?.(f.obj); }
    }
    for (const c of [...board, ...[...holes.values()].flat()]) if (c.flip) {
      c.flip.t = Math.min(1, c.flip.t + dt / .35);
      const e = c.flip.t * c.flip.t * (3 - 2 * c.flip.t);
      c.pivot.rotation.x = c.flip.from + (c.flip.to - c.flip.from) * e;
      c.pivot.position.y = Math.sin(Math.PI * c.flip.t) * .18;
      if (c.flip.t >= 1) c.flip = null;
    }
    refreshStacks(now);
    puck.position.lerp(puckTo, Math.min(1, dt * 5));
    ring.material.opacity = .45 + .4 * Math.sin(now * 4);
    if (banner.visible) banner.position.y = -0.35 + Math.sin(now * 1.6) * .03;
  }

  // What this device's scene holds, for tests: my face-up cards and how many cards are face down. It has no faces
  // for anyone else's hidden cards to report.
  const debug = () => ({
    seats: n, me: meIdx,
    myCards: (holes.get(meIdx) || []).filter(c => c.up).map(c => c.code),
    shown: Object.fromEntries([...holes].filter(([i]) => i !== meIdx).map(([i, cs]) => [i, cs.filter(c => c.up).map(c => c.code)])),
    faceDown: [...holes.values()].flat().filter(c => !c.up && !c.mucked).length,
    board: board.map(c => c.code), flying: flights.length, banner: banner.visible,
    stacks: Object.fromEntries([...stacks].map(([k, s]) => [k, s.shown])),
  });
  return { root, update, tick, debug, theta: i => theta(i) };
}
