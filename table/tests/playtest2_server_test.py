"""Playtest 2 server fixes (docs/PLAYTEST-2-GOAL.md F1, F3, F5): the quiet autopass default, the held /api/events request,
and the visible log surviving a restart. Real servers on their own ports.

  ~/.venvs/table/bin/python table/tests/playtest2_server_test.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TABLE = Path(__file__).resolve().parents[1]
REPO = TABLE.parent
FAILED = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILED.append(name)


class Srv:
    def __init__(self, port, extra=(), tmp=None):
        self.port = port
        self.tmp = tmp or Path(tempfile.mkdtemp(prefix="playtest2-"))
        cmd = [sys.executable, str(TABLE / "server.py"), "--any-card", "--port", str(port), "--brain", "external",
               "--token-file", str(self.tmp / "token"), "--ai", "Claude|Kaust, Eyes of the Glade|",
               "--ai-deck", str(REPO / "decks/kaust.json"), "--pilot", "Claude", "--human", "Michael|", "--human", "Sam|",
               "--order", "Claude,Michael,Sam", "--priority-window", "0", *extra]
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        env.update(HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", TABLE_RESEARCH_DIR=str(self.tmp / "research"))
        self.proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=open(self.tmp / "log", "a"), stderr=subprocess.STDOUT)
        for _ in range(180):
            if self.proc.poll() is not None:
                sys.exit(f"server exited: {self.tmp / 'log'}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/phase", timeout=2).read()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(1)

    def call(self, method, path, body=None, headers=None, timeout=60):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                   headers={"Content-Type": "application/json", **(headers or {})},
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def pilot_start(s):
    code, d = s.call("POST", "/api/seat/claim", {"name": "Claude"})
    key = d["key"]
    s.call("POST", "/api/phase/next", {"by": "Claude", "key": key})
    box = {}

    def go():
        t0 = time.time()
        for _ in range(60):
            c, r = s.call("POST", "/api/brain/begin", {"seat": "Claude", "quiet": True}, {"X-Seat-Key": key}, timeout=60)
            if c == 200:
                box["done"] = time.time() - t0
                return
            time.sleep(1)
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th, box, key


def main():
    print("F1: the cloud default — people auto-pass the quiet steps of the pilot's turn")
    a = Srv(8830, ["--autopass-default", "others-no-combat"])
    try:
        p = a.call("GET", "/api/phase")[1]
        check("the phase reports the default so /me can show it", p.get("autopass_default") == "others-no-combat", str(p.get("autopass_default")))
        th, box, key = pilot_start(a)
        th.join(timeout=40)
        check("the pilot's begin completes with NO human passing (it used to stall on upkeep)", "done" in box, str(box))
        p = a.call("GET", "/api/phase")[1]
        check("the AI seat itself is never auto-passed for as a person (nobody is listed as passing for Claude)",
              "Claude" not in (p["passes"].get("passed") or []), str(p["passes"]))
        ev = [e for e in a.call("GET", "/api/events?since=0")[1]["events"] if e["type"] == "pass" and e.get("auto")]
        check("each automatic pass is in the public log with the person's name", {e["by"] for e in ev} >= {"Michael", "Sam"}, str(ev[:4]))
    finally:
        a.stop()

    print("F1: an explicit 'stop at every step' is honoured over the default")
    b = Srv(8831, ["--autopass-default", "others-no-combat", "--human-pass-secs", "0"])
    try:
        c, d = b.call("POST", "/api/seat/claim", {"name": "Michael"})
        key_m = d["key"]
        c, _ = b.call("POST", "/api/autopass", {"by": "Michael", "key": key_m, "mode": "off"})
        check("Michael switches auto-pass off", c == 200)
        th, box, key = pilot_start(b)
        time.sleep(6)
        p = b.call("GET", "/api/phase")[1]
        check("the table now waits on Michael (stopped at the step) while Sam is not asked first",
              p["passes"]["next"] == "Michael" and "done" not in box, str(p["passes"]))
        check("his choice shows as 'off' in the phase", p["autopass"].get("Michael") == "off", str(p["autopass"]))
        for _ in range(40):                                    # he stops at every step, so he passes at each one
            if "done" in box:
                break
            b.call("POST", "/api/phase/next", {"by": "Michael", "key": key_m})
            time.sleep(0.5)
        th.join(timeout=20)
        check("after he passes, the turn goes on", "done" in box, str(box))
    finally:
        b.stop()

    print("F5: /api/events is a held request")
    c = Srv(8832)
    try:
        last = c.call("GET", "/api/events?since=latest")[1]["last"]
        t0 = time.time()
        r = c.call("GET", f"/api/events?since={last}&wait=2")[1]
        el = time.time() - t0
        check("with nothing new it waits about the cap, then answers empty", 1.6 <= el <= 4.5 and r["events"] == [], f"{el:.1f}s {r}")
        t0 = time.time()
        out = {}
        th = threading.Thread(target=lambda: out.update(r=c.call("GET", f"/api/events?since={last}&wait=15")[1]))
        th.start()
        time.sleep(1.0)
        c.call("POST", "/api/chat", {"name": "Michael", "text": "hello from the long poll test"})
        th.join(timeout=20)
        el = time.time() - t0
        check("a new event wakes it at once (well before the cap)", out.get("r", {}).get("events") and el < 5, f"{el:.1f}s {out}")
        t0 = time.time()
        c.call("GET", "/api/events?since=latest&wait=10")
        check("since=latest never waits (a page's first call must not hang)", time.time() - t0 < 2)
        t0 = time.time()
        r = c.call("GET", "/api/events?since=999999&wait=10")[1]
        check("a cursor from an older server answers 'restarted' at once", r.get("restarted") is True and time.time() - t0 < 2, str(r))
        r = c.call("GET", "/api/events?since=0&wait=abc")[1]
        check("a junk wait value is treated as no wait", "events" in r)
    finally:
        c.stop()

    print("F3: the log survives a restart with --restore")
    tmp = Path(tempfile.mkdtemp(prefix="playtest2-restore-"))
    d1 = Srv(8833, tmp=tmp)
    try:
        d1.call("POST", "/api/chat", {"name": "Michael", "text": "before the deploy"})
        n1 = d1.call("GET", "/api/events?since=0")[1]
        d1.call("POST", "/api/chat", {"name": "Sam", "text": "second line"})   # triggers snapshot
        time.sleep(1.0)
        n1 = d1.call("GET", "/api/events?since=0")[1]
    finally:
        d1.stop()
    snaps = sorted((tmp / "research").glob("*/snapshot.pkl"))
    snap = snaps[-1] if snaps else tmp / "none"
    check("a snapshot was written", snap.exists(), str(snaps))
    d2 = Srv(8833, ["--restore", str(snap)], tmp=tmp)
    try:
        n2 = d2.call("GET", "/api/events?since=0")[1]
        texts = [e.get("text") for e in n2["events"]]
        check("the earlier chat lines are still in the log after the restart", "before the deploy" in texts and "second line" in texts, str(texts[-6:]))
        check("event ids continue upward (a page's cursor stays valid)", n2["last"] >= n1["last"], f"{n2['last']} < {n1['last']}")
        d2.call("POST", "/api/chat", {"name": "Michael", "text": "after"})
        n3 = d2.call("GET", "/api/events?since=0")[1]
        ids = [e["id"] for e in n3["events"]]
        check("new events get fresh ids, no duplicates", len(ids) == len(set(ids)) and ids == sorted(ids), str(ids[-6:]))
    finally:
        d2.stop()

    if FAILED:
        print(f"\nFAILED ({len(FAILED)}): " + "; ".join(FAILED))
        sys.exit(1)
    print("\nOK: all checks passed")


main()
