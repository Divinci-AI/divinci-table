"""Every game opens with the high roll (a person pressed START and it used to be their turn at once, no roll).

    ~/.venvs/table/bin/python table/tests/opening_ceremony_test.py

NEXT/START from a person or a virtual-deck seat now runs the high roll for who goes first: every player rolls, ties re-roll, the
winner takes the first turn and the order turns round the table from them, and the whole roll (every die, the randomness it came
from) is one `highroll` event in the game log. A second START cannot re-roll. TABLE_HIGHROLL=first (the other tests) skips it.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.pop("TABLE_HIGHROLL", None)                 # THIS test wants the real ceremony
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage1_guard_test as G  # noqa: E402
from stage1_guard_test import Server  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {str(detail)[:300]}"), flush=True)


os.environ.pop("TABLE_HIGHROLL", None)                 # (stage1_guard_test set the default at import)
os.environ["TABLE_HIGHROLL"] = "table"                 # no network in a test: the table's own randomness; the cloud default is quantum

with Server() as S:                                    # Claude (pilot), Fusion (pilot), Michael, Sam
    S.claim_all()
    ph0 = S.phase()
    check("before START nobody has the turn", ph0.get("player") is None, ph0.get("player"))
    c, d = S.next("Michael")
    ph = S.phase()
    hr = d.get("highroll") or {}
    check("a person's START rolls the high roll first", c == 200 and hr.get("winner") and hr.get("rolls"), (c, str(d)[:200]))
    names = set(hr.get("seating") or [])
    check("every seat at the table rolled", names == {"Claude", "Fusion", "Michael", "Sam"}, names)
    last_round = hr["rolls"][str(max(map(int, hr["rolls"])))]
    top = max(last_round.values())
    check("the winner rolled the highest die of the deciding round", last_round[hr["winner"]] == top, last_round)
    check("the winner has the first turn", ph.get("player") == hr["winner"], (ph.get("player"), hr.get("winner")))
    check("play goes round the table from the winner", ph.get("order", [])[0] == hr["winner"] and set(ph["order"]) == names, ph.get("order"))
    check("the roll names where its randomness came from", hr.get("source") and hr.get("entropy"), hr.get("source"))
    evs = S.call("GET", "/api/events?since=0")[1].get("events", [])
    hrs = [e for e in evs if e.get("type") == "highroll" and e.get("winner")]
    check("the roll is in the game log as one event with every die", len(hrs) == 1 and hrs[0].get("rolls") == hr["rolls"], len(hrs))
    c2, d2 = S.next("Sam")
    check("a second START does not roll again", S.phase().get("player") == hr["winner"] and len([e for e in S.call("GET", "/api/events?since=0")[1]["events"]
          if e.get("type") == "highroll" and e.get("winner")]) == 1, (c2, str(d2)[:100]))
    c3, d3 = S.call("POST", "/api/highroll/start", {"mode": "table", "sides": 20, "by": "Sam"}, key=S.claim("Sam"))
    check("…and the roll endpoint refuses to re-roll for a better result", c3 == 409, (c3, d3))

with Server(humans=False, order="Claude,Fusion") as S2:
    S2.claim_all()
    c, d = S2.next("Claude")
    hr = d.get("highroll") or {}
    check("a virtual-deck seat's START runs the same ceremony", c == 200 and hr.get("winner") in ("Claude", "Fusion") and S2.phase().get("player") == hr.get("winner"), (c, str(d)[:160]))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
