"""Long-polling the event log: /api/events?since=N&wait=S answers the moment something newer than N exists.

Unit checks on core.Events.wait_since, then the real dnd server: a held request is released by an event in well
under half a second (the old polling averaged ~0.75 s), a quiet log answers at the timeout, the wait is capped, a page
ahead of the server is told so at once, and a request without ?wait= is still answered instantly.

    ~/.venvs/table/bin/python table/tests/events_longpoll_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "table"))
from core import Events  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


# ── the log itself ───────────────────────────────────────────────────────────────────────────────────────────
ev = Events()
t0 = time.time(); r = ev.wait_since(0, 0.3)
check("a quiet log answers at the timeout, with nothing", 0.25 < time.time() - t0 < 0.6 and r["events"] == [], f"{time.time() - t0:.2f}s {r}")
ev.emit("a")
t0 = time.time(); r = ev.wait_since(0, 5)
check("something already newer: answered at once", time.time() - t0 < 0.1 and [e["type"] for e in r["events"]] == ["a"])
got = {}
def _held():
    got["r"] = ev.wait_since(1, 5)
    got["at"] = time.time()


threading.Thread(target=_held, daemon=True).start()
time.sleep(0.3); sent = time.time(); ev.emit("b"); time.sleep(0.2)
check("a held request is released by emit within 50 ms", "r" in got and got["at"] - sent < 0.05 and [e["type"] for e in got["r"]["events"]] == ["b"],
      f"{got.get('at', 0) - sent:.3f}s")
t0 = time.time(); r = ev.wait_since(99, 5)
check("a page ahead of the server (after a restart) is told so at once", time.time() - t0 < 0.1 and r["restarted"], str(r))
check("the wait is capped", Events.MAX_WAIT <= 30)
waiters = [threading.Thread(target=lambda: ev.wait_since(2, 5), daemon=True) for _ in range(5)]
[w.start() for w in waiters]; time.sleep(0.2); ev.emit("c"); time.sleep(0.2)
check("one emit releases every waiting request", not any(w.is_alive() for w in waiters))

# ── the real server ──────────────────────────────────────────────────────────────────────────────────────────
PORT = 8200 + os.getpid() % 90
BASE = f"http://127.0.0.1:{PORT}"
tmp = tempfile.mkdtemp()
srv = subprocess.Popen([str(Path.home() / ".venvs/table/bin/python"), "table/dnd_server.py", "--players", "Ana,Ben", "--port", str(PORT)], cwd=ROOT,
                       env={**os.environ, "DIVINCI_FUSION_API_KEY": "", "TABLE_RESEARCH_DIR": tmp}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get(path):
    t = time.time()
    with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": "lp"}), timeout=40) as r:
        return json.loads(r.read()), time.time() - t


def post(path, body, who="x"):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": who})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


try:
    for _ in range(80):
        try:
            get("/api/dnd"); break
        except OSError:
            time.sleep(0.25)
    last = get("/api/events?since=0")[0]["last"]
    d, took = get(f"/api/events?since={last}")
    check("no ?wait=: answered instantly, empty", took < 0.2 and d["events"] == [], f"{took:.2f}s")
    d, took = get(f"/api/events?since={last}&wait=1")
    check("?wait=1 on a quiet table: answers at ~1 s, empty", 0.9 < took < 1.5 and d["events"] == [], f"{took:.2f}s")
    d, took = get("/api/events?since=99999&wait=5")
    check("a page ahead of the server is told at once even with ?wait", took < 0.3 and d["restarted"], f"{took:.2f}s")
    d, took = get("/api/events?since=latest&wait=5")
    check("since=latest never waits", took < 0.3, f"{took:.2f}s")
    d, took = get("/api/events?since=0&wait=abc")
    check("a junk ?wait is treated as none", took < 0.3)
    key = post("/api/seat/claim", {"name": "Ana"}, who="lp")["key"]
    last = get("/api/events?since=0")[0]["last"]
    lat = []
    for i in range(6):
        res = {}
        th = threading.Thread(target=lambda: res.update(r=get(f"/api/events?since={last}&wait=10")), daemon=True); th.start()
        time.sleep(0.3 + i * 0.07)                                  # land the event at different moments of the wait
        sent = time.time(); post("/api/dnd/act", {"by": "Ana", "key": key, "text": f"I look around, round {i}."}, who="lp")
        th.join(10)
        d, _ = res["r"]; lat.append(time.time() - sent)
        last = d["last"]
    lat.sort()
    check(f"an event reaches a held request: median {lat[len(lat) // 2] * 1000:.0f} ms, worst {lat[-1] * 1000:.0f} ms (was ~1080/1500)", lat[len(lat) // 2] < 0.15 and lat[-1] < 0.4)
finally:
    srv.terminate()

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
