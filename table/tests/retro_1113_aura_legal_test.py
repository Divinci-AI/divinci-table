"""2026-11-13 retro, C3: an Aura meant for an OPPONENT's creature at a table where every seat is virtual.

Seen in 38 soak games: Vow of Flight, Vow of Wildness and Kenrith's Transformation never once appeared in a legal list. VirtualPlayer.castable
asked `public_board` (what real-deck players announced), which is always empty when every seat is virtual, so the cards were never castable.

Now they are castable when another seat, virtual or not, controls a creature, and /api/seat/legal lists one entry per enemy creature (as it does
per own creature for a friendly Aura).

Two pilot seats on a real table on a free port; everything under test goes through the seat's own key. The three cards are not in the Star Trek
set: their rules text is written here (card rules, no art). Before the fix (git stash -- table/server.py table/player.py) the "castable",
"one entry per enemy creature" and "accepted unchanged" checks fail.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro_1113_aura_legal_test.py
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import cards_path  # noqa: E402
import retro_game4_test as R  # noqa: E402

check = R.check
M, C = "Michael", "Claude"
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830, 8883))   # never a live game's port


def aura(name, cost, mv, text, colors):
    return {"name": name, "manaCost": cost, "manaValue": float(mv), "type": "Enchantment — Aura", "types": ["Enchantment"], "subtypes": ["Aura"], "supertypes": [],
            "text": text, "power": None, "toughness": None, "colors": colors, "colorIdentity": colors}


AURAS = [aura("Vow of Flight", "{2}{U}", 3, "Enchant creature\nEnchanted creature gets +2/+2, has flying, and can't attack you or planeswalkers you control.", ["U"]),
         aura("Vow of Wildness", "{2}{G}", 3, "Enchant creature\nEnchanted creature gets +3/+3, has trample, and can't attack you or planeswalkers you control.", ["G"]),
         aura("Kenrith's Transformation", "{1}{G}", 2, "Enchant creature\nWhen this Aura enters, draw a card.\nEnchanted creature loses all abilities and is a green Elk creature with base power and toughness 3/3.", ["G"])]
SET = {}
for c in json.load(open(cards_path.cards_json())):
    if c.get("set") == "trk":
        SET.setdefault(c["name"], c)


def deck(name):
    main = [R.shim.entry(dict(SET[n]), k) for n, k in {"Island": 20, "Forest": 20, "Horta": 6, "Moopsy": 6, "Xindi Surveyors": 6}.items()]
    main += [{**a, "count": 4} for a in AURAS]
    f = R.TMP / f"{name}.json"
    f.write_text(json.dumps({"name": name, "commander": None, "startingLife": 20, "mainBoard": main}))
    return str(f)


R.DECK_MICHAEL = deck("Michael-aura")
R.DECK_CLAUDE = deck("Claude-aura")


def casts(t, name):
    return [a for a in t.legal(M).get("actions", []) if a["action"] == "cast" and a["body"].get("name") == name]


def main_():
    t = R.Table()
    try:
        t.start()
        t.lands(M, 4, "Island")
        t.lands(M, 4, "Forest")
        for a in AURAS:
            t.hand(M, a["name"])
        t.onto(M, "Xindi Surveyors")

        print("\n── no enemy creature yet")
        for a in AURAS:
            check(not casts(t, a["name"]), f"{a['name']}: not listed while no other seat has a creature", casts(t, a["name"]))

        print("\n── two enemy creatures, both seats virtual")
        t.onto(C, "Horta")
        t.onto(C, "Moopsy")
        hand = {h["name"]: h for h in t.state(M)["hand"]}
        for a in AURAS:
            n = a["name"]
            check(hand[n].get("castable_now") is True, f"{n}: the seat's own state says it is castable now", {k: hand[n].get(k) for k in ("castable_now", "why_not")})
            es = casts(t, n)
            check(sorted(e["body"].get("on") for e in es) == ["Horta", "Moopsy"], f"{n}: one entry per enemy creature", [e["body"] for e in es])
            check(all("Claude" in e.get("why", "") for e in es), "…each saying whose creature it is", [e.get("why") for e in es])
            check(not any("Xindi" in str(e["body"].get("on")) for e in es), "…and none for the caster's own creature")

        print("\n── a listed entry is accepted exactly as listed")
        pick = next(e for e in casts(t, "Vow of Flight") if e["body"]["on"] == "Horta")
        c, d = t.call("POST", "/api/brain/cast", pick["body"], key=t.keys[M])
        check(c == 200, "POST of the listed body, unchanged: accepted", (c, d))
        said = " ".join(str(e.get("text") or "") for e in t.events())
        check("It enchants Horta" in said, "…and the log says what it went on", said[-300:])
        pick = next(e for e in casts(t, "Kenrith's Transformation") if e["body"]["on"] == "Moopsy")
        c, d = t.call("POST", "/api/brain/cast", pick["body"], key=t.keys[M])
        check(c == 200, "a second one (Kenrith's Transformation on the other creature): accepted", (c, d))

    finally:
        t.stop()


def unit():
    print("\n── the engine alone: who counts as a foe")
    import player
    v = player.VirtualPlayer(R.DECK_MICHAEL, M, seed=3)
    v.hand[:] = [dict(AURAS[0])]
    for nm in ("Island", "Island", "Island"):
        v.battlefield.append(player.Perm(R.shim.entry(dict(SET[nm])), sick=False))
    keep = player.FOE_CREATURES[0]
    try:
        player.FOE_CREATURES[0] = None
        check(not [o for o in v.castable() if o[1]["name"] == "Vow of Flight"], "no table, nothing announced: not castable (as before)")
        v.public_board = ["Grizzly Bears"]
        check(bool([o for o in v.castable() if o[1]["name"] == "Vow of Flight"]), "a real-deck player's announced card still makes it castable (as before)")
        v.public_board = []
        player.FOE_CREATURES[0] = lambda seat: ["Horta"] if seat == M else []
        check(bool([o for o in v.castable() if o[1]["name"] == "Vow of Flight"]), "another virtual seat's creature makes it castable")
        player.FOE_CREATURES[0] = lambda seat: 1 / 0
        check(not [o for o in v.castable() if o[1]["name"] == "Vow of Flight"], "a table hook that fails means 'no target', not a crash")
    finally:
        player.FOE_CREATURES[0] = keep


def main():
    for fn in (unit, main_):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, "the test ran", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
