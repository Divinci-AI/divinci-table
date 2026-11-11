"""v6: counterspells. A pilot's spell is held while an opposing pilot who could counter it answers (casts the counterspell, or passes); a countered
spell goes to the graveyard without resolving; "unless its controller pays {N}" asks its controller.

    PYTHONDONTWRITEBYTECODE=1 python3 table/tests/counter_test.py"""
import os
import time
from v6_harness import R, Table, check, run, cast, answer, choices, says

M, C = "Michael", "Claude"


def setup(extra_env=None, claude_lands=3, michael_lands=6):
    old = {k: os.environ.get(k) for k in (extra_env or {})}
    os.environ.update(extra_env or {})
    try:
        t = Table()
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    t.start()                                              # Michael's main 1
    t.lands(M, michael_lands, "Forest")
    t.lands(C, claude_lands, "Island")
    return t


def phase(t):
    return t.call("GET", "/api/phase")[1]


def raw(t, seat, action, **body):
    return t.call("POST", f"/api/brain/{action}", {"seat": seat, **body}, key=t.keys[seat])


def no_holder_no_hold():
    t = setup(michael_lands=10)
    try:
        c, d = cast(t, M, "Mugato")
        check(c == 200 and t.perm(M, "Mugato") is not None and "held" not in phase(t), "nobody holds a counterspell: the spell resolves at once, as before", (c, d.get("said") or d))
        t.hand(C, "Consider the Prime Directive")
        for p in [x for x in t.state(C)["permanents"] if x["name"] == "Island"]:
            t.call("POST", "/api/brain/tap", {"seat": C, "ref": f"#{p['id']}", "quiet": True})
        c, d = cast(t, M, "Mugato")
        check(c == 200 and len(t.perms(M, "Mugato")) == 2 and "held" not in phase(t), "a counterspell in hand but no mana for it: no hold either", (c, d.get("said") or d))
        h = next(x for x in t.state(C)["hand"] if x["name"] == "Consider the Prime Directive")
        check(h["can_cast"] is False, "…and the counterspell says it can't be cast now", h.get("why_not"))
    finally:
        t.stop()


def pass_lets_it_resolve():
    t = setup()
    try:
        t.hand(C, "Consider the Prime Directive")
        hand0 = len(t.state(M)["hand"])
        c, d = cast(t, M, "Mugato")
        ph = phase(t)
        check(c == 200 and t.perm(M, "Mugato") is None and (ph.get("held") or {}).get("waiting") == [C] and ph["held"]["spell"] == "Mugato" and (d.get("private") or {}).get("held"),
              "Claude holds Consider the Prime Directive and three Islands: Michael's Mugato is cast, paid for, and waits", (c, d.get("said") or d, ph.get("held")))
        st = t.state(M)
        check(not any(h["name"] == "Mugato" for h in st["hand"][hand0:]) and "Mugato" not in (st.get("graveyard") or []) and len([p for p in st["permanents"] if p["name"] == "Forest" and p["tapped"]]) >= 1,
              "it is off his hand and his lands are tapped: not on the battlefield, not in the graveyard", (st.get("graveyard"), [p["tapped"] for p in st["permanents"]]))
        c, d = raw(t, M, "end", text="x")
        check(c == 409 and "waiting on passes" in d.get("error", "") and "Claude" in d.get("waiting", ""), "Michael can't move on while it waits (the table says who it waits for)", (c, d.get("error")))
        ev = [e for e in t.events(0) if e.get("type") == "attention" and e.get("kind") == "respond"]
        check(len(ev) == 1 and ev[0]["addressee"] == C and "Mugato" in ev[0]["text"], "Claude is told: respond or pass", ev)
        la = t.legal(C)["actions"]
        cs = [a for a in la if a["action"] == "cast" and a["body"]["name"] == "Consider the Prime Directive"]
        check(any(a["body"].get("modes") == [1] and a["body"].get("targets") == [{"at": M, "card": "Mugato"}] for a in cs), "Claude's legal list offers the counter mode, aimed at the held Mugato", cs)
        h = next(x for x in t.state(C)["hand"] if x["name"] == "Consider the Prime Directive")
        opts = [o for ms in h.get("mode_slots") or [] for sl in ms for o in sl.get("options") or [] if o.get("card") == "Mugato"]
        check(opts and opts[0]["at"] == M and "being cast" in opts[0]["label"], "…and so does his hand (the ordinary target dropdown: at + card)", h.get("mode_slots"))
        c, d = raw(t, C, "pass", player=M, step="main 1")
        check(c == 200 and d.get("responded") == "pass", "Claude passes", (c, d))
        check(t.perm(M, "Mugato") is not None and "held" not in phase(t), "…and the Mugato resolves onto the battlefield", t.state(M)["permanents"][-1:])
        check(any(x.startswith("Mugato resolves") for x in says(t)), "the log says it resolved", says(t)[-4:])
        c, d = t.act(M, "end", text="x")
        check(c == 200, "and Michael's turn goes on", (c, d.get("error")))
    finally:
        t.stop()


def countered_unless_pays():
    t = setup(michael_lands=7)
    try:
        t.hand(C, "Consider the Prime Directive"); t.hand(C, "Consider the Prime Directive"); t.hand(C, "Consider the Prime Directive")
        t.lands(C, 6, "Island")
        gy0 = len(t.state(M).get("graveyard") or [])
        cast(t, M, "Dominion Assault Force")               # four of his seven lands; an enters trigger (choose one) that must NOT happen if it is countered
        c, d = raw(t, C, "cast", name="Consider the Prime Directive", modes=[1], targets=[{"at": M, "card": "Dominion Assault Force"}])
        pend = choices(t, M)
        check(c == 200 and pend and pend[0]["kind"] == "option" and [o["label"][:9] for o in pend[0]["slot"]["options"]] == ["Pay {3}: ", "Don't pay"],
              "Claude counters it unless Michael pays {3}: Michael (three lands left) is asked, pay or not", (c, d.get("said") or d, pend))
        hp = phase(t).get("held") or {}
        check(t.perm(M, "Dominion Assault Force") is None and hp.get("waiting") == [M] and hp.get("paying") is True and hp.get("seconds_left", 0) > 5,
              "the spell still waits while he decides: the table now waits on HIM, with a fresh clock", hp)
        c, d = answer(t, M, "Don't pay")
        st = t.state(M)
        check(c == 200 and t.perm(M, "Dominion Assault Force") is None and (st.get("graveyard") or [])[gy0:] == ["Dominion Assault Force"] and "held" not in phase(t),
              "he doesn't pay: the spell goes to his graveyard without resolving", (c, d.get("said") or d, st.get("graveyard")))
        check(not choices(t, M) and not any("Dominion Assault Force resolves" in x for x in says(t)), "its enters trigger never happened (no mode is asked)", (choices(t, M), says(t)[-5:]))
        check("Consider the Prime Directive" in (t.state(C).get("graveyard") or []), "the counterspell is in Claude's graveyard", t.state(C).get("graveyard"))
        # he pays
        t.turn(M)
        t.lands(M, 1, "Forest")
        cast(t, M, "Mugato")
        raw(t, C, "cast", name="Consider the Prime Directive", modes=[1], targets=[{"at": M, "card": "Mugato"}])
        tapped0 = len([p for p in t.state(M)["permanents"] if p["name"] == "Forest" and p["tapped"]])
        c, d = answer(t, M, "Pay {3}")
        check(c == 200 and t.perm(M, "Mugato") is not None and len([p for p in t.state(M)["permanents"] if p["name"] == "Forest" and p["tapped"]]) == tapped0 + 3 and "held" not in phase(t),
              "next turn he pays {3}: three more lands tap and the Mugato resolves", (c, d.get("said") or d))
        # he can't pay
        t.turn(M)
        for p in [x for x in t.state(M)["permanents"] if x["name"] == "Forest"][:3]:
            t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{p['id']}", "quiet": True})
        gy1 = len(t.state(M).get("graveyard") or [])
        cast(t, M, "Mugato")
        held = phase(t).get("held")
        c, d = raw(t, C, "cast", name="Consider the Prime Directive", modes=[1], targets=[{"at": M, "card": "Mugato"}])
        check(held and c == 200 and not choices(t, M) and "Mugato" in (t.state(M).get("graveyard") or [])[gy1:] and "held" not in phase(t),
              "with no mana left he can't pay: countered at once, nothing is asked", (held, c, d.get("said") or d))
    finally:
        t.stop()


def wesley_counters():
    t = setup(claude_lands=4, michael_lands=7)
    try:
        t.lands(M, 2, "Plains")
        t.hand(C, "Wesley Crusher, Starfleet Cadet")
        t.hand(M, "Mugato")
        c, d = t.act(M, "cast", name="Mugato")
        check(c == 200 and "held" not in phase(t) and t.perm(M, "Mugato") is not None, "Wesley's mode counters only a noncreature spell: a creature spell isn't held for it", (c, phase(t).get("held")))
        hand0 = len(t.state(M)["hand"])
        c, d = cast(t, M, "Test of Conviction")            # a sorcery: "Battle Stations — Put a +1/+1 counter on each creature you control"
        if c != 200:
            c, d = t.act(M, "cast", name="Test of Conviction", modes=[1])
        check(c == 200 and (phase(t).get("held") or {}).get("spell") == "Test of Conviction", "a sorcery is held: Claude could flash in Wesley and counter it", (c, d.get("said") or d, phase(t).get("held")))
        c, d = raw(t, C, "cast", name="Wesley Crusher, Starfleet Cadet")
        check(c == 200 and t.perm(C, "Wesley Crusher, Starfleet Cadet") is not None, "Claude casts Wesley in response (flash); Wesley itself is not held", (c, d.get("said") or d))
        answer(t, C, "Counter target noncreature spell")
        answer(t, C, "Target player draws")
        if any("counter" in (p_["slot"].get("label") or "") for p_ in choices(t, C)):
            answer(t, C, "Test of Conviction")
        pend = choices(t, M)
        check(pend and pend[0]["kind"] == "option" and "Pay {2}" in pend[0]["slot"]["options"][0]["label"], "Wesley's first mode: Michael is asked to pay {2}", (pend, choices(t, C)))
        answer(t, M, "Don't pay")
        check("Test of Conviction" in (t.state(M).get("graveyard") or []) and t.perm(M, "Mugato")["counters"] == 0 and "held" not in phase(t),
              "not paid: Test of Conviction is countered (no counters were put on anything)", (t.state(M).get("graveyard"), t.perm(M, "Mugato")["counters"]))
        if choices(t, C):
            answer(t, C, "Claude")
            while choices(t, C):
                answer(t, C)
        check(not choices(t, C), "Wesley's other mode finishes too", choices(t, C))
    finally:
        t.stop()


def timeout_and_bot():
    t = setup(extra_env={"TABLE_RESPOND_SECS": "2"})
    try:
        t.hand(C, "Consider the Prime Directive")
        t0 = time.time()
        cast(t, M, "Mugato")
        check(phase(t).get("held") is not None, "held", "")
        end = time.time() + 8
        while time.time() < end and phase(t).get("held"):
            time.sleep(0.2)
        check(t.perm(M, "Mugato") is not None and time.time() - t0 < 7, "a seat that never answers doesn't hold the game: after the time limit the spell resolves", round(time.time() - t0, 1))
        c, d = t.act(M, "end", text="x")
        check(c == 200, "and the turn goes on", (c, d.get("error")))
    finally:
        t.stop()


def kicked_counter():
    t = setup(claude_lands=2, michael_lands=6)
    try:
        t.lands(C, 2, "Island")
        t.hand(C, "Highly Illogical")
        cast(t, M, "Mugato")                               # mana value 5: only the kicked Highly Illogical (counter target spell) may counter it
        held = phase(t).get("held")
        check(held and held.get("waiting") == [C], "a five-mana spell is held for Highly Illogical because it can be kicked ({1}{U}{1}{U} = four Islands)", held)
        la = [a for a in t.legal(C)["actions"] if a["action"] == "cast" and a["body"]["name"] == "Highly Illogical"]
        check(la and all(a["body"].get("kicked") for a in la) and la[0]["body"]["targets"] == [{"at": M, "card": "Mugato"}], "the only way to counter it is the kicked entry, aimed at the held spell", [a["body"] for a in la])
        c, d = raw(t, C, "cast", name="Highly Illogical", targets=[{"at": M, "card": "Mugato"}])
        check(c == 400, "unkicked it can't name a mana value 5 spell", (c, d.get("error")))
        c, d = raw(t, C, "cast", name="Highly Illogical", kicked=True, targets=[{"at": M, "card": "Mugato"}])
        check(c == 200 and t.perm(M, "Mugato") is None and "Mugato" in (t.state(M).get("graveyard") or []) and "held" not in phase(t), "kicked: Mugato is countered and goes to the graveyard unresolved", (c, d.get("said") or d))
    finally:
        t.stop()


def unpaid_at_zero():
    t = setup(extra_env={"TABLE_RESPOND_SECS": "3"}, michael_lands=8)
    try:
        t.hand(C, "Consider the Prime Directive")
        cast(t, M, "Mugato")
        raw(t, C, "cast", name="Consider the Prime Directive", modes=[1], targets=[{"at": M, "card": "Mugato"}])
        check(choices(t, M) and (phase(t).get("held") or {}).get("paying"), "he is asked to pay {3} (three lands are left)", phase(t).get("held"))
        end = time.time() + 12
        while time.time() < end and phase(t).get("held"):
            time.sleep(0.2)
        check("held" not in phase(t) and t.perm(M, "Mugato") is None and "Mugato" in (t.state(M).get("graveyard") or []) and not choices(t, M),
              "he never answers: when the clock reaches zero it was not paid, so the spell is countered (and nothing is left waiting)", (phase(t).get("held"), t.state(M).get("graveyard"), choices(t, M)))
        check(not any(p["tapped"] for p in t.state(M)["permanents"] if p["name"] == "Forest") or len([p for p in t.state(M)["permanents"] if p["name"] == "Forest" and p["tapped"]]) == 5,
              "no extra land was tapped for him", "")
        check(any("it was not paid" in x for x in says(t)), "the log says the time ran out unpaid", says(t)[-3:])
    finally:
        t.stop()


if __name__ == "__main__":
    run([("no counterspell, no hold", no_holder_no_hold),
         ("a pass lets it resolve", pass_lets_it_resolve),
         ("Consider the Prime Directive: countered unless its controller pays", countered_unless_pays),
         ("Wesley Crusher's counter mode", wesley_counters),
         ("the time limit", timeout_and_bot), ("pay-or-not left unanswered", unpaid_at_zero), ("Highly Illogical, kicked", kicked_counter)])
