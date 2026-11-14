"""2026-11-13 retro, item 3 (the table's side): equipping from a seat's own key.

Seen at the table: Michael, on the hand page, said he had no way to equip (Type-2 Phaser and Tricorder on the battlefield, a new creature,
Targ Wrangler). The action existed and worked ("I pay {3} to equip Type-2 Phaser to Ghost of Illyria" earlier in the same game). What this
file pins, on a real table on a free port, is everything the page now leans on:

  * the seat's state lists, per Equipment, `equip_targets` (the creatures it may go onto: new), `equip_can` and `equip_why`;
  * the reason the buttons vanished: with the lands tapped (the page's mana pool taps them for real) the table says
    equip_can False "needs {3}: …", and the SAME request succeeds once those lands are untapped again (which the page does before it asks);
  * re-equipping moves the Equipment and pays again; every refusal changes nothing and says why (timing, mana, not a creature, not mine,
    already on it, an Aura is not Equipment: the engine has no way to move an Aura, so the page does not offer one).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro_1113_equip_test.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import retro_game4_test as R  # noqa: E402

check = R.check
M, C = "Michael", "Claude"
R.POOL.update({"Ghost of Illyria": 6, "Targ Wrangler": 6, "Type-2 Phaser": 6, "Tricorder": 6, "De-evolve": 6, "Moopsy": 6})
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830, 8883, 8891))   # never a live game's port
R.DECK_MICHAEL = R.deck_file("Michael-equip")
R.DECK_CLAUDE = R.deck_file("Claude-equip")


def gear(t, pid):
    return next(p for p in t.state(M)["permanents"] if p["id"] == pid)


def pt(t, pid):
    return next(p for p in t.state(M)["permanents"] if p["id"] == pid).get("pt")


def untapped(t):
    return sum(1 for p in t.state(M)["permanents"] if "Land" in (p.get("type") or "") and not p["tapped"])


def main_():
    t = R.Table()
    try:
        t.start()                                            # Michael's main 1
        for n in ("Mountain", "Mountain", "Mountain", "Plains"):
            t.onto(M, n)
        ghost = t.onto(M, "Ghost of Illyria")
        phaser = t.onto(M, "Type-2 Phaser")
        t.decline(M)
        tric = t.onto(M, "Tricorder")
        foe = t.onto(C, "Moopsy")
        base = pt(t, ghost)

        print("\n── what the seat's state says")
        g = gear(t, phaser)
        check(g.get("equip_cost") == "{3}" and g.get("equip_can") is True and g.get("equip_targets") == [ghost], "Type-2 Phaser: Equip {3}, can, onto Ghost of Illyria", {k: g.get(k) for k in ("equip_cost", "equip_can", "equip_why", "equip_targets")})
        check(gear(t, tric).get("equip_targets") == [ghost], "Tricorder: the same one target (never the opponent's Moopsy, a land, or itself)", gear(t, tric).get("equip_targets"))
        check("equip_targets" not in gear(t, ghost), "a creature carries no equip list")

        print("\n── equip, then a new creature arrives")
        c, d = t.act(M, "equip", equipment=f"#{phaser}", creature=f"#{ghost}")
        p0, tg0 = (int(x) for x in base.split("/"))
        check(c == 200 and "I pay {3} to equip Type-2 Phaser to Ghost of Illyria" in " ".join(d.get("said", [])) and pt(t, ghost) == f"{p0 + 3}/{tg0}", "the Phaser goes onto Ghost of Illyria (+3/+0), said in the table's words", (c, d.get("said"), pt(t, ghost)))
        check(gear(t, phaser)["attached_to"] == ghost and gear(t, phaser).get("equip_targets") == [], "it is on the Ghost; with one creature there is nowhere else for it to go", gear(t, phaser))
        t.turn(C)
        t.turn(M)                                            # a new turn of Michael's: the lands are untapped
        targ = t.onto(M, "Targ Wrangler")
        tbase = pt(t, targ)
        check(gear(t, phaser).get("equip_targets") == [targ] and sorted(gear(t, tric).get("equip_targets")) == sorted([ghost, targ]),
              "Targ Wrangler arrives: the Phaser may move onto it; the Tricorder may go onto either creature", (gear(t, phaser).get("equip_targets"), gear(t, tric).get("equip_targets")))
        legal = [a for a in t.legal(M)["actions"] if a["action"] == "equip"]
        check(len(legal) == 3 and all(a["body"].get("equipment") and a["body"].get("creature") for a in legal), "the legal list offers the three equips, each a complete request", [a["why"] for a in legal])

        print("\n── the lands are tapped first (what the page's mana pool does)")
        lands = [p["id"] for p in t.state(M)["permanents"] if p["name"] == "Mountain"]
        for pid in lands:
            cc, dd = t.act(M, "tap", ref=f"#{pid}")
            assert cc == 200, dd
        g = gear(t, phaser)
        check(g["equip_can"] is False and str(g["equip_why"]).startswith("needs {3}") and g["equip_targets"] == [targ],
              "the table now says: can't, 'needs {3}: …' (this is where the page's buttons used to vanish); the target list is still there", (g["equip_can"], g["equip_why"], g["equip_targets"]))
        check(gear(t, tric)["equip_can"] is True, "the Tricorder ({1}) can still be paid for with the one land left")
        c, d = t.act(M, "equip", equipment=f"#{phaser}", creature=f"#{targ}")
        check(c == 400 and "can't pay {3}" in str(d.get("error")) and gear(t, phaser)["attached_to"] == ghost and pt(t, targ) == tbase,
              "asked like that the table refuses, says why, and changes nothing", (c, d))
        for pid in lands:                                    # the page gives the pool back (untaps what it tapped) before it asks
            t.act(M, "untap", ref=f"#{pid}")
        c, d = t.act(M, "equip", equipment=f"#{phaser}", creature=f"#{targ}")
        tp, tt = (int(x) for x in tbase.split("/"))
        check(c == 200 and gear(t, phaser)["attached_to"] == targ and pt(t, targ) == f"{tp + 3}/{tt}" and pt(t, ghost) == base,
              "with the pool given back the same request moves the Phaser: Targ Wrangler +3/+0, Ghost of Illyria back to its own size", (c, d.get("said"), pt(t, targ), pt(t, ghost)))
        check(untapped(t) == 1, "moving it was paid for again ({3}: three lands tapped)", untapped(t))
        board = {p.get("id"): p for p in t.board(M).get("permanents", [])}
        check(board.get(phaser, {}).get("attached_to") == targ and "Type-2 Phaser" in str(board.get(targ, {}).get("attached_names")), "the public board shows it on Targ Wrangler", (board.get(phaser), board.get(targ)))

        print("\n── refusals: nothing changes, and the reason is said")
        def refused(what, needle, **body):
            before = (gear(t, phaser)["attached_to"], gear(t, tric)["attached_to"], untapped(t))
            c, d = t.act(M, "equip", **body)
            after = (gear(t, phaser)["attached_to"], gear(t, tric)["attached_to"], untapped(t))
            check(c in (400, 403, 404) and needle.lower() in str(d.get("error", "")).lower() and before == after, what, (c, d, before, after))
        refused("onto the creature it is already on: 'Nothing was paid'", "Nothing was paid", equipment=f"#{phaser}", creature=f"#{targ}")
        refused("onto a land: not a creature", "not a creature", equipment=f"#{tric}", creature=f"#{lands[0]}")
        refused("a creature is not Equipment", "is not Equipment", equipment=f"#{ghost}", creature=f"#{targ}")
        c, d = t.act(M, "equip", equipment=f"#{tric}", creature=f"#{foe}")
        check(c != 200 and gear(t, tric)["attached_to"] is None, "onto the opponent's creature: refused (Equip is for a creature you control)", (c, d))
        c, d = t.act(C, "equip", equipment=f"#{tric}", creature=f"#{foe}")
        check(c != 200 and gear(t, tric)["attached_to"] is None, "the other seat cannot use my Equipment", (c, d))
        aura = t.onto(M, "De-evolve")
        t.settle_setup()
        a = next((p for p in t.state(M)["permanents"] if p["id"] == aura), None)
        if a is not None:
            check(not a.get("equip_cost") and "equip_targets" not in a, "an Aura on the battlefield carries no equip cost and no target list (nothing for the page to offer)", a)
            refused("…and asking to 'equip' an Aura is refused: the engine has no move for an Aura", "is not Equipment", equipment=f"#{aura}", creature=f"#{ghost}")
        t.next_to(M, "declare attackers")
        g = gear(t, tric)
        check(g["equip_can"] is False and "main phase" in str(g["equip_why"]), "outside the main phase the state says so", (g["equip_can"], g["equip_why"]))
        refused("…and the request is refused with the same reason", "only as a sorcery", equipment=f"#{tric}", creature=f"#{ghost}")
    finally:
        t.stop()


def main():
    try:
        main_()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        check(False, "the test ran to its end", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
