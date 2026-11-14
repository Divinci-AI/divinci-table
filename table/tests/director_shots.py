"""Render the director's pages against two scratch tables and save screenshots (for a person to look at; not a pass/fail test).

    PW=<path to @playwright/test> python3 table/tests/director_shots.py --out <folder> [--art-dir <the table's TABLE_ART_DIR>]

Shots: pov-names.png (a point of view, names only), pov-art.png (with art, if --art-dir), director-pip.png (the rotating page: a person's
seat with the hand hidden, the other table as picture-in-picture, a commentary caption), hand-hidden.png (after the panic key),
budget-paused.png, recap.png. Scratch tables only; rule bots in every seat; stub commentary (no model)."""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

from director_lib import begin, free_port, http, start_autopilot, start_table

from director import backends, config as cfg
from director.server import Director, serve

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--art-dir", default=None)
a = ap.parse_args()
OUT = Path(a.out); OUT.mkdir(parents=True, exist_ok=True)
PW = os.environ.get("PW") or str(Path.home() / "Documents/server/workspace/clients/tests/node_modules/@playwright/test")
TMP = Path(tempfile.mkdtemp(prefix="dir-shots-"))
JS = TMP / "shot.cjs"
JS.write_text("""const { chromium } = require(process.env.PW);
(async () => { const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  await p.goto(process.argv[2]); await p.waitForTimeout(+process.argv[4] || 2500); await p.screenshot({ path: process.argv[3] }); await b.close(); })()
  .catch(e => { console.error(String(e).slice(0, 300)); process.exit(1); });
""")


def shot(url, name, wait=2500):
    r = subprocess.run(["node", str(JS), url, str(OUT / name), str(wait)], env={**os.environ, "PW": PW}, capture_output=True, text=True, timeout=90)
    print(("saved " if r.returncode == 0 else "FAILED ") + str(OUT / name) + ("" if r.returncode == 0 else " " + r.stderr[:200]), flush=True)


(TMP / "t1").mkdir(); (TMP / "t2").mkdir()
procs = []
s1, B1, KF1, env1 = start_table(free_port(), TMP / "t1", "Michael", "Claude", seed=5)
s2, B2, KF2, env2 = start_table(free_port(), TMP / "t2", "Fusion", "Sam", seed=7)
procs += [s1, s2]
CONF = {"tables": [
    {"name": "Table 1", "url": B1, "seats": [{"name": "Michael", "kind": "human", "key_file": KF1["Michael"]},
                                             {"name": "Claude", "kind": "ai", "key_file": KF1["Claude"], "player": "rules autopilot"}]},
    {"name": "Table 2", "url": B2, "seats": [{"name": "Fusion", "kind": "ai", "key_file": KF2["Fusion"], "player": "Gemma 4 (local)"},
                                             {"name": "Sam", "kind": "ai", "key_file": KF2["Sam"], "player": "rules autopilot (stand-in)"}]}],
    "poll_secs": 1.0, "rotation": {"dwell_secs": 600, "recap_secs": 40}}
stub = backends.Stub(["Fusion is building a board here, and Sam is running out of answers.", "A bold line. Let us see if the attack is there next turn."])
voices = [{"name": "Sonnet", "role": "play-by-play", "model": "claude-sonnet-5-5"}, {"name": "Opus", "role": "colour", "model": "claude-opus-5-5"}]
TOKEN = secrets.token_urlsafe(16)
d = Director(cfg.parse(CONF), backend=stub, voices=voices)
d.com.min_gap, d.com.caption_secs = 5, 60
P = free_port()
srv, stop = serve(d, P, TOKEN)
D = f"http://127.0.0.1:{P}"
ctl = lambda b: http("POST", D + "/api/control", b, {"X-Director-Token": TOKEN})  # noqa: E731
try:
    begin(B1, "Michael", KF1["Michael"]); begin(B2, "Fusion", KF2["Fusion"])
    for base, kf, env, tmp in ((B1, KF1, env1, TMP / "t1"), (B2, KF2, env2, TMP / "t2")):
        for seat, f in kf.items():
            procs.append(start_autopilot(base, seat, f, env, tmp, grace="3"))
    time.sleep(40)
    shot(D + "/pov?seat=Fusion", "pov-names.png")
    if a.art_dir:
        d2 = Director(cfg.parse({**CONF, "art_dir": a.art_dir}), clients={"Table 1": d.watches[0].c, "Table 2": d.watches[1].c})
        P2 = free_port()
        srv2, stop2 = serve(d2, P2, TOKEN)
        time.sleep(3)
        shot(f"http://127.0.0.1:{P2}/pov?seat=Fusion", "pov-art.png", 5000)
        stop2.set(); srv2.shutdown()
    ctl({"cmd": "pin", "table": "Table 1", "seat": "Michael"})
    shot(D + "/director", "director-pip.png")
    ctl({"cmd": "pin", "table": "Table 1", "seat": "Claude"})
    shot(D + "/director", "director-claude-delayed.png")
    ctl({"cmd": "pin", "table": "Table 2", "seat": "Fusion"})
    ctl({"cmd": "hide"})
    shot(D + "/director", "hand-hidden.png")
    ctl({"cmd": "unhide"})
    d.com.budget.usd_per_stream = 0.0
    d.com.caption_secs = 0
    time.sleep(2)
    shot(D + "/director", "budget-paused.png")
    ctl({"cmd": "unpin"})
    t0 = time.time()
    while time.time() - t0 < 300 and d.program()["mode"] != "recap":
        time.sleep(0.3)
    print("mode:", d.program()["mode"], flush=True)
    shot(D + "/director", "recap.png", 1500)
finally:
    stop.set(); srv.shutdown()
    for p in procs:
        p.terminate()
