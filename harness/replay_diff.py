#!/usr/bin/env python3
"""Does a seed reproduce a game? Play the same seed + decks + (deterministic) pilot twice and diff the public event logs.

    python3 harness/replay_diff.py [--seed 5] [--decks kaust,tuvasa] [--turns 6] [--runs DIR]

The pilot is the fake router's "first" mode (always option 1), so the only sources of difference are the table's own entropy,
its threads and its timing. Timestamps and clocks are removed before comparing; everything else (event type, who, what, the roll,
the draws) must match. It reports the first divergent event and, as a softer view, whether each SEAT's own sequence matches
(two seats acting in parallel can interleave differently without any game state differing).
Exit 0: identical. 1: differs (see the report). It never prints a hand: events are the PUBLIC stream.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
VOLATILE = {"ts", "started", "deadline", "now", "seconds_left", "secs", "id"}


def norm(e: dict) -> dict:
    return {k: v for k, v in e.items() if k not in VOLATILE}


def seat_of(e: dict):
    return e.get("speaker") or e.get("player") or e.get("addressee") or e.get("by") or e.get("name")


def diff(a: list[dict], b: list[dict]) -> dict:
    na, nb = [norm(e) for e in a], [norm(e) for e in b]
    first = next((i for i, (x, y) in enumerate(zip(na, nb)) if x != y), None if len(na) == len(nb) else min(len(na), len(nb)))
    seats = {seat_of(e) for e in a + b}
    per_seat = {s: [x for x in na if seat_of(x) == s] == [x for x in nb if seat_of(x) == s] for s in seats}
    return {"events": [len(a), len(b)], "identical": first is None, "first_divergence": first,
            "at": {"a": na[first] if first is not None and first < len(na) else None, "b": nb[first] if first is not None and first < len(nb) else None},
            "per_seat_identical": per_seat, "same_multiset": sorted(map(json.dumps, na)) == sorted(map(json.dumps, nb))}


def play(seed, decks, turns, run: Path, port: int) -> list[dict]:
    import fake_router
    srv = fake_router.start("first", 0)
    env = {**os.environ, "JETSON_BASE": srv.base}
    subprocess.run([sys.executable, str(HERE / "pilot_game.py"), "--run", str(run), "--port", str(port), "--seed", str(seed), "--decks", decks,
                    "--turns", str(turns), "--max-secs", "300"], capture_output=True, text=True, env=env, cwd=HERE.parent, timeout=400)
    return json.loads((run / "events.json").read_text())["events"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=5); ap.add_argument("--decks", default="kaust,tuvasa"); ap.add_argument("--turns", type=int, default=6)
    ap.add_argument("--runs", default=None); ap.add_argument("--port", type=int, default=8970)
    a = ap.parse_args()
    base = Path(a.runs or tempfile.mkdtemp(prefix="replay-"))
    ev1 = play(a.seed, a.decks, a.turns, base / "run1", a.port)
    ev2 = play(a.seed, a.decks, a.turns, base / "run2", a.port + 1)
    r = diff(ev1, ev2)
    print(json.dumps({"seed": a.seed, "decks": a.decks, "turns": a.turns, **r}, indent=1, default=str))
    sys.exit(0 if r["identical"] else 1)


if __name__ == "__main__":
    main()
