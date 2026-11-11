"""The lobby: starts, lists and stops Divinci Table games, one table process per game (AGENT-TABLE-PLAN §2, PRIMETIME §1).

Loopback only (127.0.0.1) and host-only (X-Lobby-Token from a 0600 file). Nothing here opens a network port to the outside; a
public lobby is Michael's decision (hosting, Access, terms).

    POST /tables       {"seats": [{"name": "Michael", "deck": "/abs/path.json"}, ...], "format": "1v1"|"2hg",
                        "teams": {"Feds": ["Michael", "Claude"], ...} (2hg), "entry_fee": 0.05 (test mode), "decklists": "declared",
                        "windows": false}
                       -> {"id", "port", "url", "watch", "keys": {seat: "/path/to/seat.key"}}   (key FILES, 0600; never key text)
    GET  /tables       every game: id, port, seats, phase (player, step), life, game over + winner, age
    POST /tables/<id>/stop
    GET  /health

Each game runs `table/server.py` on a free port with TABLE_NO_VOICE=1 (~170 MB, see multi_table_probe), its own token file and
temp dir. At most --max-tables run at once (memory, not CPU, is the limit); a finished game is stopped after --reap-minutes.

    ~/.venvs/table/bin/python table/lobby.py --port 8830 --token-file ~/.divinci/star-trek-mtg/live/lobby.token
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8830)
ap.add_argument("--token-file", required=True)
ap.add_argument("--max-tables", type=int, default=20)
ap.add_argument("--reap-minutes", type=float, default=10)
ap.add_argument("--any-card", action="store_true", default=True)
args = ap.parse_args()

TOKEN = secrets.token_urlsafe(24)
fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, TOKEN.encode()); os.close(fd)
TABLES: dict = {}
LOCK = threading.Lock()
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _'-]{0,23}$")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def tget(port, path, timeout=5):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as x:
        return json.loads(x.read() or b"{}")


def tpost(port, path, body, headers=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(r, timeout=10) as x:
        return json.loads(x.read() or b"{}")


def create(b: dict) -> tuple[int, dict]:
    seats = b.get("seats") or []
    fmt = b.get("format") or "1v1"
    if fmt not in ("1v1", "2hg"):
        return 400, {"error": "format is 1v1 or 2hg"}
    if not (2 <= len(seats) <= 4) or (fmt == "2hg" and len(seats) != 4):
        return 400, {"error": "1v1: 2 seats; 2hg: 4 seats"}
    names = [str(s.get("name") or "") for s in seats]
    if len(set(n.lower() for n in names)) != len(names) or not all(NAME.match(n) for n in names):
        return 400, {"error": "seat names: distinct, a letter first, up to 24 of letters, digits, space, _ ' -"}
    for s in seats:
        p = Path(str(s.get("deck") or "")).expanduser()
        if not (p.is_absolute() and p.suffix == ".json" and p.is_file()):
            return 400, {"error": f"deck for {s.get('name')}: an absolute path to an existing .json deck file"}
    teams = b.get("teams") or {}
    if fmt == "2hg":
        flat = [m for ms in teams.values() for m in ms]
        if len(teams) != 2 or sorted(flat) != sorted(names) or any(len(ms) != 2 for ms in teams.values()):
            return 400, {"error": "2hg: two teams of two, covering every seat"}
    fee = float(b.get("entry_fee") or 0)
    with LOCK:
        live = [t for t in TABLES.values() if t["proc"].poll() is None]
        if len(live) >= args.max_tables:
            return 503, {"error": f"the lobby is full ({args.max_tables} games): try again when one finishes"}
        tid = secrets.token_hex(4)
        port, tmp = free_port(), Path(tempfile.mkdtemp(prefix=f"lobby-{tid}-"))
        os.chmod(tmp, 0o700)
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        env.update(TABLE_NO_VOICE="1", TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1",
                   ROUTER="code", REPLIES="template", NO_GEMMA="1", TABLE_RESEARCH_DIR=str(tmp / "r"), TABLE_SAVES_DIR=str(tmp / "s"))
        cmd = [sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(port), "--brain", "external",
               "--fair-seed", "off", "--token-file", str(tmp / "token"), "--decklists", str(b.get("decklists") or "declared")]
        for s in seats:
            cmd += ["--ai", f"{s['name']}|", "--ai-deck", str(Path(s["deck"]).expanduser()), "--pilot", s["name"]]
        for tn, ms in teams.items():
            cmd += ["--team", f"{tn}:{','.join(ms)}"]
        if fee:
            cmd += ["--entry-fee", str(fee)]
        proc = subprocess.Popen(cmd, cwd=HERE.parent, env=env, stdout=open(tmp / "server.log", "w"), stderr=subprocess.STDOUT)
        TABLES[tid] = {"id": tid, "port": port, "tmp": tmp, "proc": proc, "seats": names, "format": fmt, "teams": teams,
                       "created": time.time(), "over_at": None}
    for _ in range(80):
        try:
            tget(port, "/api/phase"); break
        except Exception:
            time.sleep(0.25)
    keys = {}
    if not fee:                                      # a paid table: each agent claims (and pays for) its own seat
        for n in names:
            k = tpost(port, "/api/seat/claim", {"name": n}).get("key") or ""
            kf = tmp / f"{n}.key"
            fd_ = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd_, k.encode()); os.close(fd_)
            keys[n] = str(kf)
    if b.get("windows") is False:
        tpost(port, "/api/phase/windows", {"on": False, "by": "host"})
    return 200, {"id": tid, "port": port, "url": f"http://127.0.0.1:{port}", "watch": f"http://127.0.0.1:{port}/watch",
                 "keys": keys, **({"entry_fee": fee, "note": "agents claim their own seats and pay (test mode)"} if fee else {})}


def describe(t: dict) -> dict:
    out = {k: t[k] for k in ("id", "port", "seats", "format", "teams")}
    out["age_s"] = round(time.time() - t["created"])
    out["running"] = t["proc"].poll() is None
    if out["running"]:
        try:
            ph = tget(t["port"], "/api/phase")
            out.update(player=ph.get("player"), step=ph.get("step"), life=tget(t["port"], "/api/life"),
                       game_over=bool(ph.get("game_over")), winner=ph.get("winner"))
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)[:80]
    return out


def stop(tid: str) -> bool:
    t = TABLES.get(tid)
    if not t:
        return False
    if t["proc"].poll() is None:
        t["proc"].terminate()
    scrub(t)
    return True


def scrub(t: dict) -> None:
    """A stopped game's secrets go with it: its seat-key files and host token. Logs stay for the post-game review (S19)."""
    for f in list(t["tmp"].glob("*.key")) + [t["tmp"] / "token"]:
        try:
            f.unlink()
        except FileNotFoundError:
            pass


def reaper():
    while True:
        time.sleep(15)
        with LOCK:
            items = list(TABLES.values())
        for t in items:
            if t["proc"].poll() is not None:
                continue
            try:
                over = tget(t["port"], "/api/phase").get("game_over")
            except Exception:
                continue
            if over and t["over_at"] is None:
                t["over_at"] = time.time()
            if t["over_at"] and time.time() - t["over_at"] > args.reap_minutes * 60:
                stop(t["id"])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self):
        return self.client_address[0] in ("127.0.0.1", "::1") and not self.headers.get("X-Forwarded-For") \
            and secrets.compare_digest(self.headers.get("X-Lobby-Token", ""), TOKEN)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "tables": sum(1 for t in TABLES.values() if t["proc"].poll() is None),
                                    "max": args.max_tables})
        if not self._ok():
            return self._send(403, {"error": "lobby token required (this laptop only)"})
        if self.path == "/tables":
            return self._send(200, {"tables": [describe(t) for t in TABLES.values()]})
        return self._send(404, {"error": "no such route"})

    def do_POST(self):
        if not self._ok():
            return self._send(403, {"error": "lobby token required (this laptop only)"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            b = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return self._send(400, {"error": "JSON body"})
        if self.path == "/tables":
            code, out = create(b)
            return self._send(code, out)
        m = re.fullmatch(r"/tables/([0-9a-f]{8})/stop", self.path)
        if m:
            return self._send(200 if stop(m.group(1)) else 404, {"stopped": m.group(1)})
        return self._send(404, {"error": "no such route"})


def _bye(*_a):                                   # SIGTERM / SIGINT: stop every game this lobby started, then exit
    raise SystemExit(0)


import signal  # noqa: E402
signal.signal(signal.SIGTERM, _bye)
signal.signal(signal.SIGINT, _bye)
threading.Thread(target=reaper, daemon=True).start()
print(f"lobby on http://127.0.0.1:{args.port} (loopback only), up to {args.max_tables} games", flush=True)
try:
    ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()
finally:
    for t in TABLES.values():
        if t["proc"].poll() is None:
            t["proc"].terminate()
        scrub(t)
