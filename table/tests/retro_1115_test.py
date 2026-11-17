"""Game retro 2026-11-15 (Star Trek set, 1v1, two pilot seats). One section per item, each on a table of its own on a free port (never a live
game's: 8800, 8801, 8820, 8830, 8883 are refused), through the route a player uses (the seat's own key); set-up uses the host token.

  1  Support Mission: "Whenever a creature you control enters, put a quest counter on this enchantment. Put a +1/+1 counter on that creature.
     If this enchantment has five or more quest counters on it, instead put two +1/+1 counters on that creature." Quest counters are kept apart
     from +1/+1 counters, and the host's `counter` on an enchantment says which counter it is.
  2  a host's +1/+1 counter stays on the creature through the opponent's turn, attack, blocks and end step (a guard: the night's loss did not
     reproduce on any of these paths)
  3  L'Rell, Fierce Visionary: the beginning-of-combat token is made when the turn ENTERS beginning of combat, also when the attack is declared
     straight from main 1; a pilot whose attack was chosen before it existed is asked to declare again; the end step sacrifices THAT token only
  4  a summoning-sick creature's {T} mana ability is refused (tap for_mana)
  5  Tal Shiar Operative's "Target creature you control gains deathtouch until end of turn" asks for its target; the host's `grant` gives a
     keyword until end of turn, combat damage honours it, and it is gone the next turn
  10 the redeclare 409 (review of the L'Rell fix): the autopilot declares again and both attack; the old choice sent again is taken (one 409);
     a host-token attack and an attack after NEXT into beginning of combat get none; a token that can't attack (tapped) gets none; after the
     409, End still sacrifices the token
  11 the host's `counter`: a planeswalker defaults to loyalty, each kind lands in its own field, an unknown kind and |n| > 99 are refused, and
     a count never goes below 0
  12 summoning sickness and haste: a creature with printed or granted haste taps for mana the turn it arrives

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 HF_HUB_OFFLINE=1 python3 table/tests/retro_1115_test.py
    R1115_ONLY="support|lrell"   only the sections whose title names one of these
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
from v6_harness import R, Table, check, choices, answer  # noqa: E402

LIVE_PORTS = (8800, 8801, 8820, 8830, 8883)
BOT = HERE.parent / "bots"                                 # the rules autopilot (claude_autopilot.py), for section 10
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in LIVE_PORTS)   # never a live game's port
R.POOL.update({n: 6 for n in ("Starfleet Security", "Captain's Tutelage", "Cybernetic Specialist", "Boarding Action", "Plasma Cascade", "Transporter Malfunction", "Support Mission", "Xenobotanist", "Tal Shiar Operative", "L'Rell, Fierce Visionary", "Mugato", "Xindi Surveyors", "Horta")})
R.DECK_MICHAEL, R.DECK_CLAUDE = R.deck_file("Michael-1115"), R.deck_file("Claude-1115")
M, C = "Michael", "Claude"


def table():
    t = Table()
    assert t.port not in LIVE_PORTS
    t.start()                                              # Michael's main 1, response windows off
    return t


def perm_id(t, seat, name):
    return next(p["id"] for p in t.state(seat)["permanents"] if p["name"] == name)


def says(t, ev0=0):
    return [e.get("text") or "" for e in t.events(ev0) if e.get("type") == "say"]


def act_until(t, seat, action, secs=60, **body):
    """t.act with a DEADLINE, not a try count: with response windows on, a step's round can take ~20 s (the pass timers), which a count of 80
    tries at a quarter second gave up on half the time. Retries while the table says it is waiting on passes (passing for the other seats here,
    as a person would), and returns the first other answer."""
    end = time.time() + secs
    c, d = 0, {}
    while time.time() < end:
        c, d = t.call("POST", f"/api/brain/{action}", {"seat": seat, **body}, key=t.keys[seat])
        if not (c == 409 and "waiting on" in str(d.get("error", "")) and "defenders" not in str(d.get("error"))):
            return c, d
        ph = t.call("GET", "/api/phase")[1]
        for w in set(str(d.get("waiting") or "").split(", ")) | {(ph.get("passes") or {}).get("next")}:
            if w in t.keys and w != seat:
                t.call("POST", "/api/brain/pass", {"seat": w, "player": ph.get("player"), "step": ph.get("step")}, key=t.keys[w])
        time.sleep(0.25)
    return c, d


def attackers(t, seat):
    """The refs attacking for `seat` this turn, from the public combat."""
    for a in (t.call("GET", "/api/phase")[1].get("combat") or {}).get("attacks") or []:
        if a.get("attacker") == seat:
            return sorted(r.get("ref") for r in a.get("attackers") or [])
    return []


def take_all(t, defender=M):
    """The defender answers every attack: no block (it takes it)."""
    for a in (t.call("GET", "/api/phase")[1].get("combat") or {}).get("attacks") or []:
        for r in a.get("attackers") or []:
            if r.get("defender") == defender and not r.get("answered"):
                t.call("POST", "/api/brain/block", {"seat": defender, "attacker": r["ref"]}, key=t.keys[defender])


def support_mission():
    t = table()
    try:
        t.lands(M, 6, "Swamp")
        sm = t.onto(M, "Support Mission")
        t.hand(M, "Tal Shiar Operative")
        c, d = t.act(M, "cast", name="Tal Shiar Operative")
        op, mis = t.perm(M, "Tal Shiar Operative"), t.perm(M, "Support Mission")
        check(c == 200 and op and op["counters"] == 1 and op["pt"] == "3/3" and mis["quest"] == 1 and mis["counters"] == 0,
              "a creature enters: a quest counter on Support Mission, a +1/+1 counter on the creature (3/3)", (c, d.get("said") or d, op, mis))
        ev0 = t.events(0)[-1]["id"]
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{sm}", "n": 3})
        mis = t.perm(M, "Support Mission")
        check(c == 200 and mis["quest"] == 4 and mis["counters"] == 0 and not any("+1/+1" in x for x in d.get("said") or [])
              and any("quest counter" in x for x in d.get("said") or []),
              "the host's counter on the enchantment (no kind named) is a QUEST counter, and says so (not '+3/+3 counters')", (c, d, mis))
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{sm}", "n": 1, "kind": "+1/+1"})
        check(c == 200 and t.perm(M, "Support Mission")["counters"] == 1, "…and kind names any other counter explicitly", (c, d))
        t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{sm}", "n": -1, "kind": "+1/+1"})
        t.hand(M, "Tal Shiar Operative")
        c, d = t.act(M, "cast", name="Tal Shiar Operative")
        ops = t.perms(M, "Tal Shiar Operative")
        mis = t.perm(M, "Support Mission")
        check(c == 200 and mis["quest"] == 5 and sorted(o["counters"] for o in ops) == [1, 2],
              "the fifth quest counter: two +1/+1 counters on the creature that entered (the first keeps its one)", (c, d.get("said") or d, ops, mis))
        ev = says(t, ev0)
        check(any("Support Mission gets a quest counter (5)" in x and "two +1/+1 counters" in x for x in ev), "…and the log says so", ev[-6:])
    finally:
        t.stop()


def counter_persists():
    t = table()
    try:
        op = t.onto(M, "Tal Shiar Operative")
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{op}", "n": 1})
        check(c == 200 and t.perm(M, "Tal Shiar Operative")["pt"] == "3/3", "host: +1/+1 counter on Tal Shiar Operative (3/3)", (c, d))
        t.onto(C, "L'Rell, Fierce Visionary")
        t.turn(C)
        t.next_to(C, "declare attackers")
        refs = {f"#{p['id']}": M for p in t.state(C)["permanents"] if p["name"].startswith(("L'Rell", "Klingon"))}
        c, d = t.act(C, "attack", assign=refs)
        tok = next(p for p in t.state(C)["permanents"] if p["name"].startswith("Klingon"))
        lr = perm_id(t, C, "L'Rell, Fierce Visionary")
        t.call("POST", "/api/brain/block", {"seat": M, "attacker": f"#{tok['id']}", "blocker": f"#{op}"}, key=t.keys[M])
        t.call("POST", "/api/brain/block", {"seat": M, "attacker": f"#{lr}"}, key=t.keys[M])
        check(t.perm(M, "Tal Shiar Operative")["counters"] == 1, "…through the opponent's attack and the block", t.perm(M, "Tal Shiar Operative"))
        for _ in range(4):
            t.act(C, "next")
        t.turn(M, fresh=False)
        p = t.perm(M, "Tal Shiar Operative")
        check(p and p["counters"] == 1 and p["pt"] == "3/3", "…and through the damage step and the end of that turn: still 3/3", p)
    finally:
        t.stop()


def lrell():
    t = table()
    try:
        t.onto(C, "L'Rell, Fierce Visionary")
        c, d = t.call("POST", "/api/brain/token", {"seat": C, "name": "Klingon Warrior", "power": 2, "toughness": 1})   # a Klingon token of another source
        other = next(p["id"] for p in t.state(C)["permanents"] if p["name"].startswith("Klingon"))
        t.turn(C)
        lr = perm_id(t, C, "L'Rell, Fierce Visionary")
        kl = lambda: [p for p in t.state(C)["permanents"] if p["name"].startswith("Klingon") and p["id"] != other]
        # response windows off: the attack is declared straight from main 1 (the hand page's ⚔ Attack does that)
        c, d = t.call("POST", "/api/brain/attack", {"seat": C, "assign": {f"#{lr}": M}}, key=t.keys[C])
        new = kl()
        check(c == 409 and d.get("redeclare") and len(new) == 1 and new[0]["can_attack"] and not (t.call("GET", "/api/phase")[1].get("combat") or {}).get("attacks"),
              "an attack from main 1: L'Rell's token is made at beginning of combat, and the attack is sent back to be declared again with it", (c, d, new))
        c, d = t.act(C, "attack", assign={f"#{lr}": M, f"#{new[0]['id']}": M})
        check(c == 200 and len(kl()) == 1 and "Klingon Warrior token" in " ".join(d.get("said") or []),
              "…declared again: L'Rell and the hasty token attack, and the trigger did not run a second time", (c, d.get("said") or d, kl()))
        t.call("POST", "/api/brain/block", {"seat": M, "attacker": f"#{lr}"}, key=t.keys[M])
        t.call("POST", "/api/brain/block", {"seat": M, "attacker": f"#{new[0]['id']}"}, key=t.keys[M])
        t.turn(M)
        ids = [p["id"] for p in t.state(C)["permanents"] if p["name"].startswith("Klingon")]
        check(ids == [other], "the end step sacrifices L'Rell's token, and only that one (the other Klingon Warrior token stays)", ids)
        # response windows on: the turn walks main 1 → beginning of combat and waits there for the other seat's pass
        t.turn(C)
        t.call("POST", "/api/phase/windows", {"on": True, "by": "host"}, host=False)   # (on at Claude's main 1: the harness walks turns with them off)
        at_boc = None
        for _ in range(80):                                # the attack walks main 1 → beginning of combat and waits there for Michael's pass
            c, d = t.call("POST", "/api/brain/attack", {"seat": C, "assign": {f"#{lr}": M}}, key=t.keys[C])
            ph = t.call("GET", "/api/phase")[1]
            if c == 409 and ph.get("step") == "beginning of combat":
                at_boc = kl()
                break
            if c != 409 or "waiting on" not in str(d.get("error")):
                break
            t.call("POST", "/api/brain/pass", {"seat": M, "player": ph.get("player"), "step": ph.get("step")}, key=t.keys[M])
            time.sleep(0.25)
        check(at_boc is not None and len(at_boc) == 1, "windows on: the turn stands in beginning of combat with L'Rell's token already there", (c, d.get("error"), ph.get("step"), at_boc))
        c, d = act_until(t, C, "attack", assign={f"#{lr}": M})   # (the same choice again, once the window closes)
        ph = t.call("GET", "/api/phase")[1]
        check(c == 409 and d.get("redeclare") and len(kl()) == 1 and ph.get("step") in ("beginning of combat", "declare attackers")
              and not (t.call("GET", "/api/phase")[1].get("combat") or {}).get("attacks"),
              "…and the attack chosen before it existed is sent back (the night: no token)",
              (c, d.get("error"), ph.get("step"), kl()))
        tok = kl()[0]["id"]
        c, d = act_until(t, C, "attack", assign={f"#{lr}": M, f"#{tok}": M})
        check(c == 200 and len(kl()) == 1, "…declared again with the token: it attacks", (c, d.get("said") or d, kl()))
    finally:
        t.stop()


def mana_sick():
    t = table()
    try:
        xb = t.onto(M, "Xenobotanist")
        c, d = t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{xb}", "for_mana": True, "announce": True}, key=t.keys[M])
        check(c == 400 and "summoning sickness" in str(d.get("error")) and not t.perm(M, "Xenobotanist")["tapped"],
              "Xenobotanist the turn it arrives: its {T}: Add mana is refused, and it stays untapped", (c, d))
        t.turn(C)
        t.turn(M)
        c, d = t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{xb}", "for_mana": True, "announce": True}, key=t.keys[M])
        check(c == 200 and t.perm(M, "Xenobotanist")["tapped"], "…and on its controller's next turn it taps for mana", (c, d))
    finally:
        t.stop()


def tal_shiar():
    t = table()
    try:
        op = t.onto(M, "Tal Shiar Operative")
        xs = t.onto(M, "Xindi Surveyors")
        t.turn(C)
        t.turn(M)
        c, d = t.act(M, "attack", assign={f"#{op}": C})
        answer(t, M, "deathtouch")
        pend = choices(t, M)
        opts = [o["label"] for o in (pend[0]["slot"].get("options") or [])] if pend else []
        check(pend and pend[0]["kind"] == "effect" and any("Xindi Surveyors" in o for o in opts) and any("Tal Shiar Operative" in o for o in opts),
              "deathtouch mode: the table asks which creature of yours gains it", (pend, opts))
        if pend:
            answer(t, M, "Xindi Surveyors")
        check("Deathtouch" in t.perm(M, "Xindi Surveyors")["keywords"] and "Deathtouch" not in t.perm(M, "Tal Shiar Operative")["keywords"],
              "…the one named gains deathtouch, not the Operative", (t.perm(M, "Xindi Surveyors")["keywords"], t.perm(M, "Tal Shiar Operative")["keywords"]))
        t.call("POST", "/api/brain/block", {"seat": C, "attacker": f"#{op}"}, key=t.keys[C])
        t.turn(C)
        check("Deathtouch" not in t.perm(M, "Xindi Surveyors")["keywords"], "…until end of turn: gone on the next turn", t.perm(M, "Xindi Surveyors")["keywords"])
        # the host's grant, and combat damage honouring it
        ho = t.onto(C, "Horta")                            # 7/6
        t.turn(M)
        c, d = t.call("POST", "/api/brain/grant", {"seat": M, "ref": f"#{op}", "keyword": "deathtouch"})
        check(c == 200 and "Deathtouch" in t.perm(M, "Tal Shiar Operative")["keywords"], "host grant: Tal Shiar Operative gains deathtouch until end of turn", (c, d))
        c, d = t.call("POST", "/api/brain/grant", {"seat": M, "ref": f"#{op}", "keyword": "telepathy"})
        check(c == 400, "…an unknown keyword is refused", (c, d))
        c, d = t.act(M, "attack", assign={f"#{op}": C})
        for p_ in choices(t, M):                           # its own attack trigger: Surveil 1, keep the card
            answer(t, M, "Surveil")
            break
        t.call("POST", "/api/brain/surveil", {"seat": M, "graveyard": []}, key=t.keys[M])
        c, d = t.call("POST", "/api/brain/block", {"seat": C, "attacker": f"#{op}", "blocker": f"#{ho}"}, key=t.keys[C])
        for _ in range(3):
            if t.call("GET", "/api/phase")[1].get("step") == "main 2":
                break
            t.act(M, "next")
        check(t.perm(C, "Horta") is None and "Horta" in (t.state(C).get("graveyard") or []),
              "…blocked by Horta (7/6): the 2 deathtouch damage destroys it", (c, d, t.perm(C, "Horta"), t.state(C).get("graveyard")))
        t.turn(C)
        p = t.perm(M, "Tal Shiar Operative")
        check(p is None or "Deathtouch" not in p["keywords"], "…and the grant is over with the turn", p)
    finally:
        t.stop()


def any_target():
    t = table()
    try:
        t.onto(C, "Horta")
        t.lands(M, 6, "Mountain")
        for name in ("Plasma Cascade", "Transporter Malfunction"):
            t.hand(M, name)
            h = next(x for x in t.state(M)["hand"] if x["name"] == name)
            opts = (h.get("slots") or [{}])[0].get("options") or []
            check(any(o.get("player") == C for o in opts) and any(o.get("player") == M for o in opts) and any(o.get("ref") for o in opts),
                  f"{name} (any target): the list offers the opponent, yourself and the creatures", [o.get("label") for o in opts])
        ev0 = t.events(0)[-1]["id"]
        l0 = t.lifeof(C)
        c, d = t.act(M, "cast", name="Plasma Cascade", targets=[{"player": C}])
        lifes = [e for e in t.events(ev0) if e["type"] == "life" and e.get("player") == C]
        check(c == 200 and t.lifeof(C) == l0 - 3 and lifes and lifes[0].get("delta") == -3 and any("deals 3 to Claude" in x for x in says(t, ev0)),
              "cast at the opposing player: 3 off their life, as a life event, said in the log", (c, d.get("said") or d, t.lifeof(C), lifes))
        t.decline(M)                                       # ("You may discard a card": no)
        t.turn(C)                                          # an instant (Plasma Cascade) on the opponent's turn: the same list
        t.hand(M, "Plasma Cascade")
        h = next(x for x in t.state(M)["hand"] if x["name"] == "Plasma Cascade")
        opts = (h.get("slots") or [{}])[0].get("options") or []
        check(any(o.get("player") == C for o in opts), "…on the opponent's turn too (an instant): the opponent is on the list", [o.get("label") for o in opts])
        l0 = t.lifeof(C)
        c, d = t.act(M, "cast", name="Plasma Cascade", targets=[{"player": C}])
        check(c == 200 and t.lifeof(C) == l0 - 3, "…and cast there it hits them", (c, d.get("said") or d.get("error"), t.lifeof(C)))
    finally:
        t.stop()


def first_strike():
    t = table()
    try:
        cs1 = t.onto(M, "Cybernetic Specialist")           # 2/1
        cs2 = t.onto(M, "Cybernetic Specialist")
        t.turn(C)
        t.lands(C, 6, "Mountain")
        c, d = t.call("POST", "/api/brain/token", {"seat": C, "name": "Klingon Warrior", "power": 2, "toughness": 1, "keywords": ["First strike", "Haste"]})
        fs = next(p["id"] for p in t.state(C)["permanents"] if p["name"].startswith("Klingon"))
        t.hand(C, "Boarding Action")
        t.act(C, "cast", name="Boarding Action")           # a plain 2/1 hasty Klingon Warrior
        ba = next(p["id"] for p in t.state(C)["permanents"] if p["name"].startswith("Klingon") and p["id"] != fs)
        t.hand(C, "Captain's Tutelage")
        c, d = t.act(C, "cast", name="Captain's Tutelage", on=f"#{ba}")
        tok = next(p for p in t.state(C)["permanents"] if p["id"] == ba)
        aura = t.perm(C, "Captain's Tutelage")
        check(c == 200 and "First strike" in tok["keywords"] and tok["pt"] == "4/1" and "First strike" not in (aura or {}).get("keywords", []),
              "Captain's Tutelage: the ENCHANTED creature gains first strike until end of turn (not the Aura), and gets +2/+0", (c, d.get("said") or d, tok, aura))
        c, d = t.act(C, "attack", assign={f"#{fs}": M, f"#{ba}": M})
        check(c == 200, "the printed first striker and the enchanted token attack", (c, d.get("said") or d.get("error")))
        b1 = t.call("POST", "/api/brain/block", {"seat": M, "attacker": f"#{fs}", "blocker": f"#{cs1}"}, key=t.keys[M])
        b2 = t.call("POST", "/api/brain/block", {"seat": M, "attacker": f"#{ba}", "blocker": f"#{cs2}"}, key=t.keys[M])
        for _ in range(3):
            if t.call("GET", "/api/phase")[1].get("step") == "main 2":
                break
            t.act(C, "next")
        left = sorted(p["id"] for p in t.state(C)["permanents"] if p["name"].startswith("Klingon"))
        check(left == sorted([fs, ba]) and not t.perms(M, "Cybernetic Specialist"),
              "first strike, printed and granted: each 2/1 blocker dies in the first-strike step and never strikes back; both tokens live",
              (left, [p["id"] for p in t.perms(M, "Cybernetic Specialist")], b1, b2, says(t)[-6:]))
    finally:
        t.stop()


def tutelage_after_blocks():
    """The night (turn 4): Sam's 2/1 Klingon Warrior token attacks, Starfleet Security (1/1) blocks, THEN Sam casts Captain's Tutelage (flash) on
    the token. It is 4/1 with first strike: Security dies in the first-strike step and deals nothing back; the token lives."""
    t = table()
    try:
        ss = t.onto(M, "Starfleet Security")
        t.turn(C)
        t.lands(C, 4, "Mountain")
        t.call("POST", "/api/brain/token", {"seat": C, "name": "Klingon Warrior", "power": 2, "toughness": 1, "keywords": ["Haste"]})
        tok = next(p["id"] for p in t.state(C)["permanents"] if p["name"].startswith("Klingon"))
        t.call("POST", "/api/phase/windows", {"on": True, "by": "host"}, host=False)   # a window after blocks, as on the night
        c, d = act_until(t, C, "attack", assign={f"#{tok}": M})
        check(c == 200 and attackers(t, C) == [f"#{tok}"], "the token's attack is declared (through the windows, however long they take)",
              (c, d.get("error") or d.get("said"), t.call("GET", "/api/phase")[1].get("step")))
        c, d = t.call("POST", "/api/brain/block", {"seat": M, "attacker": f"#{tok}", "blocker": f"#{ss}"}, key=t.keys[M])
        ph = t.call("GET", "/api/phase")[1]
        check(c == 200 and ph.get("step") == "declare blockers" and t.perm(M, "Starfleet Security") and t.perm(C, "Klingon Warrior token"),
              "Starfleet Security blocks the token; nothing dealt yet (declare blockers, a window)", (c, d.get("said") or d, ph.get("step")))
        t.hand(C, "Captain's Tutelage")
        c, d = act_until(t, C, "cast", name="Captain's Tutelage", on=f"#{tok}")
        tk = next((p for p in t.state(C)["permanents"] if p["id"] == tok), None)
        check(c == 200 and tk and tk["pt"] == "4/1" and "First strike" in tk["keywords"]
              and not any("Captain's Tutelage gains" in x for x in d.get("said") or []),
              "Captain's Tutelage after blocks: the TOKEN is 4/1 with first strike (the log names the creature, not the Aura)", (c, d.get("said") or d, tk))
        for _ in range(60):
            ph = t.call("GET", "/api/phase")[1]
            if ph.get("step") in ("main 2", "end step") or ph.get("player") != C:
                break
            nxt = (ph.get("passes") or {}).get("next")
            if nxt == M:
                t.call("POST", "/api/brain/pass", {"seat": M, "player": ph.get("player"), "step": ph.get("step")}, key=t.keys[M])
            else:
                t.call("POST", "/api/brain/next", {"seat": C}, key=t.keys[C])
            time.sleep(0.3)
        ev = says(t)
        check(t.perm(M, "Starfleet Security") is None and t.perm(C, "Klingon Warrior token") is not None and not any("Starfleet Security deals" in x for x in ev[-10:]),
              "first-strike step: Security dies and never deals its 1 back; the token survives", (t.perm(C, "Klingon Warrior token"), ev[-8:]))
    finally:
        t.stop()


def bot_for(t, seat):
    """The rules autopilot (bots/claude_autopilot.py) on `seat`, in this process, through that seat's own key (as bot_v6_test does)."""
    import importlib
    import tempfile
    keyf = Path(tempfile.mkdtemp()) / "k"
    keyf.write_text(t.keys[seat])
    keyf.chmod(0o600)
    os.environ.update(CLAUDE_SEAT=seat, CLAUDE_KEY_FILE=str(keyf), TABLE_URL=t.b)
    if str(BOT) not in sys.path:
        sys.path.insert(0, str(BOT))
    ap = importlib.import_module("claude_autopilot")
    ap.BASE, ap.SEAT, ap.KEY = t.b, seat, t.keys[seat]
    ap.REFUSED.clear()
    return ap


def redeclare():
    t = table()
    try:
        t.call("POST", "/api/life", {"player": M, "delta": 60, "by": "test"}, host=False)   # (several turns of L'Rell's attacks ahead)
        lr = t.onto(C, "L'Rell, Fierce Visionary")
        kl = lambda: [p for p in t.state(C)["permanents"] if p["name"].startswith("Klingon")]
        # (1) the autopilot (the night's repro: its attack got the 409, it never declared again, and nothing attacked)
        t.turn(C)
        ap = bot_for(t, C)
        c, d = ap.declare_attack()
        tok = [p["id"] for p in kl()]
        check(c == 200 and len(tok) == 1 and attackers(t, C) == sorted([f"#{lr}", f"#{tok[0]}"]),
              "the autopilot: beginning of combat makes L'Rell's token, the table sends the attack back once, the bot reads the board again and "
              "declares L'Rell AND the token", (c, d.get("error") or d.get("said"), tok, attackers(t, C)))
        take_all(t)
        # (2) the old choice sent again after the 409 is taken: one 409 a turn, never a loop
        t.turn(M)
        t.turn(C)
        c1, d1 = t.act(C, "attack", assign={f"#{lr}": M})
        c2, d2 = t.act(C, "attack", assign={f"#{lr}": M})
        check(c1 == 409 and d1.get("redeclare") and c2 == 200 and attackers(t, C) == [f"#{lr}"],
              "a pilot sends the same choice after the 409: it is declared (L'Rell alone); the table asked once", (c1, c2, d2.get("error"), attackers(t, C)))
        take_all(t)
        # (3) a 409, then End without attacking: the end step still sacrifices the token
        t.turn(M)
        t.turn(C)
        c, d = t.act(C, "attack", assign={f"#{lr}": M})
        made = [p["id"] for p in kl()]
        check(c == 409 and d.get("redeclare") and len(made) == 1, "the 409 again (a new turn, a new token)", (c, d.get("error"), made))
        c, d = t.act(C, "end", text="no attack")
        t.turn(M, fresh=False)
        check(not kl(), "…End without declaring: the end step sacrifices the token all the same", (c, d.get("error"), kl()))
        # (4) the host's own attack (the host token, not a pilot's key) is never sent back
        t.turn(C)
        c, d = t.call("POST", "/api/brain/attack", {"seat": C, "assign": {f"#{lr}": M}})
        check(c == 200 and not d.get("redeclare") and attackers(t, C) == [f"#{lr}"], "a host-token attack from main 1: declared as sent, no 409",
              (c, d.get("error"), attackers(t, C)))
        take_all(t)
        # (5) NEXT into beginning of combat first: the token was there when the attack was chosen, so it is not sent back
        t.turn(M)
        t.turn(C)
        t.next_to(C, "beginning of combat")
        here = kl()
        c, d = t.act(C, "attack", assign={f"#{lr}": M})
        check(len(here) == 1 and c == 200 and not d.get("redeclare") and attackers(t, C) == [f"#{lr}"],
              "NEXT to beginning of combat (the token is made there, in sight), then attack with L'Rell alone: no 409", (len(here), c, d.get("error"), attackers(t, C)))
        take_all(t)
        # (6) the token can't attack (tapped): no 409 for a creature that could not have been declared anyway
        t.turn(M)
        t.turn(C)
        t.call("POST", "/api/phase/windows", {"on": True, "by": "host"}, host=False)   # the attack walks into beginning of combat and waits there
        c, d = 0, {}
        for _ in range(80):
            c, d = t.call("POST", "/api/brain/attack", {"seat": C, "assign": {f"#{lr}": M}}, key=t.keys[C])
            if kl() or c != 409 or "waiting on" not in str(d.get("error")):
                break
            ph = t.call("GET", "/api/phase")[1]
            t.call("POST", "/api/brain/pass", {"seat": M, "player": ph.get("player"), "step": ph.get("step")}, key=t.keys[M])
            time.sleep(0.25)
        new = kl()
        if new:
            t.call("POST", "/api/brain/tap", {"seat": C, "ref": f"#{new[0]['id']}"})
        c, d = act_until(t, C, "attack", assign={f"#{lr}": M})
        check(len(new) == 1 and c == 200 and not d.get("redeclare") and attackers(t, C) == [f"#{lr}"],
              "the token made by the walk into combat is tapped (it can't attack): the attack is declared, no 409", (len(new), c, d.get("error"), attackers(t, C)))
    finally:
        t.stop()


def jace_deck():
    """Michael's test deck plus a planeswalker (the Star Trek set has none): Jace Beleren, from the card data."""
    import json
    o = json.load(open(HERE / ".cache" / "oracle.json"))["Jace Beleren"]
    d = json.load(open(R.DECK_MICHAEL))
    d["mainBoard"].append({"name": "Jace Beleren", "count": 2, "manaCost": o["cost"], "manaValue": 3.0, "type": o["type"], "types": ["Planeswalker"],
                           "subtypes": ["Jace"], "supertypes": ["Legendary"], "text": o["text"], "power": None, "toughness": None, "loyalty": "3",
                           "colors": ["U"], "colorIdentity": ["U"]})
    f = Path(R.DECK_MICHAEL).with_name("Michael-1115-jace.json")
    f.write_text(json.dumps(d))
    return str(f)


def counter_kinds():
    keep = R.DECK_MICHAEL
    R.DECK_MICHAEL = jace_deck()
    try:
        t = table()
    finally:
        R.DECK_MICHAEL = keep
    try:
        js = t.onto(M, "Jace Beleren")
        sm = t.onto(M, "Support Mission")
        op = t.onto(M, "Tal Shiar Operative")
        jc = lambda: t.perm(M, "Jace Beleren")
        before = (jc() or {}).get("counters", 0)
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{js}", "n": 2})
        check(c == 200 and jc()["counters"] == before + 2 and any("loyalty counter" in x for x in d.get("said") or []),
              "a planeswalker, no kind named: two LOYALTY counters (said so)", (c, d, jc()))
        fields = {"+1/+1": "counters", "quest": "quest", "charge": "charge", "lore": "lore", "stun": "stun", "promotion": "promo"}
        for kind, field in fields.items():
            p0 = t.perm(M, "Support Mission")
            c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{sm}", "n": 1, "kind": kind})
            p1 = t.perm(M, "Support Mission")
            others = {f: (p0.get(f) or 0, p1.get(f) or 0) for f in set(fields.values()) if f != field}
            check(c == 200 and (p1.get(field) or 0) == (p0.get(field) or 0) + 1 and all(a == b for a, b in others.values()),
                  f"kind {kind}: one counter in `{field}`, every other count unchanged", (c, d.get("error"), field, p0.get(field), p1.get(field), others))
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{sm}", "n": 1, "kind": "banana"})
        check(c == 400 and "unknown counter kind" in str(d.get("error")), "an unknown kind is refused (not filed as a +1/+1)", (c, d))
        for n in (100, -100, 1000):
            c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{op}", "n": n})
            check(c == 400 and t.perm(M, "Tal Shiar Operative")["counters"] == 0, f"n={n}: refused (|n| > 99), nothing changes", (c, d.get("error")))
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{op}", "n": 99})
        check(c == 200 and t.perm(M, "Tal Shiar Operative")["counters"] == 99, "n=99 is the most at once", (c, d.get("error")))
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{op}", "n": -99})
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{op}", "n": -3})
        check(c == 200 and t.perm(M, "Tal Shiar Operative")["counters"] == 0 and t.perm(M, "Tal Shiar Operative")["pt"] == "2/2",
              "taking off more +1/+1 counters than it has: the count stops at 0 (not -3, never a 2/2 shrunk to -1/-1)", (c, d, t.perm(M, "Tal Shiar Operative")))
        q0 = t.perm(M, "Support Mission")["quest"]
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{sm}", "n": -(q0 + 5), "kind": "quest"})
        check(c == 200 and t.perm(M, "Support Mission")["quest"] == 0, "…and the same for a quest count", (c, d, t.perm(M, "Support Mission")))
    finally:
        t.stop()


def mana_sick_haste():
    t = table()
    try:
        xb = t.onto(M, "Xenobotanist")
        c, d = t.call("POST", "/api/brain/grant", {"seat": M, "ref": f"#{xb}", "keyword": "haste"})
        c, d = t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{xb}", "for_mana": True, "announce": True}, key=t.keys[M])
        check(c == 200 and t.perm(M, "Xenobotanist")["tapped"], "Xenobotanist the turn it arrives, with haste GRANTED: its {T} mana ability is allowed", (c, d))
        t.call("POST", "/api/brain/token", {"seat": M, "name": "Hasty Druid", "power": 1, "toughness": 1, "keywords": ["Haste"], "quiet": True})
        t.call("POST", "/api/brain/token", {"seat": M, "name": "Plain Druid", "power": 1, "toughness": 1, "quiet": True})
        perms = t.state(M)["permanents"]
        hd = next(p for p in perms if p["name"].startswith("Hasty Druid"))
        pd = next(p for p in perms if p["name"].startswith("Plain Druid"))
        ch, dh = t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{hd['id']}", "for_mana": True}, key=t.keys[M])
        cp, dp = t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{pd['id']}", "for_mana": True}, key=t.keys[M])
        check(ch == 200 and cp == 400 and "summoning sickness" in str(dp.get("error")),
              "two fresh creatures: the one with PRINTED haste taps for mana, the one without is refused", (ch, dh.get("error"), cp, dp.get("error")))
    finally:
        t.stop()


SECTIONS = [("1 Support Mission: quest counters and +1/+1 counters", support_mission),
            ("2 counter persists through the opponent's turn (guard)", counter_persists),
            ("3 L'Rell beginning-of-combat token (lrell)", lrell),
            ("4 summoning-sick mana ability refused (mana)", mana_sick),
            ("5 Tal Shiar Operative target, host grant, deathtouch (talshiar)", tal_shiar),
            ("7 burn at any target: players are offered and hit (anytarget)", any_target),
            ("8 first strike, printed and granted by an Aura (firststrike)", first_strike),
            ("9 Captain's Tutelage cast after blocks (tutelage)", tutelage_after_blocks),
            ("10 the redeclare 409: autopilot, one 409, host, NEXT, tapped token, End (redeclare)", redeclare),
            ("11 host counters by kind, bounds and floor (counterkinds)", counter_kinds),
            ("12 haste lets a sick creature tap for mana (manahaste)", mana_sick_haste)]

if __name__ == "__main__":
    only = os.environ.get("R1115_ONLY")
    for title, fn in SECTIONS:
        if only and not any(k.lower() in title.lower() for k in only.split("|")):
            continue
        print("\n" + title, flush=True)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one section that throws is one failure
            traceback.print_exc()
            check(False, f"{title}: raised {type(e).__name__}", e)
    print(("\nFAILED: " + "; ".join(R.FAILS)) if R.FAILS else "\nall good", flush=True)
    sys.exit(1 if R.FAILS else 0)
