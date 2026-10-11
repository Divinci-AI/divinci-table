#!/usr/bin/env python3
"""The head camera's field-of-view test (manual Step 2), so it is one command per stage instead of a page of arithmetic.

  ~/.venvs/table/bin/python hardware/gantry/fov_test.py capture  --out DIR [--heights 100 150 200 250 300 400] [--index 0]
  ~/.venvs/table/bin/python hardware/gantry/fov_test.py analyze  DIR --cols 8 --rows 5 --square-mm 20 [--write]
  ~/.venvs/table/bin/python hardware/gantry/fov_test.py calibrate DIR --cols 8 --rows 5 --square-mm 20 [--write]

Setup: print a chessboard (OpenCV counts INNER corners: a board of 9 x 6 squares has 8 x 5), glue it flat to something
rigid, and measure one square with calipers. Fix the camera facing straight down at each measured height, measured from
the LENS to the board, not from the nozzle. Use the exact mode the gantry will use (1920 x 1080 MJPEG: that is what
`capture` takes). Focus by hand at the height you will really shoot from (about 100-150 mm) and lock the lens.

`capture` asks you to set each height, then takes one raw frame (no calibration, no mask) as h<height>.jpg.
`analyze` finds the board in each frame and reports, per height: pixels per mm at the board, the ground the whole frame
covers, the field of view, and how many pixels a 63 x 88 mm card gets. It fits the ground width and height against the
lens height through zero, which gives `footprint_per_mm`, the one number `gantry.py` needs. With --write it patches
table/.cache/gantry.json (and nothing else).
`calibrate` fits the lens distortion from the same frames (put the board at a different tilt and place in each frame;
10 or more good frames) and saves camera.npz; gantry.json's camera.calibration then undistorts every frame.

The numbers assume a flat, level board close to the lens axis. A board that fills a corner of the frame sees that part
of the lens only, so move it around between heights and compare.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLE = HERE.parent.parent / "table"
sys.path.insert(0, str(TABLE))
import gantry_camera  # noqa: E402

CARD_MM = (63.0, 88.0)
DEFAULT_HEIGHTS = [100, 150, 200, 250, 300, 400]


def find_board(img, cols, rows, cv2=None):
    """Inner-corner positions of the chessboard as an (N, 2) float array, or None."""
    cv2 = cv2 or __import__("cv2")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    ok, corners = (False, None)
    if hasattr(cv2, "findChessboardCornersSB"):
        ok, corners = cv2.findChessboardCornersSB(gray, (cols, rows), cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
    if not ok:
        ok, corners = cv2.findChessboardCorners(gray, (cols, rows))
        if ok:
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001))
    return corners.reshape(-1, 2) if ok else None


def board_stats(corners, cols, rows, square_mm):
    """Pixels per mm along the board's rows and columns, its centre in the frame, and the spread of scale across it."""
    import numpy as np
    g = corners.reshape(rows, cols, 2)
    dx = np.linalg.norm(np.diff(g, axis=1), axis=2)      # corner-to-corner along a row
    dy = np.linalg.norm(np.diff(g, axis=0), axis=2)      # and down a column
    spacing = np.concatenate([dx.ravel(), dy.ravel()])
    ppmm = float(np.mean(spacing)) / square_mm
    return {"px_per_mm": ppmm, "centre_px": [float(g[..., 0].mean()), float(g[..., 1].mean())],
            "scale_spread": float((spacing.max() - spacing.min()) / spacing.mean())}


def analyze_frame(img, height_mm, cols, rows, square_mm, cv2=None):
    """What one frame says about the lens. None if the board was not found."""
    corners = find_board(img, cols, rows, cv2)
    if corners is None:
        return None
    s = board_stats(corners, cols, rows, square_mm)
    h, w = img.shape[:2]
    gw, gh = w / s["px_per_mm"], h / s["px_per_mm"]
    return {"height_mm": height_mm, "px_per_mm": s["px_per_mm"], "ground_mm": [gw, gh],
            "hfov_deg": math.degrees(2 * math.atan(gw / 2 / height_mm)), "vfov_deg": math.degrees(2 * math.atan(gh / 2 / height_mm)),
            "card_px": [CARD_MM[0] * s["px_per_mm"], CARD_MM[1] * s["px_per_mm"]],
            "board_centre_px": s["centre_px"], "scale_spread": s["scale_spread"], "size": [w, h]}


def fit_footprint(rows_):
    """Ground width and height per mm of lens height, least squares through zero: sum(g*h) / sum(h*h)."""
    hh = sum(r["height_mm"] ** 2 for r in rows_)
    return [sum(r["ground_mm"][i] * r["height_mm"] for r in rows_) / hh for i in (0, 1)]


def height_of(path: Path):
    m = re.search(r"h(\d+(?:\.\d+)?)", path.stem)
    return float(m.group(1)) if m else None


def cmd_capture(a):
    cam = gantry_camera.camera_config({"camera": {"kind": "uvc", "index": a.index, "calibration": None}})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta = {"heights": [], "mode": [cam["width"], cam["height"], cam["fourcc"]]}
    for h in a.heights:
        input(f"Set the lens {h:g} mm above the board (measured from the lens), board in view, then press Enter... ")
        path, info = gantry_camera.capture_uvc(cam, out / f"h{h:g}.jpg")
        print(f"  {path.name}: {info['width']}x{info['height']}" + (f"  WARNING {info['warning']}" if info.get("warning") else ""))
        meta["heights"].append(h)
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"done: now run  analyze {out} --cols C --rows R --square-mm S")


def load_frames(d: Path):
    import cv2
    for p in sorted(d.glob("h*.jpg")) + sorted(d.glob("h*.png")):
        h = height_of(p)
        if h is not None:
            yield p, h, cv2.imread(str(p))


def cmd_analyze(a):
    d = Path(a.dir)
    results = []
    print(f"{'height':>7} {'px/mm':>6} {'ground (mm)':>14} {'H fov':>6} {'V fov':>6} {'card px':>11} {'spread':>6}")
    for p, h, img in load_frames(d):
        r = analyze_frame(img, h, a.cols, a.rows, a.square_mm)
        if r is None:
            print(f"{h:7g}  board not found in {p.name}: lower the height, or check --cols/--rows (inner corners) and the glare")
            continue
        results.append(r)
        print(f"{h:7g} {r['px_per_mm']:6.2f} {r['ground_mm'][0]:6.0f} x {r['ground_mm'][1]:<5.0f} {r['hfov_deg']:5.1f}° {r['vfov_deg']:5.1f}° "
              f"{r['card_px'][0]:4.0f} x {r['card_px'][1]:<4.0f} {r['scale_spread'] * 100:5.1f}%")
    if len(results) < 2:
        print("need the board found at 2 or more heights to fit the field of view")
        return 1
    kw, kh = fit_footprint(results)
    print(f"\nfootprint_per_mm = [{kw:.4f}, {kh:.4f}]   (the ground the frame covers per mm of lens height; "
          f"{math.degrees(2 * math.atan(kw / 2)):.1f}° across, {math.degrees(2 * math.atan(kh / 2)):.1f}° down)")
    spread = max(r["scale_spread"] for r in results)
    print(f"scale spread across the board: up to {spread * 100:.1f}%" + ("  (the lens bends noticeably: run `calibrate`)" if spread > 0.02 else ""))
    print("Ask `/api/board` whether the card pixel counts above are enough; and run `plan` in gantry.py to see the tile count.")
    if a.write:
        patch_config({"footprint_per_mm": [round(kw, 5), round(kh, 5)]})
    return 0


def patch_config(update: dict):
    path = TABLE / ".cache" / "gantry.json"
    cfg = json.loads(path.read_text()) if path.exists() else {}
    cfg.update(update)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2))
    print(f"wrote {sorted(update)} to {path}")


def cmd_calibrate(a):
    import cv2
    import numpy as np
    objp = np.zeros((a.rows * a.cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:a.cols, 0:a.rows].T.reshape(-1, 2) * a.square_mm
    obj, img_pts, size = [], [], None
    for p, h, img in load_frames(Path(a.dir)):
        c = find_board(img, a.cols, a.rows)
        if c is None:
            print(f"  skipped {p.name}: board not found")
            continue
        obj.append(objp); img_pts.append(c.astype(np.float32).reshape(-1, 1, 2)); size = (img.shape[1], img.shape[0])
    if len(obj) < 6:
        print(f"only {len(obj)} usable frames: take 10 or more with the board tilted and placed differently each time")
        return 1
    rms, K, dist, _, _ = cv2.calibrateCamera(obj, img_pts, size, None, None)
    out = Path(a.out) if a.out else TABLE / ".cache" / "gantry" / "camera.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, K=K, dist=dist, size=np.array(size), rms=rms)
    print(f"RMS reprojection error {rms:.2f} px over {len(obj)} frames (under 1 px is good); saved {out}")
    if a.write:
        cfg_path = TABLE / ".cache" / "gantry.json"
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        cam = {"kind": "uvc", **(cfg.get("camera") or {}), "calibration": str(out)}
        patch_config({"camera": cam})
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture"); c.add_argument("--out", required=True)
    c.add_argument("--heights", type=float, nargs="+", default=DEFAULT_HEIGHTS); c.add_argument("--index", type=int, default=0)
    for name in ("analyze", "calibrate"):
        s = sub.add_parser(name); s.add_argument("dir")
        s.add_argument("--cols", type=int, required=True, help="inner corners across")
        s.add_argument("--rows", type=int, required=True, help="inner corners down")
        s.add_argument("--square-mm", type=float, required=True)
        s.add_argument("--write", action="store_true", help="patch table/.cache/gantry.json")
        if name == "calibrate":
            s.add_argument("--out")
    a = ap.parse_args(argv)
    return {"capture": cmd_capture, "analyze": cmd_analyze, "calibrate": cmd_calibrate}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
