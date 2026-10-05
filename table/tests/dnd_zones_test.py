"""Theater of the mind (docs/THEATER-GOAL.md T1): zones, range, movement and engagement, and who may change them.

The rules on their own (dnd_zones.py), then a theater-mode table: the DM's TABLE line sets zones, places and moves
monsters, and is refused whatever breaks the rules (a person's character, out of turn, too far, new zones
mid-fight); players move and engage on their own turn, through the API, with their seat keys.

    python3 table/tests/dnd_zones_test.py      (no network: the DM is a script)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import keepalive  # noqa: E402
keepalive.install()
os.environ.pop("DIVINCI_FUSION_API_KEY", None)
sys.argv = ["dnd_server.py", "--players", "Michael,Sam", "--companions", "ai:Leonardo:chatty:wizard",
            "--mode", "theater", "--port", "0"]
import dnd_server as D  # noqa: E402
import dnd_zones as Z  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


print("the rules")
c = Z.new_card()
check("zones without 'next' form a line", not Z.set_zones(c, [{"name": "the bar"}, {"name": "the floor"}, {"name": "the door"}, {"name": "the street"}])
      and Z.zone(c, "the floor")["next"] == ["the bar", "the door"])
check("a line: the bar is 3 zones from the street", Z.hops(c, "the bar", "the street") == 3)
c2 = Z.new_card()
Z.set_zones(c2, [{"name": "A", "next": ["B", "nowhere"]}, {"name": "B"}, {"name": "C", "next": ["A"]}, {"name": "a"}])
check("explicit links go both ways, unknown names are dropped, duplicate names too",
      sorted(Z.zone(c2, "A")["next"]) == ["B", "C"] and Z.zone(c2, "B")["next"] == ["A"] and len(c2["zones"]) == 3, json.dumps(c2["zones"]))
cz = Z.new_card(); Z.set_zones(cz, [{"name": str(i)} for i in range(20)])
check(f"…and the rest are dropped ({len(cz['zones'])})", len(cz["zones"]) == Z.MAX_ZONES)
check("an empty zone list is refused", Z.set_zones(Z.new_card(), []) is not None)
for who, z in (("Ana", "the bar"), ("Bandit", "the bar"), ("Ben", "the floor"), ("Wolf", "the street")):
    Z.place(c, who, z)
check("unknown zone refused when placing", Z.place(c, "Cy", "the moon") is not None)
check("range: same zone is near, next zone near, three away far",
      Z.range_between(c, "Ana", "Bandit") == "near" and Z.range_between(c, "Ana", "Ben") == "near" and Z.range_between(c, "Ana", "Wolf") == "far")
check("engage needs the same zone", Z.engage(c, "Ana", "Ben") is not None and not Z.engage(c, "Ana", "Bandit"))
check("…and then they're engaged", Z.range_between(c, "Ana", "Bandit") == "engaged" and Z.engaged_with(c, "Bandit") == ["Ana"])
err, note = Z.move(c, "Ana", "the floor", in_turn=True)
check("leaving a foe is allowed, and the opportunity attack is noted", err is None and "Bandit" in note and not Z.engaged_with(c, "Ana"), note)
check("in a turn, a second zone needs a Dash", Z.move(c, "Ana", "the door", in_turn=True)[0] is not None)
check("…with a Dash she gets one more", Z.move(c, "Ana", "the door", dash=True, in_turn=True)[0] is None and c["where"]["Ana"] == "the door")
check("…and no further this turn", Z.move(c, "Ana", "the street", in_turn=True)[0] is not None)
Z.new_turn(c, "Ana")
check("a new turn resets her move", Z.move(c, "Ana", "the street", in_turn=True)[0] is None)
check("three zones in one turn is refused even with a Dash (the bar → the street)", Z.move(c, "Bandit", "the street", dash=True, in_turn=True)[0] is not None)
check("out of combat people go where they like", Z.move(c, "Ben", "the street")[0] is None)
c3 = Z.new_card(); Z.set_zones(c3, [{"name": "isle", "next": []}, {"name": "shore", "next": ["dock"]}, {"name": "dock"}])
Z.place(c3, "Ana", "isle")
check("no way between unlinked zones", Z.move(c3, "Ana", "dock")[0] is not None)
Z.set_zones(c, [{"name": "the bar", "desc": "a long oak counter", "cover": "half"}, {"name": "the door"}])
check("new zones keep only creatures whose zone still exists, and clear engagements",
      set(c["where"]) == {"Bandit"} and c["engaged"] == [] and c["moved"] == {}, json.dumps(c["where"]))
Z.place(c, "Ana", "the bar"); Z.place(c, "Wolf", "the door"); Z.engage(c, "Ana", "Bandit")
d = Z.describe(c, "Ana")
check("'where am I?' says where, what it's like, the fight, the cover, the way on, and who's nearby",
      all(x in d for x in ("the bar", "oak counter", "fighting Bandit", "half cover", "reach the door", "Wolf in the door")), d)

print("\na theater-mode table: the DM's TABLE line")
D.new_game()
G = D.G
check("the room is in theater mode, the card is public", D.public_state()["mode"] == "theater" and "card" in D.public_state())
t = D.apply_table_line('The tavern is loud.\nTABLE: {"zones":[{"name":"the bar","cover":"half"},{"name":"the hearth"},{"name":"the door"}],'
                       '"monsters":[{"name":"Bandit 1"},{"name":"Bandit 2"}],"zone":{"Bandit 2":"the hearth"}}')
w = G["card"]["where"]
check("the TABLE line is stripped from what players hear", t == "The tavern is loud.")
check("the party starts in the first zone; monsters in the last unless placed",
      w["Michael"] == w["Sam"] == w["Leonardo"] == "the bar" and w["Bandit 1"] == "the door" and w["Bandit 2"] == "the hearth", json.dumps(w))
D.apply_table_line('x\nTABLE: {"zone":{"Michael":"the door","Leonardo":"the hearth"}}')
check("the DM never moves a person's character (but may move a companion)", w["Michael"] == "the bar" and w["Leonardo"] == "the hearth")
D.apply_table_line('x\nTABLE: {"engage":[["Michael","Sam"],["Bandit 1","Sam"],["Bandit 2","Leonardo"]]}')
check("engage: not two people, not across zones, yes in the same zone",
      G["card"]["engaged"] == [["Bandit 2", "Leonardo"]], json.dumps(G["card"]["engaged"]))
G["initiative"] = {"active": True, "pending": [], "turn": 0, "round": 1,
                   "order": [{"name": "Bandit 1", "total": 18, "dex": 12}, {"name": "Michael", "total": 12, "dex": 10},
                             {"name": "Bandit 2", "total": 9, "dex": 12}]}
D.apply_table_line('x\nTABLE: {"zones":[{"name":"the moon"}]}')
check("no new zones mid-fight", [z["name"] for z in G["card"]["zones"]] == ["the bar", "the hearth", "the door"])
D.apply_table_line('x\nTABLE: {"zone":{"Bandit 2":"the door"}}')
check("in combat, only the creature whose turn it is moves", w["Bandit 2"] == "the hearth")
D.apply_table_line('x\nTABLE: {"zone":{"Bandit 1":"the bar"}}')
check("…and only as far as a move takes it (the door to the bar is 2 zones)", w["Bandit 1"] == "the door")
D.apply_table_line('x\nTABLE: {"zone":{"Bandit 1":{"to":"the bar","dash":true}}}')
check("…or a Dash", w["Bandit 1"] == "the bar")
p = D.build_prompt("test", [])
check("the DM's prompt carries the scene card and the theater rules, not the grid",
      '"scene_card"' in p and "theater of the mind" in p and '"grid"' not in p)

print("\nplayers, through the API")
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


mk = call("/api/seat/claim", {"name": "Michael"}, "phone-a")[1].get("key")
sk = call("/api/seat/claim", {"name": "Sam"}, "phone-b")[1].get("key")
code, out = call("/api/dnd/zone/move", {"by": "Sam", "key": sk, "zone": "the hearth"}, "phone-b")
check("not your turn: refused", code == 409 and "turn" in out.get("error", ""), json.dumps(out))
G["initiative"]["turn"] = 1                                             # Michael's turn
code, out = call("/api/dnd/zone/move", {"by": "Michael", "key": mk, "zone": "the door"}, "phone-a")
check("your turn, two zones without a Dash: refused", code == 409 and "Dash" in out.get("error", ""), json.dumps(out))
code, out = call("/api/dnd/zone/move", {"by": "Michael", "key": mk, "zone": "the hearth"}, "phone-a")
check("your turn, the next zone: moved, and the others see it", code == 200 and out["card"]["where"]["Michael"] == "the hearth", json.dumps(out.get("error")))
code, out = call("/api/dnd/zone/move", {"by": "Michael", "key": mk, "zone": "the bar", "who": "Sam"}, "phone-a")
check("'who' is ignored for a player: Michael moves only himself", code == 409 or out["card"]["where"]["Sam"] == "the bar")
code, out = call("/api/dnd/zone/engage", {"by": "Michael", "key": mk, "target": "Bandit 2"}, "phone-a")
check("engage a foe in your zone", code == 200 and ["Bandit 2", "Michael"] in out["card"]["engaged"], json.dumps(out.get("error")))
code, out = call("/api/dnd/zone/move", {"by": "Michael", "key": "wrong", "zone": "the bar"}, "phone-a")
check("a wrong seat key is refused", code == 403)
code, out = call("/api/dnd")
check("the public state carries the card", code == 200 and out["card"]["where"]["Michael"] == "the hearth")

srv.shutdown()
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
