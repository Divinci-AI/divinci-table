"""Two-Headed Giant, step 1: teammates share one life total (30). Later steps (shared team turn, team combat, the first-draw
rule) add their checks here.

    python3 table/tests/two_headed_test.py

Part A drives the engine; Part B starts a throwaway four-seat table on a free port with --team and checks the shared total
through the server (its life action and its life table). Needs test decks: built from the card data, or TABLE_TEST_DECKS=<dir>."""
from __future__ import annotations

import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
cards_path.need_card_data()                            # the table needs the card data, and a test never downloads it (skips without it)
import pickle
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import player  # noqa: E402

DECKS = cards_path.decks_dir()
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


print("A. the engine")
a = player.VirtualPlayer(str(DECKS / "claude.json"), "A1", seed=1)
b = player.VirtualPlayer(str(DECKS / "sam.json"), "A2", seed=2)
c = player.VirtualPlayer(str(DECKS / "michael.json"), "B1", seed=3)
check(a.life == 20 and b.life == 20, "a 40-card deck still starts at 20 on its own", f"{a.life} {b.life}")
a.share_life([b], 30)
check(a.life == b.life == 30, "teammates start at 30 together", f"{a.life} {b.life}")
a.life -= 4
check(b.life == 26, "damage to one teammate is damage to the team", f"b={b.life}")
b.life += 3
check(a.life == 29, "life gained by one teammate is the team's", f"a={a.life}")
check(c.life == 20, "the other team's player is untouched", f"c={c.life}")
a2, b2 = pickle.loads(pickle.dumps([a, b]))
a2.life -= 1
check(a2.life == b2.life == 28, "a saved and restored game keeps the shared total shared", f"{a2.life} {b2.life}")
old = player.VirtualPlayer.__new__(player.VirtualPlayer)
old.__dict__["life"] = 17                      # a game pickled before life moved into a box
check(old.life == 17, "a game saved before the change still reads its life", str(old.life))

print("B. the server (four seats, two teams)")
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="2hg-"))
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", NO_GEMMA="1",
           TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
cmd = [sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external", "--fair-seed", "off",
       "--token-file", str(TMP / "token"), "--seed", "7"]
for n, d in (("Michael", "sam.json"), ("Claude", "claude.json"), ("Sam", "sam.json"), ("Fusion", "michael.json")):
    cmd += ["--ai", f"{n}|", "--ai-deck", str(DECKS / d)]
cmd += ["--team", "Feds:Michael,Claude", "--team", "Klingons:Sam,Fusion"]
cmd += ["--pilot", "Michael", "--pilot", "Claude", "--pilot", "Sam", "--pilot", "Fusion"]
env["TABLE_HIGHROLL"] = "first"
proc = subprocess.Popen(cmd, cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def call(method, path, body=None, key=None):
    h = {"Content-Type": "application/json"}
    if key:
        h.update({"X-Seat-Key": key, "X-Forwarded-For": "203.0.113.9", "User-Agent": "test-" + key[:4]})
    else:
        h["X-Brain-Token"] = (TMP / "token").read_text().strip()
    r = urllib.request.Request(B + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


try:
    for _ in range(80):
        try:
            urllib.request.urlopen(B + "/api/phase", timeout=2); break
        except Exception:
            time.sleep(0.5)
    life = call("GET", "/api/life")[1]
    check(all(life.get(n) == 30 for n in ("Michael", "Claude", "Sam", "Fusion")), "every seat shows its team's 30", str(life))
    ph = call("GET", "/api/phase")[1]
    check(ph.get("teams") == {"Michael": "Feds", "Claude": "Feds", "Sam": "Klingons", "Fusion": "Klingons"},
          "the phase reply names the teams", str(ph.get("teams")))
    code, d = call("POST", "/api/brain/life", {"seat": "Claude", "delta": -5, "text": "test: 5 damage"})
    life = call("GET", "/api/life")[1]
    check(code == 200 and life.get("Claude") == life.get("Michael") == 25 and life.get("Sam") == 30,
          "a life change on one seat moves its teammate and not the other team", f"{code} {d.get('error')} {life}")
    print("C. the shared team turn (step 2)")
    K = {n: call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in ("Michael", "Claude", "Sam", "Fusion")}
    call("POST", "/api/phase/windows", {"on": False, "by": "host"})
    call("POST", "/api/phase/next", {"by": "Michael", "key": K["Michael"]}, key=K["Michael"])

    def on_turn_of(p, seat_):
        t = p.get("teams") or {}
        return p.get("player") == seat_ or (t.get(seat_) and t.get(seat_) == t.get(p.get("player")))

    def wait_for(pred, secs=20):
        end = time.time() + secs
        while time.time() < end:
            ph = call("GET", "/api/phase")[1]
            if pred(ph):
                return ph
            time.sleep(0.3)
        return call("GET", "/api/phase")[1]

    ph = wait_for(lambda p: p.get("player") == "Michael" and p.get("step") == "main 1")
    check(ph.get("player") == "Michael" and ph.get("step") == "main 1", "the Feds' turn begins (Michael leads)", f"{ph.get('player')} {ph.get('step')}")
    time.sleep(2.5)                                    # the auto-begin loop begins Claude's half too
    hands = {n: len(call("GET", f"/api/brain/state?seat={n}", key=K[n])[1].get("hand") or []) for n in ("Michael", "Claude")}
    check(hands == {"Michael": 7, "Claude": 7}, "step 4: the team on the play skips its first draw, BOTH its players", str(hands))

    def land(seat):
        st = call("GET", f"/api/brain/state?seat={seat}", key=K[seat])[1]
        nm = next((h["name"] for h in st.get("hand") or [] if h.get("land")), None)
        return call("POST", "/api/brain/land", {"seat": seat, "name": nm}, key=K[seat]) if nm else (0, {"error": "no land in hand"})

    c1, d1 = land("Claude")
    check(c1 == 200, "a teammate plays a land in its team's turn", f"{c1} {d1.get('error')}")
    c2, d2 = land("Sam")
    check(c2 == 409, "the other team can't play a land in the Feds' turn", f"{c2} {d2.get('error')}")
    call("POST", "/api/brain/end", {"seat": "Michael"}, key=K["Michael"])
    time.sleep(1.5)
    ph = call("GET", "/api/phase")[1]
    check(ph.get("player") == "Michael" and ph.get("step") == "main 1",
          "one teammate ending neither ends the team's turn nor moves its step on (the partner still gets its main phase)",
          f"{ph.get('player')} {ph.get('step')}")
    call("POST", "/api/brain/end", {"seat": "Claude"}, key=K["Claude"])
    ph = wait_for(lambda p: p.get("player") not in ("Michael", None, "Claude"), 10)
    check(ph.get("player") == "Sam", "when both have ended, the turn goes to the other team (Sam), never to the teammate",
          f"{ph.get('player')} {ph.get('step')}")

    wait_for(lambda p: p.get("player") == "Sam" and p.get("step") == "main 1", 15)
    time.sleep(2.5)
    hands = {n: len(call("GET", f"/api/brain/state?seat={n}", key=K[n])[1].get("hand") or []) for n in ("Sam", "Fusion")}
    check(hands == {"Sam": 8, "Fusion": 8}, "step 4: the team on the draw draws on its first turn, both players", str(hands))
    print("D. team combat (step 3)")
    for seat_, card in (("Sam", "Tactical Officer"), ("Claude", "Obsidian Order Operative")):     # host sets the scene
        call("POST", "/api/brain/search", {"seat": seat_, "name": card}); call("POST", "/api/brain/put", {"seat": seat_, "name": card})
    for seat_ in ("Sam", "Fusion", "Michael", "Claude"):                                     # round the table back to Sam
        wait_for(lambda p: on_turn_of(p, seat_), 15)
        time.sleep(1.5)
        call("POST", "/api/brain/end", {"seat": seat_}, key=K[seat_])
    ph = wait_for(lambda p: p.get("player") == "Sam" and p.get("step") == "main 1", 20)
    time.sleep(1.5)
    ace = next(p for p in call("GET", "/api/brain/state?seat=Sam", key=K["Sam"])[1]["permanents"] if p["name"] == "Tactical Officer")
    c5, d5 = call("POST", "/api/brain/attack", {"seat": "Sam", "assign": {f"#{ace['id']}": "Fusion"}}, key=K["Sam"])
    check(c5 != 200 and "your team" in str(d5.get("error")), "attacking your own teammate is refused", f"{c5} {d5.get('error')}")
    c6, d6 = call("POST", "/api/brain/attack", {"seat": "Sam", "assign": {f"#{ace['id']}": "Feds"}}, key=K["Sam"])
    check(c6 == 200, "an attack can name the other team", f"{c6} {d6.get('error')} {ph.get('player')} {ph.get('step')}")
    inc = call("GET", "/api/brain/state?seat=Claude", key=K["Claude"])[1].get("incoming") or []
    check(any(e.get("creature") == "Tactical Officer" for e in inc), "the attacked team's OTHER player sees the attacker too", str(inc))
    borg = next(p for p in call("GET", "/api/brain/state?seat=Claude", key=K["Claude"])[1]["permanents"] if p["name"] == "Obsidian Order Operative")
    life0 = call("GET", "/api/life")[1]
    c7, d7 = call("POST", "/api/brain/block", {"seat": "Claude", "attacker": "Tactical Officer", "blocker": f"#{borg['id']}"}, key=K["Claude"])
    life1 = call("GET", "/api/life")[1]
    check(c7 == 200 and life1.get("Michael") == life0.get("Michael"),
          "either defender may block: Claude blocks the attack aimed at Michael, and the team takes no damage",
          f"{c7} {d7.get('error')} {d7.get('said')} life {life0} -> {life1}")

    call("POST", "/api/brain/damage", {"seat": "Sam"}, key=K["Sam"])
    call("POST", "/api/brain/end", {"seat": "Sam"}, key=K["Sam"])
    time.sleep(1.0)
    ph = call("GET", "/api/phase")[1]
    check(ph.get("player") == "Sam" and ph.get("step") == "main 2",
          "a teammate done after combat moves the shared turn to main 2, so its partner still gets a main phase (4-bot deadlock)",
          f"{ph.get('player')} {ph.get('step')}")
    call("POST", "/api/brain/end", {"seat": "Fusion"}, key=K["Fusion"])
    ph = wait_for(lambda p: p.get("player") == "Michael", 10)
    check(ph.get("player") == "Michael", "the partner's end then hands the turn to the other team", f"{ph.get('player')} {ph.get('step')}")

    print("E. game over")
    call("POST", "/api/brain/life", {"seat": "Sam", "delta": -40, "text": "test: the Klingons fall"})
    ph = call("GET", "/api/phase")[1]
    check(ph.get("game_over") and ph.get("winner") in ("Michael", "Claude") and set(ph.get("finish", [])[:2]) == {"Michael", "Claude"},
          "a team at 0 ends a virtual-only table: the other team wins, both its players first in the finish order",
          f"{ph.get('game_over')} {ph.get('winner')} {ph.get('finish')}")
    c8, _ = call("POST", "/api/brain/end", {"seat": "Michael"}, key=K["Michael"])
    call("POST", "/api/brain/end", {"seat": "Claude"}, key=K["Claude"])
    time.sleep(1.5)
    ph2 = call("GET", "/api/phase")[1]
    check(ph2.get("player") == ph.get("player"), "a finished game hands out no more turns", f"{ph.get('player')} -> {ph2.get('player')}")

    bad = subprocess.run([sys.executable, str(HERE / "server.py"), "--any-card", "--port", "0", "--brain", "external", "--token-file", str(TMP / "t2"),
                          "--ai", "X|", "--ai-deck", str(DECKS / "claude.json"), "--team", "T:X,Nobody"],
                         cwd=HERE.parent, env=env, capture_output=True, text=True, timeout=60)
    check(bad.returncode != 0 and "team" in (bad.stderr + bad.stdout).lower(), "a team naming an unknown seat is refused at start",
          (bad.stderr or bad.stdout)[-200:])
finally:
    proc.terminate()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
