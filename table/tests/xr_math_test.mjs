// The headset's movement math (assets/xr-controls.js), checked against three.js in Node — the turn direction and
// "go to my seat" are exactly the things that are hard to see are wrong until you're in the headset.
//   node table/tests/xr_math_test.mjs
import * as THREE from "../vendor/three.module.min.js";
import { moveOffset, turnOffset, yawOf, yawToward } from "../assets/xr-controls.js";

let failed = 0;
const check = (name, ok, detail = "") => { if (!ok) failed++; console.log((ok ? "  ✓ " : "  ✗ ") + name + (ok ? "" : " — " + detail)); };
const near = (a, b, e = 1e-6) => Math.abs(a - b) < e;
const wrap = a => Math.atan2(Math.sin(a), Math.cos(a));

// a viewer: position + yaw, as a matrix in the reference space
const pose = (p, yaw) => new THREE.Matrix4().compose(p, new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), yaw), new THREE.Vector3(1, 1, 1));
const T = ({ pos, quat }) => new THREE.Matrix4().compose(pos, quat || new THREE.Quaternion(), new THREE.Vector3(1, 1, 1));
// WebXR: after getOffsetReferenceSpace(T) the same physical viewer is seen at T⁻¹·P
const apply = (P, off) => T(off).invert().multiply(P);
const posOf = M => new THREE.Vector3().setFromMatrixPosition(M);
const yawOfPose = M => yawOf(new THREE.Vector3(0, 0, -1).transformDirection(M));

console.log("conventions");
const cam = new THREE.PerspectiveCamera(); cam.rotation.y = Math.PI / 2; cam.updateMatrixWorld();
const d = new THREE.Vector3(); cam.getWorldDirection(d);
check("three.js rotation.y +90° looks down −X, and yawOf calls that +90° (left)", near(yawOf(d), Math.PI / 2), yawOf(d));
check("looking down −Z is yaw 0", near(yawOf(new THREE.Vector3(0, 0, -1)), 0));
check("yawToward: from the seat at +Z toward the centre is yaw 0", near(yawToward({ x: 0, z: 1.15 }, { x: 0, z: 0 }), 0));

console.log("turning");
const h = new THREE.Vector3(1, 1.6, 2), P = pose(h, 0.4);
let P2 = apply(P, turnOffset(THREE, h, Math.PI / 6));
check("turn +30° turns the viewer LEFT by 30°", near(wrap(yawOfPose(P2) - 0.4), Math.PI / 6, 1e-5), yawOfPose(P2));
check("…about the head: the head doesn't move", posOf(P2).distanceTo(h) < 1e-6, posOf(P2).toArray());
P2 = apply(P, turnOffset(THREE, h, -Math.PI / 6));
check("turn −30° turns the viewer RIGHT by 30°", near(wrap(yawOfPose(P2) - 0.4), -Math.PI / 6, 1e-5), yawOfPose(P2));

console.log("moving");
const v = new THREE.Vector3(0.5, 0, -1);
P2 = apply(P, moveOffset(THREE, v));
check("move by v puts the viewer at head + v", posOf(P2).distanceTo(h.clone().add(v)) < 1e-6, posOf(P2).toArray());
check("…without turning them", near(yawOfPose(P2), 0.4, 1e-6));

console.log("go to my seat");
const seat = new THREE.Vector3(0, 0, 1.15), centre = new THREE.Vector3(0, 0, 0);
let G = pose(new THREE.Vector3(2.5, 1.6, 3), 1.0);
G = apply(G, turnOffset(THREE, posOf(G), yawToward(seat, centre) - yawOfPose(G)));
const h1 = posOf(G);
G = apply(G, moveOffset(THREE, new THREE.Vector3(seat.x - h1.x, 0, seat.z - h1.z)));
check("ends at the seat (x, z)", near(posOf(G).x, seat.x, 1e-6) && near(posOf(G).z, seat.z, 1e-6), posOf(G).toArray());
check("keeps the head height", near(posOf(G).y, 1.6, 1e-6));
check("faces the table's centre", near(wrap(yawOfPose(G) - yawToward(seat, centre)), 0, 1e-5), yawOfPose(G));

console.log(failed ? `${failed} FAILED` : "ok");
process.exit(failed ? 1 : 0);
