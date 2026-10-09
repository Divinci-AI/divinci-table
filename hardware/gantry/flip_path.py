#!/usr/bin/env python3
"""Waypoints for the EDGE FLIP of one sleeved card (README, "Flipping a card (edge flip)"). DESIGN ONLY: nothing here has run on the rig.

  python3 hardware/gantry/flip_path.py                 # print the path
  python3 hardware/gantry/flip_path.py --json out.json --gcode out.gcode

The card lies on the bed with one LONG edge near the fence. The magnet grips the steel washer (the jig puts it at the card's centre, so
it is half the card's width from the edge), drags the edge against the fence's catching face, then lifts the washer in an arc: the card
pivots about the caught edge like a page, goes a little past vertical (the pivot hops from the foot of the fence to its top corner),
and the magnet lets go. The card topples over the fence onto the far side, back face up. The washer is on the card's back, so it is
now on TOP: the next pick is a normal pick of that washer.

THE CATCH, and why the head needs a hinge: the magnet's face is flat and the head cannot pitch. A card pivoting through 90 degrees is
held against a face that stays horizontal only at the washer's edge. Section 3 of fit_check.py computes how far the face can be tilted
against the washer before the pull is too weak (about 18 degrees, under the assumptions below): nowhere near 110. So the head needs a
passive PITCH HINGE whose axis is parallel to the fence. The path below is for a hinged head (hinge_h above the magnet face); with
hinge_h = None it describes the fixed head, which fit_check shows cannot do it.

Coordinates: bed X (head left-right) and Y (bed toward / away from you); fence along X; the catching face at y = fence_y; the card waits
on the +Y side. Z is the NOZZLE height above the bed (the CR-6's Z moves the whole X beam; the magnet face hangs below the nozzle).
Everything is in mm and degrees. Values marked TBD are assumptions to be measured.
"""
import argparse, json, math
from dataclasses import dataclass, field, asdict

@dataclass
class Params:
    card_l: float = 92.5            # long edge, along the fence (deck_box.scad / hand_rack.scad numbers)
    card_w: float = 66.5            # the edge-to-edge width: the card pivots about a long edge, so it stands this tall at most
    card_t: float = 1.6             # sleeved card WITH its washer, TBD (measure ten, divide by ten)
    washer_off: float | None = None # washer's distance from the pivot edge; None = card_w / 2 (the jig puts it at the centre)
    fence_h: float = 3.5            # flip_fence.scad
    undercut_deg: float = 8.0
    rear_d: float = 11.0            # thickness of the fence behind its face (base 5 + ramp 6)
    fence_x: float = 150.0          # bed position of the middle of the catching face
    fence_y: float = 140.0
    hinge_h: float | None = 20.0    # pitch-hinge axis above the magnet face = about the head's centre of mass, so gravity does not fight the card (fit_check section 6); None: the fixed head, which cannot do it. TBD design
    nozzle_above_axis: float = 8.0  # nozzle tip above the hinge axis; TBD design
    face_below_nozzle_fixed: float = 5.0   # fixed head: the face is at least 5 mm below the nozzle (README Step 4)
    release_deg: float = 110.0      # the magnet lets go here; past vertical, the card's centre is beyond the fence's top corner
    step_deg: float = 10.0
    approach: float = 8.0           # the card waits this far from the fence, then is dragged in
    push: float = 0.5               # drag this far past first touch so the edge seats against the face
    travel_gap: float = 25.0        # face height of the travelling moves above the card
    travel: tuple = (400.0, 400.0, 400.0)
    feeds: dict = field(default_factory=lambda: {"travel": 3000, "lower": 300, "drag": 600, "arc": 900, "retreat": 1500})
    hold_ms: int = 300              # magnet on before moving (dwell)
    release_ms: int = 600           # after letting go, wait for the card to topple

    @property
    def d(self): return self.card_w / 2 if self.washer_off is None else self.washer_off
    @property
    def top_corner(self):           # (y, z) of the lip's top edge, relative to the face's foot
        return (self.fence_h * math.tan(math.radians(self.undercut_deg)), self.fence_h)

@dataclass
class Waypoint:
    name: str
    x: float; y: float; z: float    # carriage position: x, y = the hinge axis (the magnet's axis when the head is level); z = nozzle height
    magnet: bool
    feed: int
    tilt: float = 0.0               # head pitch in degrees (passive: documentation and the swept-volume check)
    dwell_ms: int = 0
    face: tuple = (0.0, 0.0)        # (y, z) of the magnet face's centre, for checks

def fence_points(p: Params):
    """The fence's profile (y, z) relative to the face's foot, sampled along its outline: the lip, its leaning face, the rear slope."""
    u, h = p.top_corner; poly = [(-p.rear_d, 0), (0, 0), (u, h), (-5, h)]; pts = []
    for (a, b), (c, d) in zip(poly, poly[1:] + poly[:1]):
        pts += [(a + (c - a) * k / 40, b + (d - b) * k / 40) for k in range(41)]
    return pts

def penetration(p: Params, by: float, bz: float, theta_deg: float, tol: float = 1e-6) -> int:
    """How many fence outline points lie strictly inside the rigid card (a W x t rectangle whose underside end corner is at (by, bz))."""
    th = math.radians(theta_deg); ux, uz, nx, nz = math.cos(th), math.sin(th), -math.sin(th), math.cos(th); n = 0
    for (y, z) in fence_points(p):
        du, dn = (y - by) * ux + (z - bz) * uz, (y - by) * nx + (z - bz) * nz
        if tol < du < p.card_w - tol and tol < dn < p.card_t - tol: n += 1
    return n

def edge_at(p: Params, theta_deg: float):
    """(y, z) of the card's underside end corner B relative to the fence face's foot, at card angle theta (0 = flat on the card side).
    A rigid card of thickness t rests on the bed with its end against the fence: B is the closest it can get to the fence without any of the
    fence's outline inside it (a bisection on y). Up to ~90 degrees the end face is wedged between the bed and the leaning face; past
    90 the end face's lower corner rests on the bed and the underside rests on the lip's top corner."""
    th = math.radians(theta_deg); bz = 0.0 if theta_deg <= 90 else -p.card_t * math.cos(th)
    lo, hi = -30.0, 30.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if penetration(p, mid, bz, theta_deg) == 0: hi = mid
        else: lo = mid
    return (hi, bz)

def washer_top(p: Params, theta_deg: float):
    """(y, z) relative to the fence face's foot of the magnet face centre: the card's top surface over the washer."""
    th = math.radians(theta_deg); ey, ez = edge_at(p, theta_deg)
    return (ey + p.d * math.cos(th) + p.card_t * -math.sin(th), ez + p.d * math.sin(th) + p.card_t * math.cos(th))

def carriage(p: Params, face_y: float, face_z: float, theta_deg: float):
    """(y, z_nozzle) of the carriage for the face centre at (face_y, face_z) and head pitch theta."""
    th = math.radians(theta_deg)
    if p.hinge_h is None: return (face_y, face_z + p.face_below_nozzle_fixed)
    ay, az = face_y + p.hinge_h * -math.sin(th), face_z + p.hinge_h * math.cos(th)
    return (ay, az + p.nozzle_above_axis)

def waypoints(p: Params) -> list[Waypoint]:
    F, out = p.feeds, []
    def add(name, fy, fz, magnet, feed, tilt=0.0, dwell=0, x=None):
        cy, cz = carriage(p, fy, fz, tilt)
        out.append(Waypoint(name, round(p.fence_x if x is None else x, 2), round(p.fence_y + cy, 2), round(cz, 2), magnet, feed, round(tilt, 1), dwell, (round(p.fence_y + fy, 2), round(fz, 2))))
    y0 = p.approach + p.d                                   # washer's distance from the face while the card waits
    add("over the card, magnet off", y0, p.card_t + p.travel_gap, False, F["travel"])
    add("down onto the washer", y0, p.card_t, False, F["lower"])
    add("magnet on, hold", y0, p.card_t, True, F["lower"], dwell=p.hold_ms)
    add("drag the edge into the fence face", p.d - p.push, p.card_t, True, F["drag"])
    th = p.step_deg
    while th < p.release_deg - 1e-9:
        fy, fz = washer_top(p, th); add(f"arc {th:g} deg", fy, fz, True, F["arc"], tilt=th); th += p.step_deg
    fy, fz = washer_top(p, p.release_deg); add(f"arc {p.release_deg:g} deg, magnet off", fy, fz, False, F["arc"], tilt=p.release_deg, dwell=p.release_ms)
    # retreat: straight up first (the carriage does not move in Y while the head's light return spring levels it), then away from the card
    cy, cz = carriage(p, fy, fz, p.release_deg)
    out.append(Waypoint("rise straight up; the head springs level", out[-1].x, round(p.fence_y + cy, 2), round(cz + 35.0, 2), False, F["retreat"], 0.0, 0, (0.0, 0.0)))
    out.append(Waypoint("clear: back over the card side", out[-1].x, round(p.fence_y + cy + 45.0, 2), out[-1].z, False, F["travel"], 0.0, 0, (0.0, 0.0)))
    return out

def landing(p: Params):
    """Where the card should come to rest (washer centre, bed coordinates) and how far it may be off: a design target, not a measurement."""
    return {"x": p.fence_x, "y": round(p.fence_y - p.rear_d - p.d, 1), "tol_y_mm": 8.0, "tol_x_mm": 3.0, "tol_skew_deg": 5.0,
            "face": "back up, washer on top", "next": "pick the washer here, then place it exactly (gantry.pick / place)"}

def gcode(p: Params, wps=None) -> str:
    wps = wps or waypoints(p)
    L = ["; EDGE FLIP of one card. DESIGN ONLY: never run on the rig. Dry-run it with NO card and the magnet unplugged first.",
         "; Generated by hardware/gantry/flip_path.py. X/Y = carriage, Z = nozzle height (mm); M106 S255 = magnet on, M107 = off (table/gantry.py).",
         f"; card {p.card_l} x {p.card_w} x {p.card_t} (TBD) mm, washer {p.d} mm from the pivot edge, fence at X{p.fence_x} Y{p.fence_y}",
         "; The head must have a passive pitch hinge (axis parallel to X) or the card cannot be turned: see the README.", "G90", "M107"]
    for w in wps:
        L.append(f"; {w.name}" + (f"  (head pitch {w.tilt:g} deg)" if w.tilt else ""))
        L.append(f"G1 X{w.x:.2f} Y{w.y:.2f} Z{w.z:.2f} F{w.feed}")
        L.append("M400"); L.append("M106 S255" if w.magnet else "M107")
        if w.dwell_ms: L.append(f"G4 P{w.dwell_ms}")
    L.append("M107"); return "\n".join(L) + "\n"

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0]); ap.add_argument("--json"); ap.add_argument("--gcode"); a = ap.parse_args()
    p = Params(); w = waypoints(p)
    print(f"{'waypoint':40s} {'x':>7s} {'y':>7s} {'z':>7s} magnet feed  tilt")
    for q in w: print(f"{q.name:40s} {q.x:7.1f} {q.y:7.1f} {q.z:7.1f} {'ON ' if q.magnet else 'off'}  {q.feed:5d} {q.tilt:5.0f}")
    print("landing target:", landing(p))
    if a.json: open(a.json, "w").write(json.dumps({"params": {k: v for k, v in asdict(p).items()}, "waypoints": [asdict(q) for q in w], "landing": landing(p)}, indent=1))
    if a.gcode: open(a.gcode, "w").write(gcode(p, w))

if __name__ == "__main__": main()
