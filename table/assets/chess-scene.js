// The chess board in 3D: a wooden board in its frame, turned pieces (lathe profiles; the knight an extruded head), the
// square marks the 2D board has (selected, legal targets, last move, check), and moves that glide (a knight hops).
// Scene content only; chess.html owns the renderer, camera and input. 1 unit = one square; the board's centre at the
// origin; files a→h along +X, ranks 1→8 along -Z, so White sits at +Z and Black at -Z.
// Everything is drawn here from primitives: no meshes, textures or fonts are downloaded.
import * as THREE from "three";

const FILES = "abcdefgh";
export const squareXZ = sq => [FILES.indexOf(sq[0]) - 3.5, 3.5 - (+sq[1] - 1)];
const sqName = (f, r) => FILES[f] + (r + 1);

// ── pieces ────────────────────────────────────────────────────────────────────────────────────────────────────
const V = (r, y) => new THREE.Vector2(r, y);
const BASE = [V(0, 0), V(0.34, 0), V(0.345, 0.045), V(0.31, 0.075), V(0.32, 0.105), V(0.25, 0.15)];
function cap(cy, rx, ry, a0 = -70, a1 = 90, n = 10) {   // a rounded top: an arc of an ellipse, ending on the axis
  const out = [];
  for (let i = 0; i <= n; i++) { const a = (a0 + (a1 - a0) * i / n) * Math.PI / 180; out.push(V(Math.max(0, rx * Math.cos(a)), cy + ry * Math.sin(a))); }
  out[out.length - 1].x = 0; return out;
}
const PROFILES = {
  p: [...BASE, V(0.15, 0.22), V(0.11, 0.33), V(0.185, 0.36), V(0.185, 0.385), V(0.1, 0.41), ...cap(0.5, 0.125, 0.125, -55)],
  r: [...BASE, V(0.21, 0.2), V(0.185, 0.5), V(0.245, 0.54), V(0.245, 0.68), V(0.17, 0.68), V(0.17, 0.63), V(0, 0.63)],
  b: [...BASE, V(0.165, 0.2), V(0.105, 0.52), V(0.205, 0.555), V(0.205, 0.58), V(0.11, 0.6), ...cap(0.72, 0.145, 0.17, -50)],
  q: [...BASE, V(0.175, 0.2), V(0.11, 0.63), V(0.225, 0.665), V(0.225, 0.69), V(0.125, 0.72), V(0.205, 0.87), V(0.17, 0.885), V(0, 0.85)],
  k: [...BASE, V(0.185, 0.2), V(0.12, 0.69), V(0.235, 0.725), V(0.235, 0.75), V(0.13, 0.78), V(0.2, 0.93), V(0, 0.95)],
  n: [...BASE, V(0.2, 0.2), V(0.205, 0.27), V(0, 0.27)],
};
const HEIGHT = { p: 0.63, r: 0.72, n: 0.86, b: 0.93, q: 1.0, k: 1.14 };

function knightHead() {                                // a horse's head in profile, facing +X, extruded and bevelled
  const s = new THREE.Shape();
  const pts = [[-0.2, 0], [0.2, 0], [0.15, 0.13], [0.23, 0.22], [0.33, 0.28], [0.35, 0.36], [0.3, 0.41], [0.18, 0.46], [0.13, 0.55],
               [0.07, 0.62], [0.01, 0.53], [-0.13, 0.48], [-0.22, 0.33], [-0.21, 0.13]];
  s.moveTo(...pts[0]); for (const p of pts.slice(1)) s.lineTo(...p); s.closePath();
  const g = new THREE.ExtrudeGeometry(s, { depth: 0.2, bevelEnabled: true, bevelThickness: 0.045, bevelSize: 0.035, bevelSegments: 3, curveSegments: 4 });
  g.translate(0, 0, -0.1); g.computeVertexNormals();
  return g;
}

const MAT = {
  w: new THREE.MeshStandardMaterial({ color: 0xe6d6b8, roughness: 0.5, metalness: 0.02 }),
  b: new THREE.MeshStandardMaterial({ color: 0x241a13, roughness: 0.32, metalness: 0.08 }),
  trim: new THREE.MeshStandardMaterial({ color: 0xd9b46a, roughness: 0.3, metalness: 0.75 }),
};
const GEO = {};                                         // built once, shared by every piece of a kind
function geos(kind) {
  if (GEO[kind]) return GEO[kind];
  const parts = [[new THREE.LatheGeometry(PROFILES[kind], 28), [0, 0, 0]]];
  const ball = r => new THREE.SphereGeometry(r, 12, 8);
  if (kind === "b") parts.push([ball(0.045), [0, 0.92, 0]]);
  if (kind === "q") { parts.push([ball(0.065), [0, 0.93, 0]]);
    for (let i = 0; i < 8; i++) { const a = i / 8 * Math.PI * 2; parts.push([ball(0.034), [Math.cos(a) * 0.19, 0.89, Math.sin(a) * 0.19]]); } }
  if (kind === "k") { parts.push([new THREE.BoxGeometry(0.06, 0.2, 0.06), [0, 1.04, 0]]); parts.push([new THREE.BoxGeometry(0.17, 0.06, 0.06), [0, 1.06, 0]]); }
  if (kind === "r") for (let i = 0; i < 4; i++) { const a = (i + 0.5) / 4 * Math.PI * 2;
    parts.push([new THREE.BoxGeometry(0.11, 0.08, 0.11), [Math.cos(a) * 0.19, 0.71, Math.sin(a) * 0.19]]); }
  if (kind === "n") parts.push([knightHead(), [0, 0.25, 0], "head"]);
  return (GEO[kind] = parts);
}

function makePiece(ch) {
  const kind = ch.toLowerCase(), white = ch !== kind, g = new THREE.Group();
  for (const [geo, pos, role] of geos(kind)) {
    const m = new THREE.Mesh(geo, MAT[white ? "w" : "b"]); m.position.set(...pos); m.castShadow = m.receiveShadow = true;
    // A knight looks forward and to the side, so each player sees its profile (head-on, an extruded head reads as a slab).
    if (role === "head") m.rotation.y = white ? Math.PI - 0.55 : -0.55;
    g.add(m);
  }
  g.userData.piece = ch;
  return g;
}

// ── the board ───────────────────────────────────────────────────────────────────────────────────────────────────
function label(text, flip) {
  const c = document.createElement("canvas"); c.width = c.height = 64;
  const x = c.getContext("2d"); x.fillStyle = "#e6c98a"; x.font = "600 44px Georgia, serif"; x.textAlign = "center"; x.textBaseline = "middle";
  x.fillText(text, 32, 34);
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace;
  const m = new THREE.Mesh(new THREE.PlaneGeometry(0.34, 0.34), new THREE.MeshBasicMaterial({ map: t, transparent: true, depthWrite: false }));
  m.rotation.x = -Math.PI / 2; if (flip) m.rotation.z = Math.PI;
  return m;
}

export function createChessScene() {
  const root = new THREE.Group(), boardG = new THREE.Group(), markG = new THREE.Group(), pieceG = new THREE.Group(), labelG = new THREE.Group();
  root.add(boardG, markG, pieceG, labelG);

  const light = new THREE.MeshStandardMaterial({ color: 0xe8d9b5, roughness: 0.55 }), dark = new THREE.MeshStandardMaterial({ color: 0x8f6c42, roughness: 0.5 });
  const tile = new THREE.BoxGeometry(1, 0.12, 1);
  for (let f = 0; f < 8; f++) for (let r = 0; r < 8; r++) {
    const [x, z] = squareXZ(sqName(f, r));
    const m = new THREE.Mesh(tile, (f + r) % 2 === 0 ? dark : light); m.position.set(x, -0.06, z); m.receiveShadow = true; boardG.add(m);
  }
  const wood = new THREE.MeshStandardMaterial({ color: 0x3b2414, roughness: 0.48 });
  for (const [w, d, x, z] of [[9.2, 0.6, 0, 4.3], [9.2, 0.6, 0, -4.3], [0.6, 8, 4.3, 0], [0.6, 8, -4.3, 0]]) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, 0.2, d), wood); m.position.set(x, -0.07, z); m.receiveShadow = m.castShadow = true; boardG.add(m);
  }
  for (const [w, d, x, z] of [[8.1, 0.04, 0, 4.03], [8.1, 0.04, 0, -4.03], [0.04, 8.1, 4.03, 0], [0.04, 8.1, -4.03, 0]]) {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, 0.03, d), MAT.trim); m.position.set(x, 0.02, z); boardG.add(m);
  }
  const plinth = new THREE.Mesh(new THREE.BoxGeometry(9.6, 0.3, 9.6), new THREE.MeshStandardMaterial({ color: 0x0d1d25, roughness: 0.8 }));
  plinth.position.y = -0.33; plinth.receiveShadow = true; boardG.add(plinth);

  function placeLabels(flip) {                         // a–h and 1–8 on the frame, the right way up for whoever sits there
    labelG.clear();
    const s = flip ? -1 : 1;
    for (let i = 0; i < 8; i++) {
      const a = label(FILES[i], flip); a.position.set(i - 3.5, 0.031, s * 4.3); labelG.add(a);
      const n = label(String(i + 1), flip); n.position.set(-s * 4.3, 0.031, 3.5 - i); labelG.add(n);
    }
  }
  let side = "white"; placeLabels(false);

  // marks: one quad per square (last move, selected, check) and a dot / capture ring per square (legal targets)
  const marks = {}, plane = new THREE.PlaneGeometry(1, 1), dot = new THREE.CircleGeometry(0.15, 28), ring = new THREE.RingGeometry(0.39, 0.47, 40);
  for (let f = 0; f < 8; f++) for (let r = 0; r < 8; r++) {
    const name = sqName(f, r), [x, z] = squareXZ(name);
    const mk = (geo, y) => { const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false }));
      m.rotation.x = -Math.PI / 2; m.position.set(x, y, z); m.visible = false; m.renderOrder = 2; markG.add(m); return m; };
    marks[name] = { quad: mk(plane, 0.004), dot: mk(dot, 0.008), ring: mk(ring, 0.008) };
  }
  const tint = (m, color, opacity) => { m.visible = true; m.material.color.setHex(color); m.material.opacity = opacity; };

  // pieces: square → record {g, ch, from, to, t, hop, out, inn}
  const at = new Map(), leaving = new Set();
  let fen = "";
  const parse = f => {                                   // FEN board → Map(square → piece letter)
    const m = new Map(); f.split(" ")[0].split("/").forEach((row, i) => { let file = 0;
      for (const ch of row) { if (/\d/.test(ch)) file += +ch; else { m.set(FILES[file] + (8 - i), ch); file++; } } });
    return m;
  };
  const put = (rec, sq) => { const [x, z] = squareXZ(sq); rec.g.position.set(x, 0, z); rec.sq = sq; };

  function setPieces(nextFen, lastmove) {
    if (nextFen === fen) return;
    const next = parse(nextFen), prev = new Map([...at].map(([sq, r]) => [sq, r.ch]));
    const changed = [...new Set([...next.keys(), ...prev.keys()])].filter(sq => next.get(sq) !== prev.get(sq));
    const snap = !fen || changed.length > 6;             // a new game or a jump (a restore): no animation
    fen = nextFen;
    if (snap) {
      for (const r of at.values()) pieceG.remove(r.g); at.clear();
      for (const [sq, ch] of next) { const r = { g: makePiece(ch), ch, t: 1 }; put(r, sq); pieceG.add(r.g); at.set(sq, r); }
      return;
    }
    const vacated = changed.filter(sq => prev.has(sq)).map(sq => ({ sq, rec: at.get(sq) }));
    const arrived = changed.filter(sq => next.has(sq)).map(sq => ({ sq, ch: next.get(sq) }));
    for (const v of vacated) at.delete(v.sq);
    const main = lastmove ? lastmove.slice(0, 2) : null;
    for (const a of arrived) {                         // a piece that left a square and one that arrived: the same piece moving
      let i = vacated.findIndex(v => v.rec && v.rec.ch === a.ch && v.sq === main && lastmove.slice(2, 4) === a.sq);
      if (i < 0) i = vacated.findIndex(v => v.rec && v.rec.ch === a.ch);
      let rec;
      if (i >= 0 && a.sq !== vacated[i].sq) {
        rec = vacated[i].rec; vacated[i].rec = null;
        const [fx, fz] = squareXZ(vacated[i].sq), [tx, tz] = squareXZ(a.sq);
        rec.from = new THREE.Vector3(fx, 0, fz); rec.to = new THREE.Vector3(tx, 0, tz); rec.t = 0;
        rec.hop = rec.ch.toLowerCase() === "n" ? 0.9 : vacated[i].sq === main ? 0.35 : 0.2; rec.sq = a.sq;
      } else {                                         // promotion (or anything unmatched): the new piece grows in place
        rec = { g: makePiece(a.ch), ch: a.ch, t: 1, inn: 0 }; put(rec, a.sq); rec.g.scale.setScalar(0.001); pieceG.add(rec.g);
      }
      const old = at.get(a.sq); if (old && old !== rec) { old.out = 0; leaving.add(old); }
      at.set(a.sq, rec);
    }
    for (const v of vacated) if (v.rec) { v.rec.out = 0; leaving.add(v.rec); }   // captured (en passant included): shrinks away
  }

  function update(view) {                                // view: {fen, side, sel, targets[], last[], check}
    if (!view || !view.fen) return;
    if (view.side && view.side !== side) { side = view.side; placeLabels(side === "black"); }
    setPieces(view.fen, view.lastmove);
    const occupied = parse(view.fen);
    for (const [name, m] of Object.entries(marks)) {
      m.quad.visible = m.dot.visible = m.ring.visible = false;
      if ((view.last || []).includes(name)) tint(m.quad, 0xf4dc9b, 0.42);
      if (view.sel === name) tint(m.quad, 0x3fd0c9, 0.6);
      if (view.check === name) tint(m.quad, 0xff5a3a, 0.7);
      if ((view.targets || []).includes(name)) occupied.has(name) ? tint(m.ring, 0x3fd0c9, 0.85) : tint(m.dot, 0x3fd0c9, 0.75);
    }
  }

  function tick(dt) {
    const time = performance.now() / 1000;
    for (const r of at.values()) {
      if (r.t < 1) {
        r.t = Math.min(1, r.t + dt / 0.42); const e = r.t * r.t * (3 - 2 * r.t);
        r.g.position.lerpVectors(r.from, r.to, e); r.g.position.y = Math.sin(Math.PI * r.t) * r.hop;
      }
      if (r.inn != null) { r.inn = Math.min(1, r.inn + dt / 0.35); r.g.scale.setScalar(Math.max(0.001, r.inn)); if (r.inn >= 1) r.inn = null; }
    }
    for (const r of leaving) {
      r.out = Math.min(1, r.out + dt / 0.35); r.g.scale.setScalar(Math.max(0.001, 1 - r.out)); r.g.position.y = -0.3 * r.out;
      if (r.out >= 1) { pieceG.remove(r.g); leaving.delete(r); }
    }
    for (const m of Object.values(marks)) if (m.quad.visible && m.quad.material.color.getHex() === 0xff5a3a) m.quad.material.opacity = 0.5 + 0.25 * Math.sin(time * 5);
  }

  const ground = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), _p = new THREE.Vector3(), _inv = new THREE.Matrix4();
  function squareAt(raycaster) {                         // the square under a ray: a piece it hits first, else the board
    const hit = raycaster.intersectObjects(pieceG.children, true).find(h => !leaving.has(recOf(h.object)));
    if (hit) { const r = recOf(hit.object); if (r) return r.sq; }
    root.updateMatrixWorld();
    const ray = raycaster.ray.clone().applyMatrix4(_inv.copy(root.matrixWorld).invert());
    if (!ray.intersectPlane(ground, _p)) return null;
    const f = Math.floor(_p.x + 4), r = Math.floor(4 - _p.z);
    return f >= 0 && f < 8 && r >= 0 && r < 8 ? sqName(f, r) : null;
  }
  function recOf(o) { while (o && !o.userData.piece) o = o.parent; if (!o) return null; for (const r of at.values()) if (r.g === o) return r; return null; }

  const busy = () => [...at.values()].some(r => r.t < 1 || r.inn != null) || leaving.size > 0;
  // Something on the board is moving (a glide, a capture, the pulsing check mark): the page keeps drawing until it stops.
  const animating = () => busy() || Object.values(marks).some(m => m.quad.visible && m.quad.material.color.getHex() === 0xff5a3a);
  const debug = () => ({ pieces: Object.fromEntries([...at].map(([sq, r]) => [sq, r.ch])), busy: busy(), side,
    marks: Object.fromEntries(Object.entries(marks).filter(([, m]) => m.quad.visible || m.dot.visible || m.ring.visible)
      .map(([n, m]) => [n, m.ring.visible ? "capture" : m.dot.visible ? "target" : "#" + m.quad.material.color.getHexString()])) });
  return { root, update, tick, squareAt, debug, animating };
}
