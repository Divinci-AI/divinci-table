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

print("\n5. What can't be targeted (Oracle text)")
import tablefacts  # noqa: E402
druid = {"name": "Paradise Druid", "text": "This creature has hexproof as long as it's untapped. (It can't be the target of spells or abilities your opponents control.)\n{T}: Add one mana of any color.", "keywords": []}
check(tablefacts.cant_be_targeted(druid, False, "destroy") == "Paradise Druid has hexproof — it can't be targeted.",
      "Doom Blade on an UNTAPPED Paradise Druid: hexproof")
check(tablefacts.cant_be_targeted(druid, True, "destroy") is None, "…a TAPPED Paradise Druid can be targeted")
check(tablefacts.cant_be_targeted({"name": "Darksteel Myr", "text": "Indestructible", "keywords": ["Indestructible"]}, False, "destroy")
      == "Darksteel Myr is indestructible — it stays.", "destroy on an indestructible creature: it stays")
check(tablefacts.cant_be_targeted({"name": "Darksteel Myr", "text": "Indestructible", "keywords": ["Indestructible"]}, False, "exile") is None,
      "…but exile still works on it")
check(tablefacts.cant_be_targeted({"name": "Serra Angel", "text": "Flying, vigilance", "keywords": ["Flying", "Vigilance"]}, False, "destroy") is None,
      "an ordinary creature can be targeted")

print("\n6. Ellivere's Role goes on ANOTHER creature (live game 2026-09-30, turn 3)")
p = fresh()
for land in ("Plains", "Plains", "Forest", "Forest"):
    put(p, land)
p.manual_cast("Ellivere of the Wild Court", commander=True)
ell = next(x for x in p.battlefield if x.name == "Ellivere of the Wild Court")
check(not any(x.role for x in p.battlefield), "alone, Ellivere gets no Role (no other creature to target)")
check(p.stats(ell)[:2] == (4, 4), f"Ellivere is 4/4, not boosted by her own trigger (got {p.stats(ell)[:2]})")
put(p, "Paradise Druid")
ell.sick = False
p.manual_attack({f"#{ell.id}": "Sam"})
druid = next(x for x in p.battlefield if x.name == "Paradise Druid")
check(any(x.role == "Virtuous" and x.attached_to == druid.id for x in p.battlefield),
      "attacking with another creature out, the Role goes on that creature")

def give(p, name):
    """Move a card from the library into the hand."""
    c = card(p, name)
    p.library.remove(c)
    p.hand.append(c)
    return c


def board(p, *names):
    return [put(p, n) for n in names]


def tokens(p, kind):
    return [x for x in p.battlefield if x.token and x.name == f"{kind} token"]


print("\n7. Enchantress triggers (hand-resolved in the live game 2026-09-30)")
p = fresh()
board(p, "Forest", "Forest", "Forest", "Tanglespan Lookout")
look = p.named("Tanglespan Lookout")[0]
give(p, "Ancestral Mask")
n0 = len(p.hand)
p.manual_cast("Ancestral Mask", on="Tanglespan Lookout")
check(len(p.recent_draws) == 1 and len(p.hand) == n0, "Tanglespan Lookout: an Aura entering draws a card")
p = fresh()
board(p, "Plains", "Plains", "Forest", "Forest", "Tanglespan Lookout")
p.manual_cast("Ellivere of the Wild Court", commander=True)
check(any(x.role == "Virtuous" for x in p.battlefield) and len(p.recent_draws) == 1,
      "Ellivere's Virtuous Role is an Aura, so Tanglespan draws for it too")
p = fresh()
board(p, "Plains", "Plains", "Plains", "Plains", "Plains")
give(p, "Archon of Sun's Grace")
p.manual_cast("Archon of Sun's Grace")
check(not tokens(p, "Pegasus"), "Archon itself entering makes NO Pegasus (its trigger is for other enchantments)")
give(p, "Ethereal Armor")
p.manual_cast("Ethereal Armor", on="Archon of Sun's Grace")
peg = tokens(p, "Pegasus")
check(len(peg) == 1 and "Flying" in peg[0].card["keywords"], "…an enchantment entering afterwards makes a 2/2 flying Pegasus")
p = fresh()
board(p, "Forest", "Forest", "Forest", "Forest")
give(p, "Eidolon of Blossoms")
p.manual_cast("Eidolon of Blossoms")
check(len(p.recent_draws) == 1, "Eidolon of Blossoms draws for itself entering")
p = fresh()
board(p, "Forest", "Forest", "Forest", "Enchantress's Presence", "Tanglespan Lookout")
give(p, "Ancestral Mask")
p.manual_cast("Ancestral Mask", on="Tanglespan Lookout")
check(len(p.recent_draws) == 2, f"Enchantress's Presence (cast) + Tanglespan (enters): 2 cards (got {len(p.recent_draws)})")
p = fresh()
board(p, "Forest", "Forest", "Forest", "Setessan Champion")
give(p, "Ancestral Mask")
p.manual_cast("Ancestral Mask", on="Setessan Champion")
champ = p.named("Setessan Champion")[0]
check(champ.counters == 1 and len(p.recent_draws) == 1, "Setessan Champion: a +1/+1 counter and a card")
p = fresh()
board(p, "Plains", "Siona, Captain of the Pyleas")
give(p, "Ethereal Armor")
p.manual_cast("Ethereal Armor", on="Siona")
check(len(tokens(p, "Soldier")) == 1, "Siona: an Aura attached to its creature makes a 1/1 Human Soldier")

print("\n8. Cost reductions apply on their own")
p = fresh()
board(p, "Forest", "Forest", "Forest", "Jukai Naturalist")
give(p, "Eidolon of Blossoms")
check(any(c["name"] == "Eidolon of Blossoms" for _, c, _, _ in p.castable()),
      "Jukai Naturalist: Eidolon of Blossoms {2}{G}{G} is castable from 3 lands")
p = fresh()
board(p, "Forest", "Forest", "Plains", "Plains", "Danitha Capashen, Paragon", "Transcendent Envoy")
give(p, "Pollenbright Wings")
check(any(c["name"] == "Pollenbright Wings" for _, c, _, _ in p.castable()),
      "Danitha + Transcendent Envoy: Pollenbright Wings {4}{G}{W} costs 4")
check(p.auto_discount(card(p, "Tanglespan Lookout")) == 0, "…and they don't discount a creature spell")

print("\n9. Power that scales")
p = fresh()
f = put(p, "Forest")
druid, _ = board(p, "Paradise Druid", "Jukai Naturalist")
put(p, "Fertile Ground", attached_to=f.id)
put(p, "Ancestral Mask", attached_to=druid.id)
p.public_board = ["Rhystic Study"]
check(p.stats(druid) == (8, 7), f"Ancestral Mask counts every OTHER enchantment, theirs too: Druid 8/7 (got {p.stats(druid)})")
p = fresh()
kor = put(p, "Kor Spiritdancer")
put(p, "Ethereal Armor", attached_to=kor.id)
put(p, "Bear Umbra", attached_to=kor.id)
check(p.stats(kor) == (8, 10), f"Kor Spiritdancer with Ethereal Armor + Bear Umbra: 8/10 (got {p.stats(kor)})")
p = fresh()
gn, dr = board(p, "Aura Gnarlid", "Paradise Druid")
put(p, "Ethereal Armor", attached_to=dr.id)
put(p, "Bear Umbra", attached_to=dr.id)
p.public_board = ["Rancor"]
check(p.stats(gn) == (5, 5), f"Aura Gnarlid counts every Aura on the battlefield: 5/5 (got {p.stats(gn)})")

print("\n10. Mana")
p = fresh()
w, j = board(p, "Sanctum Weaver", "Jukai Naturalist")
put(p, "Ethereal Armor", attached_to=j.id)
check(p.available_mana() == 3, f"Sanctum Weaver with 3 enchantments makes 3 (got {p.available_mana()})")
check(p.plan_payment("{1}{G}{G}") is not None and p.plan_payment("{W}{W}{W}") is not None, "…of one colour, any colour")
check(p.plan_payment("{G}{W}") is None, "…but not two different colours")
p = fresh()
put(p, "Sol Ring")
check(p.available_mana() == 2, "Sol Ring makes 2")

print("\n11. Combat damage to a player")
p = fresh()
ell, druid = board(p, "Ellivere of the Wild Court", "Paradise Druid")
p.battlefield.remove(ell)
ell = Perm(p.commander, sick=False)
p.battlefield.append(ell)
put(p, "Pollenbright Wings", attached_to=druid.id)
said, life = p.combat_damage({"Paradise Druid": ("Sam", None)})
check(life == {"Sam": -2}, f"enchanted Druid hits Sam for 2 (got {life})")
check(len(tokens(p, "Saproling")) == 2, "Pollenbright Wings: 2 Saprolings")
check(len(p.recent_draws) == 1, "Ellivere: an enchanted creature hit a player, draw a card")
p = fresh()
arch = put(p, "Archon of Sun's Grace")
said, life = p.combat_damage({"Archon of Sun's Grace": ("Michael", None)})
check(life == {"Michael": -3, "Claude": 3}, f"Archon's lifelink gains what it deals (got {life})")
p.make_token("Pegasus", 2, 2, ["Flying"])
said, life = p.combat_damage({"Pegasus token": ("Sam", None)})
check(life.get("Claude") == 2, "Archon gives Pegasus tokens lifelink")

print("\n12. Attack triggers")
p = fresh()
lands = board(p, "Forest", "Forest", "Plains")
druid = put(p, "Paradise Druid")
put(p, "Bear Umbra", attached_to=druid.id)
for x in lands:
    x.tapped = True
p.manual_attack({"Paradise Druid": "Sam"})
check(not any(x.tapped for x in lands), "Bear Umbra: attacking untaps all lands")
p = fresh()
ell = Perm(p.commander, sick=False)
p.battlefield.append(ell)
druid = put(p, "Paradise Druid")
put(p, "Giant Inheritance", attached_to=druid.id)
p.manual_attack({f"#{ell.id}": "Sam", "Paradise Druid": "Sam"})
check(any(x.role == "Virtuous" and x.attached_to == druid.id for x in p.battlefield)
      and any(x.role == "Monster" and x.attached_to == ell.id for x in p.battlefield),
      "Ellivere's Virtuous Role stays on the Druid; Giant Inheritance's Monster Role goes on Ellivere")

print("\n13. Upkeep")
p = fresh()
druid = put(p, "Paradise Druid")
put(p, "Verdant Embrace", attached_to=druid.id)
p.begin_turn()
check(len(tokens(p, "Saproling")) == 1, "Verdant Embrace: a Saproling on its upkeep")
check(any("EACH opponent's upkeep" in t for t in p.todo), "…and a to-do for every opponent's upkeep")

print(f"\n{sum(results)}/{len(results)} engine checks passed")
sys.exit(0 if all(results) else 1)
