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
        c2, r2 = S.brain("Fusion", "block", blocker=f"#{guard['id']}", amount=0, trample=False, attacker="Ogre token")
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

        print("a seat with a deck cannot hand the table its own attack numbers")
        c, r = S.call("POST", "/api/declare/attack", {"by": "Claude", "key": S.claim("Claude"),
                                                      "attacks": [{"attacker": "X", "target": "Fusion", "power": 40}]}, key=S.claim("Claude"))
        check("a pilot's /api/declare/attack is refused (the engine attacks)", c == 409, (c, r))

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
