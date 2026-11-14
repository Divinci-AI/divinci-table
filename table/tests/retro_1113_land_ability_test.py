"""2026-11-13, item 8: a land that does more than make mana, and taking back a land tapped by mistake.

Seen at the table, twice (Unexplored Territory, then Artificial Anomaly): on the hand page a click on a land only tapped it for mana, the land's
ability could not be reached, and "we still can't untap land after we tap it".

What the engine already did, what it did wrong, and what is new (all pinned here, on the engine alone and on a real table through a seat's key):
  * the family: Unexplored Territory and the five Anomaly lands (Artificial, Gaseous, Spatial, Subspace, Temporal). Costs are paid by the
    normal payment ({N}{C}, {T}, sacrifice); the land goes to the graveyard; the effect is carried out; "Create a tapped Planet land token named
    New Planet with '{T}: Add one mana of any color.'" makes a real land token.
    BUG FIXED: an effect that began "Draw two cards." did only the draw: Gaseous Anomaly never made its New Planet, silently.
    NEW: Unexplored Territory's state lists the basic lands it may fetch (`pick_options`), and `pick` may be a plain string.
  * an ability the table cannot carry out: with `by_hand` (a person's seat only) the cost is still paid and the land still sacrificed, what
    the table knows is done, the rest is said "by hand:" and put on the to-do list. Without it, as before: refused, nothing paid.
  * a land token by hand: manual op "land-token" (the op "token" only makes creatures).
  * taking back a tap: a land tapped BY HAND is untapped again by its player in the same step, until that player passes priority. Never a
    land the table tapped to pay for something (that mana is spent), and never through a stale note: a land tapped by hand on one turn and
    tapped by a payment on a later turn could be untapped by hand before this change (free mana). Each refusal says which case it is.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro_1113_land_ability_test.py
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
FAMILY = ["Unexplored Territory", "Artificial Anomaly", "Gaseous Anomaly", "Spatial Anomaly", "Subspace Anomaly", "Temporal Anomaly"]
R.POOL.update({n: 6 for n in FAMILY})
R.POOL.update({"Moopsy": 6, "Horta": 6, "Xindi Surveyors": 6})
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830, 8883, 8891))   # never a live game's port
R.DECK_MICHAEL = R.deck_file("Michael-land")
R.DECK_CLAUDE = R.deck_file("Claude-land")
NEW_PLANET = "New Planet token"


def card(n):
    return dict(CARDS[n])


def seat():
    v = player.VirtualPlayer(R.DECK_MICHAEL, M, seed=3)
    v.hand.clear()
    v.turn = 1
    return v


def put(v, name, **kw):
    p = player.Perm(card(name) if isinstance(name, str) else name, sick=False)
    for k, x in kw.items():
        setattr(p, k, x)
    v.battlefield.append(p)
    return p


def untapped_lands(v):
    return sum(1 for p in v.battlefield if p.is_("Land") and not p.tapped)


def engine():
    print("\n── the engine: the family")
    check(all(n in CARDS and "Land" in CARDS[n]["types"] for n in FAMILY), "the set has Unexplored Territory and five Anomaly lands", [n for n in FAMILY if n not in CARDS])
    others = sorted(n for n, c in CARDS.items() if "Anomaly" in n and "Land" in (c.get("types") or []) and n not in FAMILY)
    check(not others, "…and no other Anomaly land this file does not cover", others)

    v = seat()
    for n in ("Swamp",) * 4:
        put(v, n)
    ut = put(v, "Unexplored Territory")
    lib0, forests = len(v.library), sum(1 for c in v.library if c["name"] == "Forest")
    try:
        v.activate(f"#{ut.id}", index=0)
        check(False, "Unexplored Territory with no land named is refused")
    except player.IllegalAction as e:
        check(ut in v.battlefield and not ut.tapped and untapped_lands(v) == 5, "Unexplored Territory with no land named: refused, nothing paid, nothing sacrificed", str(e))
    said = " ".join(v.activate(f"#{ut.id}", index=0, pick=["Forest"]))
    f = [p for p in v.battlefield if p.name == "Forest"]
    check(len(f) == 1 and f[0].tapped and ut not in v.battlefield and any(c["name"] == "Unexplored Territory" for c in v.graveyard),
          "{1}, {T}, sacrifice: a Forest comes onto the battlefield tapped; the Territory is in the graveyard", said)
    check(untapped_lands(v) == 3 and len(v.library) == lib0 - 1 and sum(1 for c in v.library if c["name"] == "Forest") == forests - 1, "{1} was paid by one land; the library is one Forest shorter", (untapped_lands(v), len(v.library) - lib0))
    check("shuffle" in said.lower() and not v.todo, "it says the library was shuffled; nothing is left by hand", (said, v.todo))

    v = seat()
    for n in ("Swamp",) * 4:
        put(v, n)
    aa = put(v, "Artificial Anomaly")
    life0 = v.life
    said = " ".join(v.activate(f"#{aa.id}", index=0))
    tok = [p for p in v.battlefield if p.name == NEW_PLANET]
    check(v.life == life0 + 2 and any(it.get("kind") == "each-opponent" and it.get("n") == 2 for it in v.__dict__.get("outbox") or []), "Artificial Anomaly: I gain 2, each opponent is sent 'loses 2'", said)
    check(len(tok) == 1 and tok[0].tapped and tok[0].token and tok[0].is_("Land") and "Planet" in (tok[0].card.get("subtypes") or []) and "Add one mana of any color" in tok[0].card["text"],
          "…and a tapped New Planet LAND token with '{T}: Add one mana of any color.'", [(p.name, p.types) for p in v.battlefield])
    check(aa not in v.battlefield and untapped_lands(v) == 0 and not v.todo, "{3}{B} paid with the four Swamps, the Anomaly sacrificed, nothing by hand", (untapped_lands(v), v.todo))
    v.clear_until_eot()
    for p in v.battlefield:
        p.tapped = False
    check(any(s is tok[0] and set("WUBRG") <= set(cols) for s, cols in v.sources()), "the New Planet token is a mana source for any colour once it untaps", [(s.name, c) for s, c in v.sources()])

    v = seat()
    for n in ("Island",) * 6:
        put(v, n)
    ga = put(v, "Gaseous Anomaly")
    h0 = len(v.hand)
    said = " ".join(v.activate(f"#{ga.id}", index=0))
    check(len(v.hand) == h0 + 2, "Gaseous Anomaly: two cards drawn", said)
    check(len([p for p in v.battlefield if p.name == NEW_PLANET]) == 1 and "New Planet" in said and not v.todo,
          "…AND the New Planet token (before: the draw was done and the land silently was not)", (said, [p.name for p in v.battlefield]))

    v = seat()
    for n in ("Mountain",) * 5:
        put(v, n)
    sp = put(v, "Spatial Anomaly")
    foe = seat()
    moopsy = put(foe, "Moopsy")
    said = " ".join(v.activate(f"#{sp.id}", index=0, target=moopsy, target_owner=foe))
    check(moopsy not in foe.battlefield and len([p for p in v.battlefield if p.name == NEW_PLANET]) == 1 and not v.todo, "Spatial Anomaly: 2 damage kills the target creature (the other seat's), and the New Planet", said)

    v = seat()
    for n in ("Plains",) * 5:
        put(v, n)
    sb = put(v, "Subspace Anomaly")
    said = " ".join(v.activate(f"#{sb.id}", index=0))
    off = [p for p in v.battlefield if p.token and "Officer" in p.name]
    check(len(off) == 2 and all(v.stats(p) == (1, 1) for p in off) and len([p for p in v.battlefield if p.name == NEW_PLANET]) == 1 and not v.todo, "Subspace Anomaly: two 1/1 Officer tokens and the New Planet", said)

    v = seat()
    v.__dict__["asks"] = False
    for n in ("Forest",) * 4:
        put(v, n)
    ta = put(v, "Temporal Anomaly")
    lib0, gy0, h0 = len(v.library), len(v.graveyard), len(v.hand)
    said = " ".join(v.activate(f"#{ta.id}", index=0))
    moved = lib0 - len(v.library)
    check(moved == 4 and (len(v.graveyard) - gy0 - 1) + (len(v.hand) - h0) == 4 and len(v.hand) - h0 <= 1, "Temporal Anomaly: four cards milled, at most one of them (a permanent card) to the hand", (said, moved, len(v.hand) - h0))
    check(len([p for p in v.battlefield if p.name == NEW_PLANET]) == 1 and not v.todo, "…and the New Planet", said)

    print("\n── the engine: costs are checked first")
    v = seat()
    for n in ("Swamp",) * 4:
        put(v, n)
    aa = put(v, "Artificial Anomaly", tapped=True)
    check(v.ability_why_not(aa, 0) == "it is tapped: its {T} ability waits", "a tapped Anomaly: 'it is tapped' (tapping it for mana first is exactly the mis-tap)", v.ability_why_not(aa, 0))
    aa.tapped = False
    v.battlefield[0].tapped = True
    check(str(v.ability_why_not(aa, 0)).startswith("needs {3}{B}"), "three lands for {3}{B}: 'needs {3}{B}' (the Anomaly's own {T} is part of the cost, not mana for it)", v.ability_why_not(aa, 0))

    print("\n── the engine: an effect the table cannot carry out")
    relic = {"name": "Test Relic", "types": ["Artifact"], "subtypes": [], "type": "Artifact", "text": "{1}, {T}, Sacrifice this artifact: Each player dances a jig. You gain 2 life.", "keywords": []}
    v = seat()
    put(v, "Swamp")
    r = put(v, relic)
    check(v.ability_why_not(r, 0) == "the table can't run this ability yet" and v.ability_why_not(r, 0, runnable=False) is None, "the table says it cannot run it; asked about the COST alone, it can be paid")
    try:
        v.activate(f"#{r.id}", index=0)
        check(False, "without by_hand it is refused")
    except player.IllegalAction as e:
        check(r in v.battlefield and not r.tapped and untapped_lands(v) == 1 and "nothing was paid" in str(e), "without by_hand: refused, nothing paid (a bot never pays for an effect nobody will carry out)", str(e))
    life0 = v.life
    said = " ".join(v.activate(f"#{r.id}", index=0, by_hand=True))
    check(r not in v.battlefield and untapped_lands(v) == 0, "with by_hand: {1} is paid and the Relic is sacrificed, so the player is not stuck", said)
    check(v.life == life0 + 2 and "I gain 2 life" in said, "the sentence the table DOES know is done (2 life)", said)
    check("by hand: Each player dances a jig" in said and any("dances a jig" in x for x in v.todo), "the one it does not know is said 'by hand:' and is on the to-do list", (said, v.todo))


def table():
    print("\n── a real table, through Michael's key")
    t = R.Table()
    try:
        t.start()
        swamps = [t.onto(M, "Swamp") for _ in range(5)]
        ut, aa = t.onto(M, "Unexplored Territory"), t.onto(M, "Artificial Anomaly")
        st = {p["id"]: p for p in t.state(M)["permanents"]}
        a_ut, a_aa = st[ut]["abilities"][0], st[aa]["abilities"][0]
        check(a_ut["can"] and a_aa["can"] and a_ut["cost"] == "{1}, {T}, Sacrifice this land", "both lands list their ability as usable now, with its whole cost", (a_ut, a_aa))
        check(isinstance(a_ut.get("pick_options"), list) and "Swamp" in a_ut["pick_options"] and "Forest" in a_ut["pick_options"] and all(n in ("Plains", "Island", "Swamp", "Mountain", "Forest") for n in a_ut["pick_options"])
              and "pick_options" not in a_aa, "Unexplored Territory's lists the basic lands in Michael's library it may fetch; the Anomaly's has no such list", a_ut.get("pick_options"))
        check("pick_options" not in json.dumps(t.state(C)) and "pick_options" not in json.dumps(t.call("GET", "/api/board3d", host=False)[1]), "that list (a library's contents) is in Michael's own state only")
        c, d = t.act(M, "activate", source=f"#{ut}", index=0)
        check(c == 400 and "pick" in str(d.get("error")) and t.perm(M, "Unexplored Territory"), "no land named: refused, the Territory is still there", (c, d))
        c, d = t.act(M, "activate", source=f"#{ut}", index=0, pick="Forest")
        forest = t.perm(M, "Forest")
        check(c == 200 and forest and forest["tapped"] and not t.perm(M, "Unexplored Territory"), "pick 'Forest' (a plain string, as the page's list gives it): a tapped Forest, the Territory sacrificed", (c, d.get("said") or d))
        life0 = (t.lifeof(M), t.lifeof(C))
        c, d = t.act(M, "activate", source=f"#{aa}", index=0)
        np_ = t.perm(M, NEW_PLANET)
        check(c == 200 and (t.lifeof(M), t.lifeof(C)) == (life0[0] + 2, life0[1] - 2), "Artificial Anomaly: Michael 20 → 22, Claude 20 → 18, each through the table's own life path", (c, d.get("said") or d, t.lifeof(M), t.lifeof(C)))
        check(np_ and np_["tapped"] and np_["token"] and "Land" in np_["type"] and not t.perm(M, "Artificial Anomaly"), "a tapped New Planet land token; the Anomaly is gone", np_)
        pub = next((p for p in t.board(M).get("permanents", []) if p.get("name") == NEW_PLANET), None)
        check(pub and pub.get("tapped") and "Artificial Anomaly" in (t.board(M).get("graveyard") or []), "the public board shows the token, and the Anomaly in the graveyard", pub)
        check("By hand" not in json.dumps(t.call("GET", "/api/todos", host=False)[1]) and "New Planet" not in json.dumps(t.call("GET", "/api/todos", host=False)[1]), "nothing on the to-do list")

        print("\n── a land token by hand")
        c, d = t.act(M, "manual", op="land-token", name="New Planet", tapped=True)
        check(c == 200 and len(t.perms(M, NEW_PLANET)) == 2 and all("Land" in p["type"] for p in t.perms(M, NEW_PLANET)), "manual op 'land-token': a second New Planet land token (the op 'token' only makes creatures)", (c, d))
        c, d = t.act(M, "manual", op="land-token", name="Bad", to="Draw seven cards.")
        check(c == 400 and len(t.perms(M, "Bad token")) == 0, "…its ability must be a mana ability ('{T}: Add …'): anything else is refused", (c, d))

        print("\n── taking back a tap")
        t.turn(C)
        t.turn(M)                                            # a new turn of Michael's: everything untapped
        a, b = swamps[0], swamps[1]
        tapped = lambda pid: next(p for p in t.state(M)["permanents"] if p["id"] == pid)["tapped"]
        c, _ = t.act(M, "tap", ref=f"#{a}")
        c2, d2 = t.act(M, "untap", ref=f"#{a}")
        check(c == 200 and c2 == 200 and not tapped(a), "a land tapped by hand is untapped again by its player, in the same step", (c2, d2))
        # a land the TABLE tapped to pay for something
        t.hand(M, "Moopsy")
        for pid in [p["id"] for p in t.state(M)["permanents"] if "Land" in p["type"] and p["id"] != swamps[4] and not p["tapped"]]:
            t.act(M, "tap", ref=f"#{pid}")                    # (so that the payment can only use swamps[4]… which makes no {G}: use a Forest instead)
        for pid in [p["id"] for p in t.state(M)["permanents"] if "Land" in p["type"] and p["tapped"]]:
            t.act(M, "untap", ref=f"#{pid}")
        f_id = t.perm(M, "Forest")["id"]
        before = {p["id"] for p in t.state(M)["permanents"] if "Land" in p["type"] and p["tapped"]}
        c, d = t.act(M, "cast", name="Moopsy")
        paid = [p["id"] for p in t.state(M)["permanents"] if "Land" in p["type"] and p["tapped"] and p["id"] not in before]
        check(c == 200 and len(paid) == 1, "(set-up) Moopsy {G} is cast; the table tapped one land for it", (c, d, paid))
        c, d = t.act(M, "untap", ref=f"#{paid[0]}")
        check(c == 409 and "tapped by the table" in str(d.get("error")) and "spent" in str(d.get("error")) and tapped(paid[0]), "that land cannot be untapped by hand: 'tapped by the table … that mana is spent'", (c, d))
        # an earlier step
        c, _ = t.act(M, "tap", ref=f"#{b}")
        t.next_to(M, "beginning of combat")
        c, d = t.act(M, "untap", ref=f"#{b}")
        check(c == 409 and "earlier step" in str(d.get("error")) and tapped(b), "a land tapped by hand in main 1 cannot be taken back in combat: 'an earlier step'", (c, d))

        print("\n── the stale note (free mana before this change)")
        # turn N: tap a land by hand and leave it. Turn N+1, the same step: the table taps that land to pay for a spell. Untap it by hand?
        t.turn(C)
        t.turn(M)
        lands = [p["id"] for p in t.state(M)["permanents"] if "Land" in p["type"]]
        note = f_id                                          # the Forest: tapped by hand this turn, in main 1
        c, _ = t.act(M, "tap", ref=f"#{note}")
        assert c == 200
        t.turn(C)
        t.turn(M)                                            # the untap step untapped it; the note from last turn's main 1 must be gone
        check(not tapped(note), "(set-up) next turn the Forest is untapped again")
        t.hand(M, "Moopsy")
        c, d = t.act(M, "cast", name="Moopsy")               # {G}: only the Forest (or a New Planet) can pay; the table chooses
        used = [pid for pid in lands if tapped(pid)]
        if c == 200 and used == [note]:
            c, d = t.act(M, "untap", ref=f"#{note}")
            check(c == 409 and tapped(note) and "tapped by the table" in str(d.get("error")), "the table tapped the SAME land to pay, in the same step of the turn: it cannot be untapped by hand (no free mana)", (c, d))
        else:
            check(c == 200 and all(t.act(M, "untap", ref=f"#{pid}")[0] == 409 for pid in [p["id"] for p in t.state(M)["permanents"] if "Land" in p["type"] and p["tapped"]]),
                  "whatever the table tapped to pay cannot be untapped by hand (no free mana)", (c, d, used))

        print("\n── …and not after passing priority")
        t.turn(C)                                            # Claude's main 1: Michael may tap a land to respond
        c, _ = t.act(M, "tap", ref=f"#{swamps[2]}")
        ph = t.call("GET", "/api/phase")[1]
        cp, dp = t.call("POST", "/api/brain/pass", {"seat": M, "player": ph.get("player"), "step": ph.get("step")}, key=t.keys[M])
        ph2 = t.call("GET", "/api/phase")[1]
        c, d = t.act(M, "untap", ref=f"#{swamps[2]}")
        if cp == 200 and ph2.get("step") == ph.get("step") and ph2.get("player") == ph.get("player"):
            check(c == 409 and "passed priority" in str(d.get("error")), "after Michael has passed priority the tap can no longer be taken back: 'before you passed priority'", (c, d))
        else:
            check(c == 409, "after Michael's pass the step moved on, and the tap can no longer be taken back", (cp, dp, c, d))
    finally:
        t.stop()


def main():
    for fn, title in [(engine, "engine"), (table, "table")]:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {title}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
