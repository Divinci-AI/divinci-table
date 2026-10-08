"""The game-independent table: seats and their keys, the public event log, room persistence, talk and photos.

A game server (chess_server.py today; the Magic server is still on its own copies of these, see
docs/ROADMAP-GOAL.md M4) builds on these pieces and adds only its rules. The HTTP contract they serve is
shared by every game, so the cloud room (cloud/src/index.ts), seat.js and photo.js work unchanged:

    GET  /api/seat/claims            POST /api/seat/claim     {name, key?}
    GET  /api/events?since=N         POST /api/chat           {by, text}
    POST /api/chat/photo             (raw JPEG/PNG; X-By, X-Seat-Key)      GET /photos/<file>
    GET  /api/room/status            POST /api/room/adopt     GET /api/room/snapshot   POST /api/room/restore
         (the /api/room/* four need X-Room-Token == $ROOM_TOKEN; without the env var they don't exist)
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import secrets
import threading
import time
import zlib
from pathlib import Path


def _keyhash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Seats:
    """Named seats a person claims from a device. The claim returns a secret key (kept in that browser);
    every action for the seat must carry it. Only hashes are stored."""

    def __init__(self, names: list[str]):
        self.names = list(names)
        self.keys: dict[str, list[str]] = {}
        self.devices: dict[str, list[str]] = {}
        self.lock = threading.Lock()

    def canonical(self, name: str) -> str | None:
        return next((n for n in self.names if n.lower() == str(name or "").lower()), None)

    def ok(self, name: str, key: str) -> bool:
        n = self.canonical(name)
        return bool(n and key) and any(secrets.compare_digest(h, _keyhash(key)) for h in self.keys.get(n, []))

    def claim(self, name: str, key: str, device: str, cloud: bool) -> tuple[int, dict]:
        n = self.canonical(name)
        if not n:
            return 400, {"error": "no such seat"}
        with self.lock:
            if key and self.ok(n, key):
                if device not in self.devices.setdefault(n, []):
                    self.devices[n].append(device)
                return 200, {"name": n, "key": key}
            if n in self.keys and device not in self.devices.get(n, []):
                where = "ask the table's host to release it" if cloud else "release it from the host laptop"
                return 409, {"error": f"{n}'s seat is claimed on another device — open the link from that device (👤), or {where}"}
            new = secrets.token_urlsafe(18)
            self.keys.setdefault(n, []).append(_keyhash(new))
            if device not in self.devices.setdefault(n, []):
                self.devices[n].append(device)
            return 200, {"name": n, "key": new}

    def release(self, name: str) -> bool:
        n = self.canonical(name)
        if not n:
            return False
        with self.lock:
            self.keys.pop(n, None)
            self.devices.pop(n, None)
        return True

    def public(self, device: str) -> dict:
        return {"humans": {n: n in self.keys for n in self.names}, "proxy": {},
                "this_device": [n for n, fps in self.devices.items() if device in fps]}

    def state(self) -> dict:
        return {"keys": self.keys, "devices": self.devices}

    def load(self, d: dict) -> None:
        self.keys, self.devices = dict(d.get("keys") or {}), dict(d.get("devices") or {})


class Events:
    """The public log every page polls. Ids only grow; a page that is ahead of the server (after a restart)
    is told so and starts over."""

    def __init__(self, keep: int = 2000):
        self.items: list[dict] = []
        self.next_id = 1
        self.keep = keep
        self.lock = threading.Condition()          # re-entrant; wait_since sleeps on it until emit() wakes it

    def emit(self, etype: str, **kw) -> dict:
        with self.lock:
            e = {"id": self.next_id, "type": etype, "ts": round(time.time(), 2), **kw}
            self.next_id += 1
            self.items.append(e)
            del self.items[:-self.keep]
            self.lock.notify_all()
            return e

    def since(self, n: int) -> dict:
        with self.lock:
            last = self.next_id - 1
            if n > last:
                return {"events": [], "last": last, "restarted": True}
            return {"events": [e for e in self.items if e["id"] > n][-200:], "last": last, "restarted": False}

    MAX_WAIT = 25.0

    def wait_since(self, n: int, timeout: float) -> dict:
        """Long poll: like since(), but holds the request until there is something newer than n (or the timeout, at
        most MAX_WAIT seconds). A page that is ahead of the server is answered at once so it can start over."""
        timeout = min(max(float(timeout), 0.0), self.MAX_WAIT)
        with self.lock:
            self.lock.wait_for(lambda: n > self.next_id - 1 or self.next_id - 1 > n, timeout)
            return self.since(n)

    def state(self) -> dict:
        return {"items": self.items[-500:], "next_id": self.next_id}

    def load(self, d: dict) -> None:
        self.items, self.next_id = list(d.get("items") or []), int(d.get("next_id") or 1)


class Room:
    """Cloud persistence: the room's Durable Object reads and writes the whole game through /api/room/*,
    with a per-room token only it holds. A process that already holds the game is never overwritten."""

    def __init__(self, dump, load):
        self.dump, self.load = dump, load          # callables: () -> dict, (dict) -> None
        self.adopted = False
        self.token = os.environ.get("ROOM_TOKEN", "")

    def token_ok(self, got: str) -> bool:
        return bool(self.token) and secrets.compare_digest(self.token, got or "")

    def snapshot(self) -> bytes:
        return pickle.dumps(self.dump())

    def restore(self, blob: bytes) -> tuple[int, dict]:
        if self.adopted:
            return 409, {"error": "this process already holds the room's game"}
        self.load(pickle.loads(blob))
        self.adopted = True
        return 200, {"ok": True}


def device_of(handler) -> str:
    """A device: its forwarded address (or socket address) plus its browser."""
    ip = (handler.headers.get("X-Forwarded-For") or handler.client_address[0]).split(",")[0].strip()
    return hashlib.sha256(f"{ip}|{handler.headers.get('User-Agent', '')}".encode()).hexdigest()[:24]


_JPEG_STANDALONE = {0x01, *range(0xD0, 0xD8)}                               # markers with no length field (SOI/EOI handled apart)


def _jpeg_orientation(payload: bytes) -> int:
    """The EXIF orientation (1-8) in an APP1 payload, or 1 if absent or unreadable. Reads one tag; keeps nothing else."""
    try:
        if payload[:6] != b"Exif\0\0":
            return 1
        t = payload[6:]
        end = {b"II": "little", b"MM": "big"}[t[:2]]
        off = int.from_bytes(t[4:8], end)
        n = int.from_bytes(t[off:off + 2], end)
        for i in range(min(n, 512)):
            e = t[off + 2 + 12 * i: off + 14 + 12 * i]
            if len(e) == 12 and int.from_bytes(e[:2], end) == 0x0112:
                v = int.from_bytes(e[8:10], end)
                return v if 1 <= v <= 8 else 1
    except (KeyError, ValueError, IndexError):
        pass
    return 1


def _orientation_segment(o: int) -> bytes:
    """A minimal APP1/EXIF segment holding only the orientation tag."""
    tiff = b"MM\0*\0\0\0\x08" + b"\0\x01" + b"\x01\x12\0\x03\0\0\0\x01" + o.to_bytes(2, "big") + b"\0\0" + b"\0\0\0\0"
    body = b"Exif\0\0" + tiff
    return b"\xff\xe1" + (len(body) + 2).to_bytes(2, "big") + body


def strip_jpeg(data: bytes) -> bytes | None:
    """The JPEG with all metadata removed (EXIF/GPS, XMP, IPTC, comments, thumbnails, MakerNotes, trailing data),
    keeping JFIF, the ICC profile, the Adobe colour flag, and a one-tag EXIF carrying only the orientation.
    Image segments are copied byte for byte. None if the file is not a complete, well-formed JPEG."""
    n = len(data)
    if data[:2] != b"\xff\xd8":
        return None
    out = [b"\xff\xd8"]
    orient, pos, seen_sos, scan = 1, 2, False, None   # scan: where the current entropy-coded run began
    while True:
        i = data.find(b"\xff", pos)
        if i < 0 or i + 1 >= n:
            return None
        m = data[i + 1]
        if m == 0xFF:                                   # fill byte
            pos = i + 1
            continue
        if m == 0x00 or (seen_sos and 0xD0 <= m <= 0xD7):   # stuffed byte / restart inside entropy-coded data
            if not seen_sos:
                return None
            pos = i + 2
            continue
        if not seen_sos and i != pos:
            return None                                 # junk between header segments
        if scan is not None:                            # a real marker ends the entropy-coded run: keep it verbatim
            out.append(data[scan:i])
            scan = None
        if m == 0xD9:
            if not seen_sos:
                return None
            if orient != 1:                             # after JFIF if there is one (it must come first), else first
                at = 2 if len(out) > 1 and out[1][:2] == b"\xff\xe0" else 1
                out.insert(at, _orientation_segment(orient))
            return b"".join(out) + b"\xff\xd9"
        if m == 0xD8:
            return None
        if m in _JPEG_STANDALONE:
            out.append(data[i:i + 2])
            pos = i + 2
            continue
        if i + 4 > n:
            return None
        ln = int.from_bytes(data[i + 2:i + 4], "big")
        if ln < 2 or i + 2 + ln > n:
            return None
        seg, payload = data[i:i + 2 + ln], data[i + 4:i + 2 + ln]
        if m == 0xDA:
            seen_sos = True
            scan = i + 2 + ln
        if m == 0xE1 and orient == 1:
            orient = _jpeg_orientation(payload)
        keep = (m == 0xE0 and payload[:5] == b"JFIF\0") \
            or (m == 0xE2 and payload[:12] == b"ICC_PROFILE\0") \
            or (m == 0xEE and payload[:5] == b"Adobe") \
            or not (0xE0 <= m <= 0xEF or m == 0xFE)
        if keep:
            out.append(seg)
        pos = i + 2 + ln


def strip_png(data: bytes) -> bytes | None:
    """The PNG without text, EXIF, time or any other non-rendering chunks. None if malformed."""
    keep = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"bKGD", b"sBIT",
            b"pHYs", b"hIST", b"acTL", b"fcTL", b"fdAT"}
    out, pos, n, first = [data[:8]], 8, len(data), True
    while pos + 12 <= n:
        ln = int.from_bytes(data[pos:pos + 4], "big")
        typ = data[pos + 4:pos + 8]
        end = pos + 12 + ln
        if end > n or not typ.isalpha() or (first and typ != b"IHDR"):
            return None
        if zlib.crc32(data[pos + 4:pos + 8 + ln]) != int.from_bytes(data[end - 4:end], "big"):
            return None
        first = False
        if typ in keep or typ[0:1].isupper():           # unknown critical chunks are needed to render; ancillary ones go
            out.append(data[pos:end])
        pos = end
        if typ == b"IEND":
            return b"".join(out)
    return None


def save_photo(data: bytes, folder: Path, cap: int = 300) -> tuple[int, dict]:
    """A JPEG or PNG from a phone, under 8 MB, at most `cap` per table. Stored with its metadata removed
    (location, device, time, thumbnails): only the orientation survives, so the picture stays upright."""
    if not 0 < len(data) <= 8_000_000:
        return 413, {"error": "a photo must be under 8 MB"}
    jpeg = data[:3] == b"\xff\xd8\xff"
    if not (jpeg or data[:8] == b"\x89PNG\r\n\x1a\n"):
        return 400, {"error": "send a JPEG or PNG"}
    clean = strip_jpeg(data) if jpeg else strip_png(data)
    if clean is None:
        return 400, {"error": "that isn't a readable " + ("JPEG" if jpeg else "PNG")}
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    if sum(1 for _ in folder.iterdir()) >= cap:
        return 429, {"error": f"this table has reached its photo limit ({cap})"}
    ext = ".jpg" if jpeg else ".png"
    name = f"{time.strftime('%H%M%S')}-{secrets.token_hex(4)}{ext}"
    (folder / name).write_bytes(clean)
    os.chmod(folder / name, 0o600)
    return 200, {"ok": True, "photo": "/photos/" + name}


import survey_items                                                         # the item bank: ids, wording, domains (adapted items)
SURVEY_TEXT = ["best", "worst", "ai", "again"]                              # free text, short


def save_survey(folder: Path, by: str, body: dict) -> tuple[int, dict]:
    """A person's optional post-game survey: the fixed questions only, kept in the room's research folder
    (never in the public log). One file per person; answering again replaces it."""
    scale = body.get("scale") or {}
    out = {"version": "1.0", "bank": survey_items.BANK_VERSION, "by": by, "ts": round(time.time(), 2), "scale": {},
           "text": {}}
    for k, v in scale.items() if isinstance(scale, dict) else []:
        iid = survey_items.item_id(str(k))                 # an item id, or a v0.1 wording; anything else is ignored
        if iid is None or v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 4:
            return 400, {"error": "ratings are whole numbers from 0 to 4"}
        out["scale"][iid] = v
    order = body.get("order")                              # the order this person was shown the items in
    if isinstance(order, list):
        out["order"] = [survey_items.item_id(str(x)) for x in order[:60] if survey_items.item_id(str(x))]
    for k in SURVEY_TEXT:
        if body.get(k):
            out["text"][k] = clean_text(body[k], 600)
    if not out["scale"] and not out["text"]:
        return 400, {"error": "answer at least one question"}
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    f = folder / f"survey-human-{hashlib.sha256(by.encode()).hexdigest()[:10]}.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    os.chmod(f, 0o600)
    return 200, {"ok": True}


def clean_text(s, n: int) -> str:
    return " ".join(str(s or "").split())[:n]


def jdump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode()
