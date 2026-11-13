"""2026-11-13 retro, C2: the legal list and a "Choose two —" spell.

Seen at the table: Austere Command was listed as `cast {"name": "Austere Command"}`; sending exactly that got 400 "Austere Command is modal —
choose 2 with --mode", and the message named tablectl's flag while the JSON field is `modes`.

Now /api/seat/legal lists one entry per legal combination, each with "modes": [i, j] (and the first legal targets when a chosen mode aims at
something; a combination with a mode that has nothing to aim at is left out), and the refusal names the JSON field too.

A real table on a free port; everything under test goes through the seat's own key. Before the fix (git stash -- table/server.py
table/player.py) the "one entry per pair", "every entry names two modes", "accepted unchanged" and "names the JSON field" checks fail.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro_1113_modal_legal_test.py
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
AUSTERE, CRYPTIC = "Austere Command", "Cryptic Command"
ALL = {}
for c in json.load(open(cards_path.cards_json())):          # the set and its Commander decks (Austere Command is a Commander-deck card)
    ALL.setdefault(c["name"], c)
if AUSTERE not in ALL:
    cards_path.skip(f"needs {AUSTERE} in the card data (the Star Trek Commander cards, set trc)")
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830, 8883))   # never a live game's port


def deck(name, pool):
    f = R.TMP / f"{name}.json"
    f.write_text(json.dumps({"name": name, "commander": None, "startingLife": 20, "mainBoard": [R.shim.entry(dict(ALL[n]), k) for n, k in pool.items()]}))
    return str(f)


POOL = {"Plains": 30, "Island": 30, "Horta": 6, "Tricorder": 6, AUSTERE: 6, **({CRYPTIC: 6} if CRYPTIC in ALL else {})}
R.DECK_MICHAEL = deck("Michael-modal", POOL)
R.DECK_CLAUDE = deck("Claude-modal", POOL)


def casts(t, name):
    return [a for a in t.legal(M).get("actions", []) if a["action"] == "cast" and a["body"].get("name") == name]


def main_():
    t = R.Table()
    try:
        t.start()
        t.lands(M, 8, "Plains")
        t.lands(M, 6, "Island")
        t.onto(C, "Horta")
        t.onto(M, "Tricorder")
        t.hand(M, AUSTERE)
        t.hand(M, AUSTERE)

        print("\n── Austere Command in the legal list")
        es = casts(t, AUSTERE)
        check(len(es) == 6, "one entry per pair of its four modes (six)", [e["body"] for e in es])
        check(all(isinstance(e["body"].get("modes"), list) and len(e["body"]["modes"]) == 2 for e in es), "every entry names exactly two modes", [e["body"] for e in es])
        check(sorted(tuple(e["body"].get("modes") or ()) for e in es) == [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)], "…each pair once, 1-based, in the card's order", [e["body"].get("modes") for e in es])
        check(all("modes" in e.get("why", "") or "/" in e.get("why", "") for e in es), "the entry says which modes it is", [e.get("why") for e in es][:2])

        print("\n── the refusal names the JSON field")
        c, d = t.act(M, "cast", name=AUSTERE)
        err = str(d.get("error"))
        check(c == 400 and '"modes"' in err and "--mode" in err, "cast without modes: 400, naming \"modes\" (JSON) and --mode (tablectl)", (c, err))
        check("choose 2 with --mode (1-based): 1." in err, "…and the part the fusion seat's parser reads keeps its shape", err)
        check(len([p for p in t.state(M)["permanents"] if p.get("tapped")]) == 0, "…and nothing was paid for the refused cast")

        print("\n── a listed entry is accepted exactly as listed")
        pick = next(e for e in es if e["body"]["modes"] == [1, 4])        # destroy all artifacts + all creatures with mana value 4 or greater
        c, d = t.call("POST", "/api/brain/cast", pick["body"], key=t.keys[M])
        check(c == 200, "POST of the listed body, unchanged: accepted", (c, d))
        said = " ".join(str(e.get("text") or "") for e in t.events())
        check("I choose: Destroy all artifacts. — and — Destroy all creatures with mana value 4 or greater." in said, "…and the log says both chosen modes", said[-400:])
        # (What the modes DO is not this fix: "destroy all …" is still carried out by hand at this table; the log and the to-do list say so.)
        es2 = casts(t, AUSTERE)
        if es2:                                                           # the second copy, another pair, if eight more mana is there (it is: 14 lands)
            pick2 = next(e for e in es2 if e["body"]["modes"] == [2, 3])
            c, d = t.call("POST", "/api/brain/cast", pick2["body"], key=t.keys[M])
            check(c == 200, "a second listed pair, unchanged: accepted too", (c, d))
        else:
            check(False, "the second copy is still listed while it can be paid for", t.state(M).get("mana_sources"))

        if CRYPTIC in ALL:
            print("\n── Cryptic Command: a pair is listed only if each chosen mode has something legal to aim at")
            t.turn(M)
            t.hand(M, CRYPTIC)
            es = casts(t, CRYPTIC)
            check(es and all(len(e["body"].get("modes") or []) == 2 for e in es), "listed, every entry with two modes", [e["body"] for e in es])
            check(all(1 not in e["body"]["modes"] for e in es), "no pair with 'Counter target spell' while there is no spell to counter", [e["body"]["modes"] for e in es])
            pick = next((e for e in es if e["body"]["modes"] == [3, 4]), es[0] if es else None)
            if pick:
                c, d = t.call("POST", "/api/brain/cast", pick["body"], key=t.keys[M])
                check(c == 200, f"its listed body {pick['body'].get('modes')}, unchanged: accepted", (c, d))
    finally:
        t.stop()


def main():
    try:
        main_()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        check(False, "the test ran", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
