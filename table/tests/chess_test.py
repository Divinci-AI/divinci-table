"""Chess table: who may move, the clocks, the end of a game, persistence, the 3D board's files, the long poll, the AI.

    ~/.venvs/table/bin/python table/tests/chess_test.py
Needs python-chess (the table's venv has it). No network; the AI part starts its own server on a safe port."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
os.environ["TABLE_RESEARCH_DIR"] = os.environ.get("TABLE_RESEARCH_DIR") or str(Path(os.environ.get("TMPDIR", "/tmp")) / "chess-test-research")
sys.argv = ["chess_server.py", "--white", "Ana", "--black", "Ben", "--minutes", "5", "--increment", "3", "--port", "0"]
import chess_server as C  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

PASS = FAIL = 0
RESERVED = {8800, 8801, 8820, 8830, 8883}           # live tables: a test never touches these


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


srv = ThreadingHTTPServer(("127.0.0.1", 0), C.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"


def call(path: str, body: dict | None = None, ua: str = "phone-a", headers: dict | None = None, raw: bool = False):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": ua, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"{}")), r.headers
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, (data if raw else json.loads(data or b"{}")), e.headers
        except ValueError:
            return e.code, {"raw": data[:200]}, e.headers


print("seats")
_, ana, _ = call("/api/seat/claim", {"name": "Ana"}, ua="phone-a")
_, ben, _ = call("/api/seat/claim", {"name": "Ben"}, ua="phone-b")
A = {"by": "Ana", "key": ana.get("key", "")}
B = {"by": "Ben", "key": ben.get("key", "")}
check("both players claim a seat and get a key", A["key"] and B["key"] and A["key"] != B["key"])
code, out, _ = call("/api/chess/start", {"by": "Ana"})
check("no key: can't start (403)", code == 403, str(code))
code, out, _ = call("/api/chess/start", {"by": "Ana", "key": B["key"]})
check("Ben's key can't act as Ana (403)", code == 403, str(code))
code, out, _ = call("/api/chess/move", {**A, "move": "e2e4"})
check("a move before Start is refused (409)", code == 409 and "Start" in out.get("error", ""), f"{code} {out.get('error')}")
code, out, _ = call("/api/chess/start", A)
check("Ana starts the clocks", code == 200 and out["started"], str(code))

print("moves")
code, out, _ = call("/api/chess/move", {**B, "move": "e7e5"})
check("Ben can't move on Ana's turn (409)", code == 409 and "Ana" in out.get("error", ""), f"{code} {out.get('error')}")
code, out, _ = call("/api/chess/move", {**A, "move": "e2e5"})
check("an illegal move is refused (400)", code == 400, str(code))
code, out, _ = call("/api/chess/move", {**A, "move": "e2e4"})
check("a legal move in UCI is played", code == 200 and out["lastmove"] == "e2e4" and out["turn"] == "black", str(code))
code, out, _ = call("/api/chess/move", {**B, "move": "e5"})
check("a legal move in SAN is played", code == 200 and out["lastmove"] == "e7e5", f"{code} {out.get('error')}")
check("the public state lists the legal moves for the side to move", out["legal"] and all(len(m) in (4, 5) for m in out["legal"]) and "g1f3" in out["legal"])
for mv, who in (("g1f3", A), ("b8c6", B), ("f1c4", A), ("g8f6", B)):
    call("/api/chess/move", {**who, "move": mv})
code, out, _ = call("/api/chess/move", {**A, "move": "e1g1"})
check("castling is played and the rook follows (FEN)", code == 200 and out["fen"].startswith("r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQ1RK1"), out.get("fen"))
check("the move list is in SAN", out["san"][-1] == "O-O", str(out["san"][-1:]))

print("clocks")
s1 = call("/api/chess")[1]
time.sleep(1.2)
s2 = call("/api/chess")[1]
check("the clock of the side to move runs", s2["clock"][s2["turn"]] < s1["clock"][s1["turn"]] - 0.8, f"{s1['clock']} → {s2['clock']}")
other = "white" if s2["turn"] == "black" else "black"
check("the other clock stands still", abs(s2["clock"][other] - s1["clock"][other]) < 0.05)
before = s2["clock"]["black"]
call("/api/chess/move", {**B, "move": "d7d6"})
s3 = call("/api/chess")[1]
check("the increment is added after a move (+3 s)", s3["clock"]["black"] > before + 2.0, f"{before} → {s3['clock']['black']}")

print("persistence")
snap = C.dump()
fen, game_id = C.G["board"].fen(), C.G["game_id"]
C.load(json.loads(json.dumps(snap)))
check("a snapshot round-trips the position and the game id", C.G["board"].fen() == fen and C.G["game_id"] == game_id)
check("…and the move history (SAN replays)", call("/api/chess")[1]["san"][:2] == ["e4", "e5"])

print("seat.js routes")
code, out, _ = call("/api/game/id")
check("/api/game/id names the game and its id", code == 200 and out == {"game": "chess", "game_id": game_id}, str(out))
code, out, _ = call("/api/seat/check", {}, headers={"X-Seat-Key": A["key"]})
check("/api/seat/check knows Ana's key", code == 200 and out.get("seat") == "Ana", f"{code} {out}")
code, out, _ = call("/api/seat/check", {}, headers={"X-Seat-Key": "not-a-key"})
check("…and refuses a stranger's with the words seat.js looks for", code == 403 and out.get("error") == "not a seated player", f"{code} {out}")

print("3D board files")
code, body, h = call("/vendor/three.module.min.js", raw=True)
check("three.js is served to the page", code == 200 and h.get("Content-Type", "").startswith("text/javascript") and len(body) > 100_000, str(code))
code, body, h = call("/assets/chess-scene.js", raw=True)
check("the board module is served", code == 200 and b"createChessScene" in body, str(code))
for bad in ("/vendor/../chess_server.py", "/vendor/%2e%2e/chess_server.py", "/assets/../../README.md", "/vendor/three-LICENSE"):
    code, _, _ = call(bad, raw=True)
    check(f"nothing outside the page code: {bad} → 404", code == 404, str(code))

print("long poll")
since = call("/api/events?since=latest")[1].get("last", C.EVENTS.next_id - 1)
t0 = time.time()
code, out, _ = call(f"/api/events?since={C.EVENTS.next_id - 1}&wait=1")
check("a long poll with nothing new waits, then answers empty", code == 200 and 0.8 < time.time() - t0 < 3 and not out.get("events"), f"{time.time() - t0:.2f}s")
res = {}


def waiter():
    t = time.time()
    res["out"] = call(f"/api/events?since={C.EVENTS.next_id - 1}&wait=10")[1]
    res["dt"] = time.time() - t


th = threading.Thread(target=waiter); th.start(); time.sleep(0.3)
call("/api/chess/move", {**A, "move": "h2h3"})
th.join(12)
check("a move wakes a held poll at once", res.get("dt", 99) < 2 and any(e.get("kind") == "move" for e in res["out"].get("events", [])), f"{res.get('dt')}")

print("end of game")
code, out, _ = call("/api/chess/draw", {**B, "action": "offer"})
check("Ben offers a draw", code == 200 and out["draw_offer"] == "black", str(out.get("draw_offer")))
code, out, _ = call("/api/chess/resign", A)
check("Ana resigns: Black wins", code == 200 and out["result"] == "0-1", str(out.get("result")))
code, out, _ = call("/api/chess/move", {**B, "move": "a7a6"})
check("no moves after the game is over (409)", code == 409, str(code))
check("the legal list is empty when the game is over", call("/api/chess")[1]["legal"] == [])

print("the AI answers (own server)")
port = random.choice([p for p in range(9100, 9900) if p not in RESERVED])
env = {**os.environ, "TABLE_RESEARCH_DIR": os.environ["TABLE_RESEARCH_DIR"]}
proc = subprocess.Popen([sys.executable, str(HERE / "chess_server.py"), "--white", "Ana", "--black", "ai:Leonardo:3", "--port", str(port)],
                        cwd=str(HERE.parent), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    BASE = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            call("/api/chess"); break
        except Exception:
            time.sleep(0.25)
    key = call("/api/seat/claim", {"name": "Ana"})[1].get("key", "")
    call("/api/chess/start", {"by": "Ana", "key": key})
    call("/api/chess/move", {"by": "Ana", "key": key, "move": "d2d4"})
    t0, s = time.time(), {}
    while time.time() - t0 < 20:
        s = call("/api/chess")[1]
        if s.get("turn") == "white" and len(s.get("moves", [])) == 2:
            break
        time.sleep(0.3)
    check("the AI (no Stockfish here: the built-in engine) answers a move", len(s.get("moves", [])) == 2, f"{s.get('moves')}")
    code, out, _ = call("/api/chess/move", {"by": "Leonardo", "key": key, "move": "a2a3"})
    check("nobody can move for the AI seat (403)", code == 403, str(code))
finally:
    proc.terminate(); proc.wait(5)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
