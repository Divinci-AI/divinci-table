"""Misheard card names, straight from Whisper transcripts in this project's own runs, and what the
table makes of them. Wrong answers are the number to keep at zero; "none" is recoverable (the
player repeats it), a wrong card is not.

  ~/.venvs/table/bin/python table/tests/hearing_names.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import match as M  # noqa: E402
import voice  # noqa: E402

CASES = [  # (what Whisper wrote, the card that was said) — every one observed in a real run
    (" I cast Lanauer Elves.", "Llanowar Elves"), (" I cast Cottom as Reach.", "Kodama's Reach"),
    (" I cast Lan Aurels.", "Llanowar Elves"), (" I cast Swords to Flushes.", "Swords to Plowshares"),
    (" High-class cultivator.", "Cultivate"), (" I cast Codimus Reach.", "Kodama's Reach"),
    (" I past land our elves.", "Llanowar Elves"), (" I cast Swords to Pleasures.", "Swords to Plowshares"),
    (" I cast Cardimus Rage.", "Kodama's Reach"), (" I cast another in time.", "Smothering Tithe"),
    (" And past of an Instigator.", "Goblin Instigator"), (" I cast Arcane Zeclid.", "Arcane Signet"),
    (" I cast this within.", "Beast Within"), (" I cast Kodama's Rage.", "Kodama's Reach"),
    (" I cast Sol Ray.", "Sol Ring"), (" I cast Psychonic Rift.", "Cyclonic Rift"),
    (" Uncast Smothering Tides.", "Smothering Tithe"), (" I cast swords to cloachers.", "Swords to Plowshares"),
    ("I cast Archastristic Study.", "Rhystic Study"), (" I task O'damna's reach.", "Kodama's Reach"),
    (" I cast Arcade Zeket.", "Arcane Signet"), (" I cast land RLs.", "Llanowar Elves"),
    (" I cast Lanour Elves.", "Llanowar Elves"), (" I cast Legion Morvis.", "Legion Warboss"),
    (" I cast Skirt Prospector.", "Skirk Prospector"), ("I cast Geryx Uprising.", "Garruk's Uprising"),
    (" I play small during Marge.", "Smoldering Marsh"), (" I play Vajukapog.", "Bojuka Bog"),
    (" I cast Legion Warbus.", "Legion Warboss"), (" I play Forrest.", "Forest"), (" I play Swarm.", "Swamp"),
    (" I play Boris.", "Forest"), (" High Place in the Gleeg.", "Cinder Glade"),
    ("Eyecast, Sorgs, 2 Plosures.", "Swords to Plowshares"), ("Archastristic Study", "Rhystic Study"),
    ("I cast Lenorels.", "Llanowar Elves"), ("High Tasselano or elves.", "Llanowar Elves"),
    ("I must learn our hours.", "Llanowar Elves"),
    # correct already, must stay correct
    ("I cast Llanowar Elves.", "Llanowar Elves"), ("I play a Forest.", "Forest"), ("I cast Sol Ring.", "Sol Ring"),
    # not a named card: must stay None
    ("Does anyone want pizza?", None), ("I cast a spell.", None), ("I play really well tonight.", None),
    ("I cast my commander.", None), ("I play a land.", None), ("I cast about ten spells last game.", None),
    ("I played this deck at the store yesterday.", None),
    ("High cost mana relts.", None),                 # far + noisy: unknowable — was read as Mana Vault
]


def main():
    cat = json.loads((HERE.parent / ".cache" / "card-names.json").read_text())["data"]
    right = wrong = none = 0
    for text, want in CASES:
        got, info = M.recognise_spoken(text, cat, picker=voice.pick_heard_card)
        g = got[0] if got else None
        if g == want:
            right += 1
            continue
        if g is None:
            none += 1
        else:
            wrong += 1
        print(f"  {'✗ WRONG' if g else '· none '} {text!r:40} → {g!r:24} (said {want}) {info.get('how', '')} "
              f"{info.get('candidates', '')[:2]}")
    print(f"right {right}/{len(CASES)} · none {none} · WRONG {wrong}")
    sys.exit(1 if wrong else 0)                      # a wrong card is the one thing this may not do


if __name__ == "__main__":
    main()
