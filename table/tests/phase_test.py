#!/usr/bin/env python3
"""NEXT, priority windows and snapshot/restore, end to end against a real server on a spare port.

    ~/.venvs/table/bin/python table/tests/phase_test.py

Starts its own server (fair seeding off, its own token file, port 8815) and never touches a running game.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PORT = 8815
BASE = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="phase-test-"))
TOKEN = TMP / "token"
os.environ["TABLE_RESEARCH_DIR"] = str(TMP / "research")         # this test's games stay out of the real data
RESEARCH = TMP / "research"
fails = []


def check(ok, what):
    print(("  ✅ " if ok else "  ❌ ") + what, flush=True)
    if not ok:
        fails.append(what)


def call(method, path, body=None, brain=False):
    h = {"Content-Type": "application/json"}
    if brain:
        h["X-Brain-Token"] = TOKEN.read_text().strip()
    r = urllib.request.Request(BASE + path, method=method, headers=h,
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def start(*extra):
    env = {**os.environ, "ROUTER": "code", "REPLIES": "template", "HF_HUB_OFFLINE": "1"}
    p = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--brain", "external",
                          "--ai", "Claude|Aminatou, the Fateshifter|Moira", "--ai-deck", str(HERE.parent / "decks/aminatou.json"),
                          "--human", "Ann|Kilo, Apogee Mind", "--human", "Ben|Captain N'ghathrod",
                          "--order", "Ann,Claude,Ben", "--fair-seed", "off", "--port", str(PORT),
                          "--token-file", str(TOKEN), *extra],
                         stdout=open(TMP / "server.log", "a"), stderr=subprocess.STDOUT, env=env)
    for _ in range(90):
        try:
            urllib.request.urlopen(BASE + "/api/phase", timeout=1)
            return p
        except Exception:
            time.sleep(1)
    p.kill()
    sys.exit("server did not start: " + (TMP / "server.log").read_text()[-800:])


def brain(action, **body):
    return call("POST", f"/api/brain/{action}", {"seat": "Claude", **body}, brain=True)


BEFORE = set(RESEARCH.glob("*"))              # never touch an existing game's logs
srv = start("--priority-window", "0", "--priority-secs", "30", "--priority-beat", "0.6,0.6")
BEAT = 0.7
try:
    print("NEXT and priority")
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    check(code == 200 and p["player"] == "Ann" and p["step"] == "untap", "first NEXT starts the first seat's turn at untap")
    check(p["waiting"] == [], "untap gives no one priority")
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})          # Claude has no lands yet: nothing castable
    check(p["step"] == "upkeep" and p["waiting"] == ["Claude"],
          "every AI seat gets the window, even one with nothing to cast")
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    check(code == 409 and p.get("seconds_left", 0) > 0, "NEXT waits out the window's beat")
    time.sleep(BEAT)
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    check(code == 200 and p["step"] == "draw", "a seat with nothing to cast passes by itself after the beat")
    for land in ("Swamp", "Plains", "Island"):                       # now Claude holds a castable instant
        brain("search", name=land, to="battlefield")
    brain("search", name="Mortify")
    time.sleep(BEAT)
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    check(p["step"] == "main 1" and p["waiting"] == ["Claude"], "a seat holding an instant gets the very same window")
    time.sleep(BEAT)
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    check(code == 409 and "Claude" in p.get("error", ""), "…and is waited on past the beat until it answers")
    brain("pass", quiet=True)
    code, p = call("GET", "/api/phase")
    check(p["waiting"] == [], "pass closes the window")
    code, ev = call("GET", "/api/events?since=0")
    pri = [e for e in ev["events"] if e["type"] == "attention" and e.get("kind") == "priority"]
    shape = {tuple(sorted(k for k in e if k not in ("id", "ts"))) for e in pri}
    check(len(pri) == 3 and len(shape) == 1 and len({e["text"].split(". ")[1] for e in pri}) == 1,
          "the public event for a seat with nothing and a seat with an instant look the same")
    check(not any(e["type"] == "priority" for e in ev["events"]),
          "no public event says a window closed early or who timed out")
    holders = [json.loads(l) for d in set(RESEARCH.glob("*")) - BEFORE
               for l in (d / "brain.jsonl").read_text().splitlines() if (d / "brain.jsonl").exists()]
    check(any(r.get("priority") == "main 1" and r.get("holders") == ["Claude"] for r in holders) and
          not any(r.get("priority") == "upkeep" for r in holders),
          "who could respond is recorded privately (brain.jsonl) for research, not on the stream")
    for _ in range(14):
        time.sleep(BEAT)
        code, p = call("POST", "/api/phase/next", {"by": "Ann"})
        if code == 409:
            brain("pass", quiet=True)
            continue
        if p.get("player") == "Claude":
            break
    check(p.get("player") == "Claude" and p.get("step") == "untap", "the last step hands the turn to the next seat")
    code, p = call("POST", "/api/phase/back", {"by": "Ann"})
    check(code == 200 and p["player"] == "Ann" and p["step"] == "cleanup",
          "BACK at the start of an AI's turn (not begun) returns to the previous player's cleanup")
    code, ev = call("GET", "/api/events?since=0")
    check(any(e.get("kind") == "turn-cancelled" and e.get("addressee") == "Claude" for e in ev["events"]),
          "…and tells that AI its turn was taken back")
    code, p = call("POST", "/api/phase/back", {"by": "Ann"})
    check(code == 200 and p["step"] == "end step" and p["waiting"] == [], "BACK within a turn steps back with no window")
    time.sleep(BEAT)
    call("POST", "/api/phase/next", {"by": "Ann"})
    time.sleep(BEAT)
    code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    if code == 409:
        brain("pass", quiet=True); time.sleep(BEAT); code, p = call("POST", "/api/phase/next", {"by": "Ann"})
    check(p.get("player") == "Claude", "NEXT then hands the turn on again")
    brain("begin")
    code, p = call("POST", "/api/phase/back", {"by": "Ann"})
    check(code == 409 and "already started" in p.get("error", ""), "BACK is refused once the AI has started its turn")
    brain("end", text="test")
    time.sleep(1.5)
    code, p = call("GET", "/api/phase")
    check(p["player"] == "Ben", "an AI ending its turn hands it to the next seat in order")
    code, p = call("POST", "/api/phase/back", {"by": "Ben"})
    check(code == 409 and "Claude" in p.get("error", ""), "BACK never goes into an AI's finished turn")

    print("who counts as this laptop")
    def raw(path, headers=None, method="GET", data=None):
        r = urllib.request.Request(BASE + path, method=method, headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
    check(raw("/") == 200, "the scan pad (the AI's secret draws) opens on the laptop itself")
    for h in ("X-Forwarded-For", "CF-Connecting-IP", "Forwarded", "X-Real-IP"):
        check(raw("/", {h: "203.0.113.9"}) == 403, f"through a tunnel ({h}) it does not")
    check(raw("/table", {"X-Forwarded-For": "203.0.113.9"}) == 200, "the table page opens elsewhere as a viewer")
    for path in ("/api/reset", "/api/ai/turn", "/api/utterance", "/api/players"):
        check(raw(path, {"X-Forwarded-For": "203.0.113.9", "Content-Type": "application/json"}, "POST", b"{}") == 403,
              f"…but its controls stay on the laptop ({path})")
    check(raw("/stage", {"X-Forwarded-For": "203.0.113.9"}) == 200, "a tunnelled visitor still gets the public stage")
    tok = TOKEN.read_text().strip()
    check(raw("/api/brain/state?seat=Claude", {"X-Forwarded-For": "203.0.113.9", "X-Brain-Token": tok}) == 403,
          "the brain API is refused through a tunnel even with the token")
    big = json.dumps({"player": "Ann", "delta": 1, "pad": "x" * 300_000}).encode()
    check(raw("/api/life", {"Content-Type": "application/json"}, "POST", big) == 400,
          "an oversized JSON body is refused, not read into memory")

    print("captain lines")
    audio = HERE / ".cache" / "captains" / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    mp3 = audio / "0123456789abcdef.mp3"
    made = not mp3.exists()
    if made:
        mp3.write_bytes(b"ID3test")
    try:
        code, e = call("POST", "/api/captain", {"seat": "Ann", "captain": "Kilo, Apogee Mind", "text": "One more counter.",
                                                "audio": mp3.name}, brain=True)
        check(code == 200 and e.get("type") == "captain" and e.get("audio") == "/captains/" + mp3.name,
              "a captain line with its mp3 becomes a public event")
        code, _ = call("POST", "/api/captain", {"seat": "Ann", "text": "hi", "audio": mp3.name})
        check(code == 403, "captain lines need the brain token")
        code, _ = call("POST", "/api/captain", {"seat": "Ann", "text": "hi", "audio": "../../.brain-token"}, brain=True)
        check(code == 400, "the audio must be a file captains.py made, never a path")
        code, st = call("GET", "/api/brain/state?seat=Claude", brain=True)
        held = st["hand"][0]["name"]
        code, _ = call("POST", "/api/captain", {"seat": "Claude", "text": f"Behold, {held}!", "audio": mp3.name}, brain=True)
        check(code == 409, "an AI seat's captain cannot name a card still in that hand")
        r = urllib.request.urlopen(f"{BASE}/captains/{mp3.name}", timeout=5)
        check(r.status == 200 and r.headers["Content-Type"] == "audio/mpeg", "the mp3 is served to the pages")
        try:
            urllib.request.urlopen(f"{BASE}/captains/..%2F..%2Fcaptains.json", timeout=5)
            check(False, "no path escapes the captain audio folder")
        except urllib.error.HTTPError as err:
            check(err.code == 404, "no path escapes the captain audio folder")
    finally:
        if made:
            mp3.unlink()

    print("the 3D board")
    code, _ = call("POST", "/api/brain/manifest", {"seat": "Claude", "n": 1}, brain=True)
    code, d = call("GET", "/api/board3d")
    me = next(x for x in d["seats"] if x["name"] == "Claude")
    fd = [p for p in me["permanents"] if p.get("face_down")]
    check(code == 200 and bool(fd) and all(p.get("name") is None and not p.get("text") for p in fd),
          "a face-down card is on the board with no name and no text")
    check(all(p.get("how") == "manifest" for p in fd), "…but it says how it went face down (public: manifest, cloak, morph, disguise)")
    check(any(p.get("name") == "Mortify" for p in me["permanents"]) is False and "hand" in me and isinstance(me["hand"], int),
          "the board shows a hand's size, never its cards")
    check(raw("/api/board3d", {"X-Forwarded-For": "203.0.113.9"}) == 200, "phones and remote visitors can read the board")
    check(raw("/board", {"X-Forwarded-For": "203.0.113.9"}) == 200, "the plain-HTML board opens on any device")
    code, _ = call("POST", "/api/public-board", {"seat": "Ann", "permanents": [{"name": "Mountain"}]})
    check(code == 403, "recording a human's board needs the brain token")
    code, _ = call("POST", "/api/public-board", {"seat": "Claude", "permanents": [{"name": "Black Lotus"}]}, brain=True)
    check(code == 400, "an AI seat's board can't be overwritten (it comes from the engine)")
    code, r = call("POST", "/api/public-board", {"seat": "Ann", "commander_out": True, "graveyard": ["Shock"],
                   "permanents": [{"name": "Mountain", "tapped": True}, {"name": "Centaur", "token": True, "pt": "3/3"},
                                  {"name": "x" * 500, "counters": "2"}]}, brain=True)
    code, d = call("GET", "/api/board3d")
    ann = next(x for x in d["seats"] if x["name"] == "Ann")
    names = [p["name"] for p in ann["permanents"]]
    check(names[:2] == ["Mountain", "Centaur"] and ann["permanents"][0]["tapped"] and ann["graveyard"] == ["Shock"]
          and not ann["commander_in_zone"], "a human's board, graveyard and commander are recorded as given")
    check(len(names[2]) <= 80 and ann["permanents"][2].get("counters") == 2, "fields are trimmed and typed")
    check(ann["permanents"][0].get("type", "").startswith("Basic Land"), "cards are filled in from the card file")

    print("at the table (to-dos for the physical cards)")
    code, _ = call("POST", "/api/todo", {"text": "Move Hunted Horror to your graveyard", "for": "Ben"})
    check(code == 403, "only the brain can add a to-do")
    code, r = call("POST", "/api/todo", {"items": [{"text": "Move Hunted Horror to your graveyard", "for": "Ben"},
                                                   {"text": "x" * 500}]}, brain=True)
    check(code == 200 and len(r["added"]) == 2 and len(r["added"][1]["text"]) <= 240, "the brain adds them (trimmed)")
    code, d = call("GET", "/api/todos")
    check(raw("/api/todos", {"X-Forwarded-For": "203.0.113.9"}) == 200 and len(d["open"]) == 2, "everyone can see them")
    tid = d["open"][0]["id"]
    r2 = urllib.request.Request(BASE + "/api/todo/done", method="POST", data=json.dumps({"id": tid, "by": "Ben"}).encode(),
                                headers={"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.9"})
    check(urllib.request.urlopen(r2, timeout=5).status == 200, "anyone at the table can tick one off")
    code, d = call("GET", "/api/todos")
    check(len(d["open"]) == 1 and d["done"][-1]["id"] == tid, "a ticked item moves to done for everybody")
    code, r = call("POST", "/api/todo", {"items": [{"text": "Which card does N'ghathrod take?", "for": "Ben", "ask": "Claude",
                                                   "options": ["Sol Ring", "none"]}]}, brain=True)
    qid = r["added"][0]["id"]
    check(r["added"][0]["kind"] == "question" and r["added"][0]["ask"] == "Claude", "a player can ask the table a question")
    code, ev0 = call("GET", "/api/events?since=latest")
    r3 = urllib.request.Request(BASE + "/api/todo/answer", method="POST", data=json.dumps({"id": qid, "text": "Sol Ring", "by": "Ben"}).encode(),
                                headers={"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.9"})
    check(urllib.request.urlopen(r3, timeout=5).status == 200, "anyone at the table can answer it")
    code, ev = call("GET", f"/api/events?since={ev0['last']}")
    check(any(e.get("kind") == "answered" and e.get("answer") == "Sol Ring" for e in ev["events"]), "…and the answer reaches the players")
    code, _ = call("POST", "/api/todo/answer", {"id": qid, "text": "done, thanks", "by": "Ben", "resolve": True})
    code, d = call("GET", "/api/todos")
    check(not any(x["id"] == qid for x in d["open"]), "a resolved question leaves the open list")

    print("game log")
    brain("mill", n=2)
    code, h = call("GET", "/api/history?from=0")
    mills = [e for e in h["events"] if e.get("type") == "say" and e.get("action") == "mill"]
    check(code == 200 and mills and ":" in mills[-1]["text"], "the game log has every mill, with the cards' names")
    check(not any(e.get("type") == "attention" and e.get("kind") == "priority" for e in h["events"]), "…and leaves out the noise")
    check(raw("/api/history", {"X-Forwarded-For": "203.0.113.9"}) == 200, "anyone at the table can read it")
    check(raw("/log", {"X-Forwarded-For": "203.0.113.9"}) == 200, "…and pop it out into its own window (/log)")
    check(all(isinstance(e.get("cards"), list) and len(e["cards"]) == int(e["text"].split()[2].rstrip(":")) for e in mills),
          "each mill in the log lists its cards one by one (names with commas kept whole)")
    key = f"{mills[-1]['seq']}:0"
    code, r = call("POST", "/api/placed", {"key": key, "on": True})
    code2, lst = call("GET", "/api/placed")
    check(code == 200 and key in lst, "anyone can tick a milled card as moved to the graveyard, for everyone")
    code, _ = call("POST", "/api/placed", {"key": "../x", "on": True})
    check(code == 400, "…and only a real log position")

    print("declaring attacks from a phone or the board")
    code, ev0 = call("GET", "/api/events?since=latest")
    r4 = urllib.request.Request(BASE + "/api/declare/attack", method="POST",
        data=json.dumps({"by": "Ben", "attacks": [{"attacker": "Hunted Horror", "target": "Claude", "power": 7, "trample": True},
                                                  {"attacker": "Centaur", "target": "Ann", "power": 3}]}).encode(),
        headers={"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.9"})
    check(urllib.request.urlopen(r4, timeout=5).status == 200, "a player declares attacks from any device")
    code, ev = call("GET", f"/api/events?since={ev0['last']}")
    att = [e for e in ev["events"] if e.get("kind") == "attacked"]
    check(len(att) == 1 and att[0]["addressee"] == "Claude" and att[0]["amount"] == 7 and att[0]["trample"],
          "an attacked AI seat gets its block decision, with the power and trample")
    check(any(e["type"] == "declare" and len(e["attacks"]) == 2 for e in ev["events"]), "the whole attack goes to the game log")
    code, _ = call("POST", "/api/declare/attack", {"by": "Ben", "attacks": [{"attacker": "X", "target": "Ben"}]})
    check(code == 400, "no attacking yourself (or nobody)")

    print("typed table talk")
    code, ev0 = call("GET", "/api/events?since=latest")
    r5 = urllib.request.Request(BASE + "/api/chat", method="POST", data=json.dumps({"by": "Ann", "text": "I'm at 30."}).encode(),
                                headers={"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.9"})
    check(urllib.request.urlopen(r5, timeout=20).status == 200, "anyone can type to the table from any device")
    code, ev = call("GET", f"/api/events?since={ev0['last']}")
    check(any(e["type"] == "chat" and e["by"] == "Ann" for e in ev["events"]), "the line goes to everyone")
    code, life = call("GET", "/api/life")
    check(life.get("Ann") == 30, "…and is read like a spoken line (a life report changes the life total)")

    print("snapshot / restore")
    code, st = call("GET", "/api/brain/state?seat=Claude", brain=True)
    before = json.dumps([st["hand"], st["permanents"], st["life"]], sort_keys=True)
    snaps = sorted(RESEARCH.glob("*/snapshot.pkl"), key=lambda x: x.stat().st_mtime)
    check(bool(snaps), "a snapshot is written after changes")
    srv.terminate(); srv.wait(10)
    srv = start("--restore", str(snaps[-1]), "--priority-window", "1.5")
    code, st = call("GET", "/api/brain/state?seat=Claude", brain=True)
    check(json.dumps([st["hand"], st["permanents"], st["life"]], sort_keys=True) == before,
          "restore brings back the same hand, board and life")
    code, d = call("GET", "/api/board3d")
    check(any(x["name"] == "Ann" and any(p["name"] == "Centaur" for p in x["permanents"]) for x in d["seats"]),
          "a human's recorded board survives a restore")
    code, d = call("GET", "/api/todos")
    check(len(d["open"]) == 1, "open to-dos survive a restore")
    code, p = call("GET", "/api/phase")
    check(p["player"] == "Ben", "restore brings back whose turn it is")

    print("fixed windows (the default): every step lasts the same, whoever holds what")
    code, p = call("POST", "/api/phase/next", {"by": "Ben"})           # untap → upkeep: a window opens
    t0 = time.time()
    check(code == 200 and p["waiting"] == ["Claude"] and 1.0 < p["seconds_left"] <= 1.5, "a window opens with the full fixed time")
    brain("pass", quiet=True)
    code, p = call("POST", "/api/phase/next", {"by": "Ben"})
    check(code == 409 and p["waiting"] == ["Claude"], "passing early does NOT close it (an early answer looks like none)")
    time.sleep(max(0, 1.6 - (time.time() - t0)))
    code, p = call("POST", "/api/phase/next", {"by": "Ben"})
    check(code == 200, "it closes when the time is up")
    t1 = time.time()
    code, p = call("POST", "/api/phase/next", {"by": "Ben"})
    check(code == 409, "a seat that says nothing is waited on for the same time…")
    time.sleep(max(0, 1.6 - (time.time() - t1)))
    code, p = call("POST", "/api/phase/next", {"by": "Ben"})
    check(code == 200, "…and passes when it's up — no window lasts longer than another")
    print("step timeouts off")
    code, p = call("POST", "/api/phase/windows", {"on": False, "by": "Ben"})
    check(code == 200 and p["windows"] is False, "the table can switch step timeouts off")
    code, ev0 = call("GET", "/api/events?since=latest")
    code, p = call("POST", "/api/phase/next", {"by": "Ben"})
    code2, p2 = call("POST", "/api/phase/next", {"by": "Ben"})
    check(code == 200 and code2 == 200 and p2["waiting"] == [], "with timeouts off, NEXT never waits")
    code, ev = call("GET", f"/api/events?since={ev0['last']}")
    check(sum(1 for e in ev["events"] if e.get("kind") == "priority" and e.get("addressee") == "Claude") == 2,
          "…but every AI seat still hears every window")
    code, p = call("POST", "/api/phase/windows", {"on": True})
    check(p["windows"] is True and raw("/api/phase", {"X-Forwarded-For": "203.0.113.9"}) == 200, "and back on")
finally:
    srv.terminate()
    import shutil
    for d in set(RESEARCH.glob("*")) - BEFORE:   # only folders this test created
        shutil.rmtree(d, ignore_errors=True)

print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
