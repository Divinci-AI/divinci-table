"""Turn a saved deck list ("N Card Name" lines, as the draft page writes them) into a virtual-deck JSON the table can play.
    python3 make_deck_json.py live/deck-michael.txt live/decks/michael.json "Michael's Star Trek deck"
Card data comes from cards.json (oracle text, cost, types), never from the list. An unknown name stops the run."""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest"}
SUPER = {"Legendary", "Basic", "Snow", "World"}


def front(c: dict) -> dict:
    f = (c.get("faces") or [{}])[0] if (not c.get("oracle_text") and c.get("faces")) else {}
    g = lambda k, alt=None: c.get(k) if c.get(k) not in (None, "") else f.get(alt or k)
    return {"mana_cost": g("mana_cost"), "type_line": g("type_line"), "oracle_text": g("oracle_text") or "",
            "power": g("power"), "toughness": g("toughness"), "colors": c.get("colors") or f.get("colors") or []}


def entry(c: dict, n: int) -> dict:
    d = front(c)
    left, _, right = (d["type_line"] or "").partition("—")
    words = left.split()
    return {"name": c["name"], "count": n, "manaCost": d["mana_cost"] or "", "manaValue": float(c.get("cmc") or 0),
            "type": d["type_line"], "types": [w for w in words if w not in SUPER],
            "subtypes": right.split(), "supertypes": [w for w in words if w in SUPER], "text": d["oracle_text"],
            "power": d["power"], "toughness": d["toughness"], "colors": d["colors"],
            "colorIdentity": c.get("color_identity") or d["colors"]}


def main(src: str, out: str, title: str) -> None:
    cards = json.loads(__import__("draft").CARDS_JSON.read_text())
    by: dict[str, dict] = {}
    for c in cards:                                        # prefer the Star Trek printing, else the first seen
        if c["name"] not in by or (c["set"] == "trk" and by[c["name"]]["set"] != "trk"):
            by[c["name"]] = c
    board, total, missing = [], 0, []
    for line in Path(src).read_text().splitlines():
        m = re.match(r"^\s*(\d+)x?\s+(.+?)\s*$", line)
        if not m:
            continue
        n, name = int(m.group(1)), m.group(2)
        c = by.get(name)
        if not c:
            missing.append(name)
            continue
        board.append(entry(c, n))
        total += n
    if missing:
        sys.exit(f"cards not in cards.json: {missing}")
    Path(out).write_text(json.dumps({"name": title, "commander": None, "mainBoard": board, "startingLife": 20}, indent=1))
    Path(out).chmod(0o600)
    print(f"{out}: {total} cards, {len(board)} lines")


if __name__ == "__main__":
    main(*sys.argv[1:4])
