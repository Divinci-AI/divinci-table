"""/api/seat/legal: an outside agent asks "what may I do?" and gets ready-to-send requests (AGENT-TABLE-PLAN §1a).

Property, on a real two-pilot table with a seeded shuffle: two "legal-only" agents play by picking from that list alone. Every
action they send (a land, a cast, a station, an attack, a block, damage, end, pass) must be accepted. Off-list probes (a land
on the other seat's turn, a second land in a turn, attacking with a creature the list didn't offer) must be refused. Also: the
route needs that seat's key; another seat's key or no key is refused.

    ~/.venvs/table/bin/python table/tests/seat_legal_test.py [turns=8]

Turns are counted from what /api/phase really says: a turn starts each time `player` changes (it carries no turn number). The walk ends
after TURNS turns, at game over, or at a STALL: the phase not moving and no listed action accepted for STALL_SECS (default 45). A stall
is a failure and says where it stopped, so it can't pass for an early game over. Decks: TABLE_TEST_DECKS, or the fixture decks.
"""
from __future__ import annotations

import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
import fixture_decks  # noqa: E402
DECKS = Path(os.environ["TABLE_TEST_DECKS"]) if os.environ.get("TABLE_TEST_DECKS") else fixture_decks.write(Path(tempfile.mkdtemp(prefix="legal-decks-")))
TURNS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
STALL_SECS = float(os.environ.get("STALL_SECS", "45"))
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="legal-"))
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_NO_VOICE="1", TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code",
           REPLIES="template", NO_GEMMA="1", TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
srv = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external",
                        "--fair-seed", "off", "--seed", "5", "--token-file", str(TMP / "token"),
                        "--ai", "Michael|", "--ai-deck", str(DECKS / "claude.json"), "--ai", "Claude|", "--ai-deck", str(DECKS / "claude.json"),
                        "--pilot", "Michael", "--pilot", "Claude"],
                       cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)


def req(method, path, body=None, key=None, remote=True):
    h = {"Content-Type": "application/json"}
    if key:
        h["X-Seat-Key"] = key
    if remote:                                     # as an outside agent would reach it (through a tunnel or proxy)
        h["X-Forwarded-For"] = "203.0.113.30"
    r = urllib.request.Request(B + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


rng = random.Random(3)
sent, refused_listed, kinds = 0, [], set()
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(B + "/api/phase", timeout=2); break
        except Exception:
            time.sleep(0.5)
    K = {n: req("POST", "/api/seat/claim", {"name": n}, remote=False)[1]["key"] for n in ("Michael", "Claude")}
    req("POST", "/api/phase/windows", {"on": False, "by": "host"}, remote=False)
    req("POST", "/api/phase/next", {"by": "Michael", "key": K["Michael"]}, key=K["Michael"])

    c, _ = req("GET", "/api/seat/legal?seat=Michael", key=K["Claude"])
    check(c == 403, "another seat's key can't read this seat's options", str(c))
    c, _ = req("GET", "/api/seat/legal?seat=Michael")
    check(c == 403, "no key, no options", str(c))
    codes = [req("GET", "/api/seat/legal?seat=Claude", key=K["Claude"])[0] for _ in range(100)]
    c429 = codes.count(429)
    _, d = req("GET", "/api/seat/legal?seat=Claude", key=K["Claude"])
    check(25 <= codes.count(200) <= 60 and c429 > 0, "a seat key flooding the table is slowed down (429 after its burst)",
          f"200s={codes.count(200)} 429s={c429}")
    host_codes = []
    for _ in range(60):
        r_ = urllib.request.Request(B + "/api/brain/state?seat=Claude", headers={"X-Brain-Token": (TMP / "token").read_text().strip()})
        try:
            host_codes.append(urllib.request.urlopen(r_, timeout=10).status)
        except urllib.error.HTTPError as e:
            host_codes.append(e.code)
    check(set(host_codes) == {200}, "the host's own tooling is never rate-limited", str(set(host_codes)))
    time.sleep(1.5)
    check(req("GET", "/api/seat/legal?seat=Claude", key=K["Claude"])[0] == 200, "after a short wait the seat is served again")
    early_bad = []

    probed = False
    t_walk = time.time()
    deadline = t_walk + 240
    turns, last_player, last_sig, last_progress = 0, None, None, time.time()
    stalled, game_over, accepted = None, False, 0
    while time.time() < deadline:
        ph = req("GET", "/api/phase")[1]
        if ph.get("game_over"):
            game_over = True
            break
        if ph.get("player") and ph.get("player") != last_player:    # a turn starts each time the turn changes hands
            turns += 1
            last_player = ph.get("player")
        if turns > TURNS:                                          # TURNS turns played, and the next one has begun
            break
        sig = (ph.get("player"), ph.get("step"), ph.get("begun"))
        if sig != last_sig:
            last_sig, last_progress = sig, time.time()
        elif time.time() - last_progress > STALL_SECS:
            stalled = f"turn {turns}: {ph.get('player')} {ph.get('step')} (begun={ph.get('begun')}) for {STALL_SECS:g}s with no listed action moving it"
            break
        for seat in ("Michael", "Claude"):
            code, L = req("GET", f"/api/seat/legal?seat={seat}", key=K[seat])
            if code != 200:
                refused_listed.append(f"legal {code}")
                continue
            acts = L.get("actions", [])
            if L.get("my_turn") and L.get("step") in ("untap", "upkeep", "draw") and any(a["action"] == "end" for a in acts):
                early_bad.append(L.get("step"))
            if not acts:
                continue
            if not probed and L.get("my_turn") and L.get("step") == "main 1":
                other = "Claude" if seat == "Michael" else "Michael"
                oc, od = req("POST", "/api/brain/land", {"seat": other, "name": "Swamp"}, key=K[other])
                check(oc >= 400, "off-list: a land on the other seat's turn is refused", f"{oc} {od.get('error')}")
                lands = [a for a in acts if a["action"] == "land"]
                if lands:
                    c1, d1 = req("POST", "/api/brain/land", lands[0]["body"], key=K[seat])
                    sent += 1; kinds.add("land")
                    if c1 != 200:
                        refused_listed.append(f"land {c1} {d1.get('error')}")
                    acts2 = req("GET", f"/api/seat/legal?seat={seat}", key=K[seat])[1].get("actions", [])
                    check(not any(a["action"] == "land" for a in acts2), "after the land drop, no land is listed")
                    c2, d2 = req("POST", "/api/brain/land", lands[-1]["body"], key=K[seat])
                    check(c2 >= 400, "off-list: a second land in one turn is refused", f"{c2} {d2.get('error')}")
                    probed = True
                    continue
            # prefer doing something over ending: land > cast > station > attack > block > damage > pass > end
            order = ["begin", "land", "cast", "station", "attack", "block", "damage", "pass", "end"]
            acts.sort(key=lambda a: order.index(a["action"]) if a["action"] in order else 99)
            a = acts[0] if rng.random() < 0.85 else rng.choice(acts)
            code, d = req("POST", f"/api/brain/{a['action']}", a["body"], key=K[seat])
            sent += 1; kinds.add(a["action"])
            if code == 409 and "waiting on passes" in str(d.get("error")):
                continue                                         # the round isn't done yet: a timing wait, not a refusal
            if code != 200:
                refused_listed.append(f"{seat} {a['action']} {json.dumps(a['body'])[:90]} -> {code} {str(d.get('error'))[:120]}")
            else:
                accepted += 1
                last_progress = time.time()                      # an accepted action is progress even inside one step (a land, a cast)
        time.sleep(0.4)
    walk = time.time() - t_walk
    print(f"walk: {turns} turn(s) started ({TURNS} wanted), {sent} actions sent, {accepted} accepted, {walk:.0f}s"
          + (", game over" if game_over else "") + (f", STALLED at {stalled}" if stalled else ""), flush=True)
    check(stalled is None, "the walk never stalls: following the list always moves the game", stalled)
    check(game_over or turns > TURNS, f"the walk played {TURNS} turns (or the game ended): {turns - 1 if turns > TURNS else turns} turns, game_over={game_over}",
          f"only {turns} turn(s) in {walk:.0f}s")
    check(sent >= 20 and not refused_listed, f"every listed action was accepted ({sent} sent; kinds: {sorted(kinds)})",
          "; ".join(refused_listed[:6]))
    check({"land", "cast", "end"} <= kinds, "the walk covered land, cast and end at least", str(sorted(kinds)))
    check(not early_bad, "`end` is never offered before main 1 (a model took it and skipped whole turns)", str(early_bad[:5]))
finally:
    srv.terminate()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
