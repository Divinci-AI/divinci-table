"""GET /api/card?name= knows every card in play, not only the downloaded Oracle file (2026-11-14, retro E9).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/card_lookup_test.py

On 2026-11-13 the table's card lookup answered 404 for Borg Cube, Unexplored Territory and Risa: the drafted set was newer than the Oracle
file on the host's laptop, so the host's tooling could not read a type line. The decks carried the card data all along. The stand-ins
here are cards no Oracle file can know ("Zzyx Test Outpost", "Quux Test Probe"), so the test means the same on every machine.

Proved, on scratch tables on free ports:
  1. the order (table/cardlookup.py): Oracle, then the configured set file, then a card everyone can see, then a deck;
  2. with NO set file: a card that is only in a deck answers 404 to an anonymous caller (a deck is hidden until it is played: an open
     lookup would hand out the other player's deck list), 200 to the host, 200 to the seat that owns the deck, 404 to the other seat;
  3. the moment the card is on the battlefield it answers everyone, with its type line and text;
  4. WITH the set file (TABLE_CARDS_JSON): every card of the set answers everyone, in a deck or not: that tells nobody anything.
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
import cardlookup  # noqa: E402
import match_bo3_test_lib as L  # noqa: E402

R, check, C, M = L.R, L.check, L.C, L.M
OUTPOST = {"name": "Zzyx Test Outpost", "manaCost": "", "manaValue": 0.0, "type": "Land — Planet", "types": ["Land"], "subtypes": ["Planet"], "supertypes": [],
           "text": "{T}: Add {C}.", "power": None, "toughness": None, "colors": [], "colorIdentity": []}
PROBE = {"name": "Quux Test Probe", "manaCost": "{1}", "manaValue": 1.0, "type": "Artifact Creature — Probe", "types": ["Artifact", "Creature"], "subtypes": ["Probe"],
         "supertypes": [], "text": "Flying", "power": "1", "toughness": "1", "colors": [], "colorIdentity": []}

print("1. The order of the sources", flush=True)
oracle = {"Mountain": {"cost": "", "type": "Basic Land — Mountain", "text": "({T}: Add {R}.)", "power": None, "toughness": None}}
rec, src = cardlookup.lookup("Mountain", oracle.get, [OUTPOST], [PROBE])
check(src == "oracle" and rec["type"] == "Basic Land — Mountain", "a card the Oracle file knows comes from it", (rec, src))
rec, src = cardlookup.lookup("zzyx test outpost", oracle.get, [OUTPOST], [PROBE])
check(src == "table" and rec == {"name": "Zzyx Test Outpost", "cost": "", "type": "Land — Planet", "text": "{T}: Add {C}.", "power": None, "toughness": None},
      "a card everyone can see answers with its own data, whatever the case of the name", (rec, src))
rec, src = cardlookup.lookup("Quux Test Probe", oracle.get, [OUTPOST], [PROBE])
check(src == "deck" and rec["power"] == "1" and rec["text"] == "Flying", "a deck card answers a caller who may know the deck", (rec, src))
check(cardlookup.lookup("Quux Test Probe", oracle.get, [OUTPOST], []) == (None, ""), "…and nobody else")
check(cardlookup.lookup("", oracle.get) == (None, "") and cardlookup.lookup("No Such Card", oracle.get, [OUTPOST], [PROBE]) == (None, ""), "an unknown name is nothing")
two = cardlookup.from_scryfall({"name": "A // B", "card_faces": [{"mana_cost": "{U}", "type_line": "Instant", "oracle_text": "one"}, {"oracle_text": "two"}]})
check(two == {"cost": "{U}", "type": "Instant", "text": "one\n//\ntwo", "power": None, "toughness": None}, "a two-faced card of a set file: the front face's cost and type, both texts", two)


def decks(name, life=20):
    """Claude's deck holds the Outpost and the Probe; Michael's holds neither."""
    f = R.TMP / f"lookup-{name}.json"
    board = [{**OUTPOST, "count": 30}, {**PROBE, "count": 10}] if name == C else [R.shim.entry(dict(L.RAW["Mountain"]), 40)]
    f.write_text(json.dumps({"name": name, "commander": None, "startingLife": life, "mainBoard": board}))
    return str(f)


def card(t, name, key=None, host=False, seat=None):
    q = "/api/card?name=" + urllib.parse.quote(name) + (f"&seat={seat}" if seat else "")
    return t.call("GET", q, key=key, host=host)


L.deck40 = decks
print("2. No set file: a deck is hidden until it is played", flush=True)
t = L.Table(env={"TABLE_CARDS_JSON": "", "DRAFT_CARDS_JSON": ""})
try:
    t.open_game(by=C)
    c, d = card(t, "Mountain")
    check(c == 200 and d["source"] == "oracle" and "Mountain" in d["type"], "a card the Oracle file knows: as before", (c, d))
    check(card(t, "Zzyx Test Outpost")[0] == 404, "a card that is only in a deck: 404 for an anonymous caller")
    check(card(t, "Quux Test Probe", key=t.keys[M], seat=M)[0] == 404, "…and for the OTHER seat, with its own key")
    check(card(t, "Quux Test Probe", key=t.keys[M], seat=C)[0] == 404, "…and for the other seat naming the owner's seat")
    c, d = card(t, "Quux Test Probe", key=t.keys[C], seat=C)
    check(c == 200 and d["source"] == "deck" and d["type"] == "Artifact Creature — Probe" and d["power"] == "1", "the seat that owns the deck reads its own card", (c, d))
    c, d = card(t, "Zzyx Test Outpost", host=True)
    check(c == 200 and d["source"] == "deck" and d["type"] == "Land — Planet" and d["text"] == "{T}: Add {C}.", "the table's host reads any deck's card (the host's tooling)", (c, d))
    check(card(t, "No Such Card", host=True)[0] == 404, "a name nobody has: 404, also for the host")

    print("3. On the battlefield it answers everyone", flush=True)
    c, d = t.call("POST", "/api/brain/land", {"seat": C, "name": "Zzyx Test Outpost"}, key=t.keys[C])
    check(c == 200, "Claude plays the Outpost", (c, d))
    c, d = card(t, "Zzyx Test Outpost")
    check(c == 200 and d == {"name": "Zzyx Test Outpost", "cost": "", "type": "Land — Planet", "text": "{T}: Add {C}.", "power": None, "toughness": None, "source": "table"},
          "an anonymous caller now reads its type line and text", (c, d))
    check(card(t, "zzyx test outpost", key=t.keys[M])[0] == 200, "…a remote seat too, in any case")
    check(card(t, "Quux Test Probe")[0] == 404, "the card still in Claude's hand and library stays hidden")
finally:
    t.stop()

print("4. With the set file, the whole set answers everyone", flush=True)
setfile = R.TMP / "set.json"
setfile.write_text(json.dumps([{"name": "Quux Test Probe", "mana_cost": "{1}", "type_line": "Artifact Creature — Probe", "oracle_text": "Flying", "power": "1", "toughness": "1"},
                               {"name": "Blip Test Anomaly", "mana_cost": "", "type_line": "Land — Anomaly", "oracle_text": "{T}: Add {U}."}]))
t = L.Table(env={"TABLE_CARDS_JSON": str(setfile), "DRAFT_CARDS_JSON": ""})
try:
    t.claim()
    c, d = card(t, "Quux Test Probe")
    check(c == 200 and d["source"] == "set" and d["type"] == "Artifact Creature — Probe" and d["cost"] == "{1}", "a set card that is in a deck", (c, d))
    c, d = card(t, "Blip Test Anomaly")
    check(c == 200 and d["source"] == "set" and d["type"] == "Land — Anomaly", "a set card that is in NO deck answers the same way (so a 200 says nothing about a deck)", (c, d))
    check(card(t, "Zzyx Test Outpost")[0] == 404, "a deck card that is not in the set file stays hidden")
finally:
    t.stop()

print("\nFAILED: " + "; ".join(R.FAILS) if R.FAILS else "\nall good")
sys.exit(1 if R.FAILS else 0)
