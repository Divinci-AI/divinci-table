"""Flying in combat (G3-12): a creature without flying or reach can't block a flier. In this set the fliers are stationed
Spacecraft (Class-F Shuttlecraft: "6+ | Flying, vigilance"), so: Claude stations a Shuttlecraft with a Horta, attacks with it the
next turn; Michael's Shuttle Ace (no flying) is not offered as a blocker, its block is refused, and "no block" still answers it.

    python3 table/tests/flying_block_test.py
"""
from __future__ import annotations

import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DECK = cards_path.decks_dir() / "sam.json"
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="fly-"))
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_NO_VOICE="1", TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code",
           REPLIES="template", NO_GEMMA="1", TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
srv = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external", "--fair-seed", "off",
                        "--seed", "2", "--token-file", str(TMP / "token"), "--ai", "Claude|", "--ai-deck", str(DECK), "--ai", "Michael|",
                        "--ai-deck", str(DECK), "--pilot", "Claude", "--pilot", "Michael"],
                       cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def call(method, path, body=None, key=None):
    h = {"Content-Type": "application/json"}
    if key:
        h["X-Seat-Key"] = key
    else:
        h["X-Brain-Token"] = (TMP / "token").read_text().strip()
    r = urllib.request.Request(B + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_for(pred, secs=20):
    end = time.time() + secs
    while time.time() < end:
        ph = call("GET", "/api/phase")[1]
        if pred(ph):
            return ph
        time.sleep(0.3)
    return call("GET", "/api/phase")[1]


def perm(seat, name):
    return next(p for p in call("GET", f"/api/brain/state?seat={seat}")[1]["permanents"] if p["name"] == name)


try:
    for _ in range(80):
        try:
            urllib.request.urlopen(B + "/api/phase", timeout=2); break
        except Exception:
            time.sleep(0.5)
    K = {n: call("POST", "/api/seat/claim", {"name": n})[1]["key"] for n in ("Claude", "Michael")}
    call("POST", "/api/phase/windows", {"on": False, "by": "host"})
    call("POST", "/api/phase/next", {"by": "Claude", "key": K["Claude"]}, key=K["Claude"])
    wait_for(lambda p: p.get("player") == "Claude" and p.get("step") == "main 1")
    for seat, card in (("Claude", "Class-F Shuttlecraft"), ("Claude", "Horta"), ("Michael", "Shuttle Ace")):   # host sets the scene
        call("POST", "/api/brain/search", {"seat": seat, "name": card}); call("POST", "/api/brain/put", {"seat": seat, "name": card})
    ship, horta = perm("Claude", "Class-F Shuttlecraft"), perm("Claude", "Horta")
    c, d = call("POST", "/api/brain/station", {"seat": "Claude", "ship": f"#{ship['id']}", "crew": f"#{horta['id']}"}, key=K["Claude"])
    check(c == 200 and perm("Claude", "Class-F Shuttlecraft")["charge"] >= 6, "the Shuttlecraft is stationed past 6 (a flier now)", f"{c} {d}")
    call("POST", "/api/brain/end", {"seat": "Claude"}, key=K["Claude"])
    wait_for(lambda p: p.get("player") == "Michael" and p.get("step") == "main 1")
    call("POST", "/api/brain/end", {"seat": "Michael"}, key=K["Michael"])
    wait_for(lambda p: p.get("player") == "Claude" and p.get("step") == "main 1")
    c, d = call("POST", "/api/brain/attack", {"seat": "Claude", "assign": {f"#{ship['id']}": "Michael"}}, key=K["Claude"])
    check(c == 200, "the stationed ship attacks", f"{c} {d.get('error')}")
    check(not perm("Claude", "Class-F Shuttlecraft")["tapped"], "vigilance: it attacked without tapping")
    acts = lambda seat: [a["action"] for a in call("GET", f"/api/seat/legal?seat={seat}", key=K[seat])[1].get("actions", [])]
    check("damage" not in acts("Claude"), "the attacker isn't offered damage before the defender has answered (the defender's window)",
          str(acts("Claude")))
    ace = perm("Michael", "Shuttle Ace")
    L = call("GET", "/api/seat/legal?seat=Michael", key=K["Michael"])[1]
    blocks = [a for a in L.get("actions", []) if a["action"] == "block"]
    check(blocks and not any(a["body"].get("blocker") == f"#{ace['id']}" for a in blocks) and any("blocker" not in a["body"] for a in blocks),
          "the legal list doesn't offer a non-flier as a blocker for a flier (only 'no block')", json.dumps(blocks)[:240])
    c, d = call("POST", "/api/brain/block", {"seat": "Michael", "attacker": "Class-F Shuttlecraft", "blocker": f"#{ace['id']}"}, key=K["Michael"])
    check(c == 400 and "flies" in str(d.get("error")), "a Shuttle Ace (no flying, no reach) can't block it", f"{c} {d.get('error')}")
    life0 = call("GET", "/api/life")[1]["Michael"]
    c, d = call("POST", "/api/brain/block", {"seat": "Michael", "attacker": "Class-F Shuttlecraft"}, key=K["Michael"])
    life1 = call("GET", "/api/life")[1]["Michael"]
    check(c == 200 and life1 == life0 - 2, "'no block' answers it: Michael takes the ship's 2", f"{c} {d.get('error')} life {life0}->{life1}")
    # v6c (M1): blocks are declarations and the damage is ONE step. Here nobody had anything to cast after the block, so the table has
    # dealt it (the 2 above): the attacker is offered the next step, not a second `damage`. (While the window after blocks is open the
    # list offers `damage`, the attacker's pass: tests/combat_v6_test.py.)
    check("damage" not in acts("Claude") and "next" in acts("Claude"), "once every attacker is answered and nobody can respond, the damage step is dealt: the attacker is offered Next",
          str(acts("Claude")))
    call("POST", "/api/brain/damage", {"seat": "Claude"}, key=K["Claude"])
    check("damage" not in acts("Claude"), "after dealing it, damage isn't offered again this turn (no loop)", str(acts("Claude")))
finally:
    srv.terminate()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
