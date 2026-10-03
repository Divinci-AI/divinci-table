"""🧭 Sidekick: reminds each PERSON at the table of their own triggered abilities at the right step.

It reads each person's recorded board (/api/board3d carries every card's rules text) and, when a step
begins, posts a talk line (🗨 — never read as a move) naming the triggers that fire then. It only reads
public information: the cards on the battlefield. Runs alongside the table:

  ~/.venvs/table/bin/python table/sidekick.py
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

BASE = "http://127.0.0.1:8800"
# (step it fires in, whose turn, pattern in the card's text)
TRIGGERS = [
    ("upkeep", "own", r"at the beginning of your upkeep"),
    ("upkeep", "any", r"at the beginning of each (player's )?upkeep"),
    ("draw", "own", r"at the beginning of your draw step"),
    ("beginning of combat", "own", r"at the beginning of combat on your turn"),
    ("beginning of combat", "any", r"at the beginning of each combat"),
    ("end step", "own", r"at the beginning of your end step"),
    ("end step", "any", r"at the beginning of (each|the) end step"),
]


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=8) as r:
        return json.loads(r.read())


def say(text):
    body = json.dumps({"by": "🧭 Sidekick", "talk": True, "text": text}).encode()
    urllib.request.urlopen(urllib.request.Request(BASE + "/api/chat", data=body, method="POST",
                                                  headers={"Content-Type": "application/json"}), timeout=8).read()


def sentence(text, pattern):
    """The sentence of the card's text that holds the trigger, trimmed."""
    for part in re.split(r"(?<=[.)])\s+|\n", text or ""):
        if re.search(pattern, part, re.I):
            return part.strip()[:200]
    return ""


def remind(active, step):
    b = get("/api/board3d")
    ai = set(get("/api/phase").get("ai") or [])
    for seat in b.get("seats", []):
        name = seat.get("name")
        if not name or name in ai or (seat.get("life") is not None and seat["life"] <= 0):
            continue
        lines = []
        for p in seat.get("permanents") or []:
            if p.get("face_down"):
                continue
            for st, whose, pat in TRIGGERS:
                if st == step and (whose == "any" or name == active):
                    s = sentence(p.get("text"), pat)
                    if s:
                        lines.append(f"{p['name']}: {s}")
        if lines:
            say(f"for {name} ({active}'s {step}) — " + " · ".join(dict.fromkeys(lines)))


def main():
    since = get("/api/events?since=latest").get("last", 0)
    print(f"sidekick watching from #{since}", flush=True)
    while True:
        try:
            d = get(f"/api/events?since={since}")
            for e in d.get("events", []):
                since = e["id"]
                if e.get("type") == "phase" and e.get("player") and e.get("step") in {t[0] for t in TRIGGERS}:
                    remind(e["player"], e["step"])
        except Exception as ex:                       # noqa: BLE001 — never stop reminding over one bad poll
            print(f"  ! {type(ex).__name__}: {ex}", flush=True)
            time.sleep(3)
        time.sleep(1.5)


if __name__ == "__main__":
    main()
