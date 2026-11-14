"""The room host, end to end with real table servers (cloud/roomhost.py):

  - nothing answers without the shared key; /api/room/* never passes through the proxy; a room's path can never
    become one of the host's own control calls (/__host/…)
  - a room starts on `ensure`, and a proxied request is always a REMOTE one to the table (no host-only page)
  - two scripted agents claim the two seats of an agent table and play only listed moves through the proxy
  - the game is saved, the room is put to sleep, and the next `ensure` brings the same game back
  - two rooms at once keep their own files (one room's seat key is not the other's)
  - at the cap, an idle room is refused room until it has been quiet for five minutes
  - an agent table is held to its rules (only a seat's own moves change it from outside), the lobby is told when its game
    ends or is abandoned, and a room stops when its time is up

    python3 cloud/test/roomhost_test.py          (needs numpy and table/.cache/oracle.json, like the table's own tests)
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "table" / "tests"))
import freeport  # noqa: E402  (table/tests/freeport.py: ports nobody listens on)
FAILS: list[str] = []
if not (ROOT / "table" / ".cache" / "oracle.json").exists():
    print("SKIP: the room host test needs card data: run `python3 table/fetch_data.py` once")
    sys.exit(77)
if __import__("importlib.util").util.find_spec("chess") is None:      # one of its rooms is a chess room (table/chess_server.py imports it)
    print("SKIP: the room host test needs the `chess` package in the interpreter that runs it: pip install chess")
    sys.exit(77)


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:300]}"), flush=True)
    if not ok:
        FAILS.append(what)


def until(pred, secs, every=0.2) -> bool:
    """Wait for something the room host does on its own clock, for at most `secs`: True as soon as it holds. (A fixed sleep here was
    either too short on a busy machine, which failed the test, or longer than needed on an idle one.)"""
    end = time.time() + secs
    while True:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001 — not there yet
            pass
        if time.time() >= end:
            return False
        time.sleep(every)


s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
TMP = Path(tempfile.mkdtemp(prefix="roomhost-"))
KEY = secrets.token_urlsafe(32)
(TMP / "key").write_text(KEY); os.chmod(TMP / "key", 0o600)
(TMP / "assets" / "avatars").mkdir(parents=True)
(TMP / "assets" / "avatars" / "robot.glb").write_bytes(b"glb")
REPORTS: list[dict] = []          # what the room host tells "the lobby" (a stand-in on this machine)


class FakeLobby(__import__("http.server").server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        ok = self.path == "/lobby/host/report" and self.headers.get("X-Room-Host-Key") == KEY
        if ok:
            REPORTS.append(json.loads(body))
        self.send_response(200 if ok else 404); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")


s = socket.socket(); s.bind(("127.0.0.1", 0)); LP = s.getsockname()[1]; s.close()
fake = __import__("http.server").server.ThreadingHTTPServer(("127.0.0.1", LP), FakeLobby)
threading.Thread(target=fake.serve_forever, daemon=True).start()
host = subprocess.Popen([sys.executable, str(ROOT / "cloud" / "roomhost.py"), "--host", "127.0.0.1", "--port", str(PORT), "--data", str(TMP / "data"),
                         "--assets", str(TMP / "assets"), "--max-rooms", "2", "--port-base", str(freeport.free_block(12))],   # (was 23500 in every run: rooms count up from the base)
                        env={**os.environ, "ROOM_HOST_KEY_FILE": str(TMP / "key"), "ROOM_HOST_LOBBY": f"http://127.0.0.1:{LP}", "ROOM_HOST_ABANDON_SECS": "25"},
                        stdout=open(TMP / "host.log", "w"), stderr=subprocess.STDOUT)


def req(method, path, body=None, headers=None, key=KEY, raw=False, timeout=120):
    h = {"Content-Type": "application/json", **({"X-Room-Host-Key": key} if key else {}), **(headers or {})}
    data = None if body is None else body if isinstance(body, bytes) else json.dumps(body).encode()
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method, data=data, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            b = x.read()
            return x.status, (b if raw else json.loads(b or b"{}"))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, (b if raw else json.loads(b or b"{}"))
        except ValueError:
            return e.code, {"raw": b[:200].decode("utf-8", "replace")}


def agent_cfg():
    return {"game": "magic", "agents": True, "humans": [], "ai": [],
            "pilots": [{"name": "North", "commander": "Tuvasa the Sunlit", "deck": "decks/tuvasa.json"},
                       {"name": "South", "commander": "Kaust, Eyes of the Glade", "deck": "decks/kaust.json"}]}


def room(rid, method, path, body=None, seat_key=None, fwd="203.0.113.9", extra=None):
    """A request for a room's table server, the way the Worker sends it: behind /__r/<id>."""
    return req(method, f"/__r/{rid}{path}", body, {"X-Client-Addr": fwd, "X-Forwarded-For": "198.51.100.1", **({"X-Seat-Key": seat_key} if seat_key else {}), **(extra or {})})


def play(rid, keys, seconds):
    """Both seats take the first listed move, in a fixed order of preference, until the time is up or the game ends."""
    order = ["keep", "choose", "land", "cast", "attack", "block", "damage", "pass", "end", "begin"]
    acts, refused, end = 0, [], time.time() + seconds
    while time.time() < end:
        moved = False
        for seat, k in keys.items():
            c, L = room(rid, "GET", f"/api/seat/legal?seat={seat}", seat_key=k)
            if c == 429:                               # the table's own limit per seat: wait as told
                time.sleep(float(L.get("retry_after") or 0.2)); continue
            if c != 200:
                refused.append(f"legal {seat} -> {c} {L}"); continue
            if L.get("note") in ("the game is over", "you have lost"):
                return acts, refused, True
            A = sorted(L.get("actions") or [], key=lambda a: order.index(a["action"]) if a["action"] in order else 99)
            if not A:
                continue
            c, d = room(rid, "POST", "/api/brain/" + A[0]["action"], A[0]["body"], seat_key=k)
            acts += 1; moved = True
            if c not in (200, 409, 429):
                refused.append(f"{A[0]['action']} -> {c} {str(d.get('error'))[:80]}")
        time.sleep(0.4 if not moved else 0.12)
    return acts, refused, False


try:
    for _ in range(60):
        try:
            if req("GET", "/healthz", key=None)[0] == 200:
                break
        except Exception:
            time.sleep(0.2)
    A, B = "aaaaaaa2", "bbbbbbb3"
    tokA, tokB = secrets.token_hex(24), secrets.token_hex(24)

    print("the key")
    check(req("GET", "/__host/status", key=None)[0] == 404, "no key: the status page does not exist")
    check(req("GET", "/__host/status", key="x" * 43)[0] == 404, "a wrong key: the same")
    check(req("GET", f"/__r/{A}/api/phase", key=None)[0] == 404, "no key: no room either")
    check(req("GET", "/api/phase")[0] == 404 and req("GET", "/api/phase", headers={"X-Room-Id": A})[0] == 404, "a path outside the two namespaces is nothing, even with the key")
    check(req("GET", "/__host/status", headers={"X-Room-Id": A})[0] == 404, "a request that names a room never reaches the host's own controls")
    check(req("POST", f"/__host/rooms/{A}/ensure", b"[]")[0] == 400 and req("POST", f"/__host/rooms/{A}/ensure", b"{}", headers={"Content-Length": "-1"})[0] == 400,
          "a body that is not a JSON object, or a bad length, is refused")
    check(req("POST", f"/__host/rooms/{A}/ensure", {"config": agent_cfg(), "token": "short"})[0] == 400, "a room needs a real token")
    check(req("POST", "/__host/rooms/../ensure", {"config": agent_cfg(), "token": tokA})[0] in (400, 404), "a room id is eight plain characters")
    check(room(A, "GET", "/api/phase")[0] == 409, "a room that was never started answers 409 (the Worker then starts it)")

    print("a room")
    c, d = req("POST", f"/__host/rooms/{A}/ensure", {"config": agent_cfg(), "token": tokA, "env": {"LD_PRELOAD": "/x", "FUSION_MAX_CALLS": "9"}})
    check(c == 200 and d.get("fresh") is True, "ensure starts the room", (c, d))
    c, d = req("POST", f"/__host/rooms/{A}/ensure", {"config": agent_cfg(), "token": tokA})
    check(c == 200 and d.get("fresh") is False, "a second ensure reuses it", (c, d))
    c, st = room(A, "GET", "/api/stage")
    check(c == 200, "a public page of the room comes through the proxy", (c, st))
    check(room(A, "GET", "/api/state")[0] == 403, "a host-only endpoint is refused: the proxy is never 'the table's laptop'")
    check(req("GET", f"/__r/{A}/api/state")[0] == 403, "… also when the caller sends no forwarding header at all")
    check(req("GET", f"/__r/{A}/api/state", headers={"Host": "localhost", "X-Public-Host": "localhost"})[0] == 403, "… or claims to be localhost")
    check(room(A, "GET", "/api/room/status")[0] == 404, "the room's private door is not reachable through the proxy")
    check(room(A, "GET", "/api/room/snapshot", extra={"X-Room-Token": tokA})[0] == 404, "… not even with the room's token")
    c, d = room(A, "GET", "/__host/status")
    check(c in (403, 404) and "rooms" not in d, "/__host/… behind a room's prefix is just a path on the table (which has none)", (c, d))
    c, d = room(A, "POST", f"/__host/rooms/{B}/ensure", {"config": agent_cfg(), "token": tokB})
    check(c in (403, 404) and req("GET", "/__host/status")[1]["rooms"].__len__() == 1, "… so a visitor can't start, stop or list rooms through one", (c, d))
    envs = (TMP / "data" / "rooms" / A / "room.log")
    check(envs.exists(), "the room logs to its own folder")

    print("two agents play through the proxy")
    keys = {}
    lobby_only = {"X-Room-Internal": tokA}            # what the lobby's own calls carry: the room's token
    c, d = room(A, "POST", "/api/seat/claim", {"name": "North"}, fwd="agent:North")
    check(c == 403 and "agent key" in str(d), "at an agent table a seat can't be claimed from outside", (c, d))
    check(room(A, "POST", "/api/seat/claim", {"name": "North"}, fwd="agent:North", extra={"X-Room-Internal": "x" * 48})[0] == 403, "… nor with a made-up token")
    for seat in ("North", "South"):
        c, d = room(A, "POST", "/api/seat/claim", {"name": seat}, fwd="agent:" + seat, extra=lobby_only)
        keys[seat] = d.get("key", "")
    check(all(keys.values()), "the lobby claims each agent's seat for it and gets a key", keys.keys())
    c, d = room(A, "POST", "/api/seat/claim", {"name": "North"}, fwd="agent:intruder", extra=lobby_only)
    check(c == 409, "a claimed seat is refused to another caller (devices are told apart by the address the Worker names, not by X-Forwarded-For)", (c, d))
    c, d = room(A, "GET", "/api/brain/state?seat=North", seat_key=keys["South"])
    check(c == 403, "one seat's key does not open the other's hand", (c, str(d)[:80]))
    c, d = room(A, "GET", "/api/brain/state?seat=North", seat_key=keys["North"])
    check(c == 200 and "hand" in d, "its own key does", (c, list(d)[:8]))
    c, d = room(A, "POST", "/api/phase/next", {"by": "North", "key": keys["North"]}, seat_key=keys["North"])
    check(c == 403, "a seat can't start the game itself", (c, d))
    c, d = room(A, "POST", "/api/phase/next", {"by": "North", "key": keys["North"]}, seat_key=keys["North"], extra=lobby_only)
    check(c == 200, "the lobby starts it", (c, d))
    acts, refused, over = play(A, keys, 25)
    check(acts >= 15, "dozens of listed moves are played through the proxy", acts)
    check(not refused, "no listed move is refused", refused[:3])
    ev0 = room(A, "GET", "/api/events?since=0")[1].get("events") or []
    c, ph0 = room(A, "GET", "/api/phase")

    print("sleep and wake")
    # The keeper looks every 2 s and saves a changed room once 5 s have passed since its last save: "within a few seconds" on an idle
    # machine, longer on a busy one. Wait for the file itself, not for a number of seconds (it was `time.sleep(7.5)`: flaky under load).
    snap = TMP / "data" / "rooms" / A / "snapshot.pkl"
    saved = until(lambda: snap.exists() and snap.stat().st_size > 1000, 45)
    check(saved, "the game is saved to the room's folder while it is played (the keeper's own save, before any sleep)",
          snap.stat().st_size if snap.exists() else "no snapshot.pkl after 45 s")
    check(oct(snap.stat().st_mode & 0o777) == "0o600", "the saved game is readable by the service only", oct(snap.stat().st_mode & 0o777))
    check(req("POST", f"/__host/rooms/{A}/sleep", {})[0] == 200, "the room goes to sleep")
    check(room(A, "GET", "/api/phase")[0] == 409, "asleep: 409 again")
    check(not (TMP / "data" / "rooms" / A / "app").exists(), "its working copy is gone; only the save and the log stay")
    c, d = req("POST", f"/__host/rooms/{A}/ensure", {"config": agent_cfg(), "token": tokA})
    check(c == 200 and d.get("fresh") is True, "ensure wakes it", (c, d))
    c, ph1 = room(A, "GET", "/api/phase")
    check(c == 200 and (ph1.get("turn"), ph1.get("player")) == (ph0.get("turn"), ph0.get("player")), "the same game is back: same turn, same player",
          (ph0.get("turn"), ph0.get("player"), ph1.get("turn"), ph1.get("player")))
    c, d = room(A, "GET", "/api/brain/state?seat=North", seat_key=keys["North"])
    check(c == 200, "and the seat keys still work", (c, str(d)[:80]))
    acts2, refused2, _ = play(A, keys, 6)
    check(acts2 >= 3 and not refused2, "play goes on after the wake", (acts2, refused2[:2]))

    print("an agent table's rules, and its end")
    for path, body in (("/api/phase/hold", {"on": True, "by": "North"}), ("/api/phase/windows", {"on": False, "by": "North"}), ("/api/phase/back", {"by": "North"}),
                       ("/api/highroll/start", {"mode": "quantum", "by": "North"}), ("/api/seat/handoff", {"by": "North", "to": "open"}), ("/api/life", {"player": "South", "delta": -40}),
                       ("//api/seat/claim", {"name": "North"}), ("/api/seat/claim/", {"name": "North"}), ("/api/todo/done", {"id": 1, "by": "North"})):
        c, d = room(A, "POST", path, {**body, "key": keys["North"]}, seat_key=keys["North"])
        check(c == 403 or (c == 404 and path.startswith("//")), f"a seat can't POST {path}", (c, d))
    for act, body in (("manual", {"op": "token", "power": 999, "toughness": 999, "n": 20}), ("life", {"player": "me", "delta": 100000}),
                      ("effect", {"kind": "exile", "source": "#1", "at": "South", "target": "#1"}), ("untap", {"ref": "#1"}), ("draw", {"n": 7}), ("search", {"name": "Sol Ring"})):
        c, d = room(A, "POST", "/api/brain/" + act, {"seat": "North", **body}, seat_key=keys["North"])
        check(c == 403 and "not a move" in str(d), f"a seat can't use brain/{act}: it is bookkeeping, not a move of the game", (c, d))
    c, d = room(A, "POST", "/api/brain/effect", {"seat": "North", "kind": "apply-removal"}, seat_key=keys["North"])
    check(c != 403, "the one bookkeeping step the table asks a seat for (removal aimed at it) still goes through to the table", (c, d))
    check(room(A, "POST", "//api/phase/hold", {"on": True}, seat_key=keys["North"])[0] in (403, 404) and req("GET", f"/__r/{A}//api/room/status")[0] == 404, "a doubled slash opens nothing")
    check(room(A, "DELETE", "/api/brain/state", seat_key=keys["North"])[0] == 403 and room(A, "PUT", "/api/phase/next", {}, seat_key=keys["North"])[0] == 403, "nor use another method")
    c, d = room(A, "POST", "/api/identity", {"seat": "North", "model": "scripted", "provider": "test", "presents_as": "a test"}, seat_key=keys["North"])
    check(c == 200, "it can still say what it is", (c, d))
    c, d = room(A, "POST", "/api/brain/concede", {"seat": "South"}, seat_key=keys["South"])
    check(c == 200, "a seat can concede its own game", (c, str(d)[:120]))
    t0 = time.time()
    while time.time() - t0 < 45 and not any(r.get("room") == A for r in REPORTS):
        time.sleep(1)
    rep = next((r for r in REPORTS if r.get("room") == A), {})
    check(rep.get("event") == "over" and rep.get("winner") == "North", "the room host tells the lobby the game is over, and who won", rep)
    time.sleep(22)
    check(sum(1 for r in REPORTS if r.get("room") == A) == 1 and (TMP / "data" / "rooms" / A / "reported").exists(), "once")
    req("POST", f"/__host/rooms/{A}/sleep", {})

    G, tokG = "ggggggg7", secrets.token_hex(24)
    c, d = req("POST", f"/__host/rooms/{G}/ensure", {"config": agent_cfg(), "token": tokG, "expires_at": int((time.time() + 3600) * 1000)})
    check(c == 200, "another agent table starts", (c, d))
    kg = {seat: room(G, "POST", "/api/seat/claim", {"name": seat}, fwd="agent:" + seat, extra={"X-Room-Internal": tokG})[1].get("key", "") for seat in ("North", "South")}
    room(G, "POST", "/api/phase/next", {"by": "North", "key": kg["North"]}, seat_key=kg["North"], extra={"X-Room-Internal": tokG})
    t0 = time.time()
    while time.time() - t0 < 80 and not any(r.get("room") == G for r in REPORTS):
        room(G, "GET", "/api/stage")                   # someone watching is not a seat playing
        room(G, "GET", "/api/seat/legal?seat=North", seat_key=kg["North"])     # and one seat alone is not a game
        room(G, "GET", "/api/seat/legal?seat=South", seat_key="made-up-" + secrets.token_hex(6))   # nor is a key the table does not know
        time.sleep(2)
    rep = next((r for r in REPORTS if r.get("room") == G), {})
    check(rep.get("event") == "abandoned", "a started game with one seat left playing is reported abandoned (a lone seat and watchers don't keep it)", (rep, round(time.time() - t0)))
    check(until(lambda: room(G, "GET", "/api/phase")[0] == 409, 20), "and its process is stopped")

    Hh, tokH = "hhhhhhh8", secrets.token_hex(24)
    soon = int((time.time() + 14) * 1000)
    c, d = req("POST", f"/__host/rooms/{Hh}/ensure", {"config": agent_cfg(), "token": tokH, "expires_at": soon})
    check(c == 200, "a room with fourteen seconds to live starts", (c, d))
    t0 = time.time()
    while time.time() - t0 < 30 and room(Hh, "GET", "/api/stage")[0] == 200:       # in use the whole time
        time.sleep(1)
    check(14 <= time.time() - t0 + 13 and room(Hh, "GET", "/api/stage")[0] == 409, "when its time is up it is stopped, in use or not", round(time.time() - t0, 1))
    check(req("POST", f"/__host/rooms/{Hh}/ensure", {"config": agent_cfg(), "token": tokH, "expires_at": soon})[0] == 410, "and is not started again")
    c, d = req("POST", f"/__host/rooms/{A}/ensure", {"config": agent_cfg(), "token": tokA})
    check(c == 200, "the first table wakes again for the next checks", (c, d))

    print("a second room, and the cap")
    c, d = req("POST", f"/__host/rooms/{B}/ensure", {"config": {"game": "chess", "humans": ["Ann"], "ai": [], "white": "Ann", "black": "ai:Leonardo:3",
                                                               "minutes": 5, "increment": 5}, "token": tokB})
    check(c == 200, "a chess room starts next to it", (c, d))
    check(room(B, "GET", "/api/brain/state?seat=North", seat_key=keys["North"])[0] != 200, "room A's seat key opens nothing in room B")
    c, st = req("GET", "/__host/status")
    check(c == 200 and sum(1 for r in st["rooms"] if r["running"]) == 2, "two rooms are running", st)
    c, d = req("POST", "/__host/rooms/ccccccc4/ensure", {"config": agent_cfg(), "token": secrets.token_hex(24)})
    check(c == 503, "at the cap a third room is refused while the others are in use", (c, d))

    print("a room that can't start, and a name that looks like a flag")
    req("POST", f"/__host/rooms/{B}/sleep", {})
    bad = {"game": "magic", "humans": ["Ann"], "ai": [], "pilots": [{"name": "North", "commander": "Nobody", "deck": "decks/does-not-exist.json"}]}
    c, d = req("POST", "/__host/rooms/ddddddd5/ensure", {"config": bad, "token": secrets.token_hex(24)})
    check(c == 503, "a room whose process exits answers 503", (c, d))
    t0 = time.time()
    c, d = req("POST", "/__host/rooms/ddddddd5/ensure", {"config": bad, "token": secrets.token_hex(24)})
    check(c == 503 and time.time() - t0 < 1.0, "… and is not started again on the next request", (c, round(time.time() - t0, 2)))
    dash = {"game": "dnd", "humans": ["-x", "Bo"], "ai": [], "dm": "-x", "mode": "theater"}
    c, d = req("POST", "/__host/rooms/eeeeeee6/ensure", {"config": dash, "token": secrets.token_hex(24)})
    check(c == 200, "a seat named '-x' is a name, not a flag: the room starts", (c, d))
    req("POST", "/__host/rooms/eeeeeee6/sleep", {})

    print("shared files")
    c, d = req("GET", "/__host/assets/avatars")
    check(c == 200 and d == ["robot"], "the avatar list", (c, d))
    check(req("GET", "/__host/assets/avatars/robot.glb", raw=True) == (200, b"glb"), "an avatar file")
    check(req("GET", "/__host/assets/../key", raw=True)[0] == 404 and req("GET", "/__host/assets/%2e%2e/key", raw=True)[0] == 404,
          "nothing outside the assets folder")

    print("stopping")
    host.terminate()
    host.wait(timeout=40)
    left = subprocess.run(["pgrep", "-f", str(TMP / "data")], capture_output=True, text=True).stdout.split()
    check(not left, "SIGTERM saves and stops every room: no process is left behind", left)
finally:
    if host.poll() is None:
        host.kill()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
if FAILS:
    print((TMP / "host.log").read_text()[-3000:])
sys.exit(1 if FAILS else 0)
