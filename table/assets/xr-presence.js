// Remote players in the headset: their head and hands, where they really are around the table
// (docs/xr-play-format.md P2). Each headset sends its own head and controllers ~15×/s over the room's presence
// WebSocket (/api/xr/presence, cloud rooms only) in TABLE coordinates, so every viewer places them around their
// own table, whatever its calibration. Only at life size: from a mini tabletop you'd be a giant to everyone else.

// A world pose → [x, y, z, qx, qy, qz, qw] in the table's own coordinates (pure, so it can be tested).
export function toTablePose(THREE, tableMatrixWorld, worldMatrix) {
  const m = new THREE.Matrix4().copy(tableMatrixWorld).invert().multiply(worldMatrix);
  const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
  m.decompose(p, q, s);
  return [p.x, p.y, p.z, q.x, q.y, q.z, q.w].map(n => Math.round(n * 10000) / 10000);
}

export function installPresence(o) {
  const { THREE, renderer, scene, table, mySeat, seatKey, accentOf, label, onLive } = o;
  const remotes = {};                                  // seat → {group, head, hands, tag, target, last}
  let ws = null, ready = false, backoff = 1000, lastSend = 0, stopped = false;
  const grips = [0, 1].map(i => {
    const g = renderer.xr.getControllerGrip(i);
    g.addEventListener("connected", e => (g.userData.hand = e.data.handedness));
    g.addEventListener("disconnected", () => (g.userData.hand = null));
    scene.add(g);                                     // in the scene, so three.js keeps its pose current
    return g;
  });

  function connect() {
    if (stopped) return;
    if (!seatKey()) { setTimeout(connect, 3000); return; }       // nothing to send or see until a seat is claimed
    try { ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/xr/presence"); }
    catch { setTimeout(connect, backoff = Math.min(backoff * 2, 15000)); return; }
    ws.onopen = () => ws.send(JSON.stringify({ t: "auth", key: seatKey() }));
    ws.onmessage = e => {
      let d; try { d = JSON.parse(e.data); } catch { return; }
      if (d.t === "hello") { ready = true; backoff = 1000; }
      else if (d.t === "pose" && d.seat && d.seat !== mySeat()) upsert(d);
      else if (d.t === "leave") drop(d.seat);
    };
    ws.onclose = () => { ready = false; ws = null; if (!stopped) setTimeout(connect, backoff = Math.min(backoff * 2, 15000)); };
    ws.onerror = () => {};
  }

  function body(seat) {                               // a visor, two hands and a name, in the seat's colour
    const accent = new THREE.Color(accentOf(seat) || "#e6e6e6");
    const g = new THREE.Group();
    const head = new THREE.Group();
    const shell = new THREE.Mesh(new THREE.SphereGeometry(0.11, 20, 14), new THREE.MeshStandardMaterial({ color: 0x1b2328, roughness: 0.6 }));
    const visor = new THREE.Mesh(new THREE.BoxGeometry(0.19, 0.075, 0.07), new THREE.MeshStandardMaterial({ color: 0x0a0f12, emissive: accent, emissiveIntensity: 0.9 }));
    visor.position.set(0, 0.01, -0.085);
    head.add(shell, visor);
    const hand = () => new THREE.Mesh(new THREE.SphereGeometry(0.045, 14, 10), new THREE.MeshStandardMaterial({ color: accent, emissive: accent, emissiveIntensity: 0.4 }));
    const hands = [hand(), hand()];
    const tag = label(seat, "#" + accent.getHexString()); tag.scale.multiplyScalar(0.5);
    g.add(head, ...hands, tag);
    table.add(g);
    return { group: g, head, hands, tag, target: null, last: 0 };
  }
  function upsert(d) {
    const r = remotes[d.seat] ||= body(d.seat);
    r.target = d; r.last = performance.now();
    if (!r.seen) { r.seen = true; r.group.visible = true; onLive?.(d.seat, true); }
  }
  function drop(seat) {
    const r = remotes[seat]; if (!r) return;
    table.remove(r.group); delete remotes[seat]; onLive?.(seat, false);
  }
  const _p = new THREE.Vector3(), _q = new THREE.Quaternion();
  function place(obj, a, k) {                          // ease toward the latest pose (≈ 100 ms)
    if (!a) { obj.visible = false; return; }
    obj.visible = true;
    _p.set(a[0], a[1], a[2]); _q.set(a[3], a[4], a[5], a[6]);
    obj.position.lerp(_p, k); obj.quaternion.slerp(_q, k);
  }

  function update(dt) {
    const now = performance.now(), k = Math.min(1, dt * 12);
    for (const [seat, r] of Object.entries(remotes)) {
      if (now - r.last > 2500) {                       // gone quiet: hide (they took the headset off, or lost the link)
        if (r.group.visible) { r.group.visible = false; r.seen = false; onLive?.(seat, false); }
        continue;
      }
      const t = r.target;
      place(r.head, t.h, k); place(r.hands[0], t.l, k); place(r.hands[1], t.r, k);
      r.tag.position.set(r.head.position.x, r.head.position.y + 0.24, r.head.position.z);
    }
    // send mine: life size only, ~15×/s
    if (!ready || !renderer.xr.isPresenting || Math.abs(table.scale.x - 1) > 0.01 || now - lastSend < 66) return;
    lastSend = now;
    table.updateMatrixWorld();
    const cam = renderer.xr.getCamera(); cam.updateMatrixWorld();
    const msg = { t: "pose", h: toTablePose(THREE, table.matrixWorld, cam.matrixWorld), l: null, r: null,
      mode: renderer.xr.getSession()?.environmentBlendMode === "opaque" ? "vr" : "ar" };
    for (const g of grips) {
      if (!g.userData.hand) continue;
      g.updateMatrixWorld();
      msg[g.userData.hand === "left" ? "l" : "r"] = toTablePose(THREE, table.matrixWorld, g.matrixWorld);
    }
    try { ws?.send(JSON.stringify(msg)); } catch {}
  }

  connect();
  return { update, live: seat => !!remotes[seat]?.group.visible, stop: () => { stopped = true; ws?.close(); } };
}
