"""The camera gantry: a Creality printer carrying the Canon over the table.

The printer is only a motion stage here. This module never heats anything and never extrudes:
every line goes through `safe()`, which allows moves, homing and status queries and refuses the
rest. Moves are clamped to the machine's travel, which comes from table/.cache/gantry.json or
from the printer's MACHINE_TYPE.

  ~/.venvs/table/bin/python table/gantry.py ports             # what's plugged in
  ~/.venvs/table/bin/python table/gantry.py info              # firmware, machine, position
  ~/.venvs/table/bin/python table/gantry.py home              # X and Y only (see Z below)
  ~/.venvs/table/bin/python table/gantry.py zero-z [H]        # declare Z: nozzle H mm above the bed (default 0); required before any Z move
  ~/.venvs/table/bin/python table/gantry.py flip --dry-run    # the edge flip's checked lines, nothing sent
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
#   focal_mm, sensor_mm   18 mm kit lens at its widest on the T3i's APS-C sensor (22.3 x 14.9)
#   y_feed           the bed carries the cards, so Y moves gently or they slide
RIG_DEFAULT = {"min_z": 0, "lens_at_z0_mm": 0, "cam_offset_mm": [0, 0], "focal_mm": 18,
               "sensor_mm": [22.3, 14.9], "y_feed": 1200, "overlap": 0.3, "magnet_max_s": 20, "touch_z": None,
               "lift_mm": 20, "arc_feed": 1500, "travel_z": None,
               "magnet_min_z": None, "carry_max_s": 90, "magnet_offset_mm": [0, 0],
               "flip": {}, "flip_hinged_head_built": False,
               "rack": {"slot0": None, "pitch": 46, "rise": 3.6, "slots": 7, "base_t": 2.0}}
# min_z is the CAMERA's floor (the lens must clear the bed). The magnet hand has its own floor, magnet_min_z: touch_z is
#   below min_z by design, because the magnet touches a card while the lens is not on the head. goto(tool="magnet") uses it.
#   magnet_min_z and travel_z default to None = NOT MEASURED: every magnet Z move, pick, place and flip is refused until they are set.
# magnet_offset_mm: [x, y] of the magnet's axis relative to the nozzle. Every pick, place, rack move, tap and flip is given in
#   BED coordinates of the magnet and the carriage goes to that point minus the offset.
# carry_max_s: how long the magnet may stay on across one pick-and-place. magnet_max_s covers a bare `magnet on`.
# rack.base_t: thickness of the rack's base (hand_rack.scad base_t); slot 0's shelf is that high above the bed, so every rack
#   pick/place is base_t higher than a card lying on the bed.
# flip_hinged_head_built: a live flip() is refused until this is true (the passive pitch hinge is a design, not hardware yet).
ABS_MAGNET_MAX_S = 180                 # no config value may exceed this
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


def safe(line: str, internal: bool = False) -> str:
    """The one gate every G-code line passes. Returns the cleaned line or raises Refused. `internal` is only for the two
    Printer methods that own a dangerous command and its bookkeeping (home(z, clear) and zero_z)."""
    line = line.split(";")[0].strip().upper()
    if not line:
        raise Refused("empty line")
    word = line.split()[0]
    if word == "G28" and line != "G28 X Y" and not (internal and re.fullmatch(r"G28 X Y Z", line)):
        raise Refused(f"{line}: only 'G28 X Y' may be sent; homing Z goes through Printer.home(z=True, clear=True)")
    if word == "G91":
        raise Refused("relative moves skip the travel and floor checks: use absolute moves (G90) through Printer.goto")
    if word == "M220" and line != "M220 S100":
        raise Refused(f"{line}: a feed multiplier would defeat y_feed; only 'M220 S100'")
    if word in HEATERS_OFF and re.fullmatch(rf"{word}( T\d)? S0", line):
        return line
    if word in ("M106", "M107"):                   # the part-fan output drives the magnet: full on or off, nothing else
        if line in (MAGNET_ON, MAGNET_OFF):
            return line
        raise Refused(f"{line}: the fan output is the magnet; only '{MAGNET_ON}' and '{MAGNET_OFF}' are allowed")
    if word == "G92":                              # only 'G92 Z<n>', and only Printer.zero_z() sends it: it moves where Z IS
        if not (internal and re.fullmatch(r"G92 Z-?[\d.]+", line)):
            raise Refused(f"{line}: G92 declares where an axis is; only Printer.zero_z() may send 'G92 Z<height>'")
        return line
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


def print_running() -> bool:
    """True while scripts/printer/sender.py is streaming a print. The serial port is the sender's then: a second reader steals its 'ok' lines, and opening the port can reset the board."""
    try:
        return subprocess.run(["pgrep", "-f", "scripts/printer/sender.py|printer/sender.py /"], capture_output=True).returncode == 0
    except OSError:
        return False


def find_ports() -> list[str]:
    pats = ["/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.usbmodem*", "/dev/cu.SLAB_USBtoUART*"]
    return sorted(p for pat in pats for p in glob.glob(pat))


class Printer:
    def __init__(self, port: str | None = None, baud: int | None = None, ser=None, log=print):
        self.log = log
        self._lock = threading.RLock()                 # the magnet's timer and the caller share one serial line
        self._magnet_timer: threading.Timer | None = None
        self.magnet = False
        self.magnet_fault = False                      # the timer could not turn the magnet off: refuse everything but M107/M84
        self.z_known = False                           # Z is only known after zero_z() / an explicit home(z=True, clear=True); never across a reconnect
        if ser is None:
            if print_running():
                raise SystemExit("a print is streaming (scripts/printer/sender.py is running): the serial port is the sender's. Do not open it until the print ends "
                                 "(opening it can reset the board and steals the sender's 'ok' lines). Stop the print first with `touch /tmp/printer-stop`.")
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

    def send(self, line: str, timeout: float = 30, _internal: bool = False) -> list[str]:
        cmd = safe(line, internal=_internal)
        if self.magnet_fault and cmd not in (MAGNET_OFF, "M84", "M114", "M400"):
            raise Refused(f"{cmd}: the magnet could not be switched off; only M107, M84, M114 and M400 are accepted until it is")
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

    def home(self, z: bool = False, clear: bool = False):
        """X and Y only by default. Homing Z drives the head DOWN to the endstop/probe: with the magnet or camera hanging
        below the nozzle it hits the bed. So it needs clear=True, the caller's statement that nothing hangs below the nozzle
        and the bed is clear; only then is Z known."""
        if z and not clear:
            raise Refused("homing Z drives the head down: remove the magnet head and camera from under the nozzle, "
                          "then pass clear=True (CLI: home --z --clear)")
        self.send("G28 X Y" + (" Z" if z else ""), timeout=120, _internal=True)
        if z:
            self.z_known = True

    def zero_z(self, height: float = 0.0):
        """The explicit way to say where Z is without homing it: put the NOZZLE at `height` mm above the bed by hand
        (a sheet of paper, with the magnet head off), then call this. Nothing else sets z_known."""
        self.send(f"G92 Z{height:g}", _internal=True)
        self.z_known = True

    def _need_z(self):
        if not self.z_known:
            raise Refused("Z is not known since this connection opened: run zero_z() (nozzle at a measured height) or "
                          "home(z=True, clear=True) first. A Z move from an unknown Z can drive the head into the table.")

    def goto(self, x=None, y=None, z=None, feed=3000, tool="camera"):
        """Absolute move. tool="camera" keeps the lens above min_z; tool="magnet" uses magnet_min_z (touch_z lives below min_z)."""
        cfg = config()
        if z is not None:
            self._need_z()
            floor, name = (cfg["magnet_min_z"], "magnet_min_z") if tool == "magnet" else (cfg["min_z"], "the camera floor min_z")
            if floor is None:
                raise Refused("magnet_min_z is not measured (gantry.json): the lowest Z the magnet may go, with its hang below the nozzle")
            if z < floor:
                raise Refused(f"Z{z} is below {name}={floor} (gantry.json)")
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
    def offset(self) -> tuple[float, float]:
        """[x, y] of the magnet's axis relative to the nozzle (gantry.json magnet_offset_mm)."""
        ox, oy = config().get("magnet_offset_mm") or (0, 0)
        return float(ox), float(oy)

    def _cap(self, max_s: float | None, key: str = "magnet_max_s") -> float:
        cap = float(max_s if max_s is not None else config().get(key) or 20)
        if cap <= 0:
            raise Refused("the magnet's time limit must be positive")
        return min(cap, ABS_MAGNET_MAX_S)              # whatever the config says, never longer than this

    def magnet_on(self, max_s: float | None = None):
        """Full on, with a timer that turns it off after max_s (default gantry.json magnet_max_s, at most ABS_MAGNET_MAX_S)
        whatever happens next. The timer is started BEFORE the on command, so a failure in between cannot leave the coil on with
        nothing counting; it runs on its own thread, so a blocked caller cannot stop it from starting its attempt."""
        cap = self._cap(max_s)
        with self._lock:
            if self._magnet_timer:
                self._magnet_timer.cancel()
            t = threading.Timer(cap, lambda: self._magnet_timeout(t))
            t.daemon = True
            self._magnet_timer = t
            t.start()
            try:
                self.send(MAGNET_ON)
            except Exception:
                try:
                    self.magnet_off()                  # whatever the line did, ask for off
                except Exception:
                    pass
                raise
            self.magnet = True

    def _magnet_timeout(self, timer=None):
        with self._lock:
            if timer is not None and timer is not self._magnet_timer:
                return                                 # a newer magnet_on replaced this timer while it waited for the lock
        self.log("magnet on too long: turning it off")
        for attempt in range(3):
            try:
                self.magnet_off()
                return
            except Exception as e:                     # the serial line is the only way to switch it off: try again
                self.log(f"magnet off failed ({e}); attempt {attempt + 1} of 3")
                time.sleep(0.05)
        self.magnet_fault = True                       # still on, as far as anyone knows: refuse every other command
        self.log("MAGNET MAY STILL BE ON and could not be switched off over serial: cut its power at the supply")

    def magnet_off(self):
        with self._lock:
            if self._magnet_timer:
                self._magnet_timer.cancel()
                self._magnet_timer = None
            self.send(MAGNET_OFF)
            self.magnet = False
            self.magnet_fault = False

    def arc(self, x: float, y: float, cx: float, cy: float, ccw: bool = False, feed: float | None = None):
        """From the current XY to (x, y) around the centre (cx, cy), all in CARRIAGE coordinates. The whole circle must
        stay inside the travel, so no part of the swing can leave it."""
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
        z = float(z)
        if config()["magnet_min_z"] is None:
            raise Refused("magnet_min_z is not measured (gantry.json)")
        if z < config()["magnet_min_z"]:
            raise Refused(f"touch_z={z} is below magnet_min_z={config()['magnet_min_z']}")
        return z

    def _travel_z(self, z: float) -> float:
        cfg = config()
        if cfg.get("travel_z") is None:
            raise Refused("travel_z is not measured (gantry.json): the height that clears the tallest thing on the bed "
                          "(the deck box is ~118 mm, the rack's fence 40 mm)")
        return max(z + cfg["lift_mm"], float(cfg["travel_z"]))

    def _over(self, x: float, y: float, z: float):
        """Up to the travel height first (never diagonally through the rack's fence), across, then down to z.
        x, y are where the MAGNET goes (bed coordinates); the carriage goes there minus the magnet offset."""
        ox, oy = self.offset()
        up = self._travel_z(z)
        self.goto(z=max(up, self.position()["Z"]), tool="magnet")
        self.goto(x - ox, y - oy, tool="magnet")
        self.goto(z=z, tool="magnet")
        return up

    def pick(self, x: float, y: float, dz: float = 0.0, slide_x: float = 0.0):
        """Grip the card centred at (x, y) (the magnet's position). dz: how much higher than a card lying on the bed (a rack
        slot, which includes the rack's base). slide_x: after gripping, slide the card this far in X at the same height."""
        z = self._touch_z() + dz
        up = self._over(x, y, z)
        self.magnet_on(self._cap(None, "carry_max_s"))
        time.sleep(0.2)
        if slide_x:
            self.goto(x + slide_x - self.offset()[0], tool="magnet")
        self.goto(z=up, tool="magnet")

    def place(self, x: float, y: float, dz: float = 0.0, slide_x: float = 0.0):
        """Set the held card down centred at (x, y); with slide_x it comes down at x + slide_x and slides in."""
        z = self._touch_z() + dz
        up = self._over(x + slide_x, y, z)
        if slide_x:
            self.goto(x - self.offset()[0], tool="magnet")
        self.magnet_off()
        time.sleep(0.2)
        self.goto(z=up, tool="magnet")

    def rack_slot(self, k: int) -> dict:
        """Where slot k of the hand rack is: pick(**rack_slot(k)) takes its card, place(**rack_slot(k)) fills it.
        dz includes the rack's base (rack.base_t): slot 0's shelf is base_t above the bed, not on it."""
        r = dict(RIG_DEFAULT["rack"], **(config().get("rack") or {}))
        if r.get("slot0") is None:
            raise Refused("measure the hand rack's slot 0 (rack.slot0 in gantry.json) first")
        if not 0 <= k < r["slots"]:
            raise Refused(f"the rack has slots 0..{r['slots'] - 1}")
        x0, y0 = r["slot0"]
        return {"x": x0 + k * r["pitch"], "y": y0, "dz": r["base_t"] + k * r["rise"], "slide_x": -r["pitch"] if k else 0.0}

    def tap(self, cx: float, cy: float, ccw: bool = False):
        """The held card turns a quarter about its corner pinned at (cx, cy): the magnet swings a quarter circle.
        (cx, cy) and the result are the magnet's bed coordinates; the carriage swings about (cx, cy) minus the offset."""
        ox, oy = self.offset()
        pos = self.position()
        ccx, ccy = cx - ox, cy - oy
        dx, dy = pos["X"] - ccx, pos["Y"] - ccy
        ex, ey = (ccx - dy, ccy + dx) if ccw else (ccx + dy, ccy - dx)
        self.arc(ex, ey, ccx, ccy, ccw=ccw)
        return ex + ox, ey + oy

    # ── the edge flip (hardware/gantry/flip_path.py) ──────────────────────────────────────────────
    def flip_plan(self, overrides: dict | None = None) -> list:
        """Every waypoint of the flip as (Waypoint, G-code line), checked against the travel, the magnet floor and safe()
        BEFORE anything moves. Raises Refused naming the first bad waypoint."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("flip_path", HERE.parent / "hardware" / "gantry" / "flip_path.py")
        fp = importlib.util.module_from_spec(spec)
        sys.modules["flip_path"] = fp
        spec.loader.exec_module(fp)
        cfg = config()
        params = fp.Params(**{**(cfg.get("flip") or {}), **(overrides or {})})
        params.travel = tuple(self.travel)
        ox, oy = self.offset()
        plan = []
        for w in fp.waypoints(params):
            x, y, z = w.x - ox, w.y - oy, w.z
            for axis, v, hi in (("X", x, self.travel[0]), ("Y", y, self.travel[1]), ("Z", z, self.travel[2])):
                if not 0 <= v <= hi:
                    raise Refused(f"flip waypoint '{w.name}': {axis}{v:.1f} is outside 0..{hi} mm")
            if z < cfg["magnet_min_z"]:
                raise Refused(f"flip waypoint '{w.name}': Z{z:.1f} is below magnet_min_z={cfg['magnet_min_z']}")
            feed = min(w.feed, cfg["y_feed"])          # the bed carries the cards: every flip move has a Y part
            plan.append((w, safe(f"G1 X{x:.2f} Y{y:.2f} Z{z:.2f} F{feed}")))
        return plan

    def flip(self, dry_run: bool = False, overrides: dict | None = None):
        """Run the edge flip through the same gate as every other move. dry_run=True returns the checked lines and sends
        nothing. A live flip needs Z known and flip_hinged_head_built=true in gantry.json (the hinged head is a design,
        not hardware yet); the magnet is always off when it ends, however it ends."""
        plan = self.flip_plan(overrides)
        if dry_run:
            return [line for _, line in plan]
        if not config().get("flip_hinged_head_built"):
            raise Refused("the flip needs the passive pitch hinge (README, Flipping a card): build and bench-test it, "
                          "then set flip_hinged_head_built in gantry.json")
        self._need_z()
        w0, _ = plan[0]
        ox, oy = self.offset()
        try:
            self.goto(z=max(self._travel_z(w0.z), self.position()["Z"]), tool="magnet")
            self.goto(w0.x - ox, w0.y - oy, tool="magnet")
            for w, line in plan:
                self.send("G90")
                self.send(line)
                self.send("M400", timeout=120)
                if w.magnet and not self.magnet:
                    self.magnet_on(self._cap(None, "carry_max_s"))
                elif not w.magnet and self.magnet:
                    self.magnet_off()
                if w.dwell_ms:
                    time.sleep(w.dwell_ms / 1000)
        finally:
            self.magnet_off()
        return [line for _, line in plan]

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
    h.add_argument("--clear", action="store_true", help="with --z: nothing hangs below the nozzle and the bed is clear")
    zz = sub.add_parser("zero-z"); zz.add_argument("height", type=float, nargs="?", default=0.0)
    fl = sub.add_parser("flip"); fl.add_argument("--dry-run", action="store_true")
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
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(129))    # and when the terminal is closed
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
        ox, oy = p.offset()
        p.goto(a.x - ox, a.y - oy); print(p.tap(a.cx, a.cy, ccw=a.ccw))
    elif a.cmd == "info":
        print(json.dumps(p.info(), indent=2))
    elif a.cmd == "home":
        p.home(z=a.z, clear=a.clear); print(p.position())
    elif a.cmd == "zero-z":
        p.zero_z(a.height); print(p.position())
    elif a.cmd == "flip":
        print("\n".join(p.flip(dry_run=a.dry_run)))
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
