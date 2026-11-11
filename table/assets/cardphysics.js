// Card physics for the 3D board: every card is a small rigid body pulled toward where the game says it lies.
//
//   position: a horizontal spring toward the target (slightly under-damped: it overshoots a hair and settles), gravity, and the
//             felt as a floor with a bounce (restitution) and friction. A card that must travel hops: an upward kick sized by
//             the distance, so it arcs instead of sliding.
//   rotation: an angular spring toward the target orientation with its own damping, so a tap swings the card round with
//             momentum and a little overshoot; a FLIP (face down <-> up) spins it about its own long axis while it hops.
//   entropy:  every card rests a few millimetres and a degree or two off its exact slot (seeded by its id: stable, never
//             jittering), and a landing adds a small random wobble. Piles look handled, not printed.
//   sleep:    a card that has settled snaps exactly onto its (jittered) target and costs nothing until the next change.
//
// No three.js in here: plain arrays, so node can test it (table/tests/cardphysics_test.cjs). Units are the table's (metres-ish:
// a card is ~0.086 x 0.12).
(function () {
  "use strict";

  const P = {
    k: 70,            // horizontal spring stiffness (1/s^2)
    zeta: 0.62,       // horizontal damping ratio (<1: a hint of overshoot)
    g: 7.5,           // gravity (gentler than 9.8 at this scale: you can see the arc)
    restitution: 0.32,
    friction: 6.0,    // horizontal velocity loss per second while touching the felt
    kr: 90,           // angular spring stiffness
    zetaR: 0.55,      // angular damping ratio
    hopPerMetre: 0.9, // hop height per metre travelled
    hopMax: 0.07,
    jitterPos: 0.0035,
    jitterYaw: 0.045,
    sleepV: 0.004, sleepW: 0.02, sleepErr: 0.0015,
    maxDt: 1 / 20, subDt: 1 / 180,
  };

  // quaternions as [x, y, z, w]
  const qmul = (a, b) => [
    a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
    a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
    a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
    a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]];
  const qconj = a => [-a[0], -a[1], -a[2], a[3]];
  const qnorm = a => { const n = Math.hypot(a[0], a[1], a[2], a[3]) || 1; return [a[0] / n, a[1] / n, a[2] / n, a[3] / n]; };
  const qaxis = (ax, ang) => { const s = Math.sin(ang / 2), n = Math.hypot(ax[0], ax[1], ax[2]) || 1; return [ax[0] / n * s, ax[1] / n * s, ax[2] / n * s, Math.cos(ang / 2)]; };
  const rotv = (q, v) => { const r = qmul(qmul(q, [v[0], v[1], v[2], 0]), qconj(q)); return [r[0], r[1], r[2]]; };

  // seeded random in [-1, 1): the same card always rests the same way
  function rnd(seed, salt) {
    let h = 2166136261 ^ salt;
    const s = String(seed);
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    h ^= h >>> 13; h = Math.imul(h, 0x5bd1e995); h ^= h >>> 15;
    return ((h >>> 0) / 4294967296) * 2 - 1;
  }

  // target orientation: yaw about Y (the table's up), then face down = half a turn about the card's long (local Z) axis
  function orient(yaw, faceDown) {
    const qy = qaxis([0, 1, 0], yaw);
    return faceDown ? qmul(qy, qaxis([0, 0, 1], Math.PI)) : qy;
  }

  function makeBody(id, { p, yaw = 0, faceDown = false, floor = 0 } = {}) {
    const q = orient(yaw, faceDown);
    return { id, p: p.slice(), v: [0, 0, 0], q, w: [0, 0, 0], tp: p.slice(), tq: q.slice(), yaw, faceDown, floor,
             asleep: false, landed: true };
  }

  // a new place for the card (from the game state). Entropy is applied here, once per target.
  function setTarget(b, { p, yaw = 0, faceDown = false, floor = p[1], jitter = true }) {
    const j = jitter ? [rnd(b.id, 1) * P.jitterPos, 0, rnd(b.id, 2) * P.jitterPos] : [0, 0, 0];
    const tp = [p[0] + j[0], p[1], p[2] + j[2]];
    const tq = orient(yaw + (jitter ? rnd(b.id, 3) * P.jitterYaw : 0), faceDown);
    const moved = Math.hypot(tp[0] - b.tp[0], tp[2] - b.tp[2]);
    const flipped = faceDown !== b.faceDown;
    b.tp = tp; b.tq = tq; b.floor = floor; b.yaw = yaw;
    if (moved > 1e-4 || flipped || qdist(b.q, tq) > 1e-3) b.asleep = false;
    // travel: hop so it arcs; flip: hop higher and spin about the card's own long axis
    const dist = Math.hypot(tp[0] - b.p[0], tp[2] - b.p[2]);
    let hop = Math.min(P.hopMax, dist * P.hopPerMetre);
    if (flipped) {
      hop = Math.max(hop, 0.045);
      const axis = rotv(b.q, [0, 0, 1]);                       // the card's long axis, in world space
      const spin = Math.PI / 0.42;                              // about half a turn in the hop's air time
      const sgn = rnd(b.id, 4 + (b.faceDown ? 1 : 0)) < 0 ? -1 : 1;
      b.w = [b.w[0] + axis[0] * spin * sgn, b.w[1] + axis[1] * spin * sgn, b.w[2] + axis[2] * spin * sgn];
    }
    if (hop > 0.002 && b.p[1] <= b.floor + 0.002) b.v[1] = Math.max(b.v[1], Math.sqrt(2 * P.g * hop));
    b.faceDown = faceDown;
    return b;
  }

  function qdist(a, b) { const d = Math.abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]); return 2 * Math.acos(Math.min(1, d)); }

  function sub(b, dt) {
    // horizontal spring + damping
    const wn = Math.sqrt(P.k), c = 2 * P.zeta * wn;
    for (const i of [0, 2]) b.v[i] += (P.k * (b.tp[i] - b.p[i]) - c * b.v[i]) * dt;
    // vertical: gravity, the floor, and a soft pull when the target is above the floor (a card resting on a stack)
    const floor = Math.max(b.floor, b.tp[1]);
    b.v[1] -= P.g * dt;
    for (let i = 0; i < 3; i++) b.p[i] += b.v[i] * dt;
    if (b.p[1] <= floor) {
      b.p[1] = floor;
      if (b.v[1] < -0.05) {                                     // a bounce, and a small wobble from the impact (entropy)
        b.v[1] = -b.v[1] * P.restitution;
        const kick = Math.min(1.5, Math.abs(b.v[1]) * 6);
        b.w[0] += rnd(b.id + ":" + b.p[0].toFixed(3), 5) * kick; b.w[2] += rnd(b.id + ":" + b.p[2].toFixed(3), 6) * kick;
      } else b.v[1] = 0;
      const f = Math.max(0, 1 - P.friction * dt);
      b.v[0] *= f; b.v[2] *= f;
    }
    // angular spring toward the target orientation (shortest way round, unless a flip spin is carrying it)
    let e = qmul(b.tq, qconj(b.q));
    if (e[3] < 0) e = [-e[0], -e[1], -e[2], -e[3]];
    const ang = 2 * Math.acos(Math.min(1, e[3])), s = Math.sqrt(Math.max(1e-12, 1 - e[3] * e[3]));
    const ax = ang > 1e-6 ? [e[0] / s, e[1] / s, e[2] / s] : [0, 0, 0];
    const wr = Math.sqrt(P.kr), cr = 2 * P.zetaR * wr;
    for (let i = 0; i < 3; i++) b.w[i] += (P.kr * ax[i] * ang - cr * b.w[i]) * dt;
    const dq = [b.w[0] * dt / 2, b.w[1] * dt / 2, b.w[2] * dt / 2, 0];
    const qd = qmul(dq, b.q);
    b.q = qnorm([b.q[0] + qd[0], b.q[1] + qd[1], b.q[2] + qd[2], b.q[3] + qd[3]]);
  }

  function step(b, dt) {
    if (b.asleep) return false;
    let t = Math.min(dt, P.maxDt);
    while (t > 1e-9) { const h = Math.min(P.subDt, t); sub(b, h); t -= h; }
    const err = Math.hypot(b.tp[0] - b.p[0], b.tp[2] - b.p[2], Math.max(b.floor, b.tp[1]) - b.p[1]);
    if (err < P.sleepErr && Math.hypot(...b.v) < P.sleepV && Math.hypot(...b.w) < P.sleepW && qdist(b.q, b.tq) < 0.01) {
      b.p = [b.tp[0], Math.max(b.floor, b.tp[1]), b.tp[2]]; b.q = b.tq.slice(); b.v = [0, 0, 0]; b.w = [0, 0, 0]; b.asleep = true;
    }
    return true;
  }

  const api = { P, makeBody, setTarget, step, orient, qdist, rotv, rnd };
  if (typeof window !== "undefined") window.CardPhysics = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
