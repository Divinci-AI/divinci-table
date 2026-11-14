"""2026-11-13 retro, item 2: The Destruction of Praxis, and the shape it belongs to.

    {3}{B}{B} sorcery. "Strange new worlds — All creatures get -X/-X until end of turn, where X is the number of differently named lands you
    control. If X is 6 or more, create two 2/1 red Klingon Warrior creature tokens with haste."

Seen at the table: both halves were "By hand", and an Oberth-Class Starship with 6 charge counters ("It's an artifact creature at 6+") was a
creature and should have died. Now the effect runner does the whole shape (player.py _one_effect0):

    <All creatures | All other creatures | Creatures you control> get ±N/±N until end of turn
        N a number, or X ", where X is the number of differently named lands you control"; then optionally "If X is N or more, <sentence>"

The set's cards of that shape: The Destruction of Praxis and Pah-Wraith (+X/+0, an attack trigger) are done; D'Ghor Bloodletter's "All other
creatures get -1/-1" was already done and now goes the same way. Toxic Deluge (X is a life payment) and Korok (X is opponents attacked) define
X differently: NOT claimed, they stay by hand, and that is checked here.

The engine on its own first, then one real table on a free port through the seats' own keys: both boards, the public board, the legal list,
the log, and the end of the turn.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro_1113_praxis_test.py
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
import player  # noqa: E402

check, CARDS = R.check, R.CARDS
M, C = "Michael", "Claude"
PRAXIS, OBERTH, PICARD, PAH = "The Destruction of Praxis", "Oberth-Class Starship", "Captain Jean-Luc Picard", "Pah-Wraith"
R.POOL.update({PRAXIS: 6, OBERTH: 6, PICARD: 6, PAH: 6, "Risa": 6, "Ferenginar": 6, "Moopsy": 6, "Xindi Surveyors": 6})
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830, 8883, 8891))   # never a live game's port
R.DECK_MICHAEL = R.deck_file("Michael-praxis")
R.DECK_CLAUDE = R.deck_file("Claude-praxis")
TEXT = CARDS[PRAXIS]["text"]


def card(n):
    return dict(CARDS[n])


def seat(name=M, seed=3):
    v = player.VirtualPlayer(R.DECK_MICHAEL, name, seed=seed)
    v.hand.clear()
    v.library.clear()
    v.turn = 1
    return v


def put(v, name, **kw):
    p = player.Perm(card(name), sick=False)
    for k, x in kw.items():
        setattr(p, k, x)
    v.battlefield.append(p)
    return p


def cast(v, text=TEXT):
    v.__dict__["spell_name"] = PRAXIS
    done, left = v.run_effects(None, text)
    return " ".join(done), left


def engine_x4():
    print("\n── the engine: X is 4 (five lands, two of them the same)")
    v = seat()
    for n in ("Swamp", "Swamp", "Forest", "Risa", "Ferenginar"):
        put(v, n)
    horta, moopsy, xindi = put(v, "Horta"), put(v, "Moopsy"), put(v, "Xindi Surveyors")
    ship_cold = put(v, OBERTH, charge=5)                 # not a creature: 5 of 6 charge
    ship = put(v, OBERTH, charge=6)                      # an artifact creature, 2/2
    check(ship.is_("Creature") and not ship_cold.is_("Creature"), "set-up: the Oberth at 6 charge is a creature, the one at 5 is not")
    said, left = cast(v)
    check(not left, "nothing is left by hand", left)
    check("-4/-4" in said and "X is 4" in said, "the line says -4/-4 and how X was counted (names, not lands)", said)
    check(v.stats(horta) == (3, 2) and horta in v.battlefield, "Horta 7/6 is 3/2 and lives", v.stats(horta))
    check(moopsy not in v.battlefield and xindi not in v.battlefield, "Moopsy 1/1 and Xindi Surveyors 3/2 die (toughness 0 or less)", [p.name for p in v.battlefield])
    check(ship not in v.battlefield and any(c["name"] == OBERTH for c in v.graveyard), "the Oberth that IS a creature (6 charge, 2/2) dies", [p.name for p in v.battlefield])
    check(ship_cold in v.battlefield and ship_cold.temp == (0, 0), "the Oberth that is not a creature is untouched")
    check(all(p.temp == (0, 0) for p in v.battlefield if p.is_("Land")), "lands are untouched")
    check("Moopsy dies" in said and f"{OBERTH} dies" in said, "each death is said", said)
    check(not any("Klingon" in p.name for p in v.battlefield) and "not 6 or more" in said, "X is 4: no tokens, and the line says why", said)
    out = [it for it in v.__dict__.get("outbox") or [] if it.get("kind") == "others-run"]
    check(len(out) == 1 and out[0]["sentence"] == "creatures you control get -4/-4 until end of turn" and not out[0].get("opponents_only"),
          "every OTHER board is sent the same change, with the caster's number already worked out", out)
    v.clear_until_eot()
    check(v.stats(horta) == (7, 6), "at the end of the turn Horta is 7/6 again", v.stats(horta))


def engine_x6():
    print("\n── the engine: X is 6 (tokens), and creatures made by the spell do not get the change")
    v = seat()
    for n in ("Swamp", "Forest", "Risa", "Ferenginar", "Plains", "Mountain"):
        put(v, n)
    horta = put(v, "Horta")
    said, left = cast(v)
    toks = [p for p in v.battlefield if p.token]
    check(not left and horta not in v.battlefield, "Horta 7/6 dies at -6/-6", (left, [p.name for p in v.battlefield]))
    check(len(toks) == 2 and all(v.stats(p) == (2, 1) for p in toks), "two 2/1 Klingon Warrior tokens, at their full size", [(p.name, v.stats(p)) for p in toks])
    check(all("Haste" in v.kws(p) and not p.sick for p in toks) and all("Klingon" in p.name and "Warrior" in p.name for p in toks), "…with haste, named Klingon Warrior", [p.name for p in toks])
    check("X is 6" in said and "Klingon Warrior" in said, "the line says both halves", said)
    # five names, six lands: no tokens
    w = seat()
    for n in ("Swamp", "Forest", "Risa", "Ferenginar", "Plains", "Plains"):
        put(w, n)
    said, left = cast(w)
    check(not [p for p in w.battlefield if p.token] and "X is 5" in said, "six lands with five names: X is 5, no tokens", said)
    # no lands: X is 0
    z = seat()
    m = put(z, "Moopsy")
    said, left = cast(z)
    check(not left and m in z.battlefield and "-0/-0" in said and not (z.__dict__.get("outbox") or []), "no lands: -0/-0, nothing dies, nothing is sent to the other boards", said)


def engine_far_side_and_lord():
    print("\n── the engine: the far side of it, a lord, a face-down card")
    o = seat(C)
    picard = put(o, PICARD)                              # 2/3, "Other creatures you control get +1/+1"
    a, b = put(o, "Xindi Surveyors"), put(o, "Xindi Surveyors")   # 3/2, 4/3 with Picard
    check(o.stats(a) == (4, 3), "set-up: Xindi Surveyors is 4/3 beside Picard", o.stats(a))
    done, left = o.run_effects(None, "creatures you control get -3/-3 until end of turn")
    check(not left and not any(p in o.battlefield for p in (picard, a, b)),
          "-3/-3: Picard (2/3) dies, and the two 4/3s he was holding up die with him, whatever order they stand in", [(p.name, o.stats(p)) for p in o.battlefield])
    o = seat(C)
    a, picard = put(o, "Xindi Surveyors"), put(o, PICARD)          # the lord LAST this time
    o.run_effects(None, "creatures you control get -3/-3 until end of turn")
    check(not o.creatures(), "…the same with the lord standing after them", [p.name for p in o.battlefield])
    f = seat(C)
    fd = put(f, "Plasma Cascade", face_down=True)                  # an instant, face down: a 2/2 creature
    f.run_effects(None, "creatures you control get -2/-2 until end of turn")
    check(fd not in f.battlefield, "a face-down card is a 2/2 creature: it gets the change and dies at -2/-2", [p.name for p in f.battlefield])
    g = seat(C)
    fd = put(g, "Plasma Cascade", face_down=True)
    g.run_effects(None, "creatures you control get -1/-1 until end of turn")
    check(fd in g.battlefield and g.stats(fd) == (1, 1), "…and is 1/1 at -1/-1", g.stats(fd))


def engine_shape():
    print("\n── the shape, and what is NOT claimed")
    v = seat()
    known = ["All creatures get -X/-X until end of turn, where X is the number of differently named lands you control.",
             "Creatures you control get +X/+0 until end of turn, where X is the number of differently named lands you control.",      # Pah-Wraith
             "All creatures get -2/-2 until end of turn.", "All other creatures get -1/-1 until end of turn.", "Creatures you control get -1/-1 until end of turn."]
    for s in known:
        check(v.can_run(s), f"known: {s[:70]}")
    check(v.can_run(TEXT.split(" — ", 1)[1]), "The Destruction of Praxis: the whole text, both sentences as one effect")
    for s, why in [("All creatures get -X/-X until end of turn.", "Toxic Deluge: X is a life payment"),
                   ("Creatures you control get +X/+X until end of turn, where X is the number of opponents you attacked this turn.", "Korok: another X"),
                   ("All creatures get -X/-X until end of turn, where X is the number of differently named lands you control. If X is 6 or more, each player draws nine cards and dances.", "a rider the table cannot do")]:
        check(not v.can_run(s), f"not claimed ({why})")
    w = seat()
    put(w, "Swamp")
    m = put(w, "Moopsy")
    done, left = w.run_effects(None, "All creatures get -X/-X until end of turn, where X is the number of differently named lands you control. If X is 6 or more, each player draws nine cards and dances.")
    check(left and m in w.battlefield and m.temp == (0, 0), "…and nothing is half-done for it (Moopsy lives, no change)", (done, left))
    # Pah-Wraith's sentence
    p = seat()
    for n in ("Swamp", "Forest", "Mountain"):
        put(p, n)
    h = put(p, "Horta")
    done, left = p.run_effects(None, known[1])
    check(not left and p.stats(h) == (10, 6) and not (p.__dict__.get("outbox") or []), "Pah-Wraith's sentence: my creatures get +3/+0, nothing goes to the other boards", (done, p.stats(h)))
    # D'Ghor Bloodletter's: the source is spared, the others are not
    d = seat()
    src, m = put(d, "Horta"), put(d, "Moopsy")
    done, left = d.run_effects(src, "All other creatures get -1/-1 until end of turn.")
    check(not left and src.temp == (0, 0) and m not in d.battlefield and any(it.get("kind") == "others-run" for it in d.__dict__.get("outbox") or []),
          "'All other creatures get -1/-1' (already done before): the source is spared, mine die, the other boards are sent it", done)
    check(player.is_board_wipe(card(PRAXIS)), "a bot is told it is a board wipe (it keeps it while it has creatures of its own)")


def table():
    print("\n── a real table: Michael casts it through his own key")
    t = R.Table()
    try:
        t.start()                                        # Michael's main 1
        for n in ("Swamp", "Swamp", "Forest", "Mountain", "Plains"):          # five lands, four names
            t.onto(M, n)
        mh, mm = t.onto(M, "Horta"), t.onto(M, "Moopsy")
        ch, cm, cx = t.onto(C, "Horta"), t.onto(C, "Moopsy"), t.onto(C, "Xindi Surveyors")
        ship = t.onto(C, OBERTH)
        t.decline(C)
        c, d = t.call("POST", "/api/brain/manual", {"seat": C, "op": "counter", "ref": f"#{ship}", "kind": "charge", "n": 6})
        st = next(p for p in t.state(C)["permanents"] if p["id"] == ship)
        check(c == 200 and st["charge"] == 6 and st["pt"] == "2/2", "set-up: Claude's Oberth has 6 charge and is a 2/2 artifact creature", (c, d, st.get("charge"), st.get("pt")))
        t.hand(M, PRAXIS)
        hand = next(h for h in t.state(M)["hand"] if h["name"] == PRAXIS)
        check(hand["castable_now"] and hand.get("wipe"), "it is castable, and marked as a wipe", hand)
        ev0 = max([e.get("id", 0) for e in t.events()] or [0])
        c, d = t.act(M, "cast", name=PRAXIS)
        check(c == 200, "the cast is accepted", d)
        sm, sc = t.state(M), t.state(C)
        pm = {p["id"]: p for p in sm["permanents"]}
        pc = {p["id"]: p for p in sc["permanents"]}
        check(pm.get(mh, {}).get("pt") == "3/2" and mm not in pm, "the caster's board: Horta is 3/2, Moopsy is gone", [(p["name"], p.get("pt")) for p in sm["permanents"] if p.get("pt")])
        check(pc.get(ch, {}).get("pt") == "3/2" and cm not in pc and cx not in pc, "the other board: Horta is 3/2, Moopsy and Xindi Surveyors are gone",
              [(p["name"], p.get("pt")) for p in sc["permanents"] if p.get("pt")])
        check(ship not in pc, "the Oberth-Class Starship at 6 charge (an artifact creature) is gone from the battlefield", [p["name"] for p in sc["permanents"]])
        gy = t.board(C).get("graveyard") or []
        check(OBERTH in gy and "Moopsy" in gy, "…and is in the graveyard, on the public board", gy)
        bm = {p.get("id"): p for p in t.board(M).get("permanents", [])}
        bc = {p.get("id"): p for p in t.board(C).get("permanents", [])}
        check(bm.get(mh, {}).get("pt") == "3/2" and bc.get(ch, {}).get("pt") == "3/2", "the public board shows 3/2 for both Hortas", (bm.get(mh), bc.get(ch)))
        evs = [e for e in t.events() if e.get("id", 0) > ev0]
        pub = " ".join(str(e.get("text") or "") for e in evs)
        check("-4/-4" in pub and "X is 4" in pub, "the public log says -4/-4 and X", pub[-700:])
        check(f"{OBERTH} dies" in pub and "Moopsy dies" in pub, "…and names what died", pub[-700:])
        check(not any("By hand" in str(e.get("text")) and ("Praxis" in str(e.get("text")) or "All creatures" in str(e.get("text"))) for e in evs), "no 'By hand' for it",
              [e.get("text") for e in evs if "By hand" in str(e.get("text"))])
        todos = str(t.call("GET", "/api/todos", host=False)[1])
        check("All creatures get" not in todos and "Klingon Warrior" not in todos, "nothing about it on the to-do list", todos[:300])
        # the legal list reads the same numbers: attacking with the 3/2 Horta is offered; nothing offers the dead
        legal = str(t.legal(M).get("actions"))
        check(f"#{mm}" not in legal, "the legal list offers nothing for the dead Moopsy", legal[:300])
        # the end of the turn
        t.turn(C)
        check(next(p for p in t.state(M)["permanents"] if p["id"] == mh)["pt"] == "7/6" and next(p for p in t.state(C)["permanents"] if p["id"] == ch)["pt"] == "7/6",
              "next turn both Hortas are 7/6 again")
        check(next((p.get("pt") for p in t.board(M).get("permanents", []) if p.get("id") == mh), None) == "7/6", "…on the public board too")

        print("\n── X is 6: the tokens")
        t.turn(M)
        for n in ("Risa", "Ferenginar"):
            t.onto(M, n)
        t.settle_setup()
        t.hand(M, PRAXIS)
        c, d = t.act(M, "cast", name=PRAXIS)
        check(c == 200, "the second cast is accepted", d)
        toks = [p for p in t.state(M)["permanents"] if p.get("token") and "Klingon" in p["name"]]
        check(len(toks) == 2 and all(p["pt"] == "2/1" and p.get("can_attack") for p in toks), "two 2/1 Klingon Warrior tokens that can attack this turn (haste)", toks and [(p["pt"], p.get("can_attack"), p.get("why_not")) for p in toks])
        check(not [p for p in t.state(M)["permanents"] if p["id"] == mh] and not [p for p in t.state(C)["permanents"] if p["id"] == ch], "both Hortas (7/6) die at -6/-6")
        la = [a for a in t.legal(M).get("actions", []) if a["action"] == "attack"]
        check(la and all(f"#{p['id']}" in str(la) for p in toks), "the legal list offers the attack with both tokens", la)
    finally:
        t.stop()


def main():
    for fn, title in [(engine_x4, "x4"), (engine_x6, "x6"), (engine_far_side_and_lord, "far side"), (engine_shape, "shape"), (table, "table")]:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — recorded, and the next section runs
            traceback.print_exc()
            check(False, f"section finished: {title}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
