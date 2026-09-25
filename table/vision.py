"""Finding the cards in a camera frame, and reading each one on its own.

Full-frame OCR reads a card that fills the view; it can't read one held across the table (28 %
of the frame: title text ~6 px) and it runs two cards' text together. So: find each card as a
bright rectangle with a card's aspect ratio (OpenCV contours), straighten it, scale it up to a
readable size, and OCR that crop — right way up and, if nothing reads, upside down. Measured in
tests/vision_eval.py.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image

CARD_RATIO = (0.62, 0.8)          # short/long side; real cards are 63/88 = 0.716


def find_cards(img: Image.Image, min_area=0.0015):
    import cv2
    a = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    a = cv2.GaussianBlur(a, (5, 5), 0)
    edges = cv2.Canny(a, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    W, H = img.size
    out = []
    for c in cnts:
        rect = cv2.minAreaRect(c)
        (cx, cy), (w, h), ang = rect
        if w * h < W * H * min_area or w * h > W * H * 0.9:
            continue
        if not CARD_RATIO[0] < min(w, h) / max(w, h) < CARD_RATIO[1]:
            continue
        out.append(rect)
    out.sort(key=lambda r: r[0][0])                        # left to right
    return out


def warp(img: Image.Image, rect, width=640):
    """The card straightened and upright-ish (long side vertical), scaled to `width` px."""
    import cv2
    arr = np.array(img.convert("RGB"))
    (cx, cy), (w, h), ang = rect
    box = cv2.boxPoints(rect).astype(np.float32)
    s, d = box.sum(1), np.diff(box, axis=1).ravel()
    tl, br, tr, bl = box[np.argmin(s)], box[np.argmax(s)], box[np.argmin(d)], box[np.argmax(d)]
    long_w, short_w = max(w, h), min(w, h)
    wide = np.linalg.norm(tr - tl) > np.linalg.norm(bl - tl)
    src = np.float32([tr, br, bl, tl]) if wide else np.float32([tl, tr, br, bl])
    scale = width / max(short_w, 1)
    dst = np.float32([[0, 0], [short_w * scale, 0], [short_w * scale, long_w * scale], [0, long_w * scale]])
    M = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(arr, M, (int(short_w * scale), int(long_w * scale)), flags=cv2.INTER_CUBIC)
    return Image.fromarray(out), wide


KEYWORDS = {"flying", "vigilance", "trample", "haste", "reach", "deathtouch", "lifelink", "first strike",
            "double strike", "defender", "menace", "hexproof", "indestructible", "flash", "ward", "prowess",
            "enchant creature", "enchant land", "enchant forest", "equip", "scry", "fight", "protection"}


def title_lines(lines: list[str]) -> list[str]:
    """Drop what can't be a card's title: keyword lines (fuzzily — "Fiying" is Flying), type lines,
    and rules sentences (≥ 6 words, or ending in a period)."""
    from difflib import get_close_matches
    out = []
    for l in lines:
        t = l.strip().lower()
        if not t or get_close_matches(t, list(KEYWORDS), n=1, cutoff=0.75):
            continue
        if " - " in t or " — " in t or t.startswith(("creature", "instant", "sorcery", "enchantment", "artifact",
                                                        "legendary", "land", "basic land", "planeswalker")):
            continue
        if len(t.split()) >= 6 or t.endswith("."):
            continue
        out.append(l)
    return out


def jpeg(img: Image.Image, q=92) -> bytes:
    b = io.BytesIO()
    img.convert("RGB").save(b, "JPEG", quality=q)
    return b.getvalue()


def read_cards(image_bytes: bytes, identify, read_lines) -> list[dict]:
    """[{name, tapped, box}] for every card found as a rectangle and read on its own crop.
    `identify(lines) -> name|None` and `read_lines(bytes) -> [Line]` are injected (catalog matcher,
    OCR backend)."""
    img = Image.open(io.BytesIO(image_bytes))
    found = []
    for rect in find_cards(img)[:8]:
        crop, wide = warp(img, rect)
        name = None
        for im in (crop, crop.rotate(180)):
            # only the title band (top ~15 %): the rest of a card is type line, rules text and
            # keywords, and "Flying" misread as "Fling" is a real card (measured: 3 wrong reads)
            for band in (0.16, 0.24):             # a tilted crop carries some table: a taller band
                top = im.crop((0, 0, im.width, int(im.height * band)))
                name = identify(title_lines([l.text for l in read_lines(jpeg(top))]))
                if name:
                    break
            if name:
                break
        if name:
            (cx, cy), (w, h), _ = rect
            found.append({"name": name, "tapped": bool(wide), "box": (float(cx / img.width), float(cy / img.height))})
    return found
