"""Gaps found by a mutation review of the v7a work (retro_round4_test.py passes, but these one-line breakages did not fail it).

Each section names the break it exists to catch (the mutant id is from the review's table). Same harness as retro_round4_test: tables on free ports,
the seat's own key, no Gemma.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 TABLE_TEST_DECKS=$(python table/tests/fixture_decks.py) python3 table/tests/retro_round4_gaps_test.py
    GAPS_ONLY="concede|stall"   only the sections whose title names one of these
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import retro_round4_test as RR  # noqa: E402  (its helpers, its card pool, its free-port rule)
import player  # noqa: E402

R, I, check, M, C = RR.R, RR.I, RR.R.check, RR.M, RR.C
card, set_hand, pending, answer, say_text = RR.card, RR.set_hand, RR.pending, RR.answer, RR.say_text
S = "Sam"


class Table3(R.Table):
    """Claude, Michael and a THIRD driven seat, Sam: the only way to ask what a seat that is neither the asker nor the one looked at can read."""

    def __init__(self):
        super().__init__(extra=("--ai", "Sam|", "--ai-deck", R.DECK_CLAUDE, "--pilot", "Sam"))

    def start(self):
        self.keys = {n: self.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in (C, M, S)}
        self.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)
        self.call("POST", "/api/phase/next", {"by": C, "key": self.keys[C]}, key=self.keys[C])
        self.wait(lambda p: p.get("player") == C and p.get("begun") and p.get("step") == "main 1")
        self.turn(M, fresh=False)


def table_concede_auth():
    print("Table (c1, c2): conceding is refused for another seat's key and for no key at all; the seat's own key and the host work")
    t = R.Table()
    try:
        t.start()
        ev0 = I.last_event(t)
        c, d = t.call("POST", "/api/brain/concede", {"seat": C}, key=t.keys[M])            # Michael's key, Claude's seat
        check(c in (401, 403), "another seat's key cannot concede for Claude", (c, d))
        c, d = t.call("POST", "/api/brain/concede", {"seat": C}, host=False)               # no key, no token
        check(c in (401, 403), "no key and no token: refused", (c, d))
        c, d = t.call("POST", "/api/brain/concede", {"seat": C}, key="not-a-key")
        check(c in (401, 403), "a key that is nobody's: refused", (c, d))
        evs = [e for e in t.events() if e["id"] > ev0]
        check(not [e for e in evs if e["type"] == "game-over"] and not any(str(e.get("text") or "").endswith("concedes.") for e in evs),
              "…and nothing happened: no game-over, no 'concedes.' line", [e["type"] for e in evs][-6:])
        c, d = t.call("POST", "/api/brain/concede", {"seat": M}, key=t.keys[M])
        check(c == 200 and d.get("over") and d.get("winner") == C, "(control) the seat's own key still concedes, and the other side wins", (c, d))
    finally:
        t.stop()


def table_reveal_other_seats():
    print("Table (a2, a3, b1, b2, i4, j4), THREE seats: while Michael is asked about Claude's hand, Sam's state, legal list and refusals carry nothing of it; the table says it waits on Michael; nobody else can answer")
    t = Table3()
    try:
        t.start()
        t.lands(M, 5, "Swamp")
        marks = ["Sela, Tal Shiar Paradox", "Jett Reno, Problem Solver", "Kira Nerys, Firebrand"]
        set_hand(t, C, [*marks, "Plains", "Plains"])
        t.hand(M, "Stray from History")
        c, d = t.act(M, "cast", name="Stray from History", modes=[2], targets=[{"player": C}])
        ps = pending(t, M)
        check(c == 200 and len(ps) == 1, "set-up: Michael is asked", (c, d, ps))
        if not ps:
            return
        p = ps[0]
        label = p["slot"]["label"]
        # the table says it is waiting on Michael for a choice (public words: the source, never the hand)
        wo = t.call("GET", "/api/phase")[1].get("waiting_on") or []
        check(any(w["seat"] == M and w["what"] == "a choice (Stray from History)" for w in wo) and not any(m in str(wo) for m in marks), "/api/phase waits on Michael for 'a choice (Stray from History)', and says no card", wo)
        # what the third seat can read
        c409, d409 = t.call("POST", "/api/brain/damage", {"seat": S}, key=t.keys[S])       # (Next/End/Attack say 'not your turn' first; damage and begin reach the question gate)
        check(c409 == 409 and d409.get("code") == "choice-pending-other" and (d409.get("pending_choice") or {}).get("source") == "Stray from History",
              "Sam's Damage is refused naming the source of Michael's open question (j4: the gate holds a third seat too)", (c409, d409.get("code"), d409.get("pending_choice")))
        blobs = {"Sam's state": str(t.state(S)), "Sam's legal list": str(t.legal(S)), "Sam's refused Damage (the 409 body)": str((c409, d409))}
        for what, blob in blobs.items():
            leaked = [m for m in marks if m in blob]
            check(not leaked and label not in blob and "Choose the nonland card" not in blob and "I'm choosing from" not in blob, f"{what} holds nothing of Claude's hand or Michael's question", leaked or blob[:160])
        claude_blob = str(t.state(C)) + str(t.legal(C))
        check(label not in claude_blob and "Choose the nonland card" not in claude_blob, "…and neither do Claude's own state or legal list (he is looked at, not asked)")
        # who may answer it, and with what
        opt = {k: v for k, v in p["slot"]["options"][0].items() if k != "label"}
        for who in (C, S):
            c, d = t.act(who, "choose", id=p["id"], target=opt)
            check(c in (400, 409) and pending(t, M) and len(t.state(C)["hand"]) == 5, f"{who} cannot answer Michael's question (his id, their own key)", (c, d))
        c, d = t.act(M, "choose", id=987654, target=opt)
        check(c in (400, 409) and pending(t, M) and len(t.state(C)["hand"]) == 5, "an id that is not an open question is refused (not applied to the first one)", (c, d))
        n_opts = len(p["slot"]["options"])
        c, d = t.act(M, "choose", id=p["id"], target={"at": M, "ref": f"opt:{n_opts}"})
        check(c in (400, 409) and pending(t, M) and len(t.state(C)["hand"]) == 5, f"opt:{n_opts} (one past the last of {n_opts} options) is refused", (c, str(d)[:200]))
        c, d = answer(t, M, pending(t, M)[0], "Jett Reno, Problem Solver")
        check(c == 200 and "Jett Reno, Problem Solver" not in RR.names(t.state(C)["hand"]) and not pending(t, M), "(control) the right id and a listed option still work", (c, d))
    finally:
        t.stop()


def table_concede_three_seats():
    print("Table (c4), THREE seats: a seat that has conceded cannot concede again, and the game goes on without it")
    t = Table3()
    try:
        t.start()
        c, d = t.call("POST", "/api/brain/concede", {"seat": S}, key=t.keys[S])
        check(c == 200 and d.get("over") is False and sorted(d.get("alive") or []) == [C, M], "Sam concedes: two seats are left and the game is not over", (c, d))
        c, d = t.call("POST", "/api/brain/concede", {"seat": S}, key=t.keys[S])
        check(c == 409 and "already conceded" in str(d.get("error")), "…a second concede from Sam is refused", (c, d))
        c, d = t.call("POST", "/api/brain/concede", {"seat": S})
        check(c == 409 and "already conceded" in str(d.get("error")), "…and from the host", (c, d))
    finally:
        t.stop()


def table_priority_after_a_spell():
    print("Table (k1), THREE seats: a spell cast out of turn sends priority round again: a seat that had already passed is asked again")
    t = Table3()
    try:
        t.start()
        t.lands(C, 2, "Plains")
        t.lands(S, 2, "Plains")
        set_hand(t, C, ["Set Phasers to . . .", "Plains"])          # an instant each: both can respond, so the table waits for both
        set_hand(t, S, ["Set Phasers to . . .", "Plains"])
        horta = t.onto(M, "Horta")
        ph = t.call("GET", "/api/phase")[1]
        check(ph.get("player") == M and ph["passes"].get("need") == [S, C] and ph["passes"].get("next") == S, "Michael's main 1: the table waits on Sam, then Claude", ph.get("passes"))
        c, d = t.call("POST", "/api/brain/pass", {"seat": S, "player": ph["player"], "step": ph["step"]}, key=t.keys[S])
        ps = t.call("GET", "/api/phase")[1]["passes"]
        check(c == 200 and ps.get("passed") == [S] and ps.get("next") == C, "Sam passes: now Claude's turn to answer", (c, ps))
        c, d = t.act(C, "cast", name="Set Phasers to . . .", modes=[1], targets=[{"at": M, "ref": f"#{horta}"}])        # mode 1 (Stun): tap target creature
        ps = t.call("GET", "/api/phase")[1]["passes"]
        check(c == 200 and S not in (ps.get("passed") or []) and ps.get("next") == S, "Claude casts Stun in response: Sam's earlier pass no longer stands, the table waits on Sam again", (c, ps))
    finally:
        t.stop()


def table_refused_cast_keeps_priority():
    print("Table (k1b), THREE seats: a REFUSED out-of-turn cast changes nothing: the pass round stands and the caster has not passed")
    t = Table3()
    try:
        t.start()
        t.lands(C, 2, "Plains")
        t.lands(S, 2, "Plains")
        set_hand(t, C, ["Set Phasers to . . .", "Plains"])
        set_hand(t, S, ["Set Phasers to . . .", "Plains"])
        t.onto(M, "Horta")
        ph = t.call("GET", "/api/phase")[1]
        t.call("POST", "/api/brain/pass", {"seat": S, "player": ph["player"], "step": ph["step"]}, key=t.keys[S])
        before = t.call("GET", "/api/phase")[1]["passes"]
        check(before.get("passed") == [S] and before.get("next") == C, "Sam has passed and the table waits on Claude", before)
        c, d = t.act(C, "cast", name="Set Phasers to . . .", modes=[1], targets=[{"at": M, "ref": "#9999"}])        # a creature that is not there: refused
        after = t.call("GET", "/api/phase")[1]["passes"]
        check(c != 200, "Claude's cast at a creature that is not there is refused", (c, d))
        check(after.get("passed") == before.get("passed") and after.get("next") == before.get("next"),
              "…and the pass round is untouched: Sam's pass still stands, Claude has not passed (a typo must not cost a response window)", (before, after))
    finally:
        t.stop()


def table_held_does_not_name_the_holder():
    print("Table (held), THREE seats: only Claude holds a counterspell; the table is told every opponent may answer, never which one does")
    R.POOL.update({"Highly Illogical": 6, "Tricorder": 6})
    R.DECK_CLAUDE = R.deck_file("Claude-held")
    R.DECK_MICHAEL = R.deck_file("Michael-held")
    t = Table3()
    try:
        t.start()
        t.call("POST", "/api/phase/windows", {"on": True, "by": "host"}, host=False)
        t.lands(C, 2, "Island")
        set_hand(t, C, ["Highly Illogical", "Plains"])
        set_hand(t, S, ["Plains", "Plains"])
        set_hand(t, M, ["Tricorder", "Plains"])
        t.lands(M, 1, "Plains")
        c, d = t.call("POST", "/api/brain/cast", {"seat": M, "name": "Tricorder"}, key=t.keys[M])
        ph = t.call("GET", "/api/phase")[1]
        held = ph.get("held") or {}
        check(c == 200 and held.get("spell") == "Tricorder", "Michael's spell is held", (c, held, d))
        check(held.get("waiting") == [C, S], "/api/phase held.waiting lists both opponents, not only the holder", held.get("waiting"))
        evs = [e for e in t.events() if e.get("type") == "held" and e.get("waiting") is not None]
        check(evs and evs[-1]["waiting"] == [C, S], "the public held event lists both opponents", [e.get("waiting") for e in evs])
        wo = {(w["seat"]) for w in ph.get("waiting_on", []) if "response" in w.get("what", "")}
        check(wo == {C, S}, "waiting_on names both for the response", ph.get("waiting_on"))
        # the real holder is still the only one ASKED (a private attention)
        asked = [e.get("addressee") for e in t.events() if e.get("type") == "attention" and e.get("kind") == "respond"]
        check(asked == [C], "only the seat that holds an answer is asked", asked)
    finally:
        t.stop()


def table_waiting_on_blocks():
    print("Table (i5, n): while a defender owes its blocks the phase says so, and the entry is gone once it has answered")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 3, "Plains")
        t.lands(C, 3, "Plains")
        t.turn(C)
        horta = t.onto(C, "Horta")
        t.onto(M, "Mugato")
        t.turn(M)
        t.turn(C)
        c, d = t.act(C, "attack", assign={f"#{horta}": M})
        check(c == 200, "set-up: Claude attacks Michael", (c, d))
        wo = t.call("GET", "/api/phase")[1].get("waiting_on") or []
        check(any(w["seat"] == M and w["what"] == "blocks" for w in wo), "/api/phase waits on Michael for 'blocks'", wo)
        c, d = t.call("POST", "/api/brain/damage", {"seat": C}, key=t.keys[C])
        check(c == 409 and "Michael" in str(d.get("error")), "Claude cannot deal damage while Michael's answer is owed", (c, d.get("error")))
        c, d = t.act(M, "block", none=True)
        wo = t.call("GET", "/api/phase")[1].get("waiting_on") or []
        check(c == 200 and not any(w["seat"] == M and w["what"] == "blocks" for w in wo), "Michael declares no block: the wait for his blocks is gone", (c, d, wo))
    finally:
        t.stop()


def table_stall_note():
    print("Table (i2, i3): the stall note is said once, and not before 20 s of the same wait")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 2, "Plains")
        set_hand(t, M, ["Set Phasers to . . .", "Plains"])      # an instant Michael can pay for: the table waits on him
        t.turn(C)
        ev1 = I.last_event(t)
        t0 = time.time()
        first = None
        while time.time() - t0 < 32:
            t.call("GET", "/api/phase")
            notes = [e for e in t.events() if e["id"] > ev1 and e["type"] == "table-note" and e.get("kind") == "wait"]
            if notes:
                first = first or (notes[0], time.time() - t0)
                if time.time() - t0 > first[1] + 6:
                    break
            time.sleep(0.5)
        check(first is not None and first[0].get("seat") == M and first[0].get("secs", 0) >= 20, "the first note says Michael, after at least 20 s of waiting (its own secs field)", first and (first[0].get("secs"), round(first[1], 1)))
        notes = [e for e in t.events() if e["id"] > ev1 and e["type"] == "table-note" and e.get("kind") == "wait" and e.get("seat") == M]
        check(len(notes) == 1, f"…and ONE note over the next polls (the next is two minutes on), not one per poll: {len(notes)}", [(e.get('secs'), e.get('text')) for e in notes][:4])
    finally:
        t.stop()


def engine_mccoy_himself():
    print("Engine (f1): Dr. McCoy is not 'another creature': with a +1/+1 counter of his own he still dies")
    v = I.seat("Michael")
    mc = player.Perm(card("Dr. McCoy, Surly Surgeon"), sick=False, counters=1)
    v.battlefield.append(mc)
    v.deal_damage(mc, 99, source="Test")
    check(mc not in v.battlefield, "McCoy with a counter of his own, lethal damage, no other McCoy: he dies (the replacement is for ANOTHER creature)", [p.name for p in v.battlefield])
    v = I.seat("Michael")
    mc = player.Perm(card("Dr. McCoy, Surly Surgeon"), sick=False)
    other = player.Perm(card("Mugato"), sick=False, counters=1)
    v.battlefield += [mc, other]
    v.deal_damage(other, 99, source="Test")
    check(other in v.battlefield and other.counters == 0 and mc in v.battlefield, "(control) another creature of his with a counter is saved by him")


def engine_death_scope():
    print("Engine (e2, e3): only a creature that goes to a graveyard dies")
    a = I.seat("Michael")
    heard = []
    old = player.DEATH_HOOK[0]
    player.DEATH_HOOK[0] = lambda owner, dead: heard.append(dead.name)
    try:
        for name, to, expect in (("Tricorder", "graveyard", False), ("Horta", "exile", False), ("Horta", "hand", False), ("Horta", "graveyard", True)):
            heard.clear()
            p = player.Perm(card(name), sick=False)
            a.battlefield.append(p)
            a.move(f"#{p.id}", to)
            check(bool(heard) == expect, f"{name} to the {to}: {'dies' if expect else 'does not die'} (the table's death hook {'fires' if expect else 'is silent'})", heard)
    finally:
        player.DEATH_HOOK[0] = old


def engine_outpost_boundary():
    print("Engine (h3): 'enters tapped unless you control two or more other lands': exactly two is untapped, one is tapped")
    for others, tapped in ((1, True), (2, False), (3, False)):
        v = I.seat(lands=others)
        v.hand = [card("Federation Outpost")]
        v.put("Federation Outpost")
        out = next(p for p in v.battlefield if p.name == "Federation Outpost")
        check(out.tapped is tapped, f"Federation Outpost with {others} other land{'s' if others != 1 else ''}: {'tapped' if tapped else 'untapped'}", out.tapped)


def engine_pay_gaps():
    print("Engine (g3, g4): floating mana is spent before a land is tapped; the payment that leaves the most of the hand castable is the one chosen")
    # g3: the pool empties with the step, a land does not. Seven lands, {B}{R} floating, Pet Sehlat {2}{W}: both floating mana pay its generic
    v = RR.lands_seat("FSFPFPPP", ["Mugato", "Pet Sehlat"])
    v.add_mana("BR")
    v.manual_cast("Pet Sehlat")
    check(len(v.floating()) == 0, "{B}{R} in the pool pay Pet Sehlat's {2}: nothing floats away unspent", len(v.floating()))
    v = RR.lands_seat("FSFPFPPP", ["Mugato", "Pet Sehlat"])
    v.add_mana("U")
    v.manual_cast("Pet Sehlat")
    check(len(v.floating()) == 0, "…and one floating {U} is spent too, the Plains and a Forest paying the rest", len(v.floating()))
    # g4: found by search (972 random boards: base and the broken payment differ on 9; these two are the plainest). The castable count after paying is what counts
    for spec, hand, cast, keeps in (("SFFSSPM", ["Pet Sehlat", "Mugato", "Zero, Incorporeal Sensate"], "Pet Sehlat", "Zero, Incorporeal Sensate"),
                                    ("FFMFIPP", ["Mugato", "Pet Sehlat", "Tal Shiar Operative", "Moopsy"], "Mugato", "Moopsy")):
        v = RR.lands_seat(spec, hand)
        v.manual_cast(cast)
        left = [c["name"] for _l, c, _p, _m in v.castable()]
        check(keeps in left, f"lands {spec}, casting {cast} with {len(hand) - 1} other cards in hand: {keeps} is still castable afterwards", left)


SECTIONS = [("concede auth", table_concede_auth), ("concede three seats", table_concede_three_seats), ("reveal other seats", table_reveal_other_seats), ("priority after a spell", table_priority_after_a_spell), ("refused cast keeps priority", table_refused_cast_keeps_priority), ("held does not name the holder", table_held_does_not_name_the_holder), ("waiting on blocks", table_waiting_on_blocks), ("stall note", table_stall_note),
            ("engine mccoy himself", engine_mccoy_himself), ("engine death scope", engine_death_scope), ("engine outpost", engine_outpost_boundary), ("engine pay gaps", engine_pay_gaps)]


def main():
    only = os.environ.get("GAPS_ONLY")
    for title, fn in SECTIONS:
        if only and not any(w in title for w in only.split("|")):
            continue
        try:
            fn()
        except Exception:  # noqa: BLE001 — a section that dies is a failure, and the rest still run
            traceback.print_exc()
            R.FAILS.append(f"{title}: crashed")
    print(f"\n{'all passed' if not R.FAILS else f'{len(R.FAILS)} FAILED: {R.FAILS}'}")
    sys.exit(1 if R.FAILS else 0)


if __name__ == "__main__":
    main()
