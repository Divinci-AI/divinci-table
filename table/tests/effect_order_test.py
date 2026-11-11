"""Effects happen in the order the card says, around a question (live bug, 2026-11-09): Test of Priorities' "Surveil 2, then draw a card" drew
before the surveil was answered, so the draw took a card that should have been surveilled. Now a sentence that leaves the seat a question
(a surveil, a target to name) stops the effect; the rest waits on the seat and runs right after the answer. Also Power to Life Support:
exile your creature, back under its owner's control at the beginning of the next end step. Tables of their own on free ports (never a live
game's), the seat's own key; the library stacked so the exact card drawn is known.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 ~/.venvs/table/bin/python table/tests/effect_order_test.py
"""
from __future__ import annotations

import json
import pickle
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import impulse_test as I  # noqa: E402  (free ports never a live game's; decks from the set)
import player  # noqa: E402

R, check, M, C = I.R, I.R.check, "Michael", "Claude"
R.POOL.update({"Test of Priorities": 6})
SEER = {"name": "Test Seer", "count": 4, "manaCost": "{U}", "manaValue": 1.0, "type": "Creature — Human Wizard", "types": ["Creature"],
        "subtypes": ["Human", "Wizard"], "supertypes": [], "text": "When this creature enters, surveil 1, then draw a card.", "power": "1",
        "toughness": "1", "colors": ["U"], "colorIdentity": ["U"]}


def deck(name):
    f = Path(R.deck_file(name))
    d = json.loads(f.read_text())
    d["mainBoard"].append(SEER)                          # (no "enters: surveil, then draw" card in the set: a plain one)
    f.write_text(json.dumps(d))
    return str(f)


R.DECK_MICHAEL, R.DECK_CLAUDE = deck("Michael-order"), deck("Claude-order")


def hand_count(t, seat, name):
    return sum(1 for h in t.state(seat).get("hand", []) if h["name"] == name)


def gy(t, seat):
    return t.state(seat).get("graveyard", [])


def engine_order():
    print("Engine: the draw waits for the surveil, and a deferred effect survives a snapshot")
    v = I.seat(lands=0)
    v.library = [I.card("Moopsy"), I.card("Mugato"), I.card("Horta")]     # top: Horta, then Mugato, then Moopsy
    said, left = v.run_effects(None, "Surveil 2, then draw a card.")
    check(v.__dict__.get("pending_surveil") == 2 and not v.hand and len(v.library) == 3, "surveil 2 asks; the draw has not happened", (said, len(v.hand)))
    w = pickle.loads(pickle.dumps(v))
    for _ in range(2):                                   # (what the surveil action does: both into the graveyard)
        w.graveyard.append(w.library.pop())
    w.__dict__["pending_surveil"] = 0
    more = w.resume_deferred()
    check([c["name"] for c in w.hand] == ["Moopsy"] and more, "after a snapshot and the answer, the draw happens: the card that was third", ([c["name"] for c in w.hand], more))


def table_priorities():
    print("Table: Test of Priorities")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 4, "Island")
        for keep, want in ((False, "Moopsy"), (True, "Horta")):
            t.hand(M, "Test of Priorities")
            I.stack_top(t, M, ["Horta", "Mugato", "Moopsy"])
            n0 = {x: hand_count(t, M, x) for x in ("Horta", "Mugato", "Moopsy")}
            g0 = len(gy(t, M))
            c, d = t.act(M, "cast", name="Test of Priorities", modes=[1])
            st = t.state(M)
            check(c == 200 and st.get("pending_surveil") == 2 and st.get("surveil_cards") == ["Horta", "Mugato"] and {x: hand_count(t, M, x) for x in n0} == n0,
                  "Power to Engines: surveil 2 waits on Michael, and nothing has been drawn yet", (c, d, st.get("surveil_cards")))
            c, d = t.act(M, "surveil", graveyard=[] if keep else ["Horta", "Mugato"])
            n1 = {x: hand_count(t, M, x) for x in n0}
            drew = [x for x in n0 if n1[x] == n0[x] + 1]
            check(c == 200 and drew == [want] and sum(n1.values()) == sum(n0.values()) + 1,
                  f"…{'both kept' if keep else 'both binned'}: the draw comes after, and takes {want}", (c, d, drew))
            if not keep:
                check("Horta" in gy(t, M)[g0:] and "Mugato" in gy(t, M)[g0:], "…and the two binned are in the graveyard", gy(t, M)[g0:])
            t.turn(C)
            t.turn(M)
        # Power to Life Support
        h = t.onto(M, "Horta")
        t.hand(M, "Test of Priorities")
        c, d = t.act(M, "cast", name="Test of Priorities", modes=[2], targets=[{"at": M, "ref": f"#{h}"}])
        check(c == 200 and t.perm(M, "Horta") is None, "Power to Life Support: Horta is exiled", (c, d))
        ev0 = I.last_event(t)
        c, d = t.act(M, "end", text="over")
        back = t.perm(M, "Horta")
        said = " ".join(str(e.get("text")) for e in t.events() if e["id"] > ev0) + " ".join(d.get("said") or [])
        check(c == 200 and back is not None and back["id"] != h and "returns to the battlefield" in said,
              "…and returns at the beginning of the next end step (a new object)", (c, d, back))
    finally:
        t.stop()


def table_etb():
    print("Table: an enters trigger that surveils, then draws")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 1, "Island")
        t.hand(M, "Test Seer")
        I.stack_top(t, M, ["Horta", "Mugato"])
        n0 = {x: hand_count(t, M, x) for x in ("Horta", "Mugato")}
        c, d = t.act(M, "cast", name="Test Seer")
        st = t.state(M)
        check(c == 200 and st.get("pending_surveil") == 1 and {x: hand_count(t, M, x) for x in n0} == n0, "it enters: surveil 1 waits, no draw yet", (c, d))
        c, d = t.act(M, "surveil", graveyard=["Horta"])
        n1 = {x: hand_count(t, M, x) for x in n0}
        check(c == 200 and n1["Mugato"] == n0["Mugato"] + 1 and n1["Horta"] == n0["Horta"], "Horta binned, then the draw: Mugato", (c, d, n1))
    finally:
        t.stop()


def main():
    for fn in (engine_order, table_priorities, table_etb):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {fn.__name__}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
