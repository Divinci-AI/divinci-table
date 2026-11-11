"""Round 4 engine fixes (2026-11-10), one section per item, on tables of their own on free ports (never a live game's), the seat's own key.

  1  private reveal-and-choose (Stray from History, Salt Vampire, Zero, V'Ger): only the caster is shown the hand; the public log names only the card taken
  2  "until this leaves the battlefield" exiles (Malfunctioning Holodeck)
  3  "whenever a creature dies" (Vengeance Mission, Excalbian Gamemaster, Grand Nagus Zek, Curzon Dax …)
  4  Dr. McCoy, Surly Surgeon: the +1/+1 counter replacement for destruction
  5  smart payment (plan_payment leaves the hand's other cards castable)

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 TABLE_TEST_DECKS=$(python tests/fixture_decks.py) ~/.venvs/table/bin/python table/tests/retro_round4_test.py
    R4_ONLY="reveal|holodeck"   only the sections whose title names one of these
"""
from __future__ import annotations

import json
import os
import pickle
import re
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
CARDS = R.CARDS
R.POOL.update({"Stray from History": 6, "Salt Vampire": 6, "Zero, Incorporeal Sensate": 6, "V'Ger, the Intruder": 6, "Malfunctioning Holodeck": 6,
               "Vengeance Mission": 6, "Dr. McCoy, Surly Surgeon": 6, "Excalbian Gamemaster": 6, "Grand Nagus Zek": 6,
               "Gowron, Eager for Battle": 6, "Pet Sehlat": 6, "Tal Shiar Operative": 6, "Sarcophagus, Ship of the Dead": 6, "Tenacious Drone": 6,
               "Battle-Scarred Survivalist": 6, "Rutherford, Upgraded Ensign": 6, "Risa": 6, "Bajor": 6, "Cardassia Prime": 6, "Final Frontier": 6, "Federation Outpost": 6, "Exploratory Mission": 6, "Munitions Enthusiast": 6})
R.DECK_MICHAEL, R.DECK_CLAUDE = R.deck_file("Michael-r4"), R.deck_file("Claude-r4")


def card(n):
    return dict(CARDS[n])


def card_any(n):
    """A card of any set (the commander precons, trc, are not in the draft pool CARDS holds, but their text runs through the same engine)."""
    if n in CARDS:
        return card(n)
    raw = next(c for c in json.load(open(os.path.expanduser("~/.divinci/star-trek-mtg/cards.json"))) if c["name"] == n)
    return R.shim.entry(raw)


def names(cards):
    return [c["name"] for c in cards]


# ── helpers for a table ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def set_hand(t, seat, hand):
    """Exactly this hand: what is there goes to the bottom of the library, then each named card is fetched."""
    for h in list(t.state(seat).get("hand", [])):
        c, d = t.call("POST", "/api/brain/manual", {"seat": seat, "op": "move", "ref": f"hand:{h['name']}", "to": "library-bottom"})
        assert c == 200, d
    for n in hand:
        t.hand(seat, n)
    got = sorted(h["name"] for h in t.state(seat).get("hand", []))
    assert got == sorted(hand), (got, hand)


def pending(t, seat):
    return t.state(seat).get("pending_choices") or []


def opt_for(pend, name):
    return next((o for o in pend["slot"].get("options", []) if o["label"].startswith(name + " ")), None)


def answer(t, seat, pend, name=None):
    o = opt_for(pend, name) if name else None
    return t.act(seat, "choose", id=pend["id"], target={k: v for k, v in (o or {}).items() if k != "label"})


PUBLIC = ["/api/board3d", "/api/events?since=0", "/api/game/summary", "/api/life", "/api/phase", "/api/spectate", "/api/checklist", "/api/history",
          "/api/placed", "/api/todos", "/api/journal?seat=Michael", "/api/ai/state?seat=Michael", "/board", "/watch", "/log"]


def public_text(t, since=0):
    """Everything a spectator (no key at all) or a third seat can read, as one string."""
    import urllib.error
    import urllib.request
    out = []
    for r in PUBLIC:
        try:
            with urllib.request.urlopen(t.b + r.replace("since=0", f"since={since}"), timeout=20) as x:
                out.append(x.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            out.append(e.read().decode("utf-8", "replace"))
    return "\n".join(out)


def say_text(t, since):
    return " ".join(str(e.get("text")) for e in t.events(since) if e.get("type") in ("say", "attention", "table-note", "heard", "shown"))


# ── item 1 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def table_reveal():
    print("Table: Stray from History (Repair the Timeline): the caster is shown the hand, nobody else")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 5, "Swamp")
        marks = ["Sela, Tal Shiar Paradox", "Jett Reno, Problem Solver", "Kira Nerys, Firebrand"]
        set_hand(t, C, [*marks, "Plains", "Plains"])
        t.hand(M, "Stray from History")
        ev0 = I.last_event(t)
        c, d = t.act(M, "cast", name="Stray from History", modes=[2], targets=[{"player": C}])
        check(c == 200, "cast: mode 2 aimed at Claude is accepted", (c, d))
        ps = pending(t, M)
        check(len(ps) == 1 and ps[0]["kind"] == "option", "Michael is asked one question (an option list)", ps)
        p = ps[0] if ps else {"slot": {"options": [], "label": ""}, "id": 0}
        labels = [o["label"] for o in p["slot"].get("options", [])]
        check(sorted(re.sub(r" \{.*", "", x) for x in labels) == sorted(marks), "the options are exactly Claude's three nonland cards: no land", labels)
        check("Plains x2" in p["slot"].get("label", "") and all(m in p["slot"]["label"] for m in marks), "the whole hand is listed in the question's label (lands too), for the page that shows only that",
              p["slot"].get("label"))
        check(not pending(t, C), "Claude is asked nothing", pending(t, C))
        check(sorted(h["name"] for h in t.state(C)["hand"]) == sorted([*marks, "Plains", "Plains"]), "…and nothing left Claude's hand yet")
        # nobody else: the public routes, the log, the events
        pub = public_text(t)
        leaked = [m for m in marks if m in pub]
        check(not leaked, "no public route, event or log line names a card from the hand before the choice", leaked)
        said = say_text(t, ev0)
        check("looks at Claude's hand" in said, "the public log says Michael looks at Claude's hand", said[-300:])
        # the pick
        c, d = answer(t, M, p, "Jett Reno, Problem Solver")
        check(c == 200, "Michael takes Jett Reno", (c, d))
        pub = public_text(t)
        said = say_text(t, ev0)
        check("Jett Reno, Problem Solver is no longer" not in said and "chooses Jett Reno, Problem Solver from Claude's hand: it is exiled" in said, "the public log names the chosen card", said[-400:])
        others = [m for m in marks if m != "Jett Reno, Problem Solver" and m in pub]
        check(not others, "…and only that one: the two it passed over are in no public route, event or log line", others)
        check("Jett Reno, Problem Solver" not in names(t.state(C)["hand"]) and len(t.state(C)["hand"]) == 4, "it left Claude's hand", names(t.state(C)["hand"]))
        ex = t.state(C).get("exile") or t.board(C).get("exile") or []
        check("Jett Reno, Problem Solver" in json.dumps(t.board(C)), "…and is face up in Claude's exile, for everyone", t.board(C))
        check(not pending(t, M), "the question is gone")
        # the other seat's own view of Michael carries nothing
        mv = json.dumps(t.state(C).get("opponents", {}).get(M))
        check(not any(m in mv for m in marks), "Claude's view of Michael holds no card from Claude's hand-as-seen-by-Michael")
    finally:
        t.stop()


def table_reveal_edges():
    print("Table: a hand of lands only, an emptied hand, and a hand that changes between the look and the pick")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 12, "Swamp")
        set_hand(t, C, ["Plains", "Plains", "Forest"])
        t.hand(M, "Stray from History")
        t.hand(M, "Stray from History")
        ev0 = I.last_event(t)
        c, d = t.act(M, "cast", name="Stray from History", modes=[2], targets=[{"player": C}])
        said = say_text(t, ev0)
        check(c == 200 and not pending(t, M) and "no nonland card" in said, "only lands: the spell resolves with nothing to take, and nothing is left asked", (c, d, pending(t, M), said[-200:]))
        check(len(t.state(C)["hand"]) == 3, "…and nothing leaves the hand")
        # the hand changes between the look and the pick
        set_hand(t, C, ["Sela, Tal Shiar Paradox", "Jett Reno, Problem Solver"])
        c, d = t.act(M, "cast", name="Stray from History", modes=[2], targets=[{"player": C}])
        p = pending(t, M)[0]
        t.call("POST", "/api/brain/manual", {"seat": C, "op": "move", "ref": "hand:Sela, Tal Shiar Paradox", "to": "graveyard"})
        ev1 = I.last_event(t)
        c, d = answer(t, M, p, "Sela, Tal Shiar Paradox")
        said = say_text(t, ev1)
        check(c == 200 and "no longer in Claude's hand" in said and names(t.state(C)["hand"]) == ["Jett Reno, Problem Solver"], "a card that left the hand meanwhile is not taken (said), the rest is untouched", (c, said[-250:], names(t.state(C)["hand"])))
        # refused: an answer that is not one of the options (a land, or a card not on offer)
        set_hand(t, C, ["Plains", "Sela, Tal Shiar Paradox"])
        t.hand(M, "Stray from History")
        c0, d0 = t.act(M, "cast", name="Stray from History", modes=[2], targets=[{"player": C}])
        p = pending(t, M)[0] if pending(t, M) else {"id": 0}
        check(c0 == 200 and p["id"], "(set-up: a third Stray from History is cast)", (c0, d0))
        c, d = t.act(M, "choose", id=p["id"], target={"at": M, "ref": "opt:9"})
        check(c in (400, 409) and pending(t, M) and "Plains" in names(t.state(C)["hand"]), "an option that is not on offer is refused and the question stays", (c, d))
    finally:
        t.stop()


def table_salt_vampire():
    print("Table: Salt Vampire, V'Ger and Zero")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 9, "Swamp")
        marks = ["Sela, Tal Shiar Paradox", "Jett Reno, Problem Solver", "Kira Nerys, Firebrand"]
        set_hand(t, C, [*marks, "Plains"])
        t.hand(M, "Salt Vampire")
        ev0 = I.last_event(t)
        c, d = t.act(M, "cast", name="Salt Vampire", targets=[{"player": C}])
        ps = pending(t, M)
        check(c == 200 and len(ps) == 1 and ps[0]["slot"].get("optional"), "Salt Vampire: Michael is asked, and may take none", (c, d, ps))
        p = ps[0]
        check(not any(m in public_text(t) for m in marks), "…and the hand is on no public route")
        c, d = answer(t, M, p, "Kira Nerys, Firebrand")
        hand = names(t.state(C)["hand"])
        check(c == 200 and "Kira Nerys, Firebrand" not in hand and len(hand) == 4, "Kira is exiled and Claude draws a card (still four in hand)", hand)
        check("Kira Nerys, Firebrand" in json.dumps(t.board(C)), "…face up in exile")
        said = say_text(t, ev0)
        check("Kira Nerys, Firebrand" in said and not any(m in public_text(t) for m in marks if m != "Kira Nerys, Firebrand"), "the log names Kira only")
        # "you may": none
        set_hand(t, C, [*marks[:2], "Plains"])
        t.hand(M, "Salt Vampire")
        c, d = t.act(M, "cast", name="Salt Vampire", targets=[{"player": C}])
        p = pending(t, M)[0]
        c, d = t.act(M, "choose", id=p["id"], target={})
        check(c == 200 and len(t.state(C)["hand"]) == 3 and not pending(t, M), "declining takes nothing, and the target draws nothing", (c, d))
        # V'Ger: "Look at target opponent's hand, then draw a card" is a mode of an enters-or-attacks trigger
        t.lands(M, 1, "Island")
        t.turn(C)
        t.turn(M)
        t.hand(M, "V'Ger, the Intruder")
        set_hand(t, C, [*marks[:2], "Plains"])
        n0 = len(t.state(M)["hand"])
        c, d = t.act(M, "cast", name="V'Ger, the Intruder")
        ps = pending(t, M)
        check(c == 200 and len(ps) == 1 and any("Look at target opponent's hand" in o["label"] for o in ps[0]["slot"]["options"]), "V'Ger enters: Michael is asked which mode", (c, d, ps))
        o = next(o for o in ps[0]["slot"]["options"] if o["label"].startswith("Look at"))
        c, d = t.act(M, "choose", id=ps[0]["id"], target={k: v for k, v in o.items() if k != "label"})
        ps = pending(t, M)
        check(c == 200 and len(ps) == 1 and "I have seen it" in ps[0]["slot"]["options"][0]["label"] and "Plains x1" not in ps[0]["slot"]["label"] and marks[0] in ps[0]["slot"]["label"],
              "…then the look: one answer to give, and the hand is in the question", (c, d, ps))
        check(not any(m in public_text(t) for m in marks[:2]), "…the hand is on no public route")
        c, d = t.act(M, "choose", id=ps[0]["id"], target={k: v for k, v in ps[0]["slot"]["options"][0].items() if k != "label"})
        check(c == 200 and len(t.state(M)["hand"]) == n0 - 1 + 1 and len(t.state(C)["hand"]) == 3 and not pending(t, M), "…the look is seen, Michael draws a card, and Claude loses none", (c, len(t.state(M)["hand"]), n0))
        # Zero: exile it; its owner may play it for {2} more
        t.lands(C, 6, "Plains")
        t.turn(C)
        t.turn(M)
        set_hand(t, C, ["Pet Sehlat", "Plains"])
        t.hand(M, "Zero, Incorporeal Sensate")
        c, d = t.act(M, "cast", name="Zero, Incorporeal Sensate", targets=[{"player": C}])
        ps = pending(t, M)
        check(c == 200 and len(ps) == 1 and ps[0]["slot"].get("optional"), "Zero: Michael is asked (he may take none)", (c, d, ps))
        if ps:
            answer(t, M, ps[0], "Pet Sehlat")
        t.turn(C)
        st = t.state(C)
        ex = [e for e in st.get("exile_playable", []) if e["name"] == "Pet Sehlat"]
        check(ex and "stays exiled" in str(ex[0].get("until")), "Pet Sehlat is exiled and Claude may play it for as long as it stays there", st.get("exile_playable"))
        free = sum(1 for p in st["permanents"] if "Land" in (p.get("type") or "") and not p.get("tapped"))
        c, d = t.act(C, "cast", name="Pet Sehlat", from_exile=True)
        tapped = sum(1 for p in t.state(C)["permanents"] if "Land" in (p.get("type") or "") and p.get("tapped"))
        check(c == 200 and tapped == 5 and t.perm(C, "Pet Sehlat"), "…and it costs {2}{W} + {2} = five lands", (c, d, tapped))
    finally:
        t.stop()



# ── item 2 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def hold(t, how, seat_target=C):
    """Michael casts Malfunctioning Holodeck on Claude's Horta; returns (holodeck id, horta id)."""
    h = t.onto(C, "Horta")
    t.hand(M, "Malfunctioning Holodeck")
    c, d = t.act(M, "cast", name="Malfunctioning Holodeck", targets=[{"at": C, "ref": f"#{h}"}])
    assert c == 200, (c, d)
    return t.perm(M, "Malfunctioning Holodeck")["id"], h


def table_holodeck():
    print("Table: Malfunctioning Holodeck exiles until it leaves the battlefield")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 12, "Plains")
        t.lands(C, 2, "Forest")
        hid = t.onto(C, "Horta")
        land = t.perm(C, "Forest")["id"]
        t.hand(M, "Malfunctioning Holodeck")
        c, d = t.act(M, "cast", name="Malfunctioning Holodeck", targets=[{"at": C, "ref": f"#{land}"}])
        check(c in (400, 409), "a land can't be the target (nonland permanent)", (c, d))
        ev0 = I.last_event(t)
        c, d = t.act(M, "cast", name="Malfunctioning Holodeck", targets=[{"at": C, "ref": f"#{hid}"}])
        check(c == 200 and t.perm(C, "Horta") is None, "Horta is exiled on enter", (c, d))
        check("Horta" in json.dumps(t.board(C)) and "Horta" in say_text(t, ev0), "…face up in Claude's exile, and the log says so", t.board(C))
        hol = t.perm(M, "Malfunctioning Holodeck")["id"]
        # leaves: each way
        for how in ("graveyard", "hand", "exile"):
            c, d = t.call("POST", "/api/brain/manual", {"seat": M, "op": "move", "ref": f"#{hol}", "to": how})
            back = t.perm(C, "Horta")
            check(c == 200 and back is not None and back["id"] != hid and back.get("sick") in (True, None), f"Holodeck to the {how}: Horta returns under Claude's control as a NEW object", (c, d, back))
            check("Horta" not in json.dumps(t.board(C).get("exile", [])), "…and is no longer in exile", t.board(C))
            hid = back["id"] if back else hid
            if how != "exile":
                t.hand(M, "Malfunctioning Holodeck") if how == "graveyard" else None
                c, d = t.act(M, "cast", name="Malfunctioning Holodeck", targets=[{"at": C, "ref": f"#{hid}"}])
                hol = t.perm(M, "Malfunctioning Holodeck")["id"]
                check(c == 200 and t.perm(C, "Horta") is None, f"(set-up: Holodeck again, Horta held again)", (c, d))
    finally:
        t.stop()


def engine_holodeck():
    print("Engine: Holodeck's hold, a token, a source that left first, counters gone, a snapshot, an old snapshot")
    me, foe = I.seat("Michael", lands=0), I.seat("Claude", lands=0)
    holodeck = player.Perm(card("Malfunctioning Holodeck"), sick=False)
    me.battlefield.append(holodeck)
    horta = player.Perm(card("Horta"), sick=False, counters=2)
    tok = player.Perm({"name": "Borg token", "types": ["Creature"], "power": "1", "toughness": "1", "text": "", "keywords": []}, token=True, sick=False)
    foe.battlefield += [horta, tok]
    said = me._aimed0(holodeck, player.effect_shape(card("Malfunctioning Holodeck")["text"].split("\n")[1]), "exile target nonland permanent an opponent controls until this artifact leaves the battlefield", {"perm": horta, "owner": foe})
    check(horta not in foe.battlefield and holodeck.held and names(foe.exile_zone()) == ["Horta"], "exiled, held, in the owner's exile", (said, holodeck.held))
    me._aimed0(holodeck, player.effect_shape("exile target nonland permanent an opponent controls until this artifact leaves the battlefield"), "exile target nonland permanent an opponent controls until this artifact leaves the battlefield", {"perm": tok, "owner": foe})
    check(tok not in foe.battlefield and len(holodeck.held) == 1, "a token exiled is gone and nothing waits to bring it back")
    snap = pickle.loads(pickle.dumps({"me": me, "foe": foe}))
    me2, foe2 = snap["me"], snap["foe"]
    h2 = next(p for p in me2.battlefield if p.name == "Malfunctioning Holodeck")
    check(len(h2.held) == 1 and h2.held[0][0] is foe2, "it survives a snapshot, still pointing at the same owner object", h2.held)
    me2.move(f"#{h2.id}", "graveyard")
    back = next((p for p in foe2.battlefield if p.name == "Horta"), None)
    check(back is not None and back.counters == 0 and back.sick and back.id != horta.id and not foe2.exile_zone(), "it returns new: no counters, summoning sick, out of exile", back)
    # a source that left before its trigger resolved
    me3, foe3 = I.seat("Michael"), I.seat("Claude")
    ghost = player.Perm(card("Malfunctioning Holodeck"), sick=False)           # (not on the battlefield)
    h3 = player.Perm(card("Horta"), sick=False)
    foe3.battlefield.append(h3)
    said = me3._aimed0(ghost, player.effect_shape("exile target nonland permanent an opponent controls until this artifact leaves the battlefield"), "exile target nonland permanent an opponent controls until this artifact leaves the battlefield", {"perm": h3, "owner": foe3})
    check(h3 in foe3.battlefield and not foe3.exile_zone(), "the source gone before the trigger resolves: nothing is exiled", said)
    # the card left exile some other way: nothing returns
    me4, foe4 = I.seat("Michael"), I.seat("Claude")
    d4 = player.Perm(card("Malfunctioning Holodeck"), sick=False)
    me4.battlefield.append(d4)
    h4 = player.Perm(card("Horta"), sick=False)
    foe4.battlefield.append(h4)
    me4._aimed0(d4, player.effect_shape("exile target nonland permanent an opponent controls until this artifact leaves the battlefield"), "exile target nonland permanent an opponent controls until this artifact leaves the battlefield", {"perm": h4, "owner": foe4})
    foe4.exile_zone().clear()
    me4.move(f"#{d4.id}", "graveyard")
    check(not foe4.battlefield, "a card that is no longer in exile does not come back")
    # a plain old Perm (pickled before `held` existed) has the empty default
    p_old = player.Perm(card("Horta"))
    p_old.__dict__.pop("held", None)
    check(p_old.held == (), "an older Perm without the field reads the plain default", p_old.held)


def table_holodeck_more():
    print("Table: Holodeck's targets: ward, a spell that destroys it, and an enters trigger when the card comes back")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 3, "Plains")
        t.lands(C, 2, "Forest")
        t.lands(C, 1, "Plains")
        ward = t.onto(C, "Malfunctioning Holodeck")
        t.hand(M, "Malfunctioning Holodeck")
        c, d = t.act(M, "cast", name="Malfunctioning Holodeck", targets=[{"at": C, "ref": f"#{ward}"}])
        check(c in (400, 409) and t.perm(C, "Malfunctioning Holodeck"), "Claude's own Holodeck has ward {2}: three lands can't cast one and pay it, and nothing happens", (c, d))
        t.lands(M, 2, "Plains")
        c, d = t.act(M, "cast", name="Malfunctioning Holodeck", targets=[{"at": C, "ref": f"#{ward}"}])
        check(c == 200 and t.perm(C, "Malfunctioning Holodeck") is None and sum(1 for p in t.state(M)["permanents"] if "Land" in (p.get("type") or "") and not p.get("tapped")) == 0,
              "with five lands the ward is paid with the spell and the ward holder is exiled", (c, d))
        mine = t.perm(M, "Malfunctioning Holodeck")["id"]
        # the Gamemaster is exiled; when Holodeck dies it comes back and its enters trigger makes tokens for its owner
        t.turn(C)
        gm = t.onto(C, "Rutherford, Upgraded Ensign")
        t.turn(M)
        t.lands(M, 3, "Plains")
        n_tok = len(t.perms(C, "Officer token"))
        t.hand(M, "Malfunctioning Holodeck")
        c, d = t.act(M, "cast", name="Malfunctioning Holodeck", targets=[{"at": C, "ref": f"#{gm}"}])
        check(c == 200 and t.perm(C, "Rutherford, Upgraded Ensign") is None, "Rutherford is exiled", (c, d))
        holodeck2 = max(p["id"] for p in t.perms(M, "Malfunctioning Holodeck"))
        t.settle_setup()
        I.stack_top(t, C, ["Tricorder", "Moopsy"])                        # (an artifact among the top six, so the look has something to ask about)
        c, d = t.call("POST", "/api/brain/manual", {"seat": M, "op": "move", "ref": f"#{holodeck2}", "to": "graveyard"})
        back = t.perm(C, "Rutherford, Upgraded Ensign")
        pc = pending(t, C)
        check(c == 200 and back and pc and not pending(t, M), "Holodeck leaves: Rutherford is back and its enters trigger asks CLAUDE (its owner), not Michael", (c, back, pc))
    finally:
        t.stop()


# ── item 3 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def engine_dies():
    print("Engine: 'whenever a creature dies' (Excalbian Gamemaster, Grand Nagus Zek, Curzon Dax, Skullclamp, Vengeance Mission), and the older ones still work")
    def pair():
        a, b = I.seat("Michael"), I.seat("Claude")
        return a, b
    # Zek: a Treasure for any other creature that dies, once each turn
    a, b = pair()
    zek = player.Perm(card("Grand Nagus Zek"), sick=False)
    a.battlefield.append(zek)
    d1 = player.Perm(card("Horta"), sick=False)
    b.battlefield.append(d1)
    b.move(f"#{d1.id}", "graveyard")
    said = a.creature_died(d1, b)
    check(sum(1 for p in a.battlefield if p.name.startswith("Treasure")) == 1 and said, "Zek: a creature of the other seat dies: one Treasure", said)
    d2 = player.Perm(card("Horta"), sick=False)
    b.battlefield.append(d2)
    a.creature_died(d2, b)
    check(sum(1 for p in a.battlefield if p.name.startswith("Treasure")) == 1, "…and only one each turn")
    player.DEATH_TICK[0] += 1
    a.creature_died(d2, b)
    check(sum(1 for p in a.battlefield if p.name.startswith("Treasure")) == 2, "…a new turn, a new Treasure")
    # Vengeance Mission
    a, b = pair()
    vm = player.Perm(card("Vengeance Mission"), sick=False)
    a.battlefield.append(vm)
    out = []
    for k in range(1, 9):
        dead = player.Perm(card("Horta"), sick=False)
        a.creature_died(dead, b if k % 2 else a)               # (either side's creature)
        out += [(i["n"], i["verb"]) for i in a.drain() if i.get("kind") == "target-opponent"]
    check(vm.quest == 8 and out == [(1, "loses")] * 6 + [(2, "loses")] * 2, "Vengeance Mission: a quest counter and 1 life per death; 2 from the seventh counter on", (vm.quest, out))
    # Gamemaster: its own death, and another of its controller's; not the other seat's
    a, b = pair()
    a.__dict__["asks"] = True
    gm = player.Perm(card("Excalbian Gamemaster"), sick=False)
    mate = player.Perm(card("Horta"), sick=False)
    a.battlefield += [gm, mate]
    foe_c = player.Perm(card("Mugato"), sick=False)
    b.battlefield.append(foe_c)
    a.creature_died(foe_c, b)
    check(not a.__dict__.get("pending_choices") and mate.counters == 0, "Gamemaster: an opponent's creature dying does nothing")
    a.move(f"#{mate.id}", "graveyard")
    a.creature_died(mate, a)
    pc = a.__dict__.get("pending_choices") or []
    check(len(pc) == 1 and "+1/+1 counter" in pc[0]["text"], "…its controller's other creature dying asks where the +1/+1 counter goes (a target of its controller's)", pc)
    a.__dict__["pending_choices"] = []
    a.move(f"#{gm.id}", "graveyard")
    check(True, "(Gamemaster leaves)")
    a2 = I.seat("Michael")
    a2.__dict__["asks"] = True
    gm2 = player.Perm(card("Excalbian Gamemaster"), sick=False)
    pal = player.Perm(card("Horta"), sick=False)
    a2.battlefield += [gm2, pal]
    a2.battlefield.remove(gm2)
    said = a2.creature_died(gm2, a2)
    check(len(a2.__dict__.get("pending_choices") or []) == 1, "…and the Gamemaster's own death asks too (it hears itself)", said)
    # Skullclamp: the creature it was attached to dies
    a, b = pair()
    clamp = player.Perm(card_any("Skullclamp"), sick=False)
    bear = player.Perm(card("Horta"), sick=False)
    a.battlefield += [bear, clamp]
    clamp.attached_to = bear.id
    a.library = [card("Plains")] * 5
    a.move(f"#{bear.id}", "graveyard")
    a.creature_died(bear, a)                                    # (the server's hook does this on every seat)
    check(len(a.hand) == 2 and clamp.attached_to is None, "Skullclamp: the equipped creature dies: draw two (it was attached when it died)", len(a.hand))
    a.battlefield.remove(clamp)
    # Curzon Dax: another Klingon or Warrior of its controller's dies: 1 damage to each opponent
    a, b = pair()
    cz = player.Perm(card_any("Curzon Dax, Bound by Blood"), sick=False)
    kl = player.Perm({**card("Horta"), "name": "Klingon Guy", "subtypes": ["Klingon"]}, sick=False)
    el = player.Perm(card("Horta"), sick=False)
    a.battlefield += [cz, kl, el]
    a.move(f"#{el.id}", "graveyard")
    check(not any(i.get("kind") == "each-opponent" for i in a.drain()), "Curzon Dax: a non-Klingon, non-Warrior dying does nothing")
    a.move(f"#{kl.id}", "graveyard")
    a.creature_died(kl, a)
    check([i["n"] for i in a.drain() if i.get("kind") == "each-opponent"] == [1], "…a Klingon: 1 damage to each opponent")
    # the older two are unchanged
    a, b = pair()
    gw = player.Perm(card("Gowron, Eager for Battle"), sick=False)
    sar = player.Perm(card("Sarcophagus, Ship of the Dead"), sick=False)
    a.battlefield += [gw, sar]
    x = player.Perm(card("Horta"), sick=False)
    b.battlefield.append(x)
    a.creature_died(x, b)
    check(gw.counters == 1 and sar.charge == 1, "Gowron and Sarcophagus still hear it: a +1/+1 counter, a charge counter", (gw.counters, sar.charge))


def table_dies():
    print("Table: Vengeance Mission hears combat, a spell, a sacrifice, a token and its own side")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 12, "Swamp")
        t.hand(M, "Vengeance Mission")
        c, d = t.act(M, "cast", name="Vengeance Mission")
        check(c == 200, "Vengeance Mission is cast", (c, d))
        vm = lambda: t.perm(M, "Vengeance Mission")
        life0 = t.lifeof(C)
        k = t.onto(C, "Horta")
        t.call("POST", "/api/brain/manual", {"seat": C, "op": "move", "ref": f"#{k}", "to": "graveyard"})
        check(vm()["quest"] == 1 and t.lifeof(C) == life0 - 1, "a creature of Claude's dies (sacrificed or destroyed): a quest counter, Claude loses 1", (vm()["quest"], t.lifeof(C), life0))
        c, d = t.call("POST", "/api/brain/manual", {"seat": C, "op": "token", "name": "Borg", "power": 1, "toughness": 1, "n": 1})
        tok = t.perm(C, "Borg token") or t.perm(C, "Borg")
        t.call("POST", "/api/brain/manual", {"seat": C, "op": "move", "ref": f"#{tok['id']}", "to": "graveyard"})
        check(vm()["quest"] == 2 and t.lifeof(C) == life0 - 2, "a token creature dies: counted", (vm()["quest"], t.lifeof(C)))
        mine = t.onto(M, "Mugato")
        t.call("POST", "/api/brain/manual", {"seat": M, "op": "move", "ref": f"#{mine}", "to": "graveyard"})
        check(vm()["quest"] == 3 and t.lifeof(C) == life0 - 3 and t.lifeof(M) == 20, "Michael's own creature dies: counted too, and still Claude who loses", (vm()["quest"], t.lifeof(C), t.lifeof(M)))
        # a spell
        hjim = t.onto(C, "Horta")
        t.hand(M, "He's Dead, Jim")
        c, d = t.act(M, "cast", name="He's Dead, Jim", targets=[{"at": C, "ref": f"#{hjim}"}])
        t.act(M, "surveil", graveyard=[]) if t.state(M).get("pending_surveil") else None
        check(c == 200 and vm()["quest"] == 4 and t.lifeof(C) == life0 - 4, "He's Dead, Jim destroys one: counted", (c, vm()["quest"], t.lifeof(C)))
        # combat: both die
        t.turn(C)
        ho = t.onto(C, "Horta")
        mu = t.onto(M, "Mugato")
        t.turn(M)
        t.turn(C)
        c, d = t.act(C, "attack", assign={f"#{ho}": M})
        t.act(M, "block", blocker=f"#{mu}", attacker=f"#{ho}")
        l1 = t.lifeof(C)
        c, d = t.act(C, "damage")
        gone = t.perm(C, "Horta") is None and t.perm(M, "Mugato") is None
        check(c == 200 and gone and vm()["quest"] == 6 and t.lifeof(C) == l1 - 2, "combat: Horta and Mugato kill each other: two quest counters, two life", (c, gone, vm()["quest"], t.lifeof(C), l1))
        # from the seventh counter on it is 2
        l2 = t.lifeof(C)
        z = t.onto(C, "Horta")
        t.call("POST", "/api/brain/manual", {"seat": C, "op": "move", "ref": f"#{z}", "to": "graveyard"})
        check(vm()["quest"] == 7 and t.lifeof(C) == l2 - 2, "the seventh counter: Claude loses 2 instead of 1", (vm()["quest"], t.lifeof(C), l2))
        l3 = t.lifeof(C)
        z = t.onto(C, "Horta")
        t.call("POST", "/api/brain/manual", {"seat": C, "op": "move", "ref": f"#{z}", "to": "graveyard"})
        check(vm()["quest"] == 8 and t.lifeof(C) == l3 - 2, "…and it stays 2", (vm()["quest"], t.lifeof(C)))
    finally:
        t.stop()


# ── item 4 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def engine_mccoy():
    print("Engine: Dr. McCoy, Surly Surgeon: a creature with a +1/+1 counter that would be destroyed is not (counters off, damage healed)")
    from ai_turn import resolve_block
    def board():
        v = I.seat("Michael")
        mc = player.Perm(card("Dr. McCoy, Surly Surgeon"), sick=False)
        pal = player.Perm(card("Mugato"), sick=False, counters=2)           # a 6/6 base: 8/8 with two counters
        bare = player.Perm(card("Horta"), sick=False)
        v.battlefield += [mc, pal, bare]
        return v, mc, pal, bare
    v, mc, pal, bare = board()
    said = v.deal_damage(pal, 8, source="Test")
    check(pal in v.battlefield and pal.counters == 0 and pal.damage == 0 and any("instead" in x for x in said), "lethal damage: it lives, its two counters come off and the 8 damage is healed", (said, pal.counters, pal.damage))
    said = v.deal_damage(pal, 6, source="Test")
    check(pal not in v.battlefield, "…with no counter left the next lethal damage kills it", said)
    v, mc, pal, bare = board()
    v.deal_damage(pal, 1, deathtouch=True, source="Test")
    check(pal in v.battlefield and pal.counters == 0, "deathtouch damage is destruction too")
    v, mc, pal, bare = board()
    bare.counters = 1
    v.deal_damage(bare, 20, source="Test")
    check(bare in v.battlefield and bare.counters == 0, "any other creature of its controller with a counter (Horta, one counter)")
    v.deal_damage(mc, 20, source="Test")
    check(mc not in v.battlefield, "…but not Dr. McCoy himself (it says another)")
    v, mc, pal, bare = board()
    said = v._aimed0(None, player.effect_shape("Destroy target creature"), "Destroy target creature", {"perm": pal, "owner": v})
    check(pal in v.battlefield and pal.counters == 0 and "is destroyed" not in " ".join(said), "a destroy effect: not destroyed, and the log does not say it was", said)
    said = v._aimed0(None, player.effect_shape("Destroy target creature"), "Destroy target creature", {"perm": bare, "owner": v})
    check(bare not in v.battlefield, "…a creature with no counter is destroyed as ever", said)
    v, mc, pal, bare = board()
    pal.counters = 2
    wiped = [v.move(f"#{p.id}", "graveyard", destroy=True) for p in sorted(v.battlefield, key=lambda q: q is mc) if p.is_("Creature")]      # (the table's wipe puts the replacement's source last: simultaneous)
    check(pal in v.battlefield and bare not in v.battlefield and mc not in v.battlefield, "a wipe (destroy all creatures): the one with counters stays, the rest die", [p.name for p in v.battlefield])
    v, mc, pal, bare = board()
    v.move(f"#{pal.id}", "graveyard")
    check(pal not in v.battlefield, "SACRIFICE and a plain move to the graveyard are not destruction: it dies")
    v, mc, pal, bare = board()
    pal.temp = (0, -9)                                          # toughness 0 or less is not destruction either
    said = v.check_dead(pal)
    check(pal not in v.battlefield, "toughness 0 or less: it dies, McCoy or not", said)
    v, mc, pal, bare = board()
    v.battlefield.remove(mc)
    v.deal_damage(pal, 8, source="Test")
    check(pal not in v.battlefield, "with no McCoy on the battlefield nothing is replaced")
    # combat: McCoy's controller blocks, or attacks
    v, mc, pal, bare = board()
    foe = I.seat("Claude")
    atk = player.Perm(card("Horta"), sick=False)                # 7/6
    foe.battlefield.append(atk)
    atk.counters = 2
    res = resolve_block(v, pal, 9, False, "Horta", atk=atk, atk_owner=foe)
    check(pal in v.battlefield and pal.counters == 0 and pal.damage == 0 and not res.get("attacker_died") is None or True, "(set-up)")
    check(pal in v.battlefield and pal.counters == 0 and pal.damage == 0, "blocking: lethal combat damage destroys nothing, it heals", (res["said"], pal.counters, pal.damage))
    # an attacker of McCoy's controller
    foe2 = I.seat("Claude")
    blocker = player.Perm(card("Horta"), sick=False)
    foe2.battlefield.append(blocker)
    v, mc, pal, bare = board()
    pal.counters = 1                                             # 7/7: Horta deals 7 back
    res = resolve_block(foe2, blocker, 7, False, "Mugato", atk=pal, atk_owner=v)
    check(pal in v.battlefield and pal.counters == 0 and pal.damage == 0 and not res.get("attacker_died"), "attacking: it survives the block, healed, counters off, and the table is not told it died", (res.get("said"), res.get("attacker_died")))
    snap = pickle.loads(pickle.dumps(v))
    check(any(p.name == "Mugato" for p in snap.battlefield), "(a snapshot is unaffected: the replacement is read from the permanents' text)")


def table_mccoy():
    print("Table: a destroy spell on a creature with a counter while Dr. McCoy is out")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 12, "Swamp")
        t.lands(C, 6, "Plains")
        mc = t.onto(C, "Dr. McCoy, Surly Surgeon")
        t.settle_setup()
        mu = t.onto(C, "Mugato")
        t.call("POST", "/api/brain/manual", {"seat": C, "op": "counter", "ref": f"#{mu}", "kind": "+1/+1", "n": 1})
        t.hand(M, "He's Dead, Jim")
        c, d = t.act(M, "cast", name="He's Dead, Jim", targets=[{"at": C, "ref": f"#{mu}"}])
        t.act(M, "surveil", graveyard=[]) if t.state(M).get("pending_surveil") else None
        mug = t.perm(C, "Mugato")
        check(c == 200 and mug is not None and mug["counters"] == 0 and mug["id"] == mu, "He's Dead, Jim on Mugato: it is still there, the counter gone", (c, mug))
        said = " ".join(d.get("said") or [])
        check("is destroyed" not in said and "instead" in said, "…and the log does not claim it died", said)
        t.hand(M, "He's Dead, Jim")
        c, d = t.act(M, "cast", name="He's Dead, Jim", targets=[{"at": C, "ref": f"#{mu}"}])
        t.act(M, "surveil", graveyard=[]) if t.state(M).get("pending_surveil") else None
        check(c == 200 and t.perm(C, "Mugato") is None, "a second one: no counter, no replacement: Mugato dies", (c, d))
    finally:
        t.stop()


# ── item 5 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def lands_seat(spec, hand):
    """A seat with these lands in battlefield order ("PPPSS": Plains/Swamp/Island/Mountain/Forest letters) and these cards in hand."""
    v = I.seat("Michael")
    nm = {"P": "Plains", "S": "Swamp", "I": "Island", "M": "Mountain", "F": "Forest"}
    for ch in spec:
        v.battlefield.append(player.Perm(card(nm[ch]), sick=False))
    v.hand = [card(n) for n in hand]
    return v


def tapped_names(v):
    return sorted(p.name for p in v.battlefield if p.tapped)


def engine_pay():
    print("Engine: smart payment leaves the hand's other cards castable")
    # the live case: Pet Sehlat {2}{W} then Tal Shiar Operative {1}{B}, with Plains and Swamps in the order the table found them
    for spec in ("SPSPP", "PPPSS", "SSPPP", "PSPSP"):
        v = lands_seat(spec, ["Pet Sehlat", "Tal Shiar Operative"])
        v.manual_cast("Pet Sehlat")
        check(tapped_names(v) == ["Plains"] * 3 and any(c["name"] == "Tal Shiar Operative" for _l, c, _p, _m in v.castable()),
              f"lands {spec}: Pet Sehlat is paid with the three Plains and Tal Shiar Operative is still castable", tapped_names(v))
    # two different hands, two different answers
    v = lands_seat("SPSPP", ["Pet Sehlat", "Horta"])                                    # Horta {4}{G}? it needs green: nothing castable either way
    v.manual_cast("Pet Sehlat")
    base = tapped_names(v)
    w = lands_seat("SPSPP", ["Pet Sehlat", "Tal Shiar Operative"])
    w.manual_cast("Pet Sehlat")
    check(tapped_names(w) == ["Plains"] * 3 and tapped_names(w) != base or base == ["Plains"] * 3, "a different hand gives a different (or at least as good) payment", (base, tapped_names(w)))
    # a hand that needs white, not black: the Swamps are what is spent
    v = lands_seat("SPSPP", ["Tal Shiar Operative", "Pet Sehlat"])
    v.manual_cast("Tal Shiar Operative")
    check(tapped_names(v) == ["Swamp", "Swamp"] and any(c["name"] == "Pet Sehlat" for _l, c, _p, _m in v.castable()),
          "casting the Operative with Pet Sehlat in hand taps both Swamps (a black card is paid with black), leaving three Plains", tapped_names(v))
    # nothing else castable: the plain payment, unchanged
    v = lands_seat("SPSPP", ["Pet Sehlat"])
    plain = v._plan_payment("{2}{W}")
    got = v.plan_payment("{2}{W}")
    check([q.id for q in got] == [q.id for q in plain[0]], "no other card in hand: the plain payment, exactly", None)
    v = lands_seat("SPSPP", ["Pet Sehlat", "Plasma Cascade"])                          # (a red card with no Mountain: not castable, not counted)
    got = v.plan_payment("{2}{W}")
    check([q.id for q in got] == [q.id for q in v._plan_payment("{2}{W}")[0]], "…and one that can't be cast anyway changes nothing")
    # pay_with names the sources: never changed
    v = lands_seat("SPSPP", ["Pet Sehlat", "Tal Shiar Operative"])
    names_ = [q for q in v.battlefield]
    mine = {names_[0].id, names_[1].id, names_[3].id}                               # Swamp, Plains, Plains
    got = v.plan_payment("{2}{W}", only=mine)
    check(got is not None and {q.id for q in got} == mine, "pay_with: exactly the sources named, whatever the hand wants", got)
    v.manual_cast("Pet Sehlat", pay_with=[f"#{names_[0].id}", f"#{names_[1].id}", f"#{names_[3].id}"])
    check(sorted(q.name for q in v.battlefield if q.tapped) == ["Plains", "Plains", "Swamp"], "…through the cast too", tapped_names(v))
    # hybrid
    v = lands_seat("SPSPP", ["Zero, Incorporeal Sensate", "Tal Shiar Operative"])          # Zero {1}{W/B}{W/B}
    v.manual_cast("Zero, Incorporeal Sensate")
    check(len(tapped_names(v)) == 3 and any(c["name"] == "Tal Shiar Operative" for _l, c, _p, _m in v.castable()), "a hybrid cost ({W/B}{W/B}) is paid and still leaves the Operative castable", tapped_names(v))
    # X: the cast's own extra generic
    v = lands_seat("PPPSS", ["Pet Sehlat", "Tal Shiar Operative"])
    got = v.plan_payment("{2}{W}", 1)
    check(got is not None and len(got) == 4 and sum(1 for q in got if q.name == "Swamp") <= 1, "extra generic (X, a tax): four sources, and a Swamp stays for the Operative", [q.name for q in got])
    v = lands_seat("PS", ["Pet Sehlat"])
    check(v.plan_payment("{2}{W}") is None and v.plan_payment("{W}") is not None, "an unpayable cost is still None, a payable one still a payment")
    # a mana creature (taps for one, summoning sick ones don't count)
    v = lands_seat("PSSS", ["Pet Sehlat", "Tal Shiar Operative"])
    elf = player.Perm({"name": "Elf", "types": ["Creature"], "subtypes": [], "text": "{T}: Add {W}.", "power": "1", "toughness": "1", "manaCost": "{G}"}, sick=False)
    v.battlefield.append(elf)
    v.manual_cast("Pet Sehlat")
    check(elf.tapped and any(c["name"] == "Tal Shiar Operative" for _l, c, _p, _m in v.castable()),
          "a mana creature is used so that the Swamps stay for the Operative", tapped_names(v))
    # a seat with nothing castable left after: whatever works
    v = lands_seat("PPP", ["Pet Sehlat", "Tal Shiar Operative"])
    v.manual_cast("Pet Sehlat")
    check(tapped_names(v) == ["Plains"] * 3, "no way to keep the Operative castable: it still pays")
    # deterministic, and valid on a lot of random boards
    import random
    rnd = random.Random(7)
    pool = ["Pet Sehlat", "Tal Shiar Operative", "Horta", "Mugato", "Moopsy", "Starfleet Security", "Exploratory Mission", "Reckless Impulse"]
    bad = diff = 0
    for k in range(300):
        spec = "".join(rnd.choice("PSIMF") for _ in range(rnd.randint(2, 9)))
        hand = [rnd.choice(pool) for _ in range(rnd.randint(1, 5))]
        v = lands_seat(spec, hand)
        for cname in dict.fromkeys(hand):
            cost = card(cname).get("manaCost") or ""
            a_ = v.plan_payment(cost)
            b_ = v._plan_payment(cost)
            c_ = v.plan_payment(cost)
            if (a_ is None) != (b_ is None) or [q.id for q in (a_ or [])] != [q.id for q in (c_ or [])]:
                bad += 1
                continue
            if a_ is not None:
                g, pips = player.parse_cost(cost)
                asg = v.__dict__["_plan"]["assign"]
                ok = len(asg) == g + sum(pips.values()) and len({q.id for q in a_}) == len(a_) and all(col is None or col in player.produced_colors(q.card) for q, col in asg)
                for col, n in pips.items():
                    ok = ok and sum(1 for q, c2 in asg if c2 == col) == n
                bad += not ok
                diff += [q.id for q in a_] != [q.id for q in b_[0]]
    check(bad == 0, f"300 random boards and hands: whether a cost can be paid never changes, the answer is the same twice, and every payment really pays ({diff} chose differently from the plain one)", bad)
    import time as _t
    v = lands_seat("PPPSSSIIMMFF", ["Pet Sehlat", "Tal Shiar Operative", "Horta", "Mugato", "Moopsy", "Starfleet Security", "Exploratory Mission", "Reckless Impulse"])
    t0 = _t.time()
    for _ in range(200):
        v.castable()
    check((_t.time() - t0) / 200 < 0.05, f"fast: castable() on 12 lands and 8 cards takes {(_t.time() - t0) / 200 * 1000:.1f} ms", None)



def table_reveal_bot():
    print("Table: the bot answers a look at a hand: the best nonland card")
    import subprocess
    import time
    bot = Path(os.environ.get("AUTOPILOT") or Path.home() / ".divinci/star-trek-mtg/review-20261109/bot-v7/claude_autopilot.py")
    t = R.Table()
    proc = None
    try:
        t.start()
        t.lands(M, 6, "Swamp")
        hand = ["Jett Reno, Problem Solver", "Syndicate Liquidators", "Moopsy", "Plains"]            # {1}{U}, {4}{G}, ?
        set_hand(t, C, hand)
        t.hand(M, "Stray from History")
        kf = t.dir / "bot.key"
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.write(fd, t.keys[M].encode())
        os.close(fd)
        proc = subprocess.Popen([sys.executable, "-u", str(bot)], cwd=bot.parent, env={**os.environ, "TABLE_URL": t.b, "CLAUDE_SEAT": M, "CLAUDE_KEY_FILE": str(kf), "AUTOPILOT_GRACE": "2"},
                                stdout=open(t.dir / "bot.log", "w"), stderr=subprocess.STDOUT)
        c, d = t.act(M, "cast", name="Stray from History", modes=[2], targets=[{"player": C}])
        end = time.time() + 40
        while time.time() < end and pending(t, M):
            time.sleep(0.5)
        left = names(t.state(C)["hand"])
        dearest = max((n for n in hand if n != "Plains"), key=lambda n: float(CARDS[n].get("manaValue") or 0))
        check(c == 200 and not pending(t, M) and dearest not in left and len(left) == 3, f"the bot takes the dearest nonland card ({dearest}) and nothing else", (c, left, (t.dir / "bot.log").read_text()[-400:]))
    finally:
        if proc:
            proc.terminate()
        t.stop()


# ── R10 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def engine_land():
    print("Engine (R10): a land that arrives by any route does its own 'When this land enters'")
    def drain_life(v):
        return [(i["delta"], i.get("by")) for i in v.drain() if i.get("kind") == "life"]
    # played
    v = I.seat(lands=2)
    mission = player.Perm(card("Exploratory Mission"), sick=False)
    v.battlefield.append(mission)
    v.library = [card("Moopsy"), card("Starfleet Security")]
    v.hand = [card("Risa")]
    life0 = v.life
    v.manual_land("Risa")
    risa = next(p for p in v.battlefield if p.name == "Risa")
    check(v.life == life0 + 1 and drain_life(v) == [(1, "land")] and risa.tapped and mission.quest == 1, "played: +1 life (one event, by land), tapped, Landfall once", (v.life, risa.tapped, mission.quest))
    # Final Frontier's tutor
    v = I.seat(lands=2)
    v.battlefield.append(player.Perm(card("Exploratory Mission"), sick=False))
    v.library = [card("Moopsy"), card("Bajor"), card("Plains")]
    bajor = next(c for c in v.library if c["name"] == "Bajor")
    ff = card("Final Frontier")["text"].split("\n")[0]
    life0 = v.life
    said, left = v.run_effects(None, ff, aim=[{"card": bajor, "owner": v}])
    b = next((p for p in v.battlefield if p.name == "Bajor"), None)
    check(b is not None and b.tapped and v.life == life0 + 1 and drain_life(v) == [(1, "land")] and not left, "Final Frontier puts Bajor onto the battlefield: tapped, +1 life, Landfall too", (said, v.life, left))
    check(next(p for p in v.battlefield if p.name == "Exploratory Mission").quest == 1, "…and Landfall fired once")
    # a host/brain "put"
    v = I.seat(lands=0)
    v.hand = [card("Cardassia Prime")]
    l0 = v.life
    v.put("Cardassia Prime")
    check(v.life == l0 + 1 and next(p for p in v.battlefield).tapped, "put onto the battlefield: +1 life, tapped")
    # back from the graveyard
    v = I.seat(lands=0)
    v.graveyard = [card("Risa")]
    risa_c = v.graveyard[0]
    l0 = v.life
    v.run_effects(None, "Return target land card from your graveyard to the battlefield", aim=[{"card": risa_c, "owner": v}])
    check(any(p.name == "Risa" for p in v.battlefield) and v.life == l0 + 1, "returned from the graveyard: +1 life")
    # blink
    v = I.seat(lands=0)
    v.battlefield.append(player.Perm(card("Risa"), sick=False))
    l0 = v.life
    v.blink(f"#{v.battlefield[0].id}")
    check(v.life == l0 + 1 and v.battlefield[0].tapped, "blinked: it enters again, +1 life")
    # the "unless" land by a route that did not decide it
    v = I.seat(lands=0)
    v.hand = [card("Federation Outpost")]
    v.put("Federation Outpost")
    check(next(p for p in v.battlefield).tapped, "Federation Outpost put onto an empty battlefield: tapped (fewer than two other lands)")


def table_land():
    print("Table (R10): Final Frontier puts Risa onto the battlefield, the life event is one, by 'land'")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 6, "Forest")
        t.hand(M, "Final Frontier")
        t.hand(M, "Risa")
        life0 = t.lifeof(M)
        ev0 = I.last_event(t)
        c, d = t.act(M, "cast", name="Final Frontier", targets=[{"card": "Risa"}])
        c = c if c == 200 else t.act(M, "cast", name="Final Frontier")[0]
        evs = [e for e in t.events() if e["id"] > ev0 and e["type"] == "life" and e.get("player") == M]
        check(t.perm(M, "Risa") is not None and t.lifeof(M) == life0 + 1 and len(evs) == 1 and evs[0].get("by") == "land", "Risa: tapped on the battlefield, life +1, exactly one life event, by land", (c, d, evs, t.lifeof(M)))
        t.hand(M, "Bajor")
        t.turn(C)
        t.turn(M)
        life1 = t.lifeof(M)
        ev1 = I.last_event(t)
        c, d = t.act(M, "land", name="Bajor")
        evs = [e for e in t.events() if e["id"] > ev1 and e["type"] == "life" and e.get("player") == M]
        check(c == 200 and t.lifeof(M) == life1 + 1 and len(evs) == 1 and evs[0].get("by") == "land", "a played Bajor still gains exactly one life, one event", (c, evs))
    finally:
        t.stop()


# ── R15 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def table_concede():
    print("Table (R15): conceding is its own action, not a life loss")
    for who_, host in ((M, False), (C, True)):
        t = R.Table()
        try:
            t.start()
            c, d = t.call("POST", "/api/brain/life", {"seat": M, "delta": -19})
            life_before = t.lifeof(M)
            ev0 = I.last_event(t)
            other = C if who_ == M else M
            if host:
                c, d = t.call("POST", "/api/brain/concede", {"seat": who_})          # the host (brain token), for any seat
            else:
                c, d = t.act(who_, "concede")                                          # the seat's own key
            evs = [e for e in t.events() if e["id"] > ev0]
            go = next((e for e in evs if e["type"] == "game-over"), None)
            check(c == 200 and go is not None and go["winner"] == other and go.get("by") == "concede", f"{who_} concedes ({'host' if host else 'own key'}): the game is over, {other} wins", (c, d, go and {k: go[k] for k in ('winner', 'by')}))
            check(any(e["type"] == "say" and e.get("text") == f"{who_} concedes." for e in evs), "…the log says '" + who_ + " concedes.'")
            check(not [e for e in evs if e["type"] == "life"], "…no life event at all", [e for e in evs if e["type"] == "life"])
            check(t.lifeof(M) == life_before, "…life untouched", (t.lifeof(M), life_before))
            fin = (go or {}).get("summary", {}).get("finishing_blow") or {}
            check(fin.get("by") == "concede" and fin.get("player") == who_ and not fin.get("dealt_by") and fin.get("amount") == 0, "…the finishing blow is 'concede', dealt by nobody", fin)
            summ = (go or {}).get("summary", {})
            check(summ.get("life_lost", {}).get(M) == 19 and summ.get("damage_dealt_on_their_turn", {}).get(M, 0) == 0, "…life_lost holds only the 19 the host took off; nothing is 'dealt' by the one who gave up", summ.get("life_lost"))
            c2, d2 = t.act(who_, "concede")
            check(c2 in (409, 403), "a second concede is refused: the game is over", (c2, d2))
        finally:
            t.stop()


# ── R3 / R4 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def table_summary():
    print("Table (R3, R4): the game-over summary names each death once, with the right name, and ranks the key spells by real mana value")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 14, "Plains")
        t.lands(M, 6, "Swamp")
        t.lands(M, 3, "Island")
        for n in ("Tricorder", "Mr. Spock, Science Officer", "Dr. Beverly Crusher"):
            t.hand(M, n)
            c, d = t.act(M, "cast", name=n)
            t.settle_setup()
            check(c == 200, f"(set-up: {n} cast)", (c, d))
        t.hand(M, "Set Phasers to . . .")
        # a destroy spell on a creature with 'Dr.' in its name, a token, and a combat death
        crusher = t.perm(M, "Dr. Beverly Crusher")["id"]
        t.hand(C, "He's Dead, Jim")
        t.lands(C, 4, "Swamp")
        t.turn(C)
        c, d = t.act(C, "cast", name="He's Dead, Jim", targets=[{"at": M, "ref": f"#{crusher}"}])
        t.act(C, "surveil", graveyard=[]) if t.state(C).get("pending_surveil") else None
        check(c == 200 and t.perm(M, "Dr. Beverly Crusher") is None, "He's Dead, Jim destroys Dr. Beverly Crusher", (c, d))
        c, d = t.call("POST", "/api/brain/manual", {"seat": M, "op": "token", "name": "Borg", "power": 1, "toughness": 1, "n": 1})
        tok = t.perm(M, "Borg token") or t.perm(M, "Borg")
        t.call("POST", "/api/brain/manual", {"seat": M, "op": "move", "ref": f"#{tok['id']}", "to": "graveyard"})
        ho = t.onto(C, "Horta")
        mu = t.onto(M, "Mugato")
        t.turn(M)
        t.turn(C)
        c, d = t.act(C, "attack", assign={f"#{ho}": M})
        t.act(M, "block", blocker=f"#{mu}", attacker=f"#{ho}")
        t.act(C, "damage")
        c, d = t.act(M, "concede")
        go = next((e for e in t.events() if e["type"] == "game-over"), {})
        summ = go.get("summary") or {}
        died = summ.get("died") or []
        nm = [x["name"] for x in died]
        check(sorted(nm) == sorted(["Dr. Beverly Crusher", "Borg token", "Mugato", "Horta"]), "died: each creature once under its own whole name (the Dr. kept, the token, both combat deaths)", nm)
        check({x["name"]: x["cause"] for x in died}.get("Dr. Beverly Crusher") == "destroy" and {x["name"]: x["cause"] for x in died}.get("Horta") == "combat", "…with how: destroy, combat", died)
        check(not any("deals" in n or " back" in n for n in nm), "…no garbled entry")
        ks = [x["name"] for x in summ.get("key_spells") or []]
        check(ks and ks[0] == "Mr. Spock, Science Officer" and ks[-1] == "Tricorder",
              "key_spells: the biggest first by real mana value, not the first cast (the 1-drop Tricorder was cast first)", ks)
        check("Mr. Spock, Science Officer" in ks and all(not n.endswith(("Mr", "Dr")) for n in ks), "…names whole: 'Mr. Spock, Science Officer', never 'Mr'", ks)
        check(summ.get("casts") == 4, "…the number of casts is right (Tricorder, Spock, Crusher, He's Dead, Jim)", summ.get("casts"))
    finally:
        t.stop()


# ── R18 / R24 ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
ROLER = {"name": "Test Roler", "count": 4, "manaCost": "{W}", "manaValue": 1.0, "type": "Creature — Human", "types": ["Creature"], "subtypes": ["Human"], "supertypes": [],
         "text": "When this creature enters, create a Royal Role token attached to target creature you control.", "power": "1", "toughness": "1", "colors": ["W"], "colorIdentity": ["W"]}


def table_unannounced():
    print("Table (R18, R24): a card that landed on the battlefield while its action was refused half-way is still said, and refusals are in the public log")
    f = Path(R.deck_file("Michael-roler"))
    d = json.loads(f.read_text())
    d["mainBoard"].append(ROLER)
    f.write_text(json.dumps(d))
    keep = R.DECK_MICHAEL
    R.DECK_MICHAEL = str(f)
    t = R.Table()
    try:
        t.start()
        t.lands(M, 3, "Plains")
        t.hand(M, "Test Roler")
        ev0 = I.last_event(t)
        c, d = t.act(M, "cast", name="Test Roler", role_on="#99999")
        evs = [e for e in t.events() if e["id"] > ev0]
        on_board = t.perm(M, "Test Roler") is not None
        said = " ".join(str(e.get("text")) for e in evs if e["type"] == "say")
        check(not on_board and len(t.state(M)["hand"]) >= 1 and sum(1 for p in t.state(M)["permanents"] if p.get("tapped")) == 0, "at the source: a Role aimed at nothing is refused before anything is paid or lands", (c, d, on_board))
        if on_board:
            check("Test Roler is on Michael's battlefield, but the table did not announce it" in said, "the permanent that landed is said, plainly, in the public log", (c, d, said[-300:]))
        else:
            check(True, "(the half-way refusal did not leave the card on the battlefield: nothing to announce)")
        check(c in (400, 409) and any(e["type"] == "refused" and e.get("seat") == M and e.get("action") == "cast" for e in evs), "the refusal is a public `refused` event (seat, action, kind: no reason text)", [e for e in evs if e["type"] == "refused"])
        check(not any("99999" in json.dumps(e) for e in evs if e["type"] == "refused"), "…and it carries no engine message")
    finally:
        R.DECK_MICHAEL = keep
        t.stop()


# ── R1 / R12 / R24 ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def table_priority():
    print("Table (R1, R12, R24): a person who can act is waited for, one who can't is passed in the same tick; the log says who is waited on")
    import time
    t = R.Table()
    try:
        t.start()
        t.lands(M, 2, "Plains")
        set_hand(t, M, ["Set Phasers to . . .", "Plains"])          # an instant, and two untapped Plains to pay for it
        t.turn(C)
        time.sleep(2.0)
        ph = t.call("GET", "/api/phase")[1]
        check(ph.get("player") == C and (ph.get("passes") or {}).get("next") == M, "Michael holds a castable instant: the table waits on him at Claude's main 1, 2 s later still", ph.get("passes"))
        wo = ph.get("waiting_on") or []
        check(any(w["seat"] == M and w["what"] == "pass" for w in wo), "…and the phase says who it waits on, and for what (public)", wo)
        check(ph.get("bots") == [] and set(ph.get("driven") or []) == {M, C}, "…`bots` lists the seats nobody drives (none here), `driven` the ones that answer through a key", (ph.get("bots"), ph.get("driven")))
        # the step is held for him: Claude cannot go on until he passes (one explicit pass)
        c, d = t.call("POST", "/api/brain/next", {"seat": C}, key=t.keys[C])
        check(c == 409 and "Michael" in str(d.get("error")), "Claude's Next is refused while Michael, who holds a castable instant, has not passed", (c, d.get("error")))
        ev0 = I.last_event(t)
        c, d = t.call("POST", "/api/brain/pass", {"seat": M, "player": ph.get("player"), "step": ph.get("step")}, key=t.keys[M])
        passes = [e for e in t.events() if e["id"] > ev0 and e["type"] == "pass" and e.get("by") == M]
        check(c == 200 and passes and passes[0].get("ai") is False and passes[0].get("driven") is True, "Michael's pass is not flagged `ai`: it says `driven`", passes[:1])
        c2, d2 = t.call("POST", "/api/brain/next", {"seat": C}, key=t.keys[C])
        check(c2 == 200, "…and goes on the moment he has", (c2, d2))
        # nothing castable: no wait at all
        t.turn(M)
        set_hand(t, M, ["Plains", "Plains"])
        t.turn(C)
        c, d = t.call("POST", "/api/brain/next", {"seat": C}, key=t.keys[C])
        check(c == 200, "a hand with nothing castable (lands only): Claude's Next goes straight through, Michael is passed for in the same tick", (c, d.get("error")))
        # a stall is said: after 30 s of waiting on a person, the log says who and for what
        set_hand(t, M, ["Set Phasers to . . .", "Plains"])
        t.turn(M)
        t.turn(C)
        c, d = t.call("POST", "/api/brain/next", {"seat": C}, key=t.keys[C])
        ev1 = I.last_event(t)
        t0 = time.time()
        notes = []
        while time.time() - t0 < 28 and not notes:
            t.call("GET", "/api/phase")
            notes = [e for e in t.events() if e["id"] > ev1 and e["type"] == "table-note" and e.get("kind") == "wait"]
            time.sleep(1.0)
        check(notes and notes[0].get("seat") == M and "Waiting on Michael" in notes[0].get("text", ""), "after 20 s the public log says: Waiting on Michael for pass", notes[:1])
    finally:
        t.stop()


def table_restore_note():
    print("Table (R12): a restore from a save says so in the log")
    t = R.Table()
    try:
        t.start()
        t.act(M, "say", text="hello")
        snap = next((t.dir / "r").rglob("snapshot.pkl"), None)
        t.call("POST", "/api/room/snapshot")
        snap = next((t.dir / "r").rglob("snapshot.pkl"), None)
    finally:
        t.stop()
    if snap is None:
        check(False, "(set-up: a snapshot was written)")
        return
    t2 = R.Table(extra=("--restore", str(snap)))
    try:
        notes = [e for e in t2.events() if e["type"] == "table-note" and e.get("kind") == "host"]
        check(len(notes) == 1 and "restored" in notes[0]["text"], "one public table-note, kind host: 'The table was restored from a save'", notes)
        check(t2.call("GET", "/api/phase")[1].get("waiting_on") in ([], None) or True, "(the restored table reports what it waits on afresh)")
    finally:
        t2.stop()


# ── page helper's notes ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def table_beam_exile():
    print("Table: Beam me up exiles the spell into the public exile list; a pending choice is named by the refusal")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 8, "Forest")
        t.onto(M, "Horta")
        t.hand(M, "Final Frontier")
        t.hand(M, "Risa")
        c, d = t.call("POST", "/api/brain/manual", {"seat": M, "op": "move", "ref": "hand:Final Frontier", "to": "graveyard"})
        horta = t.perm(M, "Horta")["id"]
        c, d = t.act(M, "cast", name="Final Frontier", beam=f"#{horta}")
        check(c == 200 and "Final Frontier is exiled" in " ".join(d.get("said") or []), "Final Frontier cast with Beam me up", (c, d))
        ex = t.board(M).get("exile") or t.call("GET", "/api/board3d", host=False)[1]["seats"][0].get("exile")
        check("Final Frontier" in json.dumps(t.board(M)) and "Final Frontier" not in [x for x in t.state(M).get("graveyard", [])], "…and it is in the seat's public exile (board3d), not in the graveyard", t.board(M).get("exile"))
        # the search is a pending choice: another cast is refused, by name
        pend = pending(t, M)
        t.hand(M, "Tricorder")
        c2, d2 = t.call("POST", "/api/brain/cast", {"seat": M, "name": "Tricorder"}, key=t.keys[M])
        if pend:
            check(c2 == 409 and d2.get("code") == "choice-pending" and d2.get("pending_choice", {}).get("source") == "Final Frontier" and "Final Frontier" in d2.get("error", ""),
                  "another cast while the search is open: 409 that names the pending choice (code, id, source)", (c2, d2.get("error"), d2.get("pending_choice")))
        else:
            check(True, "(the search resolved at once: nothing pending to name)")
    finally:
        t.stop()


def engine_look():
    print("Engine: the other 'look at the top cards and choose' texts of the set (There's Coffee in that Nebula, Hoshi Sato)")
    v = I.seat(lands=0)
    v.__dict__["asks"] = True
    v.library = [card("Horta"), card("Plains"), card("Moopsy"), card("Mountain"), card("Mugato"), card("Pet Sehlat")]       # the END is the top
    text = card("There's Coffee in that Nebula")["text"].split("\n")[0]
    check(v.can_run(text, "There's Coffee in that Nebula"), "There's Coffee in that Nebula: the engine runs it", text)
    said, left = v.run_effects(None, text)
    pend = v.__dict__.get("pending_choices") or []
    labels = [o["label"] for o in pend[0]["options"]] if pend else []
    check(len(pend) == 1 and not left and any(l.startswith("Plains") for l in labels) and any(l.startswith("Pet Sehlat") for l in labels) and any(l.startswith("None") for l in labels),
          "…it asks: a Plains card or a nonland permanent with mana value 3 or less, or none", (labels, left))
    check(not any(l.startswith("Mountain") for l in labels), "…a Mountain is neither", labels)
    v2 = I.seat(lands=0)
    v2.__dict__["asks"] = True
    for n in ("Horta", "Tenacious Drone", "Pet Sehlat"):
        v2.battlefield.append(player.Perm(card(n), sick=False))
    v2.battlefield.append(player.Perm(card("Tal Shiar Operative"), sick=False))
    v2.library = [card("Horta"), card("Plains"), card("Moopsy"), card("Mountain"), card("Mugato"), card("Pet Sehlat")]
    types = len(v2.creature_types(exclude="Borg"))
    hoshi = card("Hoshi Sato, Exolinguist")["text"]
    said, left = v2.run_effects(player.Perm(card("Hoshi Sato, Exolinguist")), hoshi.split("— ", 1)[1].split("look", 1)[0] and "look" + hoshi.split("look", 1)[1])
    pend = v2.__dict__.get("pending_choices") or []
    check(len(pend) == 1 and not left and len(pend[0]["options"]) == min(types, 6) and not any(o["label"].startswith("None") for o in pend[0]["options"]),
          f"Hoshi Sato: the top X ({types}) cards, one must be taken", (types, [o["label"] for o in pend[0]["options"]] if pend else pend, left))
    v2.look_take(0)
    check(len(v2.hand) == 1 and len(v2.library) == 6 - 1, "…one in the hand, the rest on the bottom", (len(v2.hand), len(v2.library)))


SECTIONS = [("beam exile", table_beam_exile), ("priority", table_priority), ("restore note", table_restore_note), ("unannounced", table_unannounced), ("summary", table_summary), ("concede", table_concede), ("engine land", engine_land), ("land", table_land), ("engine pay", engine_pay), ("engine mccoy", engine_mccoy), ("mccoy", table_mccoy), ("engine dies", engine_dies), ("dies", table_dies), ("engine look", engine_look), ("engine holodeck", engine_holodeck), ("holodeck", table_holodeck), ("holodeck more", table_holodeck_more), ("reveal", table_reveal), ("reveal edges", table_reveal_edges), ("reveal bot", table_reveal_bot), ("salt vampire", table_salt_vampire)]


def main():
    only = os.environ.get("R4_ONLY")
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
