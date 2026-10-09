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
ok &= check("refuses ANY Z move while Z is unknown (fresh connection)", refused(lambda: p.goto(10, 10, 50)) and not p.z_known)
ok &= check("homing Z without clear=True is refused", refused(lambda: p.home(z=True)) and not p.z_known)
p.zero_z(0)
ok &= check("zero_z declares Z (G92 Z0) and only then is Z known", fake.sent[-1] == "G92 Z0" and p.z_known)
ok &= check("refuses Z past travel", refused(lambda: p.goto(10, 10, 251)))
ok &= check("G92 for anything but Z is refused", refused(lambda: gantry.safe("G92 X0")) and refused(lambda: gantry.safe("G92 E0")))
ok &= check("a new connection forgets Z", not gantry.Printer(ser=FakeMarlin(), log=lambda *_: None).z_known)
q2 = gantry.Printer(ser=FakeMarlin(), log=lambda *_: None); q2.home(z=True, clear=True)
ok &= check("home(z=True, clear=True) makes Z known", q2.z_known and q2.ser.sent[-1].startswith("G28 X Y Z") or "G28 X Y Z" in q2.ser.sent)

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
big.zero_z(0)
ok &= check("camera floor still refuses a low camera move", refused(lambda: big.goto(10, 10, 40)))
fake_big = big.ser; big.goto(10, 10, 60)
ok &= check("Y moves are slowed for the cards", fake_big.sent[-2].endswith("F1200"))
fw, fh = gantry.footprint(400, cfg)
ok &= check("footprint at 300 mm lens height = 372 x 248", (round(fw), round(fh)) == (372, 248))
pts, (c, r), _ = gantry.plan((0, 0, 400, 400), 400, cfg)
ok &= check("whole bed from 300 mm: 2x2 shots", (c, r) == (2, 2) and len(pts) == 4)
pts, (c, r), _ = gantry.plan((0, 0, 300, 200), 400, cfg)
ok &= check("an area smaller than one frame is one shot, centred", (c, r) == (1, 1) and pts == [(150, 100)])
ok &= check("lens into the bed is refused", refused(lambda: gantry.footprint(100, cfg)))

# ── the hand rack: rise before crossing, slide out from under the next shelf ─────────────────────
cfg = dict(gantry.RIG_DEFAULT, touch_z=5, travel_z=60, rack={"slot0": [40, 350], "pitch": 46, "rise": 3.6, "slots": 7})
gantry.config = lambda: cfg
fake = FakeMarlin("CR-6 Max")
p = gantry.Printer(ser=fake, log=lambda *_: None)
ok &= check("pick refuses while Z is unknown", refused(lambda: p.pick(50, 50)))
p.zero_z(0)
n = len(fake.sent)
p.pick(50, 50)
moves = [l for l in fake.sent[n:] if l.startswith("G0")]
ok &= check("pick rises to travel_z before moving across", moves[0].startswith("G0 Z60.0") and moves[1].startswith("G0 X50.0 Y50.0"))
ok &= check("…then down to touch_z", moves[2].startswith("G0 Z5.0"))
s2 = p.rack_slot(2)
ok &= check("slot 2 = two pitches right, base + two rises up, out sideways", s2 == {"x": 132, "y": 350, "dz": 9.2, "slide_x": -46})
ok &= check("slot 0 sits on the rack's base (2 mm), not on the bed", p.rack_slot(0)["dz"] == 2.0)
ok &= check("slot 0 has nothing over it: straight up", p.rack_slot(0)["slide_x"] == 0.0)
ok &= check("refuses a slot the rack doesn't have", refused(lambda: p.rack_slot(7)))
n = len(fake.sent)
p.pick(**s2)
seq = fake.sent[n:]
i_on = seq.index("M106 S255")
after = [l for l in seq[i_on:] if l.startswith("G0")]
ok &= check("rack pick grips at the slot's height", any(l.startswith("G0 Z14.2") for l in seq[:i_on]))
ok &= check("…slides out to the left before lifting", after[0].startswith("G0 X86.0") and after[1].startswith("G0 Z60.0"))
n = len(fake.sent)
p.place(**s2)
seq = fake.sent[n:]
i_off = seq.index("M107")
before = [l for l in seq[:i_off] if l.startswith("G0")]
ok &= check("rack place comes down beside the slot and slides in, then lets go",
            before[-3].startswith("G0 X86.0 Y350.0") and before[-2].startswith("G0 Z14.2") and before[-1].startswith("G0 X132.0"))
cfg["rack"] = {"slot0": None}
ok &= check("refuses the rack before slot 0 is measured", refused(lambda: p.rack_slot(0)))

# ── A: the camera floor is not the magnet's floor ────────────────────────────────────────────────
cfg = dict(gantry.RIG_DEFAULT, min_z=50, touch_z=5, magnet_min_z=0, travel_z=60)
gantry.config = lambda: cfg
fake = FakeMarlin("CR-6 Max"); p = gantry.Printer(ser=fake, log=lambda *_: None); p.zero_z(0)
p.pick(50, 50)
ok &= check("A: a pick at touch_z 5 is NOT refused by the camera floor min_z 50", any(l.startswith("G0 Z5.0") for l in fake.sent))
ok &= check("A: the camera is still refused at Z40", refused(lambda: p.goto(10, 10, 40)))
cfg["magnet_min_z"] = 8
ok &= check("A: touch_z below magnet_min_z is refused", refused(lambda: p.pick(50, 50)))
cfg["magnet_min_z"] = 0

# ── E: the magnet is not on the nozzle ──────────────────────────────────────────────────────────
cfg.update(magnet_offset_mm=[10, -5])
fake = FakeMarlin("CR-6 Max"); p = gantry.Printer(ser=fake, log=lambda *_: None); p.zero_z(0)
p.pick(100, 100)
ok &= check("E: pick(100, 100) puts the carriage at (90, 105), the magnet over (100, 100)", any(l.startswith("G0 X90.0 Y105.0") for l in fake.sent))
p.goto(90, 125)                      # carriage; the magnet is over (100, 120)
ex, ey = p.tap(100, 100)             # corner at the magnet's (100, 100): the carriage swings about (90, 105)
ok &= check("E: tap swings about the corner in magnet coordinates and returns the magnet's end point",
            any(l.startswith("G2 X110.00 Y105.00 I0.00 J-20.00") for l in fake.sent) and (round(ex), round(ey)) == (120, 100))
cfg.update(magnet_offset_mm=[0, 0])

# ── D: carry-length cap, hard ceiling, retried off, fault latch ─────────────────────────────────
ok &= check("D: a bare magnet-on defaults to magnet_max_s, a carry to carry_max_s (90 s)", p._cap(None) == 20 and p._cap(None, "carry_max_s") == 90)
ok &= check("D: no config can raise the cap past the hard ceiling", p._cap(10_000) == gantry.ABS_MAGNET_MAX_S)
ok &= check("D: a zero or negative limit is refused", refused(lambda: p._cap(0)) and refused(lambda: p._cap(-3)))
fake = FakeMarlin(); p = gantry.Printer(ser=fake, log=lambda *_: None)
p.magnet_on(max_s=0.2)
orig = p.send
fails = {"n": 0}
def flaky(line, timeout=30):
    if line == "M107" and fails["n"] < 2:
        fails["n"] += 1; raise RuntimeError("serial hiccup")
    return orig(line, timeout)
p.send = flaky
time.sleep(0.6)
ok &= check("D: the timer retries a failed M107 and gets it off", fake.sent[-1] == "M107" and not p.magnet and not p.magnet_fault)
p.send = orig
fake = FakeMarlin(); p = gantry.Printer(ser=fake, log=lambda *_: None)
orig = p.send
p.magnet_on(max_s=0.2)
def dead(line, timeout=30):
    if line == "M107":
        raise RuntimeError("cable pulled")
    return orig(line, timeout)
p.send = dead
time.sleep(0.9)
ok &= check("D: if M107 cannot be sent after 3 tries the fault latches", p.magnet_fault and p.magnet)
ok &= check("D: while latched, a move is refused", refused(lambda: orig("G0 X5 Y5")))
ok &= check("D: while latched, M107 is still accepted", orig("M107") == [])
p.send = orig
p.magnet_off()
ok &= check("D: a successful off clears the latch", not p.magnet_fault and not p.magnet)

# ── F: the flip goes through the same gate ──────────────────────────────────────────────────────
cfg = dict(gantry.RIG_DEFAULT, min_z=0, magnet_min_z=0, travel_z=100, touch_z=2)
gantry.config = lambda: cfg
fake = FakeMarlin("CR-6 Max"); p = gantry.Printer(ser=fake, log=lambda *_: None)
lines = p.flip(dry_run=True)
ok &= check("F: a dry run returns checked G1 lines and sends nothing", lines and all(l.startswith("G1 ") and "E" not in l.split("F")[0] for l in lines) and "G1" not in " ".join(fake.sent))
ok &= check("F: no flip move goes faster than y_feed (the bed carries the cards)", all(int(l.split(" F")[1]) <= cfg["y_feed"] for l in lines))
ok &= check("F: a live flip is refused until the hinged head is declared built", refused(lambda: p.flip()))
cfg["flip_hinged_head_built"] = True
ok &= check("F: …and until Z is known", refused(lambda: p.flip()))
p.zero_z(0)
n = len(fake.sent)
p.flip()
seq = fake.sent[n:]
ok &= check("F: the live flip ends with the magnet off and never sent a raw fan value",
            seq[-1] == "M107" and not p.magnet and all(l in ("M106 S255", "M107") for l in seq if l.startswith(("M106", "M107"))))
ok &= check("F: it turned the magnet on (through the timed magnet_on) before the arc", "M106 S255" in seq)
cfg["flip"] = {"fence_x": 460.0}
ok &= check("F: a waypoint outside the travel is refused before anything moves", refused(lambda: p.flip_plan()))
cfg["flip"] = {}
fake = FakeMarlin("CR-6 Max"); p = gantry.Printer(ser=fake, log=lambda *_: None); cfg["flip_hinged_head_built"] = True; p.zero_z(0)
orig_send = p.send
def boom(line, timeout=30):
    if line.startswith("G1") and "Z" in line and p.magnet:
        raise RuntimeError("serial lost mid-flip")
    return orig_send(line, timeout)
p.send = boom
try:
    p.flip()
except RuntimeError:
    pass
p.send = orig_send
ok &= check("F: an error mid-flip still turns the magnet off", fake.sent[-1] == "M107" and not p.magnet)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
