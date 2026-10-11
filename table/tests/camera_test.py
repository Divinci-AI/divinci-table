"""The head camera: the UVC capture backend, the deck-box mask, the mosaic, the refusals, and the field-of-view script.

No hardware: a fake OpenCV capture, a fake Marlin board, and synthetic chessboard frames rendered at a known field of view.
  ~/.venvs/table/bin/python table/tests/camera_test.py
"""
import importlib.util
import json
import math
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import gantry  # noqa: E402
import gantry_camera as gc  # noqa: E402
import re  # noqa: E402


class FakeMarlin:
    """Answers like Marlin 2: echoes position, says ok (the same stand-in as gantry_test.py, which runs on import)."""
    def __init__(self, machine="Ender-3 V2"):
        self.machine, self.pos, self.sent, self.queue = machine, {"X": 0.0, "Y": 0.0, "Z": 0.0}, [], []

    def write(self, data):
        line = data.decode().strip()
        self.sent.append(line)
        w = line.split()[0]
        if w == "M115":
            self.queue += [f"FIRMWARE_NAME:Marlin 2.0.9.3 PROTOCOL_VERSION:1.0 MACHINE_TYPE:{self.machine} EXTRUDER_COUNT:1"]
        elif w == "M114":
            p = self.pos
            self.queue += [f"X:{p['X']:.2f} Y:{p['Y']:.2f} Z:{p['Z']:.2f} E:0.00 Count X:0 Y:0 Z:0"]
        elif w == "G28":
            self.queue += ["echo:busy: processing"]
            for a_ in "XYZ":
                if a_ in line or line == "G28":
                    self.pos[a_] = 0.0
        elif w in ("G0", "G1", "G2", "G3"):
            for a_, v in re.findall(r"([XYZ])(-?[\d.]+)", line):
                self.pos[a_] = float(v)
        self.queue.append("ok")

    def readline(self):
        return (self.queue.pop(0) + "\n").encode() if self.queue else b""

spec = importlib.util.spec_from_file_location("fov_test", HERE.parent.parent / "hardware" / "gantry" / "fov_test.py")
fov = importlib.util.module_from_spec(spec); spec.loader.exec_module(fov)

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


def refused(fn):
    try:
        fn()
    except (gantry.Refused, gc.CameraError):
        return True
    return False


# ── a fake OpenCV capture: frame k is a flat picture of grey level k, so we can tell which one we got ─────────
class FakeCap:
    instances = []

    def __init__(self, index, backend=0, opened=True):
        self.reads, self.set_calls, self.released, self.opened = 0, [], False, opened
        FakeCap.instances.append(self)

    def isOpened(self):
        return self.opened

    def set(self, prop, val):
        self.set_calls.append((prop, val))
        return True

    def read(self):
        self.reads += 1
        return True, np.full((72, 128, 3), self.reads, np.uint8)

    def release(self):
        self.released = True


class FakeCV2:
    CAP_PROP_FOURCC, CAP_PROP_FRAME_WIDTH, CAP_PROP_FRAME_HEIGHT, CAP_PROP_AUTO_EXPOSURE, CAP_PROP_AUTO_WB = 6, 3, 4, 21, 44
    IMWRITE_JPEG_QUALITY, CAP_ANY, CAP_AVFOUNDATION = 1, 0, 1200
    VideoCapture = FakeCap
    written = []

    @staticmethod
    def VideoWriter_fourcc(*c):
        return "".join(c)

    @staticmethod
    def imwrite(path, img, params=None):
        FakeCV2.written.append((path, img.copy()))
        return True


tmp = Path(tempfile.mkdtemp())
cam = gc.camera_config({"camera": {"kind": "uvc", "width": 128, "height": 72, "flush": 4, "warmup_s": 0}})
path, info = gc.capture_uvc(cam, tmp / "a.jpg", cv2=FakeCV2, sleep=lambda s: None)
cap = FakeCap.instances[-1]
check("capture: the stream is opened, read flush+1 times, and released", cap.reads == 5 and cap.released)
check("capture: the frame kept is the LAST one read (the queue of old frames is thrown away)", FakeCV2.written[-1][1][0, 0, 0] == 5)
check("capture: MJPG and the asked size are requested", ("MJPG" in [v for _, v in cap.set_calls]) and (3, 128) in cap.set_calls)
check("capture: a size mismatch is reported, not silent", "warning" not in info)
FakeCap.__init__.__defaults__ = (0, 0, False)
check("capture: a camera that will not open is a clear refusal", refused(lambda: gc.capture_uvc(cam, tmp / "b.jpg", cv2=FakeCV2, sleep=lambda s: None)))
FakeCap.__init__.__defaults__ = (0, 0, True)
check("capture: an unknown camera.kind is refused", refused(lambda: gc.camera_config({"camera": {"kind": "gopro"}})))
check("config: the default is the Canon, so the old rig is unchanged", gc.camera_config({})["kind"] == "canon")

# ── the mask ──────────────────────────────────────────────────────────────────────────────────────────────────
img = np.full((100, 200, 3), 255, np.uint8)
log = gc.mask_regions(img, head=(100, 100), footprint=(200, 100), regions_mm=[(120, 110, 140, 120)], margin_mm=0)
# 1 px per mm; the lens axis is the picture's centre (100, 50); +Y is up: bed (120..140, 110..120) -> px x 120..140, y 30..40
check("mask: the deck box's pixels are black", img[35, 130].sum() == 0 and log and log[0]["pixels"][:2] == [120, 30])
check("mask: nothing outside it is touched", img[60, 130].sum() == 765 and img[35, 150].sum() == 765)
img2 = np.full((100, 200, 3), 255, np.uint8)
check("mask: a box outside the frame logs nothing and changes nothing",
      gc.mask_regions(img2, (100, 100), (200, 100), [(500, 500, 520, 520)], 0) == [] and img2.min() == 255)
img3 = np.full((100, 200, 3), 255, np.uint8)
gc.mask_regions(img3, (100, 100), (200, 100), [(120, 110, 140, 120)], 0, y_flip=True)
check("mask: image_y_flip puts the box on the other side of the axis", img3[65, 130].sum() == 0 and img3[35, 130].sum() == 765)
img4 = np.full((100, 200, 3), 255, np.uint8)
gc.mask_regions(img4, (100, 100), (200, 100), [(120, 110, 140, 120)], margin_mm=10)
check("mask: the margin widens it", img4[25, 115].sum() == 0)

# ── the mosaic ──────────────────────────────────────────────────────────────────────────────────────────────────
red, blue = np.zeros((10, 10, 3), np.uint8), np.zeros((10, 10, 3), np.uint8)
red[...] = (0, 0, 255); blue[...] = (255, 0, 0)
board = gc.mosaic([red, blue], [(10, 10), (30, 30)], (20, 20), (0, 0, 40, 40), px_per_mm=1, cv2=cv2)
check("mosaic: a frame lands where the head was (+Y is up: bed (10,10) is the lower left)", tuple(board[30, 10]) == (0, 0, 255))
check("mosaic: the other frame at the upper right", tuple(board[10, 30]) == (255, 0, 0))

# ── footprint and refusals in gantry.py ───────────────────────────────────────────────────────────────────────
uvc_cfg = dict(gantry.RIG_DEFAULT, camera={"kind": "uvc"}, min_z=50)
check("footprint: a UVC camera with no measured field of view is refused", refused(lambda: gantry.footprint(300, uvc_cfg)))
uvc_cfg["footprint_per_mm"] = [1.2, 0.675]
fw, fh = gantry.footprint(300, uvc_cfg)
check("footprint: footprint_per_mm x lens height", (round(fw), round(fh)) == (360, 202))
check("footprint: the Canon rig is unchanged", (round(gantry.footprint(400, dict(gantry.RIG_DEFAULT, lens_at_z0_mm=0))[0]) == 496))
check("min_z: not measured by default", gantry.RIG_DEFAULT["min_z"] is None)

fake = FakeMarlin("CR-6 Max")
cfg = dict(gantry.RIG_DEFAULT)
gantry.config = lambda: cfg
p = gantry.Printer(ser=fake, log=lambda *_: None)
p.zero_z(0)
check("a camera Z move with min_z unmeasured is refused", refused(lambda: p.goto(10, 10, 5)))
cfg.update(min_z=50, camera={"kind": "uvc"}, footprint_per_mm=[1.2, 0.675], deck_box_mm=None)
sent_before = len(fake.sent)
check("scan --post without deck_box_mm is refused", refused(lambda: gantry.scan(p, (0, 0, 400, 400), z=300, post=True)))
check("...before anything moved", not any(l.startswith(("G0", "G1")) for l in fake.sent[sent_before:]))

# a UVC scan end to end with the fake camera: masked frames, a log line each, a mosaic written
gantry_camera_capture = gc.capture_uvc
frames_taken = []


def fake_capture(cam, path, cv2=None, sleep=None, process=None):
    frame = np.full((72, 128, 3), 200, np.uint8)
    info = {"width": 128, "height": 72, "locked": False}
    frame = process(frame, info) if process else frame
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2_ = __import__("cv2"); cv2_.imwrite(str(path), frame)
    frames_taken.append((path, frame.copy()))
    return path, info


gc.capture_uvc = fake_capture
gantry.CACHE = tmp / "cache"
cfg.update(deck_box_mm=[-100, -100, 500, 500], min_z=50)
out = gantry.scan(p, (0, 0, 300, 200), z=300, settle=0, post=False)
check("scan (UVC): a mosaic is written", out is not None and Path(out).exists())
check("scan (UVC): every frame was masked before it was written", all(f.max() == 0 for _, f in frames_taken) and frames_taken)
lines = [json.loads(l) for l in (tmp / "cache" / "camera-log.jsonl").read_text().splitlines()]
check("scan (UVC): one log line per frame, saying what was masked", len(lines) == len(frames_taken) and all(l["masked"] for l in lines))
gc.capture_uvc = gantry_camera_capture

# ── the field-of-view script: a chessboard rendered at a known field of view must be read back ─────────────────
def render(height_mm, hfov_deg, cols=8, rows=5, sq=20.0, size=(1920, 1080)):
    W, H = size
    ground_w = 2 * height_mm * math.tan(math.radians(hfov_deg) / 2)
    ppmm = W / ground_w
    img = np.full((H, W, 3), 255, np.uint8)
    x0 = W / 2 - (cols + 1) * sq * ppmm / 2
    y0 = H / 2 - (rows + 1) * sq * ppmm / 2
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 0:
                a, b = int(x0 + c * sq * ppmm), int(y0 + r * sq * ppmm)
                cv2.rectangle(img, (a, b), (int(a + sq * ppmm), int(b + sq * ppmm)), (0, 0, 0), -1)
    return img


for true_fov in (50.0, 100.0):
    rows_ = [r for h in (200, 250, 300, 400) if (r := fov.analyze_frame(render(h, true_fov), h, 8, 5, 20.0))]
    check(f"fov: the board is found at every height ({true_fov:.0f} deg)", len(rows_) == 4)
    kw, kh = fov.fit_footprint(rows_)
    got = math.degrees(2 * math.atan(kw / 2))
    check(f"fov: a {true_fov:.0f} deg lens is read back as {got:.1f} deg (within 1 deg)", abs(got - true_fov) < 1.0)
    check(f"fov: the vertical footprint keeps the 16:9 ratio ({true_fov:.0f} deg)", abs(kh / kw - 9 / 16) < 0.01)
check("fov: a frame with no board returns None", fov.analyze_frame(np.full((100, 100, 3), 255, np.uint8), 200, 8, 5, 20.0) is None)
r = fov.analyze_frame(render(200, 100.0), 200, 8, 5, 20.0)
check("fov: pixels per card are reported", r and 63 * r["px_per_mm"] == r["card_px"][0])

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
