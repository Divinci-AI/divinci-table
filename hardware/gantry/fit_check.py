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

def report(level, what, detail=""):
    results.append(level); print(f"  {level:4s} {what}" + (f"  ({detail})" if detail else ""))

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
    if n != 1: report("FAIL", f.name, f"{n} separate pieces")
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

print()
print("FAIL" if "FAIL" in results else ("WARN" if "WARN" in results else "ALL OK"), f"({results.count('ok')} ok, {results.count('WARN')} warn, {results.count('FAIL')} fail)")
sys.exit(1 if "FAIL" in results else 0)
