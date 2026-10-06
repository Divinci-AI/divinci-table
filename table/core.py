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


def save_photo(data: bytes, folder: Path, cap: int = 300) -> tuple[int, dict]:
    """A JPEG or PNG from a phone, under 8 MB, at most `cap` per table."""
    if not 0 < len(data) <= 8_000_000:
        return 413, {"error": "a photo must be under 8 MB"}
    if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
        return 400, {"error": "send a JPEG or PNG"}
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    if sum(1 for _ in folder.iterdir()) >= cap:
        return 429, {"error": f"this table has reached its photo limit ({cap})"}
    ext = ".jpg" if data[:3] == b"\xff\xd8\xff" else ".png"
    name = f"{time.strftime('%H%M%S')}-{secrets.token_hex(4)}{ext}"
    (folder / name).write_bytes(data)
    os.chmod(folder / name, 0o600)
    return 200, {"ok": True, "photo": "/photos/" + name}


SURVEY_SCALE = ["I felt content", "I felt skilful", "I felt bad", "I found it tiresome", "I felt satisfied",
                "I felt regret", "I felt energised", "I felt proud"]          # GEQ post-game subset, 0 not at all … 4 extremely
SURVEY_TEXT = ["best", "worst", "ai", "again"]                              # free text, short


def save_survey(folder: Path, by: str, body: dict) -> tuple[int, dict]:
    """A person's optional post-game survey: the fixed questions only, kept in the room's research folder
    (never in the public log). One file per person; answering again replaces it."""
    scale = body.get("scale") or {}
    out = {"version": "0.1", "by": by, "ts": round(time.time(), 2), "scale": {}, "text": {}}
    for q in SURVEY_SCALE:
        v = scale.get(q)
        if v is not None:
            if not isinstance(v, int) or not 0 <= v <= 4:
                return 400, {"error": "ratings are whole numbers from 0 to 4"}
            out["scale"][q] = v
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
