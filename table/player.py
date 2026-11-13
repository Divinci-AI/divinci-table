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
import threading
from collections import Counter
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
    text, subs = re.sub(r'"[^"]*"', "", card.get("text") or ""), card.get("subtypes") or []   # a quoted ability is the token's it makes, not this card's
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
    r"(This creature and enchanted creature each get|This creature gets|Enchanted creature gets|Equipped creature gets|"
    r"enchanted permanent is a creature, it gets)\s+([^.\"]+)")


def pt_bonuses(text: str) -> list[tuple[set, int, int, str | None]]:
    """Power/toughness bonuses in a card's text: (who, +P, +T, 'for each …' phrase or None).
    who is {'self'} (This creature), {'enchanted'} (an Aura's or Equipment's creature) or both (Eidolon of
    Countless Battles). Quoted granted abilities are skipped, so Giant Inheritance's "Whenever this
    creature attacks" isn't read as a bonus."""
    out = []
    for m in _PT_SUBJECT.finditer(re.sub(r"\"[^\"]*\"", "", text)):
        subj = m.group(1).lower()
        who = ({"self", "enchanted"} if "and enchanted" in subj else {"self"} if subj.startswith("this")
               else {"enchanted"})
        for b in re.finditer(r"([+-]\d+)/([+-]\d+)(?: for each ((?:(?! and \+|, |\.).)+))?", m.group(2)):
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


STATION_KEYWORDS = ("Flying", "Vigilance", "Trample", "Haste", "First strike", "Double strike", "Deathtouch", "Lifelink",
                    "Reach", "Menace", "Hexproof", "Indestructible", "Ward")
EVERY_KEYWORD = STATION_KEYWORDS + ("Defender", "Flash")


def printed_keywords(card: dict) -> list[str]:
    """Keywords on the card's own keyword lines ("Flying, vigilance", "Vigilance, ward {2}", "Menace (…)"). A deck built by
    make_deck_json has no `keywords` field at all, so until this read the text no drafted creature had Trample, Flying, Haste,
    Lifelink or Vigilance: game 4's Captain Burnham trampled for nothing and cost Michael the game on his turn 7."""
    out = list(card.get("keywords") or [])
    for line in (card.get("text") or "").split("\n"):
        line = re.sub(r"\s*\([^)]*\)", "", line).strip()
        if not line:
            continue
        found = []
        for word in line.split(","):
            w = word.strip()
            k = next((kw for kw in EVERY_KEYWORD if w.lower() == kw.lower()), None)
            if k is None and re.fullmatch(r"ward \{[^}]+\}", w, re.I):
                k = "Ward"
            if k is None:
                found = []
                break
            found.append(k)
        out += [k for k in found if k not in out]
    return out


_STATION_LINE = re.compile(r"(?m)^(\d+)\+\s*\|\s*(.+)$")


def station_lines(card: dict) -> list[tuple[int, str]]:
    """A Spacecraft's "N+ | what it gets" lines, in the card's order."""
    return [(int(m.group(1)), m.group(2)) for m in _STATION_LINE.finditer(card.get("text") or "")]


def has_station(card: dict) -> bool:
    return bool(re.search(r"^Station\b", card.get("text") or "", re.M))


def station_grants(card: dict, charge: int) -> list[str]:
    """Keywords a Spacecraft's "N+ | Flying, vigilance" lines give it once it has N charge counters (Class-F Shuttlecraft)."""
    out = []
    for n, what in station_lines(card):
        if charge >= n:
            for w in re.split(r",\s*", what):
                k = next((kw for kw in STATION_KEYWORDS if w.strip().lower().startswith(kw.lower() + " ") or w.strip().lower() == kw.lower()), None)
                if k and k not in out:
                    out.append(k)
    return out


def station_threshold(card: dict) -> int | None:
    """A Spacecraft with Station is an artifact creature once it has this many charge counters. The reminder text says so ("It's an
    artifact creature at 6+"); a printing without it ("Station" alone: H.M.S. Bounty, Romulan Bird-of-Prey, Discovery, Borg Cube) reads
    the same off its highest "N+ |" line, as long as the card prints a power (Deep Space 9 prints none and never becomes one)."""
    text = card.get("text") or ""
    m = re.search(r"artifact creature at (\d+)\+", text)
    if m:
        return int(m.group(1))
    if has_station(card) and card.get("power") not in (None, ""):
        lines = station_lines(card)
        return max(n for n, _ in lines) if lines else None
    return None


def station_top(card: dict) -> int | None:
    """Charge counters past which stationing changes nothing: the threshold, or the highest "N+ |" line (Deep Space 9)."""
    th = station_threshold(card)
    lines = [n for n, _ in station_lines(card)]
    top = max([*lines, *([th] if th is not None else [])], default=None)
    return top


def active_text(card: dict, charge: int = 0) -> str:
    """The card's rules text as it stands now: an "N+ |" line reads as its plain words once charge has reached N, and not at all before."""
    out = []
    for line in (card.get("text") or "").split("\n"):
        m = _STATION_LINE.match(line)
        if m:
            if charge >= int(m.group(1)):
                out.append(m.group(2))
        else:
            out.append(line)
    return "\n".join(out)


def beam_cost(card: dict) -> str | None:
    """The cost of "Beam me up {N}{C}": cast this from your graveyard for it, returning a creature you control to its owner's hand; then the spell is exiled."""
    m = re.search(r"(?m)^Beam me up ((?:\{[^}]+\})+)", card.get("text") or "")
    return m.group(1) if m else None


def cycle_cost(card: dict) -> str | None:
    m = re.search(r"(?m)^Basic landcycling ((?:\{[^}]+\})+)", card.get("text") or "")
    return m.group(1) if m else None


def is_equipment(card: dict) -> bool:
    return "Equipment" in (card.get("subtypes") or [])


def equip_cost(card: dict) -> str | None:
    """The {N} of "Equip {N}" (a token: the same line in the text the table gave it)."""
    m = re.search(r"(?m)^Equip ((?:\{[^}]+\})+)", card.get("text") or "")
    return m.group(1) if m else None


def differently_named_lands(perms) -> int:
    return len({p.name for p in perms if p.is_("Land") and not p.face_down})


NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def num(word: str) -> int:
    w = word.strip().lower()
    return int(w) if w.isdigit() else NUMBER_WORDS[w]


# ── what a spell or an enters trigger aims at. One clause, one slot; the table carries out only what it can name here. ──
#   side: "opp" (an opponent's), "own" (yours), "any" (anyone's); kinds: what kind of permanent; extra: a test the permanent must pass.
SHAPES = [
    # (shape name, regex over the text, side, kinds, player_ok)
    ("damage", re.compile(r"deals (\d+) damage to any target", re.I), "any", ("Creature", "Spacecraft"), True),
    ("damage", re.compile(r"deals (\d+) damage to target (?:attacking )?creature(?! you control)", re.I), "any", ("Creature",), False),
    ("damage-x", re.compile(r"deals twice X damage to target creature", re.I), "any", ("Creature",), False),
    # Saavik, Stoic Student
    ("damage-types", re.compile(r"deals X damage to target attacking or blocking creature an opponent controls, where X is the number of creature types among non-Borg creatures you control", re.I),
     "opp", ("Creature",), False),
    ("destroy", re.compile(r"Destroy target creature with power (\d+) or greater", re.I), "any", ("Creature",), False),
    ("destroy", re.compile(r"Destroy target nonartifact creature", re.I), "any", ("Creature",), False),
    ("destroy", re.compile(r"Destroy target creature", re.I), "any", ("Creature",), False),
    ("destroy", re.compile(r"Destroy target artifact or enchantment", re.I), "any", ("Artifact", "Enchantment"), False),
    ("destroy", re.compile(r"Destroy target artifact", re.I), "any", ("Artifact",), False),
    ("exile", re.compile(r"Exile target creature or Spacecraft", re.I), "any", ("Creature", "Spacecraft"), False),
    # Test of Priorities, Power to Life Support: out until the next end step
    ("flicker-end", re.compile(r"Exile (?:up to one )?(?:other )?target (?:creature|nonland permanent) you control(?:\.| \|\|then\|\|) Return that card to the battlefield under its owner's control at the beginning of the next end step", re.I),
     "own", ("Creature",), False),
    ("bounce", re.compile(r"Return (?:up to one )?target creature(?: an opponent controls)?(?: with mana value (\d+) or less)? to its owner's hand", re.I), "any", ("Creature",), False),
    # "Target creature gets +3/+3 and gains trample", "Target attacking creature gets +2/+0 and gains first strike", "Target Klingon gets +1/+1 and gains double strike"
    ("pump", re.compile(r"Target (?:attacking )?(?P<sub>creature|[A-Z][a-z]+)(?: you control)? gets (?P<bp>[+-]\d+)/(?P<bt>[+-]\d+)(?: and gains (?P<kw>[a-z ]+?))? until end of turn", re.I), "any", ("Creature",), False),
    ("grant", re.compile(r"Target (?:attacking )?creature(?: you control)? gains ([a-z ]+?) until end of turn", re.I), "any", ("Creature",), False),
    ("grant", re.compile(r"Target artifact or creature(?: you control)? gains ([a-z ]+?) until end of turn", re.I), "any", ("Artifact", "Creature"), False),
    ("tuck", re.compile(r"Target creature or Spacecraft's owner puts it on their choice of the top or bottom of their library", re.I), "any", ("Creature", "Spacecraft"), False),
    ("unblockable", re.compile(r"Target creature can't be blocked this turn", re.I), "any", ("Creature",), False),
    ("no-block", re.compile(r"Target creature can't block this turn", re.I), "any", ("Creature",), False),
    ("stun", re.compile(r"Tap (?:up to one )?target artifact or creature\.? (?:and put|Put) (a|two) stun counters? on it", re.I), "any", ("Artifact", "Creature"), False),
    ("stun", re.compile(r"Tap (?:up to one )?target creature(?: an opponent controls)?\.? (?:and put|Put) (a|two) stun counters? on it", re.I), "any", ("Creature",), False),
    # "Counter target spell", "… noncreature spell unless its controller pays {2}", "… spell with mana value 2 or less": aimed at a spell that is
    # held (cast, paid for, waiting to resolve): the table holds a pilot's spell while an opponent who could counter it answers
    ("counterspell", re.compile(r"Counter target (noncreature )?spell(?: with mana value (\d+) or less)?(?: unless its controller pays ((?:\{[^}]+\})+))?", re.I), "opp", (), False),
    ("damage-opp", re.compile(r"deals (\d+) damage to target opponent", re.I), "opp", (), True),
    # "Target opponent reveals their hand. You choose a nonland card from it. Exile that card." (Stray from History), "Look at target opponent's hand …"
    # (Salt Vampire, Zero, V'Ger): the caster alone is shown the hand (a private question of its own), see VirtualPlayer.reveal_begin
    ("peek-hand", re.compile(r"(?:Target opponent reveals their hand|Look at target opponent's hand)", re.I), "opp", (), True),
    # "Target player draws two cards, then discards two cards" (Wesley), "Target opponent draws a card", "Target player draws two cards"
    ("player-draw", re.compile(r"Target (player|opponent) draws (a|two|three) cards?(?: and also discards (a|two) cards?)?", re.I), "any", (), True),
    ("gy-to-library", re.compile(r"Put target ((?:[\w-]+ )?)card from your graveyard on (top|the bottom) of your library", re.I), "own", (), False),
    ("tap", re.compile(r"tap target creature an opponent controls\.?$", re.I), "opp", ("Creature",), False),
    ("tap", re.compile(r"^tap target creature\.?$", re.I), "any", ("Creature",), False),
    ("counter", re.compile(r"put (a|two) \+1/\+1 counters? on (?:another )?target creature(?: you control)?", re.I), "any", ("Creature",), False),
    ("shrink-etb", re.compile(r"target creature an opponent controls gets ([+-]\d+)/([+-]\d+) until end of turn", re.I), "opp", ("Creature",), False),
    ("untap", re.compile(r"Untap (another )?target (creature|permanent)", re.I), "any", ("Creature",), False),
    ("tutor", re.compile(r"^(you may )?search your library for (?:an? |an )?(.+?) cards?, (?:reveal it, )?put it (onto the battlefield(?: tapped)?|into your hand), then shuffle", re.I), "own", (), False),
    ("distribute", re.compile(r"Distribute two \+1/\+1 counters among one or two target creatures", re.I), "any", ("Creature",), False),
    ("fight", re.compile(r"Target creature you control fights up to one target creature an opponent controls", re.I), "own", ("Creature",), False),
    ("raise", re.compile(r"Return (up to one )?target ([\w,/' -]+?) cards?(?: with mana value (\d+) or less)? from your graveyard to (your hand|the battlefield)", re.I), "own", (), False),
    ("exile-card", re.compile(r"Exile (up to one )?target card from a graveyard", re.I), "any", (), False),
    ("exile-until", re.compile(r"Exile up to one target (nontoken )?artifact or creature", re.I), "any", ("Artifact", "Creature"), False),
    # Malfunctioning Holodeck: exiled until the source leaves the battlefield, then back under its owner's control (Perm.held, VirtualPlayer.return_exiled)
    ("exile-held", re.compile(r"Exile (?:up to one )?(?:another )?target (nonland permanent|creature|artifact|enchantment|artifact or creature) (an opponent controls )?"
                              r"until (?:this \w+|[A-Z][^.,\n]*?) leaves the battlefield", re.I), "opp", ("Creature", "Artifact", "Enchantment", "Planeswalker"), False),
    ("discard", re.compile(r"^(you may )?discard (a|two) cards?\.?(?: \|\|then\|\| .*)?$", re.I), "own", (), False),
    ("exile-hand", re.compile(r"^(you may )?exile a card from your hand\.?(?: \|\|then\|\| .*)?$", re.I), "own", (), False),
    ("sacrifice", re.compile(r"^(you may )?sacrifice (?:an? |another )(nontoken )?([\w ]+?)(?: of their choice)?\.?(?: \|\|then\|\| .*)?$", re.I), "own", (), False),
]


COLOUR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}


def card_fits(c: dict, phrase: str, mv: int | None = None) -> bool:
    """Is this card what "target <phrase> card" asks for? "creature", "green or white creature", "artifact, instant, or sorcery", "non-Doctor permanent"."""
    types = [t.lower() for t in (c.get("types") or [])] + [t.lower() for t in (c.get("subtypes") or [])] + [t.lower() for t in (c.get("supertypes") or [])]
    if mv is not None and float(c.get("manaValue") or 0) > mv:
        return False
    is_perm = any(t in types for t in ("artifact", "creature", "enchantment", "land", "planeswalker", "battle"))

    def word_ok(w):
        if w.startswith("non-") or w.startswith("non"):
            return w.replace("non-", "").replace("non", "", 1) not in types
        return is_perm if w == "permanent" else w in types
    alts = [a.strip() for a in re.split(r",? or |, |,? and/or ", phrase.lower()) if a.strip()]
    if not alts:
        return True
    last = alts[-1].split()
    if all(a in COLOUR_WORDS for a in alts[:-1]) and alts[:-1] and last[0] in COLOUR_WORDS:      # "green or white creature"
        want = {COLOUR_WORDS[a] for a in alts[:-1]} | {COLOUR_WORDS[last[0]]}
        return bool(want & set(c.get("colors") or [])) and all(word_ok(w) for w in last[1:])
    return any(all(word_ok(w) for w in a.split()) for a in alts)


def attacks_alone_banned(p) -> bool:
    """"This creature can't attack alone" / "can't attack or block alone" (a face-down card has no text)."""
    return not p.face_down and bool(re.search(r"can't attack (?:or block )?alone", p.card.get("text") or "", re.I))


def blocks_alone_banned(p) -> bool:
    return not p.face_down and bool(re.search(r"can't (?:attack or )?block alone", p.card.get("text") or "", re.I))


def kicker_of(c: dict):
    """("mana", "{1}{U}") for "Kicker {1}{U}", ("sac", "artifact or creature") for "Kicker—Sacrifice an artifact or creature", or None."""
    t = re.sub(r"\s*\([^)]*\)", "", c.get("text") or "")
    m = re.search(r"(?m)^Kicker\s*((?:\{[^}]+\})+)\s*$", t)
    if m:
        return ("mana", m.group(1))
    m = re.search(r"(?m)^Kicker\s*—\s*Sacrifice an? ([\w ]+?)\.?\s*$", t)
    return ("sac", m.group(1)) if m else None


def kicked_text(text: str, kicked: bool) -> str:
    """The spell's rules text as it is for this cast: the kicker line is only the cost; "If this spell was kicked, … instead" replaces the
    sentence before it when kicked and is dropped when not."""
    t = re.sub(r"\s*\([^)]*\)", "", text or "")
    t = re.sub(r"(?m)^Kicker.*$\n?", "", t).strip()
    m = re.search(r"\s*If this spell was kicked, that creature gets ([+-]\d+/[+-]\d+) until end of turn instead\.?", t)
    if m:
        base = t[:m.start()]
        return re.sub(r"gets [+-]\d+/[+-]\d+", f"gets {m.group(1)}", base, count=1) if kicked else base
    m = re.search(r"\s*If this spell was kicked, instead counter target spell\.?", t)
    if m:
        return "Counter target spell." if kicked else t[:m.start()]
    return t


def ward_cost(p) -> int:
    """Ward {N} on this permanent, as generic mana (0: none). The set's wards are {1} and {2}; a face-down disguised or cloaked card has ward {2}."""
    if p.face_down:
        return 2 if p.ward2 else 0
    m = re.search(r"(?mi)^(?:[A-Za-z ]+, )*ward \{(\d+)\}", re.sub(r"\s*\([^)]*\)", "", p.card.get("text") or ""))
    return int(m.group(1)) if m else 0


def type_line(p) -> str:
    """The type line a page shows for a permanent, as it IS now. A token the table made has no printed line (only `types`): it gets one
    ("Token Artifact Creature — Borg"). A Spacecraft that has become a creature says Creature. (M8: the hand page picks Station crew by the
    word Creature in this line, so a Borg token, or a stationed Oberth, could not be chosen.)"""
    card = p.card
    line = card.get("type") or ""
    if not line:
        subs = " ".join(card.get("subtypes") or [])
        line = ("Token " if p.token else "") + " ".join(card.get("types") or []) + (f" — {subs}" if subs else "")
    if not p.face_down and p.is_("Creature") and not re.search(r"\bCreature\b", line):
        line = line.replace(" —", " Creature —", 1) if " —" in line else line + " Creature"
    return line.strip()


def tapped_clause_runs(clause: str, name: str = "Something") -> bool:
    """Is this "Whenever X becomes tapped, …" clause one the engine runs by itself? Every sentence known, nothing conditional or replacing."""
    if re.match(r"(you may|until end of turn|if)\b", clause, re.I) or re.search(r"\binstead\b", clause, re.I):
        return False
    v = VirtualPlayer.__new__(VirtualPlayer)
    return VirtualPlayer.can_run(v, clause, name)


def effect_shape(text: str):
    """(shape, match, side, kinds, player_ok) for the first aimed effect this text names, or None."""
    best = None
    for shape, rx, side, kinds, pok in SHAPES:
        m = rx.search(text or "")
        if m and (best is None or m.start() < best[1].start()):
            best = (shape, m, side, kinds, pok)
    return best


_PROBE = threading.local()                  # effect_known's dry run, per thread (the server answers several requests at once)


def probing() -> bool:
    """True inside effect_known: a "could the engine do this?" read. Nothing it does may be seen outside the scratch player."""
    return getattr(_PROBE, "depth", 0) > 0


def _new_id() -> int:
    """A new permanent's number. A dry run's scratch permanents never take one from the table's counter (ids would skip, and a restored
    game's numbering would drift): they count on in their own range, per thread."""
    if probing():
        _PROBE.ids = getattr(_PROBE, "ids", 0) + 1
        return 10 ** 12 + _PROBE.ids
    return next(_ids)


DIED_THIS_TURN = [False]                    # "if a creature died this turn" (Planetary Bombardment); reset when the turn turns over
DEATH_TICK = [1]                            # moves on each turn: "this ability triggers only once each turn"
MANUAL_ZONES = ("hand", "battlefield", "graveyard", "exile", "library-top", "library-bottom")      # a person's manual moves (T10)
COUNTER_KINDS = {"+1/+1": "counters", "charge": "charge", "quest": "quest", "lore": "lore", "stun": "stun", "promotion": "promo", "loyalty": "counters"}
FOE_CREATURES = [None]                      # the server sets this: a callable(seat name) -> names of the face-up creatures the OTHER seats control (see hostile_targets)
DEATH_HOOK = [None]                         # the server sets this: a creature died on some seat, and every seat's "whenever a creature dies" hears it
STEP_KEY = [None]                           # the server sets this: a callable naming the step the game is in now. Mana added to a pool ("add {R}{R}")
                                            # empties when the step changes; with no server (an engine test) it lasts until the turn ends


# Sentences that speak of "that creature" / "it": what the sentence before them aimed at. They ride on it (" ||rider|| "), so they are applied to the
# same creature whenever the aim comes in (a pilot is asked first), and the ones that must hold before the effect (exile instead of dying) do.
RIDER = re.compile(r"(?:If that creature would die this turn, exile it instead"
                   r"|When that creature dies this turn, (?:you )?draw a card"
                   r"|(?:It|That creature) gains [a-z ]+? until end of turn"
                   r"|Then that creature gains [a-z ]+? until end of turn if creatures you control have total power \d+ or greater"
                   r"|Untap it"
                   r"|Its controller creates (?:a|two|three) \d+/\d+ [\w ]+? creature tokens?"
                   r"|If it's a creature, put a \+1/\+1 counter on it"
                   r"|Then put a \+1/\+1 counter on each creature you control with a counter on it"
                   r"|Until end of turn, it gets \+\d+/\+\d+ and becomes an? [A-Z][a-z]+)\.?$", re.I)


# "Look at a hand and choose" (the caster is shown the hand privately; see VirtualPlayer.reveal_begin). Each wording becomes ONE readable sentence, so
# the question the caster is asked cannot be split from what follows its answer:
#   exile       Stray from History: "Target opponent reveals their hand. You choose a nonland card from it. Exile that card."
#   exile-draw  Salt Vampire: "You may choose a nonland card from it. If you do, that player exiles that card, then draws a card."
#   exile-play  Zero: "You may exile a nonland card from it. For as long as that card remains exiled, its owner may play it. A spell cast this way costs {2} more."
#   draw        V'Ger: "Look at target opponent's hand, then draw a card."
HAND_PEEKS_RX = re.compile(
    r"(?:(?P<exile>Target opponent reveals their hand\.\s+You choose a nonland card from it\.\s+Exile that card\.?)"
    r"|(?P<exile_draw>Look at target opponent's hand\.\s+You may choose a nonland card from it\.\s+If you do, that player exiles that card, then draws a card\.?)"
    r"|(?P<exile_play>Look at target opponent's hand\.\s+You may exile a nonland card from it\.\s+For as long as that card remains exiled, its owner may play it\.\s+"
    r"A spell cast this way costs \{2\} more to cast\.?)"
    r"|(?P<draw>Look at target opponent's hand, then draw a card\.?))", re.I)
HAND_PEEK_WORDS = (("exile", "Look at target opponent's hand and exile a nonland card from it"),
                   ("exile_draw", "Look at target opponent's hand and make them exile a nonland card from it and draw a card"),
                   ("exile_play", "Look at target opponent's hand and exile a nonland card from it, which its owner may play for {2} more"),
                   ("draw", "Look at target opponent's hand and draw a card"))


def peek_mode(text: str) -> str:
    """Which of the HAND_PEEK_WORDS a sentence is: exile | exile-draw | exile-play | draw | look (a bare look)."""
    low = (text or "").lower()
    return ("exile-draw" if "draw a card" in low and "exile" in low else "exile-play" if "may play it for" in low or "may play for" in low
            else "exile" if "exile a nonland" in low else "draw" if "draw a card" in low else "look")


def effect_known(sentence: str, name: str = "Something", aimed: bool = True) -> bool:
    """Would the engine's effect runner carry out this one sentence? Asks the real code on a scratch player, so this answer
    (what coverage.py reports, and what `activate` refuses) cannot drift away from what it really does."""
    # A dry run, explicitly: while probing() is true the engine fires no death hook and sets no "a creature died this turn", the scratch
    # permanents take no ids from the table's counter, and everything else it touches is the scratch player's own (its own rng, outbox,
    # to-do). Reading a card's text must never be an event: a "destroy target creature" probe once told every Sarcophagus a creature died.
    _PROBE.depth = getattr(_PROBE, "depth", 0) + 1
    rnd = random.getstate()
    try:
        probe = VirtualPlayer.__new__(VirtualPlayer)
        probe.__dict__.update(name="probe", deck_name="", commander=None, rng=random.Random(0), library=[], hand=[], graveyard=[], battlefield=[],
                              turn=1, land_played=False, log=[], public_board=[], on_their_cards=[], last_cast=None, recent_draws=[], todo=[],
                              cmdr_in_zone=False, cmdr_casts=0, fair=None)
        probe.__dict__["_life"] = [20]
        me = Perm({"name": name, "types": ["Creature"], "power": "2", "toughness": "2", "text": ""}, sick=False)
        t1 = Perm({"name": "t1", "types": ["Creature", "Artifact"], "power": "5", "toughness": "5", "text": ""}, sick=False)
        t2 = Perm({"name": "t2", "types": ["Creature"], "power": "5", "toughness": "5", "text": ""}, sick=False)
        t3 = Perm({"name": "t3", "types": ["Creature", "Land"], "power": "5", "toughness": "5", "text": ""}, sick=False)
        probe.battlefield.extend([me, t1, t2, t3])
        # aimed False: a trigger, which has no moment to ask the player what it aims at
        aim = [{"perm": t1, "owner": probe}, {"perm": t2, "owner": probe}, {"perm": t3, "owner": probe}] if aimed else []
        probe.__dict__["asks"] = True              # a trigger asks its controller: only a sentence the engine could then carry out counts
        try:
            return probe._one_effect(me, sentence.strip(), aim) is not None
        except Exception:  # noqa: BLE001 — a sentence the runner chokes on is one it can't do
            return False
    finally:
        random.setstate(rnd)                       # (the engine keeps its own rngs; this is so even a stray module-level draw can't show)
        _PROBE.depth -= 1


def card_slots(card: dict, mode_text: str | None = None) -> list[dict]:
    """What the player must aim this at, read from the card's words: [{side, kinds, optional, label, player_ok, power_min, not_kinds, attacking}].
    Empty when nothing is aimed, or when the table can't carry the aimed part out: then there is nothing to ask for."""
    if mode_text is not None:
        text = mode_text
    elif "Instant" in (card.get("types") or []) or "Sorcery" in (card.get("types") or []):
        text = card.get("text") or ""
    else:
        m = re.search(r"When(?:ever)? (?!(?:A|An|Another|One|You)\b)(?![^,\n]* or another)(?:this \w+|[A-Z][^,\n]*?) enters?(?: or attacks)?, ([^\n]+)", card.get("text") or "")
        text = m.group(1) if m else ""
        if m and re.search(r"choose (one|two)", text):
            return []                                      # modal enters triggers have their own picker on /hand
    if re.search(r"One or two target creatures you control each deal damage equal to their power to target creature an opponent controls", text):
        return [{"side": "own", "kinds": ["Creature"], "optional": False, "label": "a creature of yours"},
                {"side": "own", "kinds": ["Creature"], "optional": True, "label": "and a second"},
                {"side": "opp", "kinds": ["Creature"], "optional": False, "label": "their creature"}]
    sh = effect_shape(text)
    if sh is None:
        return []
    kind, m, side, kinds, pok = sh
    is_spell_ = "Instant" in (card.get("types") or []) or "Sorcery" in (card.get("types") or [])
    if kind == "tutor":
        return [{"zone": "library", "side": "own", "phrase": re.sub(r"\s+card or\s+", " or ", m.group(2)).replace(" card", ""), "optional": bool(m.group(1)) or not is_spell_ or True,
                 "label": "from your library: " + m.group(2) + " (" + m.group(3).lower() + ")"}]
    if kind == "distribute":
        return [{"side": "any", "kinds": ["Creature"], "optional": False, "label": "a +1/+1 counter on"}, {"side": "any", "kinds": ["Creature"], "optional": True, "label": "and the other on (or both on the first)"}]
    if kind == "fight":
        return [{"side": "own", "kinds": ["Creature"], "optional": False, "label": "your creature, to fight"},
                {"side": "opp", "kinds": ["Creature"], "optional": True, "label": "and what it fights"}]
    if kind == "raise":
        dest = m.group(4).lower()
        return [{"zone": "graveyard", "side": "own", "phrase": m.group(2), "mv": int(m.group(3)) if m.group(3) else None, "optional": bool(m.group(1)) or not is_spell_,
                 "label": ("to your hand" if dest == "your hand" else "back onto the battlefield") + f": a {m.group(2)} card from your graveyard"}]
    if kind == "exile-card":
        return [{"zone": "graveyard", "side": "any", "phrase": "", "optional": bool(m.group(1)) or not is_spell_, "label": "exile a card from a graveyard"}]
    if kind == "gy-to-library":
        return [{"zone": "graveyard", "side": "own", "phrase": (m.group(1) or "").strip(), "optional": not is_spell_,
                 "label": f"a {(m.group(1) or '').strip() + ' ' if m.group(1).strip() else ''}card from your graveyard, onto the {'top' if m.group(2).lower() == 'top' else 'bottom'} of your library"}]
    if kind == "counterspell":
        return [{"zone": "stack", "side": "opp", "kinds": [], "optional": False, "noncreature": bool(m.group(1)), "mv": int(m.group(2)) if m.group(2) else None,
                 "label": "counter (a spell that is being cast)"}]
    if kind == "damage-opp":
        return [{"side": "opp", "kinds": [], "players_only": True, "player_ok": True, "optional": False, "label": "damage to (an opponent)"}]
    if kind == "peek-hand":
        return [{"side": "opp", "kinds": [], "players_only": True, "player_ok": True, "optional": False, "label": "whose hand to look at (an opponent)"}]
    if kind == "player-draw":
        return [{"side": "opp" if m.group(1).lower() == "opponent" else "any", "kinds": [], "players_only": True, "player_ok": True, "any_player": m.group(1).lower() == "player",
                 "optional": False, "label": f"the player who draws{' and discards' if m.group(3) else ''}"}]
    if kind == "exile-until":
        return [{"side": "any", "kinds": ["Artifact", "Creature"], "optional": True, "label": "exile, until a later chapter", "nontoken": bool(m.group(1))}]
    if kind == "exile-held":
        what = m.group(1).lower()
        kinds_ = {"nonland permanent": ["Creature", "Artifact", "Enchantment", "Planeswalker"], "creature": ["Creature"], "artifact": ["Artifact"], "enchantment": ["Enchantment"],
                  "artifact or creature": ["Artifact", "Creature"]}[what]
        return [{"side": "opp" if m.group(2) else "any", "kinds": kinds_, "optional": not is_spell_ or True, "label": "exile, until it leaves the battlefield",
                 **({"not_kinds": ["Land"]} if what == "nonland permanent" else {})}]
    if kind in ("discard", "exile-hand"):
        return [{"zone": "hand", "side": "own", "phrase": "", "optional": bool(m.group(1)), "label": "discard a card" if kind == "discard" else "exile a card from your hand"}]
    if kind == "sacrifice":
        what = m.group(3).strip().lower()
        tok = what.endswith(" token")
        what = what.removesuffix(" token")
        nonland = what.startswith("nonland ")
        what_ = what.removeprefix("nonland ")
        kinds_ = [w.strip().capitalize() for w in re.split(r" or |, ", what_)]
        if kinds_ == ["Permanent"]:
            kinds_ = ["Artifact", "Creature", "Enchantment", "Land"]
        if nonland:
            kinds_ = [k for k in kinds_ if k != "Land"]
        another = bool(re.match(r"(?:you may )?sacrifice another\b", m.group(0), re.I))
        return [{"side": "own", "kinds": kinds_, "optional": bool(m.group(1)), "label": f"sacrifice {('another ' if another else 'a ') + (m.group(2) or '') + what}", "token_only": tok, "nontoken": bool(m.group(2)),
                 **({"not_kinds": ["Land"]} if nonland else {}), **({"other": True} if another else {})}]
    if kind == "untap":
        return [{"side": "own" if "you control" in m.group(0).lower() or re.search(r"Untap (?:another )?target permanent you control", text, re.I) else "any",
                 "kinds": ["Creature"] if m.group(2).lower() == "creature" else ["Creature", "Artifact", "Land", "Enchantment"], "optional": not is_spell_, "label": "untap",
                 **({"other": True} if m.group(1) else {})}]
    lab = {"damage": "damage to", "damage-x": "damage to", "damage-types": "damage to (an attacking or blocking creature)", "flicker-end": "exile until the next end step", "destroy": "destroy", "exile": "exile", "bounce": "return", "pump": "give it that",
           "stun": "tap and stun", "tap": "tap", "counter": "counter on", "shrink-etb": "shrink", "grant": "give it that", "unblockable": "give it that (can't be blocked)",
           "no-block": "tap down for blocking (it can't block)", "tuck": "return to its owner's library"}[kind]
    slot = {"side": side, "kinds": [k for k in kinds if k != "Spacecraft"] if kind not in ("exile", "tuck") else list(kinds), "optional": False, "label": lab, "player_ok": pok}
    if kind == "bounce" and m.group(1):
        slot["mv_max"] = int(m.group(1))
    if kind == "pump" and m.group("sub").lower() != "creature":
        slot["subtype"] = m.group("sub")                   # "Target Klingon"
    if kind == "flicker-end":
        if "nonland permanent" in m.group(0).lower():
            slot["kinds"], slot["not_kinds"] = ["Creature", "Artifact", "Enchantment"], ["Land"]
        if re.search(r"\bother target\b", m.group(0), re.I):
            slot["other"] = True
    if kind == "destroy" and "power" in m.group(0):
        slot["power_min"] = int(m.group(1))
    if "nonartifact" in m.group(0).lower():
        slot["not_kinds"] = ["Artifact"]
    if "target attacking creature" in text:
        slot["attacking"] = True
    if "target attacking or blocking creature" in text:
        slot["in_combat"] = True                           # attacking or blocking, right now (the server knows the combat)
    if "another target" in m.group(0):
        slot["other"] = True
    if "you control" in m.group(0):
        slot["side"] = "own"
    if "an opponent controls" in m.group(0):
        slot["side"] = "opp"
    # the trigger's "up to one" and enters triggers are optional; a spell's own target is what makes it castable
    is_spell = "Instant" in (card.get("types") or []) or "Sorcery" in (card.get("types") or [])
    slot["optional"] = not is_spell or "up to one" in text
    out = [slot]
    if re.search(r"Destroy target land you control", text):
        out.append({"side": "own", "kinds": ["Land"], "optional": False, "label": "a land of yours to destroy"})
    return out


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
    charge: int = 0                         # charge counters (Station, Warp Core): engine state, not read from chat (S5)
    temp: tuple = (0, 0)                    # "until end of turn" +P/+T (an activated ability); cleared when the next turn starts
    base_override: tuple | None = None      # Mirror Entity: base power and toughness X/X until end of turn
    chosen: str | None = None               # "As this enters, choose a color" (Utopia Sprawl)
    # (Plain defaults, so a game pickled before these existed still loads: the class attribute answers.)
    damage: int = 0                         # damage marked on it this turn
    stun: int = 0                           # stun counters: "if it would untap, remove one instead"
    temp_kw: tuple = ()                     # keywords it gained until end of turn
    no_block_turn: int | None = None        # "can't be blocked this turn" (Combadge's rider): the turn number it holds for
    res_turn: int = 0                       # "if this is the second time this ability has resolved this turn": which turn …
    res_n: int = 0                          # … and how many times so far
    promo: int = 0                          # promotion counters (Beckett Mariner)
    lore: int = 0                           # Saga lore counters
    sac_at_end: bool = False                # "sacrifice it at the beginning of the next end step"
    attacked_turn: int = 0                  # "if this creature attacked this turn"
    quest: int = 0                          # quest counters (Exploratory Mission)
    exile_tick: int = 0                     # "if that creature would die this turn, exile it instead": the turn tick (DEATH_TICK) it holds for
    no_blocking_tick: int = 0               # "target creature can't block this turn": the turn tick it holds for
    temp_types: tuple = ()                  # creature types it has until end of turn ("becomes a Doctor")
    death_riders: tuple = ()                # "when that creature dies this turn, …": ((tick, who gets it, sentence, the source's name), …)
    held: tuple = ()                        # what this permanent holds in exile "until it leaves the battlefield" (Malfunctioning Holodeck): ((owner seat, card), …)
    id: int = field(default_factory=lambda: _new_id())

    @property
    def name(self): return self.card["name"]

    @property
    def types(self):
        t = self.card.get("types") or []
        th = station_threshold(self.card)
        return t + ["Creature"] if th is not None and self.charge >= th and "Creature" not in t else t

    def is_(self, t): return t in self.types

    @property
    def keywords(self):
        """Printed keywords, plus what charge counters have unlocked on a Spacecraft. A face-down card has none."""
        if self.face_down:
            return []
        base = printed_keywords(self.card)
        base += [k for k in self.temp_kw if k not in base]
        return base + [k for k in station_grants(self.card, self.charge) if k not in base] if self.charge else base


class VirtualPlayer:
    # Life lives in a one-element box so teammates can share it (Two-Headed Giant: one life total per team, 30). Every
    # `self.life -= n` in the engine keeps working; a teammate sees the same number.
    @property
    def life(self):
        box = self.__dict__.get("_life")
        return box[0] if box is not None else self.__dict__.get("life", 0)   # (a pickled game from before the box)

    @life.setter
    def life(self, v):
        box = self.__dict__.get("_life")
        if box is None:
            self.__dict__["_life"] = [v]
        else:
            box[0] = v

    def share_life(self, mates, total: int):
        """Two-Headed Giant: this player and `mates` share one life total, set to `total` (30 by the rules)."""
        box = [total]
        for m in [self, *mates]:
            m.__dict__["_life"] = box

    def __init__(self, deck_file: str, name: str, seed: int | None = None, fair_words: dict | None = None,
                 fair_online: bool = False):
        """fair_words: seal the shuffle (fair.py): the library order is fixed by a published commit, from
        local entropy plus these {player: secret word} inputs (and ANU/drand with fair_online)."""
        d = json.load(open(deck_file))
        self.deck_name = d["name"]
        # A deck with no commander (a 40-card sealed deck: 2-headed giant, draft) has commander None; every
        # command-zone path below is skipped for it. Commander decks are unchanged.
        self.commander = (d.get("commander") or [None])[0]
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
        self.cmdr_in_zone, self.cmdr_casts = self.commander is not None, 0
        # Starting life comes from the deck (startingLife); Commander is 40, a 40-card 1v1 is 20 (2-Headed Giant
        # is 30 per team, handled with team support later). A deck with no commander and no startingLife gets 20.
        self.life = int(d.get("startingLife") or (40 if self.commander is not None else 20))
        self.turn, self.land_played = 0, False
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
        doubled = 2 if any(not q.face_down and "If you would draw a card, draw two cards instead" in (q.card.get("text") or "") for q in self.battlefield) else 1
        for _ in range(n * doubled):
            if self.library:
                self.hand.append(self.library.pop())
                self.recent_draws.append(self.hand[-1]["name"])
                for q in self.battlefield:                  # Data, New Life
                    if not q.face_down and re.search(r"Whenever you draw a card, put a \+1/\+1 counter on", q.card.get("text") or ""):
                        q.counters += 1
                        self.out(kind="say", text=f"{q.name} gets a +1/+1 counter.")

    # ── impulse draw: cards exiled face up from the top of the library that their owner may play for a while ──
    # (All of this lives in the instance dict with setdefault, as plain lists: a game pickled before it existed still loads, with empty zones.)
    def exile_zone(self) -> list[dict]:
        """Cards this seat exiled face up from its own library. Public: every seat may see them (they were exiled face up)."""
        return self.__dict__.setdefault("exile", [])

    def impulse_zone(self) -> list[dict]:
        """The exiled cards it may still play: [{"card", "until": "eot" (this turn) | "next" (until the end of its next turn), "until_turn", "by"}].
        Each card is also in exile_zone() until it is played; when its window closes it just stays there."""
        return self.__dict__.setdefault("impulse", [])

    def impulse_cards(self) -> list[dict]:
        return [e["card"] for e in self.impulse_zone()]

    def exile_tax(self, c: dict) -> int:
        """Generic mana a card played from exile costs on top (Zero, Incorporeal Sensate: "a spell cast this way costs {2} more")."""
        e = next((e for e in self.impulse_zone() if e["card"] is c), None)
        return int((e or {}).get("tax") or 0)

    def impulse_card(self, name) -> dict | None:
        """A card it may play from exile right now, by name (as hand_card matches a name), or None."""
        return _match(name, self.impulse_cards(), lambda c: c["name"])

    @staticmethod
    def impulse_window(e: dict) -> str:
        return "this turn" if e["until"] == "eot" else "for as long as it stays exiled" if e["until"] == "play" else "until the end of your next turn"

    def impulse_take(self, c: dict) -> None:
        """c is being played from exile: ONE copy leaves exile. (Copies of a card in a deck are often the very same dict, so it is one entry and one
        occurrence, never every match: an expired copy of the same card stays in exile.)"""
        zone, ex = self.impulse_zone(), self.exile_zone()
        i = next((k for k, e in enumerate(zone) if e["card"] is c), None)
        if i is not None:
            zone.pop(i)
        j = next((k for k in range(len(ex) - 1, -1, -1) if ex[k] is c), None)
        if j is not None:
            ex.pop(j)

    def impulse_exile(self, n: int = 1, until_nonland: bool = False, until: str = "next", by: str = "") -> list[str]:
        """Exile from the top of the library, face up: the top n cards, or (until_nonland) one at a time until a nonland card is exiled. What may
        be played afterwards is every one of the n, or only that nonland card (the lands on the way stay in exile). until: "eot" for "this turn",
        "next" for "until the end of your next turn". Only the cards exiled are named: nothing else of the library is looked at."""
        took: list[dict] = []
        while self.library and (len(took) < n if not until_nonland else not (took and "Land" not in (took[-1].get("types") or []))):
            took.append(self.library.pop())               # library.pop() is the top
        if not took:
            return ["My library is empty: nothing to exile."]
        self.exile_zone().extend(took)
        playable = [c for c in took if "Land" not in (c.get("types") or [])][-1:] if until_nonland else took
        when = "this turn" if until == "eot" else "until the end of my next turn"
        for c in playable:
            self.impulse_zone().append({"card": c, "until": until, "until_turn": self.turn + 1 if until == "next" else self.turn, "by": by})
        names = [c["name"] for c in took]
        said = f"I exile {', '.join(names[:-1]) + ' and ' + names[-1] if len(names) > 1 else names[0]} from the top of my library"
        if not playable:
            return [said + ": no nonland card, so nothing to play."]
        return [said + f". I may play {' and '.join(c['name'] for c in playable)} {when}."]

    def impulse_expire(self, my_turn_ended: bool = False) -> list[str]:
        """A turn ended: "this turn" windows close; "until the end of your next turn" ones close at the end of that turn of its own (or, if that end
        was missed, once a later turn of its own has begun). The cards stay in exile."""
        keep, gone = [], []
        for e in self.impulse_zone():
            if e["until"] == "eot" or (my_turn_ended and self.turn >= e["until_turn"]) or self.turn > e["until_turn"]:
                gone.append(e["card"]["name"])
            else:
                keep.append(e)
        self.__dict__["impulse"] = keep
        return [f"I can no longer play {', '.join(gone)} from exile: {'it stays' if len(gone) == 1 else 'they stay'} exiled."] if gone else []

    def return_delayed(self) -> list[str]:
        """"Return that card to the battlefield under its owner's control at the beginning of the next end step": now. Its enters triggers
        happen as for any card entering."""
        said = []
        while self.__dict__.get("delayed_returns"):
            e = self.__dict__["delayed_returns"].pop(0)
            c = e["card"]
            p = Perm(c, sick="Haste" not in printed_keywords(c))
            self.battlefield.append(p)
            said.append(f"{c['name']} returns to the battlefield ({e.get('by') or 'delayed'}).")
            said += self.creature_entered(p)              # (a Spacecraft too: Zefram's two extra charge counters)
            if p.is_("Land"):
                said += self.land_entered(p)              # its own "When this land enters", and Landfall
            said += self.enter_effects(p, lambda card, cands: max(cands, key=lambda k: self.stats(k)[0]))
        return said

    def creature_types(self, exclude: str | None = None) -> set[str]:
        """The distinct creature types among its creatures (a creature's subtypes are its creature types). A creature with the excluded type
        counts for nothing ("non-Borg creatures"); a face-down one has no types."""
        out: set[str] = set()
        for p in self.creatures():
            if p.face_down:
                continue
            subs = list(p.card.get("subtypes") or []) + list(p.temp_types)
            if not subs and p.token:                       # a token the table made: its name carries its type ("Klingon Warrior token")
                subs = [w for w in p.name.replace(" token", "").split() if w[:1].isupper()]
            if exclude and exclude in subs:
                continue
            out |= set(subs)
        return out

    # ── which sources paid, and swapping one for another in the same step ──
    def paid_now(self) -> list[dict]:
        """[{"id", "color"}]: what the engine tapped to pay for something in THIS step (color None: it paid generic mana)."""
        rec = self.__dict__.get("paid") or {}
        key = STEP_KEY[0]() if STEP_KEY[0] else None
        if rec.get("key") != key or rec.get("turn") != self.turn:
            return []
        return [x for x in rec["items"] if any(p.id == x["id"] and p.tapped for p in self.battlefield)]

    def swap_payment(self, from_ref, to_ref) -> list[str]:
        """Untap a source the engine tapped to pay for something in this step, and tap another untapped source of mine that makes the same mana
        instead: every mana the first paid, the second can make (a colour for a coloured pip, anything for generic). Nothing else is ever untapped."""
        a, b = self.perm(from_ref), self.perm(to_ref)
        items = [x for x in self.paid_now() if x["id"] == a.id]
        if not items:
            raise IllegalAction(f"{self.shown(a)} didn't pay for anything in this step: only a source the table tapped for a payment just now can be swapped")
        if a is b:
            raise IllegalAction("that's the same source")
        offers = [cols for src, cols in self.sources() if src is b]
        if not offers:
            raise IllegalAction(f"{self.shown(b)} can't make mana right now (tapped, or not a mana source)")
        need = sorted(items, key=lambda x: x["color"] is None)   # coloured pips first, then generic
        left = list(offers)
        for x in need:
            hit = next((o for o in left if x["color"] is None or x["color"] in o), None)
            if hit is None:
                what = "{" + x["color"] + "}" if x["color"] else "that much mana"
                raise IllegalAction(f"{self.shown(b)} can't make {what}, which {self.shown(a)} paid: the payment would not be legal. Nothing changed")
            left.remove(hit)
        a.tapped = False
        self.spend([b])
        rec = self.__dict__["paid"]
        rec["items"] = [x for x in rec["items"] if x["id"] != a.id] + [{"id": b.id, "color": x["color"]} for x in items]
        return [f"I pay with {self.shown(b)} instead of {self.shown(a)}: {self.shown(a)} untaps."]

    # ── surveil / scry: look at the top N, put any number away (graveyard / bottom), the rest back on top IN ANY ORDER ──
    def finish_look(self, kind: str, n: int, away=None, top_order=None) -> list[str]:
        """kind "surveil" (away = into the graveyard) or "scry" (away = on the bottom). away: names among the top n; for a scry, in the order they go
        on the bottom, top to bottom. top_order: the kept cards, top first, exactly those (any order); None keeps their order. Checked whole first:
        a bad name or a wrong top_order changes nothing."""
        n = min(n, len(self.library))
        top = self.library[::-1][:n]                         # top first (the END of the list is the top)
        pool = list(top)

        def take(name, from_):
            c_ = next((c for c in from_ if c["name"].lower() == str(name).strip().lower()), None)
            if c_ is None:
                raise IllegalAction(f"{name} is not among the top {n} of your library")
            from_.remove(c_)                                  # (by equality: copies of a card are interchangeable)
            return c_
        gone = [take(x, pool) for x in (away or [])]
        kept = pool
        if top_order is not None:
            left_ = list(kept)
            try:
                kept = [take(x, left_) for x in top_order]
            except IllegalAction:
                raise IllegalAction(f"top_order must name exactly the cards you keep on top ({', '.join(c['name'] for c in pool) or 'none'}), each once")
            if left_:
                raise IllegalAction(f"top_order must name exactly the cards you keep on top ({', '.join(c['name'] for c in pool) or 'none'}), each once")
        del self.library[len(self.library) - n:]
        if kind == "scry":
            self.library[:0] = list(reversed(gone))           # the first named sits highest of the ones on the bottom
        else:
            self.graveyard.extend(gone)
        self.library.extend(reversed(kept))                   # kept[0] ends up on top
        self.__dict__["pending_" + kind] = 0
        where = "into my graveyard" if kind == "surveil" else "on the bottom"
        said = [f"I {kind} {n}" + (f" and put {len(gone)} {where}" if gone else "") + (f", and keep {len(kept)} on top" if kept else "") + "."]
        if kind == "surveil" and gone:
            said = [f"I surveil {n} and put {', '.join(c['name'] for c in gone)} into my graveyard" + (f", and keep {len(kept)} on top." if kept else ".")]
        return said

    # ── mana in the pool: what "add {R}{R}" makes. It is spent first, before anything is tapped, and it empties when the step does ──
    def floating(self) -> list:
        """The pool's mana, one Perm stand-in per mana (no permanent behind it: spend() takes it out of the pool instead of tapping it)."""
        fl = self.__dict__.get("mana_pool")
        if not fl:
            return []
        key = STEP_KEY[0]() if STEP_KEY[0] else None
        if fl["key"] != key:
            self.__dict__["mana_pool"] = None               # the step it was made in is over: mana empties
            return []
        return fl["mana"]

    def add_mana(self, colors: str) -> None:
        key = STEP_KEY[0]() if STEP_KEY[0] else None
        pool = self.floating()
        for col in colors:
            pool.append(Perm({"name": f"{{{col}}} in your mana pool", "types": [], "text": "", "mana": col}, sick=False, id=-(len(pool) + 1)))
        self.__dict__["mana_pool"] = {"key": key, "mana": pool}

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

    def hostile_targets(self) -> list[str]:
        """Names of the creatures another seat controls that an Aura meant for an opponent's creature could go on.
        2026-11-13 retro (C3): `castable` asked only `public_board`, which holds what REAL-deck players announced. At a table where every seat
        is virtual it is always empty, so Vow of Flight, Vow of Wildness and Kenrith's Transformation were never castable (38 soak games, never
        once listed). The other virtual seats' boards are the table's to know: it tells the engine through FOE_CREATURES."""
        hook = FOE_CREATURES[0]
        try:
            return list(hook(self.name)) if hook else []
        except Exception:  # noqa: BLE001 — a seat the table does not know (an engine test): nothing of theirs
            return []

    def creatures(self): return [p for p in self.battlefield if p.is_("Creature")]

    def enchantments(self): return [p for p in self.battlefield if p.is_("Enchantment")]

    def stats(self, p: Perm) -> tuple[int, int]:
        """Current power/toughness with counters, Auras and Roles attached to it."""
        def printed(v):
            try:
                return int(v or 0)
            except ValueError:                   # '*': the card's own line sets it below, else 0 rather than a guess
                return 0
        pw, tg = printed(p.card.get("power")), printed(p.card.get("toughness"))
        if not p.face_down:                      # Collective Drone, Captain Pike, The Traveler: */… set by their own text
            tx = p.card.get("text") or ""
            m = re.search(r"power (and toughness are each|is) equal to the number of (differently named lands|artifact creatures) you control", tx)
            if m:
                n = differently_named_lands(self.battlefield) if m.group(2).startswith("diff") else \
                    len([q for q in self.creatures() if q.is_("Artifact")])
                pw = n
                if m.group(1).startswith("and"):
                    tg = n
        if p.face_down:
            pw, tg = 2, 2
        if p.base_override:
            pw, tg = p.base_override
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
        enchanted = any(a.attached_to == p.id and not is_equipment(a.card) for a in self.battlefield)
        for src in self.battlefield:
            if src.face_down:
                continue
            # A charge line counts only once reached (Deep Space 9's "9+ | Creatures you control get +3/+3"); a one-shot is no anthem
            # ("{8}: Creatures you control get +2/+0 until end of turn"); "Artifact creatures" is not every creature (Assault Drone).
            for line in active_text(src.card, src.charge).split("\n"):
                if ": " in line.split("creatures you control")[0].split("Creatures you control")[0] or "until end of turn" in line:
                    continue
                for m in re.finditer(r"(Other |Enchanted |Artifact )?[Cc]reatures you control get \+(\d+)/\+(\d+)", line):
                    word = (m.group(1) or "").strip()
                    if (word == "Other" and src is p) or (word == "Enchanted" and not enchanted) or (word == "Artifact" and not p.is_("Artifact")):
                        continue
                    pw, tg = pw + int(m.group(2)), tg + int(m.group(3))
        return pw + p.temp[0], tg + p.temp[1]

    def kws(self, p: Perm, my_turn: bool = False) -> list[str]:
        """Every keyword this permanent has right now: its own (Perm.keywords), what its Equipment and Auras give it, and what its
        controller's other permanents give it ("Artifact creatures you control have vigilance"). my_turn: it is its controller's turn."""
        out = list(p.keywords)
        if p.face_down:
            return out
        for a in self.battlefield:
            if a.attached_to == p.id:
                for m in re.finditer(r"(?:Equipped|Enchanted) creature has ([a-z ,]+?)\.", a.card.get("text") or ""):
                    for w in re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(1)):
                        k = next((kw for kw in EVERY_KEYWORD if kw.lower() == w.strip()), None)
                        if k and k not in out:
                            out.append(k)
        for src in self.battlefield:
            if src.face_down:
                continue
            for line in active_text(src.card, src.charge).split("\n"):
                m = re.match(r"(Artifact )?[Cc]reatures you control( with counters on them)? have ([a-z ,]+?)\.?$", line.strip())
                if not m or (m.group(1) and not p.is_("Artifact")) or (m.group(2) and not (p.counters or p.charge or p.stun)):
                    continue
                for w in re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(3)):
                    k = next((kw for kw in EVERY_KEYWORD if kw.lower() == w.strip()), None)
                    if k and k not in out:
                        out.append(k)
        if my_turn:
            m = re.search(r"During your turn, this creature has ([a-z]+)", p.card.get("text") or "")
            if m:
                k = next((kw for kw in EVERY_KEYWORD if kw.lower() == m.group(1)), None)
                if k and k not in out:
                    out.append(k)
        return out

    # ── the table's own outbox: what a card did to somebody else (the server knows who the opponents are and who owns their life) ──
    def out(self, **item):
        self.__dict__.setdefault("outbox", []).append(item)
        if item.get("kind") == "life" and item.get("delta", 0) > 0 and item.get("player") == self.name:
            self.gained_life(item["delta"])

    def gained_life(self, n: int):
        """Dr. Crusher, Neelix: "whenever you gain life". Called for whatever changed this seat's life upward, once per gain."""
        for q in self.battlefield:
            if not q.face_down and re.search(r"Whenever you gain life, put a \+1/\+1 counter on", q.card.get("text") or ""):
                q.counters += 1
                self.__dict__.setdefault("outbox", []).append({"kind": "say", "text": f"{q.name} gets a +1/+1 counter."})

    def step_triggers(self, pattern: str) -> list[str]:
        """"At the beginning of your upkeep / each upkeep / your end step / each end step / your first main phase": the trigger's sentences through the shared
        engine; a condition it knows is checked first; anything else says so at the table."""
        said = []
        for p in list(self.battlefield):
            if p.face_down:
                continue
            for line in active_text(p.card, p.charge).split("\n"):
                m = re.match(rf"(?:[\w ]+ — )?At the beginning of {pattern}, (.+)$", re.sub(r"\s*\([^)]*\)", "", line).strip())
                if not m or "choose one" in m.group(1):
                    continue
                eff = m.group(1)
                c = re.match(r"if this creature attacked this turn, (.+)$", eff)
                if c:
                    if p.attacked_turn != self.turn:
                        continue
                    eff = c.group(1)
                if re.match(r"(if|target creature|you may)\b", eff, re.I) and not self.can_run(eff, p.name):
                    self.todo.append(f"{p.name}: {eff}")
                    continue
                done, left = self.run_effects(p, eff)
                said += [f"{p.name}: {x}" for x in done]
                self.todo += [f"{p.name}: {x}" for x in left]
        return said

    def spacecraft_entered(self, p: Perm) -> list[str]:
        """T13: "Spacecraft you control enter with two additional charge counters" (Zefram Cochrane), for a Spacecraft that came in any way the
        table runs (put, search), not only cast. (Cast and the other ways already go through creature_entered, which does the same.)"""
        said = []
        if "Spacecraft" not in (p.card.get("subtypes") or []):
            return said
        for q in self.battlefield:
            if not q.face_down and "Spacecraft you control enter with two additional charge counters" in (q.card.get("text") or ""):
                p.charge += 2
                said.append(f"{q.name}: {p.name} enters with two extra charge counters.")
        return said

    def creature_entered(self, p: Perm) -> list[str]:
        """A creature of this seat's has just entered: "enters with an additional counter" (Deanna Troi), Zefram's charge on a Spacecraft, Sickbay's life, Kira's pump."""
        said = []
        for q in self.battlefield:
            if q.face_down:
                continue
            t = q.card.get("text") or ""
            if q is not p and "Other creatures you control enter with an additional +1/+1 counter" in t and p.is_("Creature"):
                p.counters += 1
                said.append(f"{q.name}: {self.shown(p)} enters with a +1/+1 counter.")
            if "Spacecraft you control enter with two additional charge counters" in t and "Spacecraft" in (p.card.get("subtypes") or []):
                p.charge += 2
                said.append(f"{q.name}: {p.name} enters with two extra charge counters.")
            if q is not p and "Whenever another creature you control enters, it gets +1/+1 until end of turn" in t and p.is_("Creature"):
                p.temp = (p.temp[0] + 1, p.temp[1] + 1)
                said.append(f"{q.name}: {self.shown(p)} gets +1/+1 until end of turn.")
            if "Whenever a creature you control enters, you gain 1 life" in t and p.is_("Creature"):
                self.life += 1
                self.out(kind="life", player=self.name, delta=1)
                said.append(f"{q.name}: I gain 1 life.")
        return said

    def left_graveyard(self) -> list[str]:
        """A card just left this seat's graveyard (Vash, Hologram Stalker, Blastoneuron, Sela)."""
        said = []
        for q in list(self.battlefield):
            if q.face_down:
                continue
            m = re.search(r"Whenever (?:a card|one or more cards) leaves? your graveyard, ([^\n]+?)(?: This ability triggers only once each turn\.)?$", q.card.get("text") or "", re.M)
            if not m:
                continue
            if "only once each turn" in (q.card.get("text") or ""):
                if q.res_turn == -DEATH_TICK[0]:
                    continue
                q.res_turn = -DEATH_TICK[0]
            done, left = self.run_effects(q, m.group(1))
            said += [f"{q.name}: {x}" for x in done]
            self.todo += [f"{q.name}: {x}" for x in left]
        return said

    def drain(self) -> list[dict]:
        box, self.__dict__["outbox"] = self.__dict__.get("outbox") or [], []
        return box

    def spend(self, pay):
        """Pay with these sources: each is tapped; a Treasure is sacrificed for its mana."""
        pool = self.floating()
        plan = self.__dict__.get("_plan") or {}
        if plan.get("pay") is pay and not probing():          # what each tapped source paid, for a swap later in this step
            key = STEP_KEY[0]() if STEP_KEY[0] else None
            rec = self.__dict__.get("paid") or {}
            if rec.get("key") != key or rec.get("turn") != self.turn:
                rec = {"key": key, "turn": self.turn, "items": []}
            rec["items"] += [{"id": src.id, "color": col} for src, col in plan["assign"] if src.id >= 0 and src in self.battlefield
                             and not (src.token and src.name.startswith("Treasure"))]
            self.__dict__["paid"] = rec
        for pm in {id(x_): x_ for x_ in pay}.values():
            if any(pm is f for f in pool):                 # mana in the pool: spent, not tapped
                pool[:] = [f for f in pool if f is not pm]
                continue
            pm.tapped = True
            if pm.token and pm.name.startswith("Treasure") and pm in self.battlefield:
                self.battlefield.remove(pm)

    def deal_damage(self, p: Perm, n: int, deathtouch: bool = False, source: str = "") -> list[str]:
        """Damage to a creature of this seat: marked until end of turn; lethal destroys it (not if indestructible)."""
        if n <= 0 or p not in self.battlefield:
            return []
        p.damage += n
        said = [f"{source + ' deals' if source else 'It takes'} {n} damage to {self.shown(p)}."]
        return said + self.check_dead(p, deathtouch)

    def destroy_saved(self, p: Perm) -> list[str] | None:
        """Dr. McCoy, Surly Surgeon: "If another creature you control with a +1/+1 counter on it would be destroyed, instead remove all +1/+1 counters
        from it and all damage already dealt to it is healed." Called wherever a creature of this seat WOULD BE DESTROYED (lethal or deathtouch damage,
        a destroy effect, a wipe; not a sacrifice, not toughness 0). Returns what is said when the replacement applies (the counters and the damage
        are already gone) or None. A creature the counters were holding up (toughness 0 or less without them) still dies: that is not destruction."""
        if probing() or p.face_down or not p.is_("Creature") or p.counters <= 0 or p not in self.battlefield:
            return None
        mc = next((q for q in self.battlefield if q is not p and not q.face_down and MCCOY_RX.search(q.card.get("text") or "")), None)
        if mc is None:
            return None
        n, dmg = p.counters, p.damage
        p.counters, p.damage = 0, 0
        return [f"{mc.name}: {self.shown(p)} would be destroyed; instead {n} +1/+1 counter{'s' if n != 1 else ''} come off it and its {dmg} damage is healed."]

    def check_dead(self, p: Perm, deathtouch: bool = False) -> list[str]:
        """State-based: toughness 0 or less dies, indestructible or not; lethal damage destroys unless it is indestructible."""
        if p not in self.battlefield or not p.is_("Creature"):
            return []
        tg = self.stats(p)[1]
        if tg <= 0 or ((p.damage >= tg or (deathtouch and p.damage > 0)) and "Indestructible" not in self.kws(p)):
            name = self.shown(p)
            if tg > 0:                                     # destroyed by damage (not by having no toughness): Dr. McCoy's replacement may apply
                saved = self.destroy_saved(p)
                if saved is not None:
                    return saved + (self.check_dead(p) if self.stats(p)[1] <= 0 else [])
            instead = bool(p.exile_tick and p.exile_tick == DEATH_TICK[0] and not probing())
            self.move(f"#{p.id}", "graveyard", cause="damage" if tg > 0 else "state")
            return [f"{name} would die: it is exiled instead."] if instead else [f"{name} dies."]
        return []

    def add_counters(self, p: Perm, n: int) -> None:
        p.counters += n

    def saga_chapters(self, card: dict) -> list[tuple[list[int], str]]:
        out = []
        for line in (card.get("text") or "").split("\n"):
            m = re.match(r"((?:I{1,3}|IV|V)(?:, (?:I{1,3}|IV|V))*) — (.+)$", line.strip())
            if m:
                out.append(([{"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}[x.strip()] for x in m.group(1).split(",")], m.group(2)))
        return out

    def saga_tick(self, p: Perm) -> list[str]:
        """A lore counter on this Saga: it enters with one, and gets one more at the start of each of its controller's main phases. The chapter with that
        number happens, sentence by sentence; what the table can't do says so. After its last chapter it's sacrificed."""
        p.lore += 1
        chs = self.saga_chapters(p.card)
        last = max((n for ns, _ in chs for n in ns), default=0)
        said = [f"{p.name}: lore counter {p.lore}."]
        for ns, text in chs:
            if p.lore in ns:
                done, left = self.run_effects(p, text)
                said += [f"{p.name}: {x}" for x in done]
                for x in left:
                    self.todo.append(f"{p.name}, chapter {p.lore}: {x}")
        if p.lore >= last and p in self.battlefield:
            self.move(f"#{p.id}", "graveyard")
            said.append(f"{p.name} is done: it goes to the graveyard.")
        return said

    def face_dilemma(self) -> list[str]:
        """"You face a dilemma as you choose one or more modes for a spell or ability." Every "Whenever you face a dilemma" on this seat hears it, and the turn's
        count is kept for the end-step cards (Sisko, The Caretaker). A trigger on a card in the graveyard (Gabriel Lorca) is a choice with a cost: said, by hand."""
        if self.__dict__.get("dilemma_turn") != self.turn:
            self.__dict__["dilemma_turn"], self.__dict__["dilemmas"] = self.turn, 0
        self.__dict__["dilemmas"] += 1
        said = []
        for p in list(self.battlefield):
            if p.face_down:
                continue
            m = re.search(r"Whenever you face a dilemma, ([^\n]+)", p.card.get("text") or "")
            if m:
                done, left = self.run_effects(p, m.group(1))
                said += [f"{p.name}: {x}" for x in done]
                self.todo += [f"{p.name}: {x}" for x in left]
        for c in self.graveyard:
            m = re.search(r"Whenever you face a dilemma, ([^\n]+)", c.get("text") or "")
            if m:
                self.todo.append(f"{c['name']} (in your graveyard): {re.sub(r'\s*\([^)]*\)', '', m.group(1))}")
        return said

    def land_token(self, name: str, ability: str, tapped: bool = True) -> list[str]:
        """A land token (New Planet: "{T}: Add one mana of any color."): it taps for mana like any land, and it is a land entering."""
        tok = {"name": f"{name} token", "type": "Token Land — Planet", "types": ["Land"], "subtypes": ["Planet"], "text": ability, "keywords": []}
        p = Perm(tok, token=True, tapped=tapped, sick=False)
        self.battlefield.append(p)
        return [f"I create a {name} land token" + (", tapped." if tapped else ".")] + self.land_entered(p)

    def artifact_token(self, kind: str, n: int = 1) -> list[str]:
        """Treasure, Food, Clue: artifacts with their standard ability."""
        ability = {"Treasure": "{T}, Sacrifice this artifact: Add one mana of any color.",
                   "Food": "{2}, {T}, Sacrifice this artifact: You gain 3 life.",
                   "Clue": "{2}, Sacrifice this artifact: Draw a card."}[kind]
        for _ in range(n):
            tok = {"name": f"{kind} token", "type": f"Token Artifact — {kind}", "types": ["Artifact"], "subtypes": [kind], "text": ability, "keywords": []}
            self.battlefield.append(Perm(tok, token=True, sick=False))
        return [f"I create {'a' if n == 1 else n} {kind} token{'s' if n != 1 else ''}."]

    def land_entered(self, land: Perm, play: bool = False) -> list[str]:
        """A land came in under this seat's control: its own enters text (unless play_land already did it), then every Landfall on its board, one by one,
        in the card's own words."""
        said = [] if play else self.land_enters(land)
        for q in list(self.battlefield):
            if q is land or q.face_down or q not in self.battlefield:
                continue
            m = re.search(r"(?:Landfall — )?Whenever a land you control enters, ([^\n]+)", q.card.get("text") or "")
            if m:
                said += self.landfall(q, m.group(1))
        return said

    def landfall(self, q: Perm, effect: str) -> list[str]:
        """One Landfall resolving on q. The second-resolution rider counts resolutions per permanent per turn (Burnham, Jake Sisko)."""
        if q.res_turn != self.turn:
            q.res_turn, q.res_n = self.turn, 0
        q.res_n += 1
        # The whole ability, sentence by sentence (Exploratory Mission's four), not only its first: what the table can't do is said as a to-do.
        # The second-resolution rider is the one sentence that waits for its condition.
        m = re.search(r"(?:^|(?<=\.)\s+)If this is the second time this ability has resolved this turn, (.+?)\.?$", effect.strip())
        main = effect.strip()[:m.start()] if m else effect.strip()
        said, leftover = self.run_effects(q, main, label="Landfall: ") if main.strip() else ([], [])
        if m and q.res_n == 2:
            more, left2 = self.run_effects(q, m.group(1), label="Landfall, the second this turn: ")
            said += more
            leftover += left2
        for x in leftover:
            self.todo.append(f"{q.name} Landfall: {x}")
        return said

    # ── one engine for "what this sentence does": spells, enters triggers, Landfall, activated abilities all come through here ──
    @staticmethod
    def _sentences(text: str) -> list[str]:
        """One effect per item. "Draw two cards, then exile a card from your hand" is two; "Sacrifice X. If you do, Y" is one, with Y riding on the choice."""
        out = []
        # "Look at the top N cards … Put one into your hand and the other into your graveyard / You may reveal a … card from among them and put it
        # into your hand. Put the rest on the bottom …" and "Mill four cards. You may put a permanent card from among them into your hand": one effect
        text = re.sub(r"(Look at the top (?:\w+) cards of your library)\. (Put one of them into your hand and the (?:other|rest) into your graveyard)", r"\1 ||and|| \2", text, flags=re.I)
        text = re.sub(r"(Look at the top (?:\w+) cards of your library)\. (You may reveal an? [^.]+? from among them and put it into your hand)\. (Put the rest on the bottom(?: of your library)? in (?:a random|any) order)",
                      r"\1 ||and|| \2 ||and|| \3", text, flags=re.I)
        # Hoshi Sato: "look at the top X cards …, where X is the number of creature types among non-Borg creatures you control. Put one of them into your hand and
        # the rest on the bottom of your library in a random order" (not "may": one is taken)
        text = re.sub(r"(look at the top X cards of your library, where X is the number of creature types among non-Borg creatures you control)\. (Put one of them into your hand and the rest on the bottom(?: of your library)? in (?:a random|any) order)",
                      r"\1 ||and|| \2", text, flags=re.I)
        text = re.sub(r"(Mill (?:\w+) cards)\. (You may put an? [^.]+? card from among them into your hand)", r"\1 ||and|| \2", text, flags=re.I)
        # Vengeance Mission: "Target opponent loses 1 life. That player loses 2 life instead if there are seven or more quest counters on this enchantment."
        text = re.sub(r"Target opponent loses (\d+) life\.\s+That player loses (\d+) life instead if there are (\w+) or more quest counters on (?:this \w+|it)",
                      r"Target opponent loses \1 life, or \2 life with \3 or more quest counters", text, flags=re.I)
        text = HAND_PEEKS_RX.sub(lambda m: next(w for g, w in HAND_PEEK_WORDS if m.group(g)), text)
        for raw in re.split(r"(?<=[.])\s+|\n", text):
            raw = raw.strip()
            if re.match(r"(?:If|When) you do, ", raw) and out and re.search(r"\b(sacrifice|discard|exile a card from your hand)\b", out[-1], re.I):
                out[-1] = out[-1].rstrip(".") + " ||then|| " + raw[len("If you do, "):].lstrip()     # ("When you do, " is one longer: the strip takes its space)
                if raw.startswith("When you do, "):
                    out[-1] = out[-1][:out[-1].index(" ||then|| ")] + " ||then|| " + raw[len("When you do, "):]
            elif RIDER.match(raw) and out and re.search(r"\btarget\b", out[-1], re.I):
                out[-1] = out[-1].rstrip(".") + " ||rider|| " + raw.rstrip(".")   # said of the creature the sentence before aimed at: it waits for that aim
            elif re.match(r"Put (a|two) stun counters? on it\.?$", raw) and out and re.search(r"\bTap (?:up to one )?target\b", out[-1], re.I):
                out[-1] = out[-1].rstrip(".") + ". " + raw                           # "Tap target creature. Put two stun counters on it." is one effect
            elif re.match(r"Return that card to the battlefield under its owner's control at the beginning of the next end step\.?$", raw) \
                    and out and re.search(r"\bexile (?:up to one )?(?:other )?target (?:creature|nonland permanent) you control\.?$", out[-1], re.I):
                out[-1] = out[-1].rstrip(".") + " ||then|| " + raw      # one effect: it goes, and comes back at the next end step
            elif re.match(r"Put (?:that card|those (?:\w+ )?cards|them) (?:onto the battlefield|into your hand)\b", raw) \
                    and out and re.search(r"\breveal cards from the top of your library until you reveal [^.|]*\.?$", out[-1], re.I):
                out[-1] = out[-1].rstrip(".") + " ||then|| " + raw      # "Reveal ... until you reveal N land cards. Put those ... and the rest on the bottom": one effect
            elif re.match(r"Sacrifice (?:those lands|that land|them|it) at the beginning of (?:your|the) next end step\.?$", raw) \
                    and out and re.search(r"\breveal cards from the top of your library until you reveal [^|]* \|\|then\|\| ", out[-1], re.I):
                out[-1] = out[-1].rstrip(".") + " ||then|| " + raw      # (Will Riker: the lands it found go at the end step)
            elif re.match(r"(?:Until the end of your next turn, you may play (?:those cards|that card|them)|You may play (?:that card|those cards|them) this turn)\.?$", raw) \
                    and out and re.search(r"\bexile (?:the top|cards from the top)\b", out[-1], re.I):
                out[-1] = out[-1].rstrip(".") + " ||then|| " + raw     # impulse draw: the exile and what may be played from it are one effect
            else:
                raw = re.sub(r"(Target (?:player|opponent) draws (?:a|two|three) cards?), then (discards (?:a|two) cards?)", r"\1 and also \2", raw)   # one player, one effect
                out += [x.strip() for x in re.split(r",? then (?=discard|exile a card|draw|add )", raw) if x.strip()]
        return out

    @staticmethod
    def _plain(text: str) -> str:
        """Reminder text off, and two sentences that are really one: "deals 2 damage to target creature. It deals 4 … instead if a creature died this turn"."""
        text = re.sub(r"\s*\([^)]*\)", "", text or "")
        return re.sub(r"deals (\d+) damage to target creature\. [^.]*? deals (\d+) damage to that creature instead if a creature died this turn\.",
                      lambda m: f"deals {m.group(2) if DIED_THIS_TURN[0] else m.group(1)} damage to target creature.", text)

    def _subject(self, src: Perm | None) -> str:
        return self.shown(src) if src is not None else (self.__dict__.get("spell_name") or "This")

    def run_effects(self, src: Perm | None, text: str, aim=None, label: str = "") -> tuple[list[str], list[str]]:
        """Carry out each sentence of `text` the table knows; return (what was said, the sentences it could not do). `aim` is
        [{'perm': Perm, 'owner': VirtualPlayer}, {'player': name}, ...], already checked legal, consumed left to right."""
        sents = []
        for raw in self._sentences(self._plain(text)):
            sent = re.sub(r"^[^.—]{1,40} — ", "", raw.strip()).strip()   # "Battle Stations — Put a counter…": the name off
            if not sent or sent.startswith("Beam me up") or re.fullmatch(r"Station.*", sent):
                continue
            sents.append(sent)
        return self._run_list(src, sents, list(aim or []), label)

    def _waiting(self) -> tuple:
        """What the seat owes an answer to IN THE MIDDLE of resolving: a surveil / scry (it decides with the cards in front of it, and the next
        sentence must see the result). A parked target choice is not here: a target is chosen before its effect resolves, the effect's other
        sentences don't depend on it (Emergency Medical Hologram's life comes at once, as before)."""
        return (self.__dict__.get("pending_surveil", 0), self.__dict__.get("pending_scry", 0))

    def _run_list(self, src, sents: list, aim: list, label: str = "") -> tuple[list[str], list[str]]:
        """The sentences in order. A sentence that leaves the seat a question (surveil 2: which cards; a target to name) STOPS the effect there:
        the rest wait on the seat (deferred) and run right after it answers, in order (live bug: "Surveil 2, then draw a card" drew before the
        surveil was answered, so the draw took a card that should have been surveilled). Plain data: survives a snapshot."""
        said, left = [], []
        for i, sent in enumerate(sents):
            before = self._waiting()
            out = self._one_effect(src, sent, aim)
            if out is None:
                left.append(sent)
            else:
                said += [label + x for x in out]
            rest = sents[i + 1:]
            if rest and not probing() and any(b > a for a, b in zip(before, self._waiting())):
                self.__dict__.setdefault("deferred", []).append({"src": src, "sentences": rest, "aim": aim, "label": label,
                                                                 "who": src.name if src is not None else "That"})
                break
        return said, left

    def resume_deferred(self) -> list[str]:
        """The seat has answered: what was waiting behind its answer runs now, in order (until the next question, if one comes up)."""
        said = []
        while self.__dict__.get("deferred") and not any(self._waiting()):
            d = self.__dict__["deferred"].pop(0)
            done, left = self._run_list(d["src"], d["sentences"], d["aim"], d["label"])
            said += done
            self.todo += [f"{d['who']}: {x}" for x in left]
        return said

    def can_run(self, text: str, name: str = "Something") -> bool:
        """Would run_effects carry out every sentence of this text? Asked before anything is paid, and read by coverage.py."""
        text = self._plain(text)
        for raw in self._sentences(text):
            sent = re.sub(r"^[^.—]{1,40} — ", "", raw.strip()).strip()
            if sent and not effect_known(sent, name):
                return False
        return True

    def _one_effect(self, src, s: str, aim) -> list[str] | None:
        """One sentence, with whatever rides on its target (RIDER). The riders are carried into the aimed effect (_aimed), and a question the
        sentence parks keeps them: they are applied when the answer comes."""
        if " ||rider|| " not in s:
            return self._one_effect0(src, s, aim)
        base, *riders = s.split(" ||rider|| ")
        pend0 = [x["id"] for x in self.__dict__.get("pending_choices") or []]
        keep = self.__dict__.get("riders")
        self.__dict__["riders"] = riders
        try:
            out = self._one_effect0(src, base, aim)
        finally:
            self.__dict__["riders"] = keep or []
        for it in self.__dict__.get("pending_choices") or []:
            if it["id"] not in pend0 and it.get("text") == base.rstrip(".").strip():
                it["text"] = s.rstrip(".").strip()
        return out

    def _riders(self, when: str, p, owner, who: str) -> list[str]:
        """Apply the riders of the sentence being carried out to what it aimed at. when "pre": before the effect (it may kill); "post": after."""
        said = []
        for r in self.__dict__.get("riders") or []:
            low = r.lower().rstrip(".")
            if when == "pre":
                if "would die this turn, exile it instead" in low:
                    p.exile_tick = DEATH_TICK[0]
                elif "dies this turn" in low:
                    p.death_riders = (*p.death_riders, (DEATH_TICK[0], self, "draw a card", who))
                continue
            m = re.fullmatch(r"its controller creates (a|two|three) (\d+)/(\d+) ([\w ]+?) creature tokens?", low)
            if m:                                           # (it may be gone by now: its controller is still its controller)
                said += [f"{owner.name}: " + x.replace("I create", "creates") for x in owner.token_phrase(num(m.group(1)), m.group(2), m.group(3), re.search(r"\d+/\d+ ([\w ]+?) creature", r, re.I).group(1))]
                continue
            if low == "then put a +1/+1 counter on each creature you control with a counter on it":
                hits = [q for q in self.creatures() if q.counters > 0]
                for q in hits:
                    q.counters += 1
                said.append(f"A +1/+1 counter on each creature I control with a counter on it ({len(hits)}).")
                continue
            if p not in owner.battlefield:
                continue
            name = owner.shown(p)
            m = re.fullmatch(r"until end of turn, it gets \+(\d+)/\+(\d+) and becomes an? ([a-z]+)", low)
            if m:
                p.temp = (p.temp[0] + int(m.group(1)), p.temp[1] + int(m.group(2)))
                p.temp_types = (*p.temp_types, m.group(3).capitalize())
                said.append(f"{name} gets +{m.group(1)}/+{m.group(2)} and is a {m.group(3).capitalize()} until end of turn.")
                continue
            m = re.fullmatch(r"(?:it|that creature) gains ([a-z ]+?) until end of turn", low)
            g = re.fullmatch(r"then that creature gains ([a-z ]+?) until end of turn if creatures you control have total power (\d+) or greater", low)
            if m or g:
                kw = next((k for k in EVERY_KEYWORD if k.lower() == (m or g).group(1)), None)
                total = sum(self.stats(q)[0] for q in self.creatures())
                if g and total < int(g.group(2)):
                    said.append(f"(Total power {total}: no {g.group(1)}.)")
                elif kw:
                    p.temp_kw = (*p.temp_kw, kw)
                    said.append(f"{name} gains {kw.lower()} until end of turn" + (f" (total power {total})." if g else "."))
            elif low == "untap it":
                p.tapped = False
                said.append(f"{name} untaps.")
            elif low == "if it's a creature, put a +1/+1 counter on it" and p.is_("Creature"):
                p.counters += 1
                said.append(f"{name} gets a +1/+1 counter.")
        return said

    def _one_effect0(self, src, s: str, aim) -> list[str] | None:
        """One sentence. None: the table can't do it (the caller says so at the table). Never half-does a sentence it can't finish."""
        t = s.rstrip(".").strip()
        low = t.lower()
        who = self._subject(src)
        # ── you ──
        m = re.fullmatch(r"(?:you )?draw (a|an|one|two|three) cards?", low)
        if m:
            n = num(m.group(1))
            self.draw(n)
            return [f"I draw {n} card{'s' if n != 1 else ''}."]
        m = re.fullmatch(r"you draw (a|one|two) cards? and lose (\d+) life", low)
        if m:
            n = num(m.group(1))
            self.draw(n)
            self.life -= int(m.group(2))
            self.out(kind="life", player=self.name, delta=-int(m.group(2)))
            return [f"I draw {n} card{'s' if n != 1 else ''} and lose {m.group(2)} life."]
        m = re.fullmatch(r"you gain (\d+) life", low)
        if m:
            self.life += int(m.group(1))
            self.out(kind="life", player=self.name, delta=int(m.group(1)))
            return [f"I gain {m.group(1)} life."]
        m = re.fullmatch(r"you lose (\d+) life", low)
        if m:
            self.life -= int(m.group(1))
            self.out(kind="life", player=self.name, delta=-int(m.group(1)))
            return [f"I lose {m.group(1)} life."]
        # ── impulse draw: exile from the top of the library, and the window in which those cards may be played ──
        m = re.fullmatch(r"(?:then )?exile (the top card|the top (two|three|four|five) cards|cards from the top) of your library( until you exile a nonland card)?"
                         r" \|\|then\|\| (until the end of your next turn, you may play (?:those cards|that card|them)|you may play (?:that card|those cards|them) this turn)\.?", low)
        if m and (m.group(3) or m.group(1) != "cards from the top"):
            n = 1 if m.group(1) == "the top card" else num(m.group(2)) if m.group(2) else 1
            return self.impulse_exile(n, until_nonland=bool(m.group(3)), until="next" if m.group(4).startswith("until") else "eot",
                                      by=src.name if src is not None else "")
        # ── "Reveal cards from the top of your library until you reveal N <kind> card(s). Put those ... and the rest on the bottom in a random order." ──
        # 2026-11-13 retro: U.S.S. Voyager, Lone Wanderer entered and the table said "By hand"; the host did it with twelve manual moves. The set's
        # cards with this wording: U.S.S. Voyager (two lands, onto the battlefield tapped), Will Riker, First in the Field (two lands, onto the
        # battlefield, sacrificed at the end step), Janice Rand, Essential Aide (a land, tapped) and Pavel Chekov, First Posting (a card of a chosen
        # kind, into the hand). Every revealed card is named in the public log (a reveal is public). Lands that arrive this way enter like any
        # other land (their own enters text, then Landfall: land_entered). The rest go to the bottom in an order nobody at the table can know
        # (this seat's own generator, the same one that shuffles its library). Onto the battlefield is done for LAND cards only: another kind of
        # permanent has enters triggers this sentence has no way to ask about, so that stays by hand rather than half-done.
        m = re.fullmatch(r"(?:then )?reveal cards from the top of your library until you reveal (a|an|one|two|three|four|five) ((?:non)?[a-z]+) cards?"
                         r" \|\|then\|\| put (?:that card|those (?:[a-z]+ )?cards|them) (onto the battlefield( tapped)?|into your hand)"
                         r" and (?:put )?(?:the rest|all other cards revealed this way) on the bottom of your library in a random order"
                         r"( \|\|then\|\| sacrifice (?:those lands|that land|them|it) at the beginning of (?:your|the) next end step)?", low)
        if m and (m.group(3) == "into your hand" or m.group(2) == "land"):
            want, kind, to_field = num(m.group(1)), m.group(2), m.group(3) != "into your hand"
            shown, found = [], []
            while self.library and len(found) < want:
                c = self.library.pop()                      # (the end of the list is the top)
                shown.append(c)
                if card_fits(c, kind):
                    found.append(c)
            rest = [c for c in shown if not any(c is f for f in found)]
            self.rng.shuffle(rest)
            for c in rest:
                self.library.insert(0, c)
            what = f"{kind} card" + ("" if want == 1 else "s")
            said = [(f"{who}: I reveal cards from the top of my library until I reveal {m.group(1) if want > 1 else ('an' if kind[0] in 'aeiou' else 'a')} {what}: "
                     + ", ".join(c["name"] for c in shown) + ".") if shown else f"{who}: my library is empty: nothing to reveal."]
            if shown and len(found) < want:
                said.append(f"That was my whole library: {len(found)} {kind} card{'s' if len(found) != 1 else ''}, not {want}.")
            entered = []
            for c in found:
                if to_field:
                    q = Perm(c, sick=False, tapped=bool(m.group(4)))
                    q.sac_at_end = bool(m.group(5))
                    self.battlefield.append(q)
                    entered.append(q)
                else:
                    self.hand.append(c)
            if found:
                names = ", ".join(c["name"] for c in found)
                said.append(f"I put {names} " + (f"onto the battlefield{' tapped' if m.group(4) else ''}" if to_field else "into my hand")
                            + (f" and the other {len(rest)} on the bottom of my library in a random order." if rest else ".")
                            + (f" I sacrifice {'them' if len(found) > 1 else 'it'} at the beginning of my next end step." if m.group(5) and to_field else ""))
            elif rest:
                said.append(f"They all go on the bottom of my library in a random order ({len(rest)}).")
            for q in entered:                               # each land's own enters text ("enters tapped", a Planet's life), then Landfall, one land at a time
                if q in self.battlefield:
                    said += self.land_entered(q)
            return said
        m = re.fullmatch(r"put (a|one|two|three) quest counters? on (?:this \w+|it)", low)
        if m and src is not None:
            src.quest += num(m.group(1))
            return [f"{who} gets {'a quest counter' if num(m.group(1)) == 1 else f'{num(m.group(1))} quest counters'} ({src.quest})."]
        m = re.fullmatch(r"target opponent loses (\d+) life, or (\d+) life with (\w+) or more quest counters", low)
        if m and src is not None:                          # Vengeance Mission: the table takes it off the opponent's life (server: target-opponent)
            need = int(m.group(3)) if m.group(3).isdigit() else num(m.group(3))
            n = int(m.group(2)) if src.quest >= need else int(m.group(1))
            if not probing():
                self.out(kind="target-opponent", n=n, source=who, verb="loses")
            return [f"{who}: target opponent loses {n} life" + (f" ({src.quest} quest counters)." if src.quest >= need else ".")]
        m = re.fullmatch(r"if this \w+ has (one|two|three|four|five|six|seven|\d+) or more quest counters on it, (.+)", t, re.I)
        if m and src is not None:
            need = int(m.group(1)) if m.group(1).isdigit() else num(m.group(1).lower())
            if src.quest >= need:
                inner = self._one_effect(src, m.group(2), aim)
                return None if inner is None else [f"{who} has {src.quest} quest counters: " + " ".join(inner)]
            return [] if effect_known(m.group(2), src.name) else None       # not yet: nothing happens (but only a rider the table could carry out counts)
        m = re.fullmatch(r"add ((?:\{[wubrgc]\})+)", low)
        if m:
            cols = "".join(re.findall(r"\{([wubrgc])\}", m.group(1))).upper()
            self.add_mana(cols)
            return [f"I add {''.join('{' + c_ + '}' for c_ in cols)} to my mana pool."]
        m = re.fullmatch(r"(surveil|scry) (\d+)", low)
        if m:
            key = "pending_surveil" if m.group(1) == "surveil" else "pending_scry"
            self.__dict__[key] = self.__dict__.get(key, 0) + int(m.group(2))
            return [f"I {m.group(1)} {m.group(2)}."]
        if low == "investigate":
            return self.artifact_token("Clue")
        if low == "investigate twice":
            return self.artifact_token("Clue", 2)
        m = re.fullmatch(r"create an? (food|treasure) token", low)
        if m:
            return self.artifact_token(m.group(1).capitalize())
        m = re.fullmatch(r"create a tapped planet land token named ([\w ]+?) with \"(.+)\"", t, re.I)
        if m:
            return self.land_token(m.group(1), m.group(2), tapped=True)
        m = re.fullmatch(r"create (a|two|three|four) (\d+)/(\d+) ([\w ,'-]+?) creature tokens?( with haste)?( with \"[^\"]*\")?", t, re.I)
        if m:
            return self.token_phrase(num(m.group(1)), m.group(2), m.group(3), m.group(4), bool(m.group(5)), (m.group(6) or "").strip())
        if low == "put a +1/+1 counter on each creature you control":
            for q in self.creatures():
                q.counters += 1
            return [f"A +1/+1 counter on each creature I control ({len(self.creatures())})."]
        m = re.fullmatch(r"creatures you control get \+(\d+)/\+(\d+) until end of turn", low)
        if m:
            for q in self.creatures():
                q.temp = (q.temp[0] + int(m.group(1)), q.temp[1] + int(m.group(2)))
            return [f"My creatures get +{m.group(1)}/+{m.group(2)} until end of turn."]
        m = re.fullmatch(r"mill (a|one|two|three|four|five|six) cards?", low)
        if m:
            return self.mill(num(m.group(1)))
        m = re.fullmatch(r"(create .+?) and draw (a|two|three) cards?", t, re.I)
        if m:                                              # "Create a 1/1 red Officer creature token and draw two cards" (Make It So)
            first = self._one_effect0(src, m.group(1), aim)
            if first is None:
                return None
            self.draw(num(m.group(2)))
            return first + [f"I draw {num(m.group(2))} card{'s' if num(m.group(2)) != 1 else ''}."]
        m = re.fullmatch(r"if this spell is the first spell you've cast this game, (.+)", t, re.I)
        if m:                                              # First Contact
            if not effect_known(m.group(1)):
                return None
            return self._one_effect0(src, m.group(1), aim) if self.__dict__.get("casts_game", 0) <= 1 else []
        if " ||and|| " in t:
            got = self._look(src, t)
            if got is not None:
                return got
        m = re.fullmatch(r"then return an? (land|creature|artifact) card from your graveyard to your hand", low)
        if m:                                              # (not "target": chosen as it resolves). What it couldn't do is remembered for "If you can't, …"
            have = [c_ for c_ in self.graveyard if card_fits(c_, m.group(1))]
            self.__dict__["last_cant"] = not have
            if not have:
                return [f"No {m.group(1)} card in my graveyard."]
            if self.__dict__.get("asks") and not probing():
                return self.ask_choice(src, f"Return target {m.group(1)} card from your graveyard to your hand")
            c_ = have[-1]
            self.graveyard.remove(c_)
            self.hand.append(c_)
            return [f"{c_['name']} returns to my hand."] + self.left_graveyard()
        m = re.fullmatch(r"if you can't, (.+)", t, re.I)
        if m:
            if not effect_known(m.group(1), src.name if src is not None else "Something"):
                return None
            if not self.__dict__.get("last_cant"):
                return []
            self.__dict__["last_cant"] = False
            return self._one_effect0(src, m.group(1), aim)
        m = re.fullmatch(r"(?:.+?) deals (\d+) damage to each opponent and each creature your opponents control", low) \
            or re.fullmatch(r"(?:.+?) deals (\d+) damage to each creature and each player", low)
        if m:                                              # Khan, Despot by Design; Encounter the Unknown's second mode
            n_ = int(m.group(1))
            everyone = "each creature and each player" in low
            said_ = [f"{who} deals {n_} to each " + ("creature and each player." if everyone else "opponent and each creature they control.")]
            if everyone:
                for q in list(self.creatures()):
                    said_ += self.deal_damage(q, n_, source=who)
                self.out(kind="each-player", n=n_, source=who, verb="deals")
            else:
                self.out(kind="each-opponent", n=n_, source=who, verb="deals")
            self.out(kind="others-run", sentence=f"each creature you control is dealt {n_} damage", source=who, opponents_only=not everyone)
            return said_
        m = re.fullmatch(r"each creature you control is dealt (\d+) damage", low)
        if m:                                              # (the table's own words for the far side of a mass damage effect)
            said_ = []
            for q in list(self.creatures()):
                said_ += self.deal_damage(q, int(m.group(1)), source=who if src is not None else "It")
            return said_ or ["I control no creatures."]
        if low == "draw a card if you control a permanent with a counter on it":
            if any(q.counters or q.charge or q.quest or q.lore or q.stun or q.promo for q in self.battlefield):
                self.draw()
                return ["I control a permanent with a counter on it: I draw a card."]
            return ["I control no permanent with a counter on it: no card."]
        m = re.fullmatch(r"creatures you control get ([+-]\d+)/([+-]\d+) until end of turn", low)
        if m:
            said_ = [f"My creatures get {m.group(1)}/{m.group(2)} until end of turn."]
            for q in list(self.creatures()):
                q.temp = (q.temp[0] + int(m.group(1)), q.temp[1] + int(m.group(2)))
                said_ += self.check_dead(q)
            return said_
        m = re.fullmatch(r"all other creatures get ([+-]\d+)/([+-]\d+) until end of turn", low)
        if m:                                              # mine here; every other seat's through the table (it runs the same sentence there)
            said_ = [f"All other creatures get {m.group(1)}/{m.group(2)} until end of turn."]
            for q in list(self.creatures()):
                if q is not src:
                    q.temp = (q.temp[0] + int(m.group(1)), q.temp[1] + int(m.group(2)))
                    said_ += self.check_dead(q)
            self.out(kind="others-run", sentence=f"creatures you control get {m.group(1)}/{m.group(2)} until end of turn", source=who)
            return said_
        m = re.fullmatch(r"each player sacrifices an? ((?:nontoken )?[\w ]+?) of their choice", low)
        if m:
            mine = self._one_effect0(src, f"sacrifice a {m.group(1)}", [])
            if mine is None:
                return None
            self.out(kind="each-opponent-choice", sentence=f"sacrifice a {m.group(1)}", source=who)
            return [f"Each player sacrifices a {m.group(1)}."] + mine
        m = re.fullmatch(r"add (one|two|three) mana of any (?:one )?color", low) or re.fullmatch(r"add ((?:\{[wubrgc]\}(?:, | or |, or )?)+)", low)
        if m and (" or " in low or "any" in low):
            n_ = num(m.group(1)) if "any" in low else 1
            cols = list(COLORS) if "any" in low else [c_.upper() for c_ in re.findall(r"\{([wubrgc])\}", low)]
            if self.__dict__.get("asks") and not probing():
                return self.ask_option(src, s, [{"label": "{" + c_ + "}" * 1 + (f" ×{n_}" if n_ > 1 else ""), "do": {"kind": "mana", "colors": c_ * n_}} for c_ in cols],
                                       label="which colour of mana")
            c_ = self.missing_color() if self.missing_color() in cols else cols[0]
            self.add_mana(c_ * n_)
            return [f"I add {''.join('{' + x + '}' for x in c_ * n_)} to my mana pool."]
        if src is not None:
            m = re.fullmatch(r"double (.+?)'s power until end of turn", low)
            if m:
                pw_ = self.stats(src)[0]
                src.temp = (src.temp[0] + pw_, src.temp[1])
                return [f"{who}'s power is doubled until end of turn ({pw_} → {self.stats(src)[0]})."]
        # ── this permanent ──
        if src is not None:
            # A legend goes by part of its name in its own text ("Captain Burnham" on Captain Michael Burnham): any of its own name's words will do.
            own_words = set(re.sub(r"[^\w' ]", " ", src.name.lower()).split())
            is_me = lambda ph: ph in ("this creature", "it", "this spacecraft", "this artifact") or (bool(ph.split()) and set(ph.split()) <= own_words)
            m = re.fullmatch(r"put (a|two|three) \+1/\+1 counters? on (.+)", low)
            if m and not is_me(m.group(2)):
                m = None
            if m:
                src.counters += num(m.group(1))
                return [f"{who} gets {'a +1/+1 counter' if num(m.group(1)) == 1 else f'{num(m.group(1))} +1/+1 counters'}."]
            m = re.fullmatch(r"(.+?) gets \+(\d+)/\+(\d+) until end of turn", low)
            if m and is_me(m.group(1)):
                src.temp = (src.temp[0] + int(m.group(2)), src.temp[1] + int(m.group(3)))
                return [f"{who} gets +{m.group(2)}/+{m.group(3)} until end of turn."]
            if low in ("untap this creature", "untap it"):
                src.tapped = False
                return [f"{who} untaps."]
            m = re.fullmatch(r"put (a|two|three) charge counters? on (?:it|this spacecraft|this artifact)", low)
            if m:
                src.charge += num(m.group(1))
                return [f"{who} gets {num(m.group(1))} charge counter{'s' if num(m.group(1)) != 1 else ''} ({src.charge})."]
        m = re.fullmatch(r"(?:search your library for|you may search your library for) an? basic (plains|island|swamp|mountain|forest) card, put it onto the battlefield tapped, then shuffle", low)
        if m:
            nm = m.group(1).capitalize()
            if not any(c["name"] == nm for c in self.library):
                return [f"I search my library: no {nm}. I shuffle."] if not self.rng.shuffle(self.library) else []
            return self.search_library(nm, to="battlefield", tapped=True)
        m = re.fullmatch(r"you lose (\d+) life and draw (a|one|two) cards?", low)
        if m:
            self.life -= int(m.group(1))
            self.out(kind="life", player=self.name, delta=-int(m.group(1)))
            self.draw(num(m.group(2)))
            return [f"I lose {m.group(1)} life and draw {num(m.group(2))} card{'s' if num(m.group(2)) != 1 else ''}."]
        m = re.fullmatch(r"then discard a card unless this \w+ has (one|two|three|four|five|six|seven|\d+) or more quest counters on it", low)
        if m and src is not None:
            need = int(m.group(1)) if m.group(1).isdigit() else num(m.group(1))
            if src.quest >= need:
                return [f"{who} has {src.quest} quest counters: no discard."]
            return self.ask_choice(src, "discard a card") if self.__dict__.get("asks") else self._engine_picks(src, effect_shape("discard a card"), "discard a card")
        m = re.fullmatch(r"then discard a card unless you control (five|six|seven) or more differently named lands", low)
        if m:
            if differently_named_lands(self.battlefield) >= num(m.group(1)):
                return [f"I control {num(m.group(1))} or more differently named lands: no discard."]
            return self.ask_choice(src, "discard a card") if self.__dict__.get("asks") else self._engine_picks(src, effect_shape("discard a card"), "discard a card")
        m = re.fullmatch(r"draw x cards, where x is the number of differently named lands you control", low)
        if m:
            n = differently_named_lands(self.battlefield)
            self.draw(n)
            return [f"I draw {n} card{'s' if n != 1 else ''}."]
        m = re.fullmatch(r"you gain life equal to the number of differently named lands you control", low)
        if m:
            n = differently_named_lands(self.battlefield)
            self.life += n
            self.out(kind="life", player=self.name, delta=n)
            return [f"I gain {n} life."]
        m = re.fullmatch(r"create ([\w' -]+), a legendary (\d+)/(\d+) [\w ]+? creature token( with indestructible)?", t, re.I)
        if m:                                              # Porthos, Murf
            tok = {"name": f"{m.group(1)} token", "types": ["Creature"], "subtypes": [], "supertypes": ["Legendary"], "power": m.group(2), "toughness": m.group(3),
                   "text": "", "keywords": ["Indestructible"] if m.group(4) else []}
            self.battlefield.append(Perm(tok, token=True))
            return [f"I create {m.group(1)}, a legendary {m.group(2)}/{m.group(3)} token."]
        m = re.fullmatch(r"create a tapped (\d+)/(\d+) ([\w ,'-]+?) creature token", t, re.I)
        if m:
            said_ = self.token_phrase(1, m.group(1), m.group(2), m.group(3))
            self.battlefield[-1].tapped = True
            return said_
        if low == "create a token that's a copy of this creature" and src is not None:
            self.battlefield.append(Perm(dict(src.card), token=True))
            return [f"I create a token copy of {src.name}."]
        m = re.fullmatch(r"put a \+1/\+1 counter on each ([\w'-]+) you control", low)
        if m:
            hits = [q for q in self.creatures() if m.group(1) in [x.lower() for x in (q.card.get("subtypes") or [])] or m.group(1) in q.name.lower()]
            for q in hits:
                q.counters += 1
            return [f"A +1/+1 counter on each {m.group(1).capitalize()} I control ({len(hits)})."]
        m = re.fullmatch(r"artifact creatures you control gain ([a-z ]+?) until end of turn", low)
        if m:
            k = next((kw for kw in EVERY_KEYWORD if kw.lower() == m.group(1)), None)
            if k:
                for q in self.creatures():
                    if q.is_("Artifact"):
                        q.temp_kw = (*q.temp_kw, k)
                return [f"My artifact creatures gain {m.group(1)} until end of turn."]
        if src is not None:
            m = re.fullmatch(r"(?:this creature|[\w' ]+?) gains ([a-z ]+?) until end of turn", low)
            if m:
                k = next((kw for kw in EVERY_KEYWORD if kw.lower() == m.group(1)), None)
                if k:
                    src.temp_kw = (*src.temp_kw, k)
                    return [f"{who} gains {m.group(1)} until end of turn."]
        m = re.fullmatch(r"you gain life equal to the greatest power among creatures you control", low)
        if m:
            n = max([self.stats(q)[0] for q in self.creatures()] + [0])
            self.life += n
            self.out(kind="life", player=self.name, delta=n)
            return [f"I gain {n} life."]
        m = re.fullmatch(r"you draw (two|three) cards and each player loses (\d+) life", low)
        if m:
            self.draw(num(m.group(1)))
            self.out(kind="each-player", n=int(m.group(2)), source=who, verb="loses")
            return [f"I draw {num(m.group(1))} cards. Each player loses {m.group(2)} life."]
        if low == "each player draws a card":
            self.out(kind="each-player-draws")
            return ["Each player draws a card."]
        m = re.fullmatch(r"(?:this saga|.+?) deals (\d+) damage to each player", low)
        if m:
            self.out(kind="each-player", n=int(m.group(1)), source=who, verb="deals")
            return [f"{who} deals {m.group(1)} to each player."]
        m = re.fullmatch(r"target opponent discards (a card|two cards)", low)
        if m:                                              # at a two-player table, the one opponent; the server asks them, once per card
            for _ in range(2 if "two" in m.group(1) else 1):
                self.out(kind="each-opponent-choice", sentence="discard a card", source=who, first_only=True)
            return [f"Target opponent discards {m.group(1)}."]
        m = re.fullmatch(r"each opponent (discards a card|sacrifices an? (?:nontoken )?[\w ]+? of their choice)", low)
        if m:                                              # theirs to choose: the server asks each of them
            verb = m.group(1)
            sentence = "discard a card" if verb.startswith("discards") else re.sub(r"^sacrifices", "sacrifice", verb)
            self.out(kind="each-opponent-choice", sentence=sentence, source=who)
            return [f"Each opponent {verb}."]
        if low == "return the exiled card to the battlefield under its owner's control" and src is not None:
            back = list(src.__dict__.get("linked") or [])
            src.__dict__["linked"] = []
            said = []
            for owner, card in back:
                q = Perm(card, sick=True)
                owner.battlefield.append(q)
                said.append(f"{card['name']} returns to the battlefield.")
            return said or ["Nothing was exiled with it."]
        # ── the others (the server knows who they are, and whose life it is to change) ──
        m = re.fullmatch(r"(.+?) deals? (\d+) damage to each opponent", t, re.I) or re.fullmatch(r"()each opponent (?:loses) (\d+) life", t, re.I)
        if m:
            self.out(kind="each-opponent", n=int(m.group(2)), source=who, verb="deals" if m.group(1) else "loses")
            return [f"{who} deals {m.group(2)} to each opponent." if m.group(1) else f"Each opponent loses {m.group(2)} life."]
        m = re.fullmatch(r"each opponent loses (\d+) life and you gain (\d+) life", low)
        if m:
            self.out(kind="each-opponent", n=int(m.group(1)), source=who, verb="loses")
            self.life += int(m.group(2))
            self.out(kind="life", player=self.name, delta=int(m.group(2)))
            return [f"Each opponent loses {m.group(1)} life and I gain {m.group(2)}."]
        # ── aimed at a permanent or a player ──
        if re.search(r"each deal damage equal to their power to target creature", t, re.I):
            return self._bite(aim)
        if re.search(r"Distribute two \+1/\+1 counters among one or two target creatures", t, re.I) and aim:
            picks = [a_ for a_ in aim if "perm" in a_]
            aim.clear()
            if not picks:
                return ["No target."]
            if len(picks) == 1:
                picks = picks * 2
            for a_ in picks:
                a_["perm"].counters += 1
            return [f"{a_['owner'].shown(a_['perm'])} gets a +1/+1 counter." for a_ in picks]
        if re.search(r"Target creature you control fights up to one target creature an opponent controls", t, re.I) and len(aim) >= 2:
            me_, foe_ = aim.pop(0), aim.pop(0)
            if "perm" not in foe_ or me_["perm"] not in me_["owner"].battlefield or foe_["perm"] not in foe_["owner"].battlefield:
                return ["Nothing to fight."]
            p_, q_ = me_["owner"].stats(me_["perm"])[0], foe_["owner"].stats(foe_["perm"])[0]
            dt1, dt2 = "Deathtouch" in me_["owner"].kws(me_["perm"], my_turn=True), "Deathtouch" in foe_["owner"].kws(foe_["perm"])
            return (foe_["owner"].deal_damage(foe_["perm"], p_, deathtouch=dt1, source=me_["owner"].shown(me_["perm"]))
                    + me_["owner"].deal_damage(me_["perm"], q_, deathtouch=dt2, source=foe_["owner"].shown(foe_["perm"])))
        m = re.fullmatch(r"(?:.+? )?destroy target land you control", t, re.I)
        if m:
            if not aim or "perm" not in aim[0]:
                return None
            a = aim.pop(0)
            return a["owner"].move(f"#{a['perm'].id}", "graveyard")
        shape = effect_shape(t)
        if shape is None:
            return None
        if shape[0] in ("discard", "exile-hand", "sacrifice") and not aim and not self.__dict__.get("asks"):
            return self._engine_picks(src, shape, t)        # an engine-run seat picks for itself, and says so
        if not aim:
            # A trigger aimed at nothing yet. A seat somebody drives (a pilot: a person, or a bot through its key) is ASKED: the effect waits in its
            # pending list and the table waits on the answer, as for an aimed spell. Only an engine-run AI seat has no way to answer: for a +1/+1
            # counter on its own creature it takes its biggest and says which; anything else aimed stays by hand for it.
            if self.__dict__.get("asks"):
                if shape[0] == "discard" and num(shape[1].group(2)) > 1:
                    return [x for _ in range(num(shape[1].group(2))) for x in self.ask_choice(src, "discard a card")]
                return self.ask_choice(src, t)
            friendly = shape[0] == "counter" or (shape[0] == "pump" and not shape[1].group("bp").startswith("-")) or shape[0] in ("grant", "unblockable")
            if friendly and src is not None and self.creatures():
                pool = [c for c in self.creatures() if c is not src] if "another target" in t else self.creatures()
                if "attacking creature" in t:
                    pool = [c for c in pool if c.attacked_turn == self.turn]
                if not pool:
                    return None
                best = max(pool, key=lambda k: self.stats(k)[0])
                return [f"(No target named: {self.shown(best)}, my biggest creature.)"] + self._aimed(src, shape, t, {"perm": best, "owner": self})
            if shape[0] == "peek-hand":                     # an engine seat: the (first) opponent's hand
                return self._aimed0(src, shape, t, {"player": None})
            if shape[0] == "damage-opp":                    # an engine seat: the opponent (the first, at a bigger table)
                self.out(kind="each-opponent", n=int(shape[1].group(1)), source=who, verb="deals", first_only=True)
                return [f"{who} deals {shape[1].group(1)} to an opponent."]
            return None
        return self._aimed(src, shape, t, aim.pop(0))

    def token_phrase(self, n, power, toughness, words, haste=False, granted=""):
        """"a 2/1 red Klingon Warrior creature token with haste": the name keeps the creature type (it names the art), not the colours.
        "artifact" makes it an artifact creature; a quoted ability ("This token can't block") goes into its text."""
        ws = words.replace(",", " ").split()
        kind = " ".join(w for w in ws if w.lower() not in ("white", "blue", "black", "red", "green", "colorless", "and", "artifact", "legendary"))
        types = (["Artifact"] if any(w.lower() == "artifact" for w in ws) else []) + ["Creature"]
        tok = {"name": f"{kind or ws[-1]} token", "types": types, "subtypes": kind.split(), "power": str(power), "toughness": str(toughness),
               "text": granted.strip().strip('"').replace('with "', "").strip('"'), "keywords": ["Haste"] if haste else []}
        for _ in range(n):
            self.battlefield.append(Perm(dict(tok), token=True, sick=not haste))
        return [f"I create {n if n != 1 else 'a'} {power}/{toughness} {kind or ws[-1]} token{'s' if n != 1 else ''}" + (" with haste." if haste else ".")]

    def _bite(self, aim) -> list[str] | None:
        """"One or two target creatures you control each deal damage equal to their power to target creature an opponent controls"
        (Common Goal): one-sided, so the biters take nothing. If a biter or the target has gone by the time it resolves, the rest still counts."""
        if len(aim) < 2:
            return None
        foe = aim.pop()
        biters = list(aim)
        aim.clear()
        said = []
        for a in biters:
            if foe["perm"] not in foe["owner"].battlefield:
                break
            if a["perm"] in a["owner"].battlefield:
                said += foe["owner"].deal_damage(foe["perm"], a["owner"].stats(a["perm"])[0],
                                                 deathtouch="Deathtouch" in a["owner"].kws(a["perm"], my_turn=True),
                                                 source=a["owner"].shown(a["perm"]))
        return said

    def _aimed(self, src, shape, text, a) -> list[str]:
        """The aimed effect, with the riders of its sentence around it (see RIDER), and the last thing aimed at remembered."""
        who = self._subject(src)
        p, owner = a.get("perm"), a.get("owner")
        if p is None or owner is None or p not in owner.battlefield or shape[0] == "sacrifice":
            return self._aimed0(src, shape, text, a)
        self.__dict__["last_target"] = (p, owner)
        self._riders("pre", p, owner, who)
        said = self._aimed0(src, shape, text, a)
        return said + self._riders("post", p, owner, who)

    def _aimed0(self, src, shape, text, a) -> list[str]:
        kind, m = shape[0], shape[1]
        who = self._subject(src)
        if kind == "peek-hand":                            # "Look at target opponent's hand …": the table shows it to THIS seat only (reveal_begin)
            if "player" not in a:
                return ["(look)"] if a else self._aimed0(src, shape, text, {"player": None})
            if probing():
                return ["(look)"]
            self.out(kind="peek-hand", target=a["player"], mode=peek_mode(text), source=who)
            return []                                      # (the table says who looked, and at whose hand: server.reveal_hand)
        if kind == "counterspell":
            o = a.get("owner")
            if "spell" not in a or o is None or o.__dict__.get("held_spell") is None or o.__dict__["held_spell"]["card"] is not a["spell"]:
                return [f"{who}: that spell is no longer there to counter."]
            if m.group(3):                                  # "unless its controller pays {N}": theirs to answer
                return [f"{who}: {o.name}'s {a['spell']['name']} is countered unless {o.name} pays {m.group(3)}."] + o.tax_or_counter(m.group(3), self.name)
            return o.counter_held(self.name)
        if "card" in a:                                    # a card in a zone, already checked legal
            c, o = a["card"], a["owner"]
            if kind == "raise":
                o.graveyard.remove(c)
                left_ = o.left_graveyard()
                if m.group(4).lower() == "your hand":
                    o.hand.append(c)
                    return [f"{c['name']} returns to my hand."] + left_
                q = Perm(c, sick="Haste" not in printed_keywords(c))
                o.battlefield.append(q)
                lands_ = o.land_entered(q) if q.is_("Land") else []        # a land back from the graveyard enters like any land (R10)
                return [f"{c['name']} returns to the battlefield."] + left_ + lands_ + o.enter_effects(q, lambda card, cands: max(cands, key=lambda k: o.stats(k)[0]))
            if kind == "tutor":
                o.library.remove(c)
                to_field = m.group(3).lower().startswith("onto")
                said = []
                if to_field:
                    q = Perm(c, sick=not printed_keywords(c) or "Haste" not in printed_keywords(c), tapped="tapped" in m.group(3).lower())
                    if q.is_("Land"):
                        q.sick = False
                    o.battlefield.append(q)
                    said.append(f"I search my library, put {c['name']} onto the battlefield" + (", tapped" if q.tapped else "") + ", and shuffle.")
                    if q.is_("Land"):
                        said += o.land_entered(q)
                else:
                    o.hand.append(c)
                    if "reveal it" in m.group(0).lower():     # only a card the text has you reveal is named: otherwise it's a hidden card going to a hand
                        said.append(f"I search my library, reveal {c['name']}, put it into my hand, and shuffle.")
                    else:
                        said.append("I search my library, put a card into my hand, and shuffle.")
                o.rng.shuffle(o.library)
                return said
            if kind == "gy-to-library":
                o.graveyard.remove(c)
                top = m.group(2).lower() == "top"
                o.library.append(c) if top else o.library.insert(0, c)
                return [f"{c['name']} goes from my graveyard to the {'top' if top else 'bottom'} of my library."] + o.left_graveyard()
            if kind == "exile-card":
                o.graveyard.remove(c)
                return [f"{c['name']} is exiled from {'my' if o is self else o.name + chr(39) + 's'} graveyard."] + o.left_graveyard()
            if kind in ("discard", "exile-hand"):
                o.hand.remove(c)
                if kind == "discard":
                    o.graveyard.append(c)
                said = [f"I discard {c['name']}."] if kind == "discard" else ["I exile a card from my hand."]
                if " ||then|| " in text:
                    more, left = o.run_effects(src, text.split(" ||then|| ", 1)[1])
                    said += more
                    o.todo += [f"{who}: {x}" for x in left]
                return said
        if kind == "sacrifice":
            p, o = a["perm"], a["owner"]
            name = o.shown(p)
            o.move(f"#{p.id}", "graveyard", cause="sacrifice")
            said = [f"I sacrifice {name}."]
            if " ||then|| " in text:
                more, left = o.run_effects(src, text.split(" ||then|| ", 1)[1])
                said += more
                o.todo += [f"{who}: {x}" for x in left]
            return said
        if kind == "exile-held":
            p, o = a["perm"], a["owner"]
            name = o.shown(p)
            if src is None or src not in self.battlefield:      # it left before its trigger resolved: nothing is exiled, and so nothing waits to come back
                return [f"{who} has left the battlefield: {name} is not exiled."]
            if p not in o.battlefield:
                return [f"{name} has already gone: nothing happens."]
            card, token = p.card, p.token
            o.move(f"#{p.id}", "exile")
            self.note_other(o, f"{who} exiles your {name}" + ("" if token else " until it leaves the battlefield") + ".")
            if token:
                return [f"{name} is exiled (a token: it is gone for good)."]
            if o.commander is not None and card["name"] == o.commander["name"]:
                return [f"{name} is exiled; its owner's commander goes to the command zone instead."]
            o.on_their_cards[:] = [x for x in o.on_their_cards if x != f"{p.name} (exiled)"]
            o.exile_zone().append(card)                          # face up, in its owner's exile: everyone may see where it is
            src.held = (*src.held, (o, card))
            return [f"{name} is exiled until {who} leaves the battlefield."]
        if kind == "exile-until":
            p, o = a["perm"], a["owner"]
            name = o.shown(p)
            card = p.card
            token = p.token
            o.move(f"#{p.id}", "exile")
            if src is not None and not token:
                src.__dict__.setdefault("linked", []).append((o, card))
            self.note_other(o, f"{who} exiles your {name}.")
            return [f"{name} is exiled."]
        if "player" in a and kind == "player-draw":        # "Target player draws two cards, then discards two cards"
            n_, d_ = num(m.group(2)), (num(m.group(3)) if m.group(3) else 0)
            sents = [f"draw {m.group(2).lower()} card{'s' if n_ != 1 else ''}"] + ["discard a card"] * d_
            if a["player"] == self.name:
                said = []
                for x in sents:
                    said += self._one_effect0(src, x, []) or []
                return said
            self.out(kind="player-run", player=a["player"], sentences=sents, source=who)
            return [f"{a['player']} draws {n_} card{'s' if n_ != 1 else ''}" + (f", then discards {d_}." if d_ else ".")]
        if "player" in a:                                  # "any target", aimed at a player
            self.out(kind="one-player", player=a["player"], n=int(m.group(1)), source=who)
            return [f"{who} deals {m.group(1)} to {a['player']}."]
        p, owner = a["perm"], a["owner"]
        name = owner.shown(p)
        if p not in owner.battlefield:
            return [f"{name} has already gone: nothing happens."]
        if kind == "damage":
            return owner.deal_damage(p, int(m.group(1)), source=who)
        if kind == "damage-x":
            return owner.deal_damage(p, 2 * int(self.__dict__.get("cast_x") or 0), source=who)
        if kind == "damage-types":                         # X: the creature types among MY non-Borg creatures (the source included, if it's still here)
            kinds_ = self.creature_types(exclude="Borg")
            x = len(kinds_)
            return [f"X is {x} ({', '.join(sorted(kinds_)) or 'no creature types'})."] + (owner.deal_damage(p, x, source=who) if x else [f"{who} deals no damage."])
        if kind == "destroy":
            if "Indestructible" in owner.kws(p):
                return [f"{name} is indestructible."]
            said = owner.move(f"#{p.id}", "graveyard", destroy=True, cause="destroy")
            if p in owner.battlefield:                     # Dr. McCoy's replacement: it was not destroyed
                return said
            self.note_other(owner, f"{who} destroys your {name}.")
            return [f"{name} is destroyed."]
        if kind == "flicker-end":                          # out now; back under its owner's control at the beginning of the next end step
            token = p.token
            owner.move(f"#{p.id}", "exile")
            if not token:
                owner.__dict__.setdefault("delayed_returns", []).append({"card": p.card, "by": who})
            return [f"{name} is exiled" + (" (a token: it's gone for good)." if token else "; it returns at the beginning of the next end step.")]
        if kind == "exile":
            owner.move(f"#{p.id}", "exile")
            self.note_other(owner, f"{who} exiles your {name}.")
            return [f"{name} is exiled."]
        if kind == "fight":                                # two targets, each hits the other
            return [f"{owner.shown(p)} has nothing to fight."]
        if kind == "bounce":
            owner.move(f"#{p.id}", "hand")
            self.note_other(owner, f"{who} returns your {name} to your hand.")
            return [f"{name} returns to its owner's hand."]
        if kind in ("pump", "shrink-etb"):
            bp, bt = (int(m.group("bp")), int(m.group("bt"))) if kind == "pump" else (int(m.group(1)), int(m.group(2)))
            p.temp = (p.temp[0] + bp, p.temp[1] + bt)
            extra = ""
            if kind == "pump" and m.group("kw"):
                k = next((kw for kw in EVERY_KEYWORD if kw.lower() == m.group("kw").strip().lower()), None)
                if k:
                    p.temp_kw = (*p.temp_kw, k)
                    extra = f" and gains {k.lower()}"
            if re.search(r"until end of turn and can't be blocked this turn", text, re.I):    # Lore
                p.no_block_turn = owner.turn
                extra += " and can't be blocked this turn"
            sign = lambda v: f"{v:+d}"
            return [f"{name} gets {sign(bp)}/{sign(bt)}{extra} until end of turn."] + owner.check_dead(p)
        if kind == "grant":
            ks = [next((kw for kw in EVERY_KEYWORD if kw.lower() == w.strip().lower()), None) for w in re.split(r" and |, ", m.group(1))]
            if not ks or None in ks:
                return [f"{who}: the table doesn't know '{m.group(1)}': by hand."]
            p.temp_kw = (*p.temp_kw, *ks)
            return [f"{name} gains {' and '.join(k.lower() for k in ks)} until end of turn."]
        if kind == "tuck":                                 # its owner chooses top or bottom (a driven seat is asked; an engine seat says bottom)
            if owner.__dict__.get("asks") and not probing():
                return [f"{name}: its owner puts it on the top or the bottom of their library."] + owner.ask_option(
                    None, f"{who}: put {name} on the top or the bottom of your library",
                    [{"label": f"On top of my library: {name}", "do": {"kind": "call", "method": "tuck_own", "args": [p.id, "top"]}},
                     {"label": f"On the bottom of my library: {name}", "do": {"kind": "call", "method": "tuck_own", "args": [p.id, "bottom"]}}],
                    label="top or bottom of your library", then={"source": who})
            return owner.tuck_own(p.id, "bottom")
        if kind == "unblockable":
            p.no_block_turn = owner.turn
            return [f"{name} can't be blocked this turn."]
        if kind == "no-block":
            p.no_blocking_tick = DEATH_TICK[0]
            self.note_other(owner, f"{who}: your {name} can't block this turn.")
            return [f"{name} can't block this turn."]
        if kind == "stun":
            p.tapped = True
            n = num(m.group(1))
            p.stun += n
            self.note_other(owner, f"{who} taps your {name} and puts {n} stun counter{'s' if n != 1 else ''} on it.")
            return [f"{name} is tapped, with {n} stun counter{'s' if n != 1 else ''}."]
        if kind == "tap":
            p.tapped = True
            return [f"{name} is tapped."]
        if kind == "untap":
            p.tapped = False
            return [f"{name} untaps."]
        if kind == "counter":
            n = num(m.group(1))
            p.counters += n
            return [f"{name} gets {'a +1/+1 counter' if n == 1 else f'{n} +1/+1 counters'}."]
        return [f"{who}: the table can't do this one."]

    def _engine_picks(self, src, shape, t) -> list[str]:
        """Discard, exile from hand or sacrifice, on a seat nobody drives: the least it can give, said aloud (a hidden card stays unnamed until it's in a public zone)."""
        kind = shape[0]
        who = self._subject(src)
        if shape[1].group(1) and kind in ("discard", "exile-hand"):
            return [f"{who}: I keep my cards."]
        if kind in ("discard", "exile-hand"):
            n = num(shape[1].group(2)) if kind == "discard" else 1
            out = []
            for _ in range(n):
                if not self.hand:
                    break
                c = min(self.hand, key=lambda x: float(x.get("manaValue") or 0))
                self.hand.remove(c)
                if kind == "discard":
                    self.graveyard.append(c)
                out.append(c["name"])
            said = [f"{who}: I {'discard' if kind == 'discard' else 'exile'} {', '.join(out) or 'nothing'}."]
            if out and " ||then|| " in t:
                more, left = self.run_effects(src, t.split(" ||then|| ", 1)[1])
                said += more
                self.todo += [f"{who}: {x}" for x in left]
            return said
        slot = card_slots({"name": "x", "types": ["Instant"]}, t.split(" ||then|| ")[0])[0]
        fit = [p for p in self.battlefield if any(p.is_(k) for k in slot["kinds"]) and (not slot.get("token_only") or p.token) and (not slot.get("nontoken") or not p.token)]
        if not fit or slot.get("optional"):
            return [f"{who}: I sacrifice nothing."]
        vic = min(fit, key=lambda q: self.stats(q)[0] if q.is_("Creature") else 0)
        name = self.shown(vic)
        self.move(f"#{vic.id}", "graveyard")
        said = [f"{who}: I sacrifice {name}."]
        if " ||then|| " in t:
            more, left = self.run_effects(src, t.split(" ||then|| ", 1)[1])
            said += more
            self.todo += [f"{who}: {x}" for x in left]
        return said

    def ask_choice(self, src, sentence, kind="effect", role=None) -> list[str]:
        """Park an aimed trigger until this seat answers (the `choose` action). Plain data: it survives a snapshot. A sentence with two targets asks twice."""
        name = src.name if src is not None else (self.__dict__.get("spell_name") or "That")      # (a spell names itself: "Final Frontier", not "That")
        slots = card_slots({"name": name, "types": ["Instant"]}, sentence) if kind == "effect" else \
            [{"side": "own", "kinds": ["Creature"], "optional": False, "label": f"{role} Role on"}]
        if not slots:
            return []
        for sl in slots:
            if sl.get("other") and src is not None:
                sl["not_id"] = src.id                      # "another": never the source itself
        pend = self.__dict__.setdefault("pending_choices", [])
        self.__dict__["choice_seq"] = self.__dict__.get("choice_seq", 0) + 1
        pend.append({"id": self.__dict__["choice_seq"], "kind": kind, "source": name, "source_id": src.id if src is not None else None, "text": sentence,
                     "slot": slots[0], "slots": slots, "picked": [], "role": role})
        return [f"{self.shown(src) if src is not None else name}: {slots[0].get('label', 'target')}: I'm choosing."]

    # ── a choice that isn't a target: which mode, which colour, pay or not. It is asked like a target (the same pending list, the same `choose`
    #    answer, the same dropdown on the hand page): each option is {"at": this seat, "ref": "opt:N"}. Plain data: it survives a snapshot. ──
    def ask_option(self, src, text: str, options: list, label: str = "choose", n: int = 1, optional: bool = False, then: dict | None = None) -> list[str]:
        """options: [{"label": words, "do": {...}}]; n: how many to pick (each once). `do` kinds: mode (run a sentence), mana, note (say it), call."""
        name = src.name if src is not None else (then or {}).get("source") or self.__dict__.get("spell_name") or "That"
        if not options:
            return []
        slot = {"zone": "option", "side": "own", "kinds": [], "optional": optional, "label": label, "choices": [o["label"] for o in options]}
        slots = [dict(slot, label=label if i == 0 else f"{label} (and another)") for i in range(max(1, min(n, len(options))))]
        pend = self.__dict__.setdefault("pending_choices", [])
        self.__dict__["choice_seq"] = self.__dict__.get("choice_seq", 0) + 1
        pend.append({"id": self.__dict__["choice_seq"], "kind": "option", "source": name, "source_id": src.id if src is not None else None,
                     "text": re.sub(r"\s+", " ", text)[:300], "slot": slots[0], "slots": slots, "picked": [], "role": None, "options": options, "then": then or {}})
        return [f"{self.shown(src) if src is not None else name}: {label}: I'm choosing."]

    def tuck_own(self, perm_id: int, where: str) -> list[str]:
        """Guidance Failure: this seat's own permanent goes on the top or the bottom of its library, as it chose."""
        p = next((q for q in self.battlefield if q.id == perm_id), None)
        if p is None:
            return ["It has already left the battlefield."]
        return self.move(f"#{p.id}", "library", where=where)

    def _look(self, src, t: str) -> list[str] | None:
        """"Look at the top N … put one into your hand, the other into your graveyard" (Pattern Buffer Preservation), "you may reveal a <kind> card
        from among them and put it into your hand; the rest on the bottom" (Rutherford, Spock, There's Coffee in that Nebula), "mill N; you may put
        a permanent card from among them into your hand" (First Contact). A driven seat chooses from the cards (only it sees them: they are in
        its own pending question); an engine seat takes the one that fits with the highest mana value. None: a wording the table doesn't know."""
        parts = t.split(" ||and|| ")
        low = [x.lower().rstrip(".") for x in parts]
        m = re.fullmatch(r"look at the top (\w+) cards of your library", low[0])
        g = re.fullmatch(r"mill (\w+) cards", low[0])
        x = re.fullmatch(r"look at the top x cards of your library, where x is the number of creature types among non-borg creatures you control", low[0])
        if not (m or g or x):
            return None
        try:
            n = len(self.creature_types(exclude="Borg")) if x else num((m or g).group(1))
        except (KeyError, ValueError):
            return None
        if x:                                              # Hoshi Sato: one of the X is taken, the rest go to the bottom at random
            if len(parts) != 2 or not re.fullmatch(r"put one of them into your hand and the rest on the bottom(?: of your library)? in (?:a random|any) order", low[1]):
                return None
            m, mode, want, optional = True, "hand-bottom", "", False
        elif m and len(parts) == 2 and re.fullmatch(r"put one of them into your hand and the (?:other|rest) into your graveyard", low[1]):
            mode, want, optional = "hand-graveyard", "", False
        elif m and len(parts) == 3:
            r = re.fullmatch(r"you may reveal an? (.+?) from among them and put it into your hand", low[1])
            if not r:
                return None
            mode, want, optional = "hand-bottom", re.sub(r" card$", "", r.group(1)), True
        elif g and len(parts) == 2:
            r = re.fullmatch(r"you may put an? (.+?) card from among them into your hand", low[1])
            if not r:
                return None
            mode, want, optional = "mill-hand", r.group(1), True
        else:
            return None
        if probing():
            return ["(look)"]
        pile = [self.library.pop() for _ in range(min(n, len(self.library)))]
        who = self._subject(src)
        said = []
        if mode == "mill-hand":
            self.graveyard += pile
            said.append(f"I mill {len(pile)}: {', '.join(c['name'] for c in pile)}.")
        else:
            said.append(f"I look at the top {len(pile)} card{'s' if len(pile) != 1 else ''} of my library.")
        fits = lambda c: self._look_fits(c, want)
        self.__dict__["look_pile"] = {"cards": pile, "mode": mode, "want": want}
        cands = [i for i, c in enumerate(pile) if fits(c)]
        if not pile or (not cands and optional):
            return said + self.look_take(-1)
        if self.__dict__.get("asks"):
            opts = [{"label": pile[i]["name"] + (" (to your hand)" if mode != "mill-hand" else " (from your graveyard to your hand)"), "do": {"kind": "call", "method": "look_take", "args": [i]}} for i in cands]
            if optional:
                opts.append({"label": "None of them", "do": {"kind": "call", "method": "look_take", "args": [-1]}})
            return said + self.ask_option(src, t.replace(" ||and|| ", ". "), opts, label="the card you take")
        best = max(cands, key=lambda i: float(pile[i].get("manaValue") or 0)) if cands else -1
        return said + self.look_take(best)

    @staticmethod
    def _look_fits(c: dict, want: str) -> bool:
        """"artifact", "permanent", "noncreature, nonland", "Plains card or nonland permanent card with mana value 3 or less"."""
        if not want:
            return True
        want = want.lower()
        g = re.fullmatch(r"(.+?) card or (.+?) with mana value (\d+) or less", want)
        if g:
            return card_fits(c, g.group(1)) or (card_fits(c, g.group(2).replace(" card", "")) and float(c.get("manaValue") or 0) <= int(g.group(3)))
        if "," in want and all(w.strip().startswith("non") for w in want.split(",")):       # "noncreature, nonland": every one of them
            return all(card_fits(c, w.strip()) for w in want.split(","))
        return card_fits(c, want)

    def look_take(self, i: int) -> list[str]:
        """The answer to a look: card i of the pile goes to the hand (-1: none); the rest go where the card says."""
        lp = self.__dict__.pop("look_pile", None)
        if lp is None:
            return []
        pile, mode = lp["cards"], lp["mode"]
        took = pile[i] if 0 <= i < len(pile) else None
        rest = [c for c in pile if c is not took]
        said = []
        if mode == "mill-hand":
            if took is not None and took in self.graveyard:
                self.graveyard.remove(took)
                self.hand.append(took)
                said.append(f"I put {took['name']} into my hand.")      # (it was milled face up: its name is public)
                said += self.left_graveyard()
            else:
                said.append("I take none of them.")
            return said
        if took is not None:
            self.hand.append(took)
        if mode == "hand-graveyard":
            self.graveyard += rest
            said.append("I put one into my hand" + (f" and {', '.join(c['name'] for c in rest)} into my graveyard." if rest else "."))
        else:
            self.rng.shuffle(rest)
            for c in rest:
                self.library.insert(0, c)
            said.append((f"I reveal {took['name']} and put it into my hand" if took is not None else "I take none of them") + f"; the other {len(rest)} go on the bottom of my library in a random order.")
        return said

    # ── "look at target opponent's hand" (Stray from History, Salt Vampire, Zero, V'Ger): the caster alone is shown the hand ──
    # The cards ride in the caster's OWN pending question (its seat state, nothing else: not the log, not an event, not another seat's view).
    # The public log says who looked, and later only the card that was taken. The server hands this seat the hand (server.reveal_hand) and carries
    # out the taking from the other seat's hand (server.take_from_hand); the seat itself only decides.
    @staticmethod
    def hand_card_view(c: dict) -> dict:
        t = c.get("types") or []
        return {"name": c["name"], "cost": c.get("manaCost") or "", "mv": float(c.get("manaValue") or 0), "types": list(t), "land": "Land" in t,
                "power": c.get("power"), "toughness": c.get("toughness")}

    @staticmethod
    def hand_card_words(v: dict) -> str:
        pt = f" {v['power']}/{v['toughness']}" if v.get("power") not in (None, "") and v.get("toughness") not in (None, "") else ""
        return f"{v['name']}{' ' + v['cost'] if v.get('cost') else ''} ({'/'.join(v['types']) or 'card'}{pt})"

    def reveal_best(self, cards: list[dict]) -> dict | None:
        """The nonland card an engine-run seat takes: the dearest by mana value, then the biggest power; ties by name, so it is always the same one."""
        pool = [c for c in cards if not c["land"]]
        return max(pool, key=lambda c: (c["mv"], float(c["power"]) if str(c.get("power") or "").lstrip("-").isdigit() else 0, c["name"])) if pool else None

    def reveal_begin(self, tgt: str, hand: list[dict], mode: str, source: str) -> list[str]:
        """This seat is shown `tgt`'s hand (a list of their card dicts). mode: exile | exile-draw | exile-play | draw | look. A driven seat is asked
        (a pending "option" question; the cards are in it); an engine-run seat takes the best nonland card itself. Returns lines that name no card."""
        cards = [self.hand_card_view(c) for c in hand]
        pickable = [c for c in cards if not c["land"]]
        if probing():
            return ["(look)"]
        look_only = mode in ("draw", "look")
        if not look_only and not pickable:
            return [f"{tgt}'s hand has no nonland card to choose."]
        if not self.__dict__.get("asks"):                  # an engine-run seat decides for itself
            best = None if look_only else self.reveal_best(cards)
            return self.reveal_take(tgt, best["name"] if best else "", mode, source)
        seen, opts = set(), []
        if look_only:
            opts = [{"label": "I have seen it" + (" (then draw a card)" if mode == "draw" else ""),
                     "do": {"kind": "call", "method": "reveal_take", "args": [tgt, "", mode, source]}}]
        for c in [] if look_only else pickable:
            if c["name"] not in seen:
                seen.add(c["name"])
                opts.append({"label": self.hand_card_words(c), "do": {"kind": "call", "method": "reveal_take", "args": [tgt, c["name"], mode, source]}})
        counts: dict[str, int] = {}
        for c in cards:
            counts[c["name"]] = counts.get(c["name"], 0) + 1
        listing = ", ".join(n + (f" x{k}" if k > 1 else "") for n, k in counts.items()) or "no cards"
        verb = {"exile": "the nonland card to exile", "exile-draw": "the nonland card they exile (they draw a card)",
                "exile-play": "the nonland card to exile (they may play it for {2} more)"}.get(mode, "your look")
        # (ask_option's own line says the label aloud, and the label is the hand: so its line is dropped and a plain one said instead)
        if not self.ask_option(None, f"{source}: {tgt}'s hand", opts, label=f"{tgt}'s hand ({len(cards)}): {listing}. Choose {verb}",
                               optional=mode in ("exile-draw", "exile-play"), then={"source": source}):
            return []
        self.__dict__["pending_choices"][-1]["reveal"] = {"target": tgt, "mode": mode, "cards": cards}     # (a bot reads the cards here; it is this seat's own state)
        return [f"I'm choosing from {tgt}'s hand."]

    def opponent_hit(self, opp: str, n: int, source: str, verb: str = "loses") -> list[str]:
        """The answer to "target opponent loses N life" when there was more than one opponent to name."""
        self.out(kind="one-player", player=opp, n=int(n), source=source, verb=verb)
        return []

    def reveal_take(self, tgt: str, name: str, mode: str, source: str) -> list[str]:
        """The answer to a look at `tgt`'s hand: take `name` (an empty name: nothing). The server moves the card (server.take_from_hand)."""
        if mode in ("draw", "look"):
            if mode == "draw":
                self.draw(1)
            return [f"I have seen {tgt}'s hand" + (" and I draw a card." if mode == "draw" else ".")]
        if not name:
            return [f"I take nothing from {tgt}'s hand."]
        self.out(kind="take-from-hand", target=tgt, card=name, mode=mode, source=source)
        return []

    def _option_src(self, item: dict):
        return next((p for p in self.battlefield if p.id == item.get("source_id")), None) or \
            Perm({"name": item["source"], "types": [], "text": ""}, id=item.get("source_id") or 0)

    def _option_chosen(self, item: dict, picks: list) -> list[str]:
        """The seat picked option(s) by index: carry each out, in the card's order."""
        src = self._option_src(item)
        said = []
        for i in sorted(set(picks)):
            do = item["options"][i].get("do") or {}
            k = do.get("kind")
            if k == "mode":
                said.append(f"I choose: {do['text'].rstrip('.')}.")
                if not do.get("known", True):
                    self.todo.append(f"{item['source']}: {do['text']}")
                    continue
                done, left = self.run_effects(src, do["text"])
                said += done
                self.todo += [f"{item['source']}: {x}" for x in left]
            elif k == "mana":
                self.add_mana(do["colors"])
                said.append(f"I add {''.join('{' + c + '}' for c in do['colors'])} to my mana pool.")
            elif k == "note":
                said.append(do.get("text") or "")
            elif k == "call":                              # a method of this seat, by name, with plain arguments (the table's own follow-ups)
                said += getattr(self, do["method"])(*do.get("args", [])) or []
        return said

    def choose_modes(self, src, modes: list, n: int = 1, label: str = "choose one") -> list[str]:
        """"Choose one —" / "choose two —" on a trigger or ability: a driven seat is asked which (then each chosen mode runs through the effect
        engine, asking for its targets); an engine seat takes the first mode(s) it can carry out with nothing to aim. A mode the table can't
        do stays on the list, marked, and choosing it says "By hand"."""
        modes = [re.sub(r"\s*\([^)]*\)", "", m_).strip().lstrip("•").strip() for m_ in modes if m_.strip()]
        name = src.name if src is not None else "That"
        known = [self.can_run(re.sub(r"^[^.—]{1,40} — ", "", m_), name) for m_ in modes]
        if self.__dict__.get("asks") and not probing():
            opts = [{"label": m_[:110] + ("" if ok else " (by hand)"), "do": {"kind": "mode", "text": m_, "known": ok}} for m_, ok in zip(modes, known)]
            return self.ask_option(src, f"{label} — " + " • ".join(modes), opts, label=label, n=n)
        # A seat nobody answers for: no mode runs by itself (game 2's rule: the choice is the player's). Every mode is said at the table, as before.
        self.todo.append(f"{name}: {label} — " + " • ".join(modes))
        return []

    def resolve_choice(self, item: dict, aim: list) -> list[str]:
        """The seat answered: carry the parked effect out, on what it named. aim [] is "no target" (only an "up to one" may say that)."""
        self.__dict__["pending_choices"] = [x for x in self.__dict__.get("pending_choices", []) if x["id"] != item["id"]]
        if item["kind"] == "option":
            picks = [a["option"] for a in aim if "option" in a]
            if not picks:
                return [f"{item['source']}: nothing chosen."] + self.resume_deferred()
            return [f"{item['source']}: {x}" for x in self._option_chosen(item, picks) if x] + self.resume_deferred()
        if not aim:
            return [f"{item['source']}: no target."] + self.resume_deferred()
        if item["kind"] == "role":
            return self.add_role(aim[0]["perm"], item["role"]) + self.resume_deferred()
        src = next((p for p in self.battlefield if p.id == item["source_id"]), None) or \
            Perm({"name": item["source"], "types": [], "text": ""}, id=item["source_id"] or 0)   # gone since it triggered (a "dies" trigger): its name still speaks
        said = self._one_effect(src, item["text"], aim)
        return [f"{item['source']}: {x}" for x in (said or [f"the table can't do this: {item['text']}"])] + self.resume_deferred()

    def note_other(self, owner, text):
        if owner is not self:
            self.out(kind="notice", seat=owner.name, text=f"{self.name}'s {text}")

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
        if e.startswith("aura and equipment attached to it"):
            return len([x for x in self.battlefield if x.attached_to == target.id and (self.is_aura(x) or is_equipment(x.card))])
        if e.startswith("aura attached to it"):
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
        if e.startswith("officer token you control"):
            return len([x for x in self.battlefield if x.token and "Officer" in x.name])
        if e.startswith("treasure you control"):
            return len([x for x in self.battlefield if x.name.startswith("Treasure")])
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
    def sources(self, only=None):
        """One entry per mana it can make now: (the permanent that gets tapped, colours). A land
        enchanted by a mana Aura (Fertile Ground, Utopia Sprawl, Wild Growth, Overgrowth) makes its
        own mana PLUS the Aura's — found in the rehearsal, where the engine said 2 mana and the
        table had 3."""
        out, filters = [], []
        for p in self.battlefield:
            if p.tapped or p.attached_to is not None or p.face_down:   # a face-down 2/2 has no mana ability (and counting it tells an opponent what it is)
                continue
            text = re.sub(r'"[^"]*"', "", p.card.get("text") or "")   # (David and Carol make a land that taps for mana: they don't)
            if p.is_("Creature") and p.sick:
                continue
            if p.token and p.name.startswith("Treasure"):
                out.append((p, COLORS))                      # sacrificed when it is spent (spend)
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
        out = [(fp, fp.card["mana"]) for fp in self.floating()] + out     # the pool's mana first: it empties when the step does, a land doesn't
        if only is not None:                               # the player named which sources pay (the pool's mana is always spent first)
            out = [e for e in out if e[0].id < 0 or e[0].id in only]
            filters = [e for e in filters if e[0].id in only]
        # net one each, but only if some other mana can pay the first {1} (Mossfire Valley alone makes nothing)
        return out + filters if out else out

    def missing_color(self):
        """The commander colour its mana makes least of — what a "choose a color" land Aura names."""
        if self.commander is not None:
            src = self.commander.get("colorIdentity") or self.commander.get("colors") or []
        else:   # no commander: the colours its library actually plays
            src = [c for card in self.library for c in (card.get("colorIdentity") or card.get("colors") or [])]
        ident = [c for c in "WUBRG" if c in src] or ["G"]
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

    def plan_payment(self, mana_cost: str, extra_generic=0, only=None):
        """A set of sources that pays the cost, or None. Coloured pips first from the sources that make the FEWEST colours (keep flexible lands for
        later), then generic from the rest. Among the payments that work it prefers the one that leaves the OTHER cards in this hand castable
        (smart_payment); whether a cost CAN be paid never depends on that, and a payment the caller named (`only`) is never changed."""
        got = self._plan_payment(mana_cost, extra_generic, only)
        if got is None:
            return None
        if only is None and not probing():
            got = self.smart_payment(mana_cost, extra_generic, got)
        self.__dict__["_plan"] = {"pay": got[0], "assign": got[1]}
        return got[0]

    def _plan_payment(self, mana_cost: str, extra_generic=0, only=None, srcs=None, order=None):
        """The payment itself: (sources used, [(source, colour paid or None)]) or None. srcs: the sources to choose from (default: all it can tap);
        order: a sort key for who is tapped first among the sources that fit (default: single-colour, single-mana ones first)."""
        hybrids = re.findall(r"\{([^}/]+)/([^}/]+)\}", mana_cost or "")
        if hybrids:                                        # hybrid ({U/B}, {2/W}): either half pays; try each way until one can be paid
            import itertools
            parts = re.split(r"\{[^}/]+/[^}/]+\}", mana_cost)
            for pick in itertools.product(*hybrids):
                plain = "".join(a_ + ("{" + h_ + "}" if h_ else "") for a_, h_ in zip(parts, [*pick, ""]))
                got = self._plan_payment(plain, extra_generic, only, srcs, order)
                if got is not None:
                    return got
            return None
        generic, pips = parse_cost(mana_cost)
        generic = max(0, generic + extra_generic)
        srcs = self.sources(only) if srcs is None else srcs
        assign: list = []                                   # (source, the colour it paid or None for generic): what a later swap must still match
        yields = Counter(e[0].id for e in srcs)                  # how much mana one tap of each permanent makes
        pool = sorted(srcs, key=order or (lambda s: (len(s[1]), yields[s[0].id])))   # coloured pips from single-colour, then single-mana, sources first
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
                assign.append((s[0], color))
        for paid in range(generic):
            fr = free()
            if fr:
                s = fr[0]
            elif pool:                                   # a fresh tap: the one whose mana fits what is still owed (a 2-mana land for a
                owed = generic - paid                    # 3-mana spell, not a 2-mana land AND another land where one would do)
                by_perm: dict[int, list] = {}
                for e in pool:
                    by_perm.setdefault(e[0].id, []).append(e)
                fits_ = [g for g in by_perm.values() if len(g) <= owed]
                grp = max(fits_, key=lambda g: (len(g), -len(g[0][1]))) if fits_ else min(by_perm.values(), key=len)
                s = grp[0]
            else:
                return None
            pool.remove(s)
            used.append(s[0])
            assign.append((s[0], None))
        return used, assign

    def smart_payment(self, mana_cost: str, extra_generic, base):
        """Of the payments that work, the one that leaves this hand's OTHER cards castable (Pet Sehlat was paid with both Swamps and a Plains, and the
        two Plains left could no longer cast Tal Shiar Operative). `base` is the plain payment; candidates are built from valid payments only, so the
        answer is always one that pays; ties keep the plain one. Deterministic: no randomness, a fixed order of candidates."""
        others, skipped = [], False
        for c in self.hand:
            if "Land" in (c.get("types") or []):
                continue
            if not skipped and (c.get("manaCost") or "") == (mana_cost or ""):
                skipped = True                              # (the card being cast: one copy of it is not "another card")
                continue
            if c["name"] not in [o[0]["name"] for o in others]:
                others.append((c, -self.auto_discount(c)))
        others = [o for o in others if self._plan_payment(o[0].get("manaCost") or "", o[1]) is not None]      # castable at all with everything untapped
        if not others:
            return base
        allsrc = self.sources()
        need_cols: set = set()
        for c, _d in others:
            need_cols |= set(parse_cost(c.get("manaCost") or "")[1])
        copies = Counter(e[0].name for e in allsrc)
        useful = lambda e: len(set(e[1]) & need_cols)

        def rest_of(used):
            tapped = {q.id for q in used}
            return [e for e in allsrc if e[0].id < 0 or e[0].id not in tapped]

        def score(got):
            rest = [e for e in rest_of(got[0]) if e[0].id >= 0]
            alone = sum(1 for c, d in others if self._plan_payment(c.get("manaCost") or "", d, srcs=rest) is not None)
            together, left = 0, list(rest)
            for c, d in sorted(others, key=lambda o: (parse_cost(o[0].get("manaCost") or "")[0] + sum(parse_cost(o[0].get("manaCost") or "")[1].values()), o[0]["name"])):
                g = self._plan_payment(c.get("manaCost") or "", d, srcs=left)
                if g is not None:
                    together += 1
                    ids = {q.id for q in g[0]}
                    left = [e for e in left if e[0].id not in ids]
            cols = len({k for e in rest for k in e[1]} & need_cols)
            tapped = [e for e in allsrc if e[0] in got[0]]
            return (alone, together, cols, -sum(useful(e) for e in tapped), sum(copies[e[0].name] for e in tapped))

        cands = [base]
        ordered = sorted(others, key=lambda o: (parse_cost(o[0].get("manaCost") or "")[0] + sum(parse_cost(o[0].get("manaCost") or "")[1].values()), o[0]["name"]))
        for seq in [[o] for o in ordered] + [ordered, ordered[::-1]]:
            keep, left = [], list(allsrc)
            for c, d in seq:                                # reserve what these cards need, then pay from the rest
                g = self._plan_payment(c.get("manaCost") or "", d, srcs=left)
                if g is not None:
                    ids = {q.id for q in g[0]}
                    keep.append(ids)
                    left = [e for e in left if e[0].id not in ids]
            got = self._plan_payment(mana_cost, extra_generic, srcs=left)
            if got is not None:
                cands.append(got)
        got = self._plan_payment(mana_cost, extra_generic, order=lambda s: (useful(s), -copies[s[0].name], len(s[1]), s[0].id))      # (taps what the hand does not need first)
        if got is not None:
            cands.append(got)
        best = base
        best_score = score(base)
        floating_used = lambda g: sum(1 for q in g[0] if q.id < 0)         # mana in the pool is spent first: it empties with the step, a land doesn't
        for g in cands[1:]:
            if floating_used(g) < floating_used(base):
                continue
            sc = score(g)
            if sc > best_score:
                best, best_score = g, sc
        return best

    def available_mana(self): return len(self.sources())

    def auto_discount(self, c: dict) -> int:
        """Generic mana its own permanents take off this spell: "Enchantment spells you cast cost {1}
        less" (Jukai Naturalist, Starfield Mystic), "Aura (and Equipment) spells you cast cost {1}
        less" (Danitha Capashen, Transcendent Envoy). Each copy counts."""
        types, subs = c.get("types") or [], c.get("subtypes") or []
        n = 0
        own = c.get("text") or ""
        if re.search(r"This spell costs \{X\} less to cast, where X is the number of differently named lands you control", own):
            n += differently_named_lands(self.battlefield)                                  # Strange new worlds (Cloak and Phaser)
        g = re.search(r"This spell costs \{(\d+)\} less to cast for each noncreature, nonland card in your graveyard", own)
        if g:                                                                              # Ravenous Cosmozoan
            n += int(g.group(1)) * sum(1 for x in self.graveyard if "Creature" not in (x.get("types") or []) and "Land" not in (x.get("types") or []))
        for p in self.battlefield:
            for kinds, k in re.findall(r"([\w ]+?) spells you cast cost \{(\d+)\} less", p.card.get("text") or ""):
                kinds = kinds.lower()
                if ("enchantment" in kinds and "Enchantment" in types) or ("aura" in kinds and "Aura" in subs) \
                        or ("creature" in kinds and "Creature" in types):
                    n += int(k)
        return n

    # ── options ───────────────────────────────────────────────────────────────────────────
    def castable(self, guard_wipes: bool = False):
        """(label, card, payment, is_commander) for every spell it can afford right now. Instants
        are included (cast on its own turn). Auras need a creature of its own to enchant.
        guard_wipes: an engine seat choosing for itself never wipes its own board (a preference, not a rule). What a seat MAY cast (its state, the
        legal list) must not use it: D'Ghor Bloodletter ("each player sacrifices…") read as uncastable while the cast itself went through."""
        out = []
        seen = set()
        for c in self.hand:
            if "Land" in c["types"] or c["name"] in seen:
                continue
            seen.add(c["name"])
            if is_hostile_aura(c):
                if not self.public_board and not self.hostile_targets():
                    continue      # nothing of theirs to put it on
            elif "Aura" in (c.get("subtypes") or []) and not self.aura_targets(c):
                continue
            if guard_wipes and is_board_wipe(c) and self.creatures():
                continue          # never wipe its own board: offered only when it has no creatures
            pay = self.plan_payment(c.get("manaCost", ""), -self.auto_discount(c))
            if pay is not None:
                out.append((f"cast {c['name']}", c, pay, False))
        if self.cmdr_in_zone and self.commander is not None:
            pay = self.plan_payment(self.commander["manaCost"], 2 * self.cmdr_casts)
            if pay is not None:
                out.append((f"cast your commander {self.commander['name']}", self.commander, pay, True))
        return out

    def exile_castable(self):
        """castable() for the cards it may play from exile: (label, card, payment, False). Lands are not here (they are a land drop)."""
        out, seen = [], set()
        for c in self.impulse_cards():
            if "Land" in (c.get("types") or []) or c["name"] in seen:
                continue
            seen.add(c["name"])
            if is_hostile_aura(c):
                if not self.public_board and not self.hostile_targets():
                    continue
            elif "Aura" in (c.get("subtypes") or []) and not self.aura_targets(c):
                continue
            pay = self.plan_payment(c.get("manaCost", ""), -self.auto_discount(c) + self.exile_tax(c))
            if pay is not None:
                out.append((f"cast {c['name']} from exile", c, pay, False))
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
    def play_land(self, c, pay_life=False, from_exile=False):
        """from_exile: a land it may play from exile (Reckless Impulse): played the same way, and it is the turn's land drop all the same."""
        if from_exile:
            self.impulse_take(c)
        else:
            self.hand.remove(c)
        text = c.get("text") or ""
        tapped = "enters tapped" in text or "enters the battlefield tapped" in text
        others = sum(1 for q in self.battlefield if q.is_("Land") and not q.face_down)
        m = re.search(r"enters(?: the battlefield)? tapped unless you control (a|an|one|two|three|four|\d+) or (more|fewer) other lands", text)
        if m:                                              # Federation Outpost ("two or more"), fast lands ("two or fewer"): decided now
            n = int(m.group(1)) if m.group(1).isdigit() else {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4}[m.group(1)]
            tapped = not (others >= n if m.group(2) == "more" else others <= n)
        shock = re.search(r"As this land enters, you may pay (\d+) life\. If you don't, it enters tapped", text)
        paid = 0
        if shock and pay_life:                             # Blood Crypt and friends: your own life, your own choice, at the moment you play it
            paid = int(shock.group(1))
            self.life -= paid
            tapped = False
        p = Perm(c, sick=False, tapped=tapped)
        self.battlefield.append(p)
        self.land_played = True
        said = f"I play {c['name']}" + (" from exile" if from_exile else "") + (f", paying {paid} life." if paid else ", tapped." if p.tapped else ".")
        more = self.land_enters(p, decided=True)           # its own "When this land enters …" (R10: the same for every way a land arrives)
        more += self.land_entered(p, play=True)            # Landfall, in the cards' own words (the second-resolution riders count per turn)
        return " ".join([said, *more]) if more else said

    def land_enters(self, p: Perm, decided: bool = False) -> list[str]:
        """The land's OWN enters text, whichever way it came (played, searched for, put onto the battlefield, returned from the graveyard, Beam me up,
        Final Frontier): "This land enters tapped", "When this land enters, you gain N life" (Risa, Bajor, Cardassia Prime), and "When this land
        enters untapped, choose one" (the Outposts). Landfall is a different thing (land_entered)."""
        text = p.card.get("text") or ""
        said = []
        if not decided and not p.tapped:                   # played lands were decided in play_land (shock lands included); the other routes are decided here
            others = sum(1 for q in self.battlefield if q is not p and q.is_("Land") and not q.face_down)
            u = re.search(r"enters(?: the battlefield)? tapped unless you control (a|an|one|two|three|four|\d+) or (more|fewer) other lands", text)
            if re.search(r"This land enters tapped\.", text):
                p.tapped = True
            elif u:
                n = int(u.group(1)) if u.group(1).isdigit() else {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4}[u.group(1)]
                p.tapped = not (others >= n if u.group(2) == "more" else others <= n)
        g = re.search(r"When this land enters(?: the battlefield)?, you gain (\d+) life", text)
        if g:                                              # Risa and the other Planet lands
            n = int(g.group(1))
            self.life += n
            self.out(kind="life", player=self.name, delta=n, by="land")
            said.append(f"I gain {n} life.")
        if not p.tapped and re.search(r"When this land enters untapped, choose one", text):
            said += self.face_dilemma()
            spec = modal_after(text, "When this land enters untapped, choose one")
            if spec:                                       # Federation Outpost and the other four: the controller picks
                said += self.choose_modes(p, spec[1], n=1)
                p.__dict__["modal_asked"] = True
        return said

    def cast(self, label, c, pay, is_cmdr, choose_target, choose_modes=None, aim=None, from_exile=None, hold=False):
        """from_exile: c is a card it may play from exile (impulse draw), cast as from the hand. None: decided by where c is (by identity)."""
        if from_exile is None:
            from_exile = not is_cmdr and not any(x is c for x in self.hand) and any(x is c for x in self.impulse_cards())
        self.spend(pay)
        if is_cmdr:
            self.cmdr_in_zone = False
            self.cmdr_casts += 1
        elif from_exile:
            self.impulse_take(c)
        else:
            self.hand.remove(c)
        said = [f"I cast {c['name']}" + (" from the command zone." if is_cmdr else " from exile." if from_exile else ".")]
        if not probing():
            self.out(kind="cast", name=c["name"], mv=float(c.get("manaValue") or 0), turn=self.turn)      # (the public `cast` event: the summary reads it, not the sentence)
        import time as _t
        self.last_cast = {"name": c["name"], "perm": None, "at": _t.time(), "commander": is_cmdr}
        triggered = self.on_cast(c)                  # resolve before the spell (they're above it on the stack)
        self.__dict__["casts_game"] = self.__dict__.get("casts_game", 0) + 1     # "the first spell you've cast this game"
        if hold:
            # The spell is cast and paid for, and waits: an opponent who could counter it answers first (the table runs that window). Plain
            # data on the seat, so a snapshot keeps it; `release_held` resolves it exactly as an unheld cast would, `counter_held` puts it
            # in the graveyard unresolved.
            self.__dict__["held_spell"] = {"card": c, "is_cmdr": bool(is_cmdr), "aim": list(aim or []), "at": _t.time()}
            return " ".join(said + triggered)
        return " ".join(self._cast_resolve(c, is_cmdr, choose_target, choose_modes, aim, triggered, said))

    def _cast_resolve(self, c, is_cmdr, choose_target, choose_modes, aim, triggered, said) -> list[str]:
        """What a spell does when it resolves (the second half of `cast`): an instant or sorcery's text, or the permanent entering."""
        said = list(said)
        types = c.get("types") or []
        if "Instant" in types or "Sorcery" in types:
            self.graveyard.append(c)
            modes = modal_options(c)
            if modes and choose_modes:
                picked = choose_modes(c, modes)
                said.append("I choose: " + " — and — ".join(picked) + ".")
                said += self.face_dilemma()
                said += self.resolve_simple(" ".join(picked), aim=aim, card=c)
            else:
                said.append(_short_text(c))                      # the card's words, then what the engine can carry out itself
                said += self.resolve_simple(kicked_text(c.get("text") or "", bool(self.__dict__.get("kicked_now"))) if kicker_of(c) else c.get("text") or "",
                                            aim=aim, card=c)  # (game 9: Boarding Action's token was never made: only modal
            said += triggered                                    #  spells reached resolve_simple)
        else:
            p = Perm(c, sick="Haste" not in printed_keywords(c))
            if is_hostile_aura(c):
                target = choose_target(c, None)          # None: pick among the OTHER players' cards
                self.on_their_cards.append(f"{c['name']} on {target}")
                # still an Aura it controls entering: Tanglespan, constellation (not Siona: not its creature)
                return said + triggered + [f"It enchants {target}.", _short_text(c)] + self.on_enchantment_enters(p)
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
            said += self.creature_entered(p)
            self.__dict__["etb_aim"] = list(aim or [])          # what the player aimed its enters trigger at
            said += self.enter_effects(p, choose_target)
            self.__dict__["etb_aim"] = []
            if "Saga" in (c.get("subtypes") or []):
                said += self.saga_tick(p)
            if p.is_("Enchantment"):
                said += self.on_enchantment_enters(p)
            if re.search(r"When you cast this spell, copy it", c.get("text") or ""):
                # Combadge: "copy it. (The copy becomes a token.)": a second permanent with the same text and no card behind it
                q = Perm(dict(c), token=True, sick="Haste" not in printed_keywords(c))
                self.battlefield.append(q)
                said.append(f"The copy of {c['name']} becomes a token.")
                said += self.enter_effects(q, choose_target)
        return said

    def _choosers(self, c, on=None, role_on=None, modes=None):
        """(choose_target, choose_modes) for a cast of card c, from what the caster named: what an Aura or Role goes on, which modes."""
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
                # 2026-11-13 retro (C2): this named only tablectl's --mode; an agent or a page sends JSON, where the field is "modes".
                # (fusion_brain.py reads the tail "choose N with --mode (1-based): 1. … | 2. …" with a regex: that part keeps its shape.)
                eg = ", ".join(str(i + 1) for i in range(n))
                raise IllegalAction(f"{card['name']} is modal — the JSON field is \"modes\" (a list of mode numbers, e.g. \"modes\": [{eg}]); "
                                    f"choose {n} with --mode (1-based): " + " | ".join(f"{i + 1}. {m}" for i, m in enumerate(all_modes)))
            picked = [all_modes[int(m) - 1] for m in modes]
            if len(picked) != n:
                raise IllegalAction(f"{card['name']} needs exactly {n} modes")
            return picked
        return choose_target, choose_modes

    def release_held(self) -> list[str]:
        """Nobody countered it: the held spell resolves now, as it would have at once."""
        h = self.__dict__.pop("held_spell", None)
        if h is None:
            return []
        c = h["card"]
        ct, cm = self._choosers(c, h.get("on"), h.get("role_on"), h.get("modes"))
        self.__dict__["cast_x"] = int(h.get("x") or 0)
        self.__dict__["kicked_now"] = bool(h.get("kicked"))
        self.__dict__["role_named"] = bool(h.get("role_on"))
        said = self._cast_resolve(c, h.get("is_cmdr"), ct, cm, h.get("aim"), [], [f"{c['name']} resolves."])
        if h.get("beam") and c in self.graveyard:
            self.graveyard.remove(c)                        # Beam me up: "Then exile this spell"
            self.exile_zone().append(c)                     # (it is in exile, face up: the public list says so)
            said.append(f"{c['name']} is exiled.")
        return [" ".join(said)]

    def counter_held(self, by: str = "") -> list[str]:
        """The held spell is countered: it goes to its owner's graveyard without resolving (a commander back to the command zone)."""
        h = self.__dict__.pop("held_spell", None)
        if h is None:
            return ["There is no spell to counter any more."]
        c = h["card"]
        if h.get("is_cmdr"):
            self.cmdr_in_zone = True
        elif not h.get("beam"):
            self.graveyard.append(c)
        return [f"{self.name}'s {c['name']} is countered" + (f" by {by}" if by else "") + (": it goes to the graveyard without resolving." if not h.get("is_cmdr") and not h.get("beam") else ".")]

    def held_tax(self, cost: str, by: str = "") -> list[str]:
        """"Counter target spell unless its controller pays {N}": this seat pays (it chose to, or an engine seat that can)."""
        h = self.__dict__.get("held_spell")
        if h is None:
            return []
        pay = self.plan_payment(cost)
        if pay is None:
            return [f"I can't pay {cost}."] + self.counter_held(by)
        self.spend(pay)
        return [f"I pay {cost}: {h['card']['name']} is not countered."]

    def tax_or_counter(self, cost: str, by: str = "") -> list[str]:
        """The controller of the held spell answers "unless its controller pays {N}". A driven seat that can pay is asked; one that can't,
        doesn't; an engine seat pays when it can."""
        h = self.__dict__.get("held_spell")
        if h is None:
            return []
        if self.plan_payment(cost) is None:
            return [f"{self.name} can't pay {cost}."] + self.counter_held(by)
        if self.__dict__.get("asks"):
            name = h["card"]["name"]
            return self.ask_option(None, f"{by or 'An opponent'} counters {name} unless you pay {cost}",
                                   [{"label": f"Pay {cost}: {name} resolves", "do": {"kind": "call", "method": "held_tax", "args": [cost, by]}},
                                    {"label": f"Don't pay: {name} is countered", "do": {"kind": "call", "method": "counter_held", "args": [by]}}],
                                   label=f"pay {cost}, or it is countered", then={"source": name, "tax": True})
        return self.held_tax(cost, by)

    def resolve_simple(self, text, aim=None, card=None):
        """What a spell (or its chosen mode) does, sentence by sentence, through run_effects. A sentence the table can't carry out is left
        on the seat's to-do and said at the table (the server does that with every to-do): nothing vanishes silently."""
        self.__dict__["spell_name"] = card["name"] if card else None      # M15: "Plasma Cascade deals 3 damage to …", not "This deals …"
        try:
            said, left = self.run_effects(None, text, aim=aim)
        finally:
            self.__dict__["spell_name"] = None
        for x in left:
            self.todo.append(f"{card['name'] if card else 'That spell'}: {x}")
        return said

    def total_power_triggers(self):
        """'Whenever you attack while creatures you control have total power N or greater, put a +1/+1 counter on this creature.
        Untap it.' (Tactical Officer). Called once per attack declaration, after the attackers are tapped."""
        said = []
        total = sum(self.stats(q)[0] for q in self.creatures())
        for q in self.creatures():
            m = re.search(r"Whenever you attack while creatures you control have total power (\d+) or greater, put a \+1/\+1 counter on this creature\.(?: Untap it\.)?",
                          q.card.get("text") or "")
            if m and total >= int(m.group(1)):
                q.counters += 1
                untap = "Untap it" in m.group(0)
                if untap:
                    q.tapped = False
                said.append(f"{self.shown(q)}: total power {total} — it gets a +1/+1 counter" + (" and untaps." if untap else "."))
                continue
            # the same trigger with any other effect, in the card's own words (Hologram Janeway: "put a +1/+1 counter on target creature", which
            # its controller is asked to aim; Kirk: exile the top card, play it this turn). Total power counts every creature, tapped ones too.
            g = None if q.face_down else re.search(r"Whenever you attack while creatures you control have total power (\d+) or greater, ([^\n]+)", q.card.get("text") or "")
            if g and total >= int(g.group(1)):
                done, left = self.run_effects(q, g.group(2), label=f"{self.shown(q)} (total power {total}): ")
                said += done
                self.todo += [f"{q.name} (you attacked with total power {total}): {x}" for x in left]
        return said

    def enter_effects(self, p, choose_target):
        """The parts of 'when this enters' that change ITS OWN board: tokens and Roles."""
        text = p.card.get("text") or ""
        said = []
        if p.is_("Land") and re.search(r"When this land enters(?: the battlefield)?, you gain \d+ life", text):
            return []                                      # the Planet lands' life is land_enters' (once, for every route)
        # Only "When THIS enters": the subject must be this card. "Whenever an enchantment you control
        # enters, create a 2/2 Pegasus" (Archon) is a trigger on OTHER cards entering, and reading it
        # as Archon's own ETB made a Pegasus whenever Archon itself was cast.
        # (Legends go by a shorter name in their own text: "When Captain Archer enters", "When David and Carol Marcus enter", so the subject is any
        # capitalised name there, as long as it is not another permanent's trigger: "a", "another", "… or another creature you control".)
        subj = r"(?!(?:A|An|Another|One|You)\b)(?![^,\n]* or another)(?:this \w+|[A-Z][^,\n]*?)"
        etb = re.search(rf"When(?:ever)? {subj} enters?(?: or attacks)?(?: the battlefield)?, ([^.]+)\.", text)
        clause = etb.group(1) if etb else (text if "Sorcery" in p.types else "")
        todo_clause = None
        handled = False
        if etb and re.search(r"choose (?:one|two|up to one)\s*[—-]", text[etb.start(1):].split("\n")[0]):
            tail = text[etb.start(1):].split("\n")                 # "choose one —" + its "• …" lines: keep every mode
            modes = []
            for line in tail[1:]:
                if not line.strip().startswith("•"):
                    break
                modes.append(re.sub(r"\s*\([^)]*\)", "", line.strip()))   # reminder text off
            todo_clause = tail[0].strip() + (" " + " ".join(modes) if modes else "")
            clause = ""
            said += self.face_dilemma()
            if modes:                                      # the controller picks (a driven seat is asked; an engine seat takes what it can do): each
                two = bool(re.search(r"choose two", tail[0], re.I))     # chosen mode then runs through the effect engine and asks for its own targets
                said += self.choose_modes(p, modes, n=2 if two else 1, label="choose two" if two else "choose one")
                p.__dict__["modal_asked"] = True
                handled = True
        sm = re.search(r"search your library for any number of cards named ([^,]+), reveal them, put them into your hand", clause)
        if sm and sm.group(1).strip().lower() == p.name.lower():      # Andromedan Automatons: every other copy, revealed
            found = [c for c in self.library if c["name"].lower() == p.name.lower()]
            for c in found:
                self.library.remove(c)
                self.hand.append(c)
            self.rng.shuffle(self.library)
            said.append(f"I search my library: I reveal {len(found)} {p.name} and put {'them' if len(found) != 1 else 'it'} into my hand, then shuffle."
                        if found else f"I search my library: no other {p.name}. I shuffle.")
            handled = True
        for m in re.finditer(r"create (a|two|three) (\d+)/(\d+) ([\w ,]+?) creature tokens?", clause, re.I):  # "Create a..." opens a sorcery (Boarding Action)
            n = {"a": 1, "two": 2, "three": 3}[m.group(1).lower()]
            self.etb_done = getattr(self, "etb_done", set()) | {(p.id, "own-token")}   # the table made it: no second copy by hand
            for _ in range(n):
                # "red Klingon Warrior" -> "Klingon Warrior token": keep the creature type (it names the art), drop the colours
                kind = " ".join(w for w in m.group(4).replace(",", " ").split()
                                if w.lower() not in ("white", "blue", "black", "red", "green", "colorless", "and", "artifact"))
                tok = {"name": f"{kind or m.group(4).split(' ')[-1]} token", "types": ["Creature"], "power": m.group(2),
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
            if cands and self.__dict__.get("asks") and not self.__dict__.get("role_named"):
                said += self.ask_choice(p, clause, kind="role", role=m.group(1))    # a driven seat is asked; only an engine-run one takes its biggest
            elif cands:
                target = choose_target({"name": f"{m.group(1)} Role", "text": ROLES[m.group(1)]}, cands)
                said += self.add_role(target, m.group(1))
        # Neuroflux Heirloom: the token an enters trigger makes, attached at once (Gwyndala)
        nf = re.search(r"create a colorless Equipment artifact token named ([^\"]+?) with \"([^\"]+)\" and equip (\{[^}]+\})\. Attach it to", text)
        if nf and etb:
            tok = {"name": f"{nf.group(1)} token", "type": "Token Artifact — Equipment", "types": ["Artifact"], "subtypes": ["Equipment"],
                   "text": f"{nf.group(2)}.\nEquip {nf.group(3)}", "keywords": []}
            self.battlefield.append(Perm(tok, token=True, attached_to=p.id, sick=False))
            said.append(f"I create {nf.group(1)}, attached to {self.shown(p)}.")
            handled = True
            clause = ""
        # The sentences the shared engine knows: the whole line of the trigger, not only up to its first full stop.
        if etb and not todo_clause and not handled and clause and not re.search(r"create (a|two|three) (\d+)/(\d+) ([\w ,]+?) creature tokens?", clause, re.I):
            line = text[etb.start(1):].split("\n")[0]
            aim = self.__dict__.get("etb_aim") or []
            aimed = effect_shape(line) is not None
            if aimed and not aim and not self.__dict__.get("asks"):         # an engine-run seat, nothing named: a positive trigger takes its biggest creature
                sh = effect_shape(line)
                if sh[0] == "counter" and self.creatures():
                    others = [c for c in self.creatures() if c.id != p.id] if "another target" in line else self.creatures()
                    if others:
                        best = max(others, key=lambda k: self.stats(k)[0])
                        aim = [{"perm": best, "owner": self}]
                        said.append(f"(No target named: {self.shown(best)}, my biggest creature.)")
            if self.can_run(line) and (aim or not aimed or self.__dict__.get("asks")):
                done, left = self.run_effects(p, line, aim=list(aim))
                said += done
                for x in left:
                    self.todo.append(f"{p.name}: {x}")
                handled = True
                clause = ""
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
        elif etb and not handled and not p.__dict__.get("modal_asked"):
            self.todo.append(f"{p.name} entered: {todo_clause or clause}")
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
        """"Whenever you cast a <kind> spell, <effect>": Enchantress's Presence, Kor Spiritdancer, Cetacean Ops, Academy Instructor, Picard,
        Rutherford, Romulan Bird-of-Prey ("if this Spacecraft isn't a creature"), Tal Shiar Analyst. A permanent's face-down text doesn't count."""
        types, subs = c.get("types") or [], c.get("subtypes") or []
        if "Creature" not in types:
            self.__dict__["noncreature_turn"] = self.turn
        said = []
        for p in list(self.battlefield):
            if p.face_down:
                continue
            m = re.search(r"Whenever you cast an? (enchantment|Aura|noncreature|creature or Spacecraft|Spacecraft) spell, (?:(if this Spacecraft isn't a creature), )?(?:you may )?([^.]+)\.", p.card.get("text") or "")
            if not m:
                continue
            what = m.group(1)
            fits = {"Aura": "Aura" in subs, "enchantment": "Enchantment" in types, "noncreature": "Creature" not in types,
                    "Spacecraft": "Spacecraft" in subs, "creature or Spacecraft": "Creature" in types or "Spacecraft" in subs}[what]
            if not fits or (m.group(2) and p.is_("Creature")):
                continue
            if m.group(3).lower().strip() == "draw a card":      # (the wording these cards have always been announced with)
                self.draw()
                said.append(f"{p.name}: I draw a card.")
                continue
            done, left = self.run_effects(p, m.group(3))
            said += [f"{p.name}: {x}" for x in done]
            for x in left:
                self.todo.append(f"{p.name}: {x}")
        return said

    def someone_cast(self, c) -> list[str]:
        """Somebody, this seat included, cast a spell: "Whenever a player casts a noncreature spell" (Lieutenant Commander Data)."""
        said = []
        if "Creature" not in (c.get("types") or []):
            for q in self.battlefield:
                if not q.face_down and re.search(r"Whenever a player casts a noncreature spell, put a \+1/\+1 counter on", q.card.get("text") or ""):
                    q.counters += 1
                    said.append(f"{q.name} gets a +1/+1 counter.")
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
        if re.search(r"\bcreate a tapped planet land token\b", effect, re.I):
            more, left = self.run_effects(src, effect)
            for x in left:
                self.todo.append(f"{src.name}: {x}")
            return more
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
        return {"name": self.name, "commander": self.commander["name"] if self.commander else None,
                "life": self.life, "turn": self.turn,
                "hand": len(self.hand), "library": len(self.library), "commander_in_zone": self.cmdr_in_zone,
                "commander_tax": 2 * self.cmdr_casts,
                "lands": sum(1 for p in self.battlefield if p.is_("Land") and not p.face_down),   # a face-down card is a nameless 2/2, never a land
                "untapped_mana": self.available_mana(),
                "battlefield": [self.describe(p) for p in self.battlefield
                                if (p.face_down or not p.is_("Land")) and p.attached_to is None],
                "graveyard": [c["name"] for c in self.graveyard[-8:]],
                "on_their_cards": self.on_their_cards[-6:],
                # exiled face up from its library (impulse draw): public, and so is which of them it may still play, and until when
                "exile": [c["name"] for c in self.exile_zone()][-30:],
                "playable_from_exile": [{"name": e["card"]["name"], "until": self.impulse_window(e)} for e in self.impulse_zone()]}

    def shown(self, p) -> str:
        """How the table hears a permanent: its name, or "a face-down 2/2" (never the hidden name)."""
        return f"a face-down {self.stats(p)[0]}/{self.stats(p)[1]}" if p.face_down else p.name

    def todo_name(self, p) -> str:
        """A permanent in a to-do or note line: its name, or for a face-down one the board's words and its number (the owner finds it by
        the number; nobody else learns what it is: to-do lines are written where the whole table reads them)."""
        return f"{self.shown(p)} (#{p.id})" if p.face_down else p.name

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


def modal_after(text: str, opener: str) -> tuple[int, list[str]] | None:
    """(how many, the "• …" modes) that follow the line of `text` containing `opener` ("choose one —" on a trigger), or None."""
    lines = (text or "").split("\n")
    i = next((k for k, x in enumerate(lines) if opener in x), None)
    if i is None:
        return None
    m = re.search(r"choose (one|two|three|up to one)\s*[—-]", lines[i], re.I)
    modes = []
    for x in lines[i + 1:]:
        if not x.strip().startswith("•"):
            break
        modes.append(x.strip().lstrip("•").strip())
    return ({"one": 1, "two": 2, "three": 3, "up to one": 1}[m.group(1).lower()], modes) if m and modes else None


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


CARD_TYPES = ("creature", "artifact", "land", "enchantment", "planeswalker", "battle")


def _sacrifice_fits(p: Perm, what: str) -> bool:
    """Is this permanent what a "Sacrifice a/another <what>" cost asks for? A card type (creature, artifact…), 'permanent', or
    a subtype (Spirit, Goblin…). A face-down permanent is a nameless 2/2 creature with no subtypes. A token made by the
    table carries only its name ("Spirit token"), so a subtype also matches a token named after it."""
    w = what.lower()
    if " or " in w:
        return any(_sacrifice_fits(p, x) for x in w.split(" or "))
    if w == "permanent":
        return True
    types = ["Creature"] if p.face_down else p.types
    if w in CARD_TYPES:
        return w.capitalize() in types
    subs = [] if p.face_down else [s.lower() for s in (p.card.get("subtypes") or [])]
    return w in subs or (p.token and not p.face_down and p.name.lower().split(" ")[0] == w)


MCCOY_RX = re.compile(r"If another creature you control with a \+1/\+1 counter on it would be destroyed, instead remove all \+1/\+1 counters from it and all damage already dealt to it is healed")
DIES_RX = re.compile(r"(?:[\w' ]+ — )?Whenever (a creature|another creature|equipped creature|this creature or another creature you control"
                     r"|another (?P<k1>[A-Z]\w+)(?: or (?P<k2>[A-Z]\w+))? you control) dies, (?P<eff>.+)$")


def _manual(cls):
    def hand_card(self, name):
        c = _match(name, self.hand, lambda c: c["name"])
        if not c:
            raise IllegalAction(f"'{name}' is not in your hand")
        return c

    # Zefram Cochrane: "You may look at the top card of your library any time." / "Once during each of your turns, you may cast an
    # artifact spell from the top of your library." The top card is PRIVATE to this seat (brain_view only). The once-a-turn use is kept
    # in __dict__ ({source id: turn}), so a game pickled before this existed still loads.
    def top_sources(self, words):
        return [p for p in self.battlefield if not p.face_down and words in (p.card.get("text") or "")]

    def top_cast_card(self, name):
        """The top card, if it may be cast from there now (the server checks the turn and the step). Refusals never name it."""
        if not self.top_sources(CAST_TOP):
            raise IllegalAction("nothing you control lets you cast spells from the top of your library")
        used = self.__dict__.get("top_cast_used") or {}
        if not [p for p in self.top_sources(CAST_TOP) if used.get(p.id) != self.turn]:
            raise IllegalAction("you have already cast a spell from the top of your library this turn")
        c = self.library[-1] if self.library else None
        if c is None or str(name).strip().lower() != c["name"].lower() or "Artifact" not in (c.get("types") or []) or "Land" in (c.get("types") or []):
            raise IllegalAction("only the top card of your library, and only if it is a nonland artifact, can be cast from there")
        return c

    def top_castable(self):
        """(card, payment) when the top card could be cast from there and paid for now, else None (turn and step aside)."""
        try:
            c = self.top_cast_card(self.library[-1]["name"] if self.library else "")
        except IllegalAction:
            return None
        pay = self.plan_payment(c.get("manaCost", ""), -self.auto_discount(c))
        return None if pay is None else (c, pay)

    def hand_or_exile(self, name, from_exile=False):
        """(card, from_exile): the card from the hand, or from what it may play from exile: there when asked (from_exile), or when no card in
        the hand has that name. Refused once its window has closed (the card is then only in exile) and for any other seat (it is not theirs)."""
        if not from_exile:
            c = _match(name, self.hand, lambda c_: c_["name"])
            if c:
                return c, False
        c = self.impulse_card(name)
        if c:
            return c, True
        if from_exile:
            gone = _match(name, self.exile_zone(), lambda c_: c_["name"])
            raise IllegalAction(f"{gone['name']} is in exile, but you can no longer play it" if gone else
                                f"'{name}' is not a card you may play from exile")
        raise IllegalAction(f"'{name}' is not in your hand")

    def perm(self, ref):
        """A permanent by '#id' or name."""
        r0 = str(ref).strip()
        if r0.lstrip("#").isdigit():                        # "#5" or "5": the number on the board
            p = next((p for p in self.battlefield if p.id == int(r0.lstrip("#"))), None)
        else:
            p = _match(ref, self.battlefield, lambda p: p.name)
        if not p:
            raise IllegalAction(f"no permanent '{ref}' on your battlefield (name it, or give its number: #5 or 5)")
        return p

    def begin_turn(self, skip_draw: bool = False):
        self.turn += 1
        self.land_played = False
        gone_ = self.impulse_expire()                      # (only what an end of turn the table never told this seat about left open)
        stunned = []
        for p in self.battlefield:
            if p.stun and p.tapped:                         # "if it would become untapped, remove a stun counter instead"
                p.stun -= 1
                stunned.append(self.shown(p))
            else:
                p.tapped = False
            p.sick = False
        said = [f"{self.name}, turn {self.turn}."] + gone_  # not "Claude's turn": Moira's possessive comes out "Clouds turn"
        if stunned:
            said.append(f"Stunned, so still tapped: {', '.join(stunned)}.")
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
        said += self.step_triggers("your first main phase")
        for p in [p for p in self.battlefield if "Saga" in (p.card.get("subtypes") or []) and not p.face_down]:
            said += self.saga_tick(p)                       # (the first main phase is where the turn goes next)
        return said, drew

    def upkeep(self) -> list[str]:
        """'At the beginning of each upkeep' on its creatures (Verdant Embrace grants it). This runs
        on its own upkeep; each opponent's upkeep is a to-do for the brain."""
        # (Two-Headed Giant: when a teammate's half began first, the table already ran this seat's "each upkeep" for the team's one upkeep)
        said = self.step_triggers("your upkeep" if self.__dict__.get("skip_each_upkeep") else "(?:your upkeep|each upkeep)")
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

    def manual_land(self, name, bounce=None, pay_life=False, from_exile=False):
        """Play a land from the hand. A bounce land (Azorius Chancery, Gruul Turf: "When this land enters, return a land you
        control to its owner's hand") returns the land the caller names with `bounce` ('#id' or name of a land on its own
        battlefield, or 'self' for the bounce land itself). With no other land it returns itself; with others and no choice,
        it is refused — before the land is played."""
        c, from_exile = self.hand_or_exile(name, from_exile)
        if "Land" not in c["types"]:
            raise IllegalAction(f"{c['name']} is not a land")
        if self.land_played:
            raise IllegalAction("you already played a land this turn")
        karoo = "return a land you control to its owner's hand" in (c.get("text") or "")
        pick, itself = None, False
        if karoo:
            others = [p for p in self.battlefield if p.is_("Land") and not p.face_down]
            b = str(bounce).strip() if bounce not in (None, "") else ""
            if b.lower() in ("self", "itself", "this") or (b and b.lower() == c["name"].lower()
                                                           and not any(p.name.lower() == b.lower() for p in others)):
                itself = True
            elif b:
                pick = self.perm(b)                         # refused unless it is on the caller's own battlefield
                if pick not in others:
                    raise IllegalAction(f"{c['name']} returns a LAND you control: #{pick.id} {self.shown(pick)} is not one ("
                                        + ", ".join(f"#{p.id} {p.name}" for p in others) + f", or 'self'). Nothing was played")
            elif others:
                raise IllegalAction(f"{c['name']} returns a land you control to your hand: say which with --bounce REF ("
                                    + ", ".join(f"#{p.id} {p.name}" + (" (tapped)" if p.tapped else "") for p in others)
                                    + f", or 'self' for {c['name']} itself). Nothing was played")
            else:
                itself = True                               # the only land it can return is itself
        elif bounce not in (None, ""):
            raise IllegalAction(f"{c['name']} returns no land: leave out --bounce")
        said = [self.play_land(c, pay_life=pay_life, from_exile=from_exile)]
        if karoo:
            back = next(p for p in reversed(self.battlefield) if p.card is c) if itself else pick
            self.move(f"#{back.id}", "hand")                # Auras on it (Wild Growth) fall off, as with any bounce
            said.append(f"{c['name']} returns {'itself' if itself else back.name} to my hand.")
        return said

    def manual_cast(self, name, on=None, role_on=None, modes=None, targets=None, commander=False, x=0, discount=0,
                    color=None, aim=None, beam=None, top=False, from_exile=False, pay_with=None, hold=False, surcharge=0, kicked=False, kick_sac=None):
        """discount: generic cost reduction the brain knows applies (e.g. Jukai Naturalist makes
        enchantment spells cost {1} less) — the engine does not read cost-reduction text.
        color: for "as this enters, choose a color" (W/U/B/R/G)."""
        if color:
            self.next_color = color.strip().upper()[:1]
        self.__dict__["cast_x"] = int(x or 0)
        if self.commander is None and (commander or name.lower() == "commander"):
            raise IllegalAction("this deck has no commander")
        if beam:
            c = _match(name, self.graveyard, lambda c_: c_["name"])
            if not c:
                raise IllegalAction(f"'{name}' is not in your graveyard")
            cost = beam_cost(c)
            if not cost:
                raise IllegalAction(f"{c['name']} has no Beam me up")
            cre = self.perm(beam)
            if not (cre.is_("Creature") or cre.face_down):
                raise IllegalAction(f"{self.shown(cre)} is not a creature: Beam me up returns a creature you control. Nothing was paid")
            if any(a_.attached_to == cre.id and "can't be beamed up" in (a_.card.get("text") or "") for a_ in self.battlefield):
                raise IllegalAction(f"{self.shown(cre)} can't be beamed up")
            pay = self.plan_payment(cost)
            if pay is None:
                raise IllegalAction(f"can't pay {cost} to beam it up (untapped sources: {', '.join(p.name for p, _ in self.sources()) or 'none'})")
            self.graveyard.remove(c)
            self.hand.append(c)
            who = self.shown(cre)
            self.move(f"#{cre.id}", "hand")
            gone_ = self.left_graveyard()
            said = self.cast(f"cast {c['name']}", c, pay, False, lambda card, creatures: None, None, aim=aim, hold=hold)
            if hold and self.__dict__.get("held_spell"):
                self.__dict__["held_spell"].update(beam=True, x=int(x or 0))
                return [f"Beam me up: I pay {cost} and return {who} to my hand. " + said, *gone_]
            if c in self.graveyard:
                self.graveyard.remove(c)                    # "Then exile this spell"
                self.exile_zone().append(c)                 # …and it is in exile, face up, where the public list shows it
            return [f"Beam me up: I pay {cost} and return {who} to my hand. " + said + f" {c['name']} is exiled.", *gone_]
        if commander or (self.commander and name.lower() in (self.commander["name"].lower(), "commander")):
            if not self.cmdr_in_zone:
                raise IllegalAction("your commander is not in the command zone")
            c, is_cmdr = self.commander, True
            extra = 2 * self.cmdr_casts + x
        else:
            if top:
                c, from_exile = self.top_cast_card(name), False          # Zefram Cochrane: the top card
            else:
                c, from_exile = self.hand_or_exile(name, from_exile)  # the hand, or a card it may play from exile
            is_cmdr, extra = False, x
        extra -= max(0, int(discount)) + self.auto_discount(c)     # discount: reductions the engine can't see
        if from_exile and not is_cmdr:
            extra += self.exile_tax(c)                             # Zero: "a spell cast this way costs {2} more to cast"
        extra += max(0, int(surcharge or 0))                       # ward on what it aims at: paid with the spell, or it can't be aimed there
        if "Land" in c["types"]:
            raise IllegalAction(f"{c['name']} is a land — use land")
        only = None
        if pay_with:                                       # the player names which of its sources pay: they must pay the whole cost between them
            only = {self.perm(r).id for r in pay_with}
        kick = kicker_of(c) if kicked else None
        if kicked and kick is None:
            raise IllegalAction(f"{c['name']} has no kicker")
        kick_victim = None
        if kick and kick[0] == "sac":                      # "Kicker—Sacrifice an artifact or creature": an additional cost, named before anything is paid
            fits = [q for q in self.battlefield if any(q.is_(k.strip().capitalize()) for k in re.split(r" or ", kick[1]))]
            if not kick_sac:
                raise IllegalAction(f"{c['name']}: to kick it, sacrifice {kick[1]}: say which with kick_sac (" + (", ".join(f"#{q.id} {self.shown(q)}" for q in fits) or "you have none") + "). Nothing was paid")
            kick_victim = self.perm(str(kick_sac))
            if kick_victim not in fits:
                raise IllegalAction(f"{self.shown(kick_victim)} isn't {kick[1]}. Nothing was paid")
        cost_ = (c.get("manaCost", "") or "") + (kick[1] if kick and kick[0] == "mana" else "")
        pay = self.plan_payment(cost_, extra, only=only)
        if pay is None and only is not None:
            raise IllegalAction(f"those sources can't pay {c.get('manaCost')}{f' + {extra}' if extra > 0 else ''} between them "
                                f"(untapped sources: {', '.join(f'#{p.id} {p.name}' for p, _ in self.sources() if p.id >= 0) or 'none'}). Nothing was paid")
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

        if role_on:
            self.perm(role_on)                             # R18: a Role aimed at something that isn't there is refused BEFORE anything is paid or lands
        self.__dict__["role_named"] = bool(role_on)

        choose_target, choose_modes = self._choosers(c, on, role_on, modes)

        # validate modes BEFORE paying, so a bad call doesn't tap anything
        spec = modal_options(c)
        if spec and ("Instant" in c["types"] or "Sorcery" in c["types"]):
            choose_modes(c, spec)
        if top:                                            # off the top, into the spell: then exactly a cast from hand
            src = next(p for p in self.top_sources(CAST_TOP) if (self.__dict__.get("top_cast_used") or {}).get(p.id) != self.turn)
            self.__dict__.setdefault("top_cast_used", {})[src.id] = self.turn
            self.library.pop()
            self.hand.append(c)
        self.__dict__["kicked_now"] = bool(kick)
        if kick_victim is not None:
            said_k = self.move(f"#{kick_victim.id}", "graveyard")        # the additional cost: paid with the rest, before the spell is on its way
        said = self.cast(f"cast {c['name']}", c, pay, is_cmdr, choose_target, choose_modes, aim=aim, from_exile=from_exile and not is_cmdr and not top, hold=hold)
        if kick:
            said = f"I kick it" + (f" (sacrificing {kick_victim.name})." if kick_victim is not None else f" for {kick[1]}.") + " " + said
        if hold and self.__dict__.get("held_spell"):       # what the resolution will need, as plain data
            self.__dict__["held_spell"].update(on=on, role_on=role_on, modes=list(modes) if modes else None, x=int(x or 0), kicked=bool(kick))
        if top:
            said = f"{src.name}: from the top of my library. " + said
        if targets and isinstance(targets, str):           # the older form: words for the table, the table applies nothing
            said += f" Targeting {targets}."
        return [said]

    def attack_refusal(self, c) -> str | None:
        """Why this permanent can't be declared as an attacker now, on its own merits (not counting "can't attack alone"), or None."""
        if not c.is_("Creature"):
            return "not a creature"
        if c.tapped:
            return "tapped" + (" (a stun counter keeps it tapped)" if c.stun else "")
        kw_ = self.kws(c, my_turn=True)
        if c.sick and "Haste" not in kw_:
            return "summoning sick: it came under your control this turn"
        if "Defender" in kw_ and not (re.search(r"can attack as though it didn't have defender", c.card.get("text") or "")
                                      and self.__dict__.get("noncreature_turn") == self.turn):
            return "it has defender"
        if self.turn and c.attacked_turn == self.turn:
            return "it already attacked this turn"
        return None

    def attack_report(self) -> dict:
        """{permanent id: {can_attack, why_not, needs_partner}} for every creature of this seat: the one rule the engine's attack check, the
        legal list, the page's Attack button and a bot all read (T16). A creature that "can't attack alone" can attack only when another
        creature of this seat can attack with it (or is already attacking this turn); otherwise it is not ready, and says why."""
        cre = [c for c in self.battlefield if c.is_("Creature")]
        own = {c.id: self.attack_refusal(c) for c in cre}
        out = {}
        for c in cre:
            why, partner = own[c.id], False
            if why is None and attacks_alone_banned(c):
                partner = True
                others = [o for o in cre if o is not c and (own[o.id] is None or (self.turn and o.attacked_turn == self.turn))]
                if not others:
                    why = "it can't attack alone, and no other creature of yours can attack this turn"
            out[c.id] = {"can_attack": why is None, "why_not": why, "needs_partner": partner and why is None}
        return out

    def manual_attack(self, assignments: dict, role_on=None):
        """assignments: {'#12' or creature name: player name}."""
        by_player, said = {}, []
        for ref, who in assignments.items():
            c = self.perm(ref)
            why_ = self.attack_refusal(c)
            if why_ == "not a creature":
                raise IllegalAction(f"{c.name} is not a creature")
            if why_ and why_.startswith("tapped"):
                raise IllegalAction(f"{c.name} is tapped")
            if why_ and why_.startswith("summoning sick"):
                raise IllegalAction(f"{c.name} has summoning sickness")
            if why_ == "it has defender":
                raise IllegalAction(f"{c.name} has defender: it can't attack")
            by_player.setdefault(who, []).append(c)
        attackers = [c for cs in by_player.values() for c in cs]
        for c in attackers:                                # T16: "can't attack alone": with another attacker, in this declaration or already attacking
            if attacks_alone_banned(c) and not any(o is not c for o in attackers) \
                    and not any(o is not c and o.is_("Creature") and self.turn and o.attacked_turn == self.turn for o in self.battlefield):
                raise IllegalAction(f"{self.shown(c)} can't attack alone: declare it together with another creature that can attack. Nothing was declared")
        for c in attackers:                                # only now: a refused attack (sick, defender, tapped) never counts as having attacked
            c.attacked_turn = self.turn
        for who, cs in by_player.items():
            for c in cs:
                if "Vigilance" not in self.kws(c, my_turn=True):
                    c.tapped = True
            for c in cs:
                said += self.tapped_triggers(c) if c.tapped else []
                said += self.attack_triggers(c, who, attackers, role_on)
            # the forecast: every attacker unblocked, a double striker twice (it hits in both damage steps). Blocks and later changes make the
            # real number; the table says that one at the damage step (T1).
            total = sum(self.stats(c)[0] * (2 if "Double strike" in self.kws(c, my_turn=True) else 1) for c in cs)
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
        if m and re.match(r"choose (one|two)", m.group(1), re.I):
            said += self.face_dilemma()
            spec = modal_after(text, m.group(0)[:40])
            if spec:                                       # Tal Shiar Operative, V'Ger: the controller picks; the mode runs through the effect engine
                said += self.choose_modes(c, spec[1], n=spec[0], label="choose two" if spec[0] == 2 else "choose one")
                m = None
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
                whole = re.search(r"Whenever [^.\n]*?attacks[^,\n]*, ([^\n]+)", text)       # the whole line, through the shared effect engine
                line_ = re.sub(r"\s*\([^)]*\)", "", whole.group(1)).strip() if whole else ""
                if line_ and self.can_run(line_, c.name):                # Automated Warfare System: "you may sacrifice another … If you do, draw a card"
                    done, left = self.run_effects(c, line_)
                    said += [f"{self.shown(c)}: {x}" for x in done]
                    self.todo += [f"{c.name} attacks: {x}" for x in left]
                else:
                    self.todo.append(f"{c.name} attacks: {eff}")              # Sun Titan: pick from the graveyard
        for a in [a for a in self.battlefield if a.attached_to == c.id]:
            at = a.card.get("text") or ""
            if a.role == "Sorcerer":
                self.todo.append(f"{self.todo_name(c)} attacks with a Sorcerer Role: scry 1")
            g = re.search(r"\"Whenever this creature attacks, ([^\"]+?)\.?\"", at)
            if g and "untap all lands you control" in g.group(1):         # Bear Umbra
                for land in self.battlefield:
                    if land.is_("Land"):
                        land.tapped = False
                said.append(f"{a.name}: I untap all my lands.")
            elif g and "Monster Role token attached to up to one target attacking creature" in g.group(1):
                # Giant Inheritance: never replace a better Role (Ellivere's Virtuous) with Monster
                free = [x for x in attackers if not any(r.attached_to == x.id and r.role for r in self.battlefield)]
                if free and self.__dict__.get("asks"):                    # a driven seat is asked, here too
                    said += self.ask_choice(a, g.group(1), kind="role", role="Monster")
                elif free:
                    said += self.add_role(max(free, key=lambda k: self.stats(k)[0]), "Monster")
            elif g:
                self.todo.append(f"{a.name} on {self.todo_name(c)}: {g.group(1)}")
            w = re.search(r"Whenever (?:enchanted|equipped) creature attacks, ([^.]+)\.", at)
            if w:                                                         # Songbirds' Blessing, Tricorder
                done, left = self.run_effects(a, w.group(1))
                said += [f"{a.name}: {x}" for x in done]
                for x in left:
                    self.todo.append(f"{a.name} on {self.todo_name(c)}: {x}")
            # Combadge: a rider the Equipment gives, read off its text. Only another of your creatures holding a Communicator switches it on.
            if "while you control another creature with a Communicator attached to it, this creature can't be blocked this turn" in at:
                others = [x for x in self.battlefield if "Communicator" in (x.card.get("subtypes") or []) and x.attached_to not in (None, c.id)]
                if others:
                    c.no_block_turn = self.turn
                    said.append(f"{self.shown(c)} can't be blocked this turn (another creature of mine has a Communicator).")
        return said

    def tapped_triggers(self, c) -> list[str]:
        """"Whenever X becomes tapped". Only the ones that need no choice: Beckett Mariner's promotion counters, Shuttle Ace's life.
        The others are said as to-dos by the table (their text names a choice or a condition it doesn't track)."""
        if c.face_down:
            return []
        text = c.card.get("text") or ""
        said = []
        m = re.search(r"Whenever this creature becomes tapped, you gain (\d+) life", text)
        if m:
            self.life += int(m.group(1))
            self.out(kind="life", player=self.name, delta=int(m.group(1)))
            said.append(f"{self.shown(c)}: I gain {m.group(1)} life.")
        m = re.search(r"becomes tapped, put a promotion counter on her\. Then if there are three or more promotion counters on her, remove those counters and she deals (\d+) damage to each opponent", text)
        if m:
            c.promo += 1
            said.append(f"{self.shown(c)} gets a promotion counter ({c.promo}).")
            if c.promo >= 3:
                c.promo = 0
                self.out(kind="each-opponent", n=int(m.group(1)), source=self.shown(c), verb="deals")
                said.append(f"{self.shown(c)} deals {m.group(1)} to each opponent.")
        m = re.search(r"Whenever [^.\n]*?becomes tapped, ([^\n]+)", text)
        if m and not said:
            clause = re.sub(r"\s*\([^)]*\)", "", m.group(1)).strip()
            if tapped_clause_runs(clause, c.name):         # Hemmer: "draw a card, then discard a card": through the effect engine
                done, left = self.run_effects(c, clause)
                said += [f"{self.shown(c)}: {x}" for x in done]
                self.todo += [f"{c.name} tapped: {x}" for x in left]
            else:
                self.todo.append(f"{c.name} tapped: {clause.split('. ')[0].rstrip('.')}")
        return said

    def begin_combat(self) -> list[str]:
        """"At the beginning of combat on your turn": once per turn, when the turn reaches combat. The ones that make a token that dies at
        the end step (L'Rell, D-7 at 3+) run; the others are to-dos."""
        if self.__dict__.get("combat_turn") == self.turn:
            return []
        self.__dict__["combat_turn"] = self.turn
        said = []
        for p in list(self.battlefield):
            if p.face_down:
                continue
            for line in active_text(p.card, p.charge).split("\n"):
                m = re.match(r"At the beginning of combat on your turn, (.+)$", line.strip())
                if not m:
                    continue
                g = re.fullmatch(r"create a (\d+)/(\d+) ([\w ]+?) creature token with haste\. Sacrifice it at the beginning of the next end step\.", m.group(1))
                if g:
                    said += [f"{p.name}: " + x for x in self.token_phrase(1, g.group(1), g.group(2), g.group(3), haste=True)]
                    self.battlefield[-1].sac_at_end = True
                elif self.can_run(m.group(1), p.name) and not re.match(r"(if|you may)\b", m.group(1)):
                    done, left = self.run_effects(p, m.group(1))
                    said += [f"{p.name}: {x}" for x in done]
                    self.todo += [f"{p.name}: {x}" for x in left]
                else:
                    self.todo.append(f"{p.name}: {m.group(1)}")
        return said

    def end_step(self) -> list[str]:
        """The end step of this seat's own turn: tokens that were only ever for this turn go."""
        said = self.step_triggers("your end step" if self.__dict__.get("skip_each_end") else "(?:your end step|each end step)")
        for p in [p for p in self.battlefield if p.sac_at_end]:
            said += self.move(f"#{p.id}", "graveyard")
        return said

    def combat_damage(self, hits: dict, gone: dict | None = None) -> tuple[list[str], dict]:
        """hits: {attacker ref: (player, amount or None)} for creatures that dealt combat damage to a
        PLAYER (unblocked, or trample over). Returns what's said and life changes {player: delta},
        including its own lifelink gain under its own name. gone: {ref: {"perm", "attached", "kws"}} for an attacker
        that died in the same damage step it hit in (a deathtouch blocker): its damage and triggers happen as it last
        was (rule 603.10), instead of the hit failing on a creature that is no longer there."""
        said, life = [], {}
        gone = gone or {}
        ellivere_draws = len([p for p in self.battlefield
                              if "Whenever an enchanted creature you control deals combat damage to a player, draw a card"
                              in (p.card.get("text") or "")])
        pegasus_lifelink = any("Pegasus creatures you control have lifelink" in (p.card.get("text") or "")
                               for p in self.battlefield)
        for ref, (who, amount) in hits.items():
            lki = None
            try:
                c = self.perm(ref)
            except IllegalAction:
                lki = gone.get(ref)
                if lki is None:
                    raise
                c = lki["perm"]
            n = self.stats(c)[0] if amount is None else int(amount)
            if n <= 0:
                continue
            life[who] = life.get(who, 0) - n
            said.append(f"{self.shown(c)} deals {n} to {who}.")
            auras = [a for a in self.battlefield if a.attached_to == c.id] if lki is None else list(lki.get("attached") or [])
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
            kws = self.kws(c, my_turn=True) if lki is None else list(lki.get("kws") or [])
            if "Lifelink" in kws or (pegasus_lifelink and "Pegasus" in c.name):
                life[self.name] = life.get(self.name, 0) + n
                said.append(f"Lifelink: I gain {n}.")
        return said, life

    def move(self, ref, to, where=None, destroy=False, cause=None):
        """Your permanent leaves the battlefield: to graveyard / exile / hand / library. Attached Auras and Roles
        fall off; tokens cease to exist; your commander goes to the command zone. `to="library"` takes `where`:
        "top" (library.pop() draws from the END, so the end is the top), "bottom" (index 0), or "shuffle"
        (Chaos Warp: in, then the library is shuffled with the deck's own seeded rng)."""
        if to == "library" and where not in ("top", "bottom", "shuffle"):
            raise IllegalAction("to the library: where is top | bottom | shuffle")
        p = self.perm(ref)
        if destroy and to == "graveyard":                  # a destroy effect: a replacement (Dr. McCoy) may say it is not destroyed after all
            saved = self.destroy_saved(p)
            if saved is not None:
                if self.stats(p)[1] <= 0:
                    return saved + self.move(ref, to)
                return saved
        instead = to == "graveyard" and (p.is_("Creature") or p.face_down) and p.exile_tick and p.exile_tick == DEATH_TICK[0] and not probing()
        if instead:
            to = "exile"                                   # "If that creature would die this turn, exile it instead": it never dies (no death triggers)
        died = to == "graveyard" and (p.is_("Creature") or p.face_down)
        p.__dict__["was_equipped"] = tuple(a.id for a in self.battlefield if a.attached_to == p.id and is_equipment(a.card))     # (Skullclamp hears "equipped creature dies")
        for a in self.battlefield:                         # Equipment stays on the battlefield, unattached, when its creature leaves. Auras don't.
            if a.attached_to == p.id and is_equipment(a.card):
                a.attached_to = None
        gone = [p] + [a for a in self.battlefield if a.attached_to == p.id]
        tucked = False
        for x in gone:
            self.battlefield.remove(x)
            self.release_exiled(x)                         # what it held in exile "until it leaves the battlefield" comes back (Malfunctioning Holodeck)
            if x.token:
                continue
            if self.commander is not None and x.card["name"] == self.commander["name"]:
                self.cmdr_in_zone = True
            elif to == "hand" and x is p:
                self.hand.append(x.card)
            elif to == "library" and x is p:
                if where == "bottom":
                    self.library.insert(0, x.card)
                else:
                    self.library.append(x.card)
                tucked = True
            elif to == "exile":
                self.on_their_cards.append(f"{x.name} (exiled)")
            else:
                self.graveyard.append(x.card)
        if to == "library" and where == "shuffle" and tucked:
            self.rng.shuffle(self.library)
        where_txt = {"graveyard": "is destroyed", "exile": "is exiled", "hand": "returns to my hand",
                     "library": {"top": "goes on top of my library", "bottom": "goes on the bottom of my library",
                                 "shuffle": "is shuffled into my library"}.get(where, "")}[to]
        if p.token and to == "library":
            where_txt = "leaves the battlefield (a token: it's gone)"
        cmdr_note = " (commander to the command zone)" if self.commander is not None and p.card["name"] == self.commander["name"] else ""
        if died and probing():                         # effect_known's scratch player: its own "when this dies" only, nothing the table hears
            self.own_death(p)
        elif died:
            DIED_THIS_TURN[0] = True
            p.__dict__["death_cause"] = cause or ("destroy" if destroy else "state")      # (for the public `died` event: how, in the engine's own words)
            self.own_death(p)
            for tick, who_, sentence, label in p.death_riders:      # "When that creature dies this turn, you draw a card" (A Good Day to Die)
                if tick == DEATH_TICK[0]:
                    done, left = who_.run_effects(None, sentence)
                    for x in done:
                        who_.out(kind="say", text=f"{label}: {p.name} died this turn: {x}")
                    who_.todo += [f"{label} ({p.name} died): {x}" for x in left]
            if DEATH_HOOK[0]:
                DEATH_HOOK[0](self, p)
        return [f"{p.name} {where_txt}" + (" instead of dying" if instead else "") + cmdr_note + "."]

    def release_exiled(self, src: "Perm") -> None:
        """`src` has just left the battlefield: each card it held in exile (Malfunctioning Holodeck) returns to the battlefield under its OWNER's
        control, as a new object (summoning sick, no counters), and its enters triggers happen. A card that is no longer in exile stays where it is;
        a token never came back. The words go out in the owner's name."""
        held, src.held = src.held, ()
        for o, card in held:
            if probing():
                continue
            ex = o.exile_zone()
            i = next((k for k in range(len(ex) - 1, -1, -1) if ex[k] is card), None)
            if i is None:
                continue
            ex.pop(i)
            q = Perm(card, sick=True)
            if q.is_("Planeswalker"):
                q.counters = int(card.get("loyalty") or 0)
            said = []
            if "Aura" in (card.get("subtypes") or []) and not is_hostile_aura(card):
                tg = o.aura_targets(card)
                if not tg:
                    o.graveyard.append(card)
                    o.out(kind="say", text=f"{card['name']} cannot return as an Aura with nothing to enchant: it goes to the graveyard.")
                    continue
                q.attached_to = max(tg, key=lambda k: o.stats(k)[0] if k.is_("Creature") else 0).id
            o.battlefield.append(q)
            said.append(f"{card['name']} returns to the battlefield under {o.name}'s control ({src.name} left the battlefield).")
            said += o.creature_entered(q)
            said += o.enter_effects(q, lambda card_, cands, o=o: max(cands, key=lambda k: o.stats(k)[0]))
            if q.is_("Enchantment"):
                said += o.on_enchantment_enters(q)
            for x in said:
                o.out(kind="say", text=x)

    def search_library(self, name, to="hand", tapped=False):
        c = _match(name, self.library, lambda c: c["name"])
        if not c:
            raise IllegalAction(f"no '{name}' in your library")
        self.library.remove(c)
        entered = None
        if to == "battlefield":
            entered = Perm(c, sick=False, tapped=tapped)
            self.battlefield.append(entered)
        else:
            self.hand.append(c)
        self.rng.shuffle(self.library)
        said = [f"I search my library for {c['name'] if to == 'battlefield' or 'Basic' in (c.get('supertypes') or []) else 'a card'}, "
                f"put it {'onto the battlefield' + (' tapped' if tapped else '') if to == 'battlefield' else 'into my hand'}, and shuffle."]
        if entered is not None and entered.is_("Land"):
            said += self.land_entered(entered)         # a land entering, however it got there
        elif entered is not None:
            said += self.spacecraft_entered(entered)   # T13: and a Spacecraft gets Zefram's extra charge however it got there
        return said

    # ── library and zone tools the brain drives by hand (Brainstorm, Ponder, ninjutsu, blink) ──
    def peek(self, n):
        """Top n of the library, top first. PRIVATE: the brain sees it, the table doesn't."""
        return [c["name"] for c in self.library[::-1][:n]]

    def topdeck(self, name):
        c = self.hand_card(name)
        self.hand.remove(c)
        self.library.append(c)                       # library.pop() draws from the end: the end is the top
        return ["I put a card on top of my library."]

    # ── free manual control (T10, "think of Cockatrice"): a PERSON moves their own cards between zones, taps them, puts counters and makes
    # tokens, and the table logs every one in public words. A card coming out of a hidden zone into a hidden one is "a card".
    def manual(self, op, ref="", to="", kind="+1/+1", n=1, name="", power=1, toughness=1, tapped=False):
        """op: move (ref = "#id" for a permanent, or "<zone>:<card name>" for hand / graveyard / exile, or "library-top"; to = a zone) |
        counter (ref "#id", kind, n = +/-) | token (name, power, toughness, n, tapped). Tap/untap are the `tap`/`untap` actions. Every refusal
        happens before anything moves."""
        hidden = {"hand", "library-top", "library-bottom"}
        mine = lambda t_: "my " + {"library-top": "library (on top)", "library-bottom": "library (on the bottom)"}.get(t_, t_)
        if op == "token":
            n = max(1, min(int(n or 1), 20))
            nm = " ".join(str(name or "").split())[:40] or "Creature"
            return ["Manual: " + x for x in self.make_tokens(nm, int(power), int(toughness), n=n, tapped=bool(tapped))]
        if op == "counter":
            p = self.perm(ref)
            attr = COUNTER_KINDS.get(str(kind))
            if attr is None:
                raise IllegalAction(f"counters: {', '.join(COUNTER_KINDS)}")
            d = int(n or 1)
            setattr(p, attr, max(0, getattr(p, attr, 0) + d))
            return [f"Manual: {'+' if d > 0 else ''}{d} {kind} counter{'s' if abs(d) != 1 else ''} on {self.shown(p)} (now {getattr(p, attr)})."]
        if op != "move":
            raise IllegalAction("manual: move | counter | token (tap and untap have their own actions)")
        if to not in MANUAL_ZONES:
            raise IllegalAction(f"to: {', '.join(MANUAL_ZONES)}")
        ref = str(ref or "").strip()
        if ref.startswith("#") or ref.isdigit():          # a permanent of mine
            p = self.perm(ref)
            if to == "battlefield":
                raise IllegalAction(f"{self.shown(p)} is already on the battlefield")
            nm = self.shown(p)
            card = p.card
            where = {"library-top": ("library", "top"), "library-bottom": ("library", "bottom")}.get(to, (to, None))
            self.move(f"#{p.id}", where[0], where[1])
            if to == "exile" and not p.token:
                self.on_their_cards[:] = [x for x in self.on_their_cards if x != f"{p.name} (exiled)"]
                self.exile_zone().append(card)             # exiled by hand: it sits in my exile, face up, where everyone can see it
            return [f"Manual: I move {nm} from the battlefield to {mine(to)}."]
        src, _, cname = ref.partition(":")
        if src == "library-top":
            if not self.library:
                raise IllegalAction("your library is empty")
            c, take = self.library[-1], lambda: self.library.pop()
        elif src in ("hand", "graveyard", "exile"):
            zone = {"hand": self.hand, "graveyard": self.graveyard, "exile": self.exile_zone()}[src]
            c = _match(cname, zone, lambda c_: c_["name"])
            if not c:
                raise IllegalAction(f"no '{cname}' in your {src}")
            take = lambda: zone.pop(max(i for i, x in enumerate(zone) if x is c))
        else:
            raise IllegalAction("from: '#id' (a permanent), 'hand:<name>', 'graveyard:<name>', 'exile:<name>', or 'library-top'")
        if to == src:
            raise IllegalAction(f"it is already in your {src}")
        if src == "exile":
            self.__dict__["impulse"] = [e for e in self.impulse_zone() if e["card"] is not c]
        take()
        shown_ = c["name"] if not (src in hidden and to in hidden) else "a card"
        if to == "battlefield":
            q = Perm(c, sick="Haste" not in printed_keywords(c))
            self.battlefield.append(q)
            more = (self.land_entered(q) if q.is_("Land") else []) + self.creature_entered(q)
        else:
            {"hand": self.hand, "graveyard": self.graveyard, "exile": self.exile_zone()}.get(to, self.library).append(c) if to != "library-bottom" \
                else self.library.insert(0, c)
            more = []
        return [f"Manual: I move {shown_} from my {src.replace('library-top', 'library (the top)')} to {mine(to)}."] + more

    def take_from_top(self, name, n):
        """The topmost card of that name among the top n, out of the library BY POSITION. (Copies of a card are equal dicts, often the same
        one: list.remove() took the first equal card from the BOTTOM of the library, so a surveil binned a deep copy and left the top one.)"""
        L = self.library
        for i in range(len(L) - 1, max(len(L) - n, 0) - 1, -1):
            if L[i]["name"].lower() == str(name).strip().lower():
                return L.pop(i)
        raise IllegalAction(f"{name} is not among the top {n} of your library")

    def bottom(self, name, from_top=False):
        """A card to the bottom: from the hand, or (from_top) from the top of the library."""
        if from_top:
            c = _match(name, self.library[::-1][:10], lambda c: c["name"])
            if not c:
                raise IllegalAction(f"'{name}' isn't near the top of your library")
            c = self.take_from_top(c["name"], 10)
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
        said += self.spacecraft_entered(p)                 # T13: Zefram's two extra charge counters on a Spacecraft, however it came in
        if p.is_("Land"):
            said += self.land_enters(p)                    # R10: a land put onto the battlefield does its own enters text (Landfall is left to the cards' own routes: this is also the host's set-up tool)
        said += self.enter_effects(p, lambda card, cands: max(cands, key=lambda k: self.stats(k)[0]))
        if p.is_("Enchantment"):
            said += self.on_enchantment_enters(p)
        return said

    def blink(self, ref):
        """Exile one of its permanents and return it: a new object (no counters, untapped, summoning
        sick), its Auras fall off, and its enters-the-battlefield effects trigger again."""
        p = self.perm(ref)
        for a in [a for a in self.battlefield if a.attached_to == p.id]:
            if is_equipment(a.card):                       # Equipment stays, unattached
                a.attached_to = None
                continue
            self.battlefield.remove(a)
            if not a.token:
                self.graveyard.append(a.card)
        self.battlefield.remove(p)
        self.release_exiled(p)                             # (it left the battlefield: what it held in exile is back before it returns)
        if p.token:
            return [f"{p.name} is exiled. It's a token, so it doesn't come back."]
        q = Perm(p.card, sick=True)
        if q.is_("Planeswalker"):
            q.counters = int(p.card.get("loyalty") or 0)
        self.battlefield.append(q)
        said = [f"I exile {p.name} and return it."]
        if q.is_("Land"):
            said += self.land_entered(q)
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
        self.spend(pay)
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
            self.spend(pay)
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

    # ── activated abilities: the table checks the cost and runs the ones it understands ──
    ACT_LINE_V5 = re.compile(r"^((?:(?:\{[^}]+\})+|Sacrifice [^:,]+)(?:, ?(?:(?:\{[^}]+\})+|Sacrifice [^:,]+))*): (.+)$")
    ACT_LINE = re.compile(r"^((?:(?:\{[^}]+\})+|Sacrifice [^:,]+|Tap \w+ untapped [^:,]+)(?:, ?(?:(?:\{[^}]+\})+|Sacrifice [^:,]+|Tap \w+ untapped [^:,]+))*): (.+)$")
    # abilities of a card that is NOT on the battlefield: from the graveyard (exile it, or put it on the bottom of the library) or from the hand (discard it)
    ZONE_ACT = re.compile(r"^((?:\{[^}]+\})+), (Exile this card from your graveyard|Put this card from your graveyard on the bottom of your library|Discard this card): (.+)$")
    ACT_ONLY = re.compile(r"\s*Activate only (as a sorcery and only once each turn|as a sorcery|once each turn)\.?\s*$", re.I)
    ACT_LESS = re.compile(r"\s*This ability costs \{X\} less to activate, where X is the number of differently named lands you control\.?", re.I)

    def mana_words(self) -> str:
        n = len(self.sources())
        return f"you have {n} untapped mana source{'s' if n != 1 else ''}"

    def ability_why_not(self, p, index: int, sorcery_ok=None) -> str | None:
        """T18: why activated ability `index` of permanent p can't be activated right now, or None. The same checks `activate` makes, in the
        same order, before anything a player has to choose (a mode, a target, which permanent to sacrifice): what the text says about when
        (sorcery, once each turn), the {T} of a tapped or summoning-sick source, a sacrifice or tap-others cost with nothing to pay it, the
        mana (every untapped source, mana creatures and the floating pool included), and an effect the table can't run."""
        if p.face_down:
            return "it is face down"
        abs_ = self.ability_list(p)
        if not 0 <= index < len(abs_):
            return "no such ability"
        cost, eff, modes = abs_[index]
        less = ACT_LESS.search(eff)
        discount = differently_named_lands(self.battlefield) if less else 0
        eff = (eff[:less.start()] + eff[less.end():]).strip() if less else eff
        only = ACT_ONLY.search(eff)
        eff = eff[:only.start()].strip() if only else eff
        if only and "sorcery" in only.group(1).lower() and sorcery_ok is False:
            return "sorcery speed: only in your own main phase"
        if only and "once each turn" in only.group(1).lower() and (p.__dict__.get("act_used") or {}).get(index) == DEATH_TICK[0]:
            return "already activated this turn (once each turn)"
        tap, mana, others = False, "", []
        for c in [c.strip() for c in re.split(r",\s*(?![^{]*\})", cost)]:
            if c == "{T}":
                tap = True
            elif re.fullmatch(r"(\{[^}]+\})+", c):
                mana += c
            elif re.fullmatch(r"Sacrifice (this \w+|CARDNAME|" + re.escape(p.name) + ")", c, re.I):
                pass
            elif re.fullmatch(r"Sacrifice (?:(another)|a|an|one) ([A-Za-z'-]+(?: or [A-Za-z'-]+)?)", c, re.I):
                mm_ = re.fullmatch(r"Sacrifice (?:(another)|a|an|one) ([A-Za-z'-]+(?: or [A-Za-z'-]+)?)", c, re.I)
                if not [q for q in self.battlefield if _sacrifice_fits(q, mm_.group(2)) and not (mm_.group(1) and q is p)]:
                    return f"you have no {'other ' if mm_.group(1) else ''}{mm_.group(2)} to sacrifice"
            elif re.fullmatch(r"Tap (an|one|two|three|four|five|six|seven) untapped ([\w /]+?) you control", c, re.I):
                mm_ = re.fullmatch(r"Tap (an|one|two|three|four|five|six|seven) untapped ([\w /]+?) you control", c, re.I)
                kinds_ = [k_.rstrip("s").capitalize() for k_ in re.split(r" and/or | or ", mm_.group(2))]
                fits = [q for q in self.battlefield if not q.tapped and (any(q.is_(k_) for k_ in kinds_) or (q.face_down and "Creature" in kinds_))]
                if len(fits) < num(mm_.group(1)):
                    return f"needs {num(mm_.group(1))} untapped {mm_.group(2)} to tap: you have {len(fits)}"
                rep_ = self.attack_report()
                others = sorted(fits, key=lambda q: (q is p, q.is_("Land"), bool((rep_.get(q.id) or {}).get("can_attack")), self.stats(q)[0] if q.is_("Creature") else 0))[:num(mm_.group(1))]
            else:
                return f"the table can't pay '{c}' yet"
        if tap and p.tapped:
            return "it is tapped: its {T} ability waits"
        if tap and p.is_("Creature") and p.sick and "Haste" not in self.kws(p, my_turn=True):
            return "summoning sick: it can't {T} yet"
        if mana:
            was = [(q, q.tapped) for q in ([p] if tap else []) + others]
            for q, _ in was:
                q.tapped = True                            # as `activate` does: the source and what it taps can't also pay for it
            try:
                ok = self.plan_payment(mana, -discount) is not None
                words = self.mana_words()                  # (counted without the source itself: its {T} is part of the cost)
            finally:
                for q, t_ in was:
                    q.tapped = t_
            if not ok:
                return f"needs {mana}{f' (less {discount})' if discount else ''}: {words}"
        if modes:
            if not any(self.can_run(m_, p.name) for m_ in modes):
                return "the table can't run this ability yet"
            return None
        e = eff.lower()
        own = re.search(r"\bdraw (a|an|one|two|three) cards?\b|^(scry|surveil) \d|^search your library for|^put a \+1/\+1 counter on this creature|gets \+(x|\d+)/\+(x|\d+)"
                        r"|base power and toughness x/x|^(cloak|manifest) the top card|^put two charge counters on target spacecraft", e)
        if not own and not self.can_run(eff, p.name):
            return "the table can't run this ability yet"
        return None

    def zone_abilities(self) -> list[dict]:
        """What this seat's cards in its graveyard and hand can do from there: [{zone, name, cost, how, effect, sorcery, payable, runs}]."""
        out, seen = [], set()
        for zone, cards in (("graveyard", self.graveyard), ("hand", self.hand)):
            for c in cards:
                for line in re.sub(r"\s*\([^)]*\)", "", c.get("text") or "").split("\n"):
                    m = ZONE_ACT.match(line.strip())
                    if not m or (m.group(2) == "Discard this card") != (zone == "hand") or (zone, c["name"], m.group(1)) in seen:
                        continue
                    seen.add((zone, c["name"], m.group(1)))
                    eff = m.group(3)
                    only = ACT_ONLY.search(eff)
                    eff = eff[:only.start()].strip() if only else eff
                    out.append({"zone": zone, "name": c["name"], "cost": m.group(1), "how": m.group(2), "effect": eff, "sorcery": bool(only and "sorcery" in only.group(1).lower()),
                                "payable": self.plan_payment(m.group(1)) is not None, "runs": self.can_run(eff, c["name"]),
                                "slots": card_slots({"name": c["name"], "types": ["Instant"]}, eff) or None})
        return out

    def activate_zone(self, name, zone="graveyard", aim=None, sorcery_ok=None):
        """Pay and run an ability of a card in this seat's graveyard or hand ("{G}, Exile this card from your graveyard: …", "{W}{B}, Discard this
        card: …"). Checked whole before anything is paid; the card moves as its cost says, then the effect runs through the shared engine."""
        ab = next((a for a in self.zone_abilities() if a["zone"] == zone and a["name"].lower() == str(name).strip().lower()), None) or \
            next((a for a in self.zone_abilities() if a["zone"] == zone and a["name"].lower().startswith(str(name).strip().lower())), None)
        if ab is None:
            raise IllegalAction(f"no card '{name}' in your {zone} has an ability it can use from there")
        if ab["sorcery"] and sorcery_ok is False:
            raise IllegalAction(f"{ab['name']}: activate only as a sorcery (your own main phase). Nothing was paid")
        if not ab["runs"]:
            raise IllegalAction(f"the table can't run this ability yet ({ab['effect'][:80]}): nothing was paid")
        aimed = effect_shape(ab["effect"]) is not None
        if aimed and not aim and not self.__dict__.get("asks"):
            raise IllegalAction("this ability needs a target: targets [{\"at\": SEAT, \"ref\": \"#id\"}]. Nothing was paid")
        pay = self.plan_payment(ab["cost"])
        if pay is None:
            raise IllegalAction(f"can't pay {ab['cost']} (untapped sources: {', '.join(p.name for p, _ in self.sources()) or 'none'})")
        cards = self.graveyard if zone == "graveyard" else self.hand
        c = next(x for x in cards if x["name"] == ab["name"])
        self.spend(pay)
        cards.remove(c)
        said = []
        if ab["how"].startswith("Exile"):
            self.on_their_cards.append(f"{c['name']} (exiled)")
            said.append(f"{c['name']}: I pay {ab['cost']} and exile it from my graveyard.")
        elif ab["how"].startswith("Put"):
            self.library.insert(0, c)
            said.append(f"{c['name']}: I pay {ab['cost']} and put it from my graveyard on the bottom of my library.")
        else:
            self.graveyard.append(c)
            said.append(f"{c['name']}: I pay {ab['cost']} and discard it.")
        if zone == "graveyard":
            said += self.left_graveyard()
        fake = Perm({"name": c["name"], "types": [], "text": ""}, id=0)
        done, left = self.run_effects(fake, ab["effect"], aim=list(aim or []))
        self.todo += [f"{c['name']}: {x}" for x in left]
        return said + [f"{c['name']}: {x}" for x in done]

    def ability_list(self, p):
        """The non-mana activated abilities in a permanent's text: [(cost, effect, modes)]. A modal one ("{2}, Sacrifice this
        artifact: Choose one —") has its modes on the "• ..." lines that follow it; modes is [] for any other."""
        out, later = [], []
        lines = ("" if p.face_down else (p.card.get("text") or "")).split("\n")
        for i, line in enumerate(lines):
            old = ACT_LINE_V5.match(line.strip())
            m = old or ACT_LINE.match(re.sub(r"^[A-Z][\w' ]{2,30} — (?=\{|Tap |Sacrifice )", "", re.sub(r"\s*\([^)]*\)", "", line).strip()))
            bare = re.sub(r'"[^"]*"', "", line)              # (a quoted "{T}: Add …" is the token this line makes, not a mana ability)
            if m and "Add {" not in bare and "add {" not in bare and "Add one mana" not in bare and not re.match(r"Add an amount of mana", m.group(2)):
                modes = []
                if re.match(r"choose one\b", m.group(2).strip(), re.I):
                    for nxt in lines[i + 1:]:
                        if not nxt.strip().startswith("•"):
                            break
                        modes.append(nxt.strip().lstrip("•").strip())
                # The lines the older reading already listed keep their numbers (a page that counts abilities itself sends the same index);
                # the ones only this reading sees (a "Tap two untapped …" cost, an ability word in front) come after them.
                (out if old else later).append((m.group(1), m.group(2), modes))
        return out + later

    def abilities(self, p):
        """The non-mana activated abilities in a permanent's text: [(cost, effect)]."""
        return [(c, e) for c, e, _ in self.ability_list(p)]

    def station(self, ship_ref, crew_ref):
        """Station: tap ANOTHER untapped creature you control, put charge counters equal to its power on the Spacecraft. Sorcery
        timing is the caller's (the table knows the phase). A summoning-sick creature may station (the cost isn't {T})."""
        ship, crew = self.perm(ship_ref), self.perm(crew_ref)
        if not has_station(ship.card):
            raise IllegalAction(f"{ship.name} has no Station ability")
        th = station_threshold(ship.card)
        if crew.id == ship.id:
            raise IllegalAction("Station taps ANOTHER creature you control")
        if not crew.is_("Creature") or crew.tapped:
            raise IllegalAction(f"{crew.name} can't station: it must be an untapped creature you control")
        n = max(0, self.stats(crew)[0])
        crew.tapped = True
        was = ship.is_("Creature")
        before = station_grants(ship.card, ship.charge)
        ship.charge += n
        top = station_top(ship.card)
        said = [f"I tap {self.shown(crew)} to station {ship.name}: {n} charge counter{'s' if n != 1 else ''} ({ship.charge}" + (f"/{top})." if top else ").")]
        if ship.is_("Creature") and not was:
            said.append(f"{ship.name} is an artifact creature now ({'%d/%d' % self.stats(ship)}).")
        new = [k for k in station_grants(ship.card, ship.charge) if k not in before]
        if new:
            said.append(f"{ship.name} has {', '.join(k.lower() for k in new)} now.")
        said += self.tapped_triggers(crew)
        return said

    def equip(self, gear_ref, creature_ref):
        """Equip {N}: pay it, attach this Equipment to a creature you control. Sorcery timing is the caller's. Moving it pays again; it
        comes off the old creature by moving. Checked whole before anything is paid."""
        gear, cre = self.perm(gear_ref), self.perm(creature_ref)
        cost = equip_cost(gear.card)
        if not is_equipment(gear.card) or cost is None:
            raise IllegalAction(f"{gear.name} is not Equipment")
        if not (cre.is_("Creature") or cre.face_down):
            raise IllegalAction(f"{self.shown(cre)} is not a creature: Equipment goes on a creature you control")
        if gear.attached_to == cre.id:
            raise IllegalAction(f"{gear.name} is already on {self.shown(cre)}. Nothing was paid")
        pay = self.plan_payment(cost)
        if pay is None:
            raise IllegalAction(f"can't pay {cost} to equip {gear.name} (untapped sources: {', '.join(p.name for p, _ in self.sources()) or 'none'})")
        self.spend(pay)
        gear.attached_to = cre.id
        return [f"I pay {cost} to equip {gear.name} to {self.shown(cre)}."]

    def own_death(self, p):
        """p, one of this seat's own, just died: its own "when this dies". Nothing a choice is needed for runs; a to-do says the rest."""
        text = "" if p.face_down else (p.card.get("text") or "")
        m = re.search(r"When(?:ever)? (?:this creature|" + re.escape(p.name.split(",")[0]) + r") dies?( during your turn)?, ([^\n]+)", text)
        if m and m.group(1) and (STEP_KEY[0]() if STEP_KEY[0] else (self.name,))[0] not in (self.name, None):
            m = None                                       # "dies during your turn" (Starfleet Security): it is somebody else's turn
        if m:
            done, left = self.run_effects(p, m.group(2))
            for x in done:
                self.out(kind="say", text=f"{self.shown(p)} dies: {x}")
            for x in left:
                self.todo.append(f"{p.name} died: {x}")

    # "Whenever a creature dies, …" and its kin: every seat hears every death (the server's hook calls this on each seat), tokens and sacrifices and combat included.
    def creature_died(self, dead, dead_owner) -> list[str]:
        """Some seat's creature died: this seat's "whenever a creature dies" permanents hear it. The dead creature itself too, for "this creature or
        another creature you control dies" (Excalbian Gamemaster)."""
        said = []
        mine = dead_owner is self
        heard = [q for q in self.battlefield if not q.face_down and q is not dead]
        if mine and not dead.face_down and dead not in heard:
            heard.append(dead)
        for q in heard:
            text = q.card.get("text") or ""
            if q is not dead:
                m = re.search(r"Whenever a creature dies, if ([\w' ]+?) isn't a creature, put a charge counter on (?:it|[\w' ]+?)\.", text)
                if m:                                       # Sarcophagus
                    if not q.is_("Creature"):
                        q.charge += 1
                        said.append(f"{q.name} gets a charge counter ({q.charge}).")
                    continue
                m = re.search(r"Whenever another creature dies, put a \+1/\+1 counter on ([^.]+)\.(?: This ability triggers only once each turn\.)?", text)
                if m:                                       # Gowron, Battle-Scarred Survivalist
                    once = "only once each turn" in text
                    if not once or q.res_turn != -DEATH_TICK[0]:
                        if once:
                            q.res_turn = -DEATH_TICK[0]
                        q.counters += 1
                        said.append(f"{q.name} gets a +1/+1 counter.")
                    continue
            for line in re.sub(r"\s*\([^)]*\)", "", text).split("\n"):
                m = DIES_RX.match(line.strip())
                if not m:
                    continue
                scope, eff = m.group(1), m.group("eff")
                if scope == "a creature":
                    ok = q is not dead
                elif scope == "another creature":
                    ok = q is not dead
                elif scope == "equipped creature":
                    ok = q is not dead and q.id in (dead.__dict__.get("was_equipped") or ())
                elif scope.startswith("this creature or another"):
                    ok = mine
                else:                                       # "another Klingon or Warrior you control"
                    kinds = {m.group("k1"), m.group("k2")} - {None}
                    ok = q is not dead and mine and not dead.face_down and bool(kinds & set(dead.card.get("subtypes") or []))
                if not ok:
                    continue
                once = re.search(r"\s*This ability triggers only once each turn\.?", eff)
                eff = re.sub(r"\s*This ability triggers only once each turn\.?", "", eff).strip()
                if once:
                    if q.res_turn == -DEATH_TICK[0]:
                        continue
                    q.res_turn = -DEATH_TICK[0]
                done, left = self.run_effects(q, eff)
                said += [f"{q.name}: {x}" for x in done]
                self.todo += [f"{q.name} ({dead.name} died): {x}" for x in left]
        return said

    def landcycle(self, name):
        """Basic landcycling {N}: pay, discard this card, search your library for a basic land card, reveal it, put it into your hand, shuffle."""
        c = self.hand_card(name)
        cost = cycle_cost(c)
        if not cost:
            raise IllegalAction(f"{c['name']} has no landcycling")
        pay = self.plan_payment(cost)
        if pay is None:
            raise IllegalAction(f"can't pay {cost} to cycle {c['name']} (untapped sources: {', '.join(p.name for p, _ in self.sources()) or 'none'})")
        self.spend(pay)
        self.hand.remove(c)
        self.graveyard.append(c)
        said = [f"I pay {cost} and cycle {c['name']}."]
        fake = Perm({"name": c["name"], "types": [], "text": ""}, id=0)
        sentence = "search your library for a basic land card, reveal it, put it into your hand, then shuffle"
        if self.__dict__.get("asks"):
            return said + self.ask_choice(fake, sentence)
        land = next((x for x in self.library if "Basic" in (x.get("supertypes") or []) and "Land" in x["types"]), None)
        return said + (self.search_library(land["name"]) if land else ["No basic land in my library. I shuffle."])

    def clear_until_eot(self, ended=None):
        """A turn is over. ended: the seats whose turn it was (the server knows; None when the caller doesn't say): "until the end of your next
        turn" lasts through this seat's own next turn. Returns what the table says (a closed impulse window)."""
        DIED_THIS_TURN[0] = False
        DEATH_TICK[0] += 1
        for p in self.battlefield:
            p.temp, p.base_override = (0, 0), None
            p.damage, p.temp_kw, p.temp_types = 0, (), ()
        self.__dict__["mana_pool"] = None                   # mana empties
        back = self.return_delayed()                       # (a delayed return whose end step the table never ran: now, not never)
        return back + self.impulse_expire(my_turn_ended=ended is not None and self.name in ended)

    def activate(self, ref, x=0, index=None, target=None, pick=None, put=None, bottom=None, mode=None, sac_ref=None, target_owner=None,
                 tap_refs=None, sorcery_ok=None):
        """Pay and run one activated ability of a permanent. Everything is checked BEFORE anything is paid: an ability the table
        cannot run yet is refused whole, never half-done. `target` is a Perm (the caller resolved it, any seat's).
        `mode`: for a modal ability ("Choose one —"), which of its modes (0-based, in the card's order).
        `sac_ref`: for a cost that sacrifices ANOTHER permanent ("Sacrifice another creature", "Sacrifice a Spirit"), which one
        ('#id' or name, on this player's own battlefield, of the type the cost names)."""
        src = self.perm(ref)
        if src.face_down:
            raise IllegalAction("a face-down permanent has no abilities: turn it face up first")
        abs_ = self.ability_list(src)
        if not abs_:
            raise IllegalAction(f"{src.name} has no activated ability the table knows (its text: {(src.card.get('text') or '')[:140]})")
        if index is None:
            if len(abs_) > 1:
                raise IllegalAction(f"{src.name} has {len(abs_)} abilities: say which with --index 0..{len(abs_) - 1}: "
                                    + " | ".join(f"{i}: {c}: {e[:50]}" for i, (c, e, _) in enumerate(abs_)))
            index = 0
        if not 0 <= index < len(abs_):
            raise IllegalAction(f"{src.name} has abilities 0..{len(abs_) - 1}")
        cost, eff, modes = abs_[index]
        # what the text says about WHEN and for HOW MUCH, taken off the effect and enforced here
        less = ACT_LESS.search(eff)
        discount = differently_named_lands(self.battlefield) if less else 0
        eff = (eff[:less.start()] + eff[less.end():]).strip() if less else eff
        only = ACT_ONLY.search(eff)
        eff = eff[:only.start()].strip() if only else eff
        sorcery, once = bool(only and "sorcery" in only.group(1).lower()), bool(only and "once each turn" in only.group(1).lower())
        if sorcery and sorcery_ok is False:
            raise IllegalAction(f"{src.name}: activate only as a sorcery (your own main phase). Nothing was paid")
        if once and (src.__dict__.get("act_used") or {}).get(index) == DEATH_TICK[0]:
            raise IllegalAction(f"{src.name}: this ability can be activated only once each turn. Nothing was paid")
        if modes:
            if mode is None:
                raise IllegalAction(f"{src.name}: choose one — say which with --mode 0..{len(modes) - 1}: "
                                    + " | ".join(f"{i}: {m_}" for i, m_ in enumerate(modes)))
            if not 0 <= int(mode) < len(modes):
                raise IllegalAction(f"{src.name} has modes 0..{len(modes) - 1}: "
                                    + " | ".join(f"{i}: {m_}" for i, m_ in enumerate(modes)))
            eff = modes[int(mode)]
        elif mode is not None:
            raise IllegalAction(f"{src.name}'s ability is not modal ({eff[:60]}): leave out --mode")
        parts = [c.strip() for c in re.split(r",\s*(?![^{]*\})", cost)]
        tap = sac = False
        mana = ""
        sac_req = None                                     # ("another" or "", what): "Sacrifice another creature", "Sacrifice a Spirit"
        tap_req = None                                     # (how many, kinds): "Tap two untapped artifacts and/or creatures you control"
        for c in parts:
            if c == "{T}":
                tap = True
            elif re.fullmatch(r"Tap (an|one|two|three|four|five|six|seven) untapped ([\w /]+?) you control", c, re.I):
                mm_ = re.fullmatch(r"Tap (an|one|two|three|four|five|six|seven) untapped ([\w /]+?) you control", c, re.I)
                kinds_ = [k_.rstrip("s").capitalize() for k_ in re.split(r" and/or | or ", mm_.group(2))]
                tap_req = (num(mm_.group(1)), ["Artifact", "Creature", "Land", "Enchantment"] if kinds_ == ["Permanent"] else kinds_)
            elif re.fullmatch(r"Sacrifice (this \w+|CARDNAME|" + re.escape(src.name) + ")", c, re.I):
                sac = True
            elif re.fullmatch(r"Sacrifice (?:(another)|a|an|one) ([A-Za-z'-]+(?: or [A-Za-z'-]+)?)", c, re.I):
                mm_ = re.fullmatch(r"Sacrifice (?:(another)|a|an|one) ([A-Za-z'-]+(?: or [A-Za-z'-]+)?)", c, re.I)
                sac_req = (bool(mm_.group(1)), mm_.group(2))
            elif re.fullmatch(r"(\{[^}]+\})+", c):
                mana += c
            else:
                raise IllegalAction(f"the table can't pay '{c}' yet (life, discarding, more than one permanent)")
        victim = None
        if sac_req:
            another, what = sac_req
            art = "an" if what[0].lower() in "aeiou" else "a"
            a_ = "another" if another else art
            fits = [p_ for p_ in self.battlefield if _sacrifice_fits(p_, what) and not (another and p_ is src)]
            if not sac_ref:
                raise IllegalAction(f"{src.name}'s cost sacrifices {a_} {what}: say which with --sac REF ("
                                    + (", ".join(f"#{p_.id} {self.shown(p_)}" for p_ in fits) or f"you have no {what} to sacrifice")
                                    + "). Nothing was paid")
            victim = self.perm(str(sac_ref))               # yours: a permanent on someone else's battlefield is "not on your battlefield"
            if another and victim is src:
                raise IllegalAction(f"{src.name} says ANOTHER {what}: it can't sacrifice itself for this")
            if not _sacrifice_fits(victim, what):
                raise IllegalAction(f"#{victim.id} {self.shown(victim)} is not {art} {what}: "
                                    + (f"you can sacrifice {', '.join(f'#{p_.id} {self.shown(p_)}' for p_ in fits)}" if fits
                                       else f"you have no {what} to sacrifice") + ". Nothing was paid")
        elif sac_ref:
            raise IllegalAction(f"{src.name}'s cost sacrifices no other permanent: leave out --sac")
        tapped_for = []
        if tap_req:
            n_, kinds_ = tap_req
            fits = [p_ for p_ in self.battlefield if not p_.tapped and (any(p_.is_(k_) for k_ in kinds_) or (p_.face_down and "Creature" in kinds_)) and p_ is not victim]
            if tap_refs:
                tapped_for = [self.perm(str(r_)) for r_ in tap_refs]
                bad = [p_ for p_ in tapped_for if p_ not in fits]
                if bad or len({p_.id for p_ in tapped_for}) != n_:
                    raise IllegalAction(f"{src.name}'s cost taps {n_} untapped {' and/or '.join(k_.lower() + 's' for k_ in kinds_)} you control: "
                                        + (f"{self.shown(bad[0])} isn't one (or is tapped)" if bad else f"name exactly {n_}, each once")
                                        + ". You can tap: " + (", ".join(f"#{p_.id} {self.shown(p_)}" for p_ in fits) or "nothing") + ". Nothing was paid")
            else:                                          # nothing named: the ones it gives up least by tapping (never the source while others will do)
                rep_ = self.attack_report()
                order = sorted(fits, key=lambda p_: (p_ is src, p_.is_("Land"), bool((rep_.get(p_.id) or {}).get("can_attack")), self.stats(p_)[0] if p_.is_("Creature") else 0))
                tapped_for = order[:n_]
            if len(tapped_for) < n_:
                raise IllegalAction(f"{src.name}'s cost taps {n_} untapped {' and/or '.join(k_.lower() + 's' for k_ in kinds_)} you control: you have {len(fits)}. Nothing was paid")
        elif tap_refs:
            raise IllegalAction(f"{src.name}'s cost taps no other permanent: leave out tap")
        if tap and src.tapped:
            raise IllegalAction(f"{src.name} is tapped")
        if tap and src.is_("Creature") and src.sick and "Haste" not in self.kws(src, my_turn=True):
            raise IllegalAction(f"{src.name} has summoning sickness: its {{T}} ability waits")
        e = eff.lower()
        extra = int(x) * mana.count("{X}") - discount
        run = None
        m = re.search(r"\bdraw (a|an|one|two|three) cards?\b(?! for each)", e)
        if m and not re.search(r"\bdraws? (a|an|one|two|three) cards?\b.*\bthen\b", e):
            n = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}[m.group(1)]
            run = lambda: (self.draw(n), [f"{src.name}: I draw {n} card{'s' if n != 1 else ''}."])[1]
        elif re.match(r"(scry|surveil) (\d)", e):
            k = int(re.match(r"(scry|surveil) (\d)", e).group(2))
            word = re.match(r"(scry|surveil)", e).group(1)
            bad = [b for b in (bottom or []) if b.lower() not in [n.lower() for n in self.peek(k)]]
            if bad:
                raise IllegalAction(f"{', '.join(bad)} is not among the top {k} of your library")
            def run():
                self.last_peek = self.peek(k)
                out = []
                for b in (bottom or []):
                    if word == "scry":
                        self.bottom(b, from_top=True)
                    else:
                        self.graveyard.append(self.take_from_top(b, k))
                    out.append(b)
                return [f"{src.name}: I {word} {k}" + (f" and put {len(out)} on the {'bottom' if word == 'scry' else 'graveyard'}." if out else " and keep them on top.")]
            run = run
        elif e.startswith("search your library for") and re.search(r"basic land card|forest card|plains card|island card|swamp card|mountain card", e):
            dest = "battlefield" if "onto the battlefield" in e else "hand"
            tapped = "tapped" in e
            upto = "up to two" in e
            want = 2 if upto or "two basic" in e or " card and a " in e else 1
            picks = list(pick or [])
            if (len(picks) != want) and not (upto and 1 <= len(picks) <= 2) or not picks:
                raise IllegalAction(f"name the {want} land card{'s' if want != 1 else ''} to fetch with --pick (e.g. --pick Forest Plains)")
            types_ok = [t for t in ("forest", "plains", "island", "swamp", "mountain") if f"{t} card" in e]
            for pk in picks:
                card_ = next((c for c in self.library if c["name"].lower() == pk.lower()), None)
                if card_ is None:
                    raise IllegalAction(f"no '{pk}' in your library")
                basic = "Basic" in (card_.get("supertypes") or []) and "Land" in card_["types"]
                typed = any(t in [s.lower() for s in (card_.get("subtypes") or [])] for t in types_ok)
                if not (basic or typed) or "Land" not in card_["types"]:
                    raise IllegalAction(f"{pk} is not a card this ability can fetch")
            if len(set(p_.lower() for p_ in picks)) < len(picks) and len([c for c in self.library if c["name"].lower() == picks[0].lower()]) < len(picks):
                raise IllegalAction("not enough of that card left in your library")
            def run():
                said_ = []
                for pk in picks:
                    said_ += self.search_library(pk, to=dest, tapped=tapped)
                return [f"{src.name}: " + said_[0].replace("I search my library for", "I fetch", 1)] + said_[1:]
        elif re.match(r"put a \+1/\+1 counter on this creature", e):
            run = lambda: (setattr(src, "counters", src.counters + 1), [f"{src.name} gets a +1/+1 counter."])[1]
        elif re.search(r"^(this creature|" + re.escape(src.name.lower().split(",")[0]) + r"|[\w' ]+) gets \+(\d+)/\+(\d+) until end of turn", e) and not target:
            mm = re.search(r"gets \+(\d+)/\+(\d+) until end of turn", e)
            run = lambda: (setattr(src, "temp", (src.temp[0] + int(mm.group(1)), src.temp[1] + int(mm.group(2)))), [f"{src.name} gets +{mm.group(1)}/+{mm.group(2)} until end of turn."])[1]
        elif re.search(r"target creature gets \+(x|\d+)/\+(x|\d+)", e):
            if target is None:
                raise IllegalAction("this ability needs a target creature: --target REF (and --at SEAT if it is not yours)")
            if not target.is_("Creature"):
                raise IllegalAction(f"{target.name} is not a creature")
            mm = re.search(r"target creature gets \+(x|\d+)/\+(x|\d+)", e)
            bp = extra if mm.group(1) == "x" else int(mm.group(1))
            bt = extra if mm.group(2) == "x" else int(mm.group(2))
            def run():
                target.temp = (target.temp[0] + bp, target.temp[1] + bt)
                return [f"{src.name}: {target.name} gets +{bp}/+{bt} until end of turn."]
        elif "creatures you control have base power and toughness x/x" in e:
            def run():
                for c_ in self.battlefield:
                    if c_.is_("Creature"):
                        c_.base_override = (int(x), int(x))
                return [f"{src.name}: my creatures are {int(x)}/{int(x)} until end of turn."]
        elif re.match(r"(cloak|manifest) the top card of your library\.?$", e):
            word_ = re.match(r"(cloak|manifest)", e).group(1)
            if not self.library:
                raise IllegalAction(f"your library is empty: there is nothing to {word_}")
            run = lambda: [f"{src.name}: " + s for s in self.manifest(1, cloak=word_ == "cloak")]
        if run is None and re.match(r"put two charge counters on target spacecraft", e):      # Warp Core
            if target is None or not has_station(target.card) and "Spacecraft" not in (target.card.get("subtypes") or []):
                raise IllegalAction("this ability needs a target Spacecraft: --target REF")
            run = lambda: (setattr(target, "charge", target.charge + 2), [f"{src.name}: {target.name} gets 2 charge counters ({target.charge})."])[1]
        if run is None and self.can_run(eff, src.name):       # the shared sentence engine: anything it knows all of (Anomaly lands, Food, Tal Shiar Analyst)
            aimed = effect_shape(eff)
            if aimed is not None and target is None and not self.__dict__.get("asks"):     # (a driven seat is asked what it aims at, as for a trigger)
                raise IllegalAction("this ability needs a target: --target REF (and --at SEAT if it is not yours)")
            aim_ = [{"perm": target, "owner": target_owner or self}] if target is not None else []
            def run():
                done, left = self.run_effects(src, eff, aim=aim_)
                for x_ in left:
                    self.todo.append(f"{src.name}: {x_}")
                return [f"{src.name}: {x_}" for x_ in done]
        if run is None:
            raise IllegalAction(f"the table can't run this ability yet ({eff[:80]}): nothing was paid. "
                                f"Use `say` to tell the table what you do, or ask the host")
        if tap:
            src.tapped = True                              # so the payment below cannot use the source as a mana source
        for p_ in tapped_for:
            p_.tapped = True                               # (so the payment below can't also use them as mana sources)
        pay = self.plan_payment(mana, extra) if mana else []
        if pay is None:
            if tap:
                src.tapped = False
            for p_ in tapped_for:
                p_.tapped = False
            raise IllegalAction(f"can't pay {mana}{f' + {extra}' if extra > 0 else f' (less {discount})' if discount else ''} "
                                f"(untapped sources: {', '.join(p.name for p, _ in self.sources()) or 'none'})")
        self.spend(pay)
        if once:
            src.__dict__.setdefault("act_used", {})[index] = DEATH_TICK[0]
        said = [f"{src.name}: I activate it" + (f" with X={x}" if mana.count("{X}") else "")
                + (f", choosing: {eff.rstrip('.')}" if modes else "") + "."]
        if tapped_for:
            said.append("I tap " + " and ".join(self.shown(p_) for p_ in tapped_for) + " for it.")
            for p_ in tapped_for:
                said += self.tapped_triggers(p_)
        if modes:
            said += self.face_dilemma()
        if victim is not None:
            self.move(f"#{victim.id}", "graveyard")
            said.append(f"I sacrifice {self.shown(victim)}" + (" (a token: it's gone)." if victim.token else "."))
        if sac:
            said += self.move(f"#{src.id}", "graveyard")
        return said + run()

    for f in (mana_words, ability_why_not, zone_abilities, activate_zone, hand_card, hand_or_exile, top_sources, top_cast_card, top_castable, perm, ability_list, abilities, clear_until_eot, station, equip, landcycle, own_death, creature_died, tapped_triggers, begin_combat, end_step, activate, begin_turn, upkeep, manual_land, manual_cast, manual_attack, attack_refusal, attack_report, attack_triggers,
              combat_damage, move, release_exiled, search_library, make_token, make_tokens, discard, mill, peek, topdeck, take_from_top, manual, bottom,
              shuffle_library, mulligan, put, blink, cast_face_down, manifest, turn_up):
        setattr(cls, f.__name__, f)
    return cls


_manual(VirtualPlayer)


ACT_ONLY_RX = re.compile(r"\s*Activate only (as a sorcery and only once each turn|as a sorcery|once each turn)\.?\s*$", re.I)
ACT_LESS_RX = re.compile(r"\s*This ability costs \{X\} less to activate, where X is the number of differently named lands you control\.?", re.I)


# ── T15: the pre-game mulligan step (London mulligan). The server runs the step; the rule and the moves are here. ──
SIZE_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven")


def mulligan_advice(hand: list, deck_lands: int | None = None, deck_size: int | None = None, taken: int = 0) -> dict:
    """A suggestion for an opening seven, from its land count: {lands, verdict: keep | risky | mulligan, text, keep_range}.
    The band a hand should sit in comes from the deck's own land share (a 40-card limited deck with 17 lands, a 60-card deck with 24, a
    99-card deck with 37 all want three or four lands in seven; a deck built with fewer lands wants fewer). Two more or fewer than the band
    is a mulligan, one off is a risky keep. After a mulligan the bar drops: the next hand is a card smaller, so at one mulligan a one-off
    hand is a keep, at two only a hand with no lands or nothing but lands is still risky, and nobody is told to go below five cards."""
    n = len(hand)
    lands = sum(1 for c in hand if "Land" in (c.get("types") or []))
    share = (deck_lands / deck_size) if deck_lands and deck_size else 17 / 40
    lo = max(1, min(4, round(7 * share)))               # 40/17 → 3, 60/24 → 3, 99/37 → 3, 60/20 → 2
    hi = lo + 1
    off = 0 if lo <= lands <= hi else (lo - lands if lands < lo else lands - hi)
    if n != 7:                                           # not an opening seven (a test, a house rule): judge it by share alone
        off = 0 if abs(lands - n * share) <= 1 else 1 if abs(lands - n * share) <= 2 else 2
    verdict = "keep" if off == 0 else "risky" if off == 1 else "mulligan"
    if taken >= 1 and verdict == "risky":
        verdict = "keep"
    if taken >= 2 and verdict == "mulligan":
        verdict = "risky" if lands in (0, n) else "keep"
    words = f"{lands} land{'s' if lands != 1 else ''}"
    text = {"keep": f"Keep suggested ({words})", "risky": f"Risky keep ({words})", "mulligan": f"Mulligan suggested ({words})"}[verdict]
    if taken and verdict != "mulligan":
        text += f": you are already down to {SIZE_WORDS[max(0, 7 - taken)]}"
    return {"lands": lands, "verdict": verdict, "text": text, "keep_range": [lo, hi]}


def mulligan_bottoms(hand: list, n: int, deck_lands: int | None = None, deck_size: int | None = None) -> list[str]:
    """The n cards the rule would put on the bottom of a kept hand: a land while the hand holds more than the smaller hand wants, otherwise
    its most expensive spell (never its last land)."""
    share = (deck_lands / deck_size) if deck_lands and deck_size else 17 / 40
    left = list(hand)
    out = []
    for _ in range(max(0, min(n, len(hand)))):
        lands = [c for c in left if "Land" in (c.get("types") or [])]
        spells = [c for c in left if "Land" not in (c.get("types") or [])]
        want = max(2, round((len(hand) - n) * share) + 1)
        if spells and not (len(lands) > want):
            c = max(spells, key=lambda x: float(x.get("manaValue") or 0))
        elif lands:
            names = [x["name"] for x in lands]
            c = max(lands, key=lambda x: names.count(x["name"]))     # the land it has most copies of
        else:
            c = left[0]
        left.remove(c)
        out.append(c["name"])
    return out


def _mull(cls):
    def deck_shape(self) -> tuple[int, int]:
        """(lands, cards) of this seat's whole deck as it stands before the game: library and hand."""
        cards = [*self.library, *self.hand]
        return sum(1 for c in cards if "Land" in (c.get("types") or [])), len(cards)

    def mull_advice(self) -> dict:
        lands, size = self.deck_shape()
        return mulligan_advice(self.hand, lands, size, int(self.__dict__.get("mull_n", 0)))

    def mull_again(self) -> str:
        """London mulligan: the hand goes back, the library is shuffled (the deck's own seeded rng, so the order stays verifiable), seven are
        drawn. What it owes the bottom is settled when it keeps. Returns the size of the hand it will keep, as a word."""
        if self.turn or self.land_played or self.battlefield:
            raise IllegalAction("mulligans are only before the game starts")
        if self.__dict__.get("mull_kept"):
            raise IllegalAction("you have already kept your hand")
        n = int(self.__dict__.get("mull_n", 0)) + 1
        if n > 7:
            raise IllegalAction("you can't mulligan below zero cards")
        self.library.extend(self.hand)
        self.hand.clear()
        self.rng.shuffle(self.library)
        self.draw(7)
        self.recent_draws.clear()
        self.__dict__["mull_n"] = n
        self.mulligans = n
        if self.fair is not None:
            self.fair["mulligans"] = n
        return SIZE_WORDS[7 - n]

    def mull_keep(self, bottom: list | None = None) -> str:
        """Keep this seven, less one card per mulligan taken: `bottom` names them (bottom[0] ends up the very bottom card).
        Returns the size of the kept hand, as a word."""
        if self.__dict__.get("mull_kept"):
            raise IllegalAction("you have already kept your hand")
        n = int(self.__dict__.get("mull_n", 0))
        names = [str(x) for x in (bottom or [])]
        if len(names) != n:
            raise IllegalAction(f"choose exactly {n} card{'s' if n != 1 else ''} from your hand to put on the bottom of your library"
                                if n else "you took no mulligan: nothing goes on the bottom")
        left = list(self.hand)
        picked = []
        for nm in names:
            c = next((x for x in left if x["name"].lower() == nm.lower()), None)
            if c is None:
                raise IllegalAction(f"{nm} isn't in your hand (or you named it more often than you hold it). Nothing was moved")
            left.remove(c)
            picked.append(c)
        for c in reversed(picked):                         # index 0 is the bottom: the first named ends up lowest
            self.hand.remove(c)
            self.library.insert(0, c)
        self.__dict__["mull_kept"] = True
        return SIZE_WORDS[len(self.hand)] if len(self.hand) < len(SIZE_WORDS) else str(len(self.hand))

    def mull_by_rule(self, floor: int = 5) -> list[str]:
        """An engine seat (or one that didn't answer in time) decides by the suggestion: mulligan while it says so and the hand would stay at
        `floor` or more, then keep, bottoming what the rule picks. Returns the sizes it went through, the kept one last (as words)."""
        steps = []
        while not self.__dict__.get("mull_kept"):
            n = int(self.__dict__.get("mull_n", 0))
            if self.mull_advice()["verdict"] == "mulligan" and 7 - (n + 1) >= floor:
                steps.append(self.mull_again())
                continue
            lands, size = self.deck_shape()
            steps.append(self.mull_keep(mulligan_bottoms(self.hand, n, lands, size)))
        return steps

    for f in (deck_shape, mull_advice, mull_again, mull_keep, mull_by_rule):
        setattr(cls, f.__name__, f)
    return cls


_mull(VirtualPlayer)

LOOK_TOP = "You may look at the top card of your library any time"
CAST_TOP = "Once during each of your turns, you may cast an artifact spell from the top of your library"


def brain_view(p: VirtualPlayer) -> dict:
    """Everything the brain needs, including the PRIVATE hand. Never send this to a page."""
    castable = {o[1]["name"]: o[1].get("manaCost") for o in p.castable()}
    top = p.library[-1] if p.library and p.top_sources(LOOK_TOP) else None
    xcast = {o[1]["name"] for o in p.exile_castable()}
    atk = p.attack_report()
    return {
        "library_top": None if top is None else
            {"name": top["name"], "cost": top.get("manaCost") or "", "type": top.get("type"), "text": top.get("text"),
             "pt": f"{top['power']}/{top['toughness']}" if top.get("power") else None,
             "castable_now": p.top_castable() is not None, "slots": card_slots(top) or None},
        **p.public(),
        "hand": [{"name": c["name"], "cost": c.get("manaCost") or "", "type": c.get("type"),
                  "text": c.get("text"), "pt": f"{c['power']}/{c['toughness']}" if c.get("power") else None,
                  "castable_now": c["name"] in castable, "land": "Land" in c["types"],
                  "wipe": is_board_wipe(c),            # it hits every board, the caster's too (a bot keeps it while it has creatures)
                  "face_down_castable": bool(face_cost(c)) and p.plan_payment("{3}") is not None,
                  "face_up_cost": " ".join(face_cost(c)) if face_cost(c) else None,
                  "slots": (None if modal_options(c) and ("Instant" in c["types"] or "Sorcery" in c["types"]) else card_slots(c)) or None,
                  "mode_slots": ([card_slots(c, m_) for m_ in (modal_options(c) or (0, []))[1]] if modal_options(c) and ("Instant" in c["types"] or "Sorcery" in c["types"]) else None),
                  "cycle_cost": cycle_cost(c),
                  "kicker": (lambda k: {"kind": k[0], "cost": k[1], "payable": k[0] == "sac" and any(q.is_("Artifact") or q.is_("Creature") for q in p.battlefield)
                             or k[0] == "mana" and p.plan_payment((c.get("manaCost") or "") + k[1], -p.auto_discount(c)) is not None})(kicker_of(c)) if kicker_of(c) else None,
                  "shock_land": bool(re.search(r"As this land enters, you may pay \d+ life", c.get("text") or "")),
                  # for Auras: exactly where it may go ("#id name"), or "an opponent's permanent"
                  "aura_targets": (["an opponent's permanent (--on 'their card')"] if is_hostile_aura(c) else
                                   [f"#{t.id} {t.name}" for t in p.aura_targets(c)])
                  if "Aura" in (c.get("subtypes") or []) else None} for c in p.hand],
        "commander_card": None if p.commander is None else
            {"name": p.commander["name"], "cost": p.commander["manaCost"], "text": p.commander["text"],
             "castable_now": p.commander["name"] in castable},
        "permanents": [{"id": x.id, "name": x.name, "type": type_line(x), "tapped": x.tapped, "sick": x.sick,
                        "face_down": x.face_down, "turn_up_cost": (" ".join(face_cost(x.card)) if face_cost(x.card)
                                                                   else x.card.get("manaCost")) if x.face_down else None,
                        "token": x.token, "attached_to": x.attached_to, "role": x.role,
                        "pt": "%d/%d" % p.stats(x) if x.is_("Creature") else None,
                        "charge": x.charge, "station_at": station_threshold(x.card), "station_top": station_top(x.card), "has_station": has_station(x.card),
                        "lore": x.lore, "quest": x.quest, "counters": x.counters, "stun": x.stun, "damage": x.damage, "promo": x.promo, "keywords": p.kws(x, my_turn=True),
                        "equip_cost": equip_cost(x.card) if is_equipment(x.card) else None,
                        "attached_names": [p.shown(a) for a in p.battlefield if a.attached_to == x.id],
                        "no_block": x.no_block_turn == p.turn,
                        "abilities": [{"index": i_, "cost": c_, "effect": e_, "modes": m_} for i_, (c_, e_, m_) in enumerate(p.ability_list(x))],
                        "modal_asked": bool(x.__dict__.get("modal_asked")),      # its "choose one —" enters trigger went through the table's own question
                        # T16: may it be declared as an attacker now, by the engine's own rule, and if not, why (None for a non-creature)
                        "can_attack": (atk.get(x.id) or {}).get("can_attack", False), "why_not": (atk.get(x.id) or {}).get("why_not"),
                        "attack_needs_partner": (atk.get(x.id) or {}).get("needs_partner", False),
                        "text": x.card.get("text") or ""} for x in p.battlefield],
        "mana_sources": [f"{s.name} ({cols})" for s, cols in p.sources()],
        "land_played": p.land_played,
        "beam_cards": [{"name": c["name"], "cost": beam_cost(c), "type": c.get("type"), "text": c.get("text"), "payable": p.plan_payment(beam_cost(c)) is not None,
                        "instant": "Instant" in c["types"], "slots": card_slots(c) or None}
                       for c in {c_["name"]: c_ for c_ in p.graveyard}.values() if beam_cost(c)],
        # the cards it may play from exile, shaped like the hand's (the page and a bot treat them alike), with when the window closes
        "exile_playable": [{"name": c["name"], "cost": c.get("manaCost") or "", "type": c.get("type"), "text": c.get("text"),
                            "pt": f"{c['power']}/{c['toughness']}" if c.get("power") else None,
                            "castable_now": c["name"] in xcast, "land": "Land" in c["types"], "until": p.impulse_window(e), "by": e.get("by") or "",
                            "slots": (None if modal_options(c) and ("Instant" in c["types"] or "Sorcery" in c["types"]) else card_slots(c)) or None,
                            "mode_slots": ([card_slots(c, m_) for m_ in (modal_options(c) or (0, []))[1]] if modal_options(c) and ("Instant" in c["types"] or "Sorcery" in c["types"]) else None),
                            "aura_targets": (["an opponent's permanent (--on 'their card')"] if is_hostile_aura(c) else [f"#{t.id} {t.name}" for t in p.aura_targets(c)])
                            if "Aura" in (c.get("subtypes") or []) else None}
                           for e in p.impulse_zone() for c in [e["card"]]],
        "floating_mana": [f.card["mana"] for f in p.floating()],
        "paid_this_step": p.paid_now(),
        "pending_surveil": p.__dict__.get("pending_surveil", 0),
        "pending_scry": p.__dict__.get("pending_scry", 0),
        "pending_choices": list(p.__dict__.get("pending_choices") or []),
        "graveyard": [c["name"] for c in p.graveyard],
        # abilities of cards in the graveyard / hand that are used from there ("{G}, Exile this card from your graveyard: …")
        "zone_abilities": p.zone_abilities(),
    }
