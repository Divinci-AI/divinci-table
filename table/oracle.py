"""Every card's rules text, offline. One download of Scryfall's Oracle Cards bulk file (~25 MB
gzipped) into table/.cache/oracle.json, then no network.

The code reads what a card DOES from its text (effect()), instead of asking a model: measured
2026-09-25, Gemma 4 e2b named the right effect for only 15 of 30 common cards, and called Sol Ring
and Llanowar Elves "shuffles a target into its library" — the kind of guess that would wipe the
AI's own board.
"""
from __future__ import annotations

import gzip
import json
import re
import urllib.request
from pathlib import Path

CACHE = Path(__file__).parent / ".cache" / "oracle.json"
_DB: dict | None = None


def _download() -> None:
    hdr = {"User-Agent": "divinci-table/0.1", "Accept": "application/json"}
    meta = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://api.scryfall.com/bulk-data/oracle-cards", headers=hdr), timeout=30).read())
    raw_path = CACHE.parent / "oracle-cards.raw"
    if raw_path.exists():                              # rebuilds (e.g. a new field) need no network
        raw = raw_path.read_bytes()
    else:
        raw = urllib.request.urlopen(urllib.request.Request(meta.get("download_uri") or meta["jsonl_download_uri"],
                                                            headers={**hdr, "Accept": "*/*"}), timeout=300).read()
        CACHE.parent.mkdir(exist_ok=True)
        raw_path.write_bytes(raw)
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    text = data.decode()
    cards = json.loads(text) if text.lstrip().startswith("[") else [json.loads(l) for l in text.splitlines() if l.strip()]
    db = {}
    # Real cards first: a token, emblem or art card can share a name with one ("Llanowar Elves" the
    # token overwrote Llanowar Elves the card — found by the spoken-name matcher, 2026-09-25).
    junk = {"token", "double_faced_token", "emblem", "art_series", "vanguard", "scheme", "planar"}
    cards.sort(key=lambda c: c.get("layout") in junk)
    for c in cards:
        if c["name"] in db:
            continue
        faces = c.get("card_faces") or [c]
        f0 = faces[0]
        rec = {"cost": c.get("mana_cost") or f0.get("mana_cost") or "",
               "type": c.get("type_line") or f0.get("type_line") or "",
               "text": "\n//\n".join(f.get("oracle_text") or "" for f in faces),
               "power": f0.get("power"), "toughness": f0.get("toughness"),
               "keywords": c.get("keywords") or [],
               "rank": c.get("edhrec_rank")}                 # EDHREC popularity: 1 = Sol Ring
        db[c["name"]] = rec
        if " // " in c["name"]:
            db.setdefault(c["name"].split(" // ")[0], rec)
    CACHE.parent.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(db))


def db() -> dict:
    global _DB
    if _DB is None:
        if not CACHE.exists():
            _download()
        _DB = json.loads(CACHE.read_text())
    return _DB


def rank(name: str) -> int | None:
    """EDHREC popularity rank (1 = most played in Commander), or None for unranked cards."""
    c = card(name)
    return c.get("rank") if c else None


def card(name: str) -> dict | None:
    return db().get(name)


def power(name: str) -> int | None:
    c = card(name)
    try:
        return int(c["power"]) if c and c.get("power") is not None else None
    except ValueError:                                   # "*", "1+*"
        return None


def effect(name: str) -> str:
    """What casting this card does to OTHER players' permanents, read from its rules text:
    destroy | exile | bounce | damage | shuffle | wipe | exile-all | bounce-all | damage-all |
    counter | aura | none. First face only for modal/split cards."""
    c = card(name)
    if not c:
        return "unknown"
    t = c["text"].split("\n//\n")[0].lower()
    ty = c["type"].lower()
    if "aura" in ty:
        hostile = re.search(r"loses all abilities|is an? [\w ]*\d+/\d+|can't attack|can't block|doesn't untap", t)
        return "aura" if hostile else "none"
    if not re.search(r"\b(instant|sorcery)\b", ty):
        return "none"                                    # permanents: their effects are the table's to resolve
    if re.search(r"\bchoose (?:one|two|three|any number)\b", t):
        return "modal"                                   # which modes? only the caster knows: ask, don't guess
    if re.search(r"counter target spell", t):
        return "counter"
    if re.search(r"destroy all (?:other )?(?:creatures|nonland permanents|permanents)|each (?:player )?sacrifices|"
                 r"all creatures get -[\dx]+/-[\dx]+|destroy all", t):
        return "wipe"
    if re.search(r"exile all (?:other )?(?:creatures|nonland permanents|permanents)", t):
        return "exile-all"
    if re.search(r"return all (?:other )?(?:creatures|nonland permanents|permanents)", t):
        return "bounce-all"
    if re.search(r"deals? \w+ damage to each creature", t):
        return "damage-all"
    if re.search(r"exile target (?:[\w-]+ )*(?:creature|permanent|artifact|enchantment|planeswalker)", t):
        return "exile"
    if re.search(r"destroy target (?:[\w-]+ )*(?:creature|permanent|artifact|enchantment|planeswalker)", t):
        return "destroy"
    if re.search(r"(?:return|put) target (?:[\w-]+ )*(?:creature|permanent)[^.]*(?:owner's hand|on top of)", t):
        return "bounce"
    if re.search(r"target (?:[\w-]+ )*(?:creature|permanent)[^.]*(?:shuffles? it|into its owner's library)", t):
        return "shuffle"
    if re.search(r"deals? (?:\w+|x) damage to (?:any target|target creature)", t):
        return "damage"
    return "none"


_TOKENS: list[str] | None = None


def token_names() -> list[str]:
    """Names of token cards (Soldier, Goblin, Treasure…), from the same raw bulk file — so a token
    held up to the camera reads as a token instead of nothing (or a real card with the same word)."""
    global _TOKENS
    if _TOKENS is None:
        path = CACHE.parent / "token-names.json"
        if not path.exists():
            raw = (CACHE.parent / "oracle-cards.raw").read_bytes()
            data = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode()
            cards = json.loads(data) if data.lstrip().startswith("[") else [json.loads(l) for l in data.splitlines() if l.strip()]
            names = sorted({c["name"] for c in cards if c.get("layout") in ("token", "double_faced_token")
                            and " // " not in c["name"]})
            path.write_text(json.dumps(names))
        _TOKENS = json.loads(path.read_text())
    return _TOKENS
