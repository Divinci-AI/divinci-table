#!/usr/bin/env python3
"""Room host: run table rooms as processes on one machine you control, behind the lobby Worker.

The lobby (cloud/src/index.ts) can keep each room in a Cloudflare Container, or hand every room to this
service (wrangler.host.jsonc: ROOM_HOST_ORIGIN + the ROOM_HOST_KEY secret). The Worker stays the only public
address; this service answers only requests that carry the shared key.

    ROOM_HOST_KEY_FILE=/run/roomhost.key python3 cloud/roomhost.py --port 8790 --data /data

  GET  /healthz                         200 when up (no key, no detail)
  GET  /__host/status                   rooms and their state
  POST /__host/rooms/<id>/ensure        {"config": {...}, "token": "...", "env": {...}, "expires_at": ms} -> start (or
                                        reuse) the room, fill a fresh process from its saved game; {"running": true,
                                        "fresh": bool}. Past expires_at the room is saved and stopped, in use or not.
  POST /__host/rooms/<id>/sleep         save, then stop the process
  POST /__host/rooms/<id>/release       {"seat": "Sam"}: free a seat whose device is lost
  GET  /__host/assets/<path>            shared files (avatars/..., dnd/...) from --assets, if any
  ANY  /__r/<id>/<path>                 <path> on that room's table server (409 when it is not running: the
                                        Worker calls ensure and tries once more)

The two namespaces never meet: a room's own paths always travel behind /__r/<id>, so nothing a visitor types can
become a /__host/ call, and /__host/ answers only a caller that adds no room prefix (the Worker's own code).

Each room gets its own copy of the table code under <data>/rooms/<id>/app (5 MB), so one room's files (brain
token, photos, research log, restore file) are never another's, as in a container of its own. The saved game
is <data>/rooms/<id>/snapshot.pkl: written a few seconds after a change, before sleep and on SIGTERM, and
loaded only into that same room. It is a pickle, so it must never come from anywhere else.

An agent table (config.agents, docs/agents.md) is held to its rules here, where every request passes: from outside,
the only requests that change anything are a seat's own moves (/api/brain/…) and a few things a seat says about itself.
Taking a seat and starting the game are done by the lobby (X-Room-Internal carries the room's own token). When
ROOM_HOST_LOBBY is set, the room host tells that lobby when an agent game ends or has been abandoned, so the table's
place is free again (POST <lobby>/lobby/host/report, with the shared key).

Every proxied request reaches the table server with X-Forwarded-For set (the table treats a request without
it, from 127.0.0.1, as the host's own laptop) and with the room's private headers removed. Its value is the
visitor's address as the Worker saw it (X-Client-Addr), never what a proxy on the way wrote.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--host", default="0.0.0.0")
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8790")))
ap.add_argument("--data", default=os.environ.get("ROOM_HOST_DATA", "/data"))
ap.add_argument("--assets", default=os.environ.get("ROOM_HOST_ASSETS", ""), help="a folder of shared files served at /__host/assets/")
ap.add_argument("--max-rooms", type=int, default=int(os.environ.get("ROOM_HOST_MAX_ROOMS", "16")), help="processes running at once")
ap.add_argument("--idle-secs", type=int, default=int(os.environ.get("ROOM_HOST_IDLE_SECS", "2700")), help="stop a room nobody has asked for this long")
ap.add_argument("--keep-days", type=float, default=float(os.environ.get("ROOM_HOST_KEEP_DAYS", "3")), help="delete a stopped room's folder after this long")
ap.add_argument("--port-base", type=int, default=21000)
args = ap.parse_args()

DATA = Path(args.data)
ROOMS_DIR = DATA / "rooms"
ROOMS_DIR.mkdir(parents=True, exist_ok=True)
ASSETS = Path(args.assets).resolve() if args.assets else None


def _load_key() -> bytes:
    f = os.environ.get("ROOM_HOST_KEY_FILE")
    k = Path(f).read_text().strip() if f else os.environ.get("ROOM_HOST_KEY", "").strip()
    if len(k) < 32:
        sys.exit("ROOM_HOST_KEY_FILE (or ROOM_HOST_KEY) must hold a key of at least 32 characters")
    return k.encode()


KEY = _load_key()
ID_RE = re.compile(r"^[a-z0-9]{8}$")
# What a room's process may be told by the Worker. Anything else in "env" is dropped: the Worker is trusted, but a
# list keeps a future field from becoming PATH or LD_PRELOAD by accident.
ENV_OK = {"FUSION_MAX_CALLS", "OPENMIC_ROOM", "ROOM_ORIGIN", "DND_CLOUD_AI", "DND_DM_RELEASE_ID", "HUMAN_PASS_SECS",
          "AUTOPASS_DEFAULT", "TABLE_AI_PASS_SECS"}
# Secrets a room may need live in this service's own environment (the box), never in a request: the Worker only says
# whether this room may have them ("ai": true), so they never cross the tunnel.
SECRET_ENV = ("DIVINCI_FUSION_API_KEY", "FUSION_CONFIG")
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade",
       "content-length", "host"}
PRIVATE = {"x-room-host-key", "x-room-id", "x-room-token", "x-public-host", "x-client-addr", "x-room-internal"}
# What changes an agent table from outside: a seat's own moves, and what a seat says about itself. Everything else that a
# person's table lets any seated player do (start a roll, hold or rewind the turn, switch the windows off, hand a seat on,
# claim one) would let one agent spoil the game for the others, so it is the lobby's alone.
AGENT_POST_EXACT = {"/api/identity", "/api/journal", "/api/declare-deck", "/api/chat", "/api/seat/check"}
# …and of a seat's moves, the ones that are moves of the GAME: what /api/seat/legal can list, plus talk, the answers the table
# asks for, and giving up. A pilot's key can do more at a people's table (set its own life, put a token down, move a card by
# hand: "manual", "life", "effect", "tap", "untap", "swap"), because there a person is keeping an honest board in step with
# real cards. At a table of agents those are a way to cheat, and the result goes on a public record.
AGENT_BRAIN_OK = {"activate", "attack", "begin", "block", "cast", "choose", "cycle", "damage", "end", "equip", "keep", "land", "mulligan",
                  "next", "pass", "station", "say", "concede", "scry", "surveil", "turn-up"}
LOBBY = os.environ.get("ROOM_HOST_LOBBY", "").rstrip("/")      # https://… of the lobby to report finished agent games to
ABANDON_SECS = int(os.environ.get("ROOM_HOST_ABANDON_SECS", "900"))    # a started agent game with fewer than two seats playing for this long
MAX_BODY = 30_000_000
MAX_LOG = 20_000_000              # a room's log is emptied past this (it is for whoever runs the box, not for the game)
RETRY_FAILED_AFTER = 60           # a room that could not start is not started again for this long

LOCK = threading.RLock()          # the ROOMS table
ROOMS: dict[str, "Room"] = {}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def free_port() -> int:
    used = {r.port for r in ROOMS.values() if r.port}
    for p in range(args.port_base, args.port_base + 2000):
        if p in used:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no free port")


class Room:
    def __init__(self, rid: str):
        self.id = rid
        self.dir = ROOMS_DIR / rid
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.token = ""
        self.last = time.time()        # the last request for this room
        self.lock = threading.Lock()   # start / stop / save, one at a time
        self.digest = ""
        self.dirty = threading.Event()
        self.saved_at = 0.0
        self.failed_at = 0.0           # the last start that failed
        self.config: dict = {}
        self.expires = 0.0             # stop the room at this time (0 = no limit)
        self.seats_seen: dict[str, float] = {}   # seat key (hashed) -> its last request for its own game: an agent table's signs of life
        self.up_since = time.time()
        self.reported = (self.dir / "reported").exists()   # the lobby has been told how this agent game ended

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ── talking to the room's own table server ──
    def call(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 60):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            c.request(method, path, body=body, headers={"X-Room-Token": self.token, "X-Forwarded-For": "room-host", "Host": "room.internal",
                                                         **({"Content-Length": str(len(body))} if body is not None else {}), **(headers or {})})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def _app(self) -> Path:
        """A fresh copy of the table code for this run (a new process starts from nothing, as a new container would)."""
        app = self.dir / "app"
        shutil.rmtree(app, ignore_errors=True)
        (app / "cloud").mkdir(parents=True)
        shutil.copytree(ROOT / "table", app / "table", ignore=shutil.ignore_patterns(".cache", "__pycache__", "tests", ".brain-token"))
        (app / "table" / ".cache").mkdir()
        oracle = ROOT / "table" / ".cache" / "oracle.json"
        if oracle.exists():
            os.symlink(oracle, app / "table" / ".cache" / "oracle.json")
        os.symlink(ROOT / "decks", app / "decks")
        shutil.copy2(ROOT / "cloud" / "entrypoint.py", app / "cloud" / "entrypoint.py")
        return app

    def start(self, config: dict, token: str, env_in: dict, ai: bool) -> bool:
        """Start the process if it is not running. Returns True when a NEW process was started."""
        with self.lock:
            self.last = time.time()
            if self.running():
                return False
            self.config = config
            self.seats_seen, self.up_since = {}, time.time()
            self.dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.dir, 0o700)
            app = self._app()
            with LOCK:
                self.port = free_port()
            self.token = token
            env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(self.dir), "LANG": "C.UTF-8",
                   "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PORT": str(self.port), "ROOM_BIND": "127.0.0.1",
                   "ROOM_CONFIG": json.dumps(config), "ROOM_TOKEN": token, "ROOM_ID": self.id, "HF_HUB_OFFLINE": "1"}
            env.update({k: str(v)[:500] for k, v in (env_in or {}).items() if k in ENV_OK})
            if not env.get("ROOM_ORIGIN", "https://").startswith("https://"):
                env.pop("ROOM_ORIGIN", None); env.pop("OPENMIC_ROOM", None)
            if ai:
                env.update({k: os.environ[k] for k in SECRET_ENV if os.environ.get(k)})
            logf = self.dir / "room.log"
            if logf.exists() and logf.stat().st_size > MAX_LOG // 4:
                logf.write_bytes(logf.read_bytes()[-200_000:])       # keep the tail of the last run
            out = open(logf, "ab", buffering=0)
            self.proc = subprocess.Popen([sys.executable, "-u", "cloud/entrypoint.py"], cwd=app, env=env, stdout=out, stderr=subprocess.STDOUT,
                                         start_new_session=True)          # its own process group: the brains die with it
            deadline = time.time() + 90
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    code = self.proc.returncode
                    self._kill()
                    self.failed_at = time.time()
                    raise RuntimeError(f"the room's process exited ({code}); see room.log")
                try:
                    if self.call("GET", "/api/room/status", timeout=3)[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.3)
            else:
                self._kill()
                self.failed_at = time.time()
                raise RuntimeError("the room did not answer within 90 s")
            self._fill()
            log(f"room {self.id}: started on {self.port} ({config.get('game', 'magic')})")
            return True

    def _fill(self):
        """A fresh process: load this room's saved game, or mark the new game as the room's."""
        st = json.loads(self.call("GET", "/api/room/status")[2] or b"{}")
        if st.get("adopted"):
            return
        snap = self.dir / "snapshot.pkl"
        if snap.exists() and snap.stat().st_size:
            code, _, body = self.call("POST", "/api/room/restore", snap.read_bytes(), {"Content-Type": "application/octet-stream"}, timeout=120)
            if code == 200:
                self.digest = hashlib.sha256(snap.read_bytes()).hexdigest()
                return
            log(f"room {self.id}: restore failed {code} {body[:200]!r}; starting a new game in its place")
        self.call("POST", "/api/room/adopt", b"")

    def save(self, why: str) -> bool:
        """Pull the game out of the process and keep it on disk (skipped when nothing changed)."""
        if not self.running():
            return False
        try:
            code, _, blob = self.call("GET", "/api/room/snapshot", timeout=60)
        except OSError as e:
            log(f"room {self.id}: save ({why}) failed: {e}")
            return False
        if code != 200 or not blob:
            return False
        d = hashlib.sha256(blob).hexdigest()
        if d == self.digest:
            return False
        tmp = self.dir / "snapshot.pkl.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.dir / "snapshot.pkl")
        self.digest, self.saved_at = d, time.time()
        return True

    def _kill(self):
        p = self.proc
        if p is None:
            return
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            p.wait(timeout=5)
        self.proc, self.port = None, 0
        shutil.rmtree(self.dir / "app", ignore_errors=True)

    def stop(self, why: str):
        with self.lock:
            if self.proc is None:
                return
            self.save(why)
            self._kill()
            log(f"room {self.id}: stopped ({why})")


def room(rid: str, create: bool = False) -> Room | None:
    with LOCK:
        r = ROOMS.get(rid)
        if r is None and create:
            r = ROOMS[rid] = Room(rid)
        return r


def make_space(for_id: str) -> bool:
    """At the cap: put the longest-idle room to sleep if it has been quiet for five minutes (its game is saved)."""
    with LOCK:
        live = [r for r in ROOMS.values() if r.running() and r.id != for_id]
    if len(live) < args.max_rooms:
        return True
    oldest = min(live, key=lambda r: r.last)
    if time.time() - oldest.last < 300:
        return False
    oldest.stop("made room for another table")
    return True


def agent_refusal(method: str, path: str, rid: str, internal: bool, body: bytes = b"") -> str | None:
    """Why a request to an agent table is refused, or None. Reads are the table's own business (it keeps every hand
    private); of the requests that change something, only a seat's own moves of the game are let through from outside."""
    if internal or method in ("GET", "HEAD"):
        return None
    if method == "POST" and path in AGENT_POST_EXACT:
        return None
    if method == "POST" and path.startswith("/api/brain/"):
        act = path[len("/api/brain/"):]
        if act in AGENT_BRAIN_OK:
            return None
        if act == "effect":                               # the one bookkeeping step the table asks a seat for: removal aimed at it
            try:
                if json.loads(body or b"{}").get("kind") == "apply-removal":
                    return None
            except (ValueError, AttributeError):
                pass
        return f"'{act}' is not a move at an agent table. Send one of the moves GET /api/seat/legal lists (see /agents/skill.md)."
    if path == "/api/seat/claim":
        return f"This is an agent table: a seat is taken with an agent key, at POST /agents/tables/{rid}/claim (see /agents/skill.md)."
    if path == "/api/phase/next":
        return "At an agent table the game starts by itself when every seat is taken. A seat plays with POST /api/brain/<action>."
    return "Not available at an agent table. A seat plays with POST /api/brain/<action> (see /agents/skill.md)."


def tell_lobby(r: "Room", event: str, extra: dict) -> bool:
    import urllib.request
    body = json.dumps({"room": r.id, "event": event, **extra}).encode()
    req = urllib.request.Request(LOBBY + "/lobby/host/report", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Room-Host-Key": KEY.decode(), "User-Agent": "divinci-table-roomhost"})
    class NoRedirect(urllib.request.HTTPRedirectHandler):       # the key goes to the lobby's address and nowhere it points
        def redirect_request(self, *a, **k):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=15) as x:
            return x.status == 200
    except Exception as e:  # noqa: BLE001
        log(f"room {r.id}: could not tell the lobby ({event}): {e}")
        return False


def reporter():
    """Agent games: tell the lobby when one is over, or abandoned, so its place is free again. Abandoned: a started game in
    which fewer than two seats have asked or acted for ABANDON_SECS. One seat playing on against an empty chair is not a game
    (each step would wait a minute for the absent seat, for hours); people watching don't count either."""
    while True:
        time.sleep(20)
        with LOCK:
            rooms = [r for r in ROOMS.values() if r.running() and r.config.get("agents") and not r.reported]
        for r in rooms:
            try:
                s = json.loads(r.call("GET", "/api/game/summary", timeout=10)[2] or b"{}")
                if s.get("over"):
                    turns = (s.get("summary") or {}).get("turns")
                    done = tell_lobby(r, "over", {"winner": s.get("winner"), "totals": s.get("totals"), "turns": turns})
                elif (time.time() - r.up_since > ABANDON_SECS
                      and sum(1 for t in list(r.seats_seen.values()) if time.time() - t < ABANDON_SECS) < min(2, len(r.config.get("pilots") or []))
                      and json.loads(r.call("GET", "/api/phase", timeout=10)[2] or b"{}").get("player")):
                    done = tell_lobby(r, "abandoned", {})
                    if done:
                        r.stop("abandoned")
                else:
                    continue
                if done:
                    r.reported = True
                    (r.dir / "reported").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            except Exception as e:  # noqa: BLE001 — one room's trouble must not stop the reporter
                log(f"room {r.id}: reporter error: {e}")


def keeper():
    """Save rooms that changed, stop idle ones, forget dead ones, and clear out old folders."""
    swept = 0.0
    while True:
        time.sleep(2)
        now = time.time()
        with LOCK:
            rooms = list(ROOMS.values())
        for r in rooms:
            try:
                if r.proc is not None and not r.running():
                    log(f"room {r.id}: its process ended ({r.proc.returncode}); the next request restarts it from the last save")
                    with r.lock:
                        r._kill()
                elif r.running() and r.dirty.is_set() and now - r.saved_at >= 5:
                    r.dirty.clear()
                    with r.lock:
                        r.save("play")
                elif r.running() and r.expires and now > r.expires:
                    r.stop("its time is up")
                elif r.running() and now - r.last > args.idle_secs:
                    r.stop("idle")
                logf = r.dir / "room.log"
                if r.running() and logf.exists() and logf.stat().st_size > MAX_LOG:
                    os.truncate(logf, 0)                   # (opened for append, so the process simply writes on)
            except Exception as e:  # noqa: BLE001 — one room's trouble must not stop the keeper
                log(f"room {r.id}: keeper error: {e}")
        if now - swept > 3600:
            swept = now
            try:
                for d in ROOMS_DIR.iterdir():
                    r = ROOMS.get(d.name)
                    if (r is None or not r.running()) and now - d.stat().st_mtime > args.keep_days * 86400:
                        shutil.rmtree(d, ignore_errors=True)
                        with LOCK:
                            ROOMS.pop(d.name, None)
                with LOCK:                                 # rooms that never ran and have no folder: forget them
                    for rid in [k for k, r in ROOMS.items() if not r.running() and not r.dir.exists()]:
                        ROOMS.pop(rid, None)
            except Exception as e:  # noqa: BLE001 — the sweep must never take the keeper down with it
                log(f"sweep error: {e}")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "roomhost"
    sys_version = ""
    timeout = 75                       # a caller that stops sending (or reading) is dropped; a table's long-poll is 25 s

    def log_message(self, *a):
        pass

    def _send(self, code: int, obj, headers: dict | None = None):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", (headers or {}).pop("Content-Type", "application/json"))
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self) -> bytes | None:
        raw = self.headers.get("Content-Length") or "0"
        n = int(raw) if raw.isdigit() else -1
        if n < 0 or n > MAX_BODY or len(self.headers.get_all("Content-Length") or []) > 1:
            self.close_connection = True
            self._send(413 if n > MAX_BODY else 400, {"error": "too large" if n > MAX_BODY else "bad Content-Length"})
            return None
        return self.rfile.read(n) if n else b""

    def _handle(self):
        path = self.path.split("?")[0]
        if path == "/healthz" and self.command in ("GET", "HEAD"):
            return self._send(200, {"ok": True})
        if not hmac.compare_digest(self.headers.get("X-Room-Host-Key", "").encode(), KEY):
            self.close_connection = True
            return self._send(404, {"error": "not found"})
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return self._send(411, {"error": "send a Content-Length"})
        body = self._body()
        if body is None:
            return
        pr = re.match(r"^/__r/([a-z0-9]{8})(/.*)$", self.path)
        if pr:                                            # a room's own path: everything after the prefix, and nothing else
            rid, rest = pr.groups()
            if rest.startswith("//") or rest.split("?")[0].startswith("/api/room/"):   # the room's private door: never through the proxy (and no "//": the table would read it as "/")
                return self._send(404, {"error": "not found"})
            r = room(rid)
            if not r or not r.running():
                return self._send(409, {"error": "room not running"}, {"X-Room-Down": "not running"})
            p0 = rest.split("?")[0]
            if r.config.get("agents"):
                why = agent_refusal(self.command, p0, rid, hmac.compare_digest(self.headers.get("X-Room-Internal", "").encode(), r.token.encode()), body)
                if why:
                    return self._send(403, {"error": why})
            sk = self.headers.get("X-Seat-Key")
            seat_call = bool(sk) and p0.startswith(("/api/brain/", "/api/seat/legal"))
            if self._proxy(r, rest, body) == 200 and seat_call:    # a sign of life: a seat's request the TABLE accepted (a made-up key is not one)
                if len(r.seats_seen) > 16:
                    r.seats_seen = dict(sorted(r.seats_seen.items(), key=lambda kv: kv[1])[-8:])
                r.seats_seen[hashlib.sha256(sk.encode()).hexdigest()[:16]] = time.time()
            return
        if self.headers.get("X-Room-Id") or not path.startswith("/__host/"):
            return self._send(404, {"error": "not found"})
        if path == "/__host/status" and self.command == "GET":
            with LOCK:
                rooms = [{"id": r.id, "running": r.running(), "idle": round(time.time() - r.last), "saved": bool(r.digest)} for r in ROOMS.values()]
            return self._send(200, {"rooms": rooms, "max": args.max_rooms})
        if path.startswith("/__host/assets/") and self.command in ("GET", "HEAD"):
            return self._asset(path[len("/__host/assets/"):])
        m = re.match(r"^/__host/rooms/([^/]+)/(ensure|sleep|release)$", path)
        if m and self.command == "POST":
            rid, what = m.groups()
            if not ID_RE.match(rid):
                return self._send(400, {"error": "bad room id"})
            try:
                b = json.loads(body or b"{}")
            except ValueError:
                return self._send(400, {"error": "not JSON"})
            if not isinstance(b, dict):
                return self._send(400, {"error": "not a JSON object"})
            if what == "ensure":
                cfg, tok = b.get("config"), str(b.get("token") or "")
                if not isinstance(cfg, dict) or len(tok) < 32:
                    return self._send(400, {"error": "config and token required"})
                r = room(rid, create=True)
                exp = b.get("expires_at")
                r.expires = exp / 1000 if isinstance(exp, (int, float)) and exp > 0 else 0.0
                if r.expires and time.time() > r.expires:
                    return self._send(410, {"error": "the room's time is up"})
                if not r.running() and time.time() - r.failed_at < RETRY_FAILED_AFTER:
                    return self._send(503, {"error": "the table could not start"}, {"Retry-After": "30"})
                if not r.running() and not make_space(rid):
                    return self._send(503, {"error": "every table is busy"}, {"Retry-After": "30"})
                try:
                    fresh = r.start(cfg, tok, b.get("env") if isinstance(b.get("env"), dict) else {}, b.get("ai") is True)
                except Exception as e:  # noqa: BLE001
                    log(f"room {rid}: start failed: {e}")
                    return self._send(503, {"error": "the table could not start"}, {"Retry-After": "5"})
                return self._send(200, {"running": True, "fresh": fresh})
            r = room(rid)
            if what == "sleep":
                if r:
                    r.stop("asked to sleep")
                return self._send(200, {"slept": True})
            if not r or not r.running():
                return self._send(409, {"error": "room not running"})
            code, _, out = r.call("POST", "/api/room/release", json.dumps({"seat": str(b.get("seat") or "")}).encode(), {"Content-Type": "application/json"})
            r.dirty.set()
            return self._send(code, out)
        return self._send(404, {"error": "not found"})

    def _asset(self, rel: str):
        if ASSETS is None or not re.match(r"^[A-Za-z0-9 ._/-]{1,200}$", rel) or ".." in rel:
            return self._send(404, {"error": "not found"})
        p = (ASSETS / rel).resolve()
        if ASSETS not in p.parents:
            return self._send(404, {"error": "not found"})
        if p.is_dir() and rel.rstrip("/") == "avatars":   # the stage's model list
            return self._send(200, sorted(f.stem for f in p.glob("*.glb")))
        if not p.is_file():
            return self._send(404, {"error": "not found"})
        data = p.read_bytes()
        self._send(200, data, {"Content-Type": "application/octet-stream", "ETag": '"' + hashlib.sha256(data).hexdigest()[:20] + '"'})

    def _proxy(self, r: Room, path: str, body: bytes) -> int:
        """Pass the request to the room's table server; returns the table's status (0 when it did not answer)."""
        r.last = time.time()
        status = 0
        # Who is asking, as the Worker saw them (X-Client-Addr). Anything a proxy on the way put in X-Forwarded-For is dropped.
        fwd = (self.headers.get("X-Client-Addr") or "unknown").split(",")[0].strip()[:100] or "unknown"
        hs = {k: v for k, v in self.headers.items() if k.lower() not in HOP | PRIVATE and not k.lower().startswith(("x-forwarded-", "forwarded", "cf-"))}
        for k in ("X-Real-IP", "True-Client-IP"):
            hs.pop(k, None)
        hs.update({"X-Forwarded-For": fwd, "Host": self.headers.get("X-Public-Host") or "room.internal", "Connection": "close"})
        if self.command not in ("GET", "HEAD") or body:
            hs["Content-Length"] = str(len(body))
        c = http.client.HTTPConnection("127.0.0.1", r.port, timeout=150)
        try:
            try:
                c.request(self.command, path, body=body or None, headers=hs)
                up = c.getresponse()
            except OSError as e:
                self._send(502, {"error": "the table is restarting. Try again in a few seconds."}, {"Retry-After": "5", "X-Room-Down": "no answer"})
                return 0
            status = up.status
            self.send_response(up.status)
            n = up.getheader("Content-Length")
            for k, v in up.getheaders():
                if k.lower() not in HOP and k.lower() not in ("server", "date", "x-room-down"):
                    self.send_header(k, v)
            if n is not None:
                self.send_header("Content-Length", n)
            else:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if self.command != "HEAD":
                while True:
                    chunk = up.read1(65536) if n is None else up.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk if n is not None else b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    if n is None:
                        self.wfile.flush()
                if n is None:
                    self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        finally:
            c.close()
            r.last = time.time()
            if self.command not in ("GET", "HEAD"):
                r.dirty.set()
        return status

    def do_GET(self):
        self._handle()

    do_POST = do_HEAD = do_PUT = do_DELETE = do_PATCH = do_GET


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


def main():
    srv = Server((args.host, args.port), H)

    def bye(*_):
        log("stopping: saving every room")
        with LOCK:
            rooms = list(ROOMS.values())
        for r in rooms:
            try:
                r.stop("host stopping")
            except Exception as e:  # noqa: BLE001
                log(f"room {r.id}: {e}")
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    threading.Thread(target=keeper, daemon=True).start()
    if LOBBY.startswith("https://") or LOBBY.startswith("http://127.0.0.1:"):
        threading.Thread(target=reporter, daemon=True).start()
    elif LOBBY:
        sys.exit("ROOM_HOST_LOBBY must be an https:// address")
    log(f"room host on {args.host}:{args.port}, data {DATA}, up to {args.max_rooms} rooms")
    srv.serve_forever()


if __name__ == "__main__":
    main()
