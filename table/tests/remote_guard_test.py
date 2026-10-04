"""Cloud rooms: another device changes the table only with a seat key, and only for its own seat where it
should; a pilot seat (a person's virtual deck) sees and plays only its own cards.

    ~/.venvs/table/bin/python table/tests/remote_guard_test.py

Starts its own server (TABLE_CLOUD=1, a free port, no model); every request carries X-Forwarded-For, which is
what the cloud Worker sends, so the server treats it as another device."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

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


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
before = set(RESEARCH.glob("*")) if RESEARCH.exists() else set()
env = dict(os.environ, TABLE_CLOUD="1", NO_GEMMA="1", ROUTER="code", REPLIES="code")
env.pop("DIVINCI_FUSION_API_KEY", None)
proc = subprocess.Popen([sys.executable, "table/server.py", "--port", str(PORT), "--any-card", "--brain", "external",
                         "--fair-seed", "off", "--ai", "Fusion|Tuvasa the Sunlit|", "--ai-deck", "decks/tuvasa.json",
                         "--ai", "Michael|Inspirit, Flagship Vessel|", "--ai-deck", "decks/inspirit.json",
                         "--pilot", "Michael", "--human", "Sam|"], cwd=ROOT, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call(method, path, body=None, key=None, remote=True, ua="phone"):
    h = {"Content-Type": "application/json", "User-Agent": ua}
    if remote:
        h["X-Forwarded-For"] = "203.0.113.9"
    if key:
        h["X-Seat-Key"] = key
    r = urllib.request.Request(BASE + path, method=method, headers=h,
                               data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(r, timeout=20) as x:
            raw = x.read()
            return x.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else {})


try:
    for _ in range(60):
        try:
            if call("GET", "/api/phase", remote=False)[0] == 200:
                break
        except OSError:
            pass
        time.sleep(0.5)
    else:
        sys.exit("server did not start")

    _, m = call("POST", "/api/seat/claim", {"name": "Michael"}, ua="phone-m"); mk = m.get("key")
    _, s = call("POST", "/api/seat/claim", {"name": "Sam"}, ua="phone-s"); sk = s.get("key")
    check("both seats claimed (the pilot seat is claimable)", mk and sk, (m, s))

    print("the seat guard (another device)")
    check("anonymous life change refused", call("POST", "/api/life", {"player": "Sam", "delta": -5})[0] == 403)
    check("Sam can't change Michael's life", call("POST", "/api/life", {"player": "Michael", "delta": -5}, key=sk)[0] == 403)
    c, _ = call("POST", "/api/life", {"player": "Sam", "delta": -5, "by": "someone else"}, key=sk)
    _, lt = call("GET", "/api/life")
    table = lt.get("life", lt) if isinstance(lt, dict) else {}
    check("Sam changes his own life (40 → 35)", c == 200 and table.get("Sam") == 35, lt)
    check("a made-up key is refused", call("POST", "/api/life", {"player": "Sam", "delta": 1}, key="made-up")[0] == 403)
    check("the key in the body works too", call("POST", "/api/life", {"player": "Sam", "delta": 1, "key": sk})[0] == 200)
    check("anonymous hold refused", call("POST", "/api/phase/hold", {"on": True, "by": "stage"})[0] == 403)
    c, ph = call("POST", "/api/phase/hold", {"on": True, "by": "stage"}, key=sk)
    check("a seated player holds, recorded under their name", c == 200 and ph.get("hold_by", ph.get("hold", {})) is not None, ph)
    _, ev = call("GET", "/api/events?since=0")
    hold = [e for e in ev.get("events", []) if e.get("kind") == "hold"]
    check("…as Sam, not 'stage'", hold and hold[-1].get("by") == "Sam", hold[-1:] if hold else ev)
    call("POST", "/api/phase/hold", {"on": False}, key=sk)
    check("anonymous BACK refused", call("POST", "/api/phase/back", {"by": "Sam"})[0] == 403)
    check("anonymous timeouts switch refused", call("POST", "/api/phase/windows", {"on": False})[0] == 403)
    check("Sam can't talk as Michael", call("POST", "/api/chat", {"by": "Michael", "text": "~ I concede"}, key=sk)[0] == 403)
    check("Sam talks as himself", call("POST", "/api/chat", {"by": "Sam", "text": "~ hello"}, key=sk)[0] == 200)
    check("Sam can't declare Michael's attack", call("POST", "/api/declare/attack", {"by": "Michael", "attacks": [
        {"attacker": "Bear", "target": "Fusion", "power": 2}]}, key=sk)[0] == 403)
    check("Sam can't set Michael's fair word", call("POST", "/api/fair/word", {"player": "Michael", "word": "x"}, key=sk)[0] == 403)
    check("Sam can't set Michael's hand count", call("POST", "/api/stage/hand", {"player": "Michael", "n": 1}, key=sk)[0] == 403)
    check("anonymous placed-mark refused", call("POST", "/api/placed", {"key": "1:0", "on": True})[0] == 403)
    check("anonymous high roll refused", call("POST", "/api/highroll/start", {"mode": "quantum", "sides": 20})[0] == 403)
    check("the laptop itself is never asked", call("POST", "/api/phase/hold", {"on": False, "by": "host"}, remote=False)[0] == 200)

    c, d = call("POST", "/api/seat/check", {}, key=sk)
    check("seat check names the key's seat", c == 200 and d.get("seat") == "Sam", (c, d))
    _, dm = call("POST", "/api/seat/check", {}, key=mk)
    check("seat check gives a virtual deck's card names (for reading photos)", "Inspirit, Flagship Vessel" in (dm.get("deck") or [])
          and len(dm["deck"]) > 50, (dm.get("seat"), len(dm.get("deck") or [])))
    check("…and none for a seat whose deck is unknown", d.get("deck") == [], d.get("deck"))
    check("seat check refuses no key / a made-up key", call("POST", "/api/seat/check", {})[0] == 403
          and call("POST", "/api/seat/check", {}, key="made-up")[0] == 403)
    c, _ = call("POST", "/api/my-board", {"by": "Sam", "key": sk, "permanents": [{"name": "Forest"}], "graveyard": ["Shock"]})
    c2, _ = call("POST", "/api/my-board", {"by": "Sam", "key": sk, "permanents": [{"name": "Forest"}, {"name": "Llanowar Elves", "tapped": True}]})
    _, b3 = call("GET", "/api/board3d")
    sam = next((x for x in b3.get("seats", []) if x.get("name") == "Sam"), {})
    check("a board photo (no graveyard sent) keeps the graveyard", c == 200 and c2 == 200 and sam.get("graveyard") == ["Shock"]
          and len(sam.get("permanents", [])) >= 2, (c, c2, sam.get("graveyard"), len(sam.get("permanents", []))))
    call("POST", "/api/my-board", {"by": "Sam", "key": sk, "permanents": [{"name": "Forest", "tapped": True}, {"name": "Island"},
                                                                         {"name": "Morph", "face_down": True}], "graveyard": ["Shock"]})
    c, md = call("POST", "/api/my-board", {"by": "Sam", "key": sk, "merge": True, "seen": [
        {"name": "Forest", "tapped": False, "count": 1}, {"name": "Llanowar Elves", "tapped": False, "count": 2}, {"name": ""}, "junk"]})
    _, b3 = call("GET", "/api/board3d")
    perms = next((x for x in b3.get("seats", []) if x.get("name") == "Sam"), {}).get("permanents", [])
    names = sorted(p.get("name") or "(face-down)" for p in perms)
    check("a photo's reading ADDS (2 Elves) and updates (Forest seen upright), removes nothing",
          c == 200 and md.get("added") == 2 and md.get("updated") == 1 and len(perms) == 5, (c, md, names))
    check("…keeping the face-down card and the graveyard", any(p.get("face_down") or not p.get("name") for p in perms)
          and next((x for x in b3.get("seats", []) if x.get("name") == "Sam"), {}).get("graveyard") == ["Shock"], names)
    check("Sam can't record Michael's board", call("POST", "/api/my-board", {"by": "Michael", "key": sk, "permanents": []})[0] == 403)
    print("pilot seat (a person's virtual deck)")
    c, st = call("GET", "/api/brain/state?seat=Michael", key=mk)
    check("Michael sees his own hand", c == 200 and isinstance(st.get("hand"), list) and len(st["hand"]) >= 7, (c, str(st)[:160]))
    check("Sam's key can't read Michael's hand", call("GET", "/api/brain/state?seat=Michael", key=sk)[0] == 403)
    check("Michael can't read Fusion's hand", call("GET", "/api/brain/state?seat=Fusion", key=mk)[0] == 403)
    check("no key, no hand", call("GET", "/api/brain/state?seat=Michael")[0] == 403)
    check("Michael can't act for Fusion", call("POST", "/api/brain/land", {"seat": "Fusion", "name": "Forest"}, key=mk)[0] == 403)
    check("a pilot can't start a new game", call("POST", "/api/brain/new-game", {"seat": "Michael"}, key=mk)[0] == 403)
    check("a pilot can't take from another hand (no hidden-hand oracle)",
          call("POST", "/api/brain/take", {"seat": "Michael", "from": "Fusion", "name": "Forest"}, key=mk)[0] == 403)
    check("a pilot can't change another player's life",
          call("POST", "/api/brain/life", {"seat": "Michael", "player": "Sam", "delta": -10}, key=mk)[0] == 403)
    lands = [h["name"] for h in st.get("hand", []) if h.get("land")]
    if lands:
        c, _ = call("POST", "/api/brain/land", {"seat": "Michael", "name": lands[0]}, key=mk)
        _, st2 = call("GET", "/api/brain/state?seat=Michael", key=mk)
        check(f"Michael plays a land ({lands[0]})", c == 200 and any(p["name"] == lands[0] for p in st2.get("permanents", [])))
    check("/hand is reachable from another device", call("GET", "/hand")[0] == 200)
    c, ph = call("POST", "/api/phase/next", {"by": "Michael", "key": mk})
    check("a virtual-deck seat can START the game", c == 200 and ph.get("player"), (c, ph.get("error"), ph.get("player")))
    c, ph = call("POST", "/api/phase/next", {"by": "Michael", "key": mk})
    check("…but NEXT after that stays a person's button (turns run from /hand)", c == 403, (c, ph.get("player")))
    check("Sam's key can't START or press NEXT as Michael", call("POST", "/api/phase/next", {"by": "Michael", "key": sk})[0] == 403)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    for d in (set(RESEARCH.glob("*")) if RESEARCH.exists() else set()) - before:   # only what this test created
        if d.is_dir() and time.time() - d.stat().st_mtime < 600:
            shutil.rmtree(d, ignore_errors=True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
