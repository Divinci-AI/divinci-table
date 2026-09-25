"""Rules the engine (player.py) must get right, each from a position that came up in a real or
rehearsed game. No server, no model — pure arithmetic, runs in a second.

  ~/.venvs/table/bin/python table/tests/engine_rules.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from player import Perm, VirtualPlayer  # noqa: E402

DECK = HERE.parent.parent / "decks" / "ellivere.json"
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("  ✅ " if ok else "  ❌ ") + what)


def card(p: VirtualPlayer, name: str) -> dict:
    for c in p.library + p.hand + [p.commander]:
        if c["name"] == name:
            return c
    raise KeyError(name)


def fresh() -> VirtualPlayer:
    p = VirtualPlayer(str(DECK), "Claude", seed=1)
    p.library += p.hand
    p.hand = []
    return p


def put(p, name, **kw):
    x = Perm(card(p, name), sick=False, **kw)
    p.battlefield.append(x)
    return x


print("1. Mana Auras on lands (rehearsal 2026-09-25, turn 5)")
p = fresh()
f1, f2, pl = put(p, "Forest"), put(p, "Forest"), put(p, "Plains")
put(p, "Fertile Ground", attached_to=f1.id)
put(p, "Utopia Sprawl", attached_to=f2.id, chosen="W")
check(p.available_mana() == 5, f"two Forests with Fertile Ground + Utopia Sprawl, and a Plains: 5 mana (got {p.available_mana()})")
check(p.plan_payment("{2}{W}{W}") is not None, "…which pays Ajani's Chosen {2}{W}{W} (white from Plains and the Sprawl)")
check(p.plan_payment("{3}{G}{W}") is not None, "…and Syr Armont {3}{G}{W}")
check(p.plan_payment("{W}{W}{W}") is not None, "…and WWW (Plains + Sprawl's white + Fertile Ground's any colour)")
check(p.plan_payment("{W}{W}{W}{W}") is None, "…but not WWWW")
pay = p.plan_payment("{2}{W}{W}")
for x in pay:
    x.tapped = True
check(f2.tapped and pl.tapped, "paying taps the lands whose mana was used")

print("\n2. Utopia Sprawl's colour")
p = fresh()
f = put(p, "Forest")
p.hand.append(card(p, "Utopia Sprawl"))
p.library.remove(card(p, "Utopia Sprawl")) if card(p, "Utopia Sprawl") in p.library else None
p.manual_cast("Utopia Sprawl", on=f"#{f.id}", color="W")
spr = next(x for x in p.battlefield if x.name == "Utopia Sprawl")
check(spr.chosen == "W", f"--color W is remembered (got {spr.chosen})")
p2 = fresh()
f = put(p2, "Forest")
p2.hand.append(card(p2, "Utopia Sprawl"))
p2.manual_cast("Utopia Sprawl", on=f"#{f.id}")
spr = next(x for x in p2.battlefield if x.name == "Utopia Sprawl")
check(spr.chosen == "W", f"with no colour given, it names the colour its mana lacks — W for a G/W commander (got {spr.chosen})")

print("\n3. Anthems")
p = fresh()
sp = put(p, "Destiny Spinner")
el = put(p, "Ellivere of the Wild Court")
put(p, "Syr Armont, the Redeemer")
put(p, "Ethereal Armor", attached_to=sp.id)
before = p.stats(el)
check(p.stats(sp)[0] == 2 + 2 + 1, f"Destiny Spinner with Ethereal Armor (2 enchantments) + Syr Armont's +1/+1: 5 power (got {p.stats(sp)[0]})")
check(before == (4, 4), f"Ellivere, not enchanted, doesn't get Armont's bonus: 4/4 (got {before})")

print("\n4. Attack wording matches the Gemma turn")
p = fresh()
a = put(p, "Destiny Spinner")
b = put(p, "Ellivere of the Wild Court")
said = p.manual_attack({"Destiny Spinner": "Sam", "Ellivere": "Sam"})
check(any(s.startswith("I attack Sam with Destiny Spinner and Ellivere of the Wild Court, ") and s.endswith(" damage.") for s in said),
      f"'I attack Sam with A and B, N damage.' (got {said[-1]!r})")

print(f"\n{sum(results)}/{len(results)} engine checks passed")
sys.exit(0 if all(results) else 1)
