"""S5 (game 5 retro): the auto-deck keeps its removal and its best creatures, not just the cheapest cards.
Run: python3 test_deckbuild_quality.py"""
import collections
import sys

from deckbuild import build, validate, removal_score
from draft import load_pool

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


BY = {c["name"]: c for c in load_pool()}

# Claude's game-5 shape: black/red, three He's Dead, Jim, Horta, two Cha'DIch Investigators, Genesis Effect, plus far more cheap
# commons than a deck needs (the old builder took those, cheapest first) and an off-colour splash it should leave out.
POOL = (["He's Dead, Jim"] * 3 + ["Genesis Effect", "Plasma Cascade", "Transporter Malfunction", "Resistance Is Futile"]
        + ["Horta", "Cha'DIch Investigator", "Cha'DIch Investigator", "Targ Wrangler", "Captain Michael Burnham", "The Duras Sisters"]
        + ["Relentless Drednok"] * 3 + ["Starfleet Security"] * 3 + ["Battle-Scarred Survivalist"] * 3 + ["Tenacious Drone"] * 2
        + ["Savvy Profiteer"] * 2 + ["Tal Shiar Operative"] * 2 + ["Planetary Patrol"] * 2 + ["Fugitive Listener"] * 2
        + ["Storied Veteran", "Cantankerous Captain", "Tal Shiar Analyst", "Munitions Enthusiast", "Automated Warfare System"]
        + ["Command Decision", "Set Phasers to . . .", "Energize"])
missing = [n for n in POOL if n not in BY]
check("every test card is in the draft set", not missing, str(missing))
pool = [BY[n] for n in POOL if n in BY]
deck = build(pool)
names = collections.Counter(BY_ID[i]["name"] for i in deck["spells"]) if (BY_ID := {c["id"]: c for c in pool}) else {}

check("the deck is legal", not validate(deck), str(validate(deck)))
check("black/red", set(deck["colours"]) == {"B", "R"}, str(deck["colours"]))
check("all three He's Dead, Jim make the deck", names["He's Dead, Jim"] == 3, str(names))
for n in ("Genesis Effect", "Plasma Cascade", "Transporter Malfunction", "Resistance Is Futile"):
    check(f"removal {n} makes the deck", names[n] >= 1, str(names))
check("Horta (7/6 trample) makes the deck", names["Horta"] == 1, str(names))
check("both Cha'DIch Investigators make the deck", names["Cha'DIch Investigator"] == 2, str(names))
check("Targ Wrangler makes the deck", names["Targ Wrangler"] == 1, str(names))
creatures = sum(n for k, n in names.items() if "Creature" in BY[k]["type_line"])
check("15-17 creatures", 15 <= creatures <= 17, str(creatures))
check("no off-colour card", all(set(BY[k]["colors"]) <= {"B", "R"} for k in names), str(names))
cheap = sum(n for k, n in names.items() if "Creature" in BY[k]["type_line"] and BY[k]["cmc"] <= 2)
check("a curve: at least four creatures costing two or less", cheap >= 4, str(cheap))
check("the 1/1 for one is what gets cut, not the removal", names["Relentless Drednok"] < 3, str(names))
lands = deck["lands"]
check("16 or 17 lands, split over both colours", lands in (16, 17) and set(deck["basics"]) == {"Swamp", "Mountain"}, str(deck))
check("removal is recognised", all(removal_score(BY[n]) > 0 for n in ("He's Dead, Jim", "Genesis Effect", "Plasma Cascade")))
check("a graveyard exile is not removal", removal_score(BY["Test of Courage"]) == 0 if "Test of Courage" in BY else True)

# a dual land in the pair is counted among the lands
dual = next((c for c in load_pool() if "Land" in c["type_line"] and set(c.get("color_identity") or []) == {"B", "R"}), None)
if dual:
    d2 = build(pool + [dual])
    check("a B/R dual land goes in, counted among the lands", dual["id"] in d2["duals"] and d2["lands"] in (16, 17) and not validate(d2),
          str(d2))
else:
    print("SKIP no B/R dual land in the draft set")

print("\nfailures:", fails or "none")
sys.exit(1 if fails else 0)
