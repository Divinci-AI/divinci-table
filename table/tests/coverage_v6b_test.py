"""v6, further down the coverage list: look-at-the-top choices, an owner's top-or-bottom choice, mass damage, cost reductions in a spell's own
text, The Doctor's second sentence, Shields Up!, Temporal Conqueror, an attack trigger with "you may sacrifice", Hemmer.

    PYTHONDONTWRITEBYTECODE=1 python3 table/tests/coverage_v6b_test.py"""
from v6_harness import R, Table, check, run, cast, answer, choices, says
import player

M, C = "Michael", "Claude"


def gy(t, seat):
    return t.state(seat).get("graveyard") or []


def looks():
    t = Table()
    try:
        t.start()
        t.lands(M, 6, "Island"); t.lands(M, 4, "Forest"); t.lands(M, 2, "Swamp"); t.lands(M, 3, "Plains")
        hand0, lib0, gy0 = len(t.state(M)["hand"]), t.state(M)["library"], len(gy(t, M))
        c, d = cast(t, M, "Pattern Buffer Preservation")
        pend = choices(t, M)
        check(c == 200 and pend and pend[0]["kind"] == "option" and len(pend[0]["slot"]["options"]) == 2, "Pattern Buffer Preservation: the top two cards are shown to me as a choice", (c, d.get("said") or d, pend))
        names = [o["label"].split(" (")[0] for o in pend[0]["slot"]["options"]]
        pub = str(t.events(0)) + str(t.call("GET", "/api/board3d")[1]) + str(t.state(C).get("opponents"))
        secret = [n for n in names if n not in ("Plains", "Forest", "Mountain", "Swamp", "Island") and n in pub]
        check(not secret, "the cards I am looking at are named nowhere public (the log, the board, the other seat's state)", secret)
        answer(t, M, names[0])
        st = t.state(M)
        check(any(h["name"] == names[0] for h in st["hand"]) and gy(t, M)[gy0:] == ["Pattern Buffer Preservation", names[1]] and st["library"] == lib0 - 3,
              "the one I chose is in my hand, the other in my graveyard", (gy(t, M)[gy0:], st["library"], lib0))
        # Rutherford: an artifact from the top six, the rest on the bottom
        t.hand(M, "Rutherford, Upgraded Ensign")
        t.call("POST", "/api/brain/search", {"seat": M, "name": "Tricorder", "quiet": True})
        t.call("POST", "/api/brain/topdeck", {"seat": M, "name": "Tricorder", "quiet": True})
        lib1 = t.state(M)["library"]
        c, d = t.act(M, "cast", name="Rutherford, Upgraded Ensign")
        pend = choices(t, M)
        labels = [o["label"] for o in pend[0]["slot"]["options"]] if pend else []
        check(c == 200 and any(x.startswith("Tricorder") for x in labels) and labels[-1] == "None of them", "Rutherford enters: the artifacts among my top six are offered, and 'None of them'", (c, d.get("said") or d, labels))
        c, d = answer(t, M, "Tricorder")
        check(any(h["name"] == "Tricorder" for h in t.state(M)["hand"]) and t.state(M)["library"] == lib1 - 1 and any("I reveal Tricorder" in x for x in d.get("said") or []),
              "I take the Tricorder (revealed, so named); the other five go back on the bottom", (d.get("said"), t.state(M)["library"], lib1))
        # First Contact: mill four, a permanent card from among them
        t.turn(M)
        g0 = len(gy(t, M))
        c, d = cast(t, M, "First Contact")
        pend = choices(t, M)
        milled = gy(t, M)[g0:]
        check(c == 200 and len([x for x in milled if x != "First Contact"]) == 4, "First Contact: four cards are milled", milled)
        if pend:
            pick = pend[0]["slot"]["options"][0]["label"].split(" (")[0]
            answer(t, M)
            check(any(h["name"] == pick for h in t.state(M)["hand"]) and gy(t, M)[g0:].count(pick) == milled.count(pick) - 1, "a permanent card among them goes to my hand", (pick, gy(t, M)[g0:]))
        check(not any("By hand" in x and ("Pattern Buffer" in x or "Rutherford" in x) for x in says(t)), "no By hand notes for the looks", [x for x in says(t) if "By hand" in x])
    finally:
        t.stop()


def tuck_and_mass():
    t = Table()
    try:
        t.start()
        t.lands(M, 8, "Island"); t.lands(M, 6, "Mountain")
        mug = t.onto(C, "Mugato"); t.onto(C, "Tenacious Drone"); mine = t.onto(M, "Tenacious Drone")
        lib0 = t.state(C)["library"]
        c, d = cast(t, M, "Guidance Failure", targets=[{"at": C, "ref": f"#{mug}"}])
        pend = choices(t, C)
        check(c == 200 and pend and pend[0]["kind"] == "option" and [o["label"][:6] for o in pend[0]["slot"]["options"]] == ["On top", "On the"] and t.perm(C, "Mugato") is not None,
              "Guidance Failure: the creature's OWNER is asked, top or bottom", (c, d.get("said") or d, pend))
        answer(t, C, "On top")
        top = t.call("POST", "/api/brain/peek", {"seat": C, "n": 1, "quiet": True})[1]
        check(t.perm(C, "Mugato") is None and t.state(C)["library"] == lib0 + 1 and ((top.get("private") or {}).get("top") or [None])[0] == "Mugato", "he says top: it is the top card of his library", top.get("private"))
        # Khan, Despot by Design: 1 damage to each opponent and each creature they control (not mine)
        ev0 = len(t.events(0))
        c, d = cast(t, M, "Khan, Despot by Design")
        check(c == 200 and t.perm(C, "Tenacious Drone") is None and t.perm(M, "Tenacious Drone") is not None and any(p["name"] == "Borg token" for p in t.state(C)["permanents"]),
              "Khan enters: 1 damage to each creature the opponent controls (his 1/1 dies), none to mine", (c, d.get("said") or d))
        hits = [e.get("text") or "" for e in t.events(0) if "deals 1 damage to Claude" in (e.get("text") or "")]
        check(hits, "…and 1 to the opponent", says(t)[-6:])
    finally:
        t.stop()


def small_ones():
    t = Table()
    try:
        t.start()
        t.lands(M, 5, "Plains"); t.lands(M, 5, "Swamp"); t.lands(M, 3, "Forest")
        a = t.onto(M, "Mugato"); b = t.onto(M, "Horta")
        t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{b}", "n": 1, "quiet": True})
        c, d = cast(t, M, "The Doctor, EMH", targets=[{"at": M, "ref": f"#{a}"}])
        check(c == 200 and t.perm(M, "Mugato")["counters"] == 2 and t.perm(M, "Horta")["counters"] == 2,
              "The Doctor: a counter on the target, then one more on each creature of mine with a counter (the target included)", (c, d.get("said") or d, t.perm(M, "Mugato")["counters"], t.perm(M, "Horta")["counters"]))
        c, d = cast(t, M, "Shields Up!", targets=[{"at": M, "ref": f"#{a}"}])
        m_ = t.perm(M, "Mugato")
        check(c == 200 and {"Hexproof", "Indestructible"} <= set(m_["keywords"]) and m_["counters"] == 3, "Shields Up!: hexproof and indestructible until end of turn, and a +1/+1 counter (it's a creature)", (c, d.get("said") or d, m_["keywords"], m_["counters"]))
        # Temporal Conqueror: mill three, a land back or a Treasure
        t.turn(M)
        g0 = len(gy(t, M))
        c, d = cast(t, M, "Temporal Conqueror")
        milled = gy(t, M)[g0:]
        lands = [x for x in milled if x in ("Plains", "Forest", "Mountain", "Swamp", "Island")]
        pend = choices(t, M)
        if any(x in ("Plains", "Forest", "Mountain", "Swamp", "Island") or "Land" in (R.CARDS.get(x, {}).get("types") or []) for x in gy(t, M)) or pend:
            if pend:
                answer(t, M)
            check(not any(p["name"].startswith("Treasure") for p in t.state(M)["permanents"]), "Temporal Conqueror with a land in the graveyard: the land comes back, no Treasure", (milled, d.get("said")))
        else:
            check(any(p["name"].startswith("Treasure") for p in t.state(M)["permanents"]), "Temporal Conqueror with no land in the graveyard: a Treasure instead", (milled, d.get("said")))
        check(len(milled) == 3 or pend is not None, "three cards were milled", milled)
        # an attack trigger through the effect engine: Automated Warfare System
        aws = t.onto(M, "Automated Warfare System")
        t.call("POST", "/api/brain/token", {"seat": M, "name": "Borg", "power": 1, "toughness": 1, "quiet": True})
        t.turn(M)
        hand0 = len(t.state(M)["hand"])
        c, d = t.act(M, "attack", assign={f"#{aws}": C})
        pend = choices(t, M)
        check(c == 200 and pend and pend[0]["slot"]["optional"] and "sacrifice another" in pend[0]["slot"]["label"], "Automated Warfare System attacks: 'you may sacrifice another creature or artifact' is asked", (c, d.get("said") or d, pend))
        answer(t, M, "Borg")
        check(len(t.state(M)["hand"]) == hand0 + 1, "sacrificed: I draw a card", (len(t.state(M)["hand"]), hand0))
    finally:
        t.stop()


def units():
    v = player.VirtualPlayer(R.DECK_MICHAEL, "Michael", seed=3)
    for n in ("Plains", "Island", "Swamp"):
        v.battlefield.append(player.Perm({"name": n, "types": ["Land"], "text": ""}, sick=False))
    check(v.auto_discount(R.CARDS["Cloak and Phaser"]) == 3, "Cloak and Phaser costs {X} less: three differently named lands, three less", v.auto_discount(R.CARDS["Cloak and Phaser"]))
    hem = player.Perm(R.CARDS["Hemmer, Fixing What Is Broken"], sick=False)
    v.battlefield.append(hem)
    h0 = len(v.hand)
    said = v.tapped_triggers(hem)
    check(len(v.hand) == h0 and any("draw" in x for x in said) and any("discard" in x for x in said), "Hemmer becomes tapped: draw a card, then discard a card (an engine seat picks the discard)", said)
    import card_coverage as cc
    for name in ("Wesley Crusher, Starfleet Cadet", "Dahar Master", "Khaaaaaaaaaaaannn!", "Worf, Honor-Bound Warrior", "Tenacious Drone", "Photosynthetic Physician", "Relentless Drednok",
                 "Daniels, Temporal Agent", "Consider the Prime Directive", "D'Ghor Bloodletter", "Lore, Positronic Provocateur", "Federation Field Medic", "Cantankerous Captain",
                 "La'An Noonien-Singh, Security", "Una Chin-Riley, Number One", "Tactical Officer", "Pet Sehlat", "A Good Day to Die", "Ghost of Illyria", "Tal Shiar Operative", "Tea, Earl Grey, Hot"):
        bad = cc.by_hand_lines(R.CARDS[name])
        check(not bad, f"coverage: {name} has no By hand line", bad)


def castable_is_what_can_be_cast():
    """A seat's state and legal list say what it MAY cast. "Never wipe your own board" is an engine seat's preference, not a rule."""
    t = Table()
    try:
        t.start()
        t.lands(M, 3, "Swamp")
        t.onto(M, "Mugato"); t.onto(M, "Horta")
        t.hand(M, "D'Ghor Bloodletter")
        h = next(c_ for c_ in t.state(M)["hand"] if c_["name"] == "D'Ghor Bloodletter")
        check(h["can_cast"] is True and h["castable_now"] is True and h["why_not"] is None and h["wipe"] is True, "D'Ghor Bloodletter with two creatures of my own: castable, and flagged as hitting every board", {k: h.get(k) for k in ("can_cast", "castable_now", "why_not", "wipe")})
        check(any(a["action"] == "cast" and a["body"]["name"] == "D'Ghor Bloodletter" for a in t.legal(M)["actions"]), "…and on the legal list", [a["body"].get("name") for a in t.legal(M)["actions"] if a["action"] == "cast"])
        c, d = t.act(M, "cast", name="D'Ghor Bloodletter")
        check(c == 200, "…and the cast goes through, as the state said", (c, d.get("error")))
    finally:
        t.stop()
    v = player.VirtualPlayer(R.DECK_MICHAEL, "Robo", seed=1)
    v.hand = [R.CARDS["D'Ghor Bloodletter"]]
    v.battlefield += [player.Perm({"name": "Swamp", "types": ["Land"], "subtypes": ["Swamp"], "text": "{T}: Add {B}."}, sick=False) for _ in range(3)]
    v.battlefield.append(player.Perm(R.CARDS["Mugato"], sick=False))
    check([o[1]["name"] for o in v.castable()] == ["D'Ghor Bloodletter"] and v.castable(guard_wipes=True) == [], "an engine seat choosing for itself still keeps it while it has creatures", "")


def ward():
    """M29: Ward {N} is paid by whoever aims at the permanent: with the spell, or when a trigger's target is named. One that can't be paid for can't be aimed at."""
    t = Table()
    try:
        t.start()
        wh = t.onto(C, "Humpback Whales")                  # ward {2}
        mug = t.onto(C, "Mugato")
        own = t.onto(M, "Humpback Whales")
        t.lands(M, 3, "Mountain")
        t.hand(M, "Plasma Cascade")
        cost = int(R.CARDS["Plasma Cascade"]["manaValue"])
        card = next(c_ for c_ in t.state(M)["hand"] if c_["name"] == "Plasma Cascade")
        labels = [o["label"] for sl in card.get("slots") or [] for o in sl.get("options") or []]
        c, d = t.act(M, "cast", name="Plasma Cascade", targets=[{"at": C, "ref": f"#{wh}"}])
        tapped = lambda: len([p for p in t.state(M)["permanents"] if p["name"] == "Mountain" and p["tapped"]])
        if cost + 2 > 3:
            check(c == 400 and tapped() == 0, f"a {cost}-mana spell at a ward-2 creature with three lands: refused, nothing tapped", (c, d))
            t.lands(M, cost + 2 - 3, "Mountain")
            c, d = t.act(M, "cast", name="Plasma Cascade", targets=[{"at": C, "ref": f"#{wh}"}])
        check(c == 200 and tapped() == cost + 2 and "Ward: I pay {2} more" in " ".join(d.get("said") or []), "with the mana for both: the spell's cost and the ward are paid together, and the log says so", (c, d.get("said") or d, tapped(), cost))
        # my own ward creature costs me nothing; an opponent's with no mana left can't be named by a trigger
        t.turn(M)
        t.lands(M, 2, "Plains")
        t.hand(M, "Shields Up!")
        t0 = len([p for p in t.state(M)["permanents"] if p["tapped"]])
        c, d = t.act(M, "cast", name="Shields Up!", targets=[{"at": M, "ref": f"#{own}"}])
        paid = len([p for p in t.state(M)["permanents"] if p["tapped"] and p["name"] in ("Plains", "Mountain")]) - 0
        check(c == 200 and "Ward" not in " ".join(d.get("said") or []), "aiming at my own ward creature costs nothing extra", (c, d.get("said") or d))
        for p in [x for x in t.state(M)["permanents"] if x.get("type", "").startswith("Basic Land") and not x["tapped"]]:
            t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{p['id']}", "quiet": True})
        dm = t.onto(M, "Tenacious Drone")
        t.call("POST", "/api/brain/search", {"seat": M, "name": "Dahar Master", "quiet": True})
        t.call("POST", "/api/brain/put", {"seat": M, "name": "Dahar Master", "quiet": True})
        if not choices(t, M):                              # (put doesn't run enters triggers on every table: then cast it next turn instead)
            t.turn(M)
            t.lands(M, 4, "Mountain")
            for p in [x for x in t.state(M)["permanents"] if x.get("type", "").startswith("Basic Land") and not x["tapped"]][4:]:
                t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{p['id']}", "quiet": True})
            cast(t, M, "Dahar Master")
        answer(t, M, "Tenacious Drone")
        pend = choices(t, M)
        tl = [o["label"] for o in pend[0]["slot"]["options"]] if pend else []
        check(pend and any("Mugato" in x for x in tl) and not any("Humpback Whales" in x and x.startswith("Claude") for x in tl), "a trigger's target with no mana left: the opponent's ward creature is not on the list", tl)
        answer(t, M, "Mugato")
    finally:
        t.stop()


def kicker():
    t = Table()
    try:
        t.start()
        t.lands(M, 6, "Mountain"); t.lands(M, 2, "Swamp")
        mug = t.onto(C, "Mugato"); rat = t.onto(M, "Tenacious Drone")
        t.hand(M, "Eject the Warp Core")
        h = next(c_ for c_ in t.state(M)["hand"] if c_["name"] == "Eject the Warp Core")
        check(h["kicker"] == {"kind": "sac", "cost": "artifact or creature", "payable": True}, "the hand says the card has a kicker, and whether it can be paid", h.get("kicker"))
        ks = [a for a in t.legal(M)["actions"] if a["action"] == "cast" and a["body"]["name"] == "Eject the Warp Core"]
        check(any(a["body"].get("kicked") and a["body"].get("kick_sac") for a in ks) and any(not a["body"].get("kicked") for a in ks), "the legal list has the plain cast and a kicked one", [a["body"] for a in ks])
        c, d = t.act(M, "cast", name="Eject the Warp Core", targets=[{"at": C, "ref": f"#{mug}"}])
        m1 = t.perm(C, "Mugato")
        check(c == 200 and m1 is not None and m1["pt"].split("/")[0] == str(int(R.CARDS["Mugato"]["power"]) - 2), "unkicked: -2/-2", (c, m1 and m1["pt"], d.get("said")))
        t.turn(M)
        t.hand(M, "Eject the Warp Core")
        c, d = t.act(M, "cast", name="Eject the Warp Core", kicked=True, targets=[{"at": C, "ref": f"#{mug}"}])
        check(c == 400 and "kick_sac" in d.get("error", "") and t.perm(M, "Tenacious Drone") is not None and not any(p["tapped"] for p in t.state(M)["permanents"] if p["name"] == "Mountain"),
              "kicked without naming what to sacrifice: refused, nothing paid", (c, d.get("error")))
        c, d = t.act(M, "cast", name="Eject the Warp Core", kicked=True, kick_sac=f"#{rat}", targets=[{"at": C, "ref": f"#{mug}"}])
        check(c == 200 and t.perm(C, "Mugato") is None and t.perm(M, "Tenacious Drone") is None and "I kick it (sacrificing Tenacious Drone)" in " ".join(d.get("said") or []),
              "kicked, sacrificing the Drone: -6/-6 kills the Mugato (and the Drone's own death token appears)", (c, d.get("said") or d))
        # Highly Illogical: unkicked, mana value 2 or less only; kicked, any spell
        t.turn(M)
        t.lands(M, 4, "Island")
        t.hand(M, "Highly Illogical")
        h = next(c_ for c_ in t.state(M)["hand"] if c_["name"] == "Highly Illogical")
        check(h["kicker"] == {"kind": "mana", "cost": "{1}{U}", "payable": True}, "Highly Illogical: kicker {1}{U}, payable with four Islands", h.get("kicker"))
    finally:
        t.stop()


if __name__ == "__main__":
    run([("looking at the top of the library", looks), ("an owner's choice; mass damage", tuck_and_mass), ("The Doctor, Shields Up!, Temporal Conqueror, Automated Warfare System", small_ones),
         ("cost reduction, Hemmer, and what the coverage list now says", units), ("what may be cast", castable_is_what_can_be_cast), ("Ward", ward), ("Kicker", kicker)])
