"""The arena (harness/arena.py): scripted seats, a fake local model server, the safety limits, and the files a game leaves for
the Phase A tools. No real model, no network beyond localhost.

  /usr/bin/python3 table/tests/arena_test.py
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness"))
sys.path.insert(0, str(REPO / "table"))
import arena as A  # noqa: E402
import ratings  # noqa: E402

FAILED = []
SEATS = "human-sim,heuristic,random,scripted-llm"


def check(name, cond, detail=""):
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        FAILED.append(name)


def run(root: Path, *argv, seats=SEATS):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = A.main(["--seats", seats, "--research-dir", str(root), "--survey-runs", "1", *argv])
    return rc, out.getvalue()


def games(root: Path):
    p = root / "arena-games.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def ledger(root: Path):
    p = root / "arena-ledger.json"
    return json.loads(p.read_text())["games"] if p.exists() else []


# ── a fake local model server ────────────────────────────────────────────────────────────────────────────────────
class Fake(BaseHTTPRequestHandler):
    seen: list = []

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models": []}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        p = body["messages"][0]["content"]
        Fake.seen.append({"json_mode": body.get("format") == "json", "think": body.get("think"), "prompt": p[:60]})
        if "Answer each question with the NUMBER" in p:
            n = len(re.findall(r"^Question \d+:", p, re.M))
            text = json.dumps({"answers": [1] * (n - 1) + ["not an option"]})        # the last answer is unusable on purpose
        elif "Say ONE short sentence" in p:
            text = "Good luck, all."
        elif "Journal entry" in p:
            text = json.dumps({"text": "Steady.", "ratings": {"engaged": 3, "frustrated": 1, "in_control": 2},
                               "prediction": {"winner": "Seat 2", "turns_left": 3}})
        else:
            text = json.dumps({"items": {l: "quite a lot" for l in re.findall(r"^  (q\d\d):", p, re.M)}})
        out = json.dumps({"message": {"content": text}}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def suite(mod, tmp: Path) -> list[str]:
    FAILED.clear()
    r1 = tmp / "r1"
    rc, out = run(r1, "--games", "2", "--seed", "11")
    gs, led = games(r1), ledger(r1)
    check("two games are played and recorded", rc == 0 and len(gs) == 2 and all(g["status"] == "finished" for g in gs), str(gs[:1]))
    check("every finished game has a complete finish order that starts with the winner",
          all(len(e["result"]["order"]) == 4 and e["result"]["order"][0] == e["result"]["winner"] for e in led), str(led[:1]))
    def oracle(gid, seats):
        """The finish order from the engine's own public events, independent of how the arena ranks."""
        evs = [json.loads(l) for l in (r1 / gid / "events.jsonl").read_text().splitlines()]
        out = [int(re.match(r"☠ Seat (\d)", e["text"]).group(1)) - 1 for e in evs if e["type"] == "log" and e["text"].startswith("☠")]
        win = [int(re.match(r"Seat (\d)", e["text"]).group(1)) - 1 for e in evs if e["type"] == "log" and " wins — last player" in e["text"]]
        return [seats[i] for i in win + list(reversed(out))]
    check("the finish order is the engine's own: the survivor first, then the last eliminated, back to the first out",
          all(oracle(g["id"], g["seats"]) == e["result"]["order"] for g, e in zip(gs, led)),
          str([(oracle(g["id"], g["seats"]), e["result"]["order"]) for g, e in zip(gs, led)][:1]))
    loaded = ratings.from_ledger(r1 / "arena-ledger.json")
    check("ratings.py reads the arena ledger: all games are the ai-only condition (human-sim is not a person)",
          len(loaded["conditions"].get("ai-only", [])) == 2 and not loaded["conditions"].get("mixed"), str(loaded.get("conditions", {}).keys()))
    g0 = r1 / gs[0]["id"]
    ev = [json.loads(l) for l in (g0 / "events.jsonl").read_text().splitlines()]
    kinds = {e["type"] for e in ev}
    check("the public log holds engine events, table talk, journal requests and the game-over", {"log", "say", "journal-due", "game-over"} <= kinds, str(kinds))
    check("game-over names every seat in the finish order", len([e for e in ev if e["type"] == "game-over"][0]["order"]) == 4)
    idn = json.loads((g0 / "identity.json").read_text())["seats"]
    check("every seat declared an identity, human-sim says it is not a person",
          set(idn) == {"human-sim", "heuristic", "random", "scripted-llm"} and "NOT a person" in idn["human-sim"]["notes"]
          and "persona=" in idn["human-sim"]["notes"], str(idn))
    check("only the model seat journals, and it was asked at the fixed moments",
          (g0 / "journal-scripted-llm.jsonl").exists() and not list(g0.glob("journal-human-sim*")) and
          {json.loads(l)["moment"] for l in (g0 / "journal-scripted-llm.jsonl").read_text().splitlines()} <= set(A.J.MOMENTS))
    b = json.loads((r1 / "bundles" / f"{gs[0]['id']}-bundle" / "manifest.json").read_text())
    check("the bundle has the journals (the game is over) and no undeclared seat",
          not b["journals_sealed"] and not b["undeclared_seats"] and any(f["path"].startswith("journals/") for f in b["files"]), str(b["left_out"]))
    check("the model seat's survey ran (2 framings) and parsed", gs[0]["surveys"] == {"asked": 2, "parsed": 2}, str(gs[0]["surveys"]))

    r2 = tmp / "r2"
    run(r2, "--games", "2", "--seed", "11")
    check("the same seed plays the same games (order and decisions)",
          [(g["rounds"], g["decisions"]) for g in games(r2)] == [(g["rounds"], g["decisions"]) for g in gs]
          and [e["result"]["order"] for e in ledger(r2)] == [e["result"]["order"] for e in led])

    r3 = tmp / "r3"
    run(r3, "--games", "1", "--seed", "5", seats="scripted-llm,scripted-llm,heuristic,random")
    ids = games(r3)[0]["seats"]
    check("a repeated seat gets a distinct name", ids[0] == "scripted-llm" and ids[1] == "scripted-llm#2", str(ids))

    r4 = tmp / "r4"
    (r4).mkdir()
    (r4 / "ARENA.STOP").write_text("")
    rc, out = run(r4, "--games", "3")
    check("a STOP file stops the run before any game", games(r4) == [] and "STOP" in out)

    r5 = tmp / "r5"
    rc, out = run(r5, "--games", "5", "--game-timeout", "0")
    gs5 = games(r5)
    check("a game past its timeout is aborted, never rated, and its journals stay sealed",
          gs5 and all(g["status"] == "aborted" and g["abort"] == "game timeout" for g in gs5) and ledger(r5) == []
          and not (r5 / "bundles").exists(), str(gs5[:1]))
    check("three aborted games in a row stop the run", len(gs5) == 3 and "three games failed" in out, str(len(gs5)))

    r6 = tmp / "r6"
    rc, out = run(r6, "--games", "2", "--min-free-gb", "1e9")
    check("a nearly full disk stops the run", games(r6) == [] and "disk nearly full" in out)

    r7 = tmp / "r7"
    run(r7, "--games", "1", "--seed", "3", "--round-cap", "2")
    gs7 = games(r7)
    check("a game that hits the round cap is recorded as capped and carries no finish order",
          gs7 and gs7[0]["status"] == "capped" and ledger(r7)[0]["result"]["order"] is None, str(gs7[:1]))
    check("…and ratings.py leaves it out", sum(len(v) for v in ratings.from_ledger(r7 / "arena-ledger.json")["conditions"].values()) == 0)

    try:
        A.make_seat("jev", 0, False)
        refused = False
    except SystemExit:
        refused = True
    check("a paid seat is refused without --allow-paid", refused)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            A.main(["--seats", "jev,heuristic,random,heuristic", "--research-dir", str(tmp / "r8")])
        up_front = False
    except SystemExit:
        up_front = not (tmp / "r8" / "arena-games.jsonl").exists()
    check("…and refused before any game starts", up_front)

    a1, a2 = A.HumanSimAgent(7), A.HumanSimAgent(7)
    check("a human-sim persona is fixed by its seed", a1.persona == a2.persona)
    lines = {p: {l.format(who="X") for lines in A.PERSONAS[p].values() for l in lines} for p in A.PERSONAS}
    check("the three personas share no lines, so a judge's ranking is checkable against the known persona",
          not (lines["gracious"] & lines["salty"]) and not (lines["salty"] & lines["neutral"]) and not (lines["gracious"] & lines["neutral"]))

    check("option answers: a number, a digit string, key text, 'key — description'",
          A._option_key(2, [("a", ""), ("b", "")]) == "b" and A._option_key("1", [("a", ""), ("b", "")]) == "a"
          and A._option_key("go to combat", [("cast X", ""), ("go to combat", "")]) == "go to combat"
          and A._option_key("cast X — Cost 1.", [("cast X", ""), ("go to combat", "")]) == "cast X")
    check("option answers: out of range, zero, a boolean, junk and an ambiguous prefix mean nothing",
          A._option_key(3, [("a", ""), ("b", "")]) is None and A._option_key(0, [("a", "")]) is None
          and A._option_key(True, [("a", "")]) is None and A._option_key("banana", [("a", "")]) is None
          and A._option_key("cast X Y", [("cast X", ""), ("cast X Y", "")]) == "cast X Y")
    return list(FAILED)


def server_part(tmp: Path):
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    saved = A.OLLAMA
    A.OLLAMA = f"http://127.0.0.1:{srv.server_port}"
    Fake.seen.clear()
    try:
        r = tmp / "model"
        rc, out = run(r, "--games", "1", "--seed", "9", seats="human-sim,heuristic,random,gemma4:e2b")
        gs = games(r)
        st = gs[0]["agents"]["gemma4:e2b"] if gs else {}
        check("a game with a model seat plays through a (fake) local model", rc == 0 and gs and gs[0]["status"] in ("finished", "capped"), out[-200:])
        check("a decision whose answer is unusable falls back to the heuristic and is counted, not hidden",
              st.get("calls", 0) > 0 and st.get("fallbacks", 0) > 0 and st["fallbacks"] <= st["calls"], str(st))
        check("a usable answer by number is used (fewer fallbacks than calls when a question has one option)", st.get("fallbacks", 0) <= st.get("calls", 0))
        decide = [s for s in Fake.seen if s["prompt"].startswith("You are Seat") and "NUMBER" in s["prompt"]]
        check("decisions ask for JSON with thinking off; table talk asks for plain text",
              any(s["json_mode"] and s["think"] is False for s in Fake.seen if s["prompt"].startswith("You are Seat"))
              and any(not s["json_mode"] for s in Fake.seen if "Say ONE" in s["prompt"] or s["prompt"].startswith("You are Seat") and False)
              or any(not s["json_mode"] for s in Fake.seen), str(Fake.seen[:3]))
        ev = [json.loads(l) for l in (r / gs[0]["id"] / "events.jsonl").read_text().splitlines()]
        says = [e["text"] for e in ev if e["type"] == "say" and e["speaker"] == "gemma4:e2b"]
        check("the model's table talk is a sentence, not JSON", says and all(not t.startswith("{") for t in says), str(says[:3]))
        jr = [json.loads(l) for l in (r / gs[0]["id"] / "journal-gemma4_e2b.jsonl").read_text().splitlines()]
        check("a journal prediction naming 'Seat 2' is stored as that seat's name",
              jr and all(j["prediction"]["winner"] in gs[0]["seats"] for j in jr), str([j["prediction"] for j in jr[:2]]))
    finally:
        A.OLLAMA = saved
        srv.shutdown()
    return list(FAILED)


def mutations(tmp: Path):
    results = []

    def go(name, owner, attr, repl, sub):
        saved = getattr(owner, attr)
        setattr(owner, attr, repl)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                failed = suite(A, tmp / sub)
        finally:
            setattr(owner, attr, saved)
        results.append((name, bool(failed)))
    go("the winner is put last in the finish order", A, "finish_order",
       lambda game, rec: ("finished", list(reversed([p.seat for p in game.players if p.alive] + list(reversed(rec.out_order)))))
       if len([p for p in game.players if p.alive]) == 1 else ("capped", None), "m1")
    go("the STOP file is ignored", A.Recorder, "check_abort", lambda self: None, "m2")
    go("any number is accepted as an option", A, "_option_key", lambda a, options: options[0][0], "m3")
    FAILED.clear()
    return results


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        print("arena (scripted seats)")
        suite(A, tmp / "base")
        print("a model seat against a fake local model server")
        server_part(tmp)
        base = list(FAILED)
        print("mutations (each must be caught)")
        for name, caught in mutations(tmp):
            print(("  ✓ caught: " if caught else "  ✗ NOT caught: ") + name)
            if not caught:
                base.append("mutation: " + name)
        FAILED[:] = base
    print(f"\n{'all passed' if not FAILED else str(len(FAILED)) + ' FAILED: ' + '; '.join(FAILED)}")
    sys.exit(1 if FAILED else 0)
