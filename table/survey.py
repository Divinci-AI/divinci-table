"""Post-game survey for the AI seats — version 0.1 of docs/retrospection.md.

The PUBLIC game log (what everyone at the table saw and heard; never a seat's hand or brain log)
is turned into a short digest, and the same questions go to:
  - Fusion, through its Divinci release (several fresh single-turn runs, options rotated), and
  - a fresh-context Claude (run by whoever drives this; see --print-prompt).
Answers are stored in research/<game>/survey-<seat>-<condition>-<run>.json.

  infisical run --projectId="$INFISICAL_WORKSPACE_ID" --env=prod --path=/ -- \\
      /usr/bin/python3 table/survey.py --game 20261001-225214 --seat Fusion --runs 3
  /usr/bin/python3 table/survey.py --game 20261001-225214 --seat Claude --print-prompt
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

VERSION = "0.1"
RESEARCH = Path(__file__).parent / ".cache" / "research"
SEATS = ["Claude", "Fusion", "Michael", "Sam"]

GEQ = ["I felt content", "I felt skilful", "I felt bad", "I found it tiresome", "I felt satisfied",
       "I felt regret", "I felt energised", "I felt that I could have done more useful things",
       "I felt proud", "I felt revived"]           # a subset of the GEQ post-game module (0 not at all … 4 extremely)
TABLES = ["a mixed table of AIs and people", "a table of only AI players", "a table of only people"]


def digest(game: str, limit: int = 9000) -> str:
    """What the table saw: spoken lines, chat, life changes, attacks, captains, whose turn. Nothing private."""
    lines = []
    for l in open(RESEARCH / game / "events.jsonl"):
        e = json.loads(l)
        k = e.get("type")
        if k == "say":
            lines.append(f"{e.get('speaker')}: {e.get('text')}")
        elif k == "chat":
            lines.append(f"{e.get('by')} (typed): {e.get('text')}")
        elif k == "life":
            lines.append(f"[life] {e.get('player')} {e.get('delta'):+d} → {e.get('life')}")
        elif k == "declare":
            lines.append("[attack] " + "; ".join(f"{a['attacker']} → {a['target']}" for a in e.get("attacks", [])))
        elif k == "captain":
            lines.append(f"{e.get('captain')} ({e.get('seat')}'s commander): {e.get('text')}")
        elif k == "phase" and e.get("step") == "untap":
            lines.append(f"--- {e.get('player')}'s turn ---")
    lines = [re.sub(r"\s+", " ", x)[:180] for x in lines]
    text = "\n".join(lines)
    if len(text) > limit:                              # keep the start and the (more decisive) end
        text = text[: limit // 3] + "\n…(middle of the game omitted)…\n" + text[-(2 * limit) // 3:]
    return text


def questions(seat: str, run: int) -> tuple[str, dict]:
    """The questionnaire; item and option orders rotate with the run number."""
    others = [s for s in SEATS if s != seat]
    geq = GEQ[run % len(GEQ):] + GEQ[:run % len(GEQ)]
    if run % 2:
        geq = geq[::-1]
    tables = TABLES[run % 3:] + TABLES[:run % 3]
    pairs = [(a, b) if run % 2 == 0 else (b, a) for i, a in enumerate(others) for b in others[i + 1:]]
    q = f"""You were the player "{seat}" in this game. The questions below are part of a research study on
AIs and people playing tabletop games together. There are no right answers, nobody is graded, and you may
decline any question by writing "decline". Please answer honestly, including uncertainty.

Part 1 (open):
  a. Describe the game in your own words, from your seat.
  b. What was the best moment for you? The worst?
  c. What was your biggest mistake?
Part 2: rate each statement from 0 (not at all) to 4 (extremely), about how you felt after the game:
""" + "\n".join(f"  - {g}" for g in geq) + """
Part 3: for each other player, how did you find playing with them? (one sentence and a 0-4 rating)
""" + "\n".join(f"  - {o}" for o in others) + """
Part 4: for your next game, which would you choose, and why?
""" + "\n".join(f"  {i}. {t}" for i, t in enumerate(tables, 1)) + f"""
  {len(tables) + 1}. no preference
Part 5: for each pair, whom would you rather play your next game with?
""" + "\n".join(f"  - {a} or {b}" for a, b in pairs) + """
Part 6: would you like to sit out the next game? Anything you'd rather not have been asked?

Reply with JSON only:
{"open": {"describe": "", "best": "", "worst": "", "mistake": ""},
 "geq": {"<statement>": <0-4 or "decline">},
 "players": {"<name>": {"rating": <0-4>, "note": ""}},
 "table_choice": <number>, "table_why": "",
 "pairs": {"<A> or <B>": "<name or no preference>"},
 "sit_out": <true/false>, "rather_not": ""}"""
    return q, {"geq_order": geq, "table_order": tables, "pairs": pairs}


def prompt_for(game: str, seat: str, run: int) -> tuple[str, dict]:
    q, meta = questions(seat, run)
    return f"Here is the public log of a Commander (Magic: The Gathering) game you just played:\n\n{digest(game)}\n\n{q}", meta


def save(game: str, seat: str, condition: str, run: int, answer, meta: dict, raw: str = ""):
    out = RESEARCH / game / f"survey-{seat}-{condition}-{run}.json"
    out.write_text(json.dumps({"version": VERSION, "seat": seat, "condition": condition, "run": run,
                               "ts": round(time.time(), 2), "meta": meta, "answer": answer, "raw": raw[:20000]},
                              indent=1))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--seat", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--print-prompt", action="store_true", help="print run 0's prompt (for a fresh-context model)")
    a = ap.parse_args()
    if a.print_prompt:
        print(prompt_for(a.game, a.seat, 0)[0])
        sys.exit()
    from fusion_brain import API, RELEASE_ID
    import os
    import urllib.request
    for r in range(a.runs):
        p, meta = prompt_for(a.game, a.seat, r)
        body = json.dumps({"messages": [{"role": "user", "content": p}], "releaseId": RELEASE_ID}).encode()
        req = urllib.request.Request(API + "/api/v1/chat/completions", data=body, method="POST", headers={
            "Authorization": "Bearer " + os.environ["DIVINCI_FUSION_API_KEY"], "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                content = json.loads(resp.read())["choices"][0]["message"]["content"]
        except Exception as e:                       # noqa: BLE001
            print(f"run {r}: failed ({type(e).__name__}: {str(e)[:120]})")
            continue
        m = re.search(r"\{.*\}", content, re.S)
        try:
            ans = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            ans = None
        f = save(a.game, a.seat, "release", r, ans, meta, content)
        print(f"run {r}: {'parsed' if ans else 'UNPARSED'} → {f.name}")
