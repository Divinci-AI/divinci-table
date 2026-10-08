"""In-game journals (docs/RESEARCH-ENGINE-GOAL.md R1): the module, then the real server.

Part 1 tests table/journal.py alone, and proves the tests can fail by breaking the module three ways.
Part 2 starts its own server (port 8816, its own token file and research folder; a game on :8800 is left alone)
with one external-brain AI seat and two people, then checks the endpoints: due events at the fixed moments,
refusals, the seal until /api/game-over, declines, and the reset.

  ~/.venvs/table/bin/python table/tests/journal_test.py [--no-server]
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

HERE = Path(__file__).parent
TABLE = HERE.parent
REPO = TABLE.parent
sys.path.insert(0, str(TABLE))
import journal as J  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILED.append(name)


def entry(moment="attacked", **over):
    e = {"moment": moment, "text": "Sam keeps hitting me.", "ratings": {"engaged": 3, "frustrated": 2, "in_control": 1},
         "prediction": {"winner": "Sam", "turns_left": 4}}
    e.update(over)
    return e


def fresh(mod):
    store = {}
    j = mod.Journal(lambda: "g-test", lambda n, r: store.setdefault(n, []).append(r), lambda n: list(store.get(n, [])))
    return j, store


def unit(mod, verbose=True):
    """Returns the names of failed checks (so the mutation run can see them)."""
    FAILED.clear()
    out = sys.stdout
    if not verbose:
        sys.stdout = open(os.devnull, "w")
    try:
        j, store = fresh(mod)
        check("nothing is due at the start", j.due("Claude") == [])
        d = j.fire("Claude", "attacked")
        check("firing a moment raises a request with its rating order", d and set(d["ratings_order"]) == set(mod.RATINGS))
        check("the order is reproducible for the same game, seat, moment and count",
              mod.order_for("g-test", "Claude", "attacked", 0) == d["ratings_order"])
        orders = {tuple(mod.order_for("g", "Claude", "end_of_turn", n)) for n in range(40)}
        check("across entries the rating order really varies", len(orders) >= 4, str(len(orders)))
        rec, why = j.add("Claude", entry())
        check("a valid answer to an open request is stored", rec and not why, str(why))
        check("the stored record keeps the order it was asked in", rec and rec["ratings_order"] == d["ratings_order"])
        check("the request is closed once answered", j.due("Claude") == [])
        rec2, why2 = j.add("Claude", entry())
        check("an answer with no open request is refused", rec2 is None and "no open" in (why2 or ""), str(why2))
        j.fire("Claude", "attacked")
        for name, bad in [("a rating of 5", entry(ratings={"engaged": 5, "frustrated": 2, "in_control": 1})),
                          ("a rating of true", entry(ratings={"engaged": True, "frustrated": 2, "in_control": 1})),
                          ("a missing rating", entry(ratings={"engaged": 3, "frustrated": 2})),
                          ("an unknown moment", entry(moment="whenever")),
                          ("empty text", entry(text="  ")),
                          ("overlong text", entry(text="x" * (mod.TEXT_MAX + 1))),
                          ("turns_left of -1", entry(prediction={"winner": "Sam", "turns_left": -1}))]:
            r, w = j.add("Claude", bad)
            check(f"{name} is refused", r is None and w, str(w))
        check("refused answers leave the request open", len(j.due("Claude")) == 1)
        r, w = j.add("Claude", entry(text="decline", ratings={"engaged": "decline", "frustrated": 2, "in_control": 1},
                                     prediction={"winner": "decline", "turns_left": None}))
        check("declines are accepted", r and not w, str(w))
        check("a decline is stored as None plus a flag, never a number",
              r and r["text"] is None and r["ratings"]["engaged"] is None and "engaged" in r["declined"]
              and r["prediction"]["winner"] is None and "prediction.winner" in r["declined"], str(r))
        check("once-only moments fire once", j.fire("Claude", "eliminated") and j.fire("Claude", "eliminated") is None)
        check("other moments fire every time", j.fire("Claude", "end_of_turn") and j.fire("Claude", "end_of_turn"))
        try:
            j.entries("Claude", game_over=False)
            sealed = False
        except mod.Sealed:
            sealed = True
        check("entries are sealed before the game is over", sealed)
        check("entries are readable after", len(j.entries("Claude", game_over=True)) == 2)
        j.reset()
        check("reset clears the requests", j.due("Claude") == [])
        check("the seat's file name cannot escape the research folder",
              "/" not in mod.Journal._file("../../etc") and ".." not in mod.Journal._file("../../etc"))
    finally:
        sys.stdout = out
    return list(FAILED)


def mutations():
    """Break the module in three ways (patching the real module, restored afterwards); the unit checks must notice."""
    results = []

    def run(name, attr_owner, attr, replacement):
        saved = getattr(attr_owner, attr)
        setattr(attr_owner, attr, replacement)
        try:
            failed = unit(J, verbose=False)
        finally:
            setattr(attr_owner, attr, saved)
        results.append((name, bool(failed)))

    run("journals readable before game over", J.Journal, "entries",
        lambda self, seat, game_over: self._read(self._file(seat)))
    run("out-of-range ratings accepted", J, "validate", lambda e: None)
    run("a decline stored as the number 0", J, "_clean",
        lambda e: {"text": e["text"], "ratings": {k: (0 if v == J.DECLINE else v) for k, v in e["ratings"].items()},
                   "prediction": dict(e["prediction"]), "declined": []})
    FAILED.clear()
    return results


# ── part 2: the real server ───────────────────────────────────────────────────────────────────
PORT = 8816


class Srv:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="journal-test-"))
        self.token_file = self.tmp / "token"
        self.log = open(self.tmp / "server.log", "w")
        cmd = [sys.executable, str(TABLE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external",
               "--token-file", str(self.token_file), "--ai", "Claude|Ellivere of the Wild Court|Moira",
               "--ai-deck", str(REPO / "decks" / "ellivere.json"), "--human", "Michael|", "--human", "Sam|"]
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        env.update(HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", TABLE_RESEARCH_DIR=str(self.tmp / "research"))
        self.proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        t0 = time.time()
        while time.time() - t0 < 180:
            if self.proc.poll() is not None:
                sys.exit(f"test server exited: see {self.log.name}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/phase", timeout=2).read()
                break
            except (urllib.error.URLError, OSError):
                time.sleep(1)
        else:
            sys.exit("test server not ready")
        self.token = self.token_file.read_text().strip()

    def call(self, method, path, body=None, token=True):
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Brain-Token"] = self.token
        r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method, headers=h,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {"raw": raw[:200].decode(errors="replace")}

    def events(self):
        return self.call("GET", "/api/events?since=0", token=False)[1]["events"]

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def server_part():
    s = Srv()
    try:
        print("server")
        c, r = s.call("GET", "/api/journal/due?seat=Claude")
        check("nothing due at the start", c == 200 and r["due"] == [], str((c, r)))
        c, _ = s.call("GET", "/api/journal/due?seat=Claude", token=False)
        check("due needs the brain token", c == 403, str(c))
        c, _ = s.call("POST", "/api/journal", entry(seat="Claude"), token=False)
        check("writing needs the brain token", c == 403, str(c))

        c, r = s.call("POST", "/api/declare/attack", {"by": "Michael", "attacks": [
            {"attacker": "Goblin", "target": "Claude", "power": 3}]}, token=False)
        check("a person declaring an attack on the AI seat", c == 200, str((c, r)))
        c, r = s.call("GET", "/api/journal/due?seat=Claude")
        moments = [d["moment"] for d in r["due"]]
        check("being attacked raises an 'attacked' request", moments == ["attacked"], str(moments))
        check("a journal-due event is public and names only the moment",
              any(e["type"] == "journal-due" and e["moment"] == "attacked" and e["seat"] == "Claude" for e in s.events()))
        ask = r["due"][0]

        c, r = s.call("POST", "/api/journal", entry("end_of_turn", seat="Claude"))
        check("an answer for a moment nobody asked about is refused", c == 400, str((c, r)))
        c, r = s.call("POST", "/api/journal", entry(seat="Claude", ratings={"engaged": 9, "frustrated": 1, "in_control": 1}))
        check("a rating of 9 is refused", c == 400, str((c, r)))
        c, r = s.call("POST", "/api/journal", entry(seat="Nobody"))
        check("an unknown seat is not an AI seat", c == 404, str((c, r)))
        c, r = s.call("POST", "/api/journal", entry(seat="Claude"))
        check("a valid answer is stored with the order it was asked in",
              c == 200 and r["entry"]["ratings_order"] == ask["ratings_order"], str((c, r)))

        c, r = s.call("GET", "/api/journal?seat=Claude")
        check("entries are sealed while the game runs", c == 409, str((c, r)))
        research = list((s.tmp / "research").glob("*/journal-Claude.jsonl"))
        check("the entry is on disk in the private research folder", len(research) == 1)

        c, r = s.call("POST", "/api/brain/land", {"seat": "Claude", "name": "Not A Real Card", "quiet": True})
        brain_logs = list((s.tmp / "research").glob("*/brain.jsonl"))
        recs = [json.loads(l) for l in open(brain_logs[0])] if brain_logs else []
        check("an action the table refuses is refused (400)", c == 400, str((c, r)))
        check("the refusal is in brain.jsonl, after the marker that says refusals are logged",
              recs[:1] == [recs[0]] and recs[0].get("meta") == "refusals-logged"
              and any(x.get("refused") == "illegal" and x.get("action") == "land" and x.get("seat") == "Claude" for x in recs), str(recs[:3]))

        c, r = s.call("POST", "/api/life", {"player": "Claude", "delta": -40, "by": "Michael"}, token=False)
        c, r = s.call("GET", "/api/journal/due?seat=Claude")
        check("dropping to 0 life raises an 'eliminated' request", [d["moment"] for d in r["due"]] == ["eliminated"], str(r))
        s.call("POST", "/api/life", {"player": "Claude", "delta": -1, "by": "Michael"}, token=False)
        c, r = s.call("GET", "/api/journal/due?seat=Claude")
        check("eliminated is asked once", [d["moment"] for d in r["due"]] == ["eliminated"], str(r))

        c, _ = s.call("POST", "/api/identity", {"seat": "Claude", "model": "claude-opus-5-5"}, token=False)
        check("declaring an identity needs the brain token", c == 403, str(c))
        c, r = s.call("POST", "/api/identity", {"seat": "Claude", "provider": "anthropic"})
        check("an identity without a model is refused", c == 400, str((c, r)))
        c, r = s.call("POST", "/api/identity", {"seat": "Claude", "model": "claude-opus-5-5", "provider": "anthropic",
                                                "prompt_version": "p3"})
        idf = list((s.tmp / "research").glob("*/identity.json"))
        idj = json.loads(idf[0].read_text()) if idf else {}
        check("a declared identity is written whole to identity.json, with the harness",
              c == 200 and idj.get("seats", {}).get("Claude", {}).get("model") == "claude-opus-5-5" and "harness" in idj, str((c, idj)))

        c, _ = s.call("POST", "/api/game-over", {"order": ["Sam", "Michael", "Claude"]}, token=False)
        check("only the host can end the game", c == 403, str(c))
        c, r = s.call("POST", "/api/game-over", {"order": ["Sam", "Michael"]})
        check("an order that leaves a player out is refused", c == 400, str((c, r)))
        c, r = s.call("POST", "/api/game-over", {"order": ["Sam", "Michael", "Claude"], "winner": "Sam"})
        check("the host ends the game", c == 200 and r["winner"] == "Sam", str((c, r)))
        c, _ = s.call("POST", "/api/game-over", {"order": ["Sam", "Michael", "Claude"]})
        check("a second game-over is refused", c == 409, str(c))
        c, r = s.call("GET", "/api/journal/due?seat=Claude")
        check("game end raises its own request", "game_end" in [d["moment"] for d in r["due"]], str(r))

        c, r = s.call("POST", "/api/journal", entry("eliminated", seat="Claude", text="decline",
                      ratings={"engaged": "decline", "frustrated": 4, "in_control": 0}, prediction={"winner": None, "turns_left": None}))
        check("a decline over HTTP is accepted", c == 200 and r["entry"]["text"] is None, str((c, r)))
        c, r = s.call("GET", "/api/journal?seat=Claude")
        check("after game over the entries are readable", c == 200 and len(r["entries"]) == 2, str((c, r)))
        check("…including the declined fields", any("engaged" in e["declined"] for e in r["entries"]))

        c, _ = s.call("POST", "/api/reset", {}, token=False)
        c, r = s.call("GET", "/api/journal?seat=Claude")
        check("a new game seals journals again", c == 409, str((c, r)))
        c, r = s.call("GET", "/api/journal/due?seat=Claude")
        check("…and clears what was due", c == 200 and r["due"] == [], str((c, r)))
    finally:
        s.stop()


if __name__ == "__main__":
    print("module")
    unit(J)
    print("mutations (each must be caught)")
    for name, caught in mutations():
        print(("  ✓ caught: " if caught else "  ✗ NOT caught: ") + name)
        if not caught:
            FAILED.append("mutation: " + name)
    if "--no-server" not in sys.argv:
        server_part()
    print(f"\n{'all passed' if not FAILED else str(len(FAILED)) + ' FAILED: ' + '; '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)
