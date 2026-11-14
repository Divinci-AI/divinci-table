"""Shared by the director tests: a fake table for the unit tests, and real scratch tables (repo fixture decks, ports 8920-8999 only,
never a live game's port) for the end-to-end test. Nothing here reads a real game's files."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent.parent
sys.path.insert(0, str(REPO))
FAILS: list = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)
    return ok


def done():
    print(f"\n{'FAILED: ' + str(len(FAILS)) if FAILS else 'all passed'}", flush=True)
    sys.exit(1 if FAILS else 0)


def free_port(lo=8920, hi=8999) -> int:
    for _ in range(200):
        p = lo + int.from_bytes(os.urandom(2), "big") % (hi - lo + 1)
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p)); s.close()
            return p
        except OSError:
            s.close()
    raise RuntimeError("no free port in 8920-8999")


def keyfile(d: Path, name: str, value: str) -> str:
    f = d / f"{name.lower()}-seat.key"
    f.write_text(value); os.chmod(f, 0o600)
    return str(f)


class FakeTable:
    """A table as the director's client sees it: scripted public and private answers, and a count of every private read."""
    def __init__(self, seats, game="g1"):
        self.game, self.seats = game, list(seats)
        self.events: list = []
        self.hands = {s: [] for s in seats}
        self.legal = {s: [] for s in seats}
        self.thoughts = {s: [] for s in seats}
        self.consent: dict = {}
        self.spect = None
        self.over = False
        self.player, self.step = seats[0], "main 1"
        self.private_calls: list = []
        self.requests = 0
        self.keys: dict = {}          # seat -> the key the table expects (the fake checks the file's content)
        self.has_thoughts = True
        self.match = None

    def emit(self, type_, **f):
        e = {**f, "id": len(self.events) + 1, "ts": f.get("ts", 0.0), "type": type_}
        self.events.append(e)
        return e

    def board(self):
        return {"turn": self.player, "step": self.step,
                "seats": [{"name": s, "life": 20, "hand": len(self.hands[s]), "library": 30, "permanents": [], "graveyard": [], "graveyard_count": 0} for s in self.seats]}

    def turn_ends(self):
        """What the table does at a turn start: the spectator snapshot is taken at the current last event."""
        self.spect = {"board": self.board(), "life": {}, "as_of_event": len(self.events), "turn_of": self.player}

    def card(self, name, cost="{1}", type_="Creature", text="Secret rules text.", **extra):
        return {"name": name, "cost": cost, "type": type_, "text": text, "pt": "2/2", "castable_now": True, "slots": [{"id": 99, "options": ["x"]}], **extra}

    # the TableClient interface
    def public(self, path, query=""):
        from director.tableclient import TableError
        self.requests += 1
        if path == "/api/phase":
            return {"game_id": self.game, "player": self.player, "step": self.step, **({"game_over": True} if self.over else {})}
        if path == "/api/stream-consent":
            return {"game_id": self.game, "seats": dict(self.consent)}
        if path == "/api/events":
            since = int(dict(x.split("=") for x in query.split("&"))["since"])
            return {"last": len(self.events), "events": [e for e in self.events if e["id"] > since], "restarted": False, "game_id": self.game}
        if path == "/api/spectate":
            return dict(self.spect) if self.spect else {"board": None, "events": []}
        if path == "/api/board3d":
            return self.board()
        if path == "/api/game/summary":
            return {"over": self.over, "winner": self.seats[0], "totals": {s: 20 for s in self.seats}, "summary": {"turns": 4}} if self.over else {"over": False}
        if path == "/api/match":
            if self.match is None:
                raise TableError(404, "no match")
            return self.match
        raise TableError(404, "fake: " + path)

    def private(self, path, seat, key_file, extra=""):
        from director.tableclient import TableError
        self.requests += 1
        self.private_calls.append((path, seat))
        if seat in self.keys and Path(key_file).read_text().strip() != self.keys[seat]:
            raise TableError(403, "the table refused " + path)
        if path == "/api/brain/state":
            return {"hand": list(self.hands[seat]), "library_top": {"name": "TOP-SECRET"}}
        if path == "/api/seat/legal":
            return {"actions": list(self.legal[seat])}
        if path == "/api/seat/thought":
            if not self.has_thoughts:
                raise TableError(404, "no thoughts here")
            since = int(extra.split("=")[1])
            return {"thoughts": [t for t in self.thoughts[seat] if t["id"] > since]}
        raise TableError(404, "fake: " + path)


def http(method, url, body=None, headers=None, timeout=15):
    """(status, parsed-or-text body, headers)."""
    r = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            raw, st, hd = x.read(), x.status, dict(x.headers)
    except urllib.error.HTTPError as e:
        raw, st, hd = e.read(), e.code, dict(e.headers)
    try:
        return st, json.loads(raw), hd
    except ValueError:
        return st, raw.decode("utf-8", "replace"), hd


def start_table(port: int, tmp: Path, a: str, b: str, seed=5):
    """A real scratch table: two pilot seats on the fixture decks. Returns (process, base url, {seat: key file path})."""
    sys.path.insert(0, str(TESTS))
    import fixture_decks  # noqa: PLC0415  (skips with 77 when the card data is not there)
    decks = fixture_decks.write(tmp / "decks")
    env = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "TABLE_ART_DIR")}
    env.update(TABLE_NO_VOICE="1", TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code",
               REPLIES="template", NO_GEMMA="1", TABLE_RESEARCH_DIR=str(tmp / "r"), TABLE_SAVES_DIR=str(tmp / "s"))
    srv = subprocess.Popen([sys.executable, str(REPO / "table" / "server.py"), "--any-card", "--port", str(port), "--brain", "external",
                            "--fair-seed", "off", "--seed", str(seed), "--token-file", str(tmp / "token"),
                            "--ai", f"{a}|", "--ai-deck", str(decks / "michael.json"), "--ai", f"{b}|", "--ai-deck", str(decks / "claude.json"),
                            "--pilot", a, "--pilot", b], cwd=REPO, env=env, stdout=open(tmp / "table.log", "w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/api/phase", timeout=2); break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    files = {}
    for n in (a, b):
        st, d, _ = http("POST", base + "/api/seat/claim", {"name": n})
        files[n] = keyfile(tmp, n, d["key"])
    http("POST", base + "/api/phase/windows", {"on": False, "by": "host"})
    return srv, base, files, env


def start_autopilot(base: str, seat: str, key_file: str, env: dict, tmp: Path, grace="1"):
    e = dict(env, TABLE_URL=base, CLAUDE_SEAT=seat, CLAUDE_KEY_FILE=key_file, AUTOPILOT_GRACE=grace)
    return subprocess.Popen([sys.executable, "-u", str(REPO / "bots" / "claude_autopilot.py")], cwd=REPO, env=e,
                            stdout=open(tmp / f"auto-{seat}.log", "w"), stderr=subprocess.STDOUT)


def begin(base: str, seat: str, key_file: str):
    k = Path(key_file).read_text().strip()
    return http("POST", base + "/api/phase/next", {"by": seat, "key": k}, {"X-Seat-Key": k})
