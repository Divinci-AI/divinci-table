"""End-to-end: a virtual AI opponent (its own deck) playing turns at the table, fully offline.

Needs the server started with a virtual deck and one human, e.g.
  server.py --ai "Ellivere|Ellivere of the Wild Court|Moira" --ai-deck decks/ellivere.json \
            --human "Michael|Ghalta, Primal Hunger" --any-card
Checks, over a simulated game: rules the code owns (≤1 land a turn, mana, cards accounted for),
privacy (the hand never appears publicly; the hand endpoint needs the token; spoken replies never
name a card in the hand), voice ("Ellivere, your turn." plays a turn; your plays are silent), and
that no process opens a non-loopback connection.

  ~/.venvs/table/bin/python table/tests/ai_turn_e2e.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from e2e_offline import EgressWatch, call, check, results  # noqa: E402

FIX = HERE / "fixtures"
TOKEN = (HERE.parent / ".brain-token").read_text().strip()
LINES = {
    "ai_your_turn": "Ellivere, your turn.",
    "ai_hand": "Ellivere, what cards are in your hand?",
    "ai_attack_q": "Ellivere, who are you attacking this turn?",
    "me_cast": "I cast Llanowar Elves.",
    "me_chatter": "Does anyone want another drink?",
}


def wav(key):
    w = FIX / f"{key}.wav"
    if not w.exists():
        aiff = FIX / f"{key}.aiff"
        subprocess.run(["say", "-o", str(aiff), LINES[key]], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1",
                        "-sample_fmt", "s16", str(w)], check=True)
        aiff.unlink()
    return w.read_bytes()


def say(key):
    return call("POST", "/api/utterance", wav(key), "audio/wav")[1]


def hand():
    code, d = call("GET", "/api/ai/hand", headers={"X-Brain-Token": TOKEN})
    return d["hand"]


def main():
    for k in LINES:
        wav(k)
    watch = EgressWatch([8800, 11434])
    watch.start()
    t0 = time.time()

    print("1. New game")
    call("POST", "/api/reset", {})
    code, st = call("GET", "/api/ai/state")
    check(code == 200 and st["hand"] == 7 and st["library"] == 92 and st["life"] == 40,
          f"Ellivere: 7 in hand, 92 in library, 40 life (got {st['hand']}, {st['library']}, {st['life']})")
    code, _ = call("GET", "/api/ai/hand")
    check(code == 403, "the hand endpoint refuses without the token")
    h = hand()
    check(len(h) == 7 and not any(n in json.dumps(st) for n in h), "the public state names no card in its hand")

    print("\n2. Eight turns (API)")
    problems, all_said, turn_ms = [], [], []
    for i in range(8):
        before = call("GET", "/api/ai/state")[1]
        code, r = call("POST", "/api/ai/turn", {})
        after = r["public"]
        turn_ms.append(r["ms"])
        all_said += r["said"]
        if after["lands"] - before["lands"] > 1:
            problems.append(f"turn {after['turn']}: {after['lands'] - before['lands']} lands")
        if not r["said"][0].startswith("Ellivere's turn") or r["said"][-1] != "That's my turn.":
            problems.append(f"turn {after['turn']}: malformed announcements")
        leak = [n for n in hand() if n in json.dumps(after)]
        if leak:
            problems.append(f"turn {after['turn']}: hand card in public state: {leak}")
    check(not problems, "rules and privacy held every turn" + (f": {problems}" if problems else ""))
    st = call("GET", "/api/ai/state")[1]
    check(st["lands"] >= 5 and len(st["battlefield"]) >= 2,
          f"after 8 turns it has developed: {st['lands']} lands, {len(st['battlefield'])} permanents")
    check(any("I cast" in s for s in all_said), f"it cast spells ({sum('I cast' in s for s in all_said)} casts)")
    print("    board: " + "; ".join(st["battlefield"])[:220])

    print("\n3. Voice")
    r = say("me_cast")
    check(r["route"]["kind"] == "play" and r["reply"] is None and "Llanowar Elves" in r["cards"],
          f"'{r['heard']}' → your play goes on the board, Ellivere stays quiet")
    r = say("me_chatter")
    check(r["reply"] is None, f"'{r['heard']}' → silence")
    turn_before = call("GET", "/api/ai/state")[1]["turn"]
    r = say("ai_your_turn")
    check(r["route"]["kind"] == "turn" and r.get("turn") and r["turn"]["public"]["turn"] == turn_before + 1,
          f"'{r['heard']}' → Ellivere plays turn {turn_before + 1} ({len((r.get('turn') or {}).get('said', []))} announcements)")
    for q in ("ai_hand", "ai_attack_q"):
        turn_before = call("GET", "/api/ai/state")[1]["turn"]
        r = say(q)
        turn_after = call("GET", "/api/ai/state")[1]["turn"]
        check(r.get("reply_source") != "turn" and turn_after == turn_before,
              f"'{r['heard']}' is a QUESTION: it must not play a turn (turn {turn_before} → {turn_after})")
        current = hand()
        named = [n for n in current if re.search(r"\b" + re.escape(n.lower()) + r"s?\b", (r["reply"] or "").lower())]
        check(r["speaker"] == "Ellivere" and r["reply"] and not named,
              f"'{r['heard']}' → Ellivere: {r['reply']!r}" + (f" — NAMED {named}" if named else ""))

    call("POST", "/api/reset", {})
    watch.stop_flag = True
    watch.join()
    print("\n4. Offline")
    check(not watch.remote, "no non-loopback connection" + (f": {sorted(watch.remote)}" if watch.remote else ""))
    print(f"\nturn time p50 {sorted(turn_ms)[len(turn_ms)//2]} ms (max {max(turn_ms)}) · session {time.time() - t0:.0f} s")
    bad = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
