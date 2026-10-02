"""Provably fair AI decks (fair.py): same inputs → same order; any change → a different order; a
published commit verifies after the game, and tampering doesn't."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import fair  # noqa: E402
from player import VirtualPlayer  # noqa: E402

DECK = Path(__file__).parent.parent.parent / "decks" / "ellivere.json"
ok = True


def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name)
    ok &= bool(cond)


names = [f"Card {i:02d}" for i in range(99)] + ["Forest"] * 10
parts = {"seat": "Claude", "local": "00" * 32, "word:Sam": "pineapple"}
s1 = fair.make_seed(parts)
check("same inputs, same seed", s1 == fair.make_seed(dict(parts)))
check("same seed, same order", fair.fair_order(sorted(names), s1) == fair.fair_order(sorted(names), s1))
s2 = fair.make_seed({**parts, "word:Sam": "pineapples"})
check("one player's word changed: a different order",
      fair.fair_order(sorted(names), s1) != fair.fair_order(sorted(names), s2))
perm = fair.fair_order(sorted(names), s1)
check("the order is a permutation", sorted(perm) == list(range(len(names))))

rec = fair.seal("Claude", names, {"Sam": "pineapple", "Michael": "zebra"})
check("a sealed record verifies", fair.verify(rec)[0])
check("the fingerprint is the commit's first 8 hex digits",
      rec["fingerprint"].replace(" ", "").lower() == rec["commit"][:8])
check("the public part has no secrets", set(fair.public(rec)) == {"seat", "commit", "fingerprint", "sealed_at", "inputs"}
      and "pineapple" not in str(fair.public(rec)))
bad = copy.deepcopy(rec)
j = next(i for i, n in enumerate(bad["library"]) if n != bad["library"][0])
bad["library"][0], bad["library"][j] = bad["library"][j], bad["library"][0]
check("swapping two different cards fails verification", not fair.verify(bad)[0])
bad = copy.deepcopy(rec)
bad["parts"]["word:Sam"] = "banana"
check("changing a revealed word fails verification", not fair.verify(bad)[0])
bad = copy.deepcopy(rec)
bad["commit"] = "0" * 64
check("a different commit fails verification", not fair.verify(bad)[0])

vp = VirtualPlayer(str(DECK), "Claude", fair_words={"Sam": "pineapple"})
check("the AI's library + hand is the sealed order",
      [c["name"] for c in vp.library] + [c["name"] for c in vp.hand][::-1] == vp.fair["library"])
check("the AI's sealed record verifies", fair.verify(vp.fair)[0])
vp2 = VirtualPlayer(str(DECK), "Claude")
check("without fair words the old shuffle still works", vp2.fair is None and len(vp2.hand) == 7)

print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
