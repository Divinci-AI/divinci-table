"""The camera gantry: a Creality printer carrying the Canon over the table.

The printer is only a motion stage here. This module never heats anything and never extrudes:
every line goes through `safe()`, which allows moves, homing and status queries and refuses the
rest. Moves are clamped to the machine's travel, which comes from table/.cache/gantry.json or
from the printer's MACHINE_TYPE.

  ~/.venvs/table/bin/python table/gantry.py ports             # what's plugged in
  ~/.venvs/table/bin/python table/gantry.py info              # firmware, machine, position
  ~/.venvs/table/bin/python table/gantry.py home              # X and Y only (see Z below)
  ~/.venvs/table/bin/python table/gantry.py goto 110 110 [Z]
  ~/.venvs/table/bin/python table/gantry.py snap              # one photo where the camera is now
  ~/.venvs/table/bin/python table/gantry.py plan --area all --z 300   # how many shots, no motion
  ~/.venvs/table/bin/python table/gantry.py scan --area south [--z 300] [--post]

Z: homing Z drives the head DOWN until the endstop or probe triggers, so with a camera hanging off
the carriage it can hit the table. `home` leaves Z alone; `home --z` is for when the camera
is clear of the probe path.

`scan` photographs a grid, stitches it into one overhead frame, and with --post sends that frame to
the table server's /api/board, the same endpoint a fixed overhead camera uses.

The magnet hand (hardware/gantry/README.md, magnet route): a 24 V electromagnet on the part-cooling fan
output. It is ONLY ever full on (M106 S255) or off (M107): part power holds weakly and drops cards. The
printer sends M107 the moment it connects; a timer turns the magnet off after `magnet_max_s` (gantry.json,
default 20 s: the maker warns against long unloaded energising); leaving `with Printer(...)`, an error, or
SIGTERM all turn it off. Tapping is a quarter-circle arc (G2/G3) around a card corner pinned on a rubber foot.

  ~/.venvs/table/bin/python table/gantry.py magnet on|off
  ~/.venvs/table/bin/python table/gantry.py carry X1 Y1 X2 Y2  # pick at 1, put down at 2 (needs touch_z)
  ~/.venvs/table/bin/python table/gantry.py tap CX CY X Y [--ccw]   # card held at X Y, corner pinned at CX CY
"""
from __future__ import annotations

import argparse
import glob
import math
import signal
import threading
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / ".cache" / "gantry"
CONFIG = HERE / ".cache" / "gantry.json"

# Travel (mm) for the Creality machines we're likely to meet; gantry.json overrides.
TRAVEL = {
    "ender-3": (220, 220, 250), "ender-3 v2": (220, 220, 250), "ender-3 s1": (220, 220, 270),
    "ender-5": (220, 220, 300), "ender-5 plus": (350, 350, 400), "ender-6": (250, 250, 400),
    "cr-6 se": (235, 235, 250), "cr-6 max": (400, 400, 400),
    "cr-10": (300, 300, 400), "cr-10 s4": (400, 400, 400), "cr-10 s5": (500, 500, 500),
    "cr-10 max": (450, 450, 470), "cr-10 v3": (300, 300, 400),
}
SAFE_DEFAULT = (200, 200, 200)            # unknown machine: stay inside the smallest one

# The camera rig, measured once after mounting and kept in gantry.json:
#   min_z            lowest Z the head may go with the camera on (lens must clear the bed + cards)
#   lens_at_z0_mm    lens front to bed distance when Z = 0 (negative: lens hangs below the nozzle)
#   cam_offset_mm    [x, y] of the lens axis relative to the nozzle
#   focal_mm, sensor_mm   18 mm kit lens at its widest on the T5i's APS-C sensor (22.3 x 14.9)
#   y_feed           the bed carries the cards, so Y moves gently or they slide
RIG_DEFAULT = {"min_z": 0, "lens_at_z0_mm": 0, "cam_offset_mm": [0, 0], "focal_mm": 18,
               "sensor_mm": [22.3, 14.9], "y_feed": 1200, "overlap": 0.3, "magnet_max_s": 20, "touch_z": None,
               "lift_mm": 20, "arc_feed": 1500, "travel_z": None,
               "rack": {"slot0": None, "pitch": 46, "rise": 3.6, "slots": 7}}
# travel_z: the head rises at least this high before any X/Y move while carrying, so it clears the hand rack's fence
# rack: the printed hand rack (hardware/gantry/parts/hand_rack.scad). slot0 = [x, y] of the centre of slot 0's card,
#   measured in Step 7; pitch and rise match the .scad. Each slot k sits k*pitch to the right and k*rise higher; a
#   card leaves and enters its slot sideways, from one pitch to the left, because the next card's shelf covers it.
MAGNET_ON, MAGNET_OFF = "M106 S255", "M107"


def config() -> dict:
    cfg = dict(RIG_DEFAULT)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text()))
    return cfg


def footprint(z: float, cfg: dict | None = None) -> tuple[float, float]:
    """Width x height (mm) of bed the camera sees from head height z."""
    cfg = cfg or config()
    d = z + cfg["lens_at_z0_mm"]
    if d <= 0:
        raise Refused(f"at Z{z} the lens would be at or below the bed")
    return d * cfg["sensor_mm"][0] / cfg["focal_mm"], d * cfg["sensor_mm"][1] / cfg["focal_mm"]


def plan(area: tuple[float, float, float, float], z: float, cfg: dict | None = None):
    """Shots (bed-point centres) covering area=(x0, y0, x1, y1) from height z with the configured overlap."""
    import math
    cfg = cfg or config()
    fw, fh = footprint(z, cfg)
    step_w, step_h = fw * (1 - cfg["overlap"]), fh * (1 - cfg["overlap"])
    x0, y0, x1, y1 = area
    cols = 1 if x1 - x0 <= fw else math.ceil((x1 - x0 - fw) / step_w) + 1
    rows = 1 if y1 - y0 <= fh else math.ceil((y1 - y0 - fh) / step_h) + 1
    cx = (x0 + fw / 2, x1 - fw / 2) if cols > 1 else ((x0 + x1) / 2,) * 2
    cy = (y0 + fh / 2, y1 - fh / 2) if rows > 1 else ((y0 + y1) / 2,) * 2
    return grid(cx[0], cy[0], cx[1], cy[1], cols, rows), (cols, rows), (fw, fh)


# Two players on the 400 x 400 bed, each with a 400 x 195 half: two rows of six cards
# (63 x 88 mm) fit, ~12 permanents each. Four players need the bigger gantry (docs/claude-brain.md).
DUEL_AREAS = {"north": (0, 205, 400, 400), "south": (0, 0, 400, 195), "all": (0, 0, 400, 400)}

# What may be sent. Anything that heats, extrudes, runs fans, writes EEPROM or prints is refused.
ALLOWED = {"G0", "G1", "G4", "G28", "G90", "G91", "M17", "M18", "M84", "M114", "M115", "M119",
           "M400", "M503", "M220", "M211", "M105"}
HEATERS_OFF = {"M104", "M140"}          # allowed ONLY with S0: turning heat off is always safe


class Refused(ValueError):
    pass


def safe(line: str) -> str:
    """The one gate every G-code line passes. Returns the cleaned line or raises Refused."""
    line = line.split(";")[0].strip().upper()
    if not line:
        raise Refused("empty line")
    word = line.split()[0]
    if word in HEATERS_OFF and re.fullmatch(rf"{word}( T\d)? S0", line):
        return line
    if word in ("M106", "M107"):                   # the part-fan output drives the magnet: full on or off, nothing else
        if line in (MAGNET_ON, MAGNET_OFF):
            return line
        raise Refused(f"{line}: the fan output is the magnet; only '{MAGNET_ON}' and '{MAGNET_OFF}' are allowed")
    if word in ("G2", "G3"):                       # arcs, for tapping: X Y end and I J centre offset, never extruding
        if re.search(r"\bE", line) or not re.fullmatch(r"G[23]( [XYIJF]-?[\d.]+)+", line):
            raise Refused(f"{line}: an arc needs X Y I J (F) only")
        return line
    if word not in ALLOWED:
        raise Refused(f"{word} is not a camera move (no heating, extruding, fans or EEPROM)")
    if word in ("G0", "G1") and re.search(r"\bE", line):
        raise Refused("moves may not extrude (E)")
    if word == "M211" and "S0" in line:
        raise Refused("software endstops stay on")
    return line


def find_ports() -> list[str]:
    pats = ["/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.usbmodem*", "/dev/cu.SLAB_USBtoUART*"]
    return sorted(p for pat in pats for p in glob.glob(pat))


class Printer:
    def __init__(self, port: str | None = None, baud: int | None = None, ser=None, log=print):
        self.log = log
        self._lock = threading.RLock()                 # the magnet's timer and the caller share one serial line
        self._magnet_timer: threading.Timer | None = None
        self.magnet = False
        if ser is None:
            import serial
            ports = [port] if port else find_ports()
            if not ports:
                raise SystemExit("no printer on USB: no /dev/cu.usbserial*, wchusbserial*, usbmodem*. "
                                 "Check the printer is ON, the cable carries data, and macOS allowed the accessory.")
            for b in ([baud] if baud else [115200, 250000]):
                ser = serial.Serial(ports[0], b, timeout=0.5)
                time.sleep(2.0)                    # most boards reset when the port opens
                ser.reset_input_buffer()
                self.ser = ser
                try:
                    if "FIRMWARE_NAME" in " ".join(self.send("M115", timeout=5)):
                        self.port, self.baud = ports[0], b
                        break
                except TimeoutError:
                    pass
                ser.close()
            else:
                raise SystemExit(f"{ports[0]} opened but no Marlin answer at 115200 or 250000 baud")
        self.ser = ser
        self.travel = self._travel()
        self.magnet_off()                              # whatever a crashed run left behind

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.magnet_off()
        return False

    def send(self, line: str, timeout: float = 30) -> list[str]:
        cmd = safe(line)
        with self._lock:
            return self._send_locked(cmd, timeout)

    def _send_locked(self, cmd: str, timeout: float) -> list[str]:
        self.ser.write((cmd + "\n").encode())
        out, deadline = [], time.time() + timeout
        while time.time() < deadline:
            raw = self.ser.readline().decode(errors="replace").strip()
            if not raw:
                continue
            if raw.startswith("ok"):
                return out + ([raw[2:].strip()] if raw[2:].strip() else [])   # M105 answers on the ok line
            if raw.lower().startswith("error"):
                raise RuntimeError(f"{cmd}: {raw}")
            if "busy" in raw:                      # long move or homing still running
                deadline = time.time() + timeout
                continue
            out.append(raw)
        raise TimeoutError(f"no 'ok' for {cmd} in {timeout}s")

    def info(self) -> dict:
        fw = " ".join(self.send("M115"))
        d = dict(re.findall(r"([A-Z_]+):(.*?)(?= [A-Z_]+:|$)", fw))
        return {"firmware": d.get("FIRMWARE_NAME", "").strip(), "machine": d.get("MACHINE_TYPE", "").strip(),
                "travel": self.travel, "position": self.position()}

    def _travel(self) -> tuple[float, float, float]:
        if CONFIG.exists():
            t = json.loads(CONFIG.read_text()).get("travel")
            if t:
                return tuple(t)
        machine = re.search(r"MACHINE_TYPE:([^\n]+?)(?= [A-Z_]+:|$)", " ".join(self.send("M115")))
        name = (machine.group(1) if machine else "").strip().lower()
        for k in sorted(TRAVEL, key=len, reverse=True):
            if k in name:
                return TRAVEL[k]
        self.log(f"unknown machine '{name}': limiting moves to {SAFE_DEFAULT}; set travel in {CONFIG}")
        return SAFE_DEFAULT

    def position(self) -> dict:
        for l in self.send("M114"):
            m = dict(re.findall(r"([XYZE]):(-?[\d.]+)", l.split("Count")[0]))
            if m:
                return {k: float(v) for k, v in m.items() if k in "XYZ"}
        return {}

    def home(self, z: bool = False):
        self.send("G28 X Y" + (" Z" if z else ""), timeout=120)

    def goto(self, x=None, y=None, z=None, feed=3000):
        cfg = config()
        if z is not None and z < cfg["min_z"]:
            raise Refused(f"Z{z} is below the camera floor min_z={cfg['min_z']} (gantry.json)")
        if y is not None:
            feed = min(feed, cfg["y_feed"])         # the bed carries the cards
        parts = []
        for axis, v, hi in (("X", x, self.travel[0]), ("Y", y, self.travel[1]), ("Z", z, self.travel[2])):
            if v is None:
                continue
            if not 0 <= v <= hi:
                raise Refused(f"{axis}{v} is outside 0..{hi} mm")
            parts.append(f"{axis}{v:.1f}")
        if not parts:
            return
        self.send("G90")
        self.send(f"G0 {' '.join(parts)} F{feed}")
        self.send("M400", timeout=120)             # wait until the move has finished

    # ── the magnet hand ───────────────────────────────────────────────────────────────────────────
    def magnet_on(self, max_s: float | None = None):
        """Full on, with a timer that turns it off after max_s (gantry.json magnet_max_s) whatever happens next."""
        cap = float(max_s if max_s is not None else config().get("magnet_max_s") or 20)
        with self._lock:
            if self._magnet_timer:
                self._magnet_timer.cancel()
            self.send(MAGNET_ON)
            self.magnet = True
            self._magnet_timer = threading.Timer(cap, self._magnet_timeout)
            self._magnet_timer.daemon = True
            self._magnet_timer.start()

    def _magnet_timeout(self):
        self.log("magnet on too long: turning it off")
        self.magnet_off()

    def magnet_off(self):
        with self._lock:
            if self._magnet_timer:
                self._magnet_timer.cancel()
                self._magnet_timer = None
            self.send(MAGNET_OFF)
            self.magnet = False

    def arc(self, x: float, y: float, cx: float, cy: float, ccw: bool = False, feed: float | None = None):
        """From the current XY to (x, y) around the centre (cx, cy). The whole circle must stay inside the travel,
        so no part of the swing can leave it."""
        pos = self.position()
        r = math.hypot(pos["X"] - cx, pos["Y"] - cy)
        if abs(math.hypot(x - cx, y - cy) - r) > 0.5:
            raise Refused(f"({x}, {y}) is not on the circle of radius {r:.1f} around ({cx}, {cy})")
        for axis, c, hi in (("X", cx, self.travel[0]), ("Y", cy, self.travel[1])):
            if c - r < 0 or c + r > hi:
                raise Refused(f"an arc of radius {r:.1f} around {axis}{c} leaves 0..{hi} mm")
        feed = min(feed or config()["arc_feed"], config()["y_feed"])
        self.send("G90")
        self.send(f"G{3 if ccw else 2} X{x:.2f} Y{y:.2f} I{cx - pos['X']:.2f} J{cy - pos['Y']:.2f} F{feed:.0f}")
        self.send("M400", timeout=120)

    def _touch_z(self) -> float:
        z = config().get("touch_z")
        if z is None:
            raise Refused("measure touch_z (the Z where the magnet just touches a card) into gantry.json first")
        return float(z)

    def _travel_z(self, z: float) -> float:
        cfg = config()
        return max(z + cfg["lift_mm"], float(cfg.get("travel_z") or 0))

    def _over(self, x: float, y: float, z: float):
        """Up to the travel height first (never diagonally through the rack's fence), across, then down to z."""
        up = self._travel_z(z)
        self.goto(z=max(up, self.position()["Z"]))
        self.goto(x, y)
        self.goto(z=z)
        return up

    def pick(self, x: float, y: float, dz: float = 0.0, slide_x: float = 0.0):
        """Grip the card centred at (x, y). dz: how much higher than a card lying on the bed (a rack slot).
        slide_x: after gripping, slide the card this far in X at the same height before lifting."""
        z = self._touch_z() + dz
        up = self._over(x, y, z)
        self.magnet_on()
        time.sleep(0.2)
        if slide_x:
            self.goto(x + slide_x)
        self.goto(z=up)

    def place(self, x: float, y: float, dz: float = 0.0, slide_x: float = 0.0):
        """Set the held card down centred at (x, y); with slide_x it comes down at x + slide_x and slides in."""
        z = self._touch_z() + dz
        up = self._over(x + slide_x, y, z)
        if slide_x:
            self.goto(x)
        self.magnet_off()
        time.sleep(0.2)
        self.goto(z=up)

    def rack_slot(self, k: int) -> dict:
        """Where slot k of the hand rack is: pick(**rack_slot(k)) takes its card, place(**rack_slot(k)) fills it."""
        r = dict(RIG_DEFAULT["rack"], **(config().get("rack") or {}))
        if r.get("slot0") is None:
            raise Refused("measure the hand rack's slot 0 (rack.slot0 in gantry.json) first")
        if not 0 <= k < r["slots"]:
            raise Refused(f"the rack has slots 0..{r['slots'] - 1}")
        x0, y0 = r["slot0"]
        return {"x": x0 + k * r["pitch"], "y": y0, "dz": k * r["rise"], "slide_x": -r["pitch"] if k else 0.0}

    def tap(self, cx: float, cy: float, ccw: bool = False):
        """The held card turns a quarter about its corner pinned at (cx, cy): the magnet swings a quarter circle."""
        pos = self.position()
        dx, dy = pos["X"] - cx, pos["Y"] - cy
        ex, ey = (cx - dy, cy + dx) if ccw else (cx + dy, cy - dx)
        self.arc(ex, ey, cx, cy, ccw=ccw)
        return ex, ey

    def temps(self) -> dict:
        for l in self.send("M105"):
            t = re.findall(r"\b([TB]):\s*(-?[\d.]+)\s*/\s*(-?[\d.]+)", l)
            if t:
                return {("nozzle" if k == "T" else "bed"): {"now": float(a), "target": float(b)} for k, a, b in t}
        return {}

    def heaters_off(self):
        self.send("M104 S0")
        self.send("M140 S0")

    def motors_off(self):
        self.send("M84")


# ── the camera ────────────────────────────────────────────────────────────────────────────────
def canon_port() -> str | None:
    out = subprocess.run(["gphoto2", "--auto-detect"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "Canon" in line:
            return line.split()[-1]
    return None


def snap(path: Path) -> Path:
    port = canon_port()
    if not port:
        raise SystemExit("Canon not found by gphoto2: is it on, in P mode, and allowed to connect?")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["gphoto2", "--port", port, "--capture-image-and-download", "--force-overwrite",
                    "--filename", str(path)], check=True, capture_output=True)
    return path


# ── a grid scan, stitched ─────────────────────────────────────────────────────────────────────
def grid(x0, y0, x1, y1, cols, rows) -> list[tuple[float, float]]:
    """Serpentine order so the head never crosses the whole table between shots."""
    xs = [x0 + (x1 - x0) * i / max(1, cols - 1) for i in range(cols)]
    ys = [y0 + (y1 - y0) * j / max(1, rows - 1) for j in range(rows)]
    return [(x, y) for j, y in enumerate(ys) for x in (xs if j % 2 == 0 else xs[::-1])]


def stitch(paths: list[Path], out: Path) -> Path | None:
    import cv2
    imgs = [cv2.imread(str(p)) for p in paths]
    imgs = [cv2.resize(i, None, fx=0.5, fy=0.5) for i in imgs if i is not None]
    if len(imgs) == 1:
        cv2.imwrite(str(out), imgs[0])
        return out
    status, pano = cv2.Stitcher_create(cv2.Stitcher_SCANS).stitch(imgs)
    if status != cv2.Stitcher_OK:
        print(f"stitch failed (status {status}); the single shots are still in {out.parent}")
        return None
    cv2.imwrite(str(out), pano)
    return out


def scan(p: Printer, area=DUEL_AREAS["all"], z=None, settle=1.0, post=False) -> Path | None:
    cfg = config()
    z = z if z is not None else p.travel[2]
    shots_at, (cols, rows), (fw, fh) = plan(area, z, cfg)
    ox, oy = cfg["cam_offset_mm"]
    run = CACHE / time.strftime("scan-%Y%m%d-%H%M%S")
    print(f"{cols}x{rows} shots, each {fw:.0f}x{fh:.0f} mm, from Z{z:.0f}")
    shots = []
    for i, (bx, by) in enumerate(shots_at):
        x = min(max(bx - ox, 0), p.travel[0])
        y = min(max(by - oy, 0), p.travel[1])
        p.goto(x, y, z)
        time.sleep(settle)                        # the bed and camera stop swaying
        shots.append(snap(run / f"{i:02d}_x{bx:.0f}_y{by:.0f}.jpg"))
        print(f"shot {i + 1}/{len(shots_at)} at bed X{bx:.0f} Y{by:.0f}")
    out = stitch(shots, run / "board.jpg")
    if out and post:
        req = urllib.request.Request("http://127.0.0.1:8800/api/board", data=out.read_bytes(),
                                     headers={"Content-Type": "image/jpeg"})
        print(urllib.request.urlopen(req, timeout=60).read().decode()[:400])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports")
    sub.add_parser("info")
    h = sub.add_parser("home"); h.add_argument("--z", action="store_true")
    g = sub.add_parser("goto"); g.add_argument("x", type=float); g.add_argument("y", type=float)
    g.add_argument("z", type=float, nargs="?")
    sub.add_parser("snap")
    sub.add_parser("off")
    sub.add_parser("temps")
    sub.add_parser("cool")
    s = sub.add_parser("scan")
    s.add_argument("--area", choices=list(DUEL_AREAS), default="all")
    s.add_argument("--z", type=float); s.add_argument("--post", action="store_true")
    mg = sub.add_parser("magnet"); mg.add_argument("state", choices=["on", "off"])
    cr = sub.add_parser("carry")                   # one run: the magnet goes off when the program ends
    for k in ("x1", "y1", "x2", "y2"):
        cr.add_argument(k, type=float)
    tp = sub.add_parser("tap")
    for k in ("cx", "cy", "x", "y"):
        tp.add_argument(k, type=float)
    tp.add_argument("--ccw", action="store_true")
    pl = sub.add_parser("plan")
    pl.add_argument("--area", choices=list(DUEL_AREAS), default="all"); pl.add_argument("--z", type=float, default=300)
    a = ap.parse_args(argv)

    if a.cmd == "ports":
        print("\n".join(find_ports()) or "no printer serial port", "\nCanon:", canon_port() or "not found")
        return
    if a.cmd == "plan":
        pts, (c, r), (fw, fh) = plan(DUEL_AREAS[a.area], a.z)
        print(f"{a.area} from Z{a.z:.0f}: {c}x{r} shots of {fw:.0f}x{fh:.0f} mm at", [(round(x), round(y)) for x, y in pts])
        return
    if a.cmd == "snap":
        print(snap(CACHE / time.strftime("snap-%Y%m%d-%H%M%S.jpg")))
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # so `with` turns the magnet off on a kill too
    with Printer(a.port, a.baud) as p:
        run(p, a)


def run(p: Printer, a) -> None:
    if a.cmd == "magnet":
        if a.state == "on":
            p.magnet_on()
            print("magnet on; Ctrl-C (or the time limit) turns it off")
            try:
                while p.magnet:
                    time.sleep(0.2)
            except KeyboardInterrupt:
                pass
        else:
            p.magnet_off()
    elif a.cmd == "carry":
        p.pick(a.x1, a.y1)
        p.place(a.x2, a.y2)
        print(p.position())
    elif a.cmd == "tap":
        p.goto(a.x, a.y); print(p.tap(a.cx, a.cy, ccw=a.ccw))
    elif a.cmd == "info":
        print(json.dumps(p.info(), indent=2))
    elif a.cmd == "home":
        p.home(z=a.z); print(p.position())
    elif a.cmd == "goto":
        p.goto(a.x, a.y, a.z); print(p.position())
    elif a.cmd == "temps":
        print(json.dumps(p.temps()))
    elif a.cmd == "cool":
        p.heaters_off(); print(json.dumps(p.temps()))
    elif a.cmd == "off":
        p.motors_off()
    elif a.cmd == "scan":
        print(scan(p, DUEL_AREAS[a.area], z=a.z, post=a.post))


if __name__ == "__main__":
    sys.exit(main())
