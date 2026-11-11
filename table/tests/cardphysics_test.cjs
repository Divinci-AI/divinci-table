// assets/cardphysics.js: cards travel in an arc and settle where the game says (within their entropy), never sink through the felt,
// overshoot a hair (momentum), swing round on a tap, flip over with a hop, bounce when dropped, rest the same way every time
// (seeded), and survive huge frame gaps.        node table/tests/cardphysics_test.cjs
const C = require("../assets/cardphysics.js");
let failed = 0;
const check = (n, ok, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + d}`); if (!ok) failed++; };
const yawOf = q => Math.atan2(2 * (q[3] * q[1] + q[0] * q[2]), 1 - 2 * (q[1] * q[1] + q[2] * q[2]));
const upY = q => C.rotv(q, [0, 1, 0])[1];          // +1 face up, -1 face down
function run(b, secs, each) { const dt = 1 / 60; let t = 0; for (let i = 0; i < secs * 60; i++) { C.step(b, dt); t += dt; if (each) each(b, t); } }

// 1. travel: arcs, overshoots a hair, settles inside its entropy, never below the felt
let b = C.makeBody("card-1", { p: [0, 0, 0] });
C.setTarget(b, { p: [0.3, 0, 0.2] });
let maxY = 0, minY = 0, maxX = 0;
run(b, 4, x => { maxY = Math.max(maxY, x.p[1]); minY = Math.min(minY, x.p[1]); maxX = Math.max(maxX, x.p[0]); });
check("a card travelling 36 cm arcs through the air (a hop)", maxY > 0.02, `max height ${maxY.toFixed(3)}`);
check("it never sinks through the felt", minY >= -1e-9, `min y ${minY}`);
check("it overshoots its slot a hair before settling (momentum)", maxX > b.tp[0] + 0.0005 && maxX < b.tp[0] + 0.06, `max x ${maxX.toFixed(4)} vs ${b.tp[0].toFixed(4)}`);
check("it comes to rest (asleep) within 4 s", b.asleep);
check("…on its slot, within its entropy (±3.5 mm)", Math.abs(b.p[0] - 0.3) <= C.P.jitterPos + 1e-9 && Math.abs(b.p[2] - 0.2) <= C.P.jitterPos + 1e-9,
      `${b.p[0].toFixed(4)}, ${b.p[2].toFixed(4)}`);

// 2. tap: swings round to -90°, past it a little, and back
b = C.makeBody("card-2", { p: [0, 0, 0], yaw: 0 });
C.setTarget(b, { p: [0, 0, 0], yaw: -Math.PI / 2, jitter: false });
let minYaw = 0;
run(b, 3, x => { minYaw = Math.min(minYaw, yawOf(x.q)); });
check("a tap swings the card a quarter turn", Math.abs(yawOf(b.q) + Math.PI / 2) < 0.01, `final yaw ${(yawOf(b.q) * 180 / Math.PI).toFixed(1)}°`);
check("…with momentum: it swings a little past before settling", minYaw < -Math.PI / 2 - 0.02 && minYaw > -Math.PI / 2 - 0.5,
      `furthest ${(minYaw * 180 / Math.PI).toFixed(1)}°`);

// 3. flip: hops, turns over about its long axis, lands face down, stays put
b = C.makeBody("card-3", { p: [0.1, 0, 0.1], yaw: 0.3, faceDown: false });
run(b, 0.5);
C.setTarget(b, { p: [0.1, 0, 0.1], yaw: 0.3, faceDown: true, jitter: false });
let peak = 0, sideways = false;
run(b, 4, x => { peak = Math.max(peak, x.p[1]); if (Math.abs(upY(x.q)) < 0.3) sideways = true; });
check("a flip hops the card up", peak > 0.03, `peak ${peak.toFixed(3)}`);
check("…turns it over through edge-on (a real 3D flip, not a texture swap)", sideways);
check("…and it lands face down, at the same angle on the table", upY(b.q) < -0.99 && C.qdist(b.q, C.orient(0.3, true)) < 0.02 && b.asleep,
      `up·y ${upY(b.q).toFixed(3)} off by ${C.qdist(b.q, C.orient(0.3, true)).toFixed(3)} rad asleep=${b.asleep}`);
C.setTarget(b, { p: [0.1, 0, 0.1], yaw: 0.3, faceDown: false, jitter: false });
run(b, 4);
check("and flips back face up", upY(b.q) > 0.99 && b.asleep, `up·y ${upY(b.q).toFixed(3)}`);

// 4. dropped from above: it bounces
b = C.makeBody("card-4", { p: [0, 0.25, 0] });
C.setTarget(b, { p: [0, 0, 0], jitter: false });
let ups = 0, lastVy = 0;
run(b, 3, x => { if (lastVy < 0 && x.v[1] > 0) ups++; lastVy = x.v[1]; });
check("dropped onto the felt, it bounces", ups >= 1, `bounces ${ups}`);
check("…and settles", b.asleep && Math.abs(b.p[1]) < 1e-6);

// 5. entropy is seeded: the same card rests the same way, different cards differently
const rest = id => { const x = C.makeBody(id, { p: [0, 0, 0] }); C.setTarget(x, { p: [0.2, 0, 0] }); run(x, 4); return x; };
const a1 = rest("same"), a2 = rest("same"), o = rest("other");
check("the same card always comes to rest in the same spot and angle", a1.p.every((v, i) => Math.abs(v - a2.p[i]) < 1e-9) && C.qdist(a1.q, a2.q) < 1e-6);
check("different cards rest a little differently (a handled pile, not a printed one)", Math.hypot(a1.p[0] - o.p[0], a1.p[2] - o.p[2]) > 1e-4 || C.qdist(a1.q, o.q) > 1e-3);

// 6. huge frame gaps (a background tab) never blow up
b = C.makeBody("card-6", { p: [0, 0, 0] });
C.setTarget(b, { p: [0.5, 0, -0.4], yaw: 2, faceDown: true });
for (let i = 0; i < 40; i++) C.step(b, 1.0);
check("one-second frames: no NaN, still settles", [...b.p, ...b.q, ...b.v, ...b.w].every(Number.isFinite) && Math.abs(b.p[0] - 0.5) < 0.01, b.p.map(v => v.toFixed(3)).join(","));

// 7. a sleeping card costs nothing until it is told to move
b = C.makeBody("card-7", { p: [0, 0, 0] }); C.setTarget(b, { p: [0, 0, 0], jitter: false }); run(b, 1);
check("a settled card sleeps (step does no work)", b.asleep && C.step(b, 1 / 60) === false);
console.log(failed ? `\n${failed} FAILED` : "\nall passed");
process.exit(failed ? 1 : 0);
