#!/usr/bin/env python3
"""Fork a paused game into an all-digital branch: turn the HUMAN seats into engine players so the
game can go on with nobody at the table (game 3, 2026-10-03, 3 a.m.: Michael and Sam went to sleep and
handed their seats to Claude).

The physical game is left exactly where it stopped: the current snapshot is copied to
snapshot-physical-<ts>.pkl, which --restore can resume when the people come back. The digital branch
cannot know what is in their physical hands or libraries, so for each human seat it:
  * rebuilds the deck from its file, minus every card the table can see (battlefield, graveyard,
    commander) — tokens are recreated, not taken from the deck;
  * shuffles the rest with a fresh secret seed (fingerprinted, never printed) and deals the recorded
    hand size from the top — a NEW hand, not the real one;
  * keeps the recorded board, counters, graveyard, life and commander tax.
Everything about the branch is written to integrity-notes.jsonl.

    ~/.venvs/table/bin/python table/fork_digital.py --snapshot <game>/snapshot.pkl \\
        --seat "Michael=decks/inspirit.json" --seat "Sam=decks/aminatou.json"
"""
import argparse
import hashlib
import json
import os
import pickle
import random
import secrets
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import player  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--snapshot", required=True)
ap.add_argument("--seat", action="append", required=True, help='"Name=decks/file.json"')
ap.add_argument("--board", default="http://127.0.0.1:8800/api/board3d", help="where the recorded human boards are read")
ap.add_argument("--command-zone", action="append", default=[], help="seat whose commander is in the command zone")
a = ap.parse_args()

import urllib.request  # noqa: E402
_raw = urllib.request.urlopen(a.board, timeout=10).read() if a.board.startswith("http") else open(a.board, "rb").read()
board = {s["name"]: s for s in json.loads(_raw)["seats"]}
snap_path = Path(a.snapshot)
d = pickle.loads(snap_path.read_bytes())
ts = time.strftime("%Y%m%d-%H%M%S")
keep = snap_path.with_name(f"snapshot-physical-{ts}.pkl")
shutil.copy2(snap_path, keep)
os.chmod(keep, 0o600)
notes = []
top = max([p.id for v in d["VPS"].values() for p in v.battlefield] + [0])
player._ids = iter(range(top + 1, top + 10_000))

for spec in a.seat:
    name, deck = spec.split("=", 1)
    s = board[name]
    raw = json.load(open(deck))
    cards = {c["name"]: c for c in raw["mainBoard"] + raw["commander"]}
    vp = player.VirtualPlayer(deck, name)          # fresh; every zone is overwritten below
    seed = secrets.randbits(128)
    vp.rng = random.Random(seed)
    lib = [c for c in raw["mainBoard"] for _ in range(c.get("count", 1))]
    vp.battlefield, vp.graveyard = [], []
    missing = []

    def take(n):
        for i, c in enumerate(lib):
            if c["name"] == n:
                return lib.pop(i)
        missing.append(n)
        return cards.get(n) or {"name": n, "types": []}

    cmdr = raw["commander"][0]["name"]
    for p in s.get("permanents") or []:
        n = p["name"]
        if p.get("token"):
            t = (p.get("type") or "").split(" // ")[0]
            card = {"name": n, "type": t, "types": [w for w in t.split(" — ")[0].split() if w not in ("Token", "Legendary", "Basic")],
                    "subtypes": t.split(" — ")[1].split() if " — " in t else [], "text": p.get("note") or ""}
        elif n == cmdr:
            card = cards[n]
        else:
            card = take(n)
        note = p.get("note") or ""
        cnt = 0
        for word in ("loyalty", "charge", "+1/+1"):
            import re
            m = re.search(rf"(\d+)\s*{re.escape(word)}", note)
            if m:
                cnt = int(m.group(1))
        vp.battlefield.append(player.Perm(card=card, tapped=bool(p.get("tapped")), sick=False,
                                          token=bool(p.get("token")), counters=cnt))
    for n in s.get("graveyard") or []:
        vp.graveyard.append(take(n))
    vp.rng.shuffle(lib)
    vp.library = lib
    vp.hand = []
    vp.draw(int(s.get("hand") or 7))
    vp.recent_draws.clear()
    on_field = any(p.name == cmdr for p in vp.battlefield)
    vp.cmdr_in_zone = not on_field
    vp.cmdr_casts = 1                                 # each commander had been cast once at the table
    vp.life = d["LIFE"].pop(name, s.get("life") or 40)
    vp.turn = max(v.turn for v in d["VPS"].values())
    vp.land_played = False
    d["VPS"][name], d["DECK_OF"][name] = vp, deck
    fp = hashlib.sha256(f"{seed}".encode()).hexdigest()[:12]
    msg = (f"{name}: digital seat from {Path(deck).name} — board {len(vp.battlefield)}, graveyard {len(vp.graveyard)}, "
           f"new hand {len(vp.hand)} dealt from a fresh shuffle (seed fingerprint {fp}), library {len(vp.library)}, "
           f"life {vp.life}, commander {'in the command zone' if vp.cmdr_in_zone else 'on the battlefield'}"
           + (f"; not found in the deck list (kept as-is): {missing}" if missing else ""))
    print(msg)
    notes.append(msg)

d["HAND_N"] = {k: v for k, v in (d.get("HAND_N") or {}).items() if k not in d["VPS"]}
d["PUBLIC_BOARD"] = {k: v for k, v in (d.get("PUBLIC_BOARD") or {}).items() if k not in d["VPS"]}
tmp = snap_path.with_suffix(".tmp")
tmp.write_bytes(pickle.dumps(d))
os.chmod(tmp, 0o600)
tmp.replace(snap_path)
with open(snap_path.parent / "integrity-notes.jsonl", "a") as f:
    f.write(json.dumps({"ts": int(time.time()), "note": (
        "DIGITAL FORK at 3 a.m.: Michael and Sam went to sleep and handed their seats to Claude; Michael asked for "
        "the rest of the game to be AI against AI. The physical game is preserved untouched at "
        f"{keep.name} (resume with --restore). From here the game is a separate branch: the human seats are engine "
        "players whose HANDS AND LIBRARIES ARE RECONSTRUCTED (the real ones are face down on the table), so results "
        "after this point are not a continuation of the human game. Claude pilots Claude, Michael and Sam; Fusion "
        "plays itself. Claude plays each seat for that seat's own win, with no deals between them, and is barred "
        "from using one seat's private hand to steer another — but one mind holds three hands, which a referee "
        "cannot prevent and the analysis must treat as a confound. ") + " | ".join(notes)}) + "\n")
print(f"physical game kept at {keep}")
