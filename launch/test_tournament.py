"""Rounds, pairings and standings for a draft pod (retro M6, 2026-11-14).

    python3 launch/test_tournament.py     (no table is started: stand-in tables that answer GET /api/match, a stand-in round launcher)

  1. four players: round 1 in seating order; round 2 winners play winners; round 3 the pairings that are left; no pairing twice in the
     three rounds; a fourth round can only repeat, and says so;
  2. standings: match wins, then game wins, then fewer game losses, then seating order; a drawn match; places shared when level;
  3. an odd pod: the bye goes to the lowest player without one, counts as a 2-0 match win, and nobody has two byes before all have one;
  4. every pod of 2 to 8 players, random results, its planned rounds: never a repeated pairing, never a player twice in a round;
  5. results come from the table itself: only when the table seats exactly that pairing and its match is over; by hand for a table
     that is gone; the round cannot be paired on while a match has no result;
  6. the state file survives a reload; the public JSON has no port, path, key or link, and is rewritten at every change;
  7. `start` gives the round launcher --ports and the pairings (Name=bot for a player the autopilot plays); a launcher that fails
     leaves the round paired and not started; `run` plays three rounds end to end and stops by itself;
  8. `serve`: the public JSON on 127.0.0.1; a foreign Host name is refused.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import tournament as T  # noqa: E402

FAILS: list[str] = []
PY = sys.executable
TMP = Path(tempfile.mkdtemp(prefix="tourn-"))
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:500]}"), flush=True)
    if not ok:
        FAILS.append(what)


def pairs_of(r):
    return [frozenset((p["a"], p["b"])) for p in r["pairings"]]


def play(st, results):
    """Give the current round's pairings a result each: results maps a winner's name to the loser's game wins (default 0)."""
    for pr in T.current_round(st)["pairings"]:
        w = pr["a"] if pr["a"] in results else pr["b"]
        l = pr["b"] if w == pr["a"] else pr["a"]
        T.set_result(st, w, l, 2, results.get(w, 0))


class Table:
    """A stand-in table: GET /api/match answers whatever `.match` holds now."""
    def __init__(self):
        self.match = {"on": True, "seats": [], "over": False, "wins": {}, "games": [], "score": ""}
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps(me.match if self.path.startswith("/api/match") else {}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def seat(self, a, b):
        self.match = {"on": True, "seats": [a, b], "over": False, "wins": {a: 0, b: 0}, "games": [], "score": "No games won yet"}

    def finish(self, winner, loser_wins=0):
        a, b = self.match["seats"]
        loser = b if winner == a else a
        self.match = {**self.match, "over": True, "winner": winner, "wins": {winner: 2, loser: loser_wins}, "games": [{}] * (2 + loser_wins)}


print("1. Four players: three rounds are a round robin, winners play winners", flush=True)
st = T.new_state(["Michael", "Sam", "Claude", "Fusion"])
check(st["rounds_planned"] == 3, "four players: three rounds", st["rounds_planned"])
r1 = T.pair(st)
check(pairs_of(r1) == [frozenset(("Michael", "Sam")), frozenset(("Claude", "Fusion"))] and [p["port"] for p in r1["pairings"]] == [8800, 8801] and r1["bye"] is None,
      "round 1 pairs in seating order: Michael-Sam, Claude-Fusion", r1)
try:
    T.pair(st)
    check(False, "a round cannot be paired while the current one has a match without a result")
except ValueError as e:
    check("round 1 is not finished" in str(e), "a round cannot be paired while the current one has a match without a result", e)
play(st, {"Michael": 1, "Claude": 0})                  # Michael beats Sam 2-1, Claude beats Fusion 2-0
r2 = T.pair(st)
check(frozenset(("Michael", "Claude")) in pairs_of(r2) and frozenset(("Sam", "Fusion")) in pairs_of(r2), "round 2: the winners play each other, and so do the losers", r2)
check(r2["pairings"][0]["a"] == "Claude" and r2["pairings"][0]["b"] == "Michael", "…the leader's table first (Claude won 2-0, Michael 2-1)", r2["pairings"][0])
play(st, {"Michael": 0, "Sam": 0})
r3 = T.pair(st)
check(set(pairs_of(r3)) == {frozenset(("Michael", "Fusion")), frozenset(("Sam", "Claude"))}, "round 3: the two pairings that are left", r3)
allp = pairs_of(r1) + pairs_of(r2) + pairs_of(r3)
check(len(set(allp)) == 6 and not any(p.get("repeat") for r in st["rounds"] for p in r["pairings"]), "no pairing twice: three rounds are everyone against everyone")
play(st, {"Michael": 0, "Claude": 1})
check(T.finished(st), "after the planned rounds the tournament is finished")
try:
    T.pair(st)
    check(False, "no fourth round unless asked")
except ValueError as e:
    check("all 3 rounds are played" in str(e), "no fourth round unless asked", e)
st["rounds_planned"] = 4
r4 = T.pair(st)
check(all(p.get("repeat") for p in r4["pairings"]) and "a repeat" in T.round_text(r4), "a fourth round can only repeat pairings, and says so", r4)

print("2. Standings", flush=True)
st["rounds"].pop()
rows = T.standings(st)
check([x["name"] for x in rows] == ["Michael", "Claude", "Sam", "Fusion"], "match wins first: Michael 3-0, Claude 2-1, Sam 1-2, Fusion 0-3", [(x["name"], x["record"]) for x in rows])
check([x["record"] for x in rows] == ["3-0", "2-1", "1-2", "0-3"] and rows[0]["game_wins"] == 6 and rows[0]["game_losses"] == 1, "records and game counts", rows[0])
s2 = T.new_state(["A", "B", "C", "D"])
T.pair(s2)
T.set_result(s2, "A", "B", 2, 1); T.set_result(s2, "C", "D", 2, 0)
rows = T.standings(s2)
check([x["name"] for x in rows] == ["C", "A", "B", "D"], "level on match wins and game wins: fewer game losses is higher (C won 2-0, A won 2-1)", [(x["name"], x["game_wins"], x["game_losses"]) for x in rows])
s3 = T.new_state(["A", "B", "C", "D"])
T.pair(s3)
T.set_result(s3, "A", "B", 1, 1); T.set_result(s3, "D", "C", 2, 0)
rows = T.standings(s3)
check([x["name"] for x in rows] == ["D", "A", "B", "C"] and rows[1]["record"] == "0-0-1" and rows[1]["place"] == rows[2]["place"] == 2 and rows[3]["place"] == 4,
      "a drawn match is a draw for both; players level on everything share a place and keep seating order", [(x["place"], x["name"], x["record"]) for x in rows])
s4 = T.new_state(["A", "B", "C", "D"])
T.pair(s4); T.set_result(s4, "A", "B", 2, 0); T.set_result(s4, "C", "D", 2, 1)
T.pair(s4); play(s4, {"C": 0, "D": 0})               # C beats A 2-0, D beats B 2-0
rows = {x["name"]: x for x in T.standings(s4)}
check([x["name"] for x in T.standings(s4)] == ["C", "D", "A", "B"] and rows["D"]["game_wins"] == 3 and rows["A"]["game_wins"] == 2,
      "match wins, THEN game wins: D (1-1, 3 games) is above A (1-1, 2 games)", [(x["name"], x["record"], x["game_wins"]) for x in T.standings(s4)])

print("3. An odd pod: the bye", flush=True)
s5 = T.new_state(["A", "B", "C", "D", "E"], rounds=5)
byes = []
for n in range(5):
    r = T.pair(s5)
    byes.append(r["bye"])
    if n == 0:
        check(r["bye"] == "E" and len(r["pairings"]) == 2, "round 1: the last seat has the bye", r)
        check(T.standings({**s5})[0]["name"] == "E" or next(x for x in T.standings(s5) if x["name"] == "E")["record"] == "1-0", "a bye is a match win")
    play(s5, {p["a"]: 0 for p in r["pairings"]})
check(sorted(byes) == ["A", "B", "C", "D", "E"], "in five rounds everybody has exactly one bye", byes)
e = next(x for x in T.standings(s5) if x["name"] == "E")
check(e["byes"] == 1 and e["game_wins"] >= 2, "…worth 2 games to 0", e)
check(len({p for r in s5["rounds"] for p in pairs_of(r)}) == 10, "five players, five rounds: all ten pairings, none twice")

print("4. Every pod from 2 to 8, random results: no repeats in the planned rounds", flush=True)
rng = random.Random(20261114)
bad = []
for n in range(2, 9):
    for trial in range(40):
        s = T.new_state([f"P{i}" for i in range(n)])
        seen = set()
        for _ in range(s["rounds_planned"]):
            r = T.pair(s)
            names = [x for p in r["pairings"] for x in (p["a"], p["b"])] + ([r["bye"]] if r["bye"] else [])
            if sorted(names) != sorted(s["players"]) or any(p in seen for p in pairs_of(r)):
                bad.append((n, trial, r))
            seen.update(pairs_of(r))
            for pr in r["pairings"]:
                wa = rng.choice([(2, 0), (2, 1), (1, 2), (0, 2), (1, 1)])
                T.set_result(s, pr["a"], pr["b"], *wa)
check(not bad, "280 random tournaments: every player once per round, never a repeated pairing", bad[:1])
check([T.new_state([f"P{i}" for i in range(n)])["rounds_planned"] for n in range(2, 9)] == [1, 3, 3, 3, 3, 3, 3], "rounds planned: 1, 3, 3 (round robin up to four), then ceil(log2 n)")

print("5. Results come from the table", flush=True)
ta, tb = Table(), Table()
state = TMP / "tournament.json"
env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TOURNAMENT_POLL": "0.3"}


def cli(*args, **extra):
    r = subprocess.run([PY, str(HERE / "tournament.py"), *map(str, args)], capture_output=True, text=True, env={**env, **extra}, timeout=120)
    return r.returncode, r.stdout + r.stderr


c, out = cli("new", "--state", state, "--players", "Michael,Sam,Claude,Fusion", "--bots", "sam", "--name", "Test pod")
check(c == 0 and state.stat().st_mode & 0o777 == 0o600 and T.public_path(state).exists(), "new: a 0600 state file and the public JSON beside it", (c, out))
check(cli("new", "--state", state, "--players", "A,B")[0] == 1, "new refuses to overwrite a tournament in progress")
check(cli("new", "--state", TMP / "x.json", "--players", "A,a")[0] == 1 and cli("new", "--state", TMP / "x.json", "--players", "A,B;rm")[0] == 1, "bad player names are refused")
c, out = cli("pair", "--state", state, "--ports", f"{ta.port},{tb.port}")
check(c == 0 and f"launcher: --ports {ta.port},{tb.port} Michael:Sam=bot Claude:Fusion" in out, "pair prints what the round launcher is given (Sam=bot: the autopilot plays his seat)", out)
ta.seat("Michael", "Sam"); tb.seat("Fusion", "Claude")
c, out = cli("record", "--state", state)
check(c == 0 and "round 1 still playing: Michael vs Sam, Claude vs Fusion" in out, "record with both matches still being played: nothing taken", out)
tb.finish("Fusion", 1)
c, out = cli("record", "--state", state)
st = T.load(state)
check("Claude vs Fusion: Fusion wins 2-1" in out and st["rounds"][0]["pairings"][1]["result"]["wins"] == {"Claude": 1, "Fusion": 2} and st["rounds"][0]["pairings"][1]["result"]["source"] == "table"
      and st["rounds"][0]["pairings"][0]["result"] is None, "a finished match is taken from its table (whatever order the table seats them in); the other is not", out)
ta.seat("Ann", "Ben"); ta.finish("Ann")
c, out = cli("record", "--state", state)
check(f"table :{ta.port} is not Michael vs Sam (it seats Ann and Ben): no result taken from it" in out and T.load(state)["rounds"][0]["pairings"][0]["result"] is None,
      "a table that seats OTHER players gives no result, whatever it says", out)
check(cli("pair", "--state", state)[0] == 1, "the next round cannot be paired while a match has no result")
c, out = cli("record", "--state", state, "--result", "sam:michael=2-0")
st = T.load(state)
check(c == 0 and st["rounds"][0]["pairings"][0]["result"]["winner"] == "Sam" and st["rounds"][0]["pairings"][0]["result"]["source"] == "hand" and "ROUND 1 DONE" in out,
      "a result by hand (the table is gone), names in any case and order", out)
check(cli("record", "--state", state, "--result", "Michael:Claude=2-0")[0] == 1 and cli("record", "--state", state, "--result", "nonsense")[0] == 1,
      "a result for a pairing that is not in the round, or nonsense, is refused")

print("6. The state file and the public JSON", flush=True)
pub = json.loads(T.public_path(state).read_text())
check(pub["name"] == "Test pod" and pub["round"] == 1 and pub["round_done"] is True and [x["name"] for x in pub["standings"]][:2] == ["Sam", "Fusion"]
      and pub["rounds"][0]["pairings"][0] == {"a": "Michael", "b": "Sam", "done": True, "winner": "Sam", "draw": False, "wins": {"Michael": 0, "Sam": 2}, "score": "0-2"}
      and {"name": "Sam", "bot": True} in pub["players"], "the public JSON: the round, each pairing's score, the standings, who is on autopilot", pub)
flat = json.dumps(pub)
check(not any(w in flat for w in ("port", str(ta.port), str(TMP), "key", "token", "link", "http")), "…and no port, path, key, token or link in it", flat[:300])
check(T.public_path(state).stat().st_mode & 0o777 == 0o644, "the public JSON is readable (0644); the state is not")
c, out = cli("standings", "--state", state)
check(c == 0 and out.splitlines()[0].startswith("1. Sam") and "(autopilot)" in out.splitlines()[0], "standings as text", out)
c, out = cli("bot", "--state", state, "--player", "sam", "off")
check(c == 0 and T.load(state)["bots"] == [] and not json.loads(T.public_path(state).read_text())["players"][1]["bot"], "bot off: Sam plays in person again (and the public JSON follows)", out)
cli("bot", "--state", state, "--player", "Fusion", "on")

print("7. start and run: the round launcher is told what to start", flush=True)
calls = TMP / "launcher-calls.txt"
stub = TMP / "play_stub.sh"
stub.write_text(f'#!/bin/bash\necho "$*" >> "{calls}"\nexit "${{STUB_RC:-0}}"\n')
stub.chmod(0o755)
c, out = cli("start", "--state", state, "--ports", f"{ta.port},{tb.port}", "--", "--single", PLAY_ROUND=str(stub), STUB_RC="1")
st = T.load(state)
check(c == 1 and len(st["rounds"]) == 2 and st["rounds"][1]["started"] is None and "paired but NOT started" in out, "a launcher that fails: the round is paired, NOT started, and it says so", (c, out))
c, out = cli("start", "--state", state, PLAY_ROUND=str(stub))
st = T.load(state)
r2 = st["rounds"][1]
check(c == 0 and len(st["rounds"]) == 2 and r2["started"], "start again: the same round (not a new one) is started", (c, out))
want = f"--ports {ta.port},{tb.port} " + " ".join(f"{p['a']}{'=bot' if p['a'] == 'Fusion' else ''}:{p['b']}{'=bot' if p['b'] == 'Fusion' else ''}" for p in r2["pairings"])
lines = calls.read_text().splitlines()
check(lines[0] == "--single " + want and lines[1] == want, "the launcher got its own options, --ports, and the pairings (Fusion=bot)", lines)
check({frozenset((p["a"], p["b"])) for p in r2["pairings"]} == {frozenset(("Sam", "Fusion")), frozenset(("Michael", "Claude"))}, "round 2 pairs the winners (Sam, Fusion) and the losers", r2)
# `run`: the rest of the tournament end to end. The stand-in tables are seated and finished by this test as each round starts.
by_port = {ta.port: ta, tb.port: tb}


def referee():
    done = set()
    end = time.time() + 60
    while time.time() < end:
        try:
            s = T.load(state)
        except (OSError, ValueError):
            time.sleep(0.1)
            continue
        r = T.current_round(s)
        if r and r.get("started") and r["n"] not in done:
            for pr in r["pairings"]:
                by_port[pr["port"]].seat(pr["a"], pr["b"])
            time.sleep(0.5)
            for pr in r["pairings"]:
                by_port[pr["port"]].finish(pr["a"], 1 if r["n"] == 3 else 0)
            done.add(r["n"])
            if r["n"] == 3:
                return
        time.sleep(0.1)


th = threading.Thread(target=referee, daemon=True)
th.start()
c, out = cli("run", "--state", state, "--max-secs", "40", PLAY_ROUND=str(stub))
th.join(timeout=5)
st = T.load(state)
check(c == 0 and "ROUND 2 DONE" in out and "ROUND 3 DONE" in out and out.rstrip().count("TOURNAMENT DONE") == 1 and T.finished(st), "run: rounds 2 and 3 are played and it stops by itself", (c, out[-700:]))
check(len(calls.read_text().splitlines()) == 3 and len({p for r in st["rounds"] for p in pairs_of(r)}) == 6,
      "run did not start round 2 a second time (it was already started), and started round 3 once, on the same ports; six different pairings in all", calls.read_text())
check(all(p["result"]["source"] == "table" for p in st["rounds"][2]["pairings"]) and out.index("ROUND 2 DONE") < out.index("round 3:") < out.index("ROUND 3 DONE"),
      "each round is recorded from its tables before the next is paired")
c, out = cli("run", "--state", state, PLAY_ROUND=str(stub))
check(c == 0 and out.startswith("TOURNAMENT DONE") and len(calls.read_text().splitlines()) == 3, "run on a finished tournament: the final standings, nothing started", out)

print("8. serve", flush=True)
srv = ThreadingHTTPServer(("127.0.0.1", 0), T.make_handler(state))
threading.Thread(target=srv.serve_forever, daemon=True).start()
u = f"http://127.0.0.1:{srv.server_address[1]}"
with LOCAL.open(u + "/tournament.json", timeout=5) as x:
    d, hdr = json.loads(x.read()), x.headers
check(d["finished"] is True and d["standings"][0]["place"] == 1 and hdr.get("Access-Control-Allow-Origin") == "*" and d == {**T.public(T.load(state)), "updated": d["updated"]},
      "GET /tournament.json: the public JSON, readable by an overlay page on another local port")
try:
    LOCAL.open(urllib.request.Request(u + "/tournament.json", headers={"Host": "evil.example"}), timeout=5)
    check(False, "a foreign Host name is refused")
except urllib.error.HTTPError as e:
    check(e.code == 403, "a foreign Host name is refused", e.code)
try:
    LOCAL.open(u + "/../tournament.json.tmp", timeout=5)
    check(False, "nothing else is served")
except urllib.error.HTTPError as e:
    check(e.code == 404, "nothing else is served", e.code)
for s in (srv, ta.srv, tb.srv):
    s.shutdown()

print("\nFAILED: " + "; ".join(FAILS) if FAILS else "\nall good")
sys.exit(1 if FAILS else 0)
