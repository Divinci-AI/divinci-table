#!/usr/bin/env python3
"""One-time downloads, so the table runs offline afterwards.

    python3 table/fetch_data.py                 card names + every card's rules text (about 40 MB from Scryfall)
    python3 table/fetch_data.py --set trk       also one set's cards, for the tests' fixture decks (table/.cache/cards-trk.json)

Only public card data. No card images are fetched: card art is copyrighted (see table/assets/README.md), and the
table serves art from a folder you point TABLE_ART_DIR at. Everything lands in table/.cache/ (git-ignored).
SCRYFALL_BASE overrides the API address (the tests point it at a local fake)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
BASE = os.environ.get("SCRYFALL_BASE", "https://api.scryfall.com").rstrip("/")
HEADERS = {"User-Agent": "divinci-table/0.1", "Accept": "application/json"}
KEEP = ("id", "name", "set", "collector_number", "layout", "mana_cost", "cmc", "type_line", "oracle_text", "power", "toughness",
        "colors", "color_identity", "rarity", "frame_effects", "promo_types")     # the fields the table's fixtures read; no image links


def get_json(url: str) -> dict:
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30).read().decode())


def card_names() -> Path:
    out = CACHE / "card-names.json"
    CACHE.mkdir(exist_ok=True)
    out.write_text(json.dumps(get_json(f"{BASE}/catalog/card-names")))
    return out


def set_cards(code: str) -> Path:
    """Every card of one set (Scryfall search, 175 a page, with the pause their guidelines ask for)."""
    cards: list[dict] = []
    url = f"{BASE}/cards/search?" + urllib.parse.urlencode({"q": f"set:{code}", "unique": "cards", "order": "set"})
    while url:
        page = get_json(url)
        for c in page.get("data", []):
            rec = {k: c[k] for k in KEEP if k in c}
            if not rec.get("oracle_text") and c.get("card_faces"):                 # a two-faced card: the table's shim reads the front
                f0 = c["card_faces"][0]
                rec.update({k: f0[k] for k in ("oracle_text", "mana_cost", "type_line", "power", "toughness", "colors") if k in f0})
            cards.append(rec)
        url = page.get("next_page") if page.get("has_more") else None
        if url:
            time.sleep(0.1)
    if not cards:
        sys.exit(f"Scryfall returned no cards for set '{code}'")
    out = CACHE / f"cards-{code}.json"
    CACHE.mkdir(exist_ok=True)
    out.write_text(json.dumps(cards))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="CODE", help="also fetch this set's cards (repeatable)")
    ap.add_argument("--no-oracle", action="store_true", help="skip the rules-text file (card names and sets only)")
    args = ap.parse_args()
    print("card names ->", card_names())
    if not args.no_oracle:
        sys.path.insert(0, str(HERE))
        import oracle
        oracle.db()                                                              # downloads and builds table/.cache/oracle.json once
        print("rules text ->", oracle.CACHE)
    for code in args.sets:
        print(f"set {code} ->", set_cards(code))
    print("done: the table needs no network from here on.")


if __name__ == "__main__":
    main()
