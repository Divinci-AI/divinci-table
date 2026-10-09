#!/usr/bin/env python3
"""Fit check for the printed parts: run it BEFORE slicing anything (written 2026-10-08 after two defects reached the printer:
a bolt that could not be slid into the swivel, and a jig tray that printed as two pieces).

  python3 hardware/gantry/fit_check.py          # needs openscad (brew install --cask openscad@snapshot)

What it proves, with the real STL files and the real hardware sizes:
  1. every part is ONE connected piece and fits the bed;
  2. every insertion is a free path: the moving hardware is swept along its path through the printed part (OpenSCAD intersection;
     empty = nothing touches), at the nominal size AND grown 0.3 mm (printed holes come out small);
  3. the bolt stack-up leaves thread for the wing nut with the spring on.
Exit status 1 on any FAIL; WARN is a margin under 1 mm. Add a sweep to SWEEPS when a part gets an assembly step.
"""
import math, re, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
STL = HERE / "parts" / "stl"
BED = (390, 390, 390)
GROW = 0.3          # printed holes come out about this much small in total
results = []
checks = []                                         # (level, id, what): id names a check the ledger can list

def report(level, what, detail="", id=None):
    results.append(level); checks.append((level, id, what)); print(f"  {level:4s} {what}" + (f"  ({detail})" if detail else ""))

def triangles(path):
    v = [tuple(round(float(x), 4) for x in m) for m in re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", path.read_text(errors="ignore"))]
    return v

def pieces(v):
    parent = {}
    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for i in range(0, len(v), 3):
        for p in v[i + 1:i + 3]: parent[find(v[i])] = find(p)
    return len({find(x) for x in v})

def collides(scad_body):
    """True if the intersection is non-empty (OpenSCAD says 'empty' otherwise)."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.scad"; f.write_text(scad_body)
        r = subprocess.run(["openscad", "--backend=manifold", "-o", str(Path(d) / "o.stl"), str(f)], capture_output=True, text=True)
        out = r.stdout + r.stderr
        if "empty" in out: return False
        if r.returncode != 0 and "Status" not in out: raise RuntimeError(out[-400:])
        v = triangles(Path(d) / "o.stl") if (Path(d) / "o.stl").exists() else []
        if not v: return False
        # a sheet of zero thickness is a touching face, not an overlap
        zs = [c[2] for c in v]; xs = [c[0] for c in v]; ys = [c[1] for c in v]
        return min(max(zs) - min(zs), max(xs) - min(xs), max(ys) - min(ys)) > 0.01

print("1. pieces and bed")
for f in sorted(STL.glob("*.stl")):
    v = triangles(f); n = pieces(v)
    size = [max(c[i] for c in v) - min(c[i] for c in v) for i in range(3)]
    if f.stem.endswith("_plate"): report("ok" if all(s_ <= b for s_, b in zip(sorted(size)[::-1][:2], BED[:2])) else "FAIL", f.name, f"a plate of {n} separate parts, " + " x ".join(f"{s_:.0f}" for s_ in size) + " mm")
    elif n != 1: report("FAIL", f.name, f"{n} separate pieces")
    elif any(s > b for s, b in zip(sorted(size)[::-1][:2], BED[:2])): report("FAIL", f.name, "bigger than the bed")
    else: report("ok", f.name, "one piece, " + " x ".join(f"{s:.0f}" for s in size) + " mm")

print("2. insertion sweeps (nominal, and hardware grown %.1f mm)" % GROW)
def bolt(x, g):                                    # hex head down in the pocket, thread up; head AF 11.11, thread 6.35, head 3.97
    return (f"translate([{x},0,6.7]) {{ cylinder(d=(11.11+{g})/cos(30), h=3.97, $fn=6); cylinder(d=6.35+{g}, h=3.97+25.4, $fn=48); }}")
for g in (0, GROW):
    bad = [x for x in (24, 18, 12, 6, 0) if collides(f'intersection(){{ import("{STL/"magnet_swivel.stl"}"); {bolt(x, g)} }}')]
    report("ok" if not bad else "FAIL", f"bolt slides into the swivel from +X ({'grown' if g else 'nominal'})", "free at x=24..0" if not bad else f"collides at x={bad}")
for g in (0, GROW):
    m3 = f'translate([0,0,3]) mirror([0,0,1]) {{ cylinder(d1=3.1+{g}, d2=6+{g}, h=0.01); translate([0,0,0]) cylinder(d=3+{g}, h=6); }}'
    # the screw goes in through the top hole: sweep it down the hole from above
    bad = [z for z in (30, 22, 14, 8) if collides(f'intersection(){{ import("{STL/"magnet_swivel.stl"}"); translate([0,0,{z}]) {{ cylinder(d=6.0+{g}, h=3.5); translate([0,0,-4.5]) cylinder(d=3+{g}, h=4.5); }} }}')]
    report("ok" if not bad else "FAIL", f"M3 screw drops down the top hole to the floor ({'grown' if g else 'nominal'})", "" if not bad else f"collides at z={bad}")
tray, bridge = STL / "sleeve_jig_tray.stl", STL / "sleeve_jig_bridge.stl"
il, wall, floor_t, wall_h = 92.5 + 1.6, 2.4, 1.6, 5
hit = collides(f'intersection(){{ import("{tray}"); translate([0,{(il + 2 * wall) / 2},{floor_t + wall_h}]) import("{bridge}"); }}')
report("FAIL" if hit else "ok", "bridge sits on the tray without overlapping it")

print("3. bolt stack-up (1 inch bolt; change BOLT_LEN for 1-1/2 inch)")
BOLT_LEN, HEAD_H, SWIVEL_TOP, POCKET_FLOOR = 25.4, 3.97, 15.1, 6.5
NYLON, BRACKET, NUT = 6.0, 3.2, 5.6
SPRING_SOLID = 6.0                                 # assumed: measure the real spring
thread_end = POCKET_FLOOR + HEAD_H + BOLT_LEN
avail = thread_end - SWIVEL_TOP
need = NYLON + BRACKET + SPRING_SOLID + NUT
margin = round(avail - need, 1)
report("FAIL" if margin < 0 else ("WARN" if margin < 1 else "ok"), "thread reaches the wing nut with the spring on",
       f"{avail:.1f} mm of thread above the swivel, {need:.1f} mm needed with a {SPRING_SOLID:.0f} mm spring: margin {margin + 0:+.1f} mm")


def scad_vars(name):                                # read the numbers at the top of a part, so the check follows the design
    out = {}
    for m in re.finditer(r"^\s*(\w+)\s*=\s*(-?[\d.]+)\s*;", (HERE / "parts" / name).read_text(), re.M): out[m.group(1)] = float(m.group(2))
    return out

print("4. deck box: the magnet reaches the top card's washer; the card behind it cannot follow")
d = scad_vars("deck_box.scad")
wall, clear, card_w, card_l, ct = d["wall"], d["clear"], d["card_w"], d["card_l"], d["card_t"]
d.setdefault("lid_frac", 1 - (clear + card_l / 2 + d["magnet_r"] + 2) / (card_l + 2 * clear))   # derived in the .scad, not a plain number
H = d["floor_t"] + d["spring_room"] + d["follower_t"] + d["deck_n"] * ct
box = STL / "deck_box.stl"
cx, cy = wall + clear + card_w / 2, wall + clear + card_l / 2          # the card's centre = where the washer is (the jig puts it there)
magnet_down = f'intersection(){{ import("{box}"); translate([{cx},{cy},{H}]) cylinder(d=20, h=15, $fn=48); }}'      # the magnet resting on the top card
report("FAIL" if collides(magnet_down) else "ok", "magnet (20 mm) can sit on the top card's centre",
       f"the card's centre is {cy:.1f} mm from the front; the open strip ends at about {wall + 1 + (card_l + 2 * clear) * (1 - d['lid_frac']) - 1:.1f} mm; the magnet needs {cy + 10:.1f} mm")
def card_out(layer, grow=0):                         # a card sliding forward out of the front slot, layer 0 = the top card
    z0 = H - (layer + 1) * ct
    return f'intersection(){{ import("{box}"); union() for (y=[{wall+clear},{wall+clear-20},{wall+clear-45},{-60}]) translate([{wall+clear},y,{z0}]) cube([{card_w},{card_l},{ct}]); }}'
report("ok" if not collides(card_out(0)) else "FAIL", "the top card slides out through the front slot")
report("ok" if collides(card_out(1)) else "FAIL", "the card UNDER it is stopped by the front wall (this one must collide)")

print("5. hand rack: a card goes in from the left, comes out to the left, and the magnet reaches its washer")
r = scad_vars("hand_rack.scad"); rack = STL / "hand_rack.stl"
pitch, sw_, rise = r["pitch"], r["card_w"] + 2 * r["clear"], r["card_t"] + r["gap"] + r["shelf_t"]
def top(k): return r["base_t"] + k * rise
bad_in, bad_out, bad_mag = [], [], []
design = f'rotate([-90,0,0]) import("{rack}")'                     # the STL is turned onto its fence for printing; turn it back
for k in range(int(r["slots"])):
    x_in, y_in = k * pitch + r["clear"], r["wall"] + r["clear"]
    path_in = f'intersection(){{ {design}; union() for (dx=[{-70},{-45},{-20},{0}]) translate([{x_in}+dx,{y_in},{top(k)}+0.4]) cube([{r["card_w"]},{r["card_l"]},{r["card_t"]}]); }}'
    if collides(path_in): bad_in.append(k)
    if k > 0:
        path_out = f'intersection(){{ {design}; union() for (dx=[0,{-15},{-30},{-pitch}]) translate([{x_in}+dx,{y_in},{top(k)}+0.8]) cube([{r["card_w"]},{r["card_l"]},{r["card_t"]}]); }}'
        if collides(path_out): bad_out.append(k)
    mx, my = k * pitch + r["clear"] + r["card_w"] / 2, y_in + r["card_l"] / 2
    for g in (0, GROW):
        if collides(f'intersection(){{ {design}; translate([{mx},{my},{top(k)}+{r["card_t"]}]) cylinder(d=20+{g}, h=15, $fn=48); }}'): bad_mag.append(k); break
report("FAIL" if bad_in else "ok", "card slides into each slot from the left", f"blocked at slots {bad_in}" if bad_in else f"all {int(r['slots'])} slots")
report("FAIL" if bad_out else "ok", "card slides out one pitch to the left over the lower card", f"blocked at slots {bad_out}" if bad_out else "slots 1..6")
report("FAIL" if bad_mag else "ok", "magnet (20 mm, and grown) rests on each card's washer", f"blocked at slots {bad_mag}" if bad_mag else "all slots")


print("6. flip station (README 'Flipping a card'): DESIGN checks. These prove collisions, reach and rigid-body geometry only; whether the friction pivot works is for the bench test")
sys.path.insert(0, str(HERE))
import flip_path as fp
P = fp.Params(); W = fp.waypoints(P); FENCE = STL / "flip_fence.stl"
# --- assumptions that nobody has measured (change them here, and bench-test them) ---
A_F0, A_G0 = 3.0, 0.5          # N the magnet pulls on a #10 washer through the sleeve at gap A_G0 mm (TBD; F falls as (g0/(g0+gap))^2)
A_M_CARD, A_M_HEAD = 0.0036, 0.045   # kg: sleeved card with washer; the tilting head (magnet + swivel)
A_COM = 20.0                   # mm: the head's centre of mass above the magnet face
A_FRIC = 1.5e-3                # N m: return spring + hinge friction torque on the head
A_LAG, A_ACC, A_SF = 3.0, 2.0, 3.0   # degrees the face lags the card; m/s^2 of the carriage; safety factor
WASHER_R = 5.55
def pull(gap_mm): return A_F0 * (A_G0 / (A_G0 + gap_mm)) ** 2
need_N = A_M_CARD * (9.81 + A_ACC) * A_SF
max_tilt = math.degrees(math.asin(min(1.0, A_G0 * (math.sqrt(A_F0 / need_N) - 1) / WASHER_R)))
report("ok", "assumption: pull on a #10 washer", f"F0={A_F0} N at {A_G0} mm (TBD, bench-test); card+washer {A_M_CARD*1000:.1f} g; needs {need_N:.2f} N with SF {A_SF:g}")
report("ok" if max_tilt < 30 else "WARN", "a FIXED flat face holds the card only to a small tilt", f"{max_tilt:.0f} deg; the flip needs {P.release_deg:g}: the head needs a pitch hinge")
def hinge_ok(h):                                       # torque: the washer's pull (lever = its radius) against gravity on the head + friction, at the lagged tilt
    cap = pull(WASHER_R * math.sin(math.radians(A_LAG))) * WASHER_R / 1000
    grav = A_M_HEAD * 9.81 * abs(A_COM - h) / 1000
    return cap, grav, cap >= A_SF * (grav + A_FRIC)
cap, grav, ok_h = hinge_ok(P.hinge_h)
report("ok" if ok_h else "FAIL", f"balanced hinge ({P.hinge_h:g} mm above the face, head CoM {A_COM:g}): the card can turn the head", f"pull torque {cap*1000:.1f} mN m vs {A_SF:g} x ({grav*1000:.1f} gravity + {A_FRIC*1000:.1f} friction)")
cap, grav, ok_34 = hinge_ok(34.0)
report("ok" if not ok_34 else "FAIL", "negative control: a hinge at 34 mm (above the head) is too heavy for the card to turn", f"needs {A_SF*(grav+A_FRIC)*1000:.1f} mN m, the pull gives {cap*1000:.1f}")

# --- (b) reach ---
lo_z = min(w.z for w in W); hi_z = max(w.z for w in W)
inside = all(14 <= w.x <= P.travel[0] - 14 and 14 <= w.y <= P.travel[1] - 14 and 0 <= w.z <= P.travel[2] for w in W)
report("ok" if inside else "FAIL", "every waypoint is inside X/Y/Z travel (head footprint 12 mm clear of the bed edge)", f"X {min(w.x for w in W):.0f}..{max(w.x for w in W):.0f}, Y {min(w.y for w in W):.0f}..{max(w.y for w in W):.0f}, nozzle Z {lo_z:.1f}..{hi_z:.1f} of {P.travel[2]:.0f}")
report("ok" if lo_z >= 12 else "WARN", "lowest nozzle height is above the camera floor (min_z; the README's example is 12)", f"{lo_z:.1f} mm")
report("ok", "Z axis", "the CR-6's Z lifts the whole X beam; the face hangs %.0f mm below the nozzle with the hinge (fixed head: 5 mm); lift needed %.0f mm of %.0f" % (P.hinge_h + P.nozzle_above_axis, hi_z - lo_z, P.travel[2]))

# --- (c) the card as a rigid rectangle pivoting about the caught edge ---
import math as _m
u_, h_ = P.top_corner
slides = [fp.edge_at(P, t / 2)[0] for t in range(0, int(P.release_deg * 2) + 1)]
worst = max(fp.penetration(P, fp.edge_at(P, t / 2)[0] + 1e-4, fp.edge_at(P, t / 2)[1], t / 2, 1e-3) for t in range(0, int(P.release_deg * 2) + 1))
report("ok" if worst == 0 else "FAIL", "a rigid card pivoting about the fence has a pose at every angle with no fence point inside it (0.5 degree steps)", f"{worst} fence points inside")
sl = max(slides) - min(slides)
report("ok" if sl <= 6 else "WARN", "the card's end corner barely slides along the bed while it turns (it is wedged, not dragged)", f"{sl:.1f} mm over 0..{P.release_deg:g} deg; if it slides more on the bench, the face needs more grip")
ey, _ = fp.edge_at(P, 90.0); wy90, wz90 = fp.washer_top(P, 90.0)
report("ok" if wz90 >= P.fence_h + 5 else "FAIL", "the washer end is high enough at vertical to clear the fence", f"{wz90:.1f} mm up at 90 deg, fence {P.fence_h:g} mm (it is the washer's distance from the edge: {P.d:g} mm)")
ey, _ = fp.edge_at(P, P.release_deg); com_y = ey + (P.card_w / 2) * _m.cos(_m.radians(P.release_deg))
report("ok" if com_y <= u_ - 5 else "FAIL", "at the release angle the card's centre of mass is beyond the fence's top corner (it topples forward)", f"{u_ - com_y:.1f} mm beyond at {P.release_deg:g} deg")
ly = P.fence_y - P.rear_d - P.d
report("ok" if (_m.cos(_m.pi) < 0 and ly < P.fence_y) else "FAIL", "it ends on the far side with its back face up (washer on top)", f"landing washer target y = {ly:.1f} (fence at {P.fence_y:g}), tolerance +/-{fp.landing(P)['tol_y_mm']:g} mm, +/-{fp.landing(P)['tol_x_mm']:g} mm in X, {fp.landing(P)['tol_skew_deg']:g} deg skew")
def rect(x0, y0, x1, y1): return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
def overlap(a, b): return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
zone = rect(P.fence_x - 61.65 - 15, P.fence_y - 51.5 - 20, P.fence_x + 61.65 + 15, P.fence_y + 8 + P.card_w + 15)      # fence, rails, tabs, the waiting card and the landing card, with 15 mm to spare
others = {"deck box": rect(316.7, 10, 316.7 + 73.3, 10 + 99.3), "hand rack": rect(25, 288.7, 374.3, 400), "discard chute": rect(10, -80, 84.5, 10)}
clash = [n for n, r in others.items() if overlap(zone, r)]
report("ok" if not clash and zone[0] >= 0 and zone[2] <= 400 and zone[1] >= 0 and zone[3] <= 400 else "FAIL", "the station zone (%.0f x %.0f mm) is on the bed and clear of the deck box, hand rack and chute" % (zone[2] - zone[0], zone[3] - zone[1]),
       ("overlaps " + ", ".join(clash)) if clash else f"X {zone[0]:.0f}..{zone[2]:.0f}, Y {zone[1]:.0f}..{zone[3]:.0f}; the fence is 123 x 52 x 3.5 mm")

# --- (a) swept volume of the head along the whole path ---
def head_scad(x, yc, zn, tilt, grow=0.0):
    ax = zn - P.nozzle_above_axis                      # hinge axis height
    g = grow
    tilting = (f"translate([{x},{yc},{ax}]) rotate([{tilt},0,0]) translate([0,0,-{P.hinge_h}]) {{ translate([0,0,-{g}]) cylinder(d=20+{2*g}, h=15+{g}, $fn=40); translate([0,0,15]) cylinder(d=24+{2*g}, h=15.1+{g}, $fn=40); }}")
    fixed = (f"translate([{x},{yc},{ax}]) {{ for (s=[-1,1]) translate([s*16-2,-7,-8]) cube([4,14,30]); translate([-25,-15,22]) cube([50,30,4]); }}")
    return tilting + "; " + fixed + ";"
def samples(grow=0.0, path=None):
    pw = path or W; out = []
    for a, b in zip(pw, pw[1:]):
        n = max(1, min(8, int(max(abs(b.x - a.x), abs(b.y - a.y), abs(b.z - a.z)) // 6) + 1))
        for k in range(n):
            t = k / n; out.append(head_scad(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, a.tilt + (b.tilt - a.tilt) * t, grow))
    out.append(head_scad(pw[-1].x, pw[-1].y, pw[-1].z, pw[-1].tilt, grow)); return out
LAYOUT = {"deck": (316.7, 10.0), "rack_x": (25.0, 374.3), "rack_y": (400 - 111.3, 400.0)}       # README bed layout: deck front right, rack along the back
obstacles = {
    "flip fence": f'translate([{P.fence_x},{P.fence_y},0]) import("{FENCE}")',
    "deck box": f'translate([{LAYOUT["deck"][0]},{LAYOUT["deck"][1]},0]) import("{STL/"deck_box.stl"}")',
    "hand rack (its bounding box, 45 mm tall)": f'translate([{LAYOUT["rack_x"][0]},{LAYOUT["rack_y"][0]},0]) cube([{LAYOUT["rack_x"][1]-LAYOUT["rack_x"][0]},{LAYOUT["rack_y"][1]-LAYOUT["rack_y"][0]},45])',
    "discard chute (its bounding box)": 'translate([10,-80,0]) cube([74.5,90,52.4])',
    "the bed surface (nothing below Z=0)": 'translate([-300,-300,-60]) cube([1000,1000,60])',
}
for name, obst in obstacles.items():
    sw = "union(){" + " ".join(samples()) + "}"
    hit = collides(f"intersection(){{ {sw}; {obst}; }}")
    near = (not hit) and collides(f"intersection(){{ union(){{ {' '.join(samples(1.0))} }}; {obst}; }}")
    report("FAIL" if hit else ("WARN" if near else "ok"), f"head swept along the whole flip path clears {name}", "collides" if hit else ("within 1 mm" if near else "clear, also with the head grown 1 mm"))
bad = fp.Waypoint("dip", P.fence_x, P.fence_y + 3, 2 + P.hinge_h + P.nozzle_above_axis, True, 100, 0.0)
report("ok" if collides(f"intersection(){{ union(){{ {head_scad(bad.x, bad.y, bad.z, 0)} }} {obstacles['flip fence']}; }}") else "FAIL", "negative control: a head that dips onto the fence DOES collide (the sweep can fail)")
fixed_head = fp.Params(hinge_h=None)
report("ok", "the fixed head's path (no hinge) is only listed, not accepted", f"it would need the face at {fp.washer_top(fixed_head, 90)[1]:.0f} mm and tilted {P.release_deg:g} deg against a level face")

print("7. stricter pre-print checks (added 2026-10-08): each fails for a reason a person could verify with a ruler")
import json as _json
def tri_list(path):
    v = triangles(path); return [v[i:i + 3] for i in range(0, len(v), 3)]
def overhang_area(T, rot_x_deg=0.0, limit_deg=45.0):
    """Area (mm2) of faces that face down more steeply than limit_deg from vertical AND are off the bed, when the part is turned by rot_x_deg
    about X and set on the bed (its lowest point at z=0). Flat 'bridges' count in full: the number is what needs supports or bridging."""
    c, s = math.cos(math.radians(rot_x_deg)), math.sin(math.radians(rot_x_deg))
    R = [[(a[0], a[1] * c - a[2] * s, a[1] * s + a[2] * c) for a in t] for t in T]
    zmin = min(p[2] for t in R for p in t); area = 0.0
    for a, b, d in R:
        ux, uy, uz = (b[i] - a[i] for i in range(3)); vx, vy, vz = (d[i] - a[i] for i in range(3))
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx; L = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        if nz / L < -math.cos(math.radians(limit_deg)) and min(a[2], b[2], d[2]) > zmin + 0.3: area += L / 2
    return area, zmin

# 7.1 the chute: a card is DRAGGED onto its top plate, which stands wall mm above the bed
ch = scad_vars("discard_chute.scad"); step_h = ch["wall"]
report("FAIL" if step_h > 0.5 else "ok", "chute: the top plate is flush enough to drag a card onto (a step above the bed catches the card's edge)",
       f"the plate stands {step_h:g} mm above the bed; the sleeved card is {ct:g} mm thick; allow at most 0.5 mm, or give it a feathered lead-in or pick-and-place", id="chute-step")
T_ch = tri_list(STL / "discard_chute.stl")
best = min(((overhang_area(T_ch, a)[0], a) for a in (0, 35, 55, 90, 180)), key=lambda t: t[0])
report("WARN" if best[0] > 300 else "ok", "chute: prints without supports in some orientation (faces more than 45 degrees from vertical, off the bed)",
       f"best of 0/35/55/90/180 deg is {best[0]:.0f} mm2 at {best[1]} deg (the STL as modelled: {overhang_area(T_ch, 0)[0]:.0f} mm2 with its lowest point {overhang_area(T_ch, 0)[1]:.1f} mm from the bed plane): plan supports, and orient it in the slicer", id="chute-overhang")

# 7.2 the card against the parts of the head that do NOT tilt (bracket leg, side plates), over the whole flip
def sat_overlap(A, B):
    for poly in (A, B):
        for i in range(len(poly)):
            x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % len(poly)]; nx, ny = y1 - y2, x2 - x1
            pa = [nx * p[0] + ny * p[1] for p in A]; pb = [nx * p[0] + ny * p[1] for p in B]
            if max(pa) <= min(pb) or max(pb) <= min(pa): return False
    return True
def card_poly(theta):                                # the card's (y, z) rectangle at pitch theta, world coordinates
    ey, ez = fp.edge_at(P, theta); th = math.radians(theta); u, nn = (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))
    return [(P.fence_y + ey + a * u[0] + b * nn[0], ez + a * u[1] + b * nn[1]) for a, b in ((0, 0), (P.card_w, 0), (P.card_w, P.card_t), (0, P.card_t))]
def fixed_boxes(x, yc, zn, lift=0.0):                # (x0, x1, y0, y1, z0, z1): the two side plates and the bracket leg, as in head_scad()
    ax = zn - P.nozzle_above_axis + lift
    return [(x + s * 16 - 2, x + s * 16 + 2, yc - 7, yc + 7, ax - 8, ax + 22) for s in (-1, 1)] + [(x - 25, x + 25, yc - 15, yc + 15, ax + 22, ax + 26)]
def card_hits_head(lift=0.0, step=0.5):
    rel = max(i for i, w in enumerate(W) if not w.magnet and w.dwell_ms == P.release_ms) if any(w.dwell_ms == P.release_ms for w in W) else len(W) - 1
    worst = None
    for a, b in zip(W[:rel], W[1:rel + 1]):
        n = max(1, int(max(abs(b.x - a.x), abs(b.y - a.y), abs(b.z - a.z)) / 1.0) + 1)
        for k in range(n + 1):
            t = k / n; x, y, z, th = (a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, a.tilt + (b.tilt - a.tilt) * t)
            cp = card_poly(th); cx0, cx1 = P.fence_x - P.card_l / 2, P.fence_x + P.card_l / 2
            for name, (x0, x1, y0, y1, z0, z1) in zip(("left side plate", "right side plate", "bracket leg"), fixed_boxes(x, y, z, lift)):
                if x1 > cx0 and x0 < cx1 and sat_overlap(cp, [(y0, z0), (y1, z0), (y1, z1), (y0, z1)]):
                    return (th, name, z0, z1, max(p[1] for p in cp))
    return None
hit = card_hits_head()
report("FAIL" if hit else "ok", "the card, turning, clears the bracket leg and side plates (the parts of the head that do not tilt)",
       f"the {hit[1]} at pitch {hit[0]:.0f} deg: the card's top edge is {hit[4]:.0f} mm above the bed; that part occupies {hit[2]:.0f}..{hit[3]:.0f} mm and hangs from the carriage, so it does not tilt with the card" if hit else "clear over the whole path, 1 mm steps", id="flip-card-vs-head")
low = card_hits_head(lift=-30.0)
report("ok" if low else "FAIL", "negative control: with the bracket 30 mm lower the same sweep DOES find the card", "" if low else "the sweep cannot fail, so its 'clear' above means nothing")

# 7.2b after the magnet lets go the head stays where it is (the script waits release_ms for the card to topple, THEN rises): does the falling card hit it?
def head_rect(theta):                                 # magnet + swivel, in (y, z) world coordinates, hanging off the card's face at pitch theta
    fy, fz = fp.washer_top(P, theta); th = math.radians(theta); u, nn = (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))
    return [(P.fence_y + fy + a * u[0] + b * nn[0], fz + a * u[1] + b * nn[1]) for a, b in ((-12, 0), (12, 0), (12, 30), (-12, 30))]
def fall_hit(shift_y=0.0):
    hr = [(y + shift_y, z) for y, z in head_rect(P.release_deg)]
    for tt in range(int(P.release_deg) + 2, 181, 2):
        if sat_overlap(card_poly(tt), hr): return tt
    return None
fh = fall_hit()
report("FAIL" if fh else "ok", "after release, the card can fall without landing on the head (the head waits release_ms where it is, then rises)",
       f"the head is on the face the card is turning toward: the card meets it at {fh} deg of its 180; the head must retreat BEFORE or AS the card falls, along the face normal and clear of the card's arc" if fh else "clear", id="flip-release-clearance")
report("ok" if fall_hit(shift_y=-150.0) is None else "FAIL", "negative control: a head 150 mm away from the falling card is not hit", "" if fall_hit(shift_y=-150.0) is None else "the check always fires, so it means nothing")

# 7.3 the swivel plug (parts/swivel_plug_lib.scad): it must slide in the way the bolt did, close the side the bolt would escape by, and stay clear of the bolt
PLUG_LIB = HERE / "parts" / "swivel_plug_lib.scad"; SWIVEL = STL / "magnet_swivel.stl"; GRADES = {"1 (0.45, loosest)": 0.45, "2 (0.35)": 0.35, "3 (0.25)": 0.25, "4 (0.15)": 0.15, "5 (0.05, tightest)": 0.05}
def plug_hits(fit, d=0.0, ribs=False):
    return collides(f'include <{PLUG_LIB}>\nintersection(){{ import("{SWIVEL}"); translate([{d},0,0]) plug({fit}, {str(ribs).lower()}); }}')
if PLUG_LIB.exists():
    for name, fit in GRADES.items():
        bad = [d for d in (24, 18, 12, 6, 0) if plug_hits(fit, d)]                          # nominal swivel
        bad_g = [d for d in (24, 18, 12, 6, 0) if plug_hits(fit - GROW / 2 + 0.01, d)]    # printed holes ~GROW small in total: GROW/2 per wall (+0.01: exact zero clearance is coplanar faces, which the kernel reports as a sliver)
        if bad: report("FAIL", f"swivel plug {name}: slides into the swivel from +X (nominal)", f"collides at x shift {bad}", id=f"plug-insert-{name[0]}")
        elif bad_g and fit >= 0.25: report("FAIL", f"swivel plug {name}: still slides in when the holes print {GROW:g} mm small", f"collides at x shift {bad_g}", id=f"plug-insert-grown-{name[0]}")
        else: report("ok", f"swivel plug {name}: slides in (nominal" + ("" if bad_g else " and holes printed 0.3 small") + ")", "" if not bad_g else "when the holes print 0.3 small it binds: a tight grade, it will need a push")
    mid = GRADES["3 (0.25)"]
    head_hits = [g for g in (0, GROW) if collides(f'include <{PLUG_LIB}>\nintersection(){{ {bolt(0, g)}; plug({mid}, false); }}')]
    report("FAIL" if head_hits else "ok", "swivel plug (grade 3) does not touch the seated bolt (nominal, and the bolt grown 0.3)", "the plug hits the bolt head" if head_hits else "clear of head and thread", id="plug-vs-bolt")
    out_free = [x for x in (2, 4, 6, 8, 12, 18) if not collides(f'include <{PLUG_LIB}>\nintersection(){{ {bolt(x, 0)}; plug({mid}, false); }}')]
    report("FAIL" if out_free else "ok", "with the plug in, the bolt cannot slide out along the keyhole slot",
           f"the bolt still passes at x={out_free}" if out_free else "blocked from 2 mm on: the head meets the base, the thread meets the neck", id="plug-retains")
    retained = not out_free
    # the assembled head: nylon washer (12.7 OD, 6.5 bore, NYLON thick) on top of the swivel and the bracket's leg (BRACKET thick, 1" wide) on that. The plug is flush on top and its ear stands out
    # past the body, and it TURNS with the swivel (the bolt does) under the bracket: sweep a full turn in 15 degree steps (nominal, and grown 0.3).
    top = SWIVEL_TOP; nyl_z, brk_z = top, top + NYLON
    def stack_at(bz):                    # ONE union: a bare { A B } inside intersection() is flattened by OpenSCAD into intersect(plug, A, B), which is always empty (found by the negative control)
        return (f'union(){{ translate([0,0,{nyl_z}]) difference(){{ cylinder(d=12.7, h={NYLON}, $fn=48); translate([0,0,-1]) cylinder(d=6.5, h={NYLON + 2}, $fn=32); }}'
                f' translate([-12.7,-25.4,{bz}]) difference(){{ cube([25.4, 76, {BRACKET}]); translate([12.7, 25.4, -1]) cylinder(d=6.6, h={BRACKET + 2}, $fn=32); }} }}')   # the 1/4" hole 1" from the bracket's end sits on the swivel's axis
    stack = stack_at(brk_z)
    turned = [a for a in range(0, 360, 15) for g in (0, GROW)
              if collides(f'include <{PLUG_LIB}>\nintersection(){{ rotate([0,0,{a}]) translate([0,0,{g/2}]) plug({mid - g/2}, false); {stack}; }}')]
    ctl = collides(f'include <{PLUG_LIB}>\nintersection(){{ plug({mid}, false); {stack_at(top - 2)}; }}')               # control: the bracket 2 mm INTO the plug's top
    report("ok" if ctl else "FAIL", "negative control: a bracket sunk 2 mm into the plug's top DOES collide (the stack check can fail)")
    report("FAIL" if turned else "ok", "swivel plug in the assembled head clears the nylon washer and the bracket, turning a full circle",
           f"touches at {sorted(set(turned))[:6]} degrees" if turned else f"clear all the way round; the plug's top is flush with the swivel, the bracket's underside is {NYLON:g} mm above it", id="plug-vs-stack")
else:
    retained = False
    report("WARN", "swivel: nothing but friction keeps it from sliding off the bolt along the keyhole slot", "no plug part yet (parts/swivel_plug_lib.scad)", id="swivel-retention")

# 7.4 hardware that the BOM lists against what the stack needs
bom = _json.loads((HERE.parent / "bom.json").read_text()); items = bom["items"] if isinstance(bom, dict) and "items" in bom else bom
bolt_item = next((i for i in items if i.get("id") == "hex-bolts"), {}); bolt_in = 1.5 if "1-1/2" in bolt_item.get("name", "") or "1.5" in bolt_item.get("name", "") else 1.0
need_len = SWIVEL_TOP + NYLON + BRACKET + SPRING_SOLID + NUT - POCKET_FLOOR - HEAD_H
report("FAIL" if bolt_in * 25.4 < need_len + 0.5 else "ok", "the bolt in the bill of materials is long enough for the stack with a margin of at least 0.5 mm",
       f"the BOM lists a {bolt_in:g} inch bolt ({bolt_in * 25.4:.1f} mm); the stack needs {need_len:.1f} mm of bolt + 0.5 mm: buy 1-1/2 inch", id="bolt-length")
est = 1.25 + 0.30 + 0.20                              # #10 washer 0.049 in + card 0.3 + two sleeves, nominal: an ESTIMATE, to be replaced by the ten-card measurement
report("WARN" if est > ct else "ok", "card_t (a card with its washer) is not below an estimate from the parts' nominal thicknesses",
       f"card_t is {ct:g} mm; washer 1.25 + card 0.30 + sleeves 0.20 = {est:.2f} mm: the deck box's slot is card_t + {d['slot_play']:g} and the rack's slot card_t + {r['gap']:g}; measure ten and set card_t in all three .scad files", id="card-t-unmeasured")

# 7.5 layout: the hard-coded footprints in section 6 against the STL files they stand for
def bbox(name):
    v = triangles(STL / name); return [(min(c[i] for c in v), max(c[i] for c in v)) for i in range(3)]
pairs = {"deck box": (bbox("deck_box.stl"), 73.3, 99.3), "discard chute": (bbox("discard_chute.stl"), 74.5, 90.0), "flip fence": (bbox("flip_fence.stl"), 123.3, 51.5)}
off = [f"{n}: STL {b[0][1]-b[0][0]:.1f} x {b[1][1]-b[1][0]:.1f} vs layout {w:g} x {h:g}" for n, (b, w, h) in pairs.items() if abs(b[0][1] - b[0][0] - w) > 1.0 or abs(b[1][1] - b[1][0] - h) > 1.0]
rb = bbox("hand_rack.stl"); rack_w = rb[0][1] - rb[0][0]
if abs(rack_w - (LAYOUT["rack_x"][1] - LAYOUT["rack_x"][0])) > 1.0: off.append(f"hand rack: STL {rack_w:.1f} wide vs layout {LAYOUT['rack_x'][1]-LAYOUT['rack_x'][0]:.1f}")
report("FAIL" if off else "ok", "the layout rectangles used by the collision checks match the STL footprints", "; ".join(off) if off else "deck box, rack, chute and fence agree to 1 mm", id="layout-drift")

# 7.6 reach needs the magnet's offset from the nozzle, which nobody has measured
cfgp = HERE.parent.parent / "table" / ".cache" / "gantry.json"; offs = (_json.loads(cfgp.read_text()).get("magnet_offset_mm") if cfgp.exists() else None)
report("WARN" if not offs else "ok", "magnet_offset_mm (magnet axis relative to the nozzle) is measured, so reach to the deck, rack and chute can be checked",
       "not measured: every reach check assumes [0, 0]. The chute needs the magnet at least ~20 mm in FRONT of the nozzle (Y offset <= -20) to drag a card onto it; bed Y can't go below 0" if not offs else f"offset {offs}", id="magnet-offset-unmeasured")

# ── the gate: a failure may only pass if it is written down, and a written-down failure that stopped failing must be removed ──
import os
LEDGER = Path(os.environ.get("FIT_CHECK_LEDGER") or HERE / "fit_check_ledger.json")
ledger = _json.loads(LEDGER.read_text())["known_failures"] if LEDGER.exists() else []
known = {k["id"]: k for k in ledger}
import datetime as _dt
parts = sorted(f.stem for f in STL.glob("*.stl")); today = _dt.date.today().isoformat(); bad_ledger = []
for k in ledger:                                      # an entry may not silence a failure by being vague, mistyped or forgotten
    for b in k.get("blocks", []):
        if b not in parts: bad_ledger.append(f"{k['id']}: blocks '{b}', which is not a part ({', '.join(parts)})")
    if not k.get("blocks") and not k.get("hardware_only"): bad_ledger.append(f"{k['id']}: blocks nothing; name the parts it blocks, or set hardware_only: true (it blocks assembly, not printing)")
    if not k.get("reason"): bad_ledger.append(f"{k['id']}: no reason")
    if str(k.get("review_by", "")) < today: bad_ledger.append(f"{k['id']}: review_by {k.get('review_by')} has passed ({today}): fix it, or re-decide it on purpose by moving the date")
failing = {i for lvl, i, _ in checks if lvl == "FAIL" and i}
anon = [w for lvl, i, w in checks if lvl == "FAIL" and not i]
new = sorted(failing - set(known)); stale = sorted(set(known) - failing)
blocked = {}
for i in failing & set(known):
    for part in known[i].get("blocks", []): blocked.setdefault(part, []).append(i)
print()
print(f"{results.count('ok')} ok, {results.count('WARN')} warn, {results.count('FAIL')} fail  ({len(failing & set(known))} on the ledger)")
for i in sorted(failing & set(known)): print(f"  known  {i}: {known[i]['reason']}")
for i in new: print(f"  NEW    {i}: a failure that is not on the ledger (fix it, or add it to {LEDGER.name} with a reason)")
for i in stale: print(f"  STALE  {i}: on the ledger but no longer failing: remove it")
for w in anon: print(f"  NEW    (no id) {w}")
for b in bad_ledger: print(f"  LEDGER {b}")
print("cleared to print:", ", ".join(p for p in parts if p not in blocked) or "none")
print("blocked:         ", "; ".join(f"{p} ({', '.join(v)})" for p, v in sorted(blocked.items())) or "none")
ok_gate = not new and not stale and not anon and not bad_ledger
print("PRE-PRINT GATE:", ("BLOCKED" if blocked else "OPEN") if ok_gate else "BROKEN (the ledger and the checks disagree)")
sys.exit(0 if ok_gate else 1)
