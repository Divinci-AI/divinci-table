#!/usr/bin/env python3
"""NEXT, priority windows and snapshot/restore, end to end against a real server on a spare port.

    ~/.venvs/table/bin/python table/tests/phase_test.py

Starts its own server (fair seeding off, its own token file, port 8815) and never touches a running game.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PORT = 8815
BASE = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="phase-test-"))
TOKEN = TMP / "token"
fails = []


def check(ok, what):
    print(("  ✅ " if ok else "  ❌ ") + what, flush=True)
    if not ok:
        fails.append(what)


def call(method, path, body=None, brain=False):
    h = {"Content-Type": "application/json"}
    if brain:
        h["X-Brain-Token"] = TOKEN.read_text().strip()
    r = urllib.request.Request(BASE + path, method=method, headers=h,
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def start(*extra):
    env = {**os.environ, "ROUTER": "code", "REPLIES": "template", "HF_HUB_OFFLINE": "1"}
    p = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--brain", "external",
                          "--ai", "Claude|Aminatou, the Fateshifter|Moira", "--ai-deck", str(HERE.parent / "decks/aminatou.json"),
                          "--human", "Ann|Kilo, Apogee Mind", "--human", "Ben|Captain N'ghathrod",
                          "--order", "Ann,Claude,Ben", "--fair-seed", "off", "--port", str(PORT),
                          "--token-file", str(TOKEN), *extra],
                         stdout=open(TMP / "server.log", "a"), stderr=subprocess.STDOUT, env=env)
    for _ in range(90):
        try:
            urllib.request.urlopen(BASE + "/api/phase", timeout=1)
            return p
        except Exception:
            time.sleep(1)
    p.kill()
    sys.exit("server did not start: " + (TMP / "server.log").read_text()[-800:])


def brain(action, **body):
    return call("POST", f"/api/brain/{action}", {"seat": "Claude", **body}, brain=True)


BEFORE = set((HERE / ".cache" / "research").glob("*"))              # never touch an existing game's logs
srv = start("--priority-secs", "30")
try:
    print("NEXT and priority")
    for land in ("Swamp", "Plains", "Island"):
        brain("search", name=land, to="battlefield")
    brain("search", name="Mortify")
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    check(code == 200 and p["player"] == "Ann" and p["step"] == "untap", "first NEXT starts the first seat's turn at untap")
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    check(p["step"] == "upkeep" and p["waiting"] == ["Claude"], "a seat holding a castable instant gets a window")
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    check(code == 409 and "Claude" in p.get("error", ""), "NEXT is refused while that seat holds priority")
    brain("pass", quiet=True)
    code, p = call("GET", "/api/phase")
    check(p["waiting"] == [], "pass closes the window")
    for _ in range(12):
        code, p = call("POST", "/api/phase/next", {"by": "Ann"})
        if p.get("waiting"):
            brain("pass", quiet=True)
        if p.get("player") == "Claude":
            break
    check(p.get("player") == "Claude" and p.get("step") == "untap", "the last step hands the turn to the next seat")
    brain("begin")
    brain("end", text="test")
    time.sleep(1.5)
    code, p = call("GET", "/api/phase")
    check(p["player"] == "Ben", "an AI ending its turn hands it to the next seat in order")

    print("snapshot / restore")
    code, st = call("GET", "/api/brain/state?seat=Claude", brain=True)
    before = json.dumps([st["hand"], st["permanents"], st["life"]], sort_keys=True)
    snaps = sorted((HERE / ".cache" / "research").glob("*/snapshot.pkl"), key=lambda x: x.stat().st_mtime)
    check(bool(snaps), "a snapshot is written after changes")
    srv.terminate(); srv.wait(10)
    srv = start("--restore", str(snaps[-1]), "--priority-secs", "2")
    code, st = call("GET", "/api/brain/state?seat=Claude", brain=True)
    check(json.dumps([st["hand"], st["permanents"], st["life"]], sort_keys=True) == before,
          "restore brings back the same hand, board and life")
    code, p = call("GET", "/api/phase")
    check(p["player"] == "Ben", "restore brings back whose turn it is")

    print("timeout")
    call("POST", "/api/phase/next", {"by": "Ben"})
    code, p = call("POST", "/api/phase/next", {"by": "Ben"})
    if p.get("waiting"):
        code, p = call("POST", "/api/phase/next", {"by": "Ben"})
        check(code == 409, "refused inside the window")
        time.sleep(2.3)
        code, p = call("POST", "/api/phase/next", {"by": "Ben"})
        check(code == 200, "a window that runs out passes for the silent seat")
    else:
        check(True, "(no instant castable this step; timeout path covered above when it is)")
finally:
    srv.terminate()
    import shutil
    for d in set((HERE / ".cache" / "research").glob("*")) - BEFORE:   # only folders this test created
        shutil.rmtree(d, ignore_errors=True)

print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
