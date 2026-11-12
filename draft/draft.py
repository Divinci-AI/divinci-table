"""Star Trek (TRK) booster draft: 8 seats, 3 packs of 14, passing left then right then left.

Draft-pool cards are the main set's collector numbers 1-306 (the set's own "Draft Cards" range).
Pack slots are an ASSUMPTION (10 common, 3 uncommon, 1 rare-or-mythic, mythic 1 in 8); check the
real booster breakdown before relying on the odds.

Run a bots-only draft:   python3 draft.py --bots-only --seed 1
Play as a human seat 0:  python3 draft.py --seed 1
Outputs a pool JSON next to this file under drafts/.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
def _cards_json() -> Path:
    """The set's cards: $DRAFT_CARDS_JSON, else cards.json beside the repo's draft/ folder, else table/.cache/cards-trk.json
    (python3 table/fetch_data.py --set trk). Nothing found: stop with that command (exit 77 = skipped, for the tests)."""
    for c in (os.environ.get("DRAFT_CARDS_JSON"), ROOT / "cards.json", ROOT / "table" / ".cache" / "cards-trk.json"):
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    print("SKIP: needs the set's card data: python3 table/fetch_data.py --set trk (or DRAFT_CARDS_JSON=<file>)", file=sys.stderr)
    sys.exit(77)


CARDS_JSON = _cards_json()
OUT_DIR = Path(__file__).resolve().parent / "drafts"

SEATS = 8
PACKS = 3
PACK_SIZE = 14
RARITY_WEIGHT = {"common": 1.0, "uncommon": 1.6, "rare": 2.4, "mythic": 2.8}
COLORS = "WUBRG"


def load_pool() -> list[dict]:
    cards = json.loads(CARDS_JSON.read_text())
    return [c for c in cards if c["set"] == "trk" and c["collector_number"].isdigit()
            and int(c["collector_number"]) <= 306]


def make_pack(pool: list[dict], rng: random.Random) -> list[dict]:
    by = {r: [c for c in pool if c["rarity"] == r] for r in RARITY_WEIGHT}
    pack = rng.sample(by["common"], 10) + rng.sample(by["uncommon"], 3)
    top = "mythic" if rng.random() < 1 / 8 else "rare"
    pack += rng.sample(by[top], 1)
    rng.shuffle(pack)
    return pack


def color_affinity(pool_cards: list[dict]) -> dict[str, float]:
    aff = {c: 0.0 for c in COLORS}
    for c in pool_cards:
        for col in c["colors"]:
            aff[col] = aff.get(col, 0.0) + 1.0
    total = sum(aff.values()) or 1.0
    return {k: v / total for k, v in aff.items()}


def bot_pick(pack: list[dict], pool_cards: list[dict], rng: random.Random) -> int:
    """Heuristic bot: rarity, then lean toward colours already in the pool. Ties are random."""
    aff = color_affinity(pool_cards)
    best, best_score = 0, -1e9
    for i, c in enumerate(pack):
        score = RARITY_WEIGHT.get(c["rarity"], 1.0)
        if c["colors"]:
            score += 1.5 * max(aff.get(col, 0.0) for col in c["colors"])
        if "Land" in c["type_line"]:
            score -= 2.0
        score += rng.random() * 0.1
        if score > best_score:
            best, best_score = i, score
    return best


def show_pack(pack: list[dict]) -> None:
    for i, c in enumerate(pack, 1):
        cost = c["mana_cost"] or "-"
        pt = f" {c['power']}/{c['toughness']}" if c.get("power") is not None else ""
        text = c["oracle_text"].replace("\n", " | ")
        print(f"  {i:2}. {c['name']}  {cost}  [{c['rarity'][0].upper()}]  {c['type_line']}{pt}")
        if text:
            print(f"        {text[:160]}")


def human_pick(pack: list[dict], pool_cards: list[dict]) -> int:
    show_pack(pack)
    while True:
        raw = input("Pick a number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(pack):
            return int(raw) - 1
        print("  enter a number from the list")


def run_draft(seed: int, bots_only: bool) -> dict:
    rng = random.Random(seed)
    pool = load_pool()
    packs = [[make_pack(pool, rng) for _ in range(SEATS)] for _ in range(PACKS)]
    picks: list[list[dict]] = [[] for _ in range(SEATS)]
    for rnd in range(PACKS):
        current = packs[rnd]
        for pick_no in range(PACK_SIZE):
            direction = 1 if rnd % 2 == 0 else -1
            for seat in range(SEATS):
                pack = current[seat]
                if not pack:
                    continue
                if seat == 0 and not bots_only:
                    print(f"\n=== Pack {rnd + 1}, pick {pick_no + 1} ===")
                    idx = human_pick(pack, picks[0])
                else:
                    idx = bot_pick(pack, picks[seat], rng)
                picks[seat].append(pack.pop(idx))
            # pass packs
            current = [current[(s - direction) % SEATS] for s in range(SEATS)]
    result = {"seed": seed, "seat0_pool": picks[0],
              "all_pools": [[c["id"] for c in p] for p in picks]}
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"draft-seed{seed}-{'bots' if bots_only else 'human'}.json"
    out.write_text(json.dumps(result, indent=1))
    print(f"\nSaved {out}")
    print(f"Seat 0 picked {len(picks[0])} cards.")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--bots-only", action="store_true")
    args = ap.parse_args()
    run_draft(args.seed, args.bots_only)


if __name__ == "__main__":
    main()
