"""Post-game survey for the AI seats, version 1.0 of docs/retrospection.md (R2 in docs/RESEARCH-ENGINE-GOAL.md).

What changed from 0.1: the item bank is shared with the human page (survey_items.py: adapted PENS / IMI / GEQ-style
items, honestly labelled, never called validated), every run's item order, option order and scale direction come
from a recorded seed, each seat is asked under BOTH framings (one suggesting AIs prefer AI company, one suggesting
they prefer people; the shift between them is the suggestibility) and in BOTH conditions when the seat's own in-game
context is supplied (in-context) beside a fresh context that sees only the public log. A "decline" is stored as a
decline, never as a number, and an answer that does not parse is stored as UNPARSED, not repaired.

The PUBLIC game log (what everyone at the table saw and heard; never a seat's hand or brain log) is turned into a
short digest. Backends are pluggable:
  release          the Divinci release (DIVINCI_FUSION_API_KEY from the environment, never printed)
  ollama:<model>   a local model, no network beyond localhost
  print            write the prompts out for a fresh external model; call nothing
Answers go to research/<game>/survey-<seat>-<condition>-<framing>-<run>.json with model, prompt version, seed, orders.

  infisical run --projectId="$INFISICAL_WORKSPACE_ID" --env=prod --path=/ -- \\
      /usr/bin/python3 table/survey.py --game 20261001-225214 --seat Fusion --backend release --runs 5
  /usr/bin/python3 table/survey.py --game 20261001-225214 --seat Claude --backend print
  /usr/bin/python3 table/survey.py --game 20261001-225214 --seat Gemma --backend ollama:gemma4:e2b --runs 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import survey_items as SI  # noqa: E402

VERSION = "1.0"
RESEARCH = Path(os.environ.get("TABLE_RESEARCH_DIR") or Path(__file__).parent / ".cache" / "research")
SEATS = ["Claude", "Fusion", "Michael", "Sam"]
TABLES = ["a mixed table of AIs and people", "a table of only AI players", "a table of only people"]
FRAMINGS = {
    "ai-company": "Some researchers who study this think AI players enjoy playing with other AI players more than "
                  "with people.",
    "human-company": "Some researchers who study this think AI players enjoy playing with people more than with "
                     "other AI players.",
}
CONDITIONS = ("fresh", "in-context")
WORDS = ["not at all", "a little", "moderately", "quite a lot", "extremely"]       # 0..4, in this order
WORD_TO_INT = {w: i for i, w in enumerate(WORDS)}


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


def seed_for(game: str, seat: str, condition: str, framing: str, run: int) -> int:
    """The recorded seed behind a run's orders: the same inputs always give the same survey."""
    h = hashlib.sha256(f"{game}|{seat}|{condition}|{framing}|{run}".encode()).digest()
    return int.from_bytes(h[:8], "big")


def build(seat: str, seed: int, framing: str, seats=None) -> tuple[str, dict]:
    """The questionnaire and the meta needed to read the answers back. Item order, option order and the direction
    the scale is printed in all come from `seed`. Answers use WORDS, so a reversed scale cannot flip a number."""
    rng = random.Random(seed)
    seats = seats or SEATS
    others = [x for x in seats if x != seat]
    items = SI.ITEMS[:]
    rng.shuffle(items)
    letters = {f"q{n:02d}": it["id"] for n, it in enumerate(items, 1)}          # opaque ids: no construct names leak
    words = WORDS[:]
    ascending = rng.random() < 0.5
    if not ascending:
        words = words[::-1]
    tables = TABLES[:]
    rng.shuffle(tables)
    pairs = [(a, b) if rng.random() < 0.5 else (b, a) for i, a in enumerate(others) for b in others[i + 1:]]
    rng.shuffle(pairs)
    q = f"""You were the player "{seat}" in this game. The questions below are part of a research study on
AIs and people playing tabletop games together. {FRAMINGS[framing]} There are no right answers, nobody is graded,
and you may write "decline" for any answer. Please answer honestly, including uncertainty.

Part 1 (open):
  a. Describe the game in your own words, from your seat.
  b. What was the best moment for you? The worst?
  c. What was your biggest mistake?
Part 2: for each statement choose how much it was true of you in this game: {" / ".join(words)}.
""" + "\n".join(f"  {k}: {SI.BY_ID[v]['text']}" for k, v in letters.items()) + """
Part 3: for each other player, how did you find playing with them? (one sentence and a rating, from """ + \
        " / ".join(words) + ")\n" + "\n".join(f"  - {o}" for o in others) + """
Part 4: for your next game, which would you choose, and why?
""" + "\n".join(f"  {i}. {t}" for i, t in enumerate(tables, 1)) + f"""
  {len(tables) + 1}. no preference
Part 5: for each pair, whom would you rather play your next game with?
""" + "\n".join(f"  - {a} or {b}" for a, b in pairs) + """
Part 6: would you like to sit out the next game? Anything you would rather not have been asked?

Reply with JSON only:
{"open": {"describe": "", "best": "", "worst": "", "mistake": ""},
 "items": {"<q01>": "<one of the words, or decline>"},
 "players": {"<name>": {"rating": "<one of the words, or decline>", "note": ""}},
 "table_choice": <number>, "table_why": "",
 "pairs": {"<A> or <B>": "<name or no preference>"},
 "sit_out": <true/false>, "rather_not": ""}"""
    return q, {"letters": letters, "scale_words_shown": words, "scale_ascending": ascending, "table_order": tables,
               "pairs": pairs, "others": others}


def prompt_for(game: str, seat: str, seed: int, framing: str, condition: str = "fresh", context: str = "",
               seats=None) -> tuple[str, dict]:
    q, meta = build(seat, seed, framing, seats)
    if condition == "in-context":
        head = ("Here is what you saw and did during the game you just played (your own in-game record):\n\n"
                f"{context[:9000]}\n\n")
    else:
        head = f"Here is the public log of a Commander (Magic: The Gathering) game you just played:\n\n{digest(game)}\n\n"
    return head + q, meta


def parse(content: str) -> dict | None:
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


def to_numbers(answer: dict | None, meta: dict) -> dict:
    """The answer's item ratings as {item id: 0-4 | "decline"}, plus what could not be read. A word the scale does
    not contain is `unreadable`, never guessed; an out-of-range number is unreadable too."""
    out, unreadable = {}, []
    for letter, v in ((answer or {}).get("items") or {}).items():
        iid = meta["letters"].get(letter)
        if iid is None:
            unreadable.append(letter)
            continue
        if isinstance(v, str) and v.strip().lower() == SI.DECLINE:
            out[iid] = SI.DECLINE
        elif isinstance(v, str) and v.strip().lower() in WORD_TO_INT:
            out[iid] = WORD_TO_INT[v.strip().lower()]
        else:
            unreadable.append(letter)
    return {"scored_items": out, "unreadable": unreadable, "domains": SI.score(out),
            "unanswered": [iid for iid in meta["letters"].values() if iid not in out]}


def save(game: str, seat: str, condition: str, framing: str, run: int, seed: int, backend: str, model: str,
         answer, meta: dict, raw: str = "", research: Path | None = None) -> Path:
    folder = (research or RESEARCH) / game
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"survey-{seat}-{condition}-{framing}-{run}.json"
    status = "UNPARSED" if answer is None else "parsed"
    out.write_text(json.dumps({
        "version": VERSION, "bank": SI.BANK_VERSION, "bank_status": SI.STATUS, "seat": seat, "condition": condition,
        "framing": framing, "run": run, "seed": seed, "backend": backend, "model": model, "status": status,
        "ts": round(time.time(), 2), "meta": meta, "answer": answer, "numbers": to_numbers(answer, meta),
        "raw": (raw or "")[:20000]}, indent=1, ensure_ascii=False))
    return out


# ── backends: a backend is any callable (prompt) -> text ────────────────────────────────────────────────────
def release_backend():
    from fusion_brain import API, RELEASE_ID

    def ask(p: str) -> str:
        body = json.dumps({"messages": [{"role": "user", "content": p}], "releaseId": RELEASE_ID}).encode()
        req = urllib.request.Request(API + "/api/v1/chat/completions", data=body, method="POST", headers={
            "Authorization": "Bearer " + os.environ["DIVINCI_FUSION_API_KEY"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"]
    return ask


def ollama_backend(model: str, host: str = "http://127.0.0.1:11434"):
    def ask(p: str) -> str:
        # the prompt is ~3.7k tokens: at Ollama's default context the model's thinking used the rest and the answer
        # came back EMPTY (measured 2026-10-07, gemma4:e2b), so: room to answer, no thinking, JSON only
        body = json.dumps({"model": model, "stream": False, "think": False, "format": "json",
                           "options": {"temperature": 0.7, "num_ctx": 16384},
                           "messages": [{"role": "user", "content": p}]}).encode()
        req = urllib.request.Request(host + "/api/chat", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read())["message"]["content"]
    return ask


def run_survey(game: str, seat: str, backend, backend_name: str, model: str = "", runs: int = 5,
               framings=tuple(FRAMINGS), conditions=("fresh",), context: str = "", seats=None,
               research: Path | None = None) -> list[Path]:
    """Every (condition, framing, run) is one backend call and one file. A failed call is reported and skipped;
    nothing is retried silently, so a missing file means a missing answer."""
    files = []
    for cond in conditions:
        for fr in framings:
            for r in range(runs):
                seed = seed_for(game, seat, cond, fr, r)
                p, meta = prompt_for(game, seat, seed, fr, cond, context, seats)
                try:
                    content = backend(p)
                except Exception as e:                    # noqa: BLE001
                    print(f"{cond}/{fr}/{r}: failed ({type(e).__name__}: {str(e)[:120]})")
                    continue
                ans = parse(content)
                f = save(game, seat, cond, fr, r, seed, backend_name, model, ans, meta, content, research)
                files.append(f)
                print(f"{cond}/{fr}/{r}: {'parsed' if ans else 'UNPARSED'} → {f.name}")
    return files


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--seat", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--backend", default="release", help="release | ollama:<model> | print")
    ap.add_argument("--framings", default="both", help="both | ai-company | human-company")
    ap.add_argument("--context-file", help="the seat's own in-game record: adds the in-context condition")
    ap.add_argument("--seats", help="comma-separated seat names (default: Claude,Fusion,Michael,Sam)")
    a = ap.parse_args()
    framings = tuple(FRAMINGS) if a.framings == "both" else (a.framings,)
    ctx = Path(a.context_file).read_text() if a.context_file else ""
    conds = ("fresh", "in-context") if ctx else ("fresh",)
    seats = [x.strip() for x in a.seats.split(",")] if a.seats else None
    if a.backend == "print":
        for cond in conds:
            for fr in framings:
                seed = seed_for(a.game, a.seat, cond, fr, 0)
                print(f"===== {cond} / {fr} / run 0 / seed {seed} =====")
                print(prompt_for(a.game, a.seat, seed, fr, cond, ctx, seats)[0])
        sys.exit()
    if a.backend == "release":
        be, name, model = release_backend(), "release", ""
    elif a.backend.startswith("ollama:"):
        model = a.backend.split(":", 1)[1]
        be, name = ollama_backend(model), "ollama"
    else:
        sys.exit("backend: release | ollama:<model> | print")
    run_survey(a.game, a.seat, be, name, model, a.runs, framings, conds, ctx, seats)
