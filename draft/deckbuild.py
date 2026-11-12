"""Build a 40-card, two-colour deck from a draft pool.

Game 5 retro (S5): the old builder ranked by rarity, then CHEAPEST first, so a deck got every cheap common and left out
three He's Dead, Jim, Horta, both Cha'DIch Investigators and Genesis Effect. Now:
  1. every card gets a quality score (removal first, then creatures by body / evasion / extra value, then the rest);
  2. the colour pair is the one whose best 23 playables score highest (dual lands in the pair add a little);
  3. the deck takes the removal, then ~15-17 creatures (threats) by score with a sane curve (enough two-drops,
     few seven-plus drops), then the best of what is left;
  4. 17 lands (16 when the curve is very low), dual lands in the pair counted among them, basics split by the
     coloured pips of the chosen spells.
The result: {"colours", "spells" (every main-deck card id, dual lands included), "basics", "lands", "duals", "size"}.
"""
from __future__ import annotations

import collections
import json
import re
from itertools import combinations
from pathlib import Path

BASIC = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
RARITY_RANK = {"mythic": 3, "rare": 2, "uncommon": 1, "common": 0}
RARITY_BONUS = {"mythic": 1.2, "rare": 0.8, "uncommon": 0.4, "common": 0.0}
DECK_SIZE = 40
LANDS = 17
LOW_CURVE_LANDS = 16
LOW_CURVE = 2.6            # average mana value of the spells at or under this: 16 lands
CREATURES = (15, 17)       # how many creatures / threats the deck wants
MAX_REMOVAL = 8
MAX_TOP_END = 3            # cards costing 7 or more
MIN_CHEAP_CREATURES = 4    # creatures costing 2 or less, when the pool has them

EVASION = ("flying", "trample", "menace", "lifelink", "deathtouch", "first strike", "double strike", "vigilance",
           "haste", "hexproof", "ward", "indestructible", "flash", "reach")

_N = r"(\d+|x|twice x)"
HARD_REMOVAL = re.compile(
    r"(destroy|exile) target (nonartifact |nonland |nonblack |tapped |attacking |blocking |attacking or blocking )?"
    r"(creature|permanent|nonland permanent|creature or spacecraft)(?! card)"
    r"|return target (creature|nonland permanent)( or spacecraft)? an opponent controls"
    r"|each player sacrifices a creature", re.I)
DAMAGE_REMOVAL = re.compile(rf"deals? {_N} damage to (target (attacking or blocking )?creature|any target)", re.I)
SHRINK_REMOVAL = re.compile(r"target creature( an opponent controls)? gets -(\d+)/-(\d+)", re.I)
FIGHT_REMOVAL = re.compile(r"\bfights?\b", re.I)
CONDITIONAL = re.compile(r"power \d+ or greater|tapped creature|attacking or blocking|mana value \d+ or less", re.I)


def _num(s) -> int:
    try:
        return int(str(s))
    except (TypeError, ValueError):
        return 0


def is_land(c: dict) -> bool:
    return "Land" in (c.get("type_line") or "")


def is_creature(c: dict) -> bool:
    return "Creature" in (c.get("type_line") or "")


def removal_score(c: dict) -> float:
    """How good a card is at killing an opponent's creature (0 when it can't). Mode cards and creatures with an
    enters trigger count: He's Dead, Jim and an ETB 'destroy target tapped creature' both score."""
    t = c.get("oracle_text") or ""
    if HARD_REMOVAL.search(t):
        return 3.0 if CONDITIONAL.search(t) else 4.0
    m = DAMAGE_REMOVAL.search(t)
    if m:
        n = m.group(1).lower()
        dmg = 4 if "x" in n else _num(n)
        return min(3.6, 0.9 * dmg) - (0.6 if CONDITIONAL.search(t) else 0)
    m = SHRINK_REMOVAL.search(t)
    if m:
        return min(3.5, 1.4 + 0.4 * _num(m.group(3)))
    if FIGHT_REMOVAL.search(t) and "target creature" in t.lower():
        return 2.5
    return 0.0


def creature_score(c: dict) -> float:
    """A body: size first (a 7/6 is worth far more than a 1/1), less a little per mana, plus evasion and extra bodies."""
    p, tg, mv = _num(c.get("power")), _num(c.get("toughness")), float(c.get("cmc") or 0)
    t = (c.get("oracle_text") or "").lower()
    s = 0.5 + 0.45 * (p + tg) - 0.2 * mv
    s += 0.3 * sum(1 for k in EVASION if re.search(rf"\b{k}\b", t))
    if re.search(r"create[^.]*creature token", t):
        s += 0.8                              # two bodies (Cha'DIch Investigator)
    if re.search(r"enters[^.]*draw|draw (a|two) cards?[^.]*\.$", t):
        s += 0.4
    return s


def score(c: dict) -> float:
    """A card's quality in a limited deck. Removal and good creatures first; cost only as a curve concern."""
    mv = float(c.get("cmc") or 0)
    t = (c.get("oracle_text") or "").lower()
    s = RARITY_BONUS.get(c.get("rarity"), 0.0)
    r = removal_score(c)
    if is_creature(c):
        s += creature_score(c) + 0.8 * r
    elif r:
        s += 2.0 + r
    else:
        s += 0.8 + (0.6 if "draw" in t else 0) + (0.5 if "token" in t else 0)
    if mv >= 7:
        s -= 0.8 + 0.3 * (mv - 7)             # castable in a 17-land deck, but late
    return s


def mana_pips(c: dict) -> collections.Counter:
    return collections.Counter(re.findall(r"\{([WUBRG])\}", c.get("mana_cost") or "")) or collections.Counter(c.get("colors") or [])


def is_dual(c: dict, pair: set) -> bool:
    ci = set(c.get("color_identity") or [])
    return is_land(c) and len(ci) == 2 and ci <= pair


def fits(c: dict, pair: set) -> bool:
    return not is_land(c) and set(c.get("colors") or []) <= pair


def pick_colours(pool: list[dict]) -> tuple[str, str]:
    """The pair whose best 23 playables score highest (ties: more playables). Dual lands in the pair add a little."""
    best, best_key = None, None
    for a, b in combinations("WUBRG", 2):
        pair = {a, b}
        play = sorted((score(c) for c in pool if fits(c, pair)), reverse=True)
        key = (sum(play[:DECK_SIZE - LANDS]) + 0.5 * sum(1 for c in pool if is_dual(c, pair)), len(play))
        if best_key is None or key > best_key:
            best, best_key = (a, b), key
    # put the colour with more pips first (cosmetic, and the old API returned "most depth first")
    a, b = best
    pa = sum(mana_pips(c)[a] for c in pool if fits(c, {a, b}))
    pb = sum(mana_pips(c)[b] for c in pool if fits(c, {a, b}))
    return (a, b) if pa >= pb else (b, a)


class _Copy(dict):
    """One copy of a card in the pool (a dict like the card, but its own object, so three copies are three picks)."""
    def __init__(self, card: dict):
        super().__init__(card)
        self.card = card


def choose_spells(cands: list[dict], n: int) -> list[dict]:
    """n spells from the candidates: removal, then creatures by score on a sane curve, then the best of the rest."""
    ranked = [_Copy(c) for c in sorted(cands, key=lambda c: (-score(c), float(c.get("cmc") or 0), c["name"]))]
    out: list = []
    used: set = set()                          # by copy: a pool lists one card's copies as the same dict, and each copy counts
    top_end = lambda: sum(1 for c in out if float(c.get("cmc") or 0) >= 7)
    ncre = lambda: sum(1 for x in out if is_creature(x))

    def take(c):
        if id(c) in used or len(out) >= n:
            return False
        if float(c.get("cmc") or 0) >= 7 and top_end() >= MAX_TOP_END:
            return False
        if sum(1 for x in out if x["name"] == c["name"]) >= 4:
            return False                       # four copies at most
        out.append(c)
        used.add(id(c))
        return True

    for c in [c for c in ranked if removal_score(c) and not is_creature(c)][:MAX_REMOVAL]:
        take(c)
    creatures = [c for c in ranked if is_creature(c)]
    room = max(0, n - len(out))
    want = min(CREATURES[1], max(CREATURES[0], room - 4), room)
    cheap = [c for c in creatures if float(c.get("cmc") or 0) <= 2][:MIN_CHEAP_CREATURES]
    for c in cheap:                            # a curve starts at two: the best few cheap bodies first
        take(c)
    for c in creatures:
        if ncre() >= want:
            break
        take(c)
    for c in ranked:                           # the rest: the best cards, creatures only up to the cap
        if not is_creature(c) or ncre() < CREATURES[1]:
            take(c)
    for c in ranked:                           # a thin pool: anything playable
        take(c)
    return [c.card for c in out]


def build(pool: list[dict]) -> dict:
    a, b = pick_colours(pool)
    pair = {a, b}
    duals = [c for c in pool if is_dual(c, pair)]
    cands = [c for c in pool if fits(c, pair)]
    spells = choose_spells(cands, DECK_SIZE - LANDS)
    lands = LANDS
    if spells and sum(float(c.get("cmc") or 0) for c in spells) / len(spells) <= LOW_CURVE:
        more = choose_spells(cands, DECK_SIZE - LOW_CURVE_LANDS)
        if len(more) == DECK_SIZE - LOW_CURVE_LANDS:
            spells, lands = more, LOW_CURVE_LANDS
    lands = DECK_SIZE - len(spells) if len(spells) < DECK_SIZE - lands else lands   # too few playables: pad, validate() says so
    duals = duals[:min(len(duals), max(0, lands - 6))]          # always room for basics of both colours
    nbasic = lands - len(duals)
    pips = collections.Counter()
    for c in spells:
        pips.update({k: v for k, v in mana_pips(c).items() if k in pair})
    if not pips:
        pips[a] = 1
    total = sum(pips.values())
    basics = {col: (max(1, round(nbasic * pips[col] / total)) if pips[col] else 0) for col in (a, b)}
    while sum(basics.values()) > nbasic:
        k = max(basics, key=lambda x: basics[x])
        basics[k] -= 1
    while sum(basics.values()) < nbasic:
        k = max(basics, key=lambda x: pips[x] / max(basics[x], 1))
        basics[k] += 1
    return {
        "colours": [a, b],
        "spells": [c["id"] for c in spells] + [c["id"] for c in duals],
        "basics": {BASIC[col]: n for col, n in basics.items() if n},
        "lands": len(duals) + sum(basics.values()),
        "duals": [c["id"] for c in duals],
        "size": len(spells) + len(duals) + sum(basics.values()),
    }


def validate(deck: dict) -> list[str]:
    problems = []
    if deck["size"] != DECK_SIZE:
        problems.append(f"deck has {deck['size']} cards, need {DECK_SIZE}")
    over = [i for i, n in collections.Counter(deck["spells"]).items() if n > 4]
    if over:
        problems.append(f"{len(over)} card(s) with more than four copies")
    n = deck.get("lands", sum(deck["basics"].values()))
    if n not in (LOW_CURVE_LANDS, LANDS):
        problems.append(f"{n} lands instead of 17 (16 for a low curve): the pool had too few playable cards in {'/'.join(deck['colours'])}")
    return problems


if __name__ == "__main__":
    import sys
    root = Path(__file__).resolve().parent.parent
    cards = {c["id"]: c for c in json.loads(__import__("draft").CARDS_JSON.read_text())}
    draft = json.loads(Path(sys.argv[1]).read_text())
    pool = [cards[i] for i in draft["all_pools"][0]]
    deck = build(pool)
    print(json.dumps(deck, indent=1))
    print("problems:", validate(deck) or "none")
