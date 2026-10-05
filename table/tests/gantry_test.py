"""The gantry driver against a fake Marlin board: no hardware needed.

Checks the safety gate (no heating, no extruding, no fans, endstops stay on), the travel limits,
that moves wait for completion, and the serpentine scan order.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import gantry  # noqa: E402


class FakeMarlin:
    """Answers like Marlin 2 on an Ender-3: echoes position, says ok, reports busy on homing."""
    def __init__(self, machine="Ender-3 V2"):
        self.machine, self.pos, self.sent, self.queue = machine, {"X": 0.0, "Y": 0.0, "Z": 0.0}, [], []

    def write(self, data):
        line = data.decode().strip()
        self.sent.append(line)
        w = line.split()[0]
        if w == "M115":
            self.queue += [f"FIRMWARE_NAME:Marlin 2.0.9.3 (Github) SOURCE_CODE_URL:github.com/MarlinFirmware/Marlin "
                           f"PROTOCOL_VERSION:1.0 MACHINE_TYPE:{self.machine} EXTRUDER_COUNT:1"]
        elif w == "M114":
            p = self.pos
            self.queue += [f"X:{p['X']:.2f} Y:{p['Y']:.2f} Z:{p['Z']:.2f} E:0.00 Count X:0 Y:0 Z:0"]
        elif w == "G28":
            self.queue += ["echo:busy: processing"]
            for a in "XYZ":
                if a in line or line == "G28":
                    self.pos[a] = 0.0
        elif w in ("G0", "G1", "G2", "G3"):
            for a, v in re.findall(r"([XYZ])(-?[\d.]+)", line):
                self.pos[a] = float(v)
        self.queue.append("ok")

    def readline(self):
        return (self.queue.pop(0) + "\n").encode() if self.queue else b""


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    return cond


def refused(fn):
    try:
        fn()
    except gantry.Refused:
        return True
    return False


ok = True
for bad in ["M104 S200", "M109 S200", "M140 S60", "M190 S60", "G1 X10 E5", "M500",
            "M211 S0", "M302 P1", "G29", "M104 S1", "M140 S60", "M104 S05",
            "M106 S128", "M106", "M106 P1 S255", "M107 P1", "M106 S255 P0",          # the magnet: full on or off only
            "G2 X10 Y10 I5 J0 E1", "G3 X10 Y10 I5 J0 Z5", "G2"]:                   # arcs: no extruding, no Z
    ok &= check(f"refuses {bad}", refused(lambda: gantry.safe(bad)))
for good in ["M104 S0", "M140 S0", "M105", "G28 X Y", "G0 X10 Y20 F3000", "M114", "M400", "M84", "g0 x5 ; comment",
             "M106 S255", "M107", "G2 X10 Y10 I5 J0 F1500", "G3 X1.5 Y-0.5 I-2 J3"]:
    ok &= check(f"allows {good}", not refused(lambda: gantry.safe(good)))

fake = FakeMarlin()
p = gantry.Printer(ser=fake, log=lambda *_: None)
ok &= check("travel from MACHINE_TYPE (Ender-3 V2 = 220x220x250)", p.travel == (220, 220, 250))
p.home()
ok &= check("home leaves Z alone by default", fake.sent[-1] == "G28 X Y")
p.goto(100, 120)
ok &= check("goto waits for the move (M400)", fake.sent[-1] == "M400" and fake.sent[-2].startswith("G0 X100.0 Y120.0"))
ok &= check("position read back", p.position() == {"X": 100.0, "Y": 120.0, "Z": 0.0})
ok &= check("refuses X past travel", refused(lambda: p.goto(221, 10)))
ok &= check("refuses negative Y", refused(lambda: p.goto(10, -1)))
ok &= check("refuses Z past travel", refused(lambda: p.goto(10, 10, 251)))

p2 = gantry.Printer(ser=FakeMarlin("Mystery Printer 9000"), log=lambda *_: None)
ok &= check("unknown machine gets the safe 200 mm box", p2.travel == gantry.SAFE_DEFAULT)
big = gantry.Printer(ser=FakeMarlin("CR-10 S5"), log=lambda *_: None)
ok &= check("CR-10 S5 = 500 mm", big.travel == (500, 500, 500))

g = gantry.grid(0, 0, 200, 100, 3, 2)
ok &= check("serpentine grid", g == [(0, 0), (100, 0), (200, 0), (200, 100), (100, 100), (0, 100)])

# ── the magnet hand ─────────────────────────────────────────────────────────────────────────────
import time  # noqa: E402
fake = FakeMarlin()
p = gantry.Printer(ser=fake, log=lambda *_: None)
ok &= check("connecting turns the magnet off first thing (M107)", "M107" in fake.sent and not p.magnet)
p.magnet_on(max_s=0.3)
ok &= check("magnet on is exactly M106 S255", fake.sent[-1] == "M106 S255" and p.magnet)
time.sleep(0.7)
ok &= check("the on-time limit turns it off by itself", fake.sent[-1] == "M107" and not p.magnet)
p.magnet_on(max_s=0.3); p.magnet_off(); n = fake.sent.count("M107"); time.sleep(0.6)
ok &= check("turning it off cancels the timer (no stray M107 later)", fake.sent.count("M107") == n)
try:
    with gantry.Printer(ser=FakeMarlin(), log=lambda *_: None) as q:
        q.magnet_on(max_s=60)
        raise RuntimeError("crash mid-carry")
except RuntimeError:
    pass
ok &= check("an error inside `with Printer` turns the magnet off", q.ser.sent[-1] == "M107" and not q.magnet)
p.goto(100, 120)
ex, ey = p.tap(100, 100)
ok &= check("tap: a quarter turn clockwise about the corner (100,120) -> (120,100)",
            (round(ex), round(ey)) == (120, 100) and any(l.startswith("G2 X120.00 Y100.00 I0.00 J-20.00") for l in fake.sent))
p.goto(10, 10)
ok &= check("refuses an arc whose circle leaves the travel", refused(lambda: p.arc(3, 3, 3, 10)))
ok &= check("refuses an end point off the circle", refused(lambda: p.arc(50, 50, 10, 20)))
ok &= check("pick refuses without a measured touch_z", refused(lambda: p.pick(50, 50)))

cfg = dict(gantry.RIG_DEFAULT, min_z=50, lens_at_z0_mm=-100)
gantry.config = lambda: cfg
ok &= check("refuses Z below the camera floor", refused(lambda: big.goto(10, 10, 40)))
fake_big = big.ser; big.goto(10, 10, 60)
ok &= check("Y moves are slowed for the cards", fake_big.sent[-2].endswith("F1200"))
fw, fh = gantry.footprint(400, cfg)
ok &= check("footprint at 300 mm lens height = 372 x 248", (round(fw), round(fh)) == (372, 248))
pts, (c, r), _ = gantry.plan((0, 0, 400, 400), 400, cfg)
ok &= check("whole bed from 300 mm: 2x2 shots", (c, r) == (2, 2) and len(pts) == 4)
pts, (c, r), _ = gantry.plan((0, 0, 300, 200), 400, cfg)
ok &= check("an area smaller than one frame is one shot, centred", (c, r) == (1, 1) and pts == [(150, 100)])
ok &= check("lens into the bed is refused", refused(lambda: gantry.footprint(100, cfg)))

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
