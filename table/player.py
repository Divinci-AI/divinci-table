"""A virtual AI player at a real table: its own shuffled deck, private hand, and virtual battlefield.

Code owns what is arithmetic — the library, the hand, one land per turn, mana from its lands, which
spells it can afford in its colours. Gemma (offline) owns what is judgment — which spell to cast,
which of its creatures an Aura/Role goes on, whom each creature attacks. Everything it does is
returned as announcements for the table to hear.

What it does NOT simulate: the rules text of its spells beyond its own board. Effects on YOUR cards
(destroy, exile, draw) are read out with the card's text and applied by the humans. It tracks its own
permanents, simple creature tokens, and the stat bonuses of Auras and Role tokens.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from itertools import count

_ids = count(1)
COLORS = "WUBRG"

# Role tokens (Wilds of Eldraine). Stat bonus for the enchanted creature; Virtuous is dynamic.
ROLES = {
    "Virtuous": "+1/+1 for each enchantment you control",
    "Monster": "+1/+1 and trample", "Royal": "+1/+1 and ward 1", "Sorcerer": "+1/+1; scry 1 when it attacks",
    "Wicked": "+1/+1", "Young Hero": "gets a +1/+1 counter when it attacks if toughness ≤ 3",
    "Cursed": "base power and toughness 1/1",
}


def parse_cost(mana_cost: str) -> tuple[int, dict]:
    """'{2}{G}{W}' → (2, {'G':1,'W':1}). X counts as 0; hybrid pays with either colour (first one)."""
    generic, pips = 0, {}
    for sym in re.findall(r"\{([^}]+)\}", mana_cost or ""):
        if sym.isdigit():
            generic += int(sym)
        elif sym in COLORS:
            pips[sym] = pips.get(sym, 0) + 1
        elif "/" in sym:
            c = sym.split("/")[0]
            if c in COLORS:
                pips[c] = pips.get(c, 0) + 1
            elif c.isdigit():
                generic += int(c)
        elif sym == "C":
            generic += 1
    return generic, pips


def produced_colors(card: dict) -> str:
    """Which colours a mana source can make, from its rules text and basic land types."""
    text, subs = card.get("text") or "", card.get("subtypes") or []
    out = set()
    for basic, c in (("Plains", "W"), ("Island", "U"), ("Swamp", "B"), ("Mountain", "R"), ("Forest", "G")):
        if basic in subs:
            out.add(c)
    if re.search(r"Add one mana of any colou?r|mana of any colou?r|any one colou?r", text):
        out |= set(COLORS)
    for part in re.findall(r"Add ([^.]+)\.", text):
        out |= set(re.findall(r"\{([WUBRG])\}", part))
        if "{C}" in part:
            out.add("C")
    return "".join(sorted(out))


@dataclass
class Perm:
    card: dict
    tapped: bool = False
    sick: bool = True
    token: bool = False
    attached_to: int | None = None          # auras / roles: the id of the creature they enchant
    role: str | None = None
    counters: int = 0
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def name(self): return self.card["name"]

    @property
    def types(self): return self.card.get("types") or []

    def is_(self, t): return t in self.types


class VirtualPlayer:
    def __init__(self, deck_file: str, name: str, seed: int | None = None):
        d = json.load(open(deck_file))
        self.deck_name = d["name"]
        self.commander = d["commander"][0]
        self.name = name
        self.rng = random.Random(seed)
        self.library = [c for c in d["mainBoard"] for _ in range(c.get("count", 1))]
        self.rng.shuffle(self.library)
        self.hand: list[dict] = []
        self.graveyard: list[dict] = []
        self.battlefield: list[Perm] = []
        self.cmdr_in_zone, self.cmdr_casts = True, 0
        self.life, self.turn, self.land_played = 40, 0, False
        self.log: list[str] = []
        self.public_board: list[str] = []         # other players' announced cards (set each turn)
        self.on_their_cards: list[str] = []       # its hostile Auras, sitting on opponents' permanents
        self.draw(7)

    # ── zones ─────────────────────────────────────────────────────────────────────────────
    def draw(self, n=1):
        for _ in range(n):
            if self.library:
                self.hand.append(self.library.pop())

    def creatures(self): return [p for p in self.battlefield if p.is_("Creature")]

    def enchantments(self): return [p for p in self.battlefield if p.is_("Enchantment")]

    def stats(self, p: Perm) -> tuple[int, int]:
        """Current power/toughness with counters, Auras and Roles attached to it."""
        try:
            pw, tg = int(p.card.get("power") or 0), int(p.card.get("toughness") or 0)
        except ValueError:                       # '*' power: count as 0 rather than guess
            pw, tg = 0, 0
        pw += p.counters
        tg += p.counters
        for a in self.battlefield:
            if a.attached_to != p.id:
                continue
            if a.role == "Cursed":
                pw, tg = 1, 1
            elif a.role == "Virtuous":
                n = len(self.enchantments())
                pw, tg = pw + n, tg + n
            elif a.role:
                pw, tg = pw + 1, tg + 1
            else:
                m = re.search(r"Enchanted creature gets \+(\d+)/\+(\d+)", a.card.get("text") or "")
                if m:
                    pw, tg = pw + int(m.group(1)), tg + int(m.group(2))
        return pw, tg

    def describe(self, p: Perm) -> str:
        if p.is_("Creature"):
            pw, tg = self.stats(p)
            extra = [a.role + " Role" if a.role else a.name for a in self.battlefield if a.attached_to == p.id]
            return f"{p.name} {pw}/{tg}" + (f" (with {', '.join(extra)})" if extra else "") + (" [tapped]" if p.tapped else "")
        return p.name + (" [tapped]" if p.tapped else "")

    # ── mana ──────────────────────────────────────────────────────────────────────────────
    def sources(self):
        out = []
        for p in self.battlefield:
            if p.tapped or p.attached_to is not None:
                continue
            text = p.card.get("text") or ""
            if p.is_("Land") or (("{T}: Add" in text or "{T}: add" in text) and not (p.is_("Creature") and p.sick)):
                cols = produced_colors(p.card)
                if cols:
                    out.append((p, cols))
        return out

    def plan_payment(self, mana_cost: str, extra_generic=0):
        """A set of sources that pays the cost, or None. Coloured pips first from the sources that
        make the FEWEST colours (keep flexible lands for later), then generic from the rest."""
        generic, pips = parse_cost(mana_cost)
        generic += extra_generic
        pool = sorted(self.sources(), key=lambda s: len(s[1]))
        used = []
        for color, n in pips.items():
            for _ in range(n):
                s = next((s for s in pool if color in s[1]), None)
                if not s:
                    return None
                pool.remove(s)
                used.append(s[0])
        if len(pool) < generic:
            return None
        used += [s[0] for s in pool[:generic]]
        return used

    def available_mana(self): return len(self.sources())

    # ── options ───────────────────────────────────────────────────────────────────────────
    def castable(self):
        """(label, card, payment, is_commander) for every spell it can afford right now. Instants
        are included (cast on its own turn). Auras need a creature of its own to enchant."""
        out = []
        seen = set()
        for c in self.hand:
            if "Land" in c["types"] or c["name"] in seen:
                continue
            seen.add(c["name"])
            if is_hostile_aura(c):
                if not self.public_board:
                    continue      # nothing of theirs announced to put it on
            elif "Aura" in (c.get("subtypes") or []) and not self.aura_targets(c):
                continue
            if is_board_wipe(c) and self.creatures():
                continue          # never wipe its own board: offered only when it has no creatures
            pay = self.plan_payment(c.get("manaCost", ""))
            if pay is not None:
                out.append((f"cast {c['name']}", c, pay, False))
        if self.cmdr_in_zone:
            pay = self.plan_payment(self.commander["manaCost"], 2 * self.cmdr_casts)
            if pay is not None:
                out.append((f"cast your commander {self.commander['name']}", self.commander, pay, True))
        return out

    def aura_targets(self, c):
        """Its own permanents this Aura can enchant, from the card's own 'Enchant …' line."""
        m = re.search(r"^Enchant ([^\n(]+)", c.get("text") or "", re.M)
        what = (m.group(1).strip().lower() if m else "creature")
        if what.startswith("creature"):
            return self.creatures()
        if what in ("land", "forest", "plains", "island", "swamp", "mountain"):
            return [p for p in self.battlefield if p.is_("Land") and
                    (what == "land" or what.capitalize() in (p.card.get("subtypes") or []))]
        if what.startswith("permanent") or what.startswith("artifact") or what.startswith("enchantment"):
            kind = what.split()[0].capitalize()
            return [p for p in self.battlefield if kind == "Permanent" or p.is_(kind)]
        return []                 # enchant player / opponent etc.: not modelled, so not cast

    def best_land(self):
        """Code picks the land: one that adds a colour it lacks, else any untapped-entering one."""
        lands = [c for c in self.hand if "Land" in c["types"]]
        if not lands:
            return None
        have = set("".join(produced_colors(p.card) for p in self.battlefield if p.is_("Land")))

        def score(c):
            cols = set(produced_colors(c))
            enters_tapped = "enters tapped" in (c.get("text") or "") or "enters the battlefield tapped" in (c.get("text") or "")
            return (len(cols - have), -enters_tapped, len(cols))
        return max(lands, key=score)

    # ── actions (each returns the sentence the table hears) ──────────────────────────────
    def play_land(self, c):
        self.hand.remove(c)
        text = c.get("text") or ""
        p = Perm(c, sick=False, tapped=("enters tapped" in text or "enters the battlefield tapped" in text))
        self.battlefield.append(p)
        self.land_played = True
        return f"I play {c['name']}" + (", tapped." if p.tapped else ".")

    def cast(self, label, c, pay, is_cmdr, choose_target, choose_modes=None):
        for p in pay:
            p.tapped = True
        if is_cmdr:
            self.cmdr_in_zone = False
            self.cmdr_casts += 1
        else:
            self.hand.remove(c)
        said = [f"I cast {c['name']}" + (" from the command zone." if is_cmdr else ".")]
        types = c.get("types") or []
        if "Instant" in types or "Sorcery" in types:
            self.graveyard.append(c)
            modes = modal_options(c)
            if modes and choose_modes:
                picked = choose_modes(c, modes)
                said.append("I choose: " + " — and — ".join(picked) + ".")
            else:
                said.append(_short_text(c))                      # the table resolves its effect
        else:
            p = Perm(c, sick="Haste" not in (c.get("keywords") or []))
            if is_hostile_aura(c):
                target = choose_target(c, None)          # None: pick among the OTHER players' cards
                self.on_their_cards.append(f"{c['name']} on {target}")
                return " ".join(said + [f"It enchants {target}.", _short_text(c)])
            if "Aura" in (c.get("subtypes") or []):
                target = choose_target(c, self.aura_targets(c))
                p.attached_to = target.id
                said.append(f"It enchants {target.name}.")
            self.battlefield.append(p)
            said += self.enter_effects(p, choose_target)
        return " ".join(said)

    def enter_effects(self, p, choose_target):
        """The parts of 'when this enters' that change ITS OWN board: tokens and Roles."""
        text = p.card.get("text") or ""
        said = []
        etb = re.search(r"When(?:ever)? [^.]*? enters(?: the battlefield)?[^,]*, ([^.]+)\.", text)
        clause = etb.group(1) if etb else (text if "Sorcery" in p.types else "")
        for m in re.finditer(r"create (a|two|three) (\d+)/(\d+) ([\w ,]+?) creature tokens?", clause):
            n = {"a": 1, "two": 2, "three": 3}[m.group(1)]
            for _ in range(n):
                tok = {"name": f"{m.group(4).split(' ')[-1]} token", "types": ["Creature"], "power": m.group(2),
                       "toughness": m.group(3), "text": "", "keywords": []}
                self.battlefield.append(Perm(tok, token=True))
            said.append(f"I create {m.group(1)} {m.group(2)}/{m.group(3)} {m.group(4)} token{'s' if n > 1 else ''}.")
        m = re.search(r"create an? (\w+(?: \w+)?) Role token attached to (?:up to one |another )?target creature", clause)
        if m and m.group(1) in ROLES:
            cands = [c for c in self.creatures() if c.id != p.id] or ([p] if p.is_("Creature") else [])
            if cands:
                target = choose_target({"name": f"{m.group(1)} Role", "text": ROLES[m.group(1)]}, cands)
                for old in [a for a in self.battlefield if a.attached_to == target.id and a.role]:
                    self.battlefield.remove(old)             # a creature keeps only one of your Roles
                role = {"name": f"{m.group(1)} Role", "types": ["Enchantment"], "subtypes": ["Aura", "Role"], "text": ""}
                self.battlefield.append(Perm(role, token=True, attached_to=target.id, role=m.group(1)))
                said.append(f"{target.name} gets a {m.group(1)} Role.")
        return said

    # ── public / private views ────────────────────────────────────────────────────────────
    def public(self):
        return {"name": self.name, "commander": self.commander["name"], "life": self.life, "turn": self.turn,
                "hand": len(self.hand), "library": len(self.library), "commander_in_zone": self.cmdr_in_zone,
                "commander_tax": 2 * self.cmdr_casts,
                "lands": sum(1 for p in self.battlefield if p.is_("Land")),
                "untapped_mana": self.available_mana(),
                "battlefield": [self.describe(p) for p in self.battlefield
                                if not p.is_("Land") and p.attached_to is None],
                "graveyard": [c["name"] for c in self.graveyard[-8:]],
                "on_their_cards": self.on_their_cards[-6:]}

    def private_hand(self):
        return [c["name"] for c in self.hand]


def is_hostile_aura(c) -> bool:
    """An Aura meant for an OPPONENT's creature (Kenrith's Transformation and friends)."""
    if "Aura" not in (c.get("subtypes") or []):
        return False
    t = (c.get("text") or "").lower()
    return bool(re.search(r"loses all abilities|is an? [\w ]*\d+/\d+|can't attack|can't block|doesn't untap", t))


def is_board_wipe(c) -> bool:
    t = (c.get("text") or "").lower()
    return bool(re.search(r"(destroy|exile) all (creatures|nonland permanents|permanents)|"
                          r"destroy all creatures with|deals? \d+ damage to each creature|each player sacrifices", t))


def modal_options(c) -> tuple[int, list[str]] | None:
    """('Choose two —', ['Destroy all artifacts.', …]) → (2, modes), or None."""
    t = c.get("text") or ""
    m = re.search(r"Choose (one|two|three)[^—]*—", t)
    if not m:
        return None
    modes = [x.strip() for x in re.findall(r"•\s*([^•\n]+)", t)]
    return ({"one": 1, "two": 2, "three": 3}[m.group(1)], modes) if modes else None


def _short_text(c):
    t = re.sub(r"\([^)]*\)", "", c.get("text") or "").replace("\n", " ").strip()
    return (t[:180] + "…") if len(t) > 180 else t
