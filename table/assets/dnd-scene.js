// The D&D table in 3D: the location's room, the grid, and on every token's squares its mini or a standee.
// One module for the web stage (dnd.html's 3D view) and the headset's diorama (D6). Scene content only; the page
// owns the renderer, camera and input. 1 unit = one 5-ft square; the grid's (0, 0) corner at the origin, columns
// along +X, rows along +Z (the same frame as scripts/dnd_room_blender.py's room.glb).
import * as THREE from "three";
import { GLTFLoader } from "/vendor/jsm/loaders/GLTFLoader.js";
import { clone as cloneSkinned } from "/vendor/jsm/utils/SkeletonUtils.js";
import { MeshoptDecoder } from "/vendor/jsm/libs/meshopt_decoder.module.js";   // rooms are meshopt-compressed

export const SIZES = { tiny: 1, small: 1, medium: 1, large: 2, huge: 3, gargantuan: 4 };
const SIDE = { party: 0x3fd0c9, foe: 0xff9b6a };
const HP = { ok: 0x58d68d, hurt: 0xffb347, down: 0x666666 };
const BLOCK = { "#": [1.2, 0x3d3a36], T: [1.8, 0x2f5a2a], R: [0.5, 0x6d6a64], O: [1.4, 0x8d877d], K: [0.5, 0x7b5634],
                B: [0.55, 0x6a4426], S: [0.55, 0x7c7a76], C: [0.7, 0x7b5634] };
const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
const cache = new Map();                                 // url → Promise<gltf>

export const assetURL = p => p ? "/dnd-assets/" + String(p).replace(/^dnd\//, "") : null;
function load(url) {
  if (!cache.has(url)) cache.set(url, loader.loadAsync(url).catch(e => { cache.delete(url); throw e; }));
  return cache.get(url);
}
// A failed download is tried again (1, 3, 9, 20 s later) for as long as it's still wanted. Without this one blip
// (a dropped request, a rollout, an edge hiccup) left the placeholder up until a refresh (seen live 2026-10-05).
export const RETRY_MS = [1000, 3000, 9000, 20000];
function loadRetry(url, wanted, onLoad, attempt = 0) {
  load(url).then(g => { if (wanted()) onLoad(g); }).catch(() => {
    if (wanted() && attempt < RETRY_MS.length) setTimeout(() => wanted() && loadRetry(url, wanted, onLoad, attempt + 1), RETRY_MS[attempt]);
  });
}

export function createDndScene() {
  const root = new THREE.Group();                        // move/scale this to put the table somewhere (XR diorama)
  const roomG = new THREE.Group(), gridG = new THREE.Group(), tokG = new THREE.Group();
  root.add(roomG, gridG, tokG);
  const toks = new Map();                                // id → {group, body, ring, turn, from, to, t, data}
  let key = "", W = 1, H = 1, layout = [];

  function clear(g) { for (const c of [...g.children]) { g.remove(c); c.traverse?.(o => { o.geometry?.dispose?.(); }); } }

  function fallbackRoom(rows) {                          // no built room (or not approved): boxes from the layout
    const g = new THREE.Group();
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(W, H), new THREE.MeshStandardMaterial({ color: 0x24363b, roughness: 0.95 }));
    floor.rotation.x = -Math.PI / 2; floor.position.set(W / 2, 0, H / 2); floor.receiveShadow = true;
    g.add(floor);
    const box = new THREE.BoxGeometry(1, 1, 1), mats = {};
    rows.forEach((row, y) => [...row].forEach((ch, x) => {
      if (ch === "W" || ch === "~") {
        const t = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), new THREE.MeshStandardMaterial({
          color: ch === "W" ? 0x1d4f6b : 0x2f6b63, transparent: true, opacity: ch === "W" ? 0.9 : 0.6 }));
        t.rotation.x = -Math.PI / 2; t.position.set(x + .5, ch === "W" ? 0.01 : 0.015, y + .5); g.add(t); return;
      }
      const b = BLOCK[ch]; if (!b) return;
      const m = mats[ch] ||= new THREE.MeshStandardMaterial({ color: b[1], roughness: 0.9 });
      const o = new THREE.Mesh(box, m); o.scale.set(ch === "#" ? 1 : .7, b[0], ch === "#" ? 1 : .7);
      o.position.set(x + .5, b[0] / 2, y + .5); o.castShadow = o.receiveShadow = true; g.add(o);
    }));
    return g;
  }

  function grid() {
    const pts = [];
    for (let x = 0; x <= W; x++) pts.push(x, 0.02, 0, x, 0.02, H);
    for (let z = 0; z <= H; z++) pts.push(0, 0.02, z, W, 0.02, z);
    const geo = new THREE.BufferGeometry(); geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.14 }));
  }

  function setLocation(map) {
    const k = map.location + "|" + (map.assets?.room || "") + "|" + map.layout.join("");
    if (k === key) return;
    key = k; layout = map.layout; H = layout.length; W = Math.max(...layout.map(r => r.length));
    clear(roomG); clear(gridG);
    roomG.add(fallbackRoom(layout)); gridG.add(grid());
    const url = assetURL(map.assets?.room);
    if (url) loadRetry(url, () => key === k, gltf => {   // the placeholder stays until (unless) it arrives
      clear(roomG);
      const room = gltf.scene.clone(true);
      room.traverse(o => { if (o.isMesh) { o.castShadow = o.receiveShadow = true; } });
      roomG.add(room);
    });
  }

  function standee(t) {                                  // the creature's initials on a card in its side's colour
    const n = SIZES[t.size] || 1, c = document.createElement("canvas"); c.width = 256; c.height = 384;
    const g = c.getContext("2d"), col = "#" + (SIDE[t.side] || 0xcccccc).toString(16).padStart(6, "0");
    const grad = g.createLinearGradient(0, 0, 0, 384); grad.addColorStop(0, col); grad.addColorStop(1, "#06121a");
    g.fillStyle = grad; g.fillRect(0, 0, 256, 384);
    g.strokeStyle = "#f4dc9b"; g.lineWidth = 10; g.strokeRect(5, 5, 246, 374);
    g.fillStyle = "#06121a"; g.font = "800 120px system-ui"; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(t.name.split(/\s+/).map(w => w[0]).join("").slice(0, 2).toUpperCase(), 128, 165);
    g.fillStyle = "#ece4cf"; g.font = "700 34px system-ui";
    g.fillText(t.name.length > 13 ? t.name.slice(0, 12) + "…" : t.name, 128, 330);
    const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
    const card = new THREE.Mesh(new THREE.PlaneGeometry(0.62 * n, 0.93 * n), new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide, transparent: true }));
    card.position.y = 0.465 * n + 0.04; card.userData.billboard = true;
    return card;
  }

  function ringMesh(n, inner, outer, color) {
    const m = new THREE.Mesh(new THREE.RingGeometry(inner * n, outer * n, 40), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: .9, side: THREE.DoubleSide }));
    m.rotation.x = -Math.PI / 2; m.position.y = 0.03; return m;
  }

  function makeToken(t, minis) {
    const n = SIZES[t.size] || 1, group = new THREE.Group();
    const ring = ringMesh(n, 0.36, 0.46, HP.ok), turn = ringMesh(n, 0.47, 0.53, 0xf4dc9b);
    turn.visible = false;
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.4 * n, 0.42 * n, 0.05, 32), new THREE.MeshStandardMaterial({ color: SIDE[t.side] || 0xcccccc, roughness: .6 }));
    base.position.y = 0.025;
    let body = standee(t);
    group.add(base, ring, turn, body);
    group.userData.tokenId = t.id;
    group.traverse(o => { o.userData.tokenId = t.id; });
    const rec = { group, body, ring, turn, from: null, to: null, t: 1, data: t, mixer: null };
    const url = t.mini && minis[t.mini] ? assetURL(minis[t.mini]) : null;
    if (url) loadRetry(url, () => toks.get(t.id) === rec, gltf => {   // the standee stays until (unless) it arrives
      const model = cloneSkinned(gltf.scene);
      model.traverse(o => { if (o.isMesh) o.castShadow = true; o.userData.tokenId = t.id; });
      model.position.y = 0.05;
      group.remove(rec.body); group.add(model); rec.body = model;
      if (gltf.animations?.length) {
        rec.mixer = new THREE.AnimationMixer(model);
        const clip = gltf.animations.find(a => /idle/i.test(a.name)) || gltf.animations[0];
        rec.mixer.clipAction(clip).play();
        rec.walk = gltf.animations.find(a => /walk|run/i.test(a.name));
      }
    });
    return rec;
  }

  const place = (t, v = new THREE.Vector3()) => { const n = SIZES[t.size] || 1; return v.set(t.x + n / 2, 0, t.y + n / 2); };

  function hpState(state, t) {
    if (t.kind === "pc") { const s = state.sheets?.[t.name]; return !s ? "ok" : s.hp <= 0 ? "down" : s.hp <= s.max_hp / 2 ? "hurt" : "ok"; }
    const m = (state.monsters || []).find(x => x.name === t.name);
    return !m ? "ok" : m.hp <= 0 ? "down" : m.hp <= m.max_hp / 2 ? "hurt" : "ok";
  }

  function update(state) {
    const map = state && state.map; if (!map) return;
    setLocation(map);
    const ini = state.initiative, now = ini && ini.active && ini.order.length && !ini.pending.length ? ini.order[ini.turn].name : null;
    const seen = new Set();
    for (const t of Object.values(map.tokens)) {
      seen.add(t.id);
      let r = toks.get(t.id);
      if (!r || r.data.size !== t.size || r.data.mini !== t.mini) {
        if (r) tokG.remove(r.group);
        r = makeToken(t, state.minis || {}); toks.set(t.id, r); tokG.add(r.group);
        place(t, r.group.position); r.to = r.group.position.clone();
      }
      const target = place(t);
      if (!r.to.equals(target)) {                        // a move: walk there over ~0.45 s, facing the way
        r.from = r.group.position.clone(); r.to = target; r.t = 0;
        const d = target.clone().sub(r.from);
        if (d.lengthSq() > 1e-6) r.group.userData.face = Math.atan2(d.x, d.z);
      }
      r.data = t;
      const hs = hpState(state, t);
      r.ring.material.color.setHex(HP[hs]);
      r.group.userData.down = hs === "down";
      r.turn.visible = t.name === now;
    }
    for (const [id, r] of toks) if (!seen.has(id)) { tokG.remove(r.group); toks.delete(id); }
  }

  const _q = new THREE.Vector3();
  function tick(dt, camera) {
    const time = performance.now() / 1000;
    for (const r of toks.values()) {
      if (r.t < 1) { r.t = Math.min(1, r.t + dt / 0.45); r.group.position.lerpVectors(r.from, r.to, r.t * r.t * (3 - 2 * r.t)); }
      if (r.group.userData.face != null && r.body && !r.body.userData.billboard) r.body.rotation.y = r.group.userData.face;
      r.mixer?.update(dt);
      r.turn.material.opacity = 0.55 + 0.45 * Math.sin(time * 4);
      if (r.body) r.body.rotation.z = r.group.userData.down ? Math.PI / 2.2 : 0;
      if (camera && r.body?.userData.billboard) {         // standees face the viewer (around the vertical only)
        camera.getWorldPosition(_q); r.body.parent.worldToLocal(_q);
        r.body.rotation.y = Math.atan2(_q.x - r.body.position.x, _q.z - r.body.position.z);
      }
    }
  }

  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), _p = new THREE.Vector3(), _inv = new THREE.Matrix4();
  function squareAt(raycaster) {                         // the grid square under a ray (in root's own frame), or null
    root.updateMatrixWorld();
    const ray = raycaster.ray.clone().applyMatrix4(_inv.copy(root.matrixWorld).invert());
    if (!ray.intersectPlane(plane, _p)) return null;
    const x = Math.floor(_p.x), y = Math.floor(_p.z);
    return x >= 0 && y >= 0 && x < W && y < H ? [x, y] : null;
  }
  function tokenAt(raycaster) {
    const hit = raycaster.intersectObjects(tokG.children, true)[0];
    return hit ? hit.object.userData.tokenId || null : null;
  }

  const debug = () => Object.fromEntries([...toks].map(([id, r]) => [id, { to: [r.to.x, r.to.z], at: [r.group.position.x, r.group.position.z],
    mini: !r.body?.userData.billboard }]));
  return { root, update, tick, squareAt, tokenAt, size: () => ({ w: W, h: H }), debug };
}
