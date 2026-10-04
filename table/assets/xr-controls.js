// In-headset controls for /xr (docs/xr-play-format.md P1). The page's HTML overlay doesn't show inside an
// immersive session on Quest, so everything a player needs is in the scene:
//
//   THE PANEL   a floating tablet: whose turn and step, ◂ BACK, NEXT ▸ (hold ½ s, so a stray pinch can't pass),
//               YOUR life ± (only your own: people own their numbers), movement buttons, and the rules text of
//               whatever card you point at. Point with a controller or a hand ray; trigger or pinch presses.
//               It stays where it is; look away for a while and it comes back to your left. A/X brings it now,
//               B/Y hides or shows it.
//   VR          left stick walks (where you look), right stick snap-turns 30° (left/right) and rises/sinks
//               (up/down). Grip — or pinch on empty space — grabs the world: pull to move yourself.
//               "Go to my seat" puts you in your chair facing the table.
//   AR          grab the table (grip, or pinch on empty space) to slide it; left stick slides it, right stick
//               turns it (left/right) and raises it (up/down). "Re-place" aims the reticle again;
//               "Mini / Life-size" switches between a tabletop model and avatars in the real chairs.
//
// Movement in VR moves the reference space (getOffsetReferenceSpace), never the scene, so the table, cards and
// hit-tests keep their coordinates.
// ── the movement math, pure so it can be tested (tests/xr_math_test.mjs) ────────────────────────────
// WebXR: getOffsetReferenceSpace(T) puts the new origin at T in the old space, so a pose seen in the new space
// is T⁻¹·(old pose). To move the VIEWER by +v, T = translate(−v). To turn the viewer by a (left is +, as in
// three.js yaw) about the head h, T = translate(h)·rotY(−a)·translate(−h).
export function moveOffset(THREE, v) {
  return { pos: v.clone().negate(), quat: null };
}
export function turnOffset(THREE, head, a) {
  const r = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -a);
  return { pos: head.clone().sub(head.clone().applyQuaternion(r)), quat: r };
}
// a direction's yaw (0 = looking down −Z, + = turned left) and the yaw that faces from a point toward another
export const yawOf = d => Math.atan2(-d.x, -d.z);
export const yawToward = (from, to) => Math.atan2(-(to.x - from.x), -(to.z - from.z));

export function installXRControls(o) {
  const { THREE, renderer, scene, camera, table, board } = o;
  const V = () => new THREE.Vector3(), Q = () => new THREE.Quaternion();
  const HOLD_MS = 500, SPEED = 1.4, DEAD = 0.25;
  let mode = null;                                   // "ar" | "vr" while presenting
  let visible = true, inspect = null, flash = null, lastDraw = "";

  // ── the panel: a canvas texture on a plane, 48 × 30 cm ─────────────────────────────────────────
  const W = 1024, H = 640, cv = document.createElement("canvas"); cv.width = W; cv.height = H;
  const g = cv.getContext("2d"), tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  const panel = new THREE.Mesh(new THREE.PlaneGeometry(0.48, 0.30),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthTest: false }));
  panel.renderOrder = 20; panel.visible = false; scene.add(panel);
  let buttons = [], hovered = null, pressed = null;

  function layout() {
    const me = o.mySeat(), ph = o.phase() || {}, human = ph.player && !(ph.ai || []).includes(ph.player);
    const b = [];
    const add = (id, label, x, y, w, h, extra = {}) => b.push({ id, label, x, y, w, h, ...extra });
    add("back", "◂ BACK", 24, 112, 220, 96, { off: !ph.player });
    add("next", o.nextLabel(), 260, 112, 740, 96, { hold: true, main: true, off: !(human || !ph.player) });
    if (me && o.lifeOf(me) != null) {
      add("life-1", "− 1", 24, 222, 200, 84); add("life+1", "+ 1", 800, 222, 200, 84);
      add("life-label", `${me} · ${o.lifeOf(me)} life`, 240, 222, 544, 84, { kind: "label" });
    } else add("claim", "👤 Claim your seat (top-right 👤 before entering) to pass and change life", 24, 222, 976, 84, { kind: "label" });
    if (mode === "vr") {
      add("turnL", "⟲ 30°", 24, 320, 200, 84); add("seat", "Go to my seat", 240, 320, 544, 84); add("turnR", "30° ⟳", 800, 320, 200, 84);
    } else {
      add("replace", "Re-place", 24, 320, 230, 84); add("size", table.scale.x > 0.5 ? "Mini table" : "Life-size", 270, 320, 300, 84);
      add("rotL", "⟲", 586, 320, 200, 84); add("rotR", "⟳", 800, 320, 200, 84);
    }
    return b;
  }

  function roundRect(x, y, w, h, r) { g.beginPath(); g.roundRect(x, y, w, h, r); }
  function wrap(text, x, y, maxW, lh, maxLines) {
    const words = String(text || "").split(/\s+/); let line = "", n = 0;
    for (const w of words) {
      const t = line ? line + " " + w : w;
      if (g.measureText(t).width > maxW && line) { g.fillText(line, x, y + n * lh); line = w; if (++n >= maxLines) return; }
      else line = t;
    }
    if (line && n < maxLines) g.fillText(line, x, y + n * lh);
  }

  function draw(now) {
    buttons = layout();
    const ph = o.phase() || {}, waiting = (ph.waiting || []).length && ph.seconds_left > 0;
    const prog = pressed?.btn.hold ? Math.min(1, (now - pressed.t0) / HOLD_MS) : 0;
    const key = JSON.stringify([buttons.map(b => b.label + b.off), hovered?.id, prog.toFixed(1), ph.player, ph.step, waiting, inspect?.name, flash]);
    if (key === lastDraw) return; lastDraw = key;
    g.clearRect(0, 0, W, H);
    roundRect(4, 4, W - 8, H - 8, 36); g.fillStyle = "rgba(6,12,16,.92)"; g.fill();
    g.lineWidth = 4; g.strokeStyle = "#d9b46a"; g.stroke();
    g.fillStyle = "#f4efe6"; g.font = "bold 44px system-ui"; g.textBaseline = "middle";
    g.fillText(ph.player ? `${ph.player} · ${ph.step}` : "Game not started", 32, 58);
    if (waiting || flash) { g.font = "32px system-ui"; g.fillStyle = "#f0a868"; g.textAlign = "right";
      g.fillText(flash || `waiting on ${ph.waiting.join(", ")} ${Math.ceil(ph.seconds_left)}s`, W - 32, 58); g.textAlign = "left"; }
    for (const b of buttons) {
      roundRect(b.x, b.y, b.w, b.h, 22);
      g.fillStyle = b.kind === "label" ? "rgba(255,255,255,.06)" : b.main ? "#f0a868" : "#10242d"; g.fill();
      if (b.off) { g.fillStyle = "rgba(0,0,0,.5)"; g.fill(); }
      if (b.main && prog > 0) { g.save(); g.clip(); g.fillStyle = "#3fd0c9"; g.fillRect(b.x, b.y, b.w * prog, b.h); g.restore(); }
      if (hovered === b && b.kind !== "label" && !b.off) { g.lineWidth = 6; g.strokeStyle = "#3fd0c9"; g.stroke(); }
      g.fillStyle = b.main ? "#140d06" : "#f4efe6"; g.font = `bold ${b.main ? 46 : b.kind === "label" ? 30 : 38}px system-ui`;
      g.textAlign = "center"; g.fillText(b.label, b.x + b.w / 2, b.y + b.h / 2 + 2); g.textAlign = "left";
    }
    if (inspect) {
      g.fillStyle = "#f4dc9b"; g.font = "bold 36px system-ui";
      g.fillText(inspect.face_down ? `A face-down ${inspect.pt || "2/2"}` : `${inspect.name}  ${inspect.cost || ""}`, 32, 446);
      g.fillStyle = "#cfe3e3"; g.font = "30px system-ui";
      const body = inspect.face_down ? (inspect.how || "") : [inspect.type, inspect.pt, inspect.tapped ? "tapped" : "",
        inspect.counters ? `${inspect.counters} counter(s)` : ""].filter(Boolean).join(" · ") + "  " + (inspect.text || "");
      wrap(body, 32, 492, W - 64, 38, 4);
    } else {
      g.fillStyle = "#8fa3a3"; g.font = "28px system-ui";
      g.fillText(mode === "vr" ? "Point at a card to read it · grip or pinch empty space to move · sticks walk and turn"
        : "Point at a card to read it · grip or pinch empty space to drag the table", 32, 470);
    }
    tex.needsUpdate = true;
  }

  // ── pointing ───────────────────────────────────────────────────────────────────────────────────
  const ray = new THREE.Raycaster(), hands = [];
  for (let i = 0; i < 2; i++) {
    const c = renderer.xr.getController(i);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([V(), new THREE.Vector3(0, 0, -1)]),
      new THREE.LineBasicMaterial({ color: 0x3fd0c9, transparent: true, opacity: 0.8 }));
    line.scale.z = 3; c.add(line);
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.008), new THREE.MeshBasicMaterial({ color: 0x3fd0c9 })); dot.visible = false; scene.add(dot);
    const h = { c, line, dot, src: null, grab: null, turned: false, btns: [] };
    c.addEventListener("connected", e => { h.src = e.data; line.visible = e.data.targetRayMode === "tracked-pointer"; });
    c.addEventListener("disconnected", () => { h.src = null; });
    c.addEventListener("selectstart", () => start(h));
    c.addEventListener("selectend", () => end(h));
    c.addEventListener("squeezestart", () => { if (!pressed) grabStart(h); });
    c.addEventListener("squeezeend", () => (h.grab = null));
    scene.add(c); hands.push(h);
  }
  const aim = h => { h.c.updateMatrixWorld(); ray.ray.origin.setFromMatrixPosition(h.c.matrixWorld);
    ray.ray.direction.set(0, 0, -1).applyQuaternion(h.c.getWorldQuaternion(Q())).normalize(); return ray; };
  function panelHit(h) {
    if (!panel.visible) return null;
    const hit = aim(h).intersectObject(panel)[0]; if (!hit) return null;
    const px = hit.uv.x * W, py = (1 - hit.uv.y) * H;
    return { dist: hit.distance, point: hit.point, btn: buttons.find(b => px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h) || null };
  }
  function start(h) {
    const p = panelHit(h);
    if (p) { o.consume(); if (p.btn && p.btn.kind !== "label" && !p.btn.off) pressed = { btn: p.btn, t0: performance.now(), h, fired: false }; return; }
    if (board.pick(aim(h))) { o.consume(); return; }            // pointing at a card: the panel shows it
    if (o.placed() || mode === "vr") { o.consume(); grabStart(h); }
  }
  function end(h) {
    if (pressed?.h === h) {
      const held = performance.now() - pressed.t0;
      if (!pressed.btn.hold) fire(pressed.btn.id);
      else if (!pressed.fired && held < HOLD_MS) say("hold NEXT to pass");
      pressed = null;
    }
    if (h.grab && !h.src?.gamepad?.buttons?.[1]?.pressed) h.grab = null;
  }
  function grabStart(h) { h.c.updateMatrixWorld(); h.grab = { prev: V().setFromMatrixPosition(h.c.matrixWorld) }; }
  function say(t) { flash = t; setTimeout(() => { if (flash === t) flash = null; }, 1600); }

  // ── what the buttons do ────────────────────────────────────────────────────────────────────────
  async function fire(id) {
    const me = o.mySeat();
    if (id === "next") return o.next();
    if (id === "back") return o.back();
    if (id === "life-1" || id === "life+1") { if (me) await o.life(id === "life+1" ? 1 : -1); return; }
    if (id === "turnL") return turnBy(Math.PI / 6);
    if (id === "turnR") return turnBy(-Math.PI / 6);
    if (id === "seat") return goToSeat();
    if (id === "replace") return o.replace();
    if (id === "size") { const life = table.scale.x < 0.5; table.scale.setScalar(life ? 1 : 0.14); o.setLifeSize?.(life); return; }
    if (id === "rotL") return table.rotateY(Math.PI / 12);
    if (id === "rotR") return table.rotateY(-Math.PI / 12);
  }

  // ── moving yourself (VR): offsets of the reference space ───────────────────────────────────────
  const later = [];                                  // work for a coming XR frame (window rAF can pause in a session)
  function offset(pos, quat) {
    const cur = renderer.xr.getReferenceSpace(); if (!cur) return;
    renderer.xr.setReferenceSpace(cur.getOffsetReferenceSpace(new XRRigidTransform(
      { x: pos.x, y: pos.y, z: pos.z, w: 1 }, quat ? { x: quat.x, y: quat.y, z: quat.z, w: quat.w } : undefined)));
  }
  const head = () => { const xc = renderer.xr.getCamera(); xc.updateMatrixWorld(); return V().setFromMatrixPosition(xc.matrixWorld); };
  const headYaw = () => { const d = V(); renderer.xr.getCamera().getWorldDirection(d); return yawOf(d); };
  function moveBy(v) { const t = moveOffset(THREE, v); offset(t.pos); }      // the viewer moves by +v in the world
  function turnBy(a) { const t = turnOffset(THREE, head(), a); offset(t.pos, t.quat); }   // turns by a (left +) about the head
  function goToSeat() {
    const me = o.mySeat(), holder = o.seatHolder(me) || o.seatHolder(o.firstSeat());
    if (!holder) return say("no seat to go to");
    const seat = holder.getWorldPosition(V()), centre = table.getWorldPosition(V());
    turnBy(yawToward(seat, centre) - headYaw());                              // face the centre…
    later.push({ frames: 1, fn: () => { const h = head(); moveBy(V().set(seat.x - h.x, 0, seat.z - h.z)); } });   // …then stand at the seat
  }

  // ── each frame ────────────────────────────────────────────────────────────────────────────────
  let offView = 0, lastFrame = performance.now();
  function placePanel() {
    const h = head(), yaw = headYaw();
    const fwd = V().set(-Math.sin(yaw), 0, -Math.cos(yaw)), left = V().set(-Math.cos(yaw), 0, Math.sin(yaw));
    panel.position.copy(h).addScaledVector(fwd, 0.55).addScaledVector(left, 0.22); panel.position.y -= 0.32;
    panel.lookAt(h); offView = 0;
  }
  function update(frame) {
    if (!renderer.xr.isPresenting) return;
    const now = performance.now(), dt = Math.min(0.05, (now - lastFrame) / 1000); lastFrame = now;
    for (let i = later.length - 1; i >= 0; i--) if (later[i].frames-- <= 0) later.splice(i, 1)[0].fn();
    panel.visible = visible;
    // pointing: ray length, hover, card under the ray
    let newHover = null, card = null;
    for (const h of hands) {
      if (!h.src) { h.dot.visible = false; continue; }
      const p = panelHit(h);
      if (p) { newHover = newHover || p.btn; h.line.scale.z = p.dist; h.dot.position.copy(p.point); h.dot.visible = true; continue; }
      const c = board.pick(aim(h));
      if (c) card = card || c;
      h.line.scale.z = 3; h.dot.visible = false;
    }
    hovered = newHover?.kind === "label" ? null : newHover;
    if (card) inspect = card;
    // hold-to-pass: fires once at ½ s while still held
    if (pressed?.btn.hold && !pressed.fired && now - pressed.t0 >= HOLD_MS) { pressed.fired = true; fire(pressed.btn.id); }
    // sticks and buttons
    for (const h of hands) {
      const gp = h.src?.gamepad; if (!gp) continue;
      const x = gp.axes[2] || 0, y = gp.axes[3] || 0, left = h.src.handedness === "left";
      if (gp.buttons[4]?.pressed && !h.btns[4]) placePanel();
      if (gp.buttons[5]?.pressed && !h.btns[5]) visible = !visible;
      h.btns = gp.buttons.map(b => b.pressed);
      if (mode === "vr") {
        if (left && Math.hypot(x, y) > DEAD) {
          const yaw = headYaw(), fwd = V().set(-Math.sin(yaw), 0, -Math.cos(yaw)), right = V().set(Math.cos(yaw), 0, -Math.sin(yaw));
          moveBy(V().addScaledVector(right, x * SPEED * dt).addScaledVector(fwd, -y * SPEED * dt));
        }
        if (!left) {
          if (Math.abs(x) > 0.7 && !h.turned) { h.turned = true; turnBy(x > 0 ? -Math.PI / 6 : Math.PI / 6); }
          if (Math.abs(x) < 0.3) h.turned = false;
          if (Math.abs(y) > DEAD && Math.abs(x) < 0.5) moveBy(V().set(0, -y * 0.8 * dt, 0));
        }
      } else if (o.placed()) {
        if (left && Math.hypot(x, y) > DEAD) {
          const yaw = headYaw(), fwd = V().set(-Math.sin(yaw), 0, -Math.cos(yaw)), right = V().set(Math.cos(yaw), 0, -Math.sin(yaw));
          table.position.addScaledVector(right, x * 0.5 * dt).addScaledVector(fwd, -y * 0.5 * dt);
        }
        if (!left) { if (Math.abs(x) > DEAD) table.rotateY(-x * 1.2 * dt); if (Math.abs(y) > DEAD) table.position.y -= y * 0.25 * dt; }
      }
    }
    // grabbing: pull the world toward you (VR), or drag the table (AR)
    for (const h of hands) {
      if (!h.grab) continue;
      h.c.updateMatrixWorld();
      const p = V().setFromMatrixPosition(h.c.matrixWorld), d = p.clone().sub(h.grab.prev);
      if (mode === "vr") { moveBy(d.clone().negate()); h.grab.prev = p.clone().sub(d); }
      else { table.position.x += d.x; table.position.z += d.z; table.position.y += d.y; h.grab.prev = p; }
    }
    // the panel comes back if it has been out of view for 1.5 s
    const toPanel = panel.position.clone().sub(head()).normalize(), look = V(); renderer.xr.getCamera().getWorldDirection(look);
    offView = toPanel.dot(look) < 0.25 ? offView + dt : 0;
    if (offView > 1.5) placePanel();
    draw(now);
  }

  renderer.xr.addEventListener("sessionstart", () => {
    const s = renderer.xr.getSession();
    mode = s.environmentBlendMode && s.environmentBlendMode !== "opaque" ? "ar" : "vr";
    visible = true; inspect = null; lastDraw = "";
    later.push({ frames: 20, fn: placePanel });                   // after the first poses have arrived
  });
  renderer.xr.addEventListener("sessionend", () => { mode = null; panel.visible = false; for (const h of hands) h.grab = null; });
  return { update };
}
