#!/usr/bin/env python3
"""A dogfood game: two model players (named for the models) play a full Commander game against each other through the table's own API,
exactly as remote players would, on a local server with its own research folder. Nothing outside this laptop is contacted.

  ~/.venvs/table/bin/python harness/dogfood_game.py start [--run DIR] [--port 8841] [--first Sonnet] [--turns 3]   # server up, seats claimed, per-seat ./tc wrappers written
  ~/.venvs/table/bin/python harness/dogfood_game.py stop  [--run DIR]                                               # game over, bundle collected, server stopped

Per seat the run folder holds `<Seat>/tc` (a wrapper that sets the seat's env and runs table/tablectl.py), `<Seat>/key` (0600, never printed) and
`<Seat>/findings.md` (where that player writes what in the interface confused it, refused it or broke). The same brief goes to both players
(docs/dogfood-brief.md). The referee (a person or the coordinating session) watches `events.log`, takes UI screenshots with
table/tests/dogfood_ui_shots.cjs, and ends the game at the turn cap.

Pilot rules apply as in a cloud room: every request carries X-Forwarded-For, the pilot allowlist is on, and the pilot clock is raised to TABLE_AI_PASS_SECS (default 240 here).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TABLE = REPO / "table"
SEATS = {"Sonnet": ("Kaust, Eyes of the Glade", "decks/kaust.json"), "Opus": ("Tuvasa the Sunlit", "decks/tuvasa.json")}
FWD = "203.0.113.7"                                     # a documentation address: "a remote device"


def call(base, method, path, body=None, headers=None):
    h = {"Content-Type": "application/json", "X-Forwarded-For": FWD, **(headers or {})}
    r = urllib.request.Request(base + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=30) as f:
            return f.status, json.loads(f.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def start(a):
    if a.swap_decks:                                     # the same two decks, the other way round (run 2)
        k = list(SEATS); SEATS[k[0]], SEATS[k[1]] = SEATS[k[1]], SEATS[k[0]]
    run = Path(a.run or tempfile.mkdtemp(prefix="dogfood-")).resolve(); run.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{a.port}"
    order = ",".join([a.first] + [s for s in SEATS if s != a.first])
    env = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY",)}
    env.update(TABLE_CLOUD="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", TABLE_RESEARCH_DIR=str(run / "research"), TABLE_AI_PASS_SECS=os.environ.get("TABLE_AI_PASS_SECS", "240"))
    cmd = [sys.executable, str(TABLE / "server.py"), "--any-card", "--port", str(a.port), "--brain", "external", "--token-file", str(run / "host-token")]
    for seat, (cmdr, deck) in SEATS.items():
        cmd += ["--ai", f"{seat}|{cmdr}|", "--ai-deck", str(REPO / deck)]
    for seat in SEATS:
        cmd += ["--pilot", seat]
    cmd += ["--order", order, "--priority-window", "0.2"]
    proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=open(run / "server.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    (run / "server.pid").write_text(str(proc.pid))
    for _ in range(240):
        if proc.poll() is not None:
            sys.exit(f"server exited: {run / 'server.log'}")
        try:
            urllib.request.urlopen(base + "/api/phase", timeout=2).read(); break
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    else:
        sys.exit("server did not start")
    for seat in SEATS:
        d = run / seat; d.mkdir(exist_ok=True)
        c, r = call(base, "POST", "/api/seat/claim", {"name": seat})
        if c != 200:
            sys.exit(f"could not claim {seat}: {c} {r}")
        (d / "key").write_text(r["key"]); os.chmod(d / "key", 0o600)
        (d / "findings.md").write_text(f"# Findings from {seat}\n\nOne bullet per thing in the interface that confused you, refused you without a clear reason, or broke. Quote the command and the answer.\n\n")
        (d / "tc").write_text(f"""#!/bin/bash
# {seat}'s tablectl: its own seat key, remote-device rules, no host token.
export TABLE_URL={base} TABLE_SEAT={seat} TABLE_SEAT_KEY_FILE={d / 'key'} TABLE_TOKEN_FILE={run / 'no-such-token'} TABLE_FORWARD_FOR={FWD}
exec {sys.executable} {TABLE / 'tablectl.py'} "$@"
""")
        os.chmod(d / "tc", 0o755)
    (run / "meta.json").write_text(json.dumps({"port": a.port, "order": order, "turn_cap": a.turns, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "decks": {s: SEATS[s] for s in SEATS}}, indent=1))
    print(json.dumps({"run": str(run), "base": base, "order": order, "pid": proc.pid}))


def stop(a):
    run = Path(a.run).resolve(); meta = json.loads((run / "meta.json").read_text()); base = f"http://127.0.0.1:{meta['port']}"
    try:
        ev = urllib.request.urlopen(urllib.request.Request(base + "/api/events?since=0", headers={"X-Forwarded-For": FWD}), timeout=15).read().decode()
        (run / "events.json").write_text(ev)
    except Exception as e:
        print("could not save events:", e)
    pid = int((run / "server.pid").read_text())
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        pass
    print("stopped; run folder:", run)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--run"); s.add_argument("--port", type=int, default=8841); s.add_argument("--first", choices=list(SEATS), default="Sonnet"); s.add_argument("--turns", type=int, default=3); s.add_argument("--swap-decks", action="store_true")
    t = sub.add_parser("stop"); t.add_argument("--run", required=True)
    a = ap.parse_args(); start(a) if a.cmd == "start" else stop(a)
