"""Checks for the draft engine, deck builder, and drafter panel. Run: python3 test_draft.py"""
import json
import random
import sys

from agents import HeuristicDrafter, panel_pick
from deckbuild import build, validate
from draft import load_pool, make_pack, run_draft, CARDS_JSON
from pathlib import Path

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


pool = load_pool()
check("draft pool is the 306 main-set draft cards", len(pool) == 306, str(len(pool)))

rng = random.Random(7)
packs_ok = all(len(make_pack(pool, rng)) == 14 for _ in range(50))
check("every pack has 14 cards", packs_ok)

cards = {c["id"]: c for c in json.loads(CARDS_JSON.read_text())}
for seed in (1, 2, 3):
    res = run_draft(seed, bots_only=True)
    pools = res["all_pools"]
    check(f"seed {seed}: 8 seats", len(pools) == 8)
    check(f"seed {seed}: 42 picks per seat", all(len(p) == 42 for p in pools),
          str({len(p) for p in pools}))
    deck = build([cards[i] for i in pools[0]])
    problems = validate(deck)
    check(f"seed {seed}: seat 0 deck is legal", not problems, str(problems))

# panel: a drafter returning an out-of-range index must be ignored, not chosen
class Bad:
    name = "bad"
    def pick(self, pack, pool, context):
        return 999

class Good:
    name = "good"
    def pick(self, pack, pool, context):
        return 2

pk = pool[:14]
idx, how = panel_pick([Bad(), Good()], pk, [], {"seed": 1})
check("panel ignores out-of-range answers", idx == 2, f"got {idx} via {how}")
idx, how = panel_pick([Bad()], pk, [], {"seed": 1})
check("panel with only bad answers falls back to heuristic", how.startswith("fallback"), how)

print("\nfailures:", failures or "none")
sys.exit(1 if failures else 0)
