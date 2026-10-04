"""The cloud open mic end to end on a real server, with a FAKE judge standing in for the room's Worker (Clef):
the server asks it once per spoken line (room token attached), a router "play" the judge is sure is no move doesn't
announce a card, a real move does, a missed move nudges the speaker, and side conversation on an open mic never
reaches the public log.   ~/.venvs/table/bin/python table/tests/openmic_room_test.py"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import keepalive  # noqa: E402  (one connection per server: polling must not use up local ports)
keepalive.install()

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parent
RESEARCH = HERE / ".cache" / "research"
PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


SCRIPT = {   # line → the judge's answers (only the parts that matter; the rest default to quiet)
    "I cast Llanowar Elves.": {"about": 0.96, "move": 0.97},
    "I cast Llanowar Elves into the ocean, figuratively.": {"about": 0.1, "move": 0.05},   # a vetoed "play"
    "I'm putting my creature into play, the green one.": {"about": 0.9, "move": 0.92},     # a move the router can't read
    "Did you see the game last night?": {"about": 0.02, "move": 0.03},                     # side conversation
}
ASKED: list[dict] = []


class Judge(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ASKED.append({"token": self.headers.get("X-Room-Token"), "cookie": self.headers.get("Cookie"),
                      "line": body["state"]["line"], "keys": sorted(body["questions"])})
        s = SCRIPT.get(body["state"]["line"], {"about": 0.5, "move": 0.1})
        a = {"about_game": {"noul": s["about"]}, "changes_game": {"noul": s["move"]}, "expects_answer": {"noul": 0.05},
             "addressee": {"choice": "nobody", "probabilities": {"nobody": 0.9}}}
        for k in body["questions"]:
            if k.startswith("speak_"):
                a[k] = {"score": 0.2}
        out = json.dumps({"answers": a}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)


judge = ThreadingHTTPServer(("127.0.0.1", free_port()), Judge)
threading.Thread(target=judge.serve_forever, daemon=True).start()
PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
before = set(RESEARCH.glob("*")) if RESEARCH.exists() else set()
env = dict(os.environ, TABLE_CLOUD="1", NO_GEMMA="1", ROUTER="code", REPLIES="code", OPENMIC_V2="1", OPENMIC_BACKEND="room",
           ROOM_ORIGIN=f"http://127.0.0.1:{judge.server_port}", ROOM_ID="testroom", ROOM_TOKEN="room-secret")
env.pop("DIVINCI_FUSION_API_KEY", None)
proc = subprocess.Popen([sys.executable, "table/server.py", "--port", str(PORT), "--any-card", "--brain", "external",
                         "--fair-seed", "off", "--ai", "Fusion|Tuvasa the Sunlit|", "--ai-deck", "decks/tuvasa.json",
                         "--human", "Sam|"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call(method, path, body=None, key=None):
    h = {"Content-Type": "application/json", "User-Agent": "phone", "X-Forwarded-For": "203.0.113.9"}
    if key:
        h["X-Seat-Key"] = key
    r = urllib.request.Request(BASE + path, method=method, headers=h, data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def events():
    return call("GET", "/api/events?since=0")[1].get("events", [])


try:
    for _ in range(60):
        try:
            if call("GET", "/api/phase")[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    _, s = call("POST", "/api/seat/claim", {"name": "Sam"}); sk = s.get("key")

    def say(line):
        return call("POST", "/api/chat", {"by": "Sam", "text": line, "spoken": True}, key=sk)

    c, out = say("I cast Llanowar Elves.")
    check("a spoken move is accepted", c == 200, (c, out))
    check("the judge was asked, with the room token and the room cookie", ASKED and ASKED[-1]["token"] == "room-secret"
          and ASKED[-1]["cookie"] == "room=testroom", ASKED[-1:] if ASKED else "never asked")
    check("…using only Clef-safe question keys", ASKED and all(__import__("re").fullmatch(r"[A-Za-z0-9_.-]+", k) for k in ASKED[-1]["keys"]),
          ASKED[-1]["keys"] if ASKED else None)
    heard = [e for e in events() if e["type"] == "heard"]
    check("a real cast announces its card", heard and "Llanowar Elves" in (heard[-1].get("cards") or []), heard[-1:])

    say("I cast Llanowar Elves into the ocean, figuratively.")
    heard = [e for e in events() if e["type"] == "heard"]
    check("a 'play' the judge is sure is no move announces no card", heard and not heard[-1].get("cards")
          and heard[-1].get("kind") != "play", heard[-1:])

    say("I'm putting my creature into play, the green one.")
    nudges = [e for e in events() if e["type"] == "openmic" and e.get("kind") == "maybe_move"]
    check("a move the router can't read nudges the speaker", nudges and nudges[-1].get("by") == "Sam", nudges[-1:])

    say("Did you see the game last night?")
    ev = events()
    check("side conversation on an open mic is never written to the public log",
          not any("Dodgers" in json.dumps(e) or "game last night" in json.dumps(e) for e in ev),
          [e for e in ev if "last night" in json.dumps(e)])
    check("…it shows as a placeholder", any(e["type"] == "heard" and e.get("text") == "(side conversation)" for e in ev))
    c, _ = call("POST", "/api/chat", {"by": "Sam", "text": "typed: good game"}, key=sk)
    check("a typed line (not spoken) still shows as said", any(e["type"] == "chat" and e.get("text") == "typed: good game" for e in events()))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    judge.shutdown()
    for d in (set(RESEARCH.glob("*")) if RESEARCH.exists() else set()) - before:
        if d.is_dir() and time.time() - d.stat().st_mtime < 600:
            shutil.rmtree(d, ignore_errors=True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
