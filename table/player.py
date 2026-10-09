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


_PT_SUBJECT = re.compile(
    r"(This creature and enchanted creature each get|This creature gets|Enchanted creature gets|"
    r"enchanted permanent is a creature, it gets)\s+([^.\"]+)")


def pt_bonuses(text: str) -> list[tuple[set, int, int, str | None]]:
    """Power/toughness bonuses in a card's text: (who, +P, +T, 'for each …' phrase or None).
    who is {'self'} (This creature), {'enchanted'} (an Aura's creature) or both (Eidolon of
    Countless Battles). Quoted granted abilities are skipped, so Giant Inheritance's "Whenever this
    creature attacks" isn't read as a bonus."""
    out = []
    for m in _PT_SUBJECT.finditer(re.sub(r"\"[^\"]*\"", "", text)):
        subj = m.group(1).lower()
        who = ({"self", "enchanted"} if "and enchanted" in subj else {"self"} if subj.startswith("this")
               else {"enchanted"})
        for b in re.finditer(r"\+(\d+)/\+(\d+)(?: for each ((?:(?! and \+|, |\.).)+))?", m.group(2)):
            out.append((who, int(b.group(1)), int(b.group(2)), b.group(3)))
    return out


def face_cost(card: dict) -> tuple[str, str] | None:
    """('Megamorph', '{2}{W}') from "Megamorph {2}{W}", or None. Disguise and morph cards can be cast
    face down for {3}; the cost here is what turning it face up costs."""
    m = re.search(r"\b(Disguise|Megamorph|Morph) ((?:\{[^}]+\})+)", card.get("text") or "")
    return (m.group(1), m.group(2)) if m else None


def mana_amount(card: dict) -> int:
    """How many mana one tap makes: Sol Ring's {C}{C}, a '{T}: Add {G}{G}' creature."""
    m = re.search(r"\{T\}: Add ((?:\{[WUBRGC]\})+)", card.get("text") or "")
    return len(re.findall(r"\{", m.group(1))) if m else 1


@dataclass
class Perm:
    card: dict
    tapped: bool = False
    sick: bool = True
    token: bool = False
    attached_to: int | None = None          # auras / roles: the id of the creature they enchant
    role: str | None = None
    face_down: bool = False                 # disguise / morph / manifest / cloak: a nameless 2/2
    ward2: bool = False                     # disguised or cloaked: ward {2} while face down
    how: str | None = None                  # how it went face down — public: disguise, morph, manifest, cloak
    turned_up_turn: int | None = None       # Kaust: "turned face up this turn"
    counters: int = 0
    chosen: str | None = None               # "As this enters, choose a color" (Utopia Sprawl)
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def name(self): return self.card["name"]

    @property
    def types(self): return self.card.get("types") or []

    def is_(self, t): return t in self.types


class VirtualPlayer:
    def __init__(self, deck_file: str, name: str, seed: int | None = None, fair_words: dict | None = None,
                 fair_online: bool = False):
        """fair_words: seal the shuffle (fair.py): the library order is fixed by a published commit, from
        local entropy plus these {player: secret word} inputs (and ANU/drand with fair_online)."""
        d = json.load(open(deck_file))
        self.deck_name = d["name"]
        self.commander = d["commander"][0]
        self.name = name
        self.rng = random.Random(seed)
        self.library = [c for c in d["mainBoard"] for _ in range(c.get("count", 1))]
        self.fair = None
        if fair_words is not None:
            import fair
            rec = fair.seal(name, [c["name"] for c in self.library], fair_words, fair_online)
            pool: dict[str, list] = {}
            for c in self.library:
                pool.setdefault(c["name"], []).append(c)
            self.library = [pool[n].pop() for n in rec["library"]]
            self.rng = random.Random(int(rec["seed"], 16))     # later shuffles follow from the same seed
            self.fair = rec
        else:
            self.rng.shuffle(self.library)
        self.hand: list[dict] = []
        self.graveyard: list[dict] = []
        self.battlefield: list[Perm] = []
        self.cmdr_in_zone, self.cmdr_casts = True, 0
        self.life, self.turn, self.land_played = 40, 0, False
        self.log: list[str] = []
        self.public_board: list[str] = []         # other players' announced cards (set each turn)
        self.on_their_cards: list[str] = []       # its hostile Auras, sitting on opponents' permanents
        self.last_cast: dict | None = None           # for "in response, I counter that"
        self.recent_draws: list[str] = []            # names drawn since the server last asked (private)
        self.todo: list[str] = []                    # triggers the engine can't resolve: the brain does them
        self.draw(7)
        self.recent_draws.clear()

    # ── zones ─────────────────────────────────────────────────────────────────────────────
    def draw(self, n=1):
        for _ in range(n):
            if self.library:
                self.hand.append(self.library.pop())
                self.recent_draws.append(self.hand[-1]["name"])

    def named(self, name):
        """Its permanents with this card name (each copy triggers separately)."""
        return [p for p in self.battlefield if p.name == name]

    def is_aura(self, p): return "Aura" in (p.card.get("subtypes") or [])

    def opponents_cards(self, kind=None):
        """Card dicts for the other players' announced permanents (public_board names), optionally of a type."""
        try:
            import oracle
        except ImportError:
            return []
        out = []
        for n in self.public_board:
            c = oracle.card(n)
            if c and (kind is None or kind in (c.get("type") or "")):
                out.append(c)
        return out

    def creatures(self): return [p for p in self.battlefield if p.is_("Creature")]

    def enchantments(self): return [p for p in self.battlefield if p.is_("Enchantment")]

    def stats(self, p: Perm) -> tuple[int, int]:
        """Current power/toughness with counters, Auras and Roles attached to it."""
        try:
            pw, tg = int(p.card.get("power") or 0), int(p.card.get("toughness") or 0)
        except ValueError:                       # '*' power: count as 0 rather than guess
            pw, tg = 0, 0
        if p.face_down:
            pw, tg = 2, 2
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
                for who, bp, bt, each in pt_bonuses(a.card.get("text") or ""):
                    if "enchanted" in who:
                        k = self.count_each(each, a, p) if each else 1
                        pw, tg = pw + k * bp, tg + k * bt
        # its own text: Kor Spiritdancer, Aura Gnarlid, Eidolon of Countless Battles (a face-down card has none)
        own = "" if p.face_down else (p.card.get("text") or "")
        nm = p.card.get("name") or ""
        if nm:                                           # "Tuvasa gets +1/+1 for each enchantment you control": its own name is "This creature"
            short = re.split(r",| the ", nm)[0]
            own = re.sub(rf"(?m)^(?:{re.escape(nm)}|{re.escape(short)}) gets", "This creature gets", own)
        for who, bp, bt, each in pt_bonuses(own):
            if "self" in who:
                k = self.count_each(each, p, p) if each else 1
                pw, tg = pw + k * bp, tg + k * bt
        # anthems from its other permanents: "Enchanted creatures you control get +1/+1" (Syr
        # Armont), "Creatures you control get +1/+1", "Other creatures you control get +1/+1"
        enchanted = any(a.attached_to == p.id for a in self.battlefield)
        for src in self.battlefield:
            t = src.card.get("text") or ""
            for m in re.finditer(r"(Other )?(Enchanted )?[Cc]reatures you control get \+(\d+)/\+(\d+)", t):
                if (m.group(1) and src is p) or (m.group(2) and not enchanted):
                    continue
                pw, tg = pw + int(m.group(3)), tg + int(m.group(4))
        return pw, tg

    def count_each(self, each: str, src: Perm, target: Perm) -> int:
        """How many there are of '... for each <each>'. src is the card with the text, target the
        creature getting the bonus. The other players' permanents come from what's been announced."""
        e = each.lower()
        auras = [x for x in self.battlefield if self.is_aura(x)]
        hostile = [x for x in self.on_their_cards if not x.endswith("(exiled)")]   # ours, on their cards
        if e.startswith("other enchantment on the battlefield"):
            return (len([x for x in self.enchantments() if x is not src]) + len(hostile)
                    + len(self.opponents_cards("Enchantment")))
        if e.startswith("enchantment you control"):
            return len(self.enchantments()) + len(hostile)
        if e.startswith("aura and equipment attached to it") or e.startswith("aura attached to it"):
            return len([x for x in auras if x.attached_to == target.id])
        if e.startswith("aura you control that's attached to a creature"):
            ids = {c.id for c in self.creatures()}
            return len([x for x in auras if x.attached_to in ids]) + len(hostile)
        if e.startswith("aura on the battlefield"):
            return len(auras) + len(hostile) + len(self.opponents_cards("Aura"))
        if e.startswith("aura you control"):
            return len(auras) + len(hostile)
        if e.startswith("creature you control"):
            return len(self.creatures())
        if e.startswith("card in its controller's hand") or e.startswith("card in your hand"):
            return len(self.hand)
        if e.startswith("land you control"):
            return len([x for x in self.battlefield if x.is_("Land")])
        msg = f"unmodelled count 'for each {each}' on {src.name}: work its size out by hand"
        if msg not in self.todo:
            self.todo.append(msg)
        return 0

    def describe(self, p: Perm) -> str:
        if p.face_down:
            pw, tg = self.stats(p)
            return f"a face-down {pw}/{tg} (#{p.id}{', ward 2' if p.ward2 else ''})" + (" [tapped]" if p.tapped else "")
        if p.is_("Planeswalker"):
            return f"{p.name} (loyalty {p.counters})" + (" [tapped]" if p.tapped else "")
        if p.is_("Creature"):
            pw, tg = self.stats(p)
            extra = [a.role + " Role" if a.role else a.name for a in self.battlefield if a.attached_to == p.id]
            return f"{p.name} {pw}/{tg}" + (f" (with {', '.join(extra)})" if extra else "") + (" [tapped]" if p.tapped else "")
        return p.name + (" [tapped]" if p.tapped else "")

    # ── mana ──────────────────────────────────────────────────────────────────────────────
    def sources(self):
        """One entry per mana it can make now: (the permanent that gets tapped, colours). A land
        enchanted by a mana Aura (Fertile Ground, Utopia Sprawl, Wild Growth, Overgrowth) makes its
        own mana PLUS the Aura's — found in the rehearsal, where the engine said 2 mana and the
        table had 3."""
        out, filters = [], []
        for p in self.battlefield:
            if p.tapped or p.attached_to is not None:
                continue
            text = p.card.get("text") or ""
            if p.is_("Creature") and p.sick:
                continue
            if p.is_("Land") or "{T}: Add" in text or "{T}: add" in text:
                cols = produced_colors(p.card)
                if cols:
                    if re.search(r"\{1\}, \{T\}: Add \{", text):     # Signets, filter lands: pay {1}, get two
                        filters.append((p, "".join(sorted(set(re.findall(r"\{([WUBRG])\}", text.split("{1}, {T}: Add", 1)[1])))) or "C"))
                        continue
                    if re.search(r"Add X mana of any one colou?r, where X is the number of enchantments", text):
                        n = len(self.enchantments())                     # Sanctum Weaver
                    else:
                        n = mana_amount(p.card)
                    out += [(p, cols)] * n
                    if p.is_("Land"):
                        out += [(p, c) for c in self.aura_mana(p)]
            # an Aura that grants "{T}: Add …" to its creature (Careful Cultivation)
            if p.is_("Creature"):
                for a in self.battlefield:
                    if a.attached_to == p.id:
                        for g in re.findall(r"\"\{T\}: Add ((?:\{[WUBRGC]\})+)\.\"", a.card.get("text") or ""):
                            out += [(p, c) for c in re.findall(r"\{([WUBRGC])\}", g)]
        # net one each, but only if some other mana can pay the first {1} (Mossfire Valley alone makes nothing)
        return out + filters if out else out

    def missing_color(self):
        """The commander colour its mana makes least of — what a "choose a color" land Aura names."""
        ident = [c for c in "WUBRG" if c in (self.commander.get("colorIdentity") or self.commander.get("colors") or [])] or ["G"]
        made = "".join(c for _, c in self.sources())
        return min(ident, key=lambda c: made.count(c))

    def aura_mana(self, land):
        """Extra mana from Auras on this land, one colour-string per mana."""
        extra = []
        for a in self.battlefield:
            if a.attached_to != land.id:
                continue
            t = a.card.get("text") or ""
            m = re.search(r"enchanted (?:land|forest|plains|island|swamp|mountain) is tapped for mana, its controller "
                          r"adds? (?:an )?additional ([^.]+)", t, re.I)
            if not m:
                continue
            what = m.group(1)
            if "any color" in what:
                extra.append("WUBRG")
            elif "chosen color" in what:
                extra.append(a.chosen or "G")
            else:
                extra += [c for c in re.findall(r"\{([WUBRGC])\}", what)] or ["G"]
        return extra

    def plan_payment(self, mana_cost: str, extra_generic=0):
        """A set of sources that pays the cost, or None. Coloured pips first from the sources that
        make the FEWEST colours (keep flexible lands for later), then generic from the rest."""
        generic, pips = parse_cost(mana_cost)
        generic = max(0, generic + extra_generic)
        pool = sorted(self.sources(), key=lambda s: len(s[1]))
        used = []
        # A land with a mana Aura is ONE tap for TWO mana: once it is tapped for one, its other mana
        # comes free and must be spent before anything else is tapped. Measured 2026-09-30: Fertile
        # Ground's Forest paid the {G} of Tanglespan Lookout, then Plains and a second Forest paid
        # the {2}, and the Aura's mana was thrown away.
        free = lambda: [s for s in pool if s[0] in used]
        # "X mana of any ONE colour" (Sanctum Weaver): every coloured pip it pays must be the same colour
        one_colour: dict[int, str] = {}

        def fits(s, color):
            if color not in s[1]:
                return False
            if "any one colo" in (s[0].card.get("text") or ""):
                return one_colour.get(s[0].id, color) == color
            return True
        for color, n in pips.items():
            for _ in range(n):
                s = next((s for s in free() if fits(s, color)), None) or next((s for s in pool if fits(s, color)), None)
                if not s:
                    return None
                if "any one colo" in (s[0].card.get("text") or ""):
                    one_colour[s[0].id] = color
                pool.remove(s)
                used.append(s[0])
        for _ in range(generic):
            s = (free() or pool or [None])[0]
            if s is None:
                return None
            pool.remove(s)
            used.append(s[0])
        return used

    def available_mana(self): return len(self.sources())

    def auto_discount(self, c: dict) -> int:
        """Generic mana its own permanents take off this spell: "Enchantment spells you cast cost {1}
        less" (Jukai Naturalist, Starfield Mystic), "Aura (and Equipment) spells you cast cost {1}
        less" (Danitha Capashen, Transcendent Envoy). Each copy counts."""
        types, subs = c.get("types") or [], c.get("subtypes") or []
        n = 0
        for p in self.battlefield:
            for kinds, k in re.findall(r"([\w ]+?) spells you cast cost \{(\d+)\} less", p.card.get("text") or ""):
                kinds = kinds.lower()
                if ("enchantment" in kinds and "Enchantment" in types) or ("aura" in kinds and "Aura" in subs) \
                        or ("creature" in kinds and "Creature" in types):
                    n += int(k)
        return n

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
            pay = self.plan_payment(c.get("manaCost", ""), -self.auto_discount(c))
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
        import time as _t
        self.last_cast = {"name": c["name"], "perm": None, "at": _t.time(), "commander": is_cmdr}
        types = c.get("types") or []
        triggered = self.on_cast(c)                  # resolve before the spell (they're above it on the stack)
        if "Instant" in types or "Sorcery" in types:
            self.graveyard.append(c)
            modes = modal_options(c)
            if modes and choose_modes:
                picked = choose_modes(c, modes)
                said.append("I choose: " + " — and — ".join(picked) + ".")
            else:
                said.append(_short_text(c))                      # the table resolves its effect
            said += triggered
        else:
            p = Perm(c, sick="Haste" not in (c.get("keywords") or []))
            if is_hostile_aura(c):
                target = choose_target(c, None)          # None: pick among the OTHER players' cards
                self.on_their_cards.append(f"{c['name']} on {target}")
                # still an Aura it controls entering: Tanglespan, constellation (not Siona: not its creature)
                return " ".join(said + triggered + [f"It enchants {target}.", _short_text(c)]
                                + self.on_enchantment_enters(p))
            if "Aura" in (c.get("subtypes") or []):
                target = choose_target(c, self.aura_targets(c))
                p.attached_to = target.id
                if "choose a color" in (c.get("text") or "").lower():
                    p.chosen = getattr(self, "next_color", None) or self.missing_color()
                    self.next_color = None
                said = [f"I cast {c['name']} on {self.shown(target)}."]      # one sentence, not two
            said += triggered
            if p.is_("Planeswalker"):
                try:
                    p.counters = int(c.get("loyalty") or 0)           # loyalty lives on the counters
                except ValueError:
                    pass
            self.battlefield.append(p)
            self.last_cast["perm"] = p.id
            said += self.enter_effects(p, choose_target)
            if p.is_("Enchantment"):
                said += self.on_enchantment_enters(p)
        return " ".join(said)

    def enter_effects(self, p, choose_target):
        """The parts of 'when this enters' that change ITS OWN board: tokens and Roles."""
        text = p.card.get("text") or ""
        said = []
        # Only "When THIS enters": the subject must be this card. "Whenever an enchantment you control
        # enters, create a 2/2 Pegasus" (Archon) is a trigger on OTHER cards entering, and reading it
        # as Archon's own ETB made a Pegasus whenever Archon itself was cast.
        short = re.escape(p.name.split(",")[0].split(" of ")[0])
        subj = rf"(?:this creature|this Aura|this enchantment|{re.escape(p.name)}|{short})"
        etb = re.search(rf"When(?:ever)? {subj} enters(?: or attacks)?(?: the battlefield)?, ([^.]+)\.", text)
        clause = etb.group(1) if etb else (text if "Sorcery" in p.types else "")
        handled = False
        for m in re.finditer(r"create (a|two|three) (\d+)/(\d+) ([\w ,]+?) creature tokens?", clause):
            n = {"a": 1, "two": 2, "three": 3}[m.group(1)]
            for _ in range(n):
                tok = {"name": f"{m.group(4).split(' ')[-1]} token", "types": ["Creature"], "power": m.group(2),
                       "toughness": m.group(3), "text": "", "keywords": []}
                self.battlefield.append(Perm(tok, token=True))
            said.append(f"I create {m.group(1)} {m.group(2)}/{m.group(3)} {m.group(4)} token{'s' if n > 1 else ''}.")
            handled = True
        m = re.search(r"create an? (\w+(?: \w+)?) Role token attached to (up to one |another )?target creature", clause)
        if m and m.group(1) in ROLES:
            handled = True
            # "another target creature" (Ellivere) never means itself: with no other creature the
            # trigger does nothing. Measured 2026-09-30: the Role went on Ellivere and made her 6/6.
            others = [c for c in self.creatures() if c.id != p.id]
            cands = others if m.group(2) == "another " else (others or ([p] if p.is_("Creature") else []))
            if cands:
                target = choose_target({"name": f"{m.group(1)} Role", "text": ROLES[m.group(1)]}, cands)
                said += self.add_role(target, m.group(1))
        m = re.search(r"draw a card for each ([^.]+)", clause)              # Sage's Reverie
        if m:
            handled = True
            n = self.count_each(m.group(1), p, p)
            self.draw(n)
            said.append(f"I draw {n} card{'s' if n != 1 else ''}.")
        elif re.search(r"\byou draw a card\b", clause):                     # Ox Drover
            handled = True
            self.draw()
            said.append("I draw a card.")
        elif re.fullmatch(r"(?:you )?draw (a|one|two|three) cards?", clause.strip()):   # Mulldrifter, Pilgrim's Eye-less
            handled = True
            n = {"a": 1, "one": 1, "two": 2, "three": 3}[re.match(r"(?:you )?draw (\w+)", clause.strip()).group(1)]
            self.draw(n)
            said.append(f"I draw {'a card' if n == 1 else f'{n} cards'}.")
        if re.search(r"target opponent creates", clause):
            self.todo.append(f"{p.name}: name the opponent who gets the token (say it)")
        elif etb and not handled:
            self.todo.append(f"{p.name} entered: {clause}")
        if p.is_("Creature") and not p.token:
            for g in self.battlefield:                                       # Gylwain, Casting Director
                if "or another nontoken creature you control enters" in (g.card.get("text") or "") \
                        and "Role token" in (g.card.get("text") or ""):
                    self.todo.append(f"{g.name}: choose a Role for {p.name} — tablectl role \"{p.name}\" "
                                     f"Royal|Sorcerer|Monster")
        return said

    def add_role(self, target: Perm, kind: str) -> list[str]:
        """A Role token on one of its creatures. A creature keeps only one of your Roles. A Role is an
        Aura, so it triggers everything an Aura entering triggers."""
        for old in [a for a in self.battlefield if a.attached_to == target.id and a.role]:
            self.battlefield.remove(old)
        role = {"name": f"{kind} Role", "types": ["Enchantment"], "subtypes": ["Aura", "Role"], "text": ""}
        r = Perm(role, token=True, attached_to=target.id, role=kind)
        self.battlefield.append(r)
        return [f"{self.shown(target)} gets a {kind} Role."] + self.on_enchantment_enters(r)

    # ── triggers on its own permanents ───────────────────────────────────────────────────
    def on_cast(self, c) -> list[str]:
        """'Whenever you cast an enchantment spell, draw a card' (Enchantress's Presence), 'Whenever
        you cast an Aura spell, you may draw a card' (Kor Spiritdancer; it always does)."""
        types, subs = c.get("types") or [], c.get("subtypes") or []
        said = []
        for p in self.battlefield:
            m = re.search(r"Whenever you cast an? (enchantment|Aura) spell, (?:you may )?draw a card", p.card.get("text") or "")
            if m and ((m.group(1) == "Aura" and "Aura" in subs) or (m.group(1) == "enchantment" and "Enchantment" in types)):
                self.draw()
                said.append(f"{p.name}: I draw a card.")
        return said

    def on_enchantment_enters(self, e: Perm) -> list[str]:
        """An enchantment it controls entered (a Role, an Aura, an enchantment creature): every
        'Whenever an enchantment/Aura you control enters' and 'becomes attached' on its board."""
        said = []
        aura = self.is_aura(e)
        own_creatures = {c.id for c in self.creatures()}
        for src in list(self.battlefield):
            t = src.card.get("text") or ""
            m = (re.search(r"Whenever an Aura you control enters, ([^.]+)\.", t) if aura else None) or \
                re.search(r"Whenever an enchantment you control enters, ([^.]+)\.", t) or \
                re.search(r"Whenever this creature or another enchantment you control enters, ([^.]+)\.", t)
            if m:
                said += self.resolve_effect(src, m.group(1))
                if aura and "If that enchantment is an Aura, you may attach it to the token" in t:   # Ajani's Chosen
                    self.todo.append(f"{src.name}: you may move {e.name} onto the new token")
            m = re.search(r"Whenever an Aura you control becomes attached to a creature you control, ([^.]+)\.", t)
            if m and aura and e.attached_to in own_creatures:
                said += self.resolve_effect(src, m.group(1))
        return said

    def resolve_effect(self, src: Perm, effect: str) -> list[str]:
        """The effect half of a trigger on src: counters, cards, tokens. Anything else goes to the
        brain's to-do list rather than being skipped silently."""
        said, done = [], False
        if re.search(r"put a \+1/\+1 counter on (?:this creature|it)", effect):
            src.counters += 1
            said.append(f"{src.name} gets a +1/+1 counter.")
            done = True
        if re.search(r"\bdraw a card\b", effect):
            self.draw()
            said.append(f"{src.name}: I draw a card.")
            done = True
        m = re.search(r"create an? (\d+)/(\d+) ([\w ]+?) creature token(?: with ([\w ,]+?))?(?:\.|$| and| If)", effect + ".")
        if m:
            words = m.group(3).split()
            kws = [k.strip().capitalize() for k in re.split(r",| and ", m.group(4) or "") if k.strip()]
            said += self.make_token(words[-1], int(m.group(1)), int(m.group(2)), kws)
            done = True
        if not done:
            self.todo.append(f"{src.name}: {effect}")
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

    def shown(self, p) -> str:
        """How the table hears a permanent: its name, or "a face-down 2/2" (never the hidden name)."""
        return f"a face-down {self.stats(p)[0]}/{self.stats(p)[1]}" if p.face_down else p.name

    def private_hand(self):
        """Names the table must not hear from the AI: its hand, and its face-down permanents."""
        return [c["name"] for c in self.hand] + [p.name for p in self.battlefield if p.face_down]


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


# ── Manual play: an external brain (a person, or Claude via tablectl) drives the player ──────────
# Every method validates what the engine can check (card in hand, one land a turn, mana and colours,
# legal Aura targets) and raises IllegalAction otherwise. It returns the sentences the table hears.
class IllegalAction(ValueError):
    pass


def _match(name: str, candidates, key):
    """Case-insensitive exact, then unique prefix, then unique substring match."""
    n = name.strip().lower().lstrip("#")
    exact = [c for c in candidates if key(c).lower() == n]
    if exact:
        return exact[0]
    for test in (lambda k: k.startswith(n), lambda k: n in k):
        hits = [c for c in candidates if test(key(c).lower())]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1 and len({key(h) for h in hits}) == 1:
            return hits[0]
        if len(hits) > 1:
            raise IllegalAction(f"'{name}' is ambiguous: {sorted({key(h) for h in hits})}")
    return None


def _manual(cls):
    def hand_card(self, name):
        c = _match(name, self.hand, lambda c: c["name"])
        if not c:
            raise IllegalAction(f"'{name}' is not in your hand")
        return c

    def perm(self, ref):
        """A permanent by '#id' or name."""
        if ref.strip().startswith("#") and ref.strip()[1:].isdigit():
            p = next((p for p in self.battlefield if p.id == int(ref.strip()[1:])), None)
        else:
            p = _match(ref, self.battlefield, lambda p: p.name)
        if not p:
            raise IllegalAction(f"no permanent '{ref}' on your battlefield")
        return p

    def begin_turn(self, skip_draw: bool = False):
        self.turn += 1
        self.land_played = False
        for p in self.battlefield:
            p.tapped = False
            p.sick = False
        said = [f"{self.name}, turn {self.turn}."]          # not "Claude's turn": Moira's possessive comes out "Clouds turn"
        said += self.upkeep()
        before = len(self.hand)
        extra = len([a for a in self.battlefield if a.attached_to is not None
                     and re.search(r"draws an additional card", a.card.get("text") or "")])   # Righteous Authority on its own creature
        if skip_draw:                                       # the player who goes first in a two-player game does not draw
            said.append("I'm on the play: no draw this turn.")
        else:
            self.draw()
        if extra:
            self.draw(extra)
            said.append(f"I draw {extra} additional card{'s' if extra != 1 else ''}.")
        drew = self.hand[-1]["name"] if len(self.hand) > before else None
        return said, drew

    def upkeep(self) -> list[str]:
        """'At the beginning of each upkeep' on its creatures (Verdant Embrace grants it). This runs
        on its own upkeep; each opponent's upkeep is a to-do for the brain."""
        said = []
        for a in self.battlefield:
            g = re.search(r"\"At the beginning of each upkeep, create a (\d+)/(\d+) ([\w ]+?) creature token\.\"",
                          a.card.get("text") or "")
            if g and a.attached_to is not None:
                kind = g.group(3).split()[-1]
                said += self.make_token(kind, int(g.group(1)), int(g.group(2)))
                msg = f"{a.name}: make a {kind} on EACH opponent's upkeep too — tablectl token {kind} 1 1"
                if msg not in self.todo:
                    self.todo.append(msg)
        return said

    def manual_land(self, name):
        c = self.hand_card(name)
        if "Land" not in c["types"]:
            raise IllegalAction(f"{c['name']} is not a land")
        if self.land_played:
            raise IllegalAction("you already played a land this turn")
        said = [self.play_land(c)]
        if "return a land you control to its owner's hand" in (c.get("text") or ""):
            # bounce lands (Simic Growth Chamber & co.): another land back to hand — a tapped basic first,
            # then any basic, then any other land; with no other land, it returns itself
            this = next((p for p in reversed(self.battlefield) if p.card is c), None)
            others = [p for p in self.battlefield if p.is_("Land") and p is not this]
            basic = lambda p: "Basic" in (p.card.get("supertypes") or [])
            pick = (next((p for p in others if basic(p) and p.tapped), None) or next((p for p in others if basic(p)), None)
                    or (others[0] if others else this))
            if pick is not None:
                self.battlefield.remove(pick)
                self.hand.append(pick.card)
                said.append(f"{c['name']} returns {pick.name} to my hand.")
        return said

    def manual_cast(self, name, on=None, role_on=None, modes=None, targets=None, commander=False, x=0, discount=0,
                    color=None):
        """discount: generic cost reduction the brain knows applies (e.g. Jukai Naturalist makes
        enchantment spells cost {1} less) — the engine does not read cost-reduction text.
        color: for "as this enters, choose a color" (W/U/B/R/G)."""
        if color:
            self.next_color = color.strip().upper()[:1]
        if commander or name.lower() in (self.commander["name"].lower(), "commander"):
            if not self.cmdr_in_zone:
                raise IllegalAction("your commander is not in the command zone")
            c, is_cmdr = self.commander, True
            extra = 2 * self.cmdr_casts + x
        else:
            c, is_cmdr, extra = self.hand_card(name), False, x
        extra -= max(0, int(discount)) + self.auto_discount(c)     # discount: reductions the engine can't see
        if "Land" in c["types"]:
            raise IllegalAction(f"{c['name']} is a land — use land")
        pay = self.plan_payment(c.get("manaCost", ""), extra)
        if pay is None:
            g, pips = parse_cost(c.get("manaCost", ""))
            raise IllegalAction(f"can't pay {c.get('manaCost')}{f' + {extra}' if extra else ''} "
                                f"(untapped sources: {', '.join(p.name for p, _ in self.sources()) or 'none'})")
        aura = "Aura" in (c.get("subtypes") or [])
        if aura and not is_hostile_aura(c):
            if not on:
                raise IllegalAction(f"{c['name']} is an Aura: say what it enchants with --on "
                                    f"(legal: {[f'#{p.id} {p.name}' for p in self.aura_targets(c)]})")
            target = self.perm(on)
            if target not in self.aura_targets(c):
                raise IllegalAction(f"{c['name']} can't enchant {target.name}")
        if aura and is_hostile_aura(c) and not on:
            raise IllegalAction(f"{c['name']} goes on an opponent's permanent: name it with --on")

        def choose_target(card, creatures):
            if creatures is None:
                return on
            if card is c and on:
                return self.perm(on)
            if role_on:
                t = self.perm(role_on)
                if t in creatures:
                    return t
            return max(creatures, key=lambda k: self.stats(k)[0])     # default: its biggest creature

        def choose_modes(card, spec):
            n, all_modes = spec
            if not modes:
                raise IllegalAction(f"{card['name']} is modal — choose {n} with --mode (1-based): "
                                    + " | ".join(f"{i + 1}. {m}" for i, m in enumerate(all_modes)))
            picked = [all_modes[int(m) - 1] for m in modes]
            if len(picked) != n:
                raise IllegalAction(f"{card['name']} needs exactly {n} modes")
            return picked

        # validate modes BEFORE paying, so a bad call doesn't tap anything
        spec = modal_options(c)
        if spec and ("Instant" in c["types"] or "Sorcery" in c["types"]):
            choose_modes(c, spec)
        said = self.cast(f"cast {c['name']}", c, pay, is_cmdr, choose_target, choose_modes)
        if targets:
            said += f" Targeting {targets}."
        return [said]

    def manual_attack(self, assignments: dict, role_on=None):
        """assignments: {'#12' or creature name: player name}."""
        by_player, said = {}, []
        for ref, who in assignments.items():
            c = self.perm(ref)
            if not c.is_("Creature"):
                raise IllegalAction(f"{c.name} is not a creature")
            if c.tapped:
                raise IllegalAction(f"{c.name} is tapped")
            if c.sick and "Haste" not in (c.card.get("keywords") or []):
                raise IllegalAction(f"{c.name} has summoning sickness")
            by_player.setdefault(who, []).append(c)
        attackers = [c for cs in by_player.values() for c in cs]
        for who, cs in by_player.items():
            for c in cs:
                if "Vigilance" not in (c.card.get("keywords") or []):
                    c.tapped = True
            for c in cs:
                said += self.attack_triggers(c, who, attackers, role_on)
            total = sum(self.stats(c)[0] for c in cs)
            # Short names keep the sentence speakable: "Siona", not "Siona, Captain of the Pyleas";
            # past four attackers, a count ("five creatures") instead of a list.
            names = [self.shown(c).split(",")[0] for c in cs]
            if len(names) > 4:
                names = [f"{['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight'][len(names)] if len(names) <= 8 else len(names)} creatures"]
            said.append(f"I attack {who} with " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1])
                        + f", {total} damage.")                    # same words as the Gemma turn
        return said

    def attack_triggers(self, c, defender, attackers, role_on=None):
        """'Whenever this creature attacks' on the attacker and on the Auras and Roles it carries."""
        said = []
        text = "" if c.face_down else (c.card.get("text") or "")
        enchanted_c = any(a.attached_to == c.id for a in self.battlefield) or "Enchantment" in (c.card.get("types") or [])
        for src in self.battlefield:                         # Kestia: "Whenever an enchanted creature or enchantment creature you control attacks, draw a card."
            if src.face_down:
                continue
            if enchanted_c and "Whenever an enchanted creature or enchantment creature you control attacks, draw a card" in (src.card.get("text") or ""):
                self.draw()
                said.append(f"{src.name}: I draw a card.")
        m = re.search(r"Whenever [^.]*?attacks[^,]*, ([^.]+)\.", text)
        if m and "an enchanted creature or enchantment creature you control attacks" in text:
            m = None                                         # handled above, for every enchanted attacker, not only this card
        if m:
            eff = m.group(1)
            if "Role token" in eff:                                       # Ellivere
                fake = Perm({**c.card, "text": f"When this creature enters, {eff}."})
                fake.id = c.id
                rt = self.perm(role_on) if role_on else None
                said += self.enter_effects(fake, lambda card, cands: rt if rt in cands else
                                           max(cands, key=lambda k: self.stats(k)[0]))
            elif "target opponent creates" in eff:                        # Ox Drover
                tok = re.search(r"creates an? (\d+/\d+ [\w ]+?) creature token", eff)
                said.append(f"{defender} creates a {tok.group(1) if tok else ''} token.".replace("  ", " "))
                if "you draw a card" in eff:
                    self.draw()
                    said.append("I draw a card.")
            else:
                self.todo.append(f"{c.name} attacks: {eff}")              # Sun Titan: pick from the graveyard
        for a in [a for a in self.battlefield if a.attached_to == c.id]:
            at = a.card.get("text") or ""
            if a.role == "Sorcerer":
                self.todo.append(f"{c.name} attacks with a Sorcerer Role: scry 1")
            g = re.search(r"\"Whenever this creature attacks, ([^\"]+?)\.?\"", at)
            if g and "untap all lands you control" in g.group(1):         # Bear Umbra
                for land in self.battlefield:
                    if land.is_("Land"):
                        land.tapped = False
                said.append(f"{a.name}: I untap all my lands.")
            elif g and "Monster Role token attached to up to one target attacking creature" in g.group(1):
                # Giant Inheritance: never replace a better Role (Ellivere's Virtuous) with Monster
                free = [x for x in attackers if not any(r.attached_to == x.id and r.role for r in self.battlefield)]
                if free:
                    said += self.add_role(max(free, key=lambda k: self.stats(k)[0]), "Monster")
            elif g:
                self.todo.append(f"{a.name} on {c.name}: {g.group(1)}")
            w = re.search(r"Whenever enchanted creature attacks, ([^.]+)\.", at)
            if w:                                                         # Songbirds' Blessing
                self.todo.append(f"{a.name} on {c.name}: {w.group(1)}")
        return said

    def combat_damage(self, hits: dict) -> tuple[list[str], dict]:
        """hits: {attacker ref: (player, amount or None)} for creatures that dealt combat damage to a
        PLAYER (unblocked, or trample over). Returns what's said and life changes {player: delta},
        including its own lifelink gain under its own name."""
        said, life = [], {}
        ellivere_draws = len([p for p in self.battlefield
                              if "Whenever an enchanted creature you control deals combat damage to a player, draw a card"
                              in (p.card.get("text") or "")])
        pegasus_lifelink = any("Pegasus creatures you control have lifelink" in (p.card.get("text") or "")
                               for p in self.battlefield)
        for ref, (who, amount) in hits.items():
            c = self.perm(ref)
            n = self.stats(c)[0] if amount is None else int(amount)
            if n <= 0:
                continue
            life[who] = life.get(who, 0) - n
            said.append(f"{self.shown(c)} deals {n} to {who}.")
            auras = [a for a in self.battlefield if a.attached_to == c.id]
            if auras and ellivere_draws:
                self.draw(ellivere_draws)
                said.append(f"It's enchanted, so I draw {'a card' if ellivere_draws == 1 else f'{ellivere_draws} cards'}.")
            for a in auras:
                at = a.card.get("text") or ""
                if "Whenever enchanted creature deals combat damage to a player, create that many 1/1" in at:
                    kind = re.search(r"that many 1/1 ([\w ]+?) creature tokens", at).group(1).split()[-1]
                    for _ in range(n):
                        self.make_token(kind, 1, 1)
                    said.append(f"{a.name}: I create {n} 1/1 {kind} tokens.")
                if "deals damage to an opponent, you may draw a card" in at:      # Snake Umbra
                    self.draw()
                    said.append(f"{a.name}: I draw a card.")
            if c.turned_up_turn == self.turn:                    # Kaust, Eyes of the Glade
                k = len([x for x in self.battlefield if not x.face_down and "that was turned face up this turn deals combat damage to a player, draw a card" in (x.card.get("text") or "")])
                if k:
                    self.draw(k)
                    said.append(f"It was turned face up this turn: I draw {'a card' if k == 1 else f'{k} cards'}.")
            kws = [] if c.face_down else (c.card.get("keywords") or [])
            if "Lifelink" in kws or (pegasus_lifelink and "Pegasus" in c.name):
                life[self.name] = life.get(self.name, 0) + n
                said.append(f"Lifelink: I gain {n}.")
        return said, life

    def move(self, ref, to):
        """Your permanent leaves the battlefield: to graveyard / exile / hand. Attached Auras and Roles
        fall off; tokens cease to exist; your commander goes to the command zone."""
        p = self.perm(ref)
        gone = [p] + [a for a in self.battlefield if a.attached_to == p.id]
        for x in gone:
            self.battlefield.remove(x)
            if x.token:
                continue
            if x.card["name"] == self.commander["name"]:
                self.cmdr_in_zone = True
            elif to == "hand" and x is p:
                self.hand.append(x.card)
            elif to == "exile":
                self.on_their_cards.append(f"{x.name} (exiled)")
            else:
                self.graveyard.append(x.card)
        where = {"graveyard": "is destroyed", "exile": "is exiled", "hand": "returns to my hand"}[to]
        return [f"{p.name} {where}" + (" (commander to the command zone)" if p.card["name"] == self.commander["name"] else "") + "."]

    def search_library(self, name, to="hand", tapped=False):
        c = _match(name, self.library, lambda c: c["name"])
        if not c:
            raise IllegalAction(f"no '{name}' in your library")
        self.library.remove(c)
        if to == "battlefield":
            self.battlefield.append(Perm(c, sick=False, tapped=tapped))
        else:
            self.hand.append(c)
        self.rng.shuffle(self.library)
        return [f"I search my library for {c['name'] if to == 'battlefield' or 'Basic' in (c.get('supertypes') or []) else 'a card'}, "
                f"put it {'onto the battlefield' + (' tapped' if tapped else '') if to == 'battlefield' else 'into my hand'}, and shuffle."]

    # ── library and zone tools the brain drives by hand (Brainstorm, Ponder, ninjutsu, blink) ──
    def peek(self, n):
        """Top n of the library, top first. PRIVATE: the brain sees it, the table doesn't."""
        return [c["name"] for c in self.library[::-1][:n]]

    def topdeck(self, name):
        c = self.hand_card(name)
        self.hand.remove(c)
        self.library.append(c)                       # library.pop() draws from the end: the end is the top
        return ["I put a card on top of my library."]

    def bottom(self, name, from_top=False):
        """A card to the bottom: from the hand, or (from_top) from the top of the library."""
        if from_top:
            c = _match(name, self.library[::-1][:10], lambda c: c["name"])
            if not c:
                raise IllegalAction(f"'{name}' isn't near the top of your library")
            self.library.remove(c)
        else:
            c = self.hand_card(name)
            self.hand.remove(c)
        self.library.insert(0, c)
        return ["I put a card on the bottom of my library."]

    def mulligan(self) -> list[str]:
        """London mulligan, Commander style: the hand goes back, the library is shuffled (from the sealed
        seed, so it stays verifiable), seven are drawn. The first mulligan is free; after that the brain
        puts one card per extra mulligan on the bottom (the "bottom" action)."""
        if self.turn or self.land_played or self.battlefield:
            raise ValueError("mulligans are only before the game starts")
        self.mulligans = getattr(self, "mulligans", 0) + 1
        self.library.extend(self.hand)
        self.hand.clear()
        self.rng.shuffle(self.library)
        self.draw(7)
        owe = self.mulligans - 1
        if self.fair is not None:
            self.fair.setdefault("mulligans", self.mulligans)
            self.fair["mulligans"] = self.mulligans
        return [f"I mulligan{' (my free one)' if owe == 0 else ''} and draw a new seven."
                + (f" I'll put {owe} on the bottom." if owe else "")]

    def shuffle_library(self):
        self.rng.shuffle(self.library)
        return ["I shuffle my library."]

    def put(self, name, tapped=False):
        """Hand → battlefield without paying (ninjutsu, "put onto the battlefield")."""
        c = self.hand_card(name)
        self.hand.remove(c)
        p = Perm(c, sick=True, tapped=tapped)
        if p.is_("Planeswalker"):
            p.counters = int(c.get("loyalty") or 0)
        self.battlefield.append(p)
        said = [f"I put {c['name']} onto the battlefield" + (" tapped." if tapped else ".")]
        said += self.enter_effects(p, lambda card, cands: max(cands, key=lambda k: self.stats(k)[0]))
        if p.is_("Enchantment"):
            said += self.on_enchantment_enters(p)
        return said

    def blink(self, ref):
        """Exile one of its permanents and return it: a new object (no counters, untapped, summoning
        sick), its Auras fall off, and its enters-the-battlefield effects trigger again."""
        p = self.perm(ref)
        for a in [a for a in self.battlefield if a.attached_to == p.id]:
            self.battlefield.remove(a)
            if not a.token:
                self.graveyard.append(a.card)
        self.battlefield.remove(p)
        if p.token:
            return [f"{p.name} is exiled. It's a token, so it doesn't come back."]
        q = Perm(p.card, sick=True)
        if q.is_("Planeswalker"):
            q.counters = int(p.card.get("loyalty") or 0)
        self.battlefield.append(q)
        said = [f"I exile {p.name} and return it."]
        said += self.enter_effects(q, lambda card, cands: max(cands, key=lambda k: self.stats(k)[0]))
        return said

    # ── face-down cards: disguise, morph, megamorph, manifest, cloak ───────────────────────
    def cast_face_down(self, name):
        """Cast a disguise/morph card face down for {3}: a nameless 2/2 (disguise: ward {2})."""
        c = self.hand_card(name)
        fc = face_cost(c)
        if not fc:
            raise IllegalAction(f"{c['name']} has no disguise or morph: it can't be cast face down")
        pay = self.plan_payment("{3}")
        if pay is None:
            raise IllegalAction("can't pay {3} to cast it face down")
        for x in pay:
            x.tapped = True
        self.hand.remove(c)
        p = Perm(c, sick=True, face_down=True, ward2=fc[0] == "Disguise", how=fc[0].lower())
        self.battlefield.append(p)
        import time as _t
        self.last_cast = {"name": "a face-down creature", "perm": p.id, "at": _t.time(), "commander": False}
        return [f"I cast a creature face down" + (" with ward 2." if p.ward2 else ".")]

    def manifest(self, n=1, cloak=False):
        """The top card(s) of the library onto the battlefield face down as 2/2s (cloak: ward {2})."""
        made = 0
        for _ in range(n):
            if not self.library:
                break
            self.battlefield.append(Perm(self.library.pop(), sick=True, face_down=True, ward2=cloak,
                                         how="cloak" if cloak else "manifest"))
            made += 1
        word = "cloak" if cloak else "manifest"
        return [f"I {word} the top card of my library." if made == 1 else f"I {word} the top {made} cards of my library."]

    def turn_up(self, ref, free=False):
        """Turn a face-down permanent face up: pay its disguise/morph/megamorph cost (a manifested or
        cloaked creature card: its mana cost), or nothing with free=True (Kaust's ability)."""
        p = self.perm(ref)
        if not p.face_down:
            raise IllegalAction(f"#{p.id} isn't face down")
        fc = face_cost(p.card)
        cost = fc[1] if fc else (p.card.get("manaCost") if p.is_("Creature") else None)
        if cost is None and not free:
            raise IllegalAction(f"#{p.id} is a face-down non-creature with no morph: it can't be turned face up")
        if not free:
            pay = self.plan_payment(cost)
            if pay is None:
                raise IllegalAction(f"can't pay {cost} to turn #{p.id} face up")
            for x in pay:
                x.tapped = True
        p.face_down, p.ward2, p.turned_up_turn = False, False, self.turn
        said = [f"I turn #{p.id} face up: {p.name}."]
        text = p.card.get("text") or ""
        if fc and fc[0] == "Megamorph":
            p.counters += 1
            said.append(f"{p.name} gets a +1/+1 counter.")
        m = re.search(r"As this creature is turned face up, put (\w+) \+1/\+1 counters? on it", text)
        if m:                                                    # Hooded Hydra
            n = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.get(m.group(1), 0)
            p.counters += n
            said.append(f"{p.name} gets {n} +1/+1 counters.")
        m = re.search(r"When this creature is turned face up, ([^.]+)\.", text)
        if m:
            said += self.resolve_effect(p, m.group(1))
        for src in list(self.battlefield):                       # True Identity, Mastery of the Unseen
            t = src.card.get("text") or ""
            if src is p or src.face_down:
                continue
            m = re.search(r"Whenever (?:this enchantment or )?(?:another )?(?:a )?permanent you control is turned face up, ([^.]+)\.", t)
            if m:
                said += self.resolve_effect(src, m.group(1))
        return said

    def make_tokens(self, name, power, toughness, keywords=(), n=1, tapped=False):
        for _ in range(n):
            tok = {"name": f"{name} token", "types": ["Creature"], "power": str(power), "toughness": str(toughness),
                   "text": "", "keywords": list(keywords)}
            self.battlefield.append(Perm(tok, token=True, tapped=tapped))
        return [f"I create {n} {power}/{toughness} {name} token{'s' if n != 1 else ''}" + (", tapped." if tapped else ".")]

    def make_token(self, name, power, toughness, keywords=()):
        tok = {"name": f"{name} token", "types": ["Creature"], "power": str(power), "toughness": str(toughness),
               "text": "", "keywords": list(keywords)}
        self.battlefield.append(Perm(tok, token=True))
        return [f"I create a {power}/{toughness} {name} token."]

    def discard(self, name):
        c = self.hand_card(name)
        self.hand.remove(c)
        self.graveyard.append(c)
        return [f"I discard {c['name']}."]

    def mill(self, n):
        milled = [self.library.pop() for _ in range(min(n, len(self.library)))]
        self.graveyard += milled
        return [f"I mill {len(milled)}: {', '.join(c['name'] for c in milled)}."]

    for f in (hand_card, perm, begin_turn, upkeep, manual_land, manual_cast, manual_attack, attack_triggers,
              combat_damage, move, search_library, make_token, make_tokens, discard, mill, peek, topdeck, bottom,
              shuffle_library, mulligan, put, blink, cast_face_down, manifest, turn_up):
        setattr(cls, f.__name__, f)
    return cls


_manual(VirtualPlayer)


def brain_view(p: VirtualPlayer) -> dict:
    """Everything the brain needs, including the PRIVATE hand. Never send this to a page."""
    castable = {o[1]["name"]: o[1].get("manaCost") for o in p.castable()}
    return {
        **p.public(),
        "hand": [{"name": c["name"], "cost": c.get("manaCost") or "", "type": c.get("type"),
                  "text": c.get("text"), "pt": f"{c['power']}/{c['toughness']}" if c.get("power") else None,
                  "castable_now": c["name"] in castable, "land": "Land" in c["types"],
                  "face_down_castable": bool(face_cost(c)) and p.plan_payment("{3}") is not None,
                  "face_up_cost": " ".join(face_cost(c)) if face_cost(c) else None,
                  # for Auras: exactly where it may go ("#id name"), or "an opponent's permanent"
                  "aura_targets": (["an opponent's permanent (--on 'their card')"] if is_hostile_aura(c) else
                                   [f"#{t.id} {t.name}" for t in p.aura_targets(c)])
                  if "Aura" in (c.get("subtypes") or []) else None} for c in p.hand],
        "commander_card": {"name": p.commander["name"], "cost": p.commander["manaCost"], "text": p.commander["text"],
                           "castable_now": p.commander["name"] in castable},
        "permanents": [{"id": x.id, "name": x.name, "type": x.card.get("type"), "tapped": x.tapped, "sick": x.sick,
                        "face_down": x.face_down, "turn_up_cost": (" ".join(face_cost(x.card)) if face_cost(x.card)
                                                                   else x.card.get("manaCost")) if x.face_down else None,
                        "token": x.token, "attached_to": x.attached_to, "role": x.role,
                        "pt": "%d/%d" % p.stats(x) if x.is_("Creature") else None,
                        "text": (x.card.get("text") or "")[:300]} for x in p.battlefield],
        "mana_sources": [f"{s.name} ({cols})" for s, cols in p.sources()],
        "land_played": p.land_played,
        "graveyard": [c["name"] for c in p.graveyard],
    }
