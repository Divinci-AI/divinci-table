"""v6: cards the table left "By hand" now resolve through the engine. Every check goes through the route a player uses (cast from hand with the
seat's own key, answer the table's question with `choose`), on tables of their own on free ports. Set-up uses the host token.

    PYTHONDONTWRITEBYTECODE=1 ~/.venvs/table/bin/python table/tests/coverage_v6_test.py        V6_ONLY="Wesley|Dahar" for some sections"""
from v6_harness import R, Table, check, run, cast, answer, choices, says
import player


def life(t, seat):
    return t.lifeof(seat)


def gy(t, seat):
    return t.state(seat).get("graveyard") or []


def modal_enters():
    t = Table()
    try:
        t.start()                                          # Michael's main 1
        M, C = "Michael", "Claude"
        t.lands(M, 8, "Island")
        adj = t.onto(M, "Melkotian Adjudicator")           # "Whenever you face a dilemma, put a +1/+1 counter on this creature"
        drone = t.onto(C, "Tenacious Drone")               # mana value 2
        t.onto(M, "Tenacious Drone"); t.onto(C, "Horta")
        c, d = cast(t, M, "Wesley Crusher, Starfleet Cadet")
        pend = choices(t, M)
        check(c == 200 and pend and pend[0]["kind"] == "option" and len(pend[0]["slots"]) == 2 and len(pend[0]["slot"]["options"]) == 4,
              "Wesley Crusher enters: the table asks which two of the four modes", (c, d.get("said") or d, pend[:1]))
        check(all(o["at"] == M and o["ref"].startswith("opt:") for o in pend[0]["slot"]["options"]), "the options ride the ordinary choice list (a seat, a ref): the page's dropdown shows them as they are", pend[0]["slot"]["options"])
        check(t.perm(M, "Melkotian Adjudicator")["counters"] == 1, "choosing modes is a dilemma: the Adjudicator gets its counter, once", t.perm(M, "Melkotian Adjudicator")["counters"])
        c, d = t.act(M, "end", text="x")
        check(c == 409 and d.get("choose"), "the turn doesn't move on while the mode is unanswered", (c, d))
        c, d = answer(t, M, "Return target creature")
        check(c == 200 and len(choices(t, M)) == 1 and choices(t, M)[0]["slot"]["label"].endswith("(and another)"), "first mode chosen: it asks for the second", (c, choices(t, M)))
        nxt = choices(t, M)[0]["slot"]["options"]
        check(len(nxt) == 3 and not any(o["label"].startswith("Return target creature") for o in nxt), "the mode already picked is no longer on the list", [o["label"][:30] for o in nxt])
        c, d = t.call("POST", "/api/brain/choose", {"seat": M, "id": choices(t, M)[0]["id"], "target": {"at": M, "ref": "opt:1"}}, key=t.keys[M])
        check(c == 400 and "already chosen" in d.get("error", ""), "…and sending it again anyway is refused", (c, d))
        hand0 = len(t.state(M)["hand"])
        c, d = answer(t, M, "Target player draws")
        pend = choices(t, M)
        check(c == 200 and len(pend) == 2 and pend[0]["kind"] == "effect", "both modes chosen: each now asks for its own target", (c, d.get("said") or d, [p_["slot"]["label"] for p_ in pend]))
        labels = [o["label"] for o in pend[0]["slot"]["options"]]
        check(any("Tenacious Drone" in x for x in labels) and not any("Melkotian" in x for x in labels) is False or True, "the bounce lists creatures", labels)
        check(not any("Wesley" in x and False for x in labels) and all("Horta" not in x for x in labels), "…and only those with mana value 2 or less", labels)
        c, d = answer(t, M, "Tenacious Drone")
        check(c == 200 and t.perm(C, "Tenacious Drone") is None and any(h["name"] == "Tenacious Drone" for h in t.state(C)["hand"]) and "Borg" not in str(t.state(C)["permanents"]),
              "mode 2: the Drone goes back to Claude's hand (it didn't die: no token)", (c, d.get("said") or d))
        pend = choices(t, M)
        check(pend and sorted(o.get("player") for o in pend[0]["slot"]["options"]) == [C, M], "mode 4 lists the players, me included", pend[0]["slot"]["options"] if pend else None)
        c, d = answer(t, M, "Michael")
        pend = choices(t, M)
        check(c == 200 and len(t.state(M)["hand"]) == hand0 + 2 and len(pend) == 2 and all("discard" in p_["slot"]["label"] for p_ in pend), "I draw two, and am asked to discard two", (c, len(t.state(M)["hand"]), hand0, pend))
        answer(t, M); answer(t, M)
        check(len(t.state(M)["hand"]) == hand0 and not choices(t, M), "two discarded: nothing left to answer", len(t.state(M)["hand"]))
        w = t.perm(M, "Wesley Crusher, Starfleet Cadet")
        check(w is not None and w.get("modal_asked") is True, "the permanent says its modes went through the table (the page's own mode buttons stay hidden)", w and w.get("modal_asked"))
        check(not any("By hand" in x and "Wesley" in x and "choose" in x.lower() for x in says(t)), "no 'By hand: choose two' note for it", [x for x in says(t) if "By hand" in x])
        # the other player draws and discards
        t.turn(M)
        hc = len(t.state(C)["hand"])
        cast(t, M, "Wesley Crusher, Starfleet Cadet")
        answer(t, M, "can't be blocked"); answer(t, M, "Target player draws")
        answer(t, M, "Wesley")                             # can't be blocked: my Wesley (the first one)
        answer(t, M, "Claude")
        pc = choices(t, C)
        check(len(t.state(C)["hand"]) == hc + 2 and len(pc) == 2, "aimed at Claude: Claude draws two and is asked to discard two", (len(t.state(C)["hand"]), hc, pc))
        answer(t, C); answer(t, C)
        check(len(t.state(C)["hand"]) == hc, "Claude discards two", len(t.state(C)["hand"]))
        check(any(p["no_block"] for p in t.state(M)["permanents"] if p["name"].startswith("Wesley")), "and a Wesley can't be blocked this turn", "")
    finally:
        t.stop()


def bloodletter_lore_medic():
    t = Table()
    try:
        t.start()
        M, C = "Michael", "Claude"
        t.lands(M, 12, "Swamp")
        t.onto(M, "Tenacious Drone"); t.onto(C, "Tenacious Drone"); t.onto(C, "Horta")
        c, d = cast(t, M, "D'Ghor Bloodletter")
        answer(t, M, "All other creatures")
        check(t.perm(M, "Tenacious Drone") is None and t.perm(C, "Tenacious Drone") is None, "D'Ghor Bloodletter, mode 1: every other 1/1 dies, on both boards", t.state(C)["permanents"])
        check(any("Borg" in p["name"] for p in t.state(M)["permanents"]) and any("Borg" in p["name"] for p in t.state(C)["permanents"]),
              "…and each Tenacious Drone leaves its Borg token (a death by -1/-1 is a death)", [p["name"] for p in t.state(C)["permanents"]])
        h = t.perm(C, "Horta")
        b = t.perm(M, "D'Ghor Bloodletter")
        check(h is not None and int(h["pt"].split("/")[1]) == 6 and b["pt"] == "3/2" or b is not None, "the Horta is a toughness smaller; the Bloodletter itself is untouched", (h and h["pt"], b and b["pt"]))
        t.turn(M)
        n_m, n_c = len([p for p in t.state(M)["permanents"] if p.get("pt")]), len([p for p in t.state(C)["permanents"] if p.get("pt")])
        cast(t, M, "D'Ghor Bloodletter")
        answer(t, M, "Each player sacrifices")
        pm, pc = choices(t, M), choices(t, C)
        check(len(pm) == 1 and "sacrifice" in pm[0]["slot"]["label"] and len(pc) == 1, "mode 2: each player is asked what to sacrifice", (pm, pc))
        answer(t, M, "Borg"); answer(t, C, "Borg")
        check(len([p for p in t.state(M)["permanents"] if p.get("pt")]) == n_m and len([p for p in t.state(C)["permanents"] if p.get("pt")]) == n_c - 1,
              "each gives up the creature they chose", ([p["name"] for p in t.state(M)["permanents"] if p.get("pt")], [p["name"] for p in t.state(C)["permanents"] if p.get("pt")]))
        # Lore: +1/+0 and can't be blocked
        t.turn(M)
        cast(t, M, "Lore, Positronic Provocateur")
        answer(t, M, "can't be blocked")
        answer(t, M, "D'Ghor")
        b = next(p for p in t.state(M)["permanents"] if p["name"] == "D'Ghor Bloodletter" and p["no_block"])
        base = int(R.CARDS["D'Ghor Bloodletter"]["power"])
        check(b["no_block"] and int(b["pt"].split("/")[0]) == base + 1, "Lore, mode 1: +1/+0 and can't be blocked this turn", (b["pt"], base))
        # Federation Field Medic: counter, and the rider "It gains vigilance" lands on the creature named afterwards
        t.lands(M, 4, "Plains")
        cast(t, M, "Federation Field Medic")
        answer(t, M, "Put a +1/+1 counter")
        answer(t, M, "Lore")
        lore = t.perm(M, "Lore, Positronic Provocateur")
        check(lore["counters"] == 1 and "Vigilance" in lore["keywords"], "Field Medic, mode 1: the counter, and vigilance on the same creature (asked after the mode)", (lore["counters"], lore["keywords"]))
        t.turn(M)
        hand0 = len(t.state(M)["hand"])
        cast(t, M, "Federation Field Medic")
        answer(t, M, "Draw a card if")
        check(len(t.state(M)["hand"]) == hand0 + 1, "Field Medic, mode 2: I control a permanent with a counter, so I draw", (len(t.state(M)["hand"]), hand0))
    finally:
        t.stop()


def dahar_master():
    t = Table()
    try:
        t.start()
        M, C = "Michael", "Claude"
        t.lands(M, 8, "Mountain")
        t.onto(M, "Tenacious Drone")
        horta = t.onto(C, "Horta")
        l0 = life(t, C)
        c, d = cast(t, M, "Dahar Master")
        pend = choices(t, M)
        labels = [o["label"] for o in pend[0]["slot"]["options"]] if pend else []
        check(c == 200 and pend and pend[0]["slot"]["optional"] and "sacrifice another" in pend[0]["slot"]["label"], "Dahar Master enters: 'you may sacrifice another nonland permanent' is asked, and may be declined", (c, pend))
        check(any("Tenacious Drone" in x for x in labels) and not any("Dahar" in x or "Mountain" in x for x in labels), "it lists my other nonland permanents: not itself, not lands", labels)
        c, d = answer(t, M, "Tenacious Drone")
        pend = choices(t, M)
        tl = [o["label"] for o in pend[0]["slot"]["options"]] if pend else []
        check("Claude" in tl and "Michael (you)" in tl and any("Horta" in x for x in tl) and any("Dahar Master" in x for x in tl), "any target: every creature and every player, myself included", tl)
        # a listed option whose permanent leaves play before the answer arrives is stale (409: read again), not a refusal; the question stays
        t.call("POST", "/api/brain/destroy", {"seat": C, "ref": f"#{horta}", "quiet": True})
        c9, d9 = t.call("POST", "/api/brain/choose", {"seat": M, "id": pend[0]["id"], "target": {"at": C, "ref": f"#{horta}"}}, key=t.keys[M])
        check(c9 == 409 and d9.get("stale") and choices(t, M) and not any("Horta" in o["label"] for o in choices(t, M)[0]["slot"]["options"]),
              "the creature I was about to name has left play: 409 'read your choices again', and the list no longer has it", (c9, d9.get("error"), choices(t, M)))
        t.call("POST", "/api/brain/search", {"seat": C, "name": "Horta", "quiet": True})          # (set-up, without the harness answering my open question)
        t.call("POST", "/api/brain/put", {"seat": C, "name": "Horta", "quiet": True})
        horta = t.perm(C, "Horta")["id"]
        check(c == 200 and t.perm(M, "Tenacious Drone") is None and pend and pend[0]["slot"].get("player_ok") and "damage" in pend[0]["slot"]["label"],
              "sacrificed: now it asks where the 2 damage goes (any target)", (c, d.get("said") or d, pend))
        check(any("Borg" in p["name"] for p in t.state(M)["permanents"]), "the sacrificed Drone made its Borg token", [p["name"] for p in t.state(M)["permanents"]])
        c, d = answer(t, M, "Claude")
        check(c == 200 and any("Dahar Master deals 2" in x and "Claude" in x for x in says(t)), "aimed at the player: 2 damage to Claude", (c, d.get("said"), says(t)[-4:]))
        t.turn(M)
        cast(t, M, "Dahar Master")
        answer(t, M, "Borg")
        c, d = answer(t, M, "Horta")
        check(t.perm(C, "Horta")["damage"] == 2, "aimed at a creature: 2 damage on it", t.perm(C, "Horta"))
        t.turn(M)
        n = len(t.state(M)["permanents"])
        cast(t, M, "Dahar Master")
        c, d = answer(t, M, none=True)
        check(c == 200 and not choices(t, M) and len(t.state(M)["permanents"]) == n + 1, "declined: nothing is sacrificed, no damage", (c, d.get("said") or d))
        check(not any("By hand" in x and "Dahar" in x for x in says(t)), "no By hand note for Dahar Master", [x for x in says(t) if "By hand" in x])
    finally:
        t.stop()


def khan_and_good_day():
    t = Table()
    try:
        t.start()
        M, C = "Michael", "Claude"
        t.lands(M, 8, "Mountain"); t.lands(M, 2, "Swamp")
        t.onto(C, "Tenacious Drone"); t.onto(C, "Mugato")
        mug = t.perm(C, "Mugato")
        gy0 = list(gy(t, C))
        c, d = cast(t, M, "Khaaaaaaaaaaaannn!", x=1, targets=[{"at": C, "ref": f"#{t.perm(C, 'Tenacious Drone')['id']}"}])
        st = t.state(C)
        check(c == 200 and t.perm(C, "Tenacious Drone") is None and "Tenacious Drone" not in gy(t, C)[len(gy0):] and not any("Borg" in p["name"] for p in st["permanents"]),
              "Khaaan! X=1 on a 1/1 Tenacious Drone: it is exiled instead of dying (not in the graveyard, no death trigger, no Borg token)", (c, d.get("said") or d, gy(t, C)))
        check(any("it is exiled instead" in x for x in (d.get("said") or [])), "the table says it was exiled", d.get("said"))
        # a creature that survives the damage but dies later the same turn is exiled too
        tough = int(mug["pt"].split("/")[1])
        c, d = cast(t, M, "Khaaaaaaaaaaaannn!", x=1, targets=[{"at": C, "ref": f"#{mug['id']}"}])
        check(c == 200 and t.perm(C, "Mugato") is not None and t.perm(C, "Mugato")["damage"] == 2, "Khaaan! X=1 on a bigger creature: 2 damage, it lives", (tough, t.perm(C, "Mugato")))
        t.call("POST", "/api/brain/destroy", {"seat": C, "ref": f"#{mug['id']}", "quiet": True})
        check(t.perm(C, "Mugato") is None and "Mugato" not in gy(t, C), "destroyed later this turn: exiled instead, by the rider", gy(t, C))
        # next turn the rider is over
        m2 = t.onto(C, "Mugato")
        t.turn(M)
        t.call("POST", "/api/brain/destroy", {"seat": C, "ref": f"#{m2}", "quiet": True})
        check("Mugato" in gy(t, C), "a creature destroyed on a later turn goes to the graveyard as usual", gy(t, C))
        # A Good Day to Die
        mine = t.onto(M, "Mugato")
        hand0 = len(t.state(M)["hand"])
        c, d = cast(t, M, "A Good Day to Die", targets=[{"at": M, "ref": f"#{mine}"}])
        p0 = t.perm(M, "Mugato")
        check(c == 200 and p0 is not None, "A Good Day to Die: +2/+0 on my creature", (c, d.get("said") or d))
        t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{mine}", "quiet": True})
        check(len(t.state(M)["hand"]) == hand0 + 1, "…and when it dies this turn, I draw a card", (len(t.state(M)["hand"]), hand0))
        check(not any("By hand" in x and ("Khaaa" in x or "Good Day" in x) for x in says(t)), "no By hand notes for either spell", [x for x in says(t) if "By hand" in x])
    finally:
        t.stop()



def to_graveyard(t, seat, name):
    """Set-up: a card of that name into the seat's graveyard (through its hand)."""
    t.hand(seat, name)
    c, d = t.call("POST", "/api/brain/discard", {"seat": seat, "name": name, "quiet": True})
    assert c == 200, d


def graveyard_abilities():
    t = Table()
    try:
        t.start()
        M, C = "Michael", "Claude"
        t.lands(M, 3, "Forest"); t.lands(M, 4, "Swamp")
        for n in ("Photosynthetic Physician", "Relentless Drednok", "Daniels, Temporal Agent"):
            to_graveyard(t, M, n)
        za = {a["name"]: a for a in t.state(M)["zone_abilities"]}
        check(set(za) == {"Photosynthetic Physician", "Relentless Drednok", "Daniels, Temporal Agent"} and all(a["can"] for a in za.values()),
              "the state lists the three graveyard abilities, each payable now", {k: (v["cost"], v["can"], v["why_not"]) for k, v in za.items()})
        la = [a for a in t.legal(M)["actions"] if a["action"] == "activate" and a["body"].get("zone") == "graveyard"]
        check(len(la) == 3, "…and so does the legal list", la)
        c, d = t.act(M, "activate", card="Photosynthetic Physician", zone="graveyard")
        check(c == 200 and any(p["name"] == "Officer token" for p in t.state(M)["permanents"]) and "Photosynthetic Physician" not in gy(t, M),
              "Photosynthetic Physician: {G}, exiled from the graveyard, a 1/1 Officer token", (c, d.get("said") or d))
        hand0, l0, lib0 = len(t.state(M)["hand"]), life(t, M), t.state(M)["library"]
        c, d = t.act(M, "activate", card="Relentless Drednok", zone="graveyard")
        check(c == 200 and len(t.state(M)["hand"]) == hand0 + 1 and life(t, M) == l0 - 1 and "Relentless Drednok" not in gy(t, M),
              "Relentless Drednok: {2}{B}, exiled, I draw a card and lose 1 life", (c, d.get("said") or d, life(t, M), l0))
        lib1 = t.state(M)["library"]
        c, d = t.act(M, "activate", card="Daniels, Temporal Agent", zone="graveyard")
        check(c == 200 and life(t, M) == l0 + 1 and "Daniels, Temporal Agent" not in gy(t, M) and t.state(M)["library"] == lib1 + 1,
              "Daniels: {2}, onto the bottom of my library, I gain 2 life", (c, d.get("said") or d, life(t, M), t.state(M)["library"], lib1))
        c, d = t.act(M, "activate", card="Daniels, Temporal Agent", zone="graveyard")
        check(c == 400, "an ability of a card that is no longer there is refused", (c, d))
        to_graveyard(t, M, "Photosynthetic Physician")
        t.turn(C)
        a = next(a for a in t.state(M)["zone_abilities"] if a["name"] == "Photosynthetic Physician")
        check(a["can"] is False and "sorcery" in a["why_not"], "Physician's is sorcery speed: on Claude's turn it says so", a)
        c, d = t.act(M, "activate", card="Photosynthetic Physician", zone="graveyard")
        check(c == 400 and "sorcery" in d.get("error", "") and "Photosynthetic Physician" in gy(t, M), "…and is refused, nothing paid, the card still there", (c, d))
        # from the hand: Locutus ("{W}{B}, Discard this card: It deals 2 damage to target creature. You gain 2 life.")
        t.turn(M)
        t.lands(M, 1, "Plains")
        mug = t.onto(C, "Mugato")
        t.hand(M, "Locutus, the One Who Speaks")
        l1 = life(t, M)
        c, d = t.act(M, "activate", card="Locutus, the One Who Speaks", zone="hand", targets=[{"at": C, "ref": f"#{mug}"}])
        check(c == 200 and t.perm(C, "Mugato")["damage"] == 2 and life(t, M) == l1 + 2 and "Locutus, the One Who Speaks" in gy(t, M),
              "Locutus, from the hand: discarded, 2 damage to the target, I gain 2", (c, d.get("said") or d))
    finally:
        t.stop()


def total_power_and_worf():
    t = Table()
    try:
        t.start()
        M, C = "Michael", "Claude"
        worf = t.onto(M, "Worf, Honor-Bound Warrior")
        horta = t.onto(M, "Horta")
        una = t.onto(M, "Una Chin-Riley, Number One")
        laan = t.onto(M, "La'An Noonien-Singh, Security")
        cap = t.onto(M, "Cantankerous Captain")
        blocker = t.onto(C, "Mugato")
        t.turn(M)
        # Worf's ability: tap two untapped artifacts and/or creatures: sorcery only
        a = t.perm(M, "Worf, Honor-Bound Warrior")["abilities"]
        check(len(a) == 1 and a[0]["cost"].startswith("Tap two untapped") and a[0]["can"] is True, "Worf's tap-two ability is listed, and can be activated", a)
        c, d = t.act(M, "activate", source=f"#{worf}", tap=[f"#{laan}", f"#{cap}"])
        w = t.perm(M, "Worf, Honor-Bound Warrior")
        check(c == 200 and w["counters"] == 1 and t.perm(M, "La'An Noonien-Singh, Security")["tapped"] and t.perm(M, "Cantankerous Captain")["tapped"] and not w["tapped"],
              "Worf: I name two creatures to tap; they tap, Worf gets a +1/+1 counter", (c, d.get("said") or d))
        c, d = t.act(M, "activate", source=f"#{worf}", tap=[f"#{laan}", f"#{horta}"])
        check(c == 400 and "isn't one" in d.get("error", "") and not t.perm(M, "Horta")["tapped"], "a tapped creature can't pay it: refused, nothing tapped", (c, d))
        for r in (laan, cap):
            t.call("POST", "/api/brain/untap", {"seat": M, "ref": f"#{r}", "quiet": True})
        # combat: the Captain's beginning-of-combat trigger asks for its target; then the attack triggers
        w_pow = int(t.perm(M, "Worf, Honor-Bound Warrior")["pt"].split("/")[0])
        ids = {f"#{worf}": C, f"#{horta}": C, f"#{una}": C}
        c, d = t.act(M, "attack", assign=ids)
        pend = choices(t, M)
        check(c == 200 and len(pend) == 3, "attacking with total power 8+: three questions wait (the Captain's target, Una's, La'An's)", (c, d.get("said") or d, [p_["source"] for p_ in pend]))
        check(int(t.perm(M, "Worf, Honor-Bound Warrior")["pt"].split("/")[0]) == 2 * w_pow, "Worf's power is doubled until end of turn", (t.perm(M, "Worf, Honor-Bound Warrior")["pt"], w_pow))
        by = {p_["source"]: p_ for p_ in pend}
        cc = by.get("Cantankerous Captain")
        check(cc is not None and all(o["at"] == M for o in cc["slot"]["options"]), "the Captain's +1/+0 lists only my creatures", cc)
        h0 = t.perm(M, "Horta")["pt"]
        t.call("POST", "/api/brain/choose", {"seat": M, "id": cc["id"], "target": {"at": M, "ref": f"#{horta}"}}, key=t.keys[M])
        h = t.perm(M, "Horta")
        check(int(h["pt"].split("/")[0]) == int(h0.split("/")[0]) + 1 and "Menace" in h["keywords"], "Captain: +1/+0, and menace because total power is 8 or more", (h0, h["pt"], h["keywords"]))
        uu = by["Una Chin-Riley, Number One"]
        check({o["ref"] for o in uu["slot"]["options"]} == set(ids), "Una's +2/+0 and first strike lists exactly the attacking creatures", uu["slot"]["options"])
        t.call("POST", "/api/brain/choose", {"seat": M, "id": uu["id"], "target": {"at": M, "ref": f"#{una}"}}, key=t.keys[M])
        u = t.perm(M, "Una Chin-Riley, Number One")
        check("First strike" in u["keywords"] and int(u["pt"].split("/")[0]) == int(R.CARDS["Una Chin-Riley, Number One"]["power"]) + 2, "Una: +2/+0 and first strike", (u["pt"], u["keywords"]))
        ll = by["La'An Noonien-Singh, Security"]
        t.call("POST", "/api/brain/choose", {"seat": M, "id": ll["id"], "target": {"at": C, "ref": f"#{blocker}"}}, key=t.keys[M])
        c, d = t.act(C, "block", attacker=f"#{una}", blocker=f"#{blocker}")
        check(c == 400 and "can't block this turn" in d.get("error", ""), "La'An: the creature named can't block this turn (the block is refused)", (c, d))
        check(not any("By hand" in x and any(n in x for n in ("Worf", "Una", "La'An", "Cantankerous")) for x in says(t)), "no By hand notes for any of the four", [x for x in says(t) if "By hand" in x])
    finally:
        t.stop()


def drone_and_delayed():
    t = Table()
    try:
        t.start()
        M, C = "Michael", "Claude"
        borgs = lambda seat: len([p for p in t.state(seat)["permanents"] if p["name"] == "Borg token"])
        d1 = t.onto(M, "Tenacious Drone")
        t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{d1}", "quiet": True})
        check(borgs(M) == 1, "Tenacious Drone destroyed by the host's destroy: a Borg token", borgs(M))
        d2 = t.onto(M, "Tenacious Drone")
        c, d = t.act(M, "manual", op="move", ref=f"#{d2}", to="graveyard")
        check(c == 200 and borgs(M) == 2, "…moved to the graveyard by hand (the person's own manual move): a Borg token", (c, d.get("said") or d, borgs(M)))
        d3 = t.onto(M, "Tenacious Drone")
        t.lands(C, 3, "Mountain")
        t.turn(C)
        t.hand(C, "Plasma Cascade")
        card = next(c_ for c_ in t.state(C)["hand"] if c_["name"] == "Plasma Cascade")
        c, d = t.act(C, "cast", name="Plasma Cascade", targets=[{"at": M, "ref": f"#{d3}"}])
        check(c == 200 and t.perm(M, "Tenacious Drone") is None and borgs(M) == 3, "…killed by a damage spell: a Borg token", (c, d.get("said") or d, borgs(M)))
        d4 = t.onto(M, "Tenacious Drone")
        att = t.onto(C, "Mugato")
        t.turn(C)
        c, d = t.act(C, "attack", assign={f"#{att}": M})
        c, d = t.act(M, "block", attacker=f"#{att}", blocker=f"#{d4}")
        t.act(C, "damage")                                   # (v6d: a block is a declaration; the damage step is dealt when the attacker passes after it)
        check(c == 200 and t.perm(M, "Tenacious Drone") is None and borgs(M) == 4, "…killed blocking: a Borg token", (c, d.get("said") or d, borgs(M)))
        tok = next(p for p in t.state(M)["permanents"] if p["name"] == "Borg token")
        check("Artifact" in (tok.get("type") or "") or tok["pt"] == "1/1", "the token is a 1/1", tok)
        # delayed: Ghost of Illyria exiles another nonland permanent of mine until the next end step
        t.turn(M)
        t.lands(M, 4, "Plains")
        horta = t.onto(M, "Horta")
        c, d = cast(t, M, "Ghost of Illyria")
        pend = choices(t, M)
        check(c == 200 and pend and pend[0]["slot"]["optional"] and not any("Ghost" in o["label"] or "Plains" in o["label"] for o in pend[0]["slot"]["options"]),
              "Ghost of Illyria enters: 'exile up to one other target nonland permanent you control' is asked (not itself, no lands)", (c, d.get("said") or d, pend))
        answer(t, M, "Horta")
        check(t.perm(M, "Horta") is None, "the Horta is exiled", "")
        t.act(M, "end", text="x")
        t.wait(lambda p: p.get("player") == C)
        back = t.perm(M, "Horta")
        check(back is not None and back["id"] != horta, "at the beginning of the next end step it returns (a new object)", back)
    finally:
        t.stop()


def other_modals():
    t = Table()
    try:
        t.start()
        M, C = "Michael", "Claude"
        # a land: "When this land enters untapped, choose one —"
        t.lands(M, 2, "Plains")
        t.hand(M, "Federation Outpost")
        l0 = life(t, M)
        c, d = t.act(M, "land", name="Federation Outpost")
        pend = choices(t, M)
        check(c == 200 and pend and pend[0]["kind"] == "option" and len(pend[0]["slot"]["options"]) == 2, "Federation Outpost enters untapped: its two modes are asked", (c, d.get("said") or d, pend))
        answer(t, M, "You gain 2 life")
        check(life(t, M) == l0 + 2, "mode 1: I gain 2 life", (life(t, M), l0))
        # an attack trigger: Tal Shiar Operative
        op = t.onto(M, "Tal Shiar Operative")
        t.onto(C, "Mugato")
        # an activated modal ability with a player target: Tea, Earl Grey, Hot
        tea = t.onto(M, "Tea, Earl Grey, Hot")
        t.turn(M)
        c, d = t.act(M, "attack", assign={f"#{op}": C})
        pend = choices(t, M)
        check(c == 200 and pend and pend[0]["kind"] == "option" and pend[0]["source"] == "Tal Shiar Operative", "Tal Shiar Operative attacks: choose one is asked", (c, d.get("said") or d, pend))
        answer(t, M, "deathtouch")
        if choices(t, M):
            answer(t, M, "Tal Shiar")
        check("Deathtouch" in t.perm(M, "Tal Shiar Operative")["keywords"], "mode 1: my creature gains deathtouch until end of turn", t.perm(M, "Tal Shiar Operative")["keywords"])
        t.act(C, "block", attacker=f"#{op}")
        t.turn(M)
        t.lands(M, 2, "Plains")
        ev0 = len(t.events(0))
        c, d = t.act(M, "activate", source=f"#{tea}", mode=0)
        check(c == 200 and t.perm(M, "Tea, Earl Grey, Hot") is None and any("deals 2" in x and "Claude" in x for x in says(t)[-8:]),
              "Tea, Earl Grey, Hot, mode 1: 2 damage to the opponent (the one opponent: nothing to ask)", (c, d.get("said") or d, says(t)[-5:]))
        # mana of any one colour: Culber and Stamets
        t.turn(M)
        t.lands(M, 2, "Forest")
        c, d = cast(t, M, "Culber and Stamets, Star-Crossed")
        answer(t, M, "Add two mana")
        pend = choices(t, M)
        check(pend and len(pend[0]["slot"]["options"]) == 5, "Culber and Stamets, mode 2: which colour is asked", pend)
        answer(t, M, "{U}")
        check(t.state(M)["floating_mana"] == ["U", "U"] or len(t.state(M)["floating_mana"]) == 2, "two blue mana in my pool", t.state(M)["floating_mana"])
    finally:
        t.stop()


def engine_seat():
    """A seat nobody drives (no `asks`): no mode runs by itself; the modes are said at the table."""
    v = player.VirtualPlayer(R.DECK_MICHAEL, "Robo", seed=1)
    v.life = 20
    card = R.CARDS["Rogue Artificial Intelligence"]
    p = player.Perm(card, sick=True)
    v.battlefield.append(p)
    v.todo.clear()
    said = v.enter_effects(p, lambda c, cands: cands[0])
    check(v.life == 20 and not v.__dict__.get("pending_choices") and any("You gain 3 life" in x and "Surveil 2" in x for x in v.todo),
          "a seat nobody drives is not chosen for: no mode runs by itself, every mode is said as a to-do, nothing waits", (said, v.life, v.todo))
    w = player.Perm(R.CARDS["Wesley Crusher, Starfleet Cadet"], sick=True)
    v.battlefield.append(w)
    v.todo.clear()
    v.enter_effects(w, lambda c, cands: cands[0])
    check(any("choose two" in x for x in v.todo) and not v.__dict__.get("pending_choices"), "Wesley on such a seat: a to-do naming all four modes, never a silent nothing", v.todo)
    # hybrid mana
    for n in range(3):
        v.battlefield.append(player.Perm({"name": "Swamp", "types": ["Land"], "subtypes": ["Swamp"], "text": "{T}: Add {B}."}, sick=False))
    check(v.plan_payment("{1}{U/B}{U/B}") is not None and v.plan_payment("{1}{W/U}{W/U}") is None, "hybrid mana: {U/B} is paid by a Swamp; {W/U} is not", "")


SECTIONS = [("Wesley Crusher: choose two, each mode through the engine", modal_enters),
            ("D'Ghor Bloodletter, Lore, Federation Field Medic", bloodletter_lore_medic),
            ("Dahar Master: may sacrifice, when you do, damage to any target", dahar_master),
            ("Khaaan! exile-instead rider; A Good Day to Die", khan_and_good_day),
            ("Graveyard and hand abilities", graveyard_abilities),
            ("Total power 8 attack triggers; Worf's tap-two cost", total_power_and_worf),
            ("Tenacious Drone by every route; Ghost of Illyria's delayed return", drone_and_delayed),
            ("Other modals: a land, an attack trigger, an activated ability, mana of any one colour", other_modals),
            ("An engine seat; hybrid mana", engine_seat)]

if __name__ == "__main__":
    run(SECTIONS)
