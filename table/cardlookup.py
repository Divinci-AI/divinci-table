"""Looking a card up by name when the downloaded Oracle file does not know it (GET /api/card?name=).

The Oracle file (table/oracle.py) holds every card Scryfall had on the day it was downloaded. A set drafted before that file knows it
(the Star Trek set, on 2026-11-13: Borg Cube, Unexplored Territory and Risa all answered 404) is still played here, because each seat's
deck file carries its own card data. So the lookup falls back, in this order:

    1. the Oracle file                                  every card ever printed (as of the download)
    2. the configured set file(s)                       TABLE_CARDS_JSON, else DRAFT_CARDS_JSON, else table/.cache/cards-*.json
                                                        (a Scryfall-shaped list: python3 table/fetch_data.py --set <code>)
    3. a card that is PUBLIC at this table right now    a face-up permanent (tokens included) or a graveyard card: its own card data
    4. a card in a deck at this table                   ONLY for the table's host, or for a seat asking about its own deck

Step 4 is restricted on purpose. In a draft a deck's contents are hidden until a card is played: an open "does this name answer?" would
let anybody walk the set's card names and read the other player's deck list off the 200s and 404s. Steps 1 and 2 answer the same for
every card of the set whoever holds it, and step 3 only says what the board already shows, so none of those tells anybody anything.

Nothing here is downloaded and no set file is part of this repository: it is read from where the host points.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIELDS = ("cost", "type", "text", "power", "toughness")
_SETS: dict[str, tuple[float, dict]] = {}              # path -> (mtime, {lower name: record})


def record(cost=None, type_=None, text=None, power=None, toughness=None) -> dict:
    return {"cost": cost or "", "type": type_ or "", "text": text or "", "power": power, "toughness": toughness}


def from_scryfall(c: dict) -> dict:
    """One card of a Scryfall-shaped list (the draft's cards.json, fetch_data.py's cards-<set>.json): the front face when it has two."""
    faces = c.get("card_faces") if isinstance(c.get("card_faces"), list) else c.get("faces") if isinstance(c.get("faces"), list) else []
    f0 = faces[0] if faces and isinstance(faces[0], dict) else {}
    g = lambda k: c.get(k) if c.get(k) not in (None, "") else f0.get(k)   # noqa: E731
    text = c.get("oracle_text") or "\n//\n".join(f.get("oracle_text") or "" for f in faces if isinstance(f, dict))
    return record(g("mana_cost"), g("type_line"), text, g("power"), g("toughness"))


def from_deck(c: dict) -> dict:
    """One card as a deck file (and so a card in a zone at the table) carries it: manaCost, type, text, power, toughness."""
    return record(c.get("manaCost") or c.get("cost"), c.get("type"), c.get("text"), c.get("power"), c.get("toughness"))


def set_files() -> list[Path]:
    """The set files the host configured, in order. TABLE_CARDS_JSON may name several (comma-separated)."""
    out = []
    for raw in (os.environ.get("TABLE_CARDS_JSON") or "").split(",") + [os.environ.get("DRAFT_CARDS_JSON") or ""]:
        p = Path(raw.strip()).expanduser() if raw.strip() else None
        if p and p.is_file() and p not in out:
            out.append(p)
    out += [p for p in sorted((HERE / ".cache").glob("cards-*.json")) if p not in out]
    return out


def _set_index(path: Path) -> dict:
    try:
        mt = path.stat().st_mtime
        hit = _SETS.get(str(path))
        if hit and hit[0] == mt:
            return hit[1]
        cards = json.loads(path.read_text())
        idx: dict = {}
        for c in cards if isinstance(cards, list) else []:
            if not isinstance(c, dict) or not isinstance(c.get("name"), str):
                continue
            rec = {"name": c["name"], **from_scryfall(c)}
            idx.setdefault(c["name"].lower(), rec)
            if " // " in c["name"]:
                idx.setdefault(c["name"].split(" // ")[0].lower(), rec)
        _SETS[str(path)] = (mt, idx)
        return idx
    except (OSError, ValueError):
        return {}


def in_sets(name: str) -> dict | None:
    for p in set_files():
        rec = _set_index(p).get(name.lower())
        if rec:
            return rec
    return None


def in_cards(name: str, cards) -> dict | None:
    """The first card of that name (any case) among deck-shaped card dicts."""
    want = name.lower()
    for c in cards:
        if isinstance(c, dict) and str(c.get("name") or "").lower() == want:
            return {"name": c["name"], **from_deck(c)}
    return None


def lookup(name: str, oracle_card=None, public_cards=(), deck_cards=()) -> tuple[dict | None, str]:
    """(record with name/cost/type/text/power/toughness, where it came from) or (None, "").
    `oracle_card` is oracle.card; `public_cards` are the cards everyone at the table can see now; `deck_cards` are the cards the CALLER may
    know about beyond that (the host: every deck; a seat: its own): pass nothing for an anonymous caller."""
    name = (name or "").strip()
    if not name:
        return None, ""
    c = oracle_card(name) if oracle_card else None
    if c:
        return {"name": name, **{k: c.get(k) for k in FIELDS}}, "oracle"
    rec = in_sets(name)
    if rec:
        return rec, "set"
    rec = in_cards(name, public_cards)
    if rec:
        return rec, "table"
    rec = in_cards(name, deck_cards)
    if rec:
        return rec, "deck"
    return None, ""
