"""Declaring a commander and a deck list at the check-in (the opening ceremony), offline.

A person playing real cards names their commander (checked against the card file, oracle.py) and may add their deck list: pasted
(one card per line, '1 Sol Ring' / '1x Sol Ring' / 'Sol Ring', section headers ignored) or a link (stored as text, never fetched).
Deck lists are public in Commander once declared, so everything here is shown to every player.

Pure functions over a {card name: record} dict, so the tests need no server.
"""
from __future__ import annotations

import re
from difflib import get_close_matches

MAX_COMMANDER = 80
MAX_LIST_LINES = 200
MAX_LIST_BYTES = 20_000
MAX_URL = 300

# Section headers in Moxfield / Archidekt / MTGO / Arena exports ("Commander", "Deck", "Sideboard (2)", "Creatures:"…)
_HEADER = re.compile(r"^(commanders?|companion|deck|main\s*deck|mainboard|main|sideboard|side\s*board|maybeboard|maybe|considering|tokens?|"
                     r"creatures?|lands?|instants?|sorcery|sorceries|artifacts?|enchantments?|planeswalkers?|battles?|other|spells?)"
                     r"\s*(\(\d+\))?\s*:?$", re.I)
_SKIP_SECTION = re.compile(r"^(sideboard|side\s*board|maybeboard|maybe|considering|tokens?)\b", re.I)   # not part of the deck


class DeclareError(ValueError):
    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.suggestions = suggestions or []


_INDEX: dict = {}


def _lower_index(db: dict) -> dict[str, str]:
    """lower-case name → canonical name, built once per card file."""
    if _INDEX.get("db") is not db:
        idx = {}
        for n in db:
            idx.setdefault(n.lower(), n)
        _INDEX.update(db=db, idx=idx, names=list(idx))
    return _INDEX["idx"]


def canonical(name: str, db: dict) -> str | None:
    """The card file's spelling of name (any case), or None."""
    if name in db:
        return name
    return _lower_index(db).get(name.strip().lower())


def suggest(text: str, db: dict, n: int = 5) -> list[str]:
    """Card names for what was typed (the commander box's completion): names it starts first, cards that can lead a deck
    before ones that cannot, then close spellings, then names containing it."""
    idx = _lower_index(db)
    q = text.strip().lower()
    if not q:
        return []
    out = sorted(_dedupe([idx[k] for k in idx if k.startswith(q)]), key=lambda x: (not can_lead(db.get(x)), x))[:n]
    if len(out) < n:
        out += [idx[k] for k in get_close_matches(q, _INDEX["names"], n=n, cutoff=0.6) if idx[k] not in out]
    if len(out) < n and len(q) >= 4:
        out += [idx[k] for k in idx if q in k and idx[k] not in out][: n - len(out)]
    return out[:n]


def can_lead(rec: dict | None) -> bool:
    """A card that can be a commander: a legendary creature (or Spacecraft / Vehicle), or one whose text says so."""
    t, x = (rec or {}).get("type") or "", (rec or {}).get("text") or ""
    return ("Legendary" in t and any(k in t for k in ("Creature", "Spacecraft", "Vehicle"))) or "can be your commander" in x


def _dedupe(names: list[str]) -> list[str]:
    """'Krenko, Baron of Tin Street // Krenko, Baron of Tin Street' is the same card as its front face: keep one."""
    have = set(names)
    return sorted({n for n in names if not (" // " in n and n.split(" // ")[0] in have)})


def match_commander(text: str, db: dict) -> str:
    """A commander's name as typed → the canonical card name. Exact (any case), a unique name it starts ('Inspirit' →
    'Inspirit, Flagship Vessel'), or a unique close spelling; cards that can lead a deck win over ones that cannot ('Inspirit' is
    also an instant). Raises DeclareError with suggestions otherwise."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        raise DeclareError("name your commander")
    if len(t) > MAX_COMMANDER:
        raise DeclareError(f"a commander's name is at most {MAX_COMMANDER} characters")
    exact = canonical(t, db)
    if exact and can_lead(db.get(exact)):
        return exact
    idx = _lower_index(db)
    q = t.lower()
    starts = _dedupe([idx[k] for k in idx if k.startswith(q) and (len(k) == len(q) or k[len(q)] in ", ")])
    leaders = [n for n in starts if can_lead(db.get(n))]
    if len(leaders) == 1:
        return leaders[0]
    if exact:
        return exact
    if len(starts) == 1:
        return starts[0]
    close = _dedupe([idx[k] for k in get_close_matches(q, _INDEX["names"], n=5, cutoff=0.88)])
    close_leaders = [n for n in close if can_lead(db.get(n))]
    if len(close_leaders) == 1:
        return close_leaders[0]
    if len(close) == 1:
        return close[0]
    if len(starts) > 1:
        raise DeclareError(f"'{t}' could be more than one card: which one?", (leaders or starts)[:5])
    sug = suggest(t, db)
    raise DeclareError(f"no card named '{t}'" + (": did you mean one of these?" if sug else ""), sug)


def parse_list(text: str, db: dict) -> dict:
    """A pasted deck list → {"cards": [{"count", "name", "known"}], "total", "recognised"}. Names are kept as given (canonical
    spelling when the card file knows them); an unknown line is kept, not refused. Sideboard / maybeboard / token sections are
    not part of the deck and are left out."""
    raw = str(text or "")
    if len(raw.encode()) > MAX_LIST_BYTES:
        raise DeclareError(f"the deck list is too long (at most {MAX_LIST_BYTES // 1000} KB)")
    lines = raw.splitlines()
    if len(lines) > MAX_LIST_LINES:
        raise DeclareError(f"the deck list is too long (at most {MAX_LIST_LINES} lines)")
    cards, skipping = [], False
    for line in lines:
        s = line.strip()
        if not s or s.startswith(("#", "//")):
            continue
        if _HEADER.match(s) or (s.endswith(":") and not re.match(r"^\d", s)):
            skipping = bool(_SKIP_SECTION.match(s))
            continue
        if skipping:
            continue
        m = re.match(r"^(\d{1,3})\s*[xX]?\s+(.+)$", s)
        n, name = (int(m.group(1)), m.group(2)) if m else (1, s)
        name = re.sub(r"\s*\([A-Za-z0-9]+\).*$", "", name).strip()          # "(SET) 123"
        name = re.sub(r"\s*\*[A-Za-z]+\*\s*$", "", name).strip()            # "*F*" foil tags
        name = name[:120]
        if not name or n <= 0:
            continue
        c = canonical(name, db)
        cards.append({"count": n, "name": c or name, "known": bool(c)})
    return {"cards": cards, "total": sum(c["count"] for c in cards), "recognised": sum(c["count"] for c in cards if c["known"])}


def is_url(text: str) -> bool:
    t = str(text or "").strip()
    return bool(re.match(r"^https?://\S+$", t, re.I))


def read_decklist(text) -> dict:
    """The decklist field: a link (stored as text) or a pasted list. Returns {"url"} or {"list": text}; {} when empty."""
    t = str(text or "").strip()
    if not t:
        return {}
    if is_url(t):
        if len(t) > MAX_URL:
            raise DeclareError(f"a deck list link is at most {MAX_URL} characters")
        return {"url": t}
    return {"list": t}


def summary(decl: dict) -> str:
    """'(deck list: 99 cards, 97 recognised)' / '(deck list link: <url>)' / '' for the game log."""
    if decl.get("url"):
        return f" (deck list link: {decl['url']})"
    if decl.get("cards"):
        return f" (deck list: {decl['total']} cards, {decl['recognised']} recognised)"
    return ""
