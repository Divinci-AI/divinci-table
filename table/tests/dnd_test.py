"""D&D table: fair dice, who may change what, the DM's TABLE line, initiative, persistence.

    python3 table/tests/dnd_test.py
No network: the DM request is replaced by a stub."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
import keepalive  # noqa: E402  (one connection per server: polling must not use up local ports)
keepalive.install()

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.pop("DIVINCI_FUSION_API_KEY", None)
sys.argv = ["dnd_server.py", "--players", "Michael,Sam", "--companions", "ai:Leonardo:chatty:wizard", "--port", "0"]
import dnd_server as D  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


srv = ThreadingHTTPServer(("127.0.0.1", 0), D.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"


def call(path: str, body: dict | None = None, ua: str = "phone-a") -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


print("dice")
check("parse d20+5", D.parse_dice("d20+5") == (1, 20, 5))
check("parse 2d6-1", D.parse_dice("2d6 - 1") == (2, 6, -1))
for bad in ("d7", "30d6", "d20+500", "1d20; rm", ""):
    try:
        D.parse_dice(bad)
        check(f"reject {bad!r}", False, "accepted")
    except ValueError:
        check(f"reject {bad!r}", True)
c = Counter(D.roll("d20")["faces"][0] for _ in range(20000))
check("d20 covers 1..20 roughly evenly", set(c) == set(range(1, 21)) and max(c.values()) / min(c.values()) < 1.4, str(c))
r = D.roll("d20+2", "adv")
check("advantage keeps the higher of two", len(r["faces"]) == 2 and r["total"] == max(r["faces"]) + 2, str(r))
r = D.roll("d20", "dis", physical=[17, 4])
check("physical disadvantage is checked and kept", r["total"] == 4 and r["physical"], str(r))
for bad in ([21], [0], [3, 4]):
    try:
        D.roll("d20", physical=bad)
        check(f"physical {bad} rejected", False, "accepted")
    except ValueError:
        check(f"physical {bad} rejected", True)
check("nat 20 is a crit", D.roll("d20", physical=[20])["crit"] and not D.roll("d20", physical=[19])["crit"])
check("2d6 is never a crit", not D.roll("2d6", physical=[6, 6])["crit"])

print("seats and ownership")
code, m = call("/api/seat/claim", {"name": "Michael"}, "phone-a")
mk = m.get("key")
code2, s = call("/api/seat/claim", {"name": "Sam"}, "phone-b")
sk = s.get("key")
check("both seats claimed", code == 200 and code2 == 200 and mk and sk)
code, _ = call("/api/dnd/hp", {"by": "Michael", "delta": -5})
check("no key, no hit-point change", code == 403)
code, _ = call("/api/dnd/hp", {"by": "Michael", "key": sk, "delta": -5})
check("Sam's key can't change Michael's hit points", code == 403)
before = D.G["sheets"]["Michael"]["hp"]
code, st = call("/api/dnd/hp", {"by": "Michael", "key": mk, "delta": -5})
check("Michael changes his own", code == 200 and st["sheets"]["Michael"]["hp"] == before - 5)
code, st = call("/api/dnd/hp", {"by": "Michael", "key": mk, "delta": -999})
check("hit points floor at 0", st["sheets"]["Michael"]["hp"] == 0)
code, st = call("/api/dnd/hp", {"by": "Michael", "key": mk, "delta": 999})
check("and cap at max", st["sheets"]["Michael"]["hp"] == st["sheets"]["Michael"]["max_hp"])
code, st = call("/api/dnd/condition", {"by": "Sam", "key": sk, "condition": "prone"})
check("conditions toggle", "prone" in st["sheets"]["Sam"]["conditions"])
code, _ = call("/api/dnd/condition", {"by": "Sam", "key": sk, "condition": "on fire"})
check("unknown condition refused", code == 400)
code, st = call("/api/dnd/sheet", {"by": "Sam", "key": sk, "pregen": "cleric"})
check("swap to a pregen", st["sheets"]["Sam"]["class"] == "cleric")
code, _ = call("/api/dnd/narrate", {"by": "Sam", "key": sk, "text": "The dragon dies."})
check("a player can't narrate as DM", code == 403)

print("the DM's TABLE line")
D.G["sheets"]["Michael"]["hp"] = 20
txt = D.apply_table_line('The goblins snarl.\nTABLE: {"monsters":[{"name":"Goblin 1","ac":15,"hp":7,"max_hp":7},'
                         '{"name":"<b>x</b>","ac":"bad"}],"scene":"Ambush at the ford","party":[{"name":"Michael","hp":0}]}')
check("narration kept, TABLE line stripped", txt == "The goblins snarl.", repr(txt))
check("monsters applied, the malformed one skipped", [m["name"] for m in D.G["monsters"]] == ["Goblin 1"], str(D.G["monsters"]))
check("scene applied", D.G["scene"] == "Ambush at the ford")
check("a player's numbers are not the DM's to set", D.G["sheets"]["Michael"]["hp"] == 20)
check("garbage TABLE line is harmless", D.apply_table_line("Hi\nTABLE: {nope") == "Hi")
body, said = D.split_companions("The bridge creaks.\nLeonardo: \"I'll light the way.\" He casts light.\nMalvo: not a companion")
check("companion lines pulled out", said == [("Leonardo", "\"I'll light the way.\" He casts light.")] and "Malvo" in body, str(said))

check("plain() strips diagrams, citations, bold",
      D.plain("Go **now** [7].\n```mermaid\ngraph TD\n```\n# Next") == "Go now.\n\nNext", repr(D.plain("Go **now** [7].\n```mermaid\ngraph TD\n```\n# Next")))

print("initiative")
code, st = call("/api/dnd/initiative", {"by": "Sam", "key": sk, "action": "start"})
ini = st["initiative"]
check("monster and companion rolled by the table", {o["name"] for o in ini["order"]} == {"Goblin 1", "Leonardo"}, str(ini["order"]))
check("players still to roll", sorted(ini["pending"]) == ["Michael", "Sam"])
call("/api/dnd/roll", {"by": "Michael", "key": mk, "dice": "d20+1", "why": "initiative", "physical": [25]})
code, st = call("/api/dnd/roll", {"by": "Michael", "key": mk, "dice": "d20+1", "why": "initiative", "physical": [18]})
check("bad physical die refused, good one entered", "Michael" in [o["name"] for o in st["initiative"]["order"]])
code, st = call("/api/dnd/roll", {"by": "Sam", "key": sk, "dice": "d20", "why": "Initiative", "physical": [1]})
order = st["initiative"]["order"]
check("order sorted high to low", [o["total"] for o in order] == sorted([o["total"] for o in order], reverse=True), str(order))
check("nobody pending", st["initiative"]["pending"] == [])
n0 = st["initiative"]["turn"]
code, st = call("/api/dnd/initiative", {"by": "Sam", "key": sk, "action": "next"})
check("next turn advances", st["initiative"]["turn"] == (n0 + 1) % len(order))
code, st = call("/api/dnd/initiative", {"by": "Sam", "key": sk, "action": "end"})
check("combat ends", not st["initiative"]["active"])

print("companions and the open mic")
D.COMPANIONS[0]["spoke_at"] = []
check("named companion always speaks", D.companions_to_speak("Leonardo, can you light this?", time.time()) == ["Leonardo"])
D.COMPANIONS[0]["spoke_at"] = [time.time() - 10]
check("chatty still respects its cooldown", D.companions_to_speak("we go left", time.time()) == [])

print("AI DM (stubbed)")
check("AI DM off without a key", not D.ai_dm_enabled())
calls = []
D.ai_dm_enabled = lambda: True
D.ask_dm = lambda prompt: (calls.append(prompt), 'A cold wind.\nLeonardo: "Stay close."\nTABLE: {"scene":"The ford"}')[1]
code, _ = call("/api/dnd/act", {"by": "Sam", "key": sk, "text": "Leonardo, what do you see?"})
for _ in range(50):
    if not D.G["dm_busy"] and calls:
        break
    time.sleep(0.05)
ev = D.EVENTS.items
check("DM narration emitted", any(e["type"] == "dm" and e["text"] == "A cold wind." for e in ev))
check("companion voiced in the same reply", any(e["type"] == "say" and e["by"] == "Leonardo" and e.get("ai") for e in ev))
check("prompt carries STATE and asks for the companion", calls and "STATE:" in calls[0] and 'starting "Leonardo:"' in calls[0])
check("prompt never includes seat keys", calls and mk not in calls[0] and sk not in calls[0])
code, _ = call("/api/chat", {"by": "Sam", "key": sk, "text": "brb, pizza"})
time.sleep(0.2)
check("table talk doesn't call the DM", len(calls) == 1, str(len(calls)))
D.G["dm_calls"] = D.MAX_DM_CALLS
call("/api/dnd/continue", {"by": "Sam", "key": sk})
time.sleep(0.2)
check("per-room DM cap holds", len(calls) == 1 and any("limit" in (e.get("text") or "") for e in D.EVENTS.items[-3:]))

print("people's survey")
code, _ = call("/api/survey", {"by": "Sam", "scale": {"I felt content": 3}})
check("survey needs a seat key", code == 403)
code, _ = call("/api/survey", {"by": "Sam", "key": sk, "scale": {"I felt content": 9}})
check("ratings stay 0-4", code == 400)
code, _ = call("/api/survey", {"by": "Sam", "key": sk})
check("an empty survey is refused", code == 400)
code, _ = call("/api/survey", {"by": "Sam", "key": sk, "scale": {"I felt content": 3, "made up": 4}, "best": "the bridge fight", "evil": "x"})
files = list((D.RESEARCH / D.G["game_id"]).glob("survey-human-*.json"))
saved = json.loads(files[0].read_text()) if files else {}
check("saved with only the fixed questions", code == 200 and saved.get("scale") == {"I felt content": 3}
      and saved.get("text") == {"best": "the bridge fight"}, str(saved))
ev = [e for e in D.EVENTS.items if e["type"] == "survey"]
check("the public log says who answered, never what", ev and set(ev[-1]) <= {"id", "type", "ts", "by"}, str(ev[-1:]))
check("survey page serves", "How was the game?" in urllib.request.urlopen(BASE + "/survey").read().decode())

print("persistence")
blob = D.ROOM.snapshot()
hp, scene, nev = D.G["sheets"]["Michael"]["hp"], D.G["scene"], D.EVENTS.next_id
D.new_game()
D.ROOM.adopted = False
D.ROOM.restore(blob)
check("restore brings back sheets, scene and log", D.G["sheets"]["Michael"]["hp"] == hp and D.G["scene"] == scene and D.EVENTS.next_id == nev)
check("restore refused once adopted", D.ROOM.restore(blob)[0] == 409)
check("seat keys survive restore", D.SEATS.ok("Michael", mk))
code, html = 200, urllib.request.urlopen(BASE + "/").read().decode()
check("page serves", "Dungeons &amp; Dragons" in html)
code, cred = 200, urllib.request.urlopen(BASE + "/api/dnd/credits").read().decode()
check("SRD credit served", "CC" in cred or "Creative Commons" in cred)

srv.shutdown()
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
