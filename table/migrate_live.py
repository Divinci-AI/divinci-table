#!/usr/bin/env python3
"""Carry a game that is running on an OLDER server (no snapshot support) into a snapshot the new
server can --restore, without stopping it and without printing any hidden card.

    ~/.venvs/table/bin/python table/migrate_live.py --order "Fusion,Sam,Claude,Michael" \\
        --turn Sam --step "main 1" --human "Michael|Kilo, Apogee Mind" --human "Sam|Captain N'ghathrod"

Reads each AI seat through the brain API (hand, board, graveyard, life, commander) and its library
order with a quiet peek of the whole library; rebuilds the VirtualPlayers from the deck files; writes
.cache/research/<game>/snapshot.pkl (0600). Prints counts and fingerprints only — the library order and
hands stay in the file. What it cannot carry: the RNG's internal state (re-seeded from the sealed seed,
so FUTURE shuffles differ from what the old process would have done — recorded in integrity-notes) and
+1/+1-style counters, which the old brain view did not expose (none were on the board at migration).
"""
import argparse
import hashlib
import itertools
import json
import os
import pickle
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import player  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8800")
ap.add_argument("--token-file", default=str(HERE / ".brain-token"))
ap.add_argument("--order", required=True)
ap.add_argument("--turn", required=True, help="whose turn it is right now")
ap.add_argument("--step", default="main 1")
ap.add_argument("--human", action="append", default=[])
ap.add_argument("--deck", action="append", default=[], help='"Seat=decks/file.json" (default: guessed from the commander)')
ap.add_argument("--out", help="snapshot path (default: the live game's research folder)")
a = ap.parse_args()
TOKEN = Path(a.token_file).read_text().strip()


def get(path, brain=False):
    r = urllib.request.Request(a.url + path, headers={"X-Brain-Token": TOKEN} if brain else {})
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read())


def post(path, body):
    r = urllib.request.Request(a.url + path, data=json.dumps(body).encode(), method="POST",
                               headers={"Content-Type": "application/json", "X-Brain-Token": TOKEN})
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read())


fp = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()[:12]
life = get("/api/life")
fair = get("/api/fair")
game = fair["game"]
decks = dict(x.split("=", 1) for x in a.deck)
for f in (HERE.parent / "decks").glob("*.json"):
    d = json.load(open(f))
    decks.setdefault("cmd:" + d["commander"][0]["name"], str(f))

VPS, DECK_OF = {}, {}
for seat in fair["seats"]:
    st = get(f"/api/brain/state?seat={urllib.parse.quote(seat)}", brain=True)
    deck = decks.get(seat) or decks["cmd:" + st["commander"]]
    cards = {}
    raw = json.load(open(deck))
    for c in raw["mainBoard"] + raw["commander"]:
        cards.setdefault(c["name"], c)
    n_lib = st["library"]
    top = post("/api/brain/peek", {"seat": seat, "n": n_lib, "quiet": True})["private"]["top"]   # top first
    vp = player.VirtualPlayer(deck, seat)                  # fresh object; every zone is overwritten below
    vp.library = [cards[n] for n in reversed(top)]          # engine: library.pop() draws from the END
    vp.hand = [cards[h["name"]] for h in st["hand"]]
    vp.graveyard = [cards.get(n, {"name": n, "types": []}) for n in st["graveyard"]]
    vp.battlefield = []
    for p in st["permanents"]:
        card = cards.get(p["name"]) or {"name": p["name"], "types": (p.get("type") or "").split(" — ")[0].split(),
                                        "type": p.get("type"), "text": p.get("text") or ""}
        vp.battlefield.append(player.Perm(card=card, tapped=p["tapped"], sick=p["sick"], token=p["token"],
                                          attached_to=p["attached_to"], role=p["role"], face_down=p["face_down"],
                                          ward2=p["face_down"], id=p["id"]))
    vp.life, vp.turn, vp.land_played = st["life"], st["turn"], st["land_played"]
    vp.cmdr_in_zone, vp.cmdr_casts = st["commander_in_zone"], st["commander_tax"] // 2
    rec_file = HERE / ".cache" / "fairness" / f"{game}-{seat}.json"
    if rec_file.exists():
        vp.fair = json.load(open(rec_file))
        vp.rng = random.Random(int(vp.fair["seed"], 16) ^ 0x5EED)   # re-seeded: the old RNG state is unreachable
    vp.recent_draws.clear()
    VPS[seat], DECK_OF[seat] = vp, deck
    print(f"  {seat}: hand {len(vp.hand)} · library {len(vp.library)} · board {len(vp.battlefield)} · "
          f"graveyard {len(vp.graveyard)} · life {vp.life} · fingerprint {fp([st['hand'], st['permanents'], st['graveyard']])}"
          f" / library {fp(top)}", flush=True)

steps = ["untap", "upkeep", "draw", "main 1", "beginning of combat", "declare attackers", "declare blockers",
         "combat damage", "main 2", "end step", "cleanup"]
snap = {"VPS": VPS, "DECK_OF": DECK_OF, "LIFE": {k: v for k, v in life.items() if k not in VPS},
        "FAIR": {"game": game, "words": {}, "revealed": fair.get("revealed", False)},
        "PHASE": {"player": a.turn, "step": steps.index(a.step)},
        "ORDER": [x.strip() for x in a.order.split(",")], "HAND_N": {}, "ev_id": 0, "saved": time.time(),
        "migrated_from": a.url}
out = Path(a.out) if a.out else HERE / ".cache" / "research" / game / "snapshot.pkl"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(pickle.dumps(snap))
os.chmod(out, 0o600)
print(f"snapshot: {out}  (turn {a.turn} · {a.step}; order {snap['ORDER']})")
