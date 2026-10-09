"""Activated abilities (dogfood run 2: Mirror Entity, Kessig Wolf Run and friends had no command). The engine checks the cost,
refuses what it cannot run BEFORE anything is paid, and runs the ones it understands. No server, no model.

  ~/.venvs/table/bin/python table/tests/activate_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from player import IllegalAction, Perm, VirtualPlayer  # noqa: E402

DECKS = HERE.parent.parent / "decks"
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("  ✅ " if ok else "  ❌ ") + what)


def fresh(deck):
    p = VirtualPlayer(str(DECKS / deck), "Test", seed=5)
    p.library += p.hand
    p.hand = []
    return p


def put(p, name, **kw):
    for c in p.library + [p.commander]:
        if c["name"] == name:
            x = Perm(c, sick=kw.pop("sick", False), **kw)
            p.battlefield.append(x)
            return x
    raise KeyError(name)


def refuses(fn, word):
    try:
        fn()
    except IllegalAction as e:
        return word in str(e), str(e)
    return False, "no refusal"


def lands(p, n, name="Mountain"):
    for _ in range(n):
        put(p, name)


# ── Kaust's deck: Mirror Entity (X), Wolf Run (+X/+0 on a target), Sakura-Tribe Elder (sacrifice, fetch) ──
k = fresh("kaust.json")
me = put(k, "Mirror Entity")
b1, b2 = put(k, "Akroma, Angel of Fury"), k.make_tokens("Bear", 2, 2) and k.battlefield[-1]
lands(k, 3, "Forest")
tapped0 = sum(p.tapped for p in k.battlefield)
r = refuses(lambda: k.activate("Mirror Entity", x=9), "can't pay")
check(r[0], f"Mirror Entity X=9 with three lands is refused and nothing is tapped ({r[1][:60]})")
check(sum(p.tapped for p in k.battlefield) == tapped0, "…no land was tapped by the refusal")
said = k.activate("Mirror Entity", x=3)
check(all(k.stats(c) == (3, 3) for c in k.battlefield if c.is_("Creature")), f"X=3: every creature is 3/3 until end of turn ({[k.stats(c) for c in k.battlefield if c.is_('Creature')]})")
check(sum(p.tapped for p in k.battlefield if p.name == "Forest") == 3, "…it cost exactly three mana")
k.clear_until_eot()
check(k.stats(b2) == (2, 2), "at the end of the turn the Bear is a 2/2 again")

k2 = fresh("kaust.json")
put(k2, "Kessig Wolf Run")
bear = (k2.make_tokens("Bear", 2, 2), k2.battlefield[-1])[1]
lands(k2, 4, "Mountain")
put(k2, "Forest")
r = refuses(lambda: k2.activate("Kessig Wolf Run", x=1), "needs a target")
check(r[0], "Kessig Wolf Run needs a target creature")
k2.activate("Kessig Wolf Run", x=2, target=bear)
check(k2.stats(bear) == (4, 2), f"Wolf Run X=2: the Bear is 4/2 until end of turn (got {k2.stats(bear)})")
check(next(p for p in k2.battlefield if p.name == "Kessig Wolf Run").tapped, "…and the Wolf Run tapped itself")
r = refuses(lambda: k2.activate("Kessig Wolf Run", x=0, target=bear), "tapped")
check(r[0], "…it cannot be used twice")

k3 = fresh("kaust.json")
elder = put(k3, "Sakura-Tribe Elder")
n_lib = len(k3.library)
r = refuses(lambda: k3.activate("Sakura-Tribe Elder"), "--pick")
check(r[0], "Sakura-Tribe Elder: you must name the land to fetch")
r = refuses(lambda: k3.activate("Sakura-Tribe Elder", pick=["Kessig Wolf Run"]), "can fetch")
check(r[0], "…a non-basic land is refused, and the Elder is still on the battlefield")
check(any(p.name == "Sakura-Tribe Elder" for p in k3.battlefield), "(the refused attempt did not sacrifice it)")
k3.activate("Sakura-Tribe Elder", pick=["Forest"])
check(not any(p.name == "Sakura-Tribe Elder" for p in k3.battlefield) and any(c["name"] == "Sakura-Tribe Elder" for c in k3.graveyard),
      "the Elder is sacrificed to the graveyard")
check(any(p.name == "Forest" and p.tapped for p in k3.battlefield), "…and a Forest enters tapped")

r = refuses(lambda: k3.activate("Mirror Entity"), "no permanent")
check(r[0], "an ability of a permanent you do not have is refused")
op = fresh("kaust.json")
put(op, "Ransom Note")
r = refuses(lambda: op.activate("Ransom Note"), "can't run this ability yet")
check(r[0] and "nothing was paid" in r[1], "an ability the table cannot run (a modal one) is refused with 'nothing was paid'")

# ── Aminatou's deck: a draw and a scry, and the tap/sacrifice costs ──
a = fresh("aminatou.json")
put(a, "Mind Stone")
lands(a, 1, "Island")
h0 = len(a.hand)
a.library.extend([a.library[0]] * 2)
a.activate("Mind Stone")
check(len(a.hand) == h0 + 1 and any(c["name"] == "Mind Stone" for c in a.graveyard), "Mind Stone {1},{T}, sacrifice: draw a card; the Mind Stone is in the graveyard (it did not pay for itself)")
cb = fresh("aminatou.json")
put(cb, "Crystal Ball")
lands(cb, 1, "Island")
top = cb.peek(2)
said = cb.activate("Crystal Ball", bottom=[top[0]])
check(cb.last_peek == top and cb.peek(1)[0] != top[0] or top[0] == top[1], f"Crystal Ball scry 2: it looked at {top}, and the one put on the bottom is gone from the top")
cb2 = fresh("aminatou.json")
put(cb2, "Crystal Ball")
lands(cb2, 1, "Island")
r = refuses(lambda: cb2.activate("Crystal Ball", bottom=["Not A Card"]), "not among")
check(r[0], "a card that is not among the top is refused")

# ── Inspirit's deck: a counter on a creature, with {T} and summoning sickness ──
i = fresh("inspirit.json")
hw = put(i, "Hangarback Walker", sick=True)
lands(i, 2, "Plains")
r = refuses(lambda: i.activate("Hangarback Walker"), "summoning")
check(r[0], "a {T} ability of a creature that just arrived waits (summoning sickness)")
hw.sick = False
i.activate("Hangarback Walker")
check(hw.counters == 1 and hw.tapped, "Hangarback Walker {1},{T}: a +1/+1 counter, and it tapped")

print(f"\n{sum(results)}/{len(results)} activated-ability checks passed")
sys.exit(0 if all(results) else 1)
