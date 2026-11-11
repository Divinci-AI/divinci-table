"""The set's cards in the shape the table plays them (the same mapping as draft/make_deck_json.py's entry()): for card_coverage.py and the tests."""
from __future__ import annotations

import json


SUPER = {"Legendary", "Basic", "Snow", "World"}


def entry(c: dict, n: int = 1) -> dict:
    left, _, right = (c.get("type_line") or "").partition("—")
    words = left.split()
    return {"name": c["name"], "count": n, "manaCost": c.get("mana_cost") or "", "manaValue": float(c.get("cmc") or 0), "type": c.get("type_line"),
            "types": [w for w in words if w not in SUPER], "subtypes": right.split(), "supertypes": [w for w in words if w in SUPER],
            "text": c.get("oracle_text") or "", "power": c.get("power"), "toughness": c.get("toughness"), "colors": c.get("colors") or [],
            "colorIdentity": c.get("color_identity") or []}


def load(path: str, set_code: str = "trk") -> list[dict]:
    seen: dict = {}
    for c in json.load(open(path)):
        if c.get("set") == set_code and c.get("layout") != "art_series" and c["name"] not in seen:
            seen[c["name"]] = entry(c)
    return list(seen.values())
