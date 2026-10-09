"""Power/toughness the engine reads from Auras and the card's own text (dogfood run 2, finding 2), and two triggers it used to
skip (Righteous Authority's extra draw, Kestia's attack draw). No server, no model.

  ~/.venvs/table/bin/python table/tests/pump_stats_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from player import Perm, VirtualPlayer  # noqa: E402

DECK = HERE.parent.parent / "decks" / "tuvasa.json"
results = []


def check(ok, what):
    results.append(bool(ok))
    print(("  ✅ " if ok else "  ❌ ") + what)


def fresh() -> VirtualPlayer:
    p = VirtualPlayer(str(DECK), "Sonnet", seed=3)
    p.library += p.hand
    p.hand = []
    return p


def card(p, name):
    for c in p.library + p.hand + [p.commander]:
        if c["name"] == name:
            return c
    raise KeyError(name)


def put(p, name, **kw):
    x = Perm(card(p, name), sick=False, **kw)
    p.battlefield.append(x)
    return x


p = fresh()
kestia = put(p, "Kestia, the Cultivator")
check(p.stats(kestia) == (4, 4), "Kestia alone is 4/4")
p.hand = [p.library.pop() for _ in range(3)]
aura = put(p, "Righteous Authority", attached_to=kestia.id)
check(p.stats(kestia) == (4 + 3, 4 + 3), f"Righteous Authority: +1/+1 per card in hand (3) -> 7/7 (got {p.stats(kestia)})")
p.hand = p.hand[:1]
check(p.stats(kestia) == (5, 5), f"…and it follows the hand: one card -> 5/5 (got {p.stats(kestia)})")

p2 = fresh()
tuv = put(p2, "Tuvasa the Sunlit")
check(p2.stats(tuv) == (1, 1), f"Tuvasa with no enchantments is 1/1 (got {p2.stats(tuv)})")
put(p2, "Righteous Authority", attached_to=tuv.id)
put(p2, "Kestia, the Cultivator")
n = len(p2.enchantments())
check(p2.stats(tuv)[0] >= 1 + n and n >= 2, f"Tuvasa gets +1/+1 per enchantment you control ({n} enchantments -> {p2.stats(tuv)})")

p3 = fresh()
c3 = put(p3, "Kestia, the Cultivator")
put(p3, "Righteous Authority", attached_to=c3.id)
h0 = len(p3.hand)
p3.library.extend([p3.library[-1]] * 3)
said, drew = p3.begin_turn()
check(len(p3.hand) == h0 + 2 and any("additional" in x for x in said), f"Righteous Authority: begin draws the normal card and one more (hand {h0} -> {len(p3.hand)})")
said, drew = p3.begin_turn(skip_draw=True)
check(len(p3.hand) == h0 + 3, "…the extra draw still happens on the play (the normal draw is what is skipped)")

p4 = fresh()
k4 = put(p4, "Kestia, the Cultivator")
put(p4, "Righteous Authority", attached_to=k4.id)
h = len(p4.hand)
said = p4.manual_attack({f"#{k4.id}": "Opus"})
check(len(p4.hand) == h + 1 and any("draw a card" in x for x in said), f"Kestia (enchanted) attacks: its trigger draws a card (hand {h} -> {len(p4.hand)})")
p5 = fresh()
put(p5, "Kestia, the Cultivator")                    # an Enchantment Creature itself: the trigger covers it
bear = p5.make_tokens("Bear", 2, 2)
bear = p5.battlefield[-1]; bear.sick = False
h = len(p5.hand)
p5.manual_attack({f"#{bear.id}": "Opus"})
check(len(p5.hand) == h, "a plain Bear nothing enchants does not draw Kestia's card when it attacks")

print(f"\n{sum(results)}/{len(results)} pump checks passed")
sys.exit(0 if all(results) else 1)
