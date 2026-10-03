"""Ask Fusion (its Divinci release) to choose between options, the way docs/retrospection.md says to:
several fresh single-turn asks, the options shuffled each time, the same neutral wording, and a
"no preference" option. Every reply is kept in full in .cache/research/fusion-choices.jsonl.

  infisical run --projectId="$INFISICAL_WORKSPACE_ID" --env=prod --path=/ -- \\
      /usr/bin/python3 table/fusion_choose.py --runs 5 --topic deck
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fusion_brain import ask  # noqa: E402

TOPICS = {
    "deck": {
        "question": ("You are Fusion, an AI player at a four-player Commander (Magic: The Gathering) table with "
                     "Claude (an AI) and two people, Michael and Sam. For the next game, which of these decks "
                     "would you like to play? Choose for yourself; there is no right answer."),
        "options": [
            "Captain N'ghathrod — blue-black Horrors: mills opponents and casts their cards from their graveyards.",
            "Estrid, the Masked (Adaptive Enchantment) — green-white-blue enchantments and Auras that protect "
            "and grow its board.",
            "Sevinne, the Chronoclasm (Mystic Intellect) — blue-red-white instants and sorceries, copying and "
            "recasting spells from the graveyard.",
        ],
        "none": "No preference — any of them is fine.",
    },
}


def run(topic: str, runs: int, seed: int | None) -> list[dict]:
    t = TOPICS[topic]
    rng = random.Random(seed)
    out = []
    for i in range(runs):
        opts = t["options"][:]
        rng.shuffle(opts)
        opts.append(t["none"])                       # the opt-out always last, so it's never mistaken for "A"
        prompt = (t["question"] + "\n\n" + "\n".join(f"{n}. {o}" for n, o in enumerate(opts, 1)) +
                  '\n\nReply with JSON only: {"choice": <number>, "why": "<one or two sentences>"}')
        r = ask(prompt, dry=False)
        n = r.get("choice")
        picked = opts[n - 1] if isinstance(n, int) and 1 <= n <= len(opts) else None
        rec = {"ts": round(time.time(), 2), "topic": topic, "run": i + 1, "order": opts, "choice_number": n,
               "picked": picked, "why": r.get("why") or r.get("say", "")}
        out.append(rec)
        logd = Path(__file__).parent / ".cache" / "research"
        logd.mkdir(parents=True, exist_ok=True)
        with open(logd / "fusion-choices.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="deck", choices=list(TOPICS))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    recs = run(a.topic, a.runs, a.seed)
    for r in recs:
        print(f"run {r['run']}: #{r['choice_number']} → {(r['picked'] or '?').split(' — ')[0]}\n   why: {r['why']}")
    tally = Counter((r["picked"] or "unparseable").split(" — ")[0] for r in recs)
    print("\ntally:", dict(tally))
