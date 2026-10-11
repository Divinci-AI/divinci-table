"""Cameras for the gantry: a UVC camera on the print head (the plan), and the Canon over gphoto2 (the old beam rig).

Pure helpers, no printer needed. OpenCV is imported only when a frame is actually taken, and every function that
touches it takes the module as an argument so tests can pass a fake.

The settings live in gantry.json under "camera":

    {"kind": "uvc", "index": 0, "width": 1920, "height": 1080, "fourcc": "MJPG",
     "flush": 8, "calibration": "table/.cache/gantry/camera.npz", "image_y_flip": false}

`kind` is "canon" (the default: nothing changes for the old rig) or "uvc". Other keys are the UVC defaults below.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

UVC_DEFAULTS = {
    "index": 0,
    "width": 1920, "height": 1080, "fourcc": "MJPG",
    "flush": 8,               # frames thrown away before the one we keep: the driver queues old ones from mid-move
    "warmup_s": 0.5,          # auto exposure and white balance settle after the stream opens
    "jpeg_quality": 95,
    "calibration": None,      # path to the .npz written by fov_test.py calibrate (camera matrix + distortion)
    "image_y_flip": False,    # False: the top of the image is +Y on the bed. Set it from the field-of-view test.
    "mask_margin_mm": 10.0,
}


class CameraError(RuntimeError):
    pass


def camera_config(cfg: dict) -> dict:
    """The camera section of the rig config with the UVC defaults filled in (kind defaults to canon)."""
    cam = dict(cfg.get("camera") or {})
    kind = cam.get("kind", "canon")
    if kind not in ("canon", "uvc"):
        raise CameraError(f"camera.kind must be 'canon' or 'uvc', not {kind!r}")
    return {"kind": kind, **({**UVC_DEFAULTS, **cam} if kind == "uvc" else cam)}


def uvc_devices() -> list[str]:
    """Names of the cameras the OS knows, without opening any of them (opening lights the camera and asks permission)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["system_profiler", "SPCameraDataType"], capture_output=True, text=True, timeout=20).stdout
            return [l.strip().rstrip(":") for l in out.splitlines() if l.startswith("    ") and not l.startswith("     ") and l.strip().endswith(":")]
        import glob
        return sorted(glob.glob("/dev/video*"))
    except (OSError, subprocess.SubprocessError):
        return []


def _cv2(cv2=None):
    if cv2 is not None:
        return cv2
    try:
        import cv2 as real
    except ImportError as e:
        raise CameraError("OpenCV is not installed: run this with ~/.venvs/table/bin/python") from e
    return real


def capture_uvc(cam: dict, path: Path, cv2=None, sleep=time.sleep, process=None) -> tuple[Path, dict]:
    """Take one fresh frame from the UVC camera and write it to `path` (JPEG). Returns (path, info).

    A UVC driver keeps a queue of frames, so a plain read() right after a move returns a picture from before the move.
    We open the stream, wait for exposure to settle, throw away `flush` frames and keep the next one.
    Exposure and white balance are locked on the first kept frame where OpenCV allows it (it does not on every macOS
    camera: `info["locked"]` says which, and a camera that will not lock should be locked with a UVC tool such as uvc-util).
    """
    cv2 = _cv2(cv2)
    backend = getattr(cv2, "CAP_AVFOUNDATION", 0) if sys.platform == "darwin" else getattr(cv2, "CAP_ANY", 0)
    cap = cv2.VideoCapture(int(cam["index"]), backend)
    if not cap.isOpened():
        raise CameraError(f"UVC camera {cam['index']} did not open: is it plugged in, and does this terminal have camera permission?")
    try:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*cam["fourcc"]))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam["width"])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam["height"])
        sleep(cam["warmup_s"])
        locked = _try_lock(cap, cv2)
        frame = None
        for _ in range(int(cam["flush"]) + 1):
            ok, f = cap.read()
            if ok and f is not None:
                frame = f
        if frame is None:
            raise CameraError("the UVC camera opened but returned no frame")
    finally:
        cap.release()
    h, w = frame.shape[:2]
    info = {"width": w, "height": h, "locked": locked, "asked": [cam["width"], cam["height"]]}
    if (w, h) != (cam["width"], cam["height"]):
        info["warning"] = f"asked for {cam['width']}x{cam['height']}, got {w}x{h}: the field of view may differ"
    if cam.get("calibration"):
        frame = undistort(frame, cam["calibration"], cv2)
        info["undistorted"] = True
    if process is not None:                    # e.g. the deck-box mask: applied in memory, so no unmasked frame is ever written
        frame = process(frame, info)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, int(cam["jpeg_quality"])])
    return path, info


def _try_lock(cap, cv2) -> bool:
    """Best effort: manual exposure and white balance. OpenCV reports success even when the driver ignores it, so this
    only says whether the properties accepted the call."""
    try:
        a = cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)          # 1 = manual on most UVC backends
        b = cap.set(cv2.CAP_PROP_AUTO_WB, 0)
        return bool(a and b)
    except Exception:
        return False


def undistort(img, calibration, cv2=None):
    """Remove lens distortion with the camera matrix and coefficients from fov_test.py calibrate."""
    cv2 = _cv2(cv2)
    import numpy as np
    d = np.load(str(calibration))
    return cv2.undistort(img, d["K"], d["dist"])


# ── what must not be read: the deck box ───────────────────────────────────────────────────────────
def bed_to_pixels(bx, by, head, footprint, size, y_flip=False):
    """Pixel (x, y) of the bed point (bx, by) when the lens axis is over `head` = (x, y) and the camera sees
    `footprint` = (width, height) mm of bed in an image of `size` = (w, h) pixels. A flat-bed pinhole model: it holds
    on an undistorted frame."""
    fw, fh = footprint
    w, h = size
    px = (bx - head[0]) / fw * w + w / 2
    py = (by - head[1]) / fh * h
    py = h / 2 + (py if y_flip else -py)
    return px, py


def mask_regions(img, head, footprint, regions_mm, margin_mm=10.0, y_flip=False):
    """Paint the given bed rectangles black, in place, and return a log entry for each one that touches the frame.

    The camera on the head looks straight into the deck box's open exit slot, and the software must never read the AI's
    face-up top card before a draw (README, Game rules). Every frame is masked before anything else sees it."""
    h, w = img.shape[:2]
    out = []
    for (x0, y0, x1, y1) in regions_mm:
        ax, ay = bed_to_pixels(x0 - margin_mm, y0 - margin_mm, head, footprint, (w, h), y_flip)
        bx, by = bed_to_pixels(x1 + margin_mm, y1 + margin_mm, head, footprint, (w, h), y_flip)
        xa, xb = sorted((ax, bx)); ya, yb = sorted((ay, by))
        xa, xb, ya, yb = max(0, int(xa)), min(w, int(xb) + 1), max(0, int(ya)), min(h, int(yb) + 1)
        if xb > xa and yb > ya:
            img[ya:yb, xa:xb] = 0
            out.append({"bed_mm": [x0, y0, x1, y1], "pixels": [xa, ya, xb, yb]})
    return out


def log_frame(log_path: Path, entry: dict) -> None:
    """One JSON line per frame the software took, so a person can check what the camera was shown."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}) + "\n")


# ── the mosaic: frames placed by where the head was, not matched by their content ─────────────────
def mosaic(frames, centres, footprint, area, px_per_mm=2.0, cv2=None, y_flip=False):
    """Paste each frame onto a canvas of the bed area at its known place. The stitcher in OpenCV matches features, and a
    plain shelf-liner bed gives it none; the gantry positions and the calibrated camera offset are exact instead.
    `frames` are BGR images, `centres` the bed point under each lens axis, `footprint` the mm each frame covers."""
    cv2 = _cv2(cv2)
    import numpy as np
    x0, y0, x1, y1 = area
    W, H = int(round((x1 - x0) * px_per_mm)), int(round((y1 - y0) * px_per_mm))
    canvas = np.zeros((H, W, 3), dtype=np.uint8)
    fw, fh = footprint
    tw, th = max(1, int(round(fw * px_per_mm))), max(1, int(round(fh * px_per_mm)))
    for img, (cx, cy) in zip(frames, centres):
        tile = cv2.resize(img, (tw, th))
        ox = int(round((cx - fw / 2 - x0) * px_per_mm))
        # canvas row 0 is the top of the picture: +Y is up unless the camera is mounted the other way round
        oy = int(round(((cy - fh / 2 - y0) if y_flip else (y1 - cy - fh / 2)) * px_per_mm))
        sx0, sy0 = max(0, ox), max(0, oy)
        sx1, sy1 = min(W, ox + tw), min(H, oy + th)
        if sx1 > sx0 and sy1 > sy0:
            canvas[sy0:sy1, sx0:sx1] = tile[sy0 - oy:sy1 - oy, sx0 - ox:sx1 - ox]
    return canvas
