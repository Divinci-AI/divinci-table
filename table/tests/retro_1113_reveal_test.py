"""2026-11-13 retro, C1: "Reveal cards from the top of your library until you reveal N <kind> card(s). Put those ... and the rest on the bottom of
your library in a random order."

Seen at the table: U.S.S. Voyager, Lone Wanderer entered, the table said "By hand: ...", and the host did it with twelve manual moves. Now the
effect runner does it (player.py _sentences joins the two or three sentences, _one_effect0 carries them out).

The set's cards with "until you reveal" (grep of the card data's oracle_text), and what this covers:
  U.S.S. Voyager, Lone Wanderer    enters: two lands, onto the battlefield tapped                               done by the table
  Will Riker, First in the Field   beginning of combat: two lands, onto the battlefield, sacrificed at the end step   done by the table
  Janice Rand, Essential Aide      "whenever a commander you control enters or attacks ...": the SENTENCE is known; her trigger is a
                                   commander trigger the table has never run (and a draft table has no commander): still by hand
  Pavel Chekov, First Posting      a replacement for a draw with a choice ("land or nonland"): still by hand

The engine on its own first (a library stacked by hand, the seat's own seeded generator), then one real table on a free port through the seat's
own key: what the public log says, what the other seat sees.  Before the fix every check from "Voyager" on fails (the lands stay in the library
and the sentence lands on the to-do list): checked with `git stash -- table/player.py`.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro_1113_reveal_test.py
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
import retro_game4_test as R  # noqa: E402
import player  # noqa: E402

check, CARDS = R.check, R.CARDS
M, C = "Michael", "Claude"
VOY, RIKER, RAND, CHEKOV = "U.S.S. Voyager, Lone Wanderer", "Will Riker, First in the Field", "Janice Rand, Essential Aide", "Pavel Chekov, First Posting"
R.POOL.update({VOY: 6, RIKER: 6, "Ferenginar": 6, "Risa": 6, "Exploratory Mission": 6, "Moopsy": 6})
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830, 8883))   # never a live game's port
R.DECK_MICHAEL = R.deck_file("Michael-reveal")
R.DECK_CLAUDE = R.deck_file("Claude-reveal")


def card(n):
    return dict(CARDS[n])


def seat(seed=3):
    v = player.VirtualPlayer(R.DECK_MICHAEL, M, seed=seed)
    v.hand.clear()
    v.library.clear()
    v.turn = 1
    return v


def stack(v, top_first):
    """Put these on the library, the first one on top (the end of the list is the top)."""
    v.library[:] = [card(n) for n in reversed(top_first)]


def enters(v, name):
    p = player.Perm(card(name), sick=False)
    v.battlefield.append(p)
    said = v.enter_effects(p, lambda c, cands: cands[0])
    return p, said


TOP = ["Horta", "Tricorder", "Forest", "Moopsy", "Combadge", "Xindi Surveyors", "Risa", "Plains", "Horta", "Mountain"]


def engine_voyager():
    print("\n── the engine: U.S.S. Voyager enters (a library stacked by hand)")
    v = seat()
    stack(v, TOP)
    life0 = v.life
    p, said = enters(v, VOY)
    text = " ".join(said)
    lands = [q for q in v.battlefield if q.is_("Land")]
    check([q.name for q in lands] == ["Forest", "Risa"], "the first two lands from the top are on the battlefield", [q.name for q in v.battlefield])
    check(all(q.tapped for q in lands), "…tapped")
    for n in TOP[:7]:
        check(n in text, f"the public line names the revealed {n}", text)
    check(not any(n in text for n in ("Plains", "Mountain")), "…and nothing below the second land (it was never revealed)", text)
    check([c["name"] for c in v.library[::-1][:3]] == ["Plains", "Horta", "Mountain"], "what was not revealed is still on top, in order", [c["name"] for c in v.library[::-1]])
    bottom = [c["name"] for c in v.library[:5]]
    check(sorted(bottom) == sorted(["Horta", "Tricorder", "Moopsy", "Combadge", "Xindi Surveyors"]), "the other five revealed cards are on the bottom", bottom)
    check(len(v.library) == 8 and not v.hand, "no card was lost or drawn", (len(v.library), len(v.hand)))
    check(v.life == life0 + 1 and "I gain 1 life" in text, "a land that arrives this way fires its own enters text (Risa: 1 life)", (v.life, text))
    check(not v.todo, "nothing is left for the host to do by hand", v.todo)
    # the order on the bottom: the same for the same seed, different between seeds, and not simply the order revealed
    orders = set()
    for seed in range(1, 9):
        w = seat(seed)
        stack(w, TOP)
        enters(w, VOY)
        orders.add(tuple(c["name"] for c in w.library[:5]))
    w1, w2 = seat(5), seat(5)
    for w in (w1, w2):
        stack(w, TOP)
        enters(w, VOY)
    check([c["name"] for c in w1.library] == [c["name"] for c in w2.library], "the same seed gives the same bottom (a restored game replays the same)")
    check(len(orders) >= 4, "across eight seeds the bottom comes out in at least four different orders (a random order)", len(orders))


def engine_landfall_and_short():
    print("\n── the engine: Landfall, and a library with too few lands")
    v = seat()
    em = player.Perm(card("Exploratory Mission"), sick=False)
    v.battlefield.append(em)
    stack(v, ["Forest", "Plains", "Horta", "Tricorder", "Moopsy", "Combadge"])
    enters(v, VOY)
    check(em.quest == 2, "each land entering is its own Landfall (two quest counters)", em.quest)
    v = seat()
    stack(v, ["Horta", "Forest", "Tricorder"])
    p, said = enters(v, VOY)
    text = " ".join(said)
    check([q.name for q in v.battlefield if q.is_("Land")] == ["Forest"] and sorted(c["name"] for c in v.library) == ["Horta", "Tricorder"],
          "one land in the whole library: it enters, everything else goes to the bottom", ([q.name for q in v.battlefield], [c["name"] for c in v.library]))
    check("whole library" in text and all(n in text for n in ("Horta", "Forest", "Tricorder")), "…and the line says the library ran out, naming all three", text)
    v = seat()
    p, said = enters(v, VOY)
    check(not v.todo and "empty" in " ".join(said), "an empty library: nothing happens, and nothing is left to do by hand", (said, v.todo))


def engine_land_kinds():
    """Item 1 of the second 2026-11-13 list: the host's ad-hoc lookup did not know "Land — Planet" cards (Risa, Ferenginar) as lands. The engine
    asks the card's own types (card_fits), so EVERY card of the set whose type line says Land must count, and no other card may."""
    print("\n── the engine: what counts as a land card")
    lands = [c for c in CARDS.values() if "Land" in (c.get("type") or "")]
    kinds = sorted({(c.get("type") or "").split("—")[-1].strip() if "—" in (c.get("type") or "") else "(no subtype)" for c in lands})
    check(len(lands) >= 20 and any("Planet" in k for k in kinds), f"the set has {len(lands)} cards with Land in the type line (subtypes: {', '.join(kinds)[:160]})")
    wrong = [c["name"] for c in lands if not player.card_fits(c, "land")]
    check(not wrong, "every one of them is a land card to the reveal: Planets, basics, two-colour lands, utility lands", wrong[:8])
    wrong = [c["name"] for c in CARDS.values() if "Land" not in (c.get("type") or "") and player.card_fits(c, "land")]
    check(not wrong, "…and no card without Land in its type line is (a card that only MENTIONS land in its name or text is not one)", wrong[:8])
    for n in ("Risa", "Ferenginar"):
        check(n in CARDS and "Planet" in CARDS[n]["type"] and player.card_fits(CARDS[n], "land"), f"{n} ({CARDS[n]['type']}) is a land card")
    nonbasic = sorted(c["name"] for c in lands if "Basic" not in (c.get("type") or "") and "Planet" not in (c.get("type") or ""))
    check(len(nonbasic) >= 2, f"(the set's other non-basic lands, all counted above: {', '.join(nonbasic)[:200]})")

    # two Planets: both found, both tapped, each one's own "you gain 1 life", and the reveal stops AT the second land
    v = seat()
    stack(v, ["Horta", "Risa", "Tricorder", "Ferenginar", "Forest", "Moopsy"])
    life0 = v.life
    p, said = enters(v, VOY)
    text = " ".join(said)
    lands_ = [q for q in v.battlefield if q.is_("Land")]
    check([q.name for q in lands_] == ["Risa", "Ferenginar"] and all(q.tapped for q in lands_), "two Planet lands: both onto the battlefield, tapped", [(q.name, q.tapped) for q in v.battlefield])
    check(v.life == life0 + 2, "each Planet's own 'When this land enters, you gain 1 life' happens (+2)", v.life - life0)
    check([c["name"] for c in v.library[::-1][:2]] == ["Forest", "Moopsy"] and "Forest" not in text and "Moopsy" not in text,
          "the reveal stops exactly at the second land: the Forest right under it is still on top, unrevealed", ([c["name"] for c in v.library[::-1]], text))
    check(sorted(c["name"] for c in v.library[:2]) == ["Horta", "Tricorder"] and len(v.library) == 4, "the two non-lands are on the bottom; the library was not shuffled", [c["name"] for c in v.library])
    # a non-basic that is neither a Planet nor basic, when the set has one
    if nonbasic:
        v = seat()
        stack(v, ["Horta", nonbasic[0], "Forest", "Moopsy"])
        enters(v, VOY)
        check(sorted(q.name for q in v.battlefield if q.is_("Land")) == sorted([nonbasic[0], "Forest"]), f"a non-basic land ({nonbasic[0]}) is found like any other", [q.name for q in v.battlefield])
    # the two lands are the top two cards: nothing to put on the bottom
    v = seat()
    stack(v, ["Forest", "Plains", "Horta"])
    p, said = enters(v, VOY)
    check([c["name"] for c in v.library] == ["Horta"] and len([q for q in v.battlefield if q.is_("Land")]) == 2 and "bottom" not in " ".join(said),
          "lands on top: both enter, nothing goes to the bottom, the next card stays where it was", (said, [c["name"] for c in v.library]))
    # a library with NO land at all
    v = seat()
    stack(v, ["Horta", "Tricorder", "Moopsy"])
    p, said = enters(v, VOY)
    text = " ".join(said)
    check(not [q for q in v.battlefield if q.is_("Land")] and sorted(c["name"] for c in v.library) == ["Horta", "Moopsy", "Tricorder"] and not v.hand and not v.todo,
          "no land in the library: nothing enters, all three cards go to the bottom, nothing by hand", (said, [c["name"] for c in v.library]))
    check("whole library" in text and "0 land cards" in text and all(n in text for n in ("Horta", "Tricorder", "Moopsy")), "…and the public line says so, naming every card it showed", text)


def engine_riker():
    print("\n── the engine: Will Riker at the beginning of combat")
    v = seat()
    v.battlefield.append(player.Perm(card(RIKER), sick=False))
    stack(v, ["Horta", "Risa", "Forest", "Tricorder"])
    said = " ".join(v.begin_combat())
    lands = [q for q in v.battlefield if q.is_("Land")]
    check(sorted(q.name for q in lands) == ["Forest", "Risa"], "two lands onto the battlefield", [q.name for q in v.battlefield])
    forest, risa = next(q for q in lands if q.name == "Forest"), next(q for q in lands if q.name == "Risa")
    check(not forest.tapped, "not tapped (his text does not say tapped)")
    check(risa.tapped, "…except a land whose OWN text says it enters tapped (Risa)")
    check("Horta" in said and "Risa" in said and "Forest" in said and "Tricorder" not in said, "the line names what was revealed, and only that", said)
    check("sacrifice" in said.lower(), "…and says they go at the end step", said)
    check(not v.todo, "nothing by hand", v.todo)
    v.end_step()
    check(not [q for q in v.battlefield if q.is_("Land")] and sorted(c["name"] for c in v.graveyard) == ["Forest", "Risa"], "at his controller's end step the two lands are sacrificed",
          ([q.name for q in v.battlefield], [c["name"] for c in v.graveyard]))


def engine_coverage():
    print("\n── what the table says it can do")
    v = seat()
    for n in (VOY, RIKER):
        line = next(l for l in card(n)["text"].split("\n") if "until you reveal" in l)
        body = line.split(", ", 1)[1] if line.startswith(("When", "At the beginning")) else line
        check(v.can_run(body, n), f"{n}: the runner knows the whole effect", body)
    hand_version = "Reveal cards from the top of your library until you reveal a nonland card. Put that card into your hand and put all other cards revealed this way on the bottom of your library in a random order."
    check(v.can_run(hand_version), "the same template into the hand (a nonland card) is known")
    w = seat()
    stack(w, ["Forest", "Plains", "Horta", "Moopsy"])
    done, left = w.run_effects(None, hand_version)
    check([c["name"] for c in w.hand] == ["Horta"] and not left and "Forest" in " ".join(done) and "Moopsy" not in " ".join(done), "…and does it: the first nonland card, the lands to the bottom", (done, left))
    creature_version = "Reveal cards from the top of your library until you reveal a creature card. Put that card onto the battlefield and the rest on the bottom of your library in a random order."
    check(not v.can_run(creature_version), "onto the battlefield for a NON-land is not claimed (its enters triggers could not be asked): it stays by hand")
    x = seat()
    stack(x, ["Forest", "Horta"])
    done, left = x.run_effects(None, creature_version)
    check(len(x.library) == 2 and left, "…and nothing is half-done for it", (done, left, len(x.library)))


def table_voyager():
    print("\n── a real table: Michael casts U.S.S. Voyager through his own key")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 4, "Forest")
        order = ["Mountain", "Horta", "Plains", "Moopsy"]             # (the last one named ends up on top)
        for n in [VOY, *order]:                                       # every search shuffles the library: fetch first, stack after
            t.hand(M, n)
        for n in order:
            c, d = t.call("POST", "/api/brain/topdeck", {"seat": M, "name": n, "quiet": True})
            assert c == 200, d
        ev0 = max([e.get("id", 0) for e in t.events()] or [0])
        c, d = t.act(M, "cast", name=VOY)
        check(c == 200, "the cast is accepted", d)
        st = t.state(M)
        lands = [p for p in st["permanents"] if "Land" in (p.get("type") or "")]
        new = [p for p in lands if p["name"] in ("Plains", "Mountain")]
        check(len(new) == 2 and all(p["tapped"] for p in new), "Plains and Mountain are on his battlefield, tapped", [(p["name"], p["tapped"]) for p in lands])
        evs = [e for e in t.events() if e.get("id", 0) > ev0]
        pub = " ".join(str(e.get("text") or "") for e in evs)
        check(all(n in pub for n in ("Moopsy", "Plains", "Horta", "Mountain")), "the public log names all four revealed cards", pub[-600:])
        check(not any(e.get("type") == "table-note" and "By hand" in str(e.get("text")) and "reveal" in str(e.get("text")).lower() for e in evs), "no 'By hand' note for it",
              [e.get("text") for e in evs if e.get("type") == "table-note"])
        board = t.board(M)
        check(sum(1 for p in board.get("permanents", []) if p.get("name") in ("Plains", "Mountain") and p.get("tapped")) >= 2, "the other seats see the two tapped lands on the public board")
        todos = json.dumps(t.call("GET", "/api/todos", host=False)[1])
        check("until you reveal" not in todos, "nothing about it on the to-do list", todos[:300])
    finally:
        t.stop()


def main():
    for fn, title in [(engine_voyager, "engine voyager"), (engine_landfall_and_short, "engine landfall"), (engine_land_kinds, "land kinds"), (engine_riker, "engine riker"), (engine_coverage, "coverage"), (table_voyager, "table")]:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — recorded, and the next section runs
            traceback.print_exc()
            check(False, f"section finished: {title}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
