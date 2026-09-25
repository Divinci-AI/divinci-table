"""Can we read a whole Commander board from ONE overhead camera frame?

Builds a synthetic table from real card images at realistic scale — the AI's hand in the band
nearest the camera, three opponents' battlefields, some cards tapped (turned 90°), all with a few
degrees of random tilt — and scores three readers against the known layout:

  A  gemma-whole   Gemma 4 vision looks at the entire frame and lists card names
  B  ocr-whole     Apple text recognition over the entire frame
  C  crop          OpenCV finds each card, straightens it, text recognition reads each card alone;
                   position gives the zone, orientation gives tapped/untapped

at 4K (3840x2160) and 1080p (1920x1080). Card names are validated against Scryfall's catalog
(a reader can't score with a card that doesn't exist).

  ~/.venvs/table/bin/python table/tests/board_eval.py
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
os.environ.pop("TYPESAFE_API_KEY", None)
from match import identify_any, norm  # noqa: E402
from ocr_mac import read_lines  # noqa: E402
import voice  # noqa: E402

FIX = HERE / "fixtures"
CATALOG = json.loads((HERE.parent / ".cache" / "card-names.json").read_text())["data"]
EXACT = {norm(n): n for n in CATALOG}

HAND = ["Sol Ring", "Counterspell", "Rhystic Study", "Swords to Plowshares", "Mulldrifter", "Cyclonic Rift"]
BATTLEFIELDS = {
    "left":  [("Sheoldred, the Apocalypse", False), ("Swamp", True), ("Swamp", True), ("Dark Ritual", False)],
    "top":   [("Ghalta, Primal Hunger", True), ("Llanowar Elves", True), ("Craw Wurm", False),
              ("Forest", True), ("Thragtusk", False)],
    "right": [("Shivan Dragon", False), ("Hellrider", True), ("Mountain", True), ("Smothering Tithe", False)],
}


def slug(n): return "".join(c for c in n if c.isalnum())


def build_board(seed=7):
    """4K frame + ground truth [(name, zone, tapped)]."""
    rng = random.Random(seed)
    W, H = 3840, 2160
    base = np.full((H, W, 3), (106, 125, 95), np.uint8)          # grey-green playmat
    base = (base + np.random.default_rng(seed).normal(0, 6, base.shape)).clip(0, 255).astype(np.uint8)
    img = Image.fromarray(base)
    truth = []

    def place(name, cx, cy, width, tapped, zone):
        card = Image.open(FIX / f"{slug(name)}.jpg").convert("RGB")
        h = int(width * card.height / card.width)
        card = card.resize((width, h), Image.LANCZOS)
        angle = (90 if tapped else 0) + rng.uniform(-4, 4)
        rot = card.convert("RGBA").rotate(angle, expand=True, resample=Image.BICUBIC)
        img.paste(rot, (int(cx - rot.width / 2), int(cy - rot.height / 2)), rot)
        truth.append((name, zone, tapped))

    # the AI's hand: nearest the camera, so larger
    for i, n in enumerate(HAND):
        place(n, 520 + i * 560, 1840, 330, False, "hand")
    # opponents' battlefields (farther away, smaller)
    for i, (n, t) in enumerate(BATTLEFIELDS["left"]):
        place(n, 330 + (i % 2) * 420, 330 + (i // 2) * 470, 240, t, "left")
    for i, (n, t) in enumerate(BATTLEFIELDS["top"]):
        place(n, 1330 + i * 330, 330, 240, t, "top")
    for i, (n, t) in enumerate(BATTLEFIELDS["right"]):
        place(n, 3080 + (i % 2) * 420, 330 + (i // 2) * 470, 240, t, "right")
    return img, truth


def to_jpeg(img: Image.Image, quality=90) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=quality)
    return b.getvalue()


def zone_of(cx, cy, W, H):
    if cy > 0.62 * H:
        return "hand"
    return "left" if cx < W / 3 else "right" if cx > 2 * W / 3 else "top"


# ── readers ─────────────────────────────────────────────────────────────────────────────────
def reader_gemma_whole(img):
    d = voice._ollama({"model": voice.OLLAMA_MODEL, "stream": False, "think": False,
                       "options": {"temperature": 0, "num_predict": 300},
                       "messages": [{"role": "user", "images": [base64.b64encode(to_jpeg(img)).decode()],
                                     "content": "This is an overhead photo of a Magic: The Gathering game. "
                                                "List the exact name of every card you can see, one per line. "
                                                "No other text."}]}, timeout=120)
    out = []
    for line in (d.get("message") or {}).get("content", "").splitlines():
        n = EXACT.get(norm(line.strip("-*• 0123456789.")))
        if n:
            out.append({"name": n})
    return out


def reader_ocr_whole(img):
    lines = [l.text for l in read_lines(to_jpeg(img))]
    return [{"name": EXACT[norm(l)]} for l in lines if norm(l) in EXACT]


def find_cards(img):
    """Card quads via contours: a card is a bright-ish rectangle with a ~0.72 aspect ratio."""
    a = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    a = cv2.GaussianBlur(a, (5, 5), 0)
    edges = cv2.Canny(a, 40, 120)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    W, H = img.size
    found = []
    for c in cnts:
        rect = cv2.minAreaRect(c)
        (cx, cy), (w, h), ang = rect
        if w * h < (W * H) * 0.002:
            continue
        ratio = min(w, h) / max(w, h)
        if not 0.62 < ratio < 0.8:
            continue
        found.append(rect)
    return found


def warp(img_np, rect):
    (cx, cy), (w, h), ang = rect
    box = cv2.boxPoints(rect).astype(np.float32)
    # order: portrait output, long side vertical
    s = box.sum(1); d = np.diff(box, axis=1).ravel()
    tl, br = box[np.argmin(s)], box[np.argmax(s)]
    tr, bl = box[np.argmin(d)], box[np.argmax(d)]
    long_w = max(w, h); short_w = min(w, h)
    wide = np.linalg.norm(tr - tl) > np.linalg.norm(bl - tl)       # card lying sideways → tapped
    if wide:
        src = np.float32([tr, br, bl, tl])                            # rotate so long side is vertical
    else:
        src = np.float32([tl, tr, br, bl])
    dst = np.float32([[0, 0], [short_w, 0], [short_w, long_w], [0, long_w]])
    M = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(img_np, M, (int(short_w), int(long_w)))
    return out, wide


def reader_crop(img):
    np_img = np.array(img)
    W, H = img.size
    out = []
    for rect in find_cards(img):
        card, tapped = warp(np_img, rect)
        crop = Image.fromarray(card)
        if crop.width < 400:                                          # upscale small crops for OCR
            f = 400 / crop.width
            crop = crop.resize((int(crop.width * f), int(crop.height * f)), Image.LANCZOS)
        best = None
        for c in (crop, crop.rotate(180)):                           # a tapped card may lie either way
            name, s1, _ = identify_any([l.text for l in read_lines(to_jpeg(c))], CATALOG)
            if name and (best is None or s1 > best[1]):
                best = (name, s1)
        (cx, cy) = rect[0]
        out.append({"name": best[0] if best else None, "zone": zone_of(cx, cy, W, H), "tapped": tapped})
    return out


# ── scoring ─────────────────────────────────────────────────────────────────────────────────
def score(found, truth):
    want = [t[0] for t in truth]
    got = [f["name"] for f in found if f.get("name")]
    pool = list(want)
    hit = 0
    for g in got:
        if g in pool:
            pool.remove(g)
            hit += 1
    wrong = len(got) - hit
    zone_ok = tap_ok = with_zone = 0
    if found and "zone" in found[0]:
        tmap = {}
        for n, z, t in truth:
            tmap.setdefault(n, []).append((z, t))
        for f in found:
            if f.get("name") in tmap and tmap[f["name"]]:
                z, t = tmap[f["name"]].pop(0)
                with_zone += 1
                zone_ok += f["zone"] == z
                tap_ok += f["tapped"] == t
    return {"names": f"{hit}/{len(want)}", "wrong": wrong,
            "zones": f"{zone_ok}/{with_zone}" if with_zone else "-", "tapped": f"{tap_ok}/{with_zone}" if with_zone else "-"}


def main():
    board, truth = build_board()
    board.save(FIX / "board_4k.jpg", quality=92)
    frames = {"4K": board, "1080p": board.resize((1920, 1080), Image.LANCZOS)}
    frames["1080p"].save(FIX / "board_1080p.jpg", quality=92)
    print(f"board: {len(truth)} cards — hand {sum(1 for t in truth if t[1]=='hand')}, "
          f"battlefield {sum(1 for t in truth if t[1]!='hand')} ({sum(1 for t in truth if t[2])} tapped)\n")
    print(f"{'reader':14} {'frame':6} {'names right':>12} {'wrong names':>12} {'zones':>7} {'tapped':>7} {'time':>8}")
    for res, frame in frames.items():
        for label, fn in (("A gemma-whole", reader_gemma_whole), ("B ocr-whole", reader_ocr_whole), ("C crop", reader_crop)):
            t0 = time.time()
            try:
                found = fn(frame)
                s = score(found, truth)
            except Exception as e:
                s = {"names": f"ERR {type(e).__name__}", "wrong": "-", "zones": "-", "tapped": "-"}
            print(f"{label:14} {res:6} {s['names']:>12} {str(s['wrong']):>12} {s['zones']:>7} {s['tapped']:>7} {time.time()-t0:7.1f}s")


if __name__ == "__main__":
    main()
