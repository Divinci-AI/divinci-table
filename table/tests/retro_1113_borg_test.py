"""2026-11-14 retro, E3 and E4.

E3  Borg Cube ({5}{W}{B} Artifact — Spacecraft 6/6): "Affinity for artifact creatures. Whenever this Spacecraft enters or attacks, assimilate a
    creature card exiled with it. If you can't, exile up to one target creature. (To assimilate a card, put it onto the battlefield under your
    control with a +1/+1 counter. It's a Borg artifact creature and loses all other creature types.)" Done by hand five times on the night.
    Now done by the table:
      * the cards "exiled with" a permanent (Perm.exiled_with): they stay in their owner's exile when the Cube leaves, and a Cube that comes
        back is a new permanent with none;
      * the trigger, on entering and on attacking (both already send a trigger's whole line to the sentence engine): assimilate; if it can't,
        its controller is asked for up to one target creature (any seat's; "no target" is an answer);
      * assimilate: onto the battlefield under the assimilator's control, a +1/+1 counter, an Artifact Creature — Borg; it remembers its owner
        and the card as printed (it goes to its OWNER's graveyard as itself);
      * only a creature CARD: a Spacecraft that was a creature because of its charge counters is an artifact card in exile, never assimilated;
      * Affinity for artifact creatures: {1} less for each one its caster controls.
    Siblings in the set NOT done here (they assimilate from a graveyard, a library or the battlefield, or need "exile target opponent's
    graveyard"): The Borg Queen, Borg Queen Perfection Manifest, Gift of Perfection, Assimilate the Wounded, Death Is Irrelevant, The Best of
    Both Worlds, Assimilation Assessor (its attack trigger's sentence IS the Cube's and is known; its enters trigger is not, so nothing is
    ever exiled with it). They are by hand as before, and the helper they need (VirtualPlayer.assimilate) now exists.

E4  Targ Wrangler: "Menace. At the beginning of your end step, if this creature attacked this turn, sacrifice a nonland permanent."
    Both were already in the engine; this pins them: the condition, the choice (any nonland permanent, never a land; settled without asking
    when there is only one), and menace at the moment blocks are declared: one blocker is refused with the reason, two are accepted, and
    the legal list a bot or the autopilot reads never offers a single blocker for it.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro_1113_borg_test.py
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
CUBE, TARG, OBERTH = "Borg Cube", "Targ Wrangler", "Oberth-Class Starship"
R.POOL.update({CUBE: 6, TARG: 6, OBERTH: 6, "Moopsy": 6, "Horta": 6, "Xindi Surveyors": 6, "Tricorder": 6, "Assault Drone": 6})
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830, 8883, 8891))   # never a live game's port
R.DECK_MICHAEL = R.deck_file("Michael-borg")
R.DECK_CLAUDE = R.deck_file("Claude-borg")


def card(n):
    return dict(CARDS[n])


def seat(name=M, asks=True):
    v = player.VirtualPlayer(R.DECK_MICHAEL, name, seed=3)
    v.hand.clear()
    v.turn = 1
    v.__dict__["asks"] = asks
    return v


def put(v, name, **kw):
    p = player.Perm(card(name), sick=False)
    for k, x in kw.items():
        setattr(p, k, x)
    v.battlefield.append(p)
    return p


def enters(v, name):
    p = player.Perm(card(name), sick=False)
    v.battlefield.append(p)
    return p, " ".join(v.enter_effects(p, lambda c, cands: cands[0]))


def pending(v):
    return list(v.__dict__.get("pending_choices") or [])


def answer(v, perm=None, owner=None):
    it = pending(v)[0]
    return " ".join(v.resolve_choice(it, [{"perm": perm, "owner": owner}] if perm is not None else []))


def engine_cube():
    print("\n── the engine: Borg Cube")
    text = CARDS[CUBE]["text"]
    check("assimilate a creature card exiled with it. If you can't, exile up to one target creature." in text and "Affinity for artifact creatures" in text, "(the card's text is the one this was written for)", text)
    me, foe = seat(M), seat(C)
    horta, moopsy = put(foe, "Horta"), put(foe, "Moopsy")
    cube, said = enters(me, CUBE)
    check("no creature card is exiled with it" in said and not me.todo, "it enters with nothing exiled with it: it says so, and nothing is left by hand", (said, me.todo))
    pc = pending(me)
    check(len(pc) == 1 and pc[0]["slot"].get("optional") and pc[0]["slot"]["kinds"] == ["Creature"] and pc[0]["slot"]["side"] == "any" and pc[0]["source"] == CUBE,
          "its controller is asked for UP TO ONE target creature, any seat's (the question is optional)", pc and pc[0]["slot"])
    said = answer(me, horta, foe)
    check(horta not in foe.battlefield and any(c["name"] == "Horta" for c in foe.exile_zone()) and "exiled with Borg Cube" in said, "Claude's Horta is exiled, face up in Claude's exile, and the line says it is exiled WITH the Cube", said)
    check(len(cube.exiled_with) == 1 and cube.exiled_with[0][0] is foe and cube.exiled_with[0][1]["name"] == "Horta" and not pending(me), "the Cube remembers it (whose card, which card)")
    check(any("Borg Cube exiles your Horta" in str(it.get("text")) for it in me.__dict__.get("outbox") or []), "Claude is told", me.__dict__.get("outbox"))

    said = " ".join(me.attack_triggers(cube, C, [cube]))
    borg = next((p for p in me.battlefield if p.name == "Horta"), None)
    check(borg is not None and not any(c["name"] == "Horta" for c in foe.exile_zone()) and not cube.exiled_with, "it attacks: Horta is assimilated (out of Claude's exile, onto Michael's battlefield)", said)
    check(borg and borg.counters == 1 and me.stats(borg) == (8, 7), "…with a +1/+1 counter: 8/7", borg and me.stats(borg))
    check(borg and borg.is_("Artifact") and borg.is_("Creature") and borg.card["subtypes"] == ["Borg"] and "Artifact Creature — Borg" in borg.card["type"], "…a Borg artifact creature that has lost its other creature types", borg and (borg.types, borg.card["subtypes"], borg.card["type"]))
    check(borg and borg.sick and borg.owner is foe and borg.orig_card["subtypes"] == CARDS["Horta"]["subtypes"], "…summoning sick, and it remembers its owner and the card as printed")
    check("I assimilate Horta" in said and "Claude's card" in said and not pending(me) and not me.todo, "the line says whose card; it could assimilate, so no target is asked for", (said, pending(me)))
    me.move(f"#{borg.id}", "graveyard")
    check(any(c is borg.orig_card for c in foe.graveyard) and not any(c["name"] == "Horta" for c in me.graveyard) and foe.graveyard[-1]["subtypes"] == CARDS["Horta"]["subtypes"],
          "when it dies it goes to its OWNER's graveyard, as the card it was printed as (not a Borg)", ([c["name"] for c in me.graveyard], [c["name"] for c in foe.graveyard][-2:]))

    print("\n── no target; a card that is not a creature card; a token")
    me, foe = seat(M), seat(C)
    moopsy = put(foe, "Moopsy")
    cube, said = enters(me, CUBE)
    said = answer(me)
    check(moopsy in foe.battlefield and not cube.exiled_with and not pending(me) and "no target" in said, "'no target' is an answer: nothing is exiled", said)
    ship = put(foe, OBERTH, charge=6)
    check(ship.is_("Creature"), "(set-up) Claude's Oberth-Class Starship has 6 charge: a creature on the battlefield")
    me.attack_triggers(cube, C, [cube])
    said = answer(me, ship, foe)
    check(ship not in foe.battlefield and len(cube.exiled_with) == 1 and "not a creature card" in said, "the Starship is exiled with the Cube, and the line says it is not a creature card", said)
    said = " ".join(me.attack_triggers(cube, C, [cube]))
    check(not any(p.name == OBERTH for p in me.battlefield) and "none a creature card" in said and len(pending(me)) == 1,
          "next trigger: the Starship (an artifact card in exile) is NOT assimilated; the Cube asks for a target again", said)
    tok = player.Perm({"name": "Officer token", "types": ["Creature"], "subtypes": ["Officer"], "power": "1", "toughness": "1", "text": "", "keywords": []}, token=True, sick=False)
    foe.battlefield.append(tok)
    said = answer(me, tok, foe)
    check(tok not in foe.battlefield and len(cube.exiled_with) == 1 and "gone for good" in said, "a token exiled this way is gone, and is not 'exiled with' anything", said)

    print("\n── two creature cards; the Cube leaves and comes back")
    me, foe = seat(M), seat(C)
    a, b = put(foe, "Moopsy"), put(me, "Xindi Surveyors")
    cube, _ = enters(me, CUBE)
    answer(me, a, foe)
    me.attack_triggers(cube, C, [cube])                          # (assimilates Moopsy)
    c2, d2 = put(foe, "Horta"), put(foe, "Xindi Surveyors")
    for x in (c2, d2):                                           # two more exiled with it, by two triggers that could not assimilate
        cube.exiled_with = (*cube.exiled_with, (foe, x.card))
        foe.battlefield.remove(x)
        foe.exile_zone().append(x.card)
    said = " ".join(me.attack_triggers(cube, C, [cube]))
    pc = pending(me)
    labs = [o["label"] for o in pc[0]["options"]] if pc else []
    check(len(pc) == 1 and pc[0]["kind"] == "option" and labs == ["Horta (Claude's)", "Xindi Surveyors (Claude's)"], "two creature cards exiled with it: its controller is asked which one", (said, labs))
    said = " ".join(me.resolve_choice(pc[0], [{"option": 1}]))
    check(any(p.name == "Xindi Surveyors" and p.owner is foe for p in me.battlefield) and [c["name"] for _, c in cube.exiled_with] == ["Horta"], "the one chosen is assimilated; the other is still exiled with the Cube", said)
    me.move(f"#{cube.id}", "hand")
    check(any(c["name"] == "Horta" for c in foe.exile_zone()), "the Cube leaves the battlefield: the card exiled with it STAYS in exile")
    cube2, said = enters(me, CUBE)
    check(not cube2.exiled_with and "no creature card is exiled with it" in said and len(pending(me)) == 1, "the Cube comes back as a new permanent: nothing is exiled with it (it asks for a target)", said)

    print("\n── affinity, and what the table says it can do")
    me = seat(M)
    c = card(CUBE)
    check(me.auto_discount(c) == 0, "no artifact creature: no discount")
    put(me, "Assault Drone")
    put(me, OBERTH, charge=6)
    put(me, OBERTH, charge=0)
    put(me, "Tricorder")
    n_art = sum(1 for q in me.battlefield if q.is_("Artifact") and q.is_("Creature"))
    check(me.auto_discount(c) == n_art and n_art >= 1, f"Affinity for artifact creatures: {{1}} less for each artifact creature I control ({n_art} here: a Spacecraft below its charge and an Equipment do not count)", (me.auto_discount(c), [(q.name, q.types) for q in me.battlefield]))
    line = next(l for l in CARDS[CUBE]["text"].split("\n") if "enters or attacks" in l).split(", ", 1)[1]
    check(me.can_run(line, CUBE), "the runner knows the whole trigger")
    check(not me.can_run("Assimilate target creature card from an opponent's graveyard."), "an assimilate from a graveyard is NOT claimed (the Borg Queen: still by hand)")


def engine_targ():
    print("\n── the engine: Targ Wrangler")
    v = seat(M)
    targ, moopsy, tri = put(v, TARG), put(v, "Moopsy"), put(v, "Tricorder")
    for _ in range(3):
        put(v, "Mountain")
    check("Menace" in v.kws(targ), "it has menace")
    said = " ".join(v.end_step())
    check(not pending(v) and len(v.battlefield) == 6, "it did not attack this turn: nothing happens at the end step", said)
    targ.attacked_turn = v.turn
    said = " ".join(v.end_step())
    pc = pending(v)
    check(len(pc) == 1 and pc[0]["source"] == TARG and "Land" in (pc[0]["slot"].get("not_kinds") or []) and not pc[0]["slot"].get("optional"),
          "it attacked this turn: at the end step its controller must choose a NONLAND permanent to sacrifice (not optional, never a land)", pc and pc[0]["slot"])
    said = answer(v, tri, v)
    check(tri not in v.battlefield and any(c["name"] == "Tricorder" for c in v.graveyard) and "sacrifice" in said.lower(), "the one chosen is sacrificed (here the Tricorder, not a creature)", said)
    w = seat(M, asks=False)
    t2 = put(w, TARG)
    put(w, "Mountain")
    t2.attacked_turn = w.turn
    said = " ".join(w.end_step())
    check(t2 not in w.battlefield and len(w.battlefield) == 1, "an engine-run seat with only the Wrangler itself to give: it sacrifices that, and keeps its land", said)


def table():
    print("\n── a real table: Borg Cube through Michael's key")
    t = R.Table()
    try:
        t.start()
        for n in ("Plains", "Plains", "Plains", "Swamp", "Swamp", "Swamp", "Swamp"):
            t.onto(M, n)
        ch, cm = t.onto(C, "Horta"), t.onto(C, "Moopsy")
        mx = t.onto(M, "Xindi Surveyors")
        t.hand(M, CUBE)
        h = next(x for x in t.state(M)["hand"] if x["name"] == CUBE)
        check(h["castable_now"], "seven lands, {5}{W}{B}: castable")
        ev0 = max([e.get("id", 0) for e in t.events()] or [0])
        c, d = t.act(M, "cast", name=CUBE)
        pc = (t.state(M).get("pending_choices") or [None])[0]
        check(c == 200 and pc and pc["source"] == CUBE and pc["slot"].get("optional"), "the cast is accepted; the table waits on Michael for up to one target creature", (c, d, pc))
        opts = [(o.get("at"), o.get("ref")) for o in (pc["slot"].get("options") or [])]
        check((C, f"#{ch}") in opts and (C, f"#{cm}") in opts and (M, f"#{mx}") in opts, "the options are every creature on the table, his own included", opts)
        acts = [a for a in t.legal(M)["actions"]]
        check(acts and all(a["action"] == "choose" for a in acts) and any(a["body"]["target"] == {} for a in acts) and any(a["body"]["target"].get("ref") == f"#{ch}" for a in acts),
              "the legal list (what a bot or the autopilot reads) is exactly those choices, 'no target' among them", [a["why"] for a in acts])
        act = next(a for a in acts if a["body"]["target"].get("ref") == f"#{ch}")
        c, d = t.call("POST", "/api/brain/choose", act["body"], key=t.keys[M])
        check(c == 200 and not t.perm(C, "Horta") and "Horta" in (t.board(C).get("exile") or []), "the listed body, POSTed unchanged: Claude's Horta is exiled, face up in Claude's exile on the public board", (c, d, t.board(C).get("exile")))
        pub = " ".join(str(e.get("text") or "") for e in t.events() if e.get("id", 0) > ev0)
        check("no creature card is exiled with it" in pub and "exiled with Borg Cube" in pub, "the public log has both lines", pub[-500:])
        cube = t.perm(M, CUBE)
        c, d = t.call("POST", "/api/brain/manual", {"seat": M, "op": "counter", "ref": f"#{cube['id']}", "kind": "charge", "n": 12})
        t.turn(C)
        t.turn(M)
        cube = t.perm(M, CUBE)
        check(cube["pt"] == "6/6" and cube["can_attack"], "(set-up) with 12 charge the Cube is a 6/6 creature and can attack", cube)
        ev1 = max([e.get("id", 0) for e in t.events()] or [0])
        c, d = t.act(M, "attack", assign={f"#{cube['id']}": C})
        borg = t.perm(M, "Horta")
        check(c == 200 and borg and borg["pt"] == "8/7" and borg["counters"] == 1 and "Artifact Creature — Borg" in borg["type"] and borg["sick"], "it attacks: Horta is on Michael's battlefield, 8/7 with a +1/+1 counter, an Artifact Creature — Borg, summoning sick", (c, d, borg))
        check("Horta" not in (t.board(C).get("exile") or []) and not (t.state(M).get("pending_choices") or []), "…it is out of Claude's exile, and no target is asked for (it could assimilate)", t.board(C).get("exile"))
        pb = next((p for p in t.board(M).get("permanents", []) if p.get("name") == "Horta"), None)
        check(pb and pb.get("pt") == "8/7" and "Borg" in str(pb.get("type")), "the public board shows it on Michael's side as a Borg", pb)
        pub = " ".join(str(e.get("text") or "") for e in t.events() if e.get("id", 0) > ev1)
        check("I assimilate Horta (Claude's card)" in pub, "the public log says whose card was assimilated", pub[-400:])
    finally:
        t.stop()

    print("\n── a real table: Targ Wrangler")
    t = R.Table()
    try:
        t.start()
        targ = t.onto(M, TARG)
        t.onto(M, "Mountain")
        tri = t.onto(M, "Tricorder")
        c0, d0 = t.call("POST", "/api/brain/manual", {"seat": C, "op": "token", "name": "Officer", "power": 1, "toughness": 1, "n": 2})   # two 1/1s: they will not kill a 5/3
        assert c0 == 200, d0
        c1, c2 = [p["id"] for p in t.state(C)["permanents"] if p.get("token")][:2]
        t.turn(C)
        t.turn(M)
        c, d = t.act(M, "attack", assign={f"#{targ}": C})
        check(c == 200, "(set-up) Targ Wrangler attacks Claude", (c, d))
        inc = (t.state(C).get("incoming") or [{}])[0]
        check(inc.get("menace") is True, "the defender is told it has menace", inc)
        blocks = [a for a in t.legal(C)["actions"] if a["action"] == "block"]
        singles = [a for a in blocks if len((a["body"].get("blockers") or ([a["body"]["blocker"]] if a["body"].get("blocker") else []))) == 1
                   or any(len(v_) == 1 for v_ in (a["body"].get("blocks") or {}).values())]
        check(blocks and not singles, "the legal list offers Claude no single-blocker block for it (two blockers, or none)", [a["why"] for a in blocks])
        c, d = t.call("POST", "/api/brain/block", {"seat": C, "blocks": {f"#{targ}": [f"#{c1}"]}}, key=t.keys[C])
        check(c == 400 and "menace" in str(d.get("error")) and "two or more" in str(d.get("error")), "ONE blocker is refused, with the reason", (c, d))
        c, d = t.call("POST", "/api/brain/block", {"seat": C, "attacker": f"#{targ}", "blocker": f"#{c1}"}, key=t.keys[C])
        check(c == 400 and "menace" in str(d.get("error")), "…in the one-attacker form of the request too", (c, d))
        st = ((t.call("GET", "/api/phase")[1].get("combat") or {}).get("attacks") or [{}])[0]
        check(st.get("stage") == "blocks" and not st["attackers"][0]["answered"], "nothing was recorded by the refused blocks: the table still waits for Claude's answer", st)
        c, d = t.call("POST", "/api/brain/block", {"seat": C, "blocks": {f"#{targ}": [f"#{c1}", f"#{c2}"]}}, key=t.keys[C])
        check(c == 200, "TWO blockers are accepted", (c, d))
        for _ in range(12):
            if (((t.call("GET", "/api/phase")[1].get("combat") or {}).get("attacks") or [{}])[0]).get("stage") == "done":
                break
            t.act(M, "next")
        check(t.perm(M, TARG) is not None, "(set-up) the Wrangler survived its two 1/1 blockers")
        t.next_to(M, "end step")
        t.act(M, "next")
        pc = (t.state(M).get("pending_choices") or [None])[0]
        check(pc and pc["source"] == TARG and not pc["slot"].get("optional"), "at Michael's end step the table asks which nonland permanent to sacrifice (it attacked this turn)", (pc, t.call("GET", "/api/phase")[1].get("step")))
        opts = [o.get("ref") for o in (pc["slot"].get("options") or [])] if pc else []
        land = t.perm(M, "Mountain")["id"]
        check(f"#{targ}" in opts and f"#{tri}" in opts and f"#{land}" not in opts, "the options are his nonland permanents (the Wrangler, the Tricorder), never the land", opts)
        c, d = t.call("POST", "/api/brain/choose", {"seat": M, "id": pc["id"], "target": {"at": M, "ref": f"#{tri}"}}, key=t.keys[M])
        check(c == 200 and t.perm(M, "Tricorder") is None and t.perm(M, TARG) is not None and "Tricorder" in (t.board(M).get("graveyard") or []), "he chooses the Tricorder: it is sacrificed, the Wrangler stays", (c, d))
    finally:
        t.stop()


def main():
    for fn, title in [(engine_cube, "cube"), (engine_targ, "targ"), (table, "table")]:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {title}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
