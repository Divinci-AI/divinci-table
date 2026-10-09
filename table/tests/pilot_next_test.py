"""NEXT for a PILOT seat (a virtual deck a person plays, as Michael did in a cloud room): it may START the game, after that
the seat passes and ends turns on /hand, and NEXT must say so instead of asking "who are you?" again (the claim popup
reloads the page, the press fails the same way, and the person is stuck in a loop). A wrong key must still be refused.

  ~/.venvs/table/bin/python table/tests/pilot_next_test.py
"""
from __future__ import annotations

import json
import os
os.environ.setdefault("TABLE_HIGHROLL", "first")        # tests start the first seat in the order; the opening high roll has its own test (opening_ceremony_test.py)
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TABLE = Path(__file__).resolve().parents[1]
REPO = TABLE.parent
PORT = 8818
BASE = f"http://127.0.0.1:{PORT}"
FAILED = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILED.append(name)


def post(path, body):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pilot-next-"))
    cmd = [sys.executable, str(TABLE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external",
           "--token-file", str(tmp / "token"), "--ai", "Claude|Kaust, Eyes of the Glade|", "--ai-deck", str(REPO / "decks/kaust.json"),
           "--pilot", "Claude", "--human", "Michael|", "--human", "Sam|"]
    env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
    env.update(HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", TABLE_RESEARCH_DIR=str(tmp / "research"))
    proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=open(tmp / "server.log", "w"), stderr=subprocess.STDOUT)
    try:
        for _ in range(180):
            if proc.poll() is not None:
                sys.exit(f"server exited: {tmp / 'server.log'}")
            try:
                urllib.request.urlopen(BASE + "/api/phase", timeout=2).read()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(1)
        code, d = post("/api/seat/claim", {"name": "Claude"})
        key = d.get("key", "")
        check("the pilot seat can be claimed", code == 200 and len(key) >= 16, str(d)[:120])
        code, d = post("/api/phase/next", {"by": "Claude", "key": key})
        check("NEXT from the pilot seat starts the game", code == 200 and d.get("player"), f"{code} {str(d)[:140]}")
        code, d = post("/api/phase/next", {"by": "Claude", "key": key})
        check("after that NEXT does not send the pilot round the claim popup (no need_seat)", not d.get("need_seat"), f"{code} {str(d)[:200]}")
        check("…it says where the buttons are", code == 403 and "/hand" in str(d.get("error", "")), f"{code} {str(d)[:200]}")
        code, d = post("/api/phase/next", {"by": "Claude", "key": "not-the-key"})
        check("a wrong key is still told to claim its seat", code == 403 and d.get("need_seat") is True, f"{code} {str(d)[:160]}")
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
