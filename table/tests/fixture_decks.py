"""Test decks from the set's card data, so no suite ever needs a real game's decks (~/.divinci/star-trek-mtg/live/decks holds people's picks).
    TABLE_TEST_DECKS=$(python3 table/tests/fixture_decks.py) ...
Prints the directory it wrote claude.json, sam.json and michael.json to. Deliberately plain 40-card decks: what the suites search for by name is in them."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import make_deck_json_shim as shim  # noqa: E402

CARDS = {c["name"]: c for c in shim.load(os.path.expanduser("~/.divinci/star-trek-mtg/cards.json"))}
DECKS = {
    "claude": {"Swamp": 9, "Mountain": 8, "Tal Shiar Operative": 3, "Fugitive Listener": 3, "Assault Drone": 2, "Obsidian Order Operative": 2, "Cha'DIch Investigator": 2,
               "Rogue Artificial Intelligence": 1, "Boarding Action": 2, "Savvy Profiteer": 2, "Tal Shiar Analyst": 2, "Bat'leth": 2, "Tricorder": 2},
    "sam": {"Plains": 8, "Mountain": 5, "Forest": 4, "Tactical Officer": 2, "Shuttle Ace": 2, "Horta": 2, "Class-F Shuttlecraft": 2, "U.S.S. Cerritos, Following Up": 1, "D-7 Battle Cruiser": 1,
            "Xindi Surveyors": 3, "Andromedan Automatons": 2, "Boarding Action": 2, "Test of Conviction": 2, "Common Goal": 2, "Tricorder": 2, "Federation Outpost": 2},
    "michael": {"Plains": 9, "Forest": 8, "Malfunctioning Holodeck": 2, "Tricorder": 2, "Set Phasers to . . .": 2, "Warp Core": 1, "Xindi Surveyors": 3, "Tactical Officer": 3, "Shuttle Ace": 2,
                "Risa": 2, "Class-F Shuttlecraft": 2, "Test of Conviction": 2, "Horta": 2},
}


def write(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for name, cards in DECKS.items():
        (out / f"{name}.json").write_text(json.dumps({"name": name, "commander": None, "startingLife": 20, "mainBoard": [{**CARDS[n], "count": k} for n, k in cards.items()]}))
    return out


if __name__ == "__main__":
    print(write(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="testdecks-"))))
