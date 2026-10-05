"""The table speaks (docs/THEATER-GOAL.md T2), server side: the DM's streamed reply becomes spoken parts, one per
finished sentence, each with its voice (narrator, an AI companion's line, an NPC's line), never the TABLE line;
however the stream happens to be chopped up. And a whole DM turn through the script backend.

    python3 table/tests/dnd_voice_test.py      (no network)
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.pop("DIVINCI_FUSION_API_KEY", None)
SCRIPT = Path(tempfile.mkdtemp()) / "dm.txt"
REPLY = ('The tavern falls silent. A hooded figure rises from the corner table!\n'
         'Leonardo: Stay close. I sense old magic here.\n'
         'NPC Barkeep: No trouble in my house, friends.\n'
         'The fire crackles\n'
         'TABLE: {"scene":"The Sleeping Griffin","monsters":[{"name":"Bandit 1"}]}')
SCRIPT.write_text(REPLY + "\n---\nSecond reply. Short.")
sys.argv = ["dnd_server.py", "--players", "Michael,Sam", "--companions", "ai:Leonardo:chatty:wizard",
            "--dm-backend", f"script:{SCRIPT}", "--port", "0"]
import dnd_server as D  # noqa: E402

PASS = FAIL = 0
EXPECT = [("narrator", "The tavern falls silent."), ("narrator", "A hooded figure rises from the corner table!"),
          ("Leonardo", "Stay close."), ("Leonardo", "I sense old magic here."),
          ("NPC Barkeep", "No trouble in my house, friends."), ("narrator", "The fire crackles")]


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


def parts_from(chunks: list[str]) -> list[tuple[str, str]]:
    D.new_game()
    start = D.EVENTS.next_id
    sp = D.Spoken("t1")
    for c in chunks:
        sp.feed(c)
    sp.finish()
    return [(e["voice"], e["text"]) for e in D.EVENTS.items if e["id"] >= start and e["type"] == "dm_part"]


print("streamed reply → spoken parts")
rng = random.Random(7)
for label, chunks in (("one piece", [REPLY]), ("a character at a time", list(REPLY)),
                      ("random chunks", [REPLY[i:i + n] for i, n in
                                         ((i, rng.randint(1, 12)) for i in range(0, len(REPLY), 6))]),
                      ("word by word", [w for w in __import__("re").split(r"(\s+)", REPLY) if w])):
    if label == "random chunks":                              # rebuild exactly from random cut points
        cuts = sorted(rng.sample(range(1, len(REPLY)), 40))
        chunks = [REPLY[a:b] for a, b in zip([0] + cuts, cuts + [len(REPLY)])]
    got = parts_from(chunks)
    check(f"{label}: the right parts, voices and order", got == EXPECT, str(got))
got = parts_from([REPLY])
check("the TABLE line is never spoken", not any("TABLE" in t or "{" in t for _, t in got))
check("a narrator line with a colon stays the narrator's",
      parts_from(["Suddenly: a crash from the kitchen. Then silence.\n"])[0][0] == "narrator")
check("a short last line with no full stop is still spoken", parts_from(["Night falls"]) == [("narrator", "Night falls")])
ECHO = 'You reach a damp chamber.\nSTATE: {"scene": "old ruins", "party": [{"name": "Ana"}]}\nThe air grows cold.'
for label, chunks in (("whole", [ECHO]), ("a character at a time", list(ECHO))):
    got = parts_from(chunks)
    check(f"an echo of the prompt (STATE: …) is never spoken ({label})",
          got == [("narrator", "You reach a damp chamber."), ("narrator", "The air grows cold.")], str(got))
check("…nor shown in the log", "STATE" not in D.plain(ECHO) and "{" not in D.plain(ECHO))
BARE = 'You reach a damp corridor.\nzones:[{"name":"the corridor","desc":"cold stone"},{"name":"the chamber"}]\nWater drips.'
check("a bare 'zones:[…]' line is never spoken", parts_from([BARE]) == [("narrator", "You reach a damp corridor."), ("narrator", "Water drips.")], str(parts_from([BARE])))
D.new_game(); D.G["mode"] = "theater"
shown = D.apply_table_line(BARE)
check("…but it is applied: the scene card has the zones, and nothing of it is shown",
      [z["name"] for z in D.G["card"]["zones"]] == ["the corridor", "the chamber"] and "zones" not in shown and "Water drips." in shown, shown)

print("\na whole DM turn (script backend)")
D.new_game()
start = D.EVENTS.next_id
t0 = time.time()
D._dm_worker("prompt", [])
evs = [e for e in D.EVENTS.items if e["id"] >= start]
parts = [e for e in evs if e["type"] == "dm_part"]
full = [e for e in evs if e["type"] == "dm"]
said = [e for e in evs if e["type"] == "say"]
check("the parts went out, then the full reply for the log, under the same turn",
      len(parts) == 6 and full and full[0]["turn"] == parts[0]["turn"], str([e["type"] for e in evs]))
check("the companion's line also goes to the log as Leonardo's, marked with the turn (so nobody speaks it twice)",
      said and said[0]["by"] == "Leonardo" and said[0]["turn"] == parts[0]["turn"])
check("the TABLE line was applied (scene and monster)", D.G["scene"] == "The Sleeping Griffin" and D.G["monsters"][0]["name"] == "Bandit 1")
first = next(e for e in evs if e["type"] == "dm_part")
check(f"the first sentence was out {first['ts'] - t0:.2f} s in, before the reply finished ({full[0]['ts'] - t0:.2f} s)",
      first["ts"] < full[0]["ts"])
D._dm_worker("prompt", [])
check("the script backend plays its replies in turn", any(e["type"] == "dm_part" and e["text"] == "Second reply." for e in D.EVENTS.items))
check("a short reply is still split into sentences", parts_from(["Hi there. Run!"]) == [("narrator", "Hi there."), ("narrator", "Run!")])
check("a short companion line at the very end keeps its voice", parts_from(["The door opens.\nLeonardo: Careful."])[-1] == ("Leonardo", "Careful."))
check("a local backend runs the AI DM on the laptop with no Fusion key", D.ai_dm_enabled())

print("\nspoken commands (T3): dice and questions, and what must stay an action")
R = D.spoken_roll
check("'roll stealth with advantage'", R("Roll stealth with advantage") == {"dice": "d20", "mode": "adv", "why": "stealth"})
check("'roll a d20'", R("roll a d20") == {"dice": "d20", "mode": "", "why": ""})
check("'let me roll 2d6 for damage'", R("let me roll 2d6 for damage") == {"dice": "2d6", "mode": "", "why": "damage"})
check("'I rolled fourteen for perception' (a real die)", R("I rolled fourteen for perception") == {"dice": "d20", "mode": "", "why": "perception", "physical": [14]})
check("'I rolled twenty-one' isn't a d20", R("I rolled twenty-one") is None)
check("'I rolled a 17 and a 4 with disadvantage'", R("I rolled a 17 and a 4 with disadvantage") == {"dice": "d20", "mode": "dis", "why": "", "physical": [17, 4]})
for act in ("I rolled under the table.", "I roll the barrel down the stairs.", "I tell him to roll with it.",
            "The dice are rolling in my head", "I look for the sleight of hand trick he used"):
    check(f"an action stays an action: {act!r}", R(act) is None, str(R(act)))
D.new_game()
D.ZONES.set_zones(D.G["card"], [{"name": "the bar"}, {"name": "the door"}]); D.sync_zone_creatures()
Q = D.spoken_question
check("'where am I?' answers from the card", (Q("Michael", "Where am I?") or "").startswith("You're in the bar"))
check("'what's around me' too", (Q("Michael", "what's around me") or "").startswith("You're in the bar"))
check("'how hurt am I' is your own hit points", (Q("Michael", "how hurt am I") or "").startswith("You have "))
for act in ("I ask the barkeep where the caravan went.", "I turn around and draw my bow.", "It's my turn to buy a round!"):
    check(f"a line that isn't a question to the table stays an action: {act!r}", Q("Michael", act) is None, str(Q("Michael", act)))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
