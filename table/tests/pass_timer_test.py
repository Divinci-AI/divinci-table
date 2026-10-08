"""The pass timer (--human-pass-secs): a person who does not pass priority in someone else's turn is passed for, in the
log, after the time; the active player is never timed out; a hold pauses it; the deadline the pages count down to is
published in the server's own clock. Two real servers on their own ports.

  ~/.venvs/table/bin/python table/tests/pass_timer_test.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TABLE = Path(__file__).resolve().parents[1]
REPO = TABLE.parent
FAILED = []
SECS = 2


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILED.append(name)


class Srv:
    def __init__(self, port, order, secs=SECS):
        self.port = port
        self.tmp = Path(tempfile.mkdtemp(prefix="passtimer-"))
        cmd = [sys.executable, str(TABLE / "server.py"), "--any-card", "--port", str(port), "--brain", "external",
               "--token-file", str(self.tmp / "token"), "--ai", "Claude|Kaust, Eyes of the Glade|",
               "--ai-deck", str(REPO / "decks/kaust.json"), "--pilot", "Claude", "--human", "Michael|", "--human", "Sam|",
               "--order", order, "--priority-window", "0", "--human-pass-secs", str(secs)]
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        env.update(HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", TABLE_RESEARCH_DIR=str(self.tmp / "research"))
        self.proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=open(self.tmp / "log", "w"), stderr=subprocess.STDOUT)
        for _ in range(180):
            if self.proc.poll() is not None:
                sys.exit(f"server exited: {self.tmp / 'log'}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/phase", timeout=2).read()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(1)

    def call(self, method, path, body=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers={"Content-Type": "application/json"},
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def passes(self):
        return self.call("GET", "/api/phase")[1]["passes"]

    def events(self):
        return self.call("GET", "/api/events?since=0")[1]["events"]

    def start(self, walk=True):
        """The pilot starts the game; when it is the pilot's own turn it also walks its turn to upkeep (like tablectl begin:
        retried in the background while the table waits on passes), where people must pass."""
        import threading
        code, d = self.call("POST", "/api/seat/claim", {"name": "Claude"})
        out = self.call("POST", "/api/phase/next", {"by": "Claude", "key": d["key"]})
        if walk and out[0] == 200 and out[1].get("player") == "Claude":
            def go():
                for _ in range(120):
                    r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/brain/begin", method="POST",
                                               headers={"Content-Type": "application/json", "X-Seat-Key": d["key"]},
                                               data=json.dumps({"seat": "Claude", "quiet": True}).encode())
                    try:
                        urllib.request.urlopen(r, timeout=30).read()
                        return
                    except urllib.error.HTTPError:
                        time.sleep(1)
            threading.Thread(target=go, daemon=True).start()
            time.sleep(1.0)
        return out

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def main():
    a = Srv(8820, "Claude,Michael,Sam")
    try:
        print("in the pilot's turn (people must pass)")
        check("before the game starts there is no deadline", "deadline" not in a.passes())
        code, d = a.start()
        check("the pilot starts the game", code == 200 and d["player"] == "Claude", str((code, d)))
        p = a.passes()
        check("Michael is next, with a deadline in the server's own clock about two seconds out",
              p["next"] == "Michael" and p.get("secs") == SECS and 0 < p["deadline"] - p["now"] <= SECS + 0.2, str(p))
        time.sleep(SECS + 0.6)
        p = a.passes()
        check("after the time Michael is passed for, and Sam's own clock starts", p["passed"] == ["Michael"] and p["next"] == "Sam"
              and p["deadline"] > time.time() - 1, str(p))
        time.sleep(SECS + 0.6)
        ev = [e for e in a.events() if e["type"] == "pass" and e.get("timeout") and e.get("human") and e.get("step") == "upkeep"]
        check("then Sam is passed for too, and both timeouts are in the public log as people's, with the step",
              [e["by"] for e in ev] == ["Michael", "Sam"] and all(e.get("player") == "Claude" for e in ev), str(ev))
        gaps = ev[1]["ts"] - ev[0]["ts"] if len(ev) == 2 else None
        check("Sam's clock started when Michael's ended, not before", gaps is not None and SECS - 0.5 <= gaps <= SECS + 1.5, str(gaps))
        # a hold pauses the timer
        a.call("POST", "/api/phase/windows", {"on": True})
        code, _ = a.call("POST", "/api/phase/back", {"by": "Claude"})
    finally:
        a.stop()

    b = Srv(8821, "Michael,Claude,Sam")
    try:
        print("in a person's own turn, and holds")
        code, d = b.start()
        check("a person's turn is under way", code == 200 and d["player"] == "Michael", str((code, d)))
        time.sleep(SECS + 1.2)
        p = b.passes()
        check("the active player is never timed out, and nobody behind them is passed for either",
              p["passed"] == [] and "deadline" not in p, str(p))
        c = Srv(8822, "Claude,Michael,Sam", secs=0)
        try:
            c.start()
            time.sleep(SECS + 1.0)
            p = c.passes()
            check("with the timer off (the default) nobody is ever passed for and no deadline is published",
                  p["passed"] == [] and "deadline" not in p, str(p))
        finally:
            c.stop()
        d_ = Srv(8823, "Claude,Michael,Sam")
        try:
            d_.start()
            code, _ = d_.call("POST", "/api/phase/hold", {"on": True, "by": "Sam"})
            time.sleep(SECS + 1.2)
            p = d_.passes()
            check("a hold pauses the timer", code == 200 and p["passed"] == [] and "deadline" not in p, str((code, p)))
            d_.call("POST", "/api/phase/hold", {"on": False, "by": "Sam"})
            p = d_.passes()
            check("releasing it gives Michael the full time again, not zero",
                  p["passed"] == [] and p.get("deadline") and p["deadline"] - p["now"] > SECS - 0.8, str(p))
        finally:
            d_.stop()
    finally:
        b.stop()
    print(f"\n{'all passed' if not FAILED else str(len(FAILED)) + ' FAILED: ' + '; '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
