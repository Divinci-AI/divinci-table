#!/usr/bin/env python3
"""migrate_live.py, end to end: carry a running game into a snapshot, restore it on another server, and
check every AI seat comes back identical — hand, board, graveyard, life, and the whole library order.

    ~/.venvs/table/bin/python table/tests/migrate_test.py

Two servers on spare ports (8817 → 8818), fair seeding off, their own token files; never touches a
running game. Prints fingerprints only, never a hidden card.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
import keepalive  # noqa: E402  (one connection per server: polling must not use up local ports)
keepalive.install()

HERE = Path(__file__).resolve().parent.parent
DECKS = HERE.parent / "decks"
TMP = Path(tempfile.mkdtemp(prefix="migrate-test-"))
os.environ["TABLE_RESEARCH_DIR"] = str(TMP / "research")         # this test's games stay out of the real data
RESEARCH = TMP / "research"
fails = []


def check(ok, what):
    print(("  ✅ " if ok else "  ❌ ") + what, flush=True)
    if not ok:
        fails.append(what)


def call(port, method, path, body=None, token=None):
    h = {"Content-Type": "application/json", **({"X-Brain-Token": token} if token else {})}
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, headers=h,
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def start(port, token, *extra):
    env = {**os.environ, "ROUTER": "code", "REPLIES": "template", "HF_HUB_OFFLINE": "1"}
    p = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--brain", "external",
                          "--ai", "Claude|Aminatou, the Fateshifter|Moira", "--ai-deck", str(DECKS / "aminatou.json"),
                          "--ai", "Fusion|Kaust, Eyes of the Glade|Daniel", "--ai-deck", str(DECKS / "kaust.json"),
                          "--human", "Ann|Kilo, Apogee Mind", "--fair-seed", "off", "--port", str(port),
                          "--token-file", str(token), *extra],
                         stdout=open(TMP / f"server-{port}.log", "a"), stderr=subprocess.STDOUT, env=env)
    for _ in range(90):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/phase", timeout=1)
            return p
        except Exception:
            time.sleep(1)
    p.kill()
    sys.exit("server did not start: " + (TMP / f"server-{port}.log").read_text()[-800:])


def fingerprint(port, token, seat):
    _, st = call(port, "GET", f"/api/brain/state?seat={seat}", token=token)
    _, pk = call(port, "POST", "/api/brain/peek", {"seat": seat, "n": st["library"], "quiet": True}, token=token)
    board = sorted((p["name"], p["tapped"], p["token"], p["face_down"]) for p in st["permanents"])
    f = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()[:12]
    return {"hand": f(sorted(h["name"] for h in st["hand"])), "board": f(board), "graveyard": f(st["graveyard"]),
            "life": st["life"], "library": f(pk["private"]["top"]), "n": st["library"]}


BEFORE = set(RESEARCH.glob("*"))
A, B = 8817, 8818
TA, TB = TMP / "token-a", TMP / "token-b"
srv_a = start(A, TA, "--order", "Fusion,Ann,Claude")
srv_b = None
try:
    ta = TA.read_text().strip()
    for seat in ("Claude", "Fusion"):                       # change things so it isn't a fresh deal
        call(A, "POST", "/api/brain/search", {"seat": seat, "name": "Forest" if seat == "Fusion" else "Swamp",
                                              "to": "battlefield"}, token=ta)
        call(A, "POST", "/api/brain/peek", {"seat": seat, "n": 3, "quiet": True}, token=ta)
    call(A, "POST", "/api/life", {"player": "Ann", "delta": -3, "by": "Fusion"})
    before = {s: fingerprint(A, ta, s) for s in ("Claude", "Fusion")}

    out = TMP / "snapshot.pkl"
    r = subprocess.run([sys.executable, str(HERE / "migrate_live.py"), "--url", f"http://127.0.0.1:{A}",
                        "--token-file", str(TA), "--order", "Fusion,Ann,Claude", "--turn", "Ann", "--step", "main 1",
                        "--human", "Ann|Kilo, Apogee Mind", "--out", str(out)], capture_output=True, text=True)
    check(r.returncode == 0 and out.exists(), "migrate_live writes a snapshot from a running server")
    check(oct(out.stat().st_mode & 0o777) == "0o600" if out.exists() else False, "the snapshot is private (0600)")
    held = {h["name"] for s in ("Claude", "Fusion") for h in call(A, "GET", f"/api/brain/state?seat={s}", token=ta)[1]["hand"]}
    check(bool(held) and not any(name in r.stdout + r.stderr for name in held),
          "its output names no card in any hand (counts and fingerprints only)")

    srv_b = start(B, TB, "--restore", str(out))
    tb = TB.read_text().strip()
    after = {s: fingerprint(B, tb, s) for s in ("Claude", "Fusion")}
    for s in ("Claude", "Fusion"):
        for k in ("hand", "board", "graveyard", "life", "n"):
            check(before[s][k] == after[s][k], f"{s}: {k} matches after restore")
        check(before[s]["library"] == after[s]["library"], f"{s}: the whole library order matches")
    _, ph = call(B, "GET", "/api/phase")
    check(ph["player"] == "Ann" and ph["step"] == "main 1" and ph["order"] == ["Fusion", "Ann", "Claude"],
          "the restored game resumes at the turn, step and order given")
    _, life = call(B, "GET", "/api/life")
    check(life.get("Ann") == 37, "human life totals carry over")
    _, d1 = call(B, "POST", "/api/brain/search", {"seat": "Claude", "name": "Plains", "to": "battlefield"}, token=tb)
    _, st = call(B, "GET", "/api/brain/state?seat=Claude", token=tb)
    ids = [p["id"] for p in st["permanents"]]
    check(len(ids) == len(set(ids)), "new permanents after a restore never reuse an id")
finally:
    for s in (srv_a, srv_b):
        if s:
            s.terminate()
    for d in set(RESEARCH.glob("*")) - BEFORE:     # only folders this test created
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(TMP, ignore_errors=True)

print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
