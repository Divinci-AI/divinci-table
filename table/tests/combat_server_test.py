"""Combat the SERVER computes (dogfood run 1, finding 1): a defender never types how much it took.

    ~/.venvs/table/bin/python table/tests/combat_server_test.py

A pilot says which of its creatures attack whom; the engine knows their power. The defender's `block` takes the number from
the table's own record (whatever amount it sends is ignored), unblocked damage is dealt when the attacker's `damage` comes or
the turn moves on, and the attacker's `damage` cannot claim more than the table dealt. Also: nothing but chat before the game
starts (and the refusal says how to start), and each seat can read the others' boards.

Remote calls only (X-Forwarded-For on every request); the creatures are made by the host, as a deck would; nothing else
is touched. One server on port 8831 (as stage1_guard_test), two pilots (Claude attacks, Fusion defends).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage1_guard_test as G  # noqa: E402
from stage1_guard_test import Server  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {str(detail)[:300]}"), flush=True)


def go(S, seat, action, **body):
    """A pilot's action, and when the table says it is still waiting on passes, those seats pass (a pilot with its own key,
    a person with NEXT) and it tries again — what the players would be doing between the steps."""
    for _ in range(40):
        c, r = S.brain(seat, action, **body)
        if c != 409 or "waiting" not in r:
            return c, r
        w = r["waiting"]
        for who in (w if isinstance(w, list) else str(w).replace(" ", "").split(",")):
            if who in ("Claude", "Fusion"):
                S.brain(who, "pass")
            elif who:
                S.next(who)
        time.sleep(0.05)
    return c, r


def perm(S, seat, name):
    return next(p for p in S.state(seat)["permanents"] if p["name"].startswith(name))


def onto_battlefield(S, seat, name):
    """The host puts a card from the seat's deck onto its battlefield: from the library, or from the hand when the shuffle
    (unseeded: --fair-seed off) already dealt it there. Returns that permanent."""
    c, r = S.host_brain(seat, "search", name=name, to="battlefield")
    if c != 200:
        c, r = S.host_brain(seat, "put", name=name)
    assert c == 200, (name, c, r)
    return [p for p in S.state(seat)["permanents"] if p["name"] == name][-1]


def main():
    with Server() as S:
        S.claim_all()
        lands = [h["name"] for h in S.state("Claude")["hand"] if h.get("land")]

        print("before the game")
        ph = S.phase()
        check("the phase says how to start the game", "highroll" in str(ph.get("start")), ph.get("start"))
        for act, body in (("land", {"name": lands[0] if lands else "Forest"}), ("attack", {"assign": {"#1": "Fusion"}}),
                          ("damage", {"hits": {}}), ("block", {"amount": 3})):
            c, r = S.brain("Claude", act, **body)
            check(f"'{act}' is refused before the game starts, with the way to start", c == 409 and "highroll" in str(r.get("error")), (c, r))
        c, r = go(S, "Claude", "begin")
        check("'begin' before the game names the way to start", c == 409 and "highroll" in str(r.get("error")), (c, r))

        print("the game")
        S.start_game()
        S.call("POST", "/api/phase/windows", {"on": False}, host=True)           # no step timers: the test walks the steps itself
        for name, p, t, kw in (("Bear", 3, 3, []), ("Wolf", 2, 2, []), ("Ogre", 5, 5, ["Trample"]), ("Imp", 4, 4, [])):
            c, r = S.host_brain("Claude", "token", name=name, power=p, toughness=t, keywords=kw)
            assert c == 200, (c, r)
        c, r = S.host_brain("Fusion", "token", name="Guard", power=1, toughness=4)
        assert c == 200, (c, r)
        c, r = go(S, "Claude", "begin")
        check("Claude begins its turn (its new creatures can attack)", c == 200, (c, r))
        life0 = S.state("Fusion")["life"]
        bear, wolf, ogre, imp = (perm(S, "Claude", n) for n in ("Bear", "Wolf", "Ogre", "Imp"))

        print("a lie about the damage changes nothing")
        c, r = go(S, "Claude", "attack", assign={f"#{bear['id']}": "Fusion"})
        check("Claude attacks Fusion with the 3/3", c == 200, (c, r))
        inc = S.state("Fusion").get("incoming")
        check("Fusion is told who attacks it, with the table's number (3)",
              inc and inc[0]["power"] == 3 and inc[0]["attacker"] == "Claude", inc)
        c, r = S.brain("Fusion", "block", amount=0, attacker="Bear token")
        l1 = S.state("Fusion")["life"]
        check("Fusion says it takes 0 — it takes the 3 the table knows", c == 200 and l1 == life0 - 3, (c, r, life0, l1))
        c, r = S.brain("Fusion", "block", amount=0)
        check("a second 'block' with nothing attacking is refused", c == 400 and "nothing is attacking" in str(r.get("error")), (c, r))
        c, r = go(S, "Claude", "damage", hits={f"#{bear['id']}": ["Fusion", 99]})
        l2 = S.state("Fusion")["life"]
        check("Claude claiming 99 damage deals nothing more", c == 200 and l2 == l1, (c, r, l1, l2))

        print("unblocked damage lands without the defender doing anything")
        c, r = go(S, "Claude", "attack", assign={f"#{wolf['id']}": "Fusion"})
        check("Claude attacks with the 2/2", c == 200, (c, r))
        c, r = go(S, "Claude", "damage")
        l3 = S.state("Fusion")["life"]
        check("Fusion never answered: the table dealt the 2 when Claude's damage came", c == 200 and l3 == l2 - 2, (c, r, l2, l3))

        print("a block, trample over it, and the lie is still ignored")
        c, r = go(S, "Claude", "attack", assign={f"#{ogre['id']}": "Fusion"})
        guard = perm(S, "Fusion", "Guard")
        c2, r2 = S.brain("Fusion", "block", blocker=f"#{guard['id']}", amount=0, trample=False, attacker="Ogre")
        l4 = S.state("Fusion")["life"]
        check("5 trample into a 1/4: 1 goes through (table's numbers, not the 0 sent)", c2 == 200 and l4 == l3 - 1, (c2, r2, l3, l4))
        check("the blocker that took lethal is gone from its board", not any(p["name"].startswith("Guard") for p in S.state("Fusion")["permanents"]))
        c, r = go(S, "Claude", "damage")
        check("Claude's damage after it adds nothing", c == 200 and S.state("Fusion")["life"] == l4, (c, r))

        print("each seat sees the other's board")
        opp = S.state("Claude").get("opponents", {}).get("Fusion")
        check("Claude can read Fusion's side (life, lands, creatures)", opp and opp["life"] == l4 and "creatures" in opp, opp)
        check("…and not Fusion's hand", opp and not isinstance(opp.get("hand"), list), opp and opp.get("hand"))
        check("Fusion sees Claude's creatures, tapped or not", any(c_["name"].startswith("Bear") and c_["tapped"] for c_ in
                                                                    S.state("Fusion")["opponents"]["Claude"]["creatures"]), S.state("Fusion")["opponents"]["Claude"])

        print("an unblocked attacker whose turn ends still hits")
        c, r = go(S, "Claude", "attack", assign={f"#{imp['id']}": "Fusion"})
        check("Claude attacks with the 4/4 and never deals damage itself", c == 200, (c, r))
        c, r = go(S, "Claude", "end")
        time.sleep(2.0)
        l5 = S.state("Fusion")["life"]
        check("the turn passed on and the table dealt the 4", l5 == l4 - 4, (c, l4, l5))

        print("the player on the play skips the first draw; the rest of the new rules")
        check("with four at the table the first player does draw (Commander): eight cards", len(S.state("Claude")["hand"]) == 8, len(S.state("Claude")["hand"]))
        c, r = S.brain("Claude", "pass")
        check("a pass in your own turn names who moves it on, not 'waiting on None'", c == 409 and "None" not in str(r.get("error")), (c, r))

        print("effects the engine does not model: only when the source card says so")
        note = onto_battlefield(S, "Claude", "Ransom Note")
        lib0, gy0 = S.state("Claude")["library"], len(S.state("Claude")["graveyard"])
        c, r = S.brain("Claude", "effect", source=f"#{note['id']}", kind="surveil", put="graveyard")
        st = S.state("Claude")
        check("Ransom Note says surveil: Claude surveils 1 and bins the top card (library -1, graveyard +1)",
              c == 200 and st["library"] == lib0 - 1 and len(st["graveyard"]) == gy0 + 1 and r["private"].get("top"), (c, r.get("error")))
        c, r = S.brain("Claude", "effect", source=f"#{note['id']}", kind="destroy", target="#1", at="Fusion")
        check("…but Ransom Note says nothing about destroying: refused, with its text", c == 400 and "doesn't say" in str(r.get("error")), (c, r))
        for _ in range(2):
            S.brain("Claude", "effect", source=f"#{note['id']}", kind="surveil")
        c, r = S.brain("Claude", "effect", source=f"#{note['id']}", kind="surveil")
        check("…and not more often than a card can do it in a turn", c == 400 and "as often" in str(r.get("error")), (c, r))
        c, r = S.brain("Claude", "effect", source="#99999", kind="draw")
        check("a source that is not on your battlefield is refused", c == 400, (c, r))

        print("activated abilities through the pilot's own key")
        me_ = onto_battlefield(S, "Claude", "Mirror Entity")
        for _ in range(3):
            onto_battlefield(S, "Claude", "Forest")
        c, r = S.brain("Claude", "activate", source=f"#{me_['id']}", x=9)
        check("Mirror Entity X=9 with three Forests: refused, with the reason", c == 400 and "can't pay" in str(r.get("error")), (c, r.get("error")))
        c, r = S.brain("Claude", "activate", source=f"#{me_['id']}", x=2)
        pts = [p["pt"] for p in S.state("Claude")["permanents"] if p.get("pt")]
        check("Mirror Entity X=2: every creature Claude controls is 2/2", c == 200 and pts and all(x == "2/2" for x in pts), (c, r.get("error"), pts))
        c, r = S.brain("Claude", "activate", source=f"#{me_['id']}", x=2)
        check("…and a second activation with no mana left is refused", c == 400, (c, r.get("error")))

        print("a modal ability: the pilot names the mode")
        for land in ("Mountain", "Plains"):
            onto_battlefield(S, "Claude", land)
        st0 = S.state("Claude")
        c, r = S.brain("Claude", "activate", source=f"#{note['id']}")
        check("Ransom Note with no mode: refused, the modes listed", c == 400 and "--mode" in str(r.get("error")) and "2: Draw" in str(r.get("error")), (c, r))
        c, r = S.brain("Claude", "activate", source=f"#{note['id']}", mode="draw")
        check("…a mode that is not a number is refused (not a server error)", c == 400 and "number" in str(r.get("error")), (c, r))
        c, r = S.brain("Claude", "activate", source=f"#{note['id']}", mode=1)
        st1 = S.state("Claude")
        check("…the goad mode is refused before paying: Ransom Note stays, nothing tapped",
              c == 400 and "nothing was paid" in str(r.get("error")) and any(p["id"] == note["id"] for p in st1["permanents"])
              and sum(p["tapped"] for p in st1["permanents"]) == sum(p["tapped"] for p in st0["permanents"]), (c, r.get("error")))
        c, r = S.brain("Claude", "activate", source=f"#{note['id']}", mode=2)
        st2 = S.state("Claude")
        check("…mode 2 draws a card and sacrifices Ransom Note", c == 200 and len(st2["hand"]) == len(st0["hand"]) + 1
              and not any(p["id"] == note["id"] for p in st2["permanents"]) and "Ransom Note" in st2["graveyard"], (c, r.get("error")))

        print("a remote pilot reaches its own journal and nobody else's")
        c, r = S.call("GET", "/api/journal/due?seat=Claude", key=S.claim("Claude"))
        check("journal-due for your own seat answers from a remote device", c == 200 and "due" in r, (c, r))
        c, r = S.call("GET", "/api/journal/due?seat=Fusion", key=S.claim("Claude"))
        check("…another seat's journal is refused", c == 403, (c, r))
        c, r = S.call("GET", "/api/journal/due?seat=Claude")
        check("…and so is no key at all", c == 403, (c, r))

        print("a seat with a deck cannot hand the table its own attack numbers")
        c, r = S.call("POST", "/api/declare/attack", {"by": "Claude", "key": S.claim("Claude"),
                                                      "attacks": [{"attacker": "X", "target": "Fusion", "power": 40}]}, key=S.claim("Claude"))
        check("a pilot's /api/declare/attack is refused (the engine attacks)", c == 409, (c, r))

    with Server(order="Claude,Fusion", humans=False) as S2:                                      # two players: the one on the play skips its first draw
        S2.claim_all()
        S2.start_game()
        c, r = go(S2, "Claude", "begin")
        check("two players: Claude on the play begins with no draw (seven cards) and is told so",
              c == 200 and len(S2.state("Claude")["hand"]) == 7 and any("no draw" in x for x in r.get("said", [])), (c, len(S2.state("Claude")["hand"]), r.get("said")))
        go(S2, "Claude", "end")
        time.sleep(1.5)
        c, r = go(S2, "Fusion", "begin")
        check("…and the second player does draw (eight cards)", c == 200 and len(S2.state("Fusion")["hand"]) == 8, (c, len(S2.state("Fusion")["hand"])))

    print("a cost that sacrifices ANOTHER permanent: the pilot names it (--sac)")
    nyx = ["--ai", "Nyx|Captain N'ghathrod|", "--ai-deck", str(G.REPO / "decks/nghathrod.json"), "--pilot", "Nyx"]
    with Server(order="Claude,Fusion,Nyx", humans=False, extra=nyx) as S4:
        S4.claim_all()
        S4.claim("Nyx")
        S4.start_game()
        strider = onto_battlefield(S4, "Nyx", "Woe Strider")
        c, r = S4.host_brain("Nyx", "token", name="Goat", power=0, toughness=1)
        assert c == 200, (c, r)
        goat = perm(S4, "Nyx", "Goat")
        c, r = S4.host_brain("Claude", "token", name="Bear", power=2, toughness=2)
        bear = perm(S4, "Claude", "Bear")
        lib0 = S4.state("Nyx")["library"]
        c, r = S4.brain("Nyx", "activate", source=f"#{strider['id']}")
        check("Woe Strider with no --sac: refused, and the Goat is offered", c == 400 and f"#{goat['id']}" in str(r.get("error")), (c, r))
        c, r = S4.brain("Nyx", "activate", source=f"#{strider['id']}", sac=f"#{bear['id']}")
        check("…Claude's Bear is not Nyx's to sacrifice: refused, and the Bear is still on Claude's board",
              c == 400 and any(p["id"] == bear["id"] for p in S4.state("Claude")["permanents"]), (c, r))
        c, r = S4.brain("Nyx", "activate", source=f"#{strider['id']}", sac=f"#{strider['id']}")
        check("…nor Woe Strider itself ('another')", c == 400 and "ANOTHER" in str(r.get("error")), (c, r))
        c, r = S4.brain("Nyx", "activate", source=f"#{strider['id']}", sac=f"#{goat['id']}")
        st = S4.state("Nyx")
        check("…--sac the Goat: the Goat is gone, Woe Strider stays, and Nyx privately sees the card it scried",
              c == 200 and not any(p["id"] == goat["id"] for p in st["permanents"]) and any(p["id"] == strider["id"] for p in st["permanents"])
              and r.get("private", {}).get("top") and st["library"] == lib0, (c, r.get("error"), r.get("private")))

    print("a bounce land: the pilot chooses which land returns (--bounce)")
    with Server(order="Claude,Fusion", humans=False) as S5:
        S5.claim_all()
        S5.start_game()
        c, r = S5.host_brain("Claude", "search", name="Gruul Turf")                 # into the hand (or it is there already)
        assert c == 200 or "Gruul Turf" in [h["name"] for h in S5.state("Claude")["hand"]], (c, r)
        forest = onto_battlefield(S5, "Claude", "Forest")
        mountain = onto_battlefield(S5, "Claude", "Mountain")
        c, r = go(S5, "Claude", "begin")
        assert c == 200, (c, r)
        c, r = S5.brain("Claude", "land", name="Gruul Turf")
        st = S5.state("Claude")
        check("Gruul Turf with no --bounce: refused, the lands it could return listed, nothing played",
              c == 400 and f"#{forest['id']}" in str(r.get("error")) and not st["land_played"]
              and "Gruul Turf" in [h["name"] for h in st["hand"]], (c, r))
        S5.host_brain("Claude", "token", name="Bear", power=2, toughness=2)
        c, r = S5.brain("Claude", "land", name="Gruul Turf", bounce=f"#{perm(S5, 'Claude', 'Bear')['id']}")
        check("…a permanent of Claude's that is not a land is refused", c == 400 and "is not one" in str(r.get("error"))
              and not S5.state("Claude")["land_played"], (c, r))
        fusion_land = onto_battlefield(S5, "Fusion", "Forest")
        c, r = S5.brain("Claude", "land", name="Gruul Turf", bounce=f"#{fusion_land['id']}")
        check("…so is Fusion's land", c == 400 and not S5.state("Claude")["land_played"], (c, r))
        mountains0 = [h["name"] for h in S5.state("Claude")["hand"]].count("Mountain")
        c, r = S5.brain("Claude", "land", name="Gruul Turf", bounce=f"#{mountain['id']}")
        st = S5.state("Claude")
        check("…--bounce the Mountain: Gruul Turf is played, the Mountain is back in hand, the Forest stays",
              c == 200 and st["land_played"] and not any(p["id"] == mountain["id"] for p in st["permanents"])
              and any(p["id"] == forest["id"] for p in st["permanents"])
              and [h["name"] for h in st["hand"]].count("Mountain") == mountains0 + 1, (c, r))

    os.environ["TABLE_AI_PASS_SECS"] = "3"                                         # a silent seat: the clock is short here
    with Server(order="Claude,Fusion", humans=False) as S3:
        S3.claim_all()
        S3.start_game()
        c, r = S3.brain("Claude", "begin")
        pas = S3.phase().get("passes", {})
        check("Fusion has not answered: the table shows its clock (seconds left) to everyone",
              c == 409 and pas.get("next") == "Fusion" and 0 < (pas.get("seconds_left") or 0) <= 3, (c, pas))
        time.sleep(3.5)
        c, r = S3.brain("Claude", "begin")
        evs = S3.call("GET", "/api/events?since=0")[1].get("events", [])
        auto = [e for e in evs if e.get("type") == "pass" and e.get("by") == "Fusion" and e.get("auto")]
        check("after the clock the table passes for Fusion, and the log marks it as automatic (not an answer)",
              c in (200, 409) and auto and auto[0].get("timeout") and auto[0].get("secs") == 3.0, (c, auto[:1]))
    del os.environ["TABLE_AI_PASS_SECS"]

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
