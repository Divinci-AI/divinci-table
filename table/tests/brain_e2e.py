"""End-to-end: an EXTERNAL brain drives the virtual player through tablectl's API.

Needs the server started with --brain external, e.g.
  server.py --any-card --brain external --ai "Claude|Ellivere of the Wild Court|Moira" \
            --ai-deck decks/ellivere.json --human "Michael|" --human "Player 2|"
Every check is built from the ACTUAL shuffled hand (read first), never from hard-coded card names.

  ~/.venvs/table/bin/python table/tests/brain_e2e.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from e2e_offline import EgressWatch, call, check, results  # noqa: E402

FIX = HERE / "fixtures"
TOKEN = (HERE.parent / ".brain-token").read_text().strip()
H = {"X-Brain-Token": TOKEN}
LINES = {"b_your_turn": "Claude, your turn.", "b_hand_q": "Claude, what cards are in your hand?",
         "b_michael_turn": "Michael, your turn.", "b_cast": "I cast Llanowar Elves."}


def brain(action, **body):
    return call("POST", f"/api/brain/{action}", body, headers=H)


def state():
    return call("GET", "/api/brain/state", headers=H)[1]


def wav(key):
    w = FIX / f"{key}.wav"
    if not w.exists():
        aiff = FIX / f"{key}.aiff"
        subprocess.run(["say", "-o", str(aiff), LINES[key]], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1",
                        "-sample_fmt", "s16", str(w)], check=True)
        aiff.unlink()
    return w.read_bytes()


def events_since(n):
    return call("GET", f"/api/events?since={n}")[1]["events"]


def main():
    for k in LINES:
        wav(k)
    watch = EgressWatch([8800, 11434])
    watch.start()

    print("1. Access")
    code, _ = call("GET", "/api/brain/state")
    check(code == 403, "brain state refused without the token")
    code, _ = call("POST", "/api/brain/land", {"name": "Forest"})
    check(code == 403, "brain actions refused without the token")

    print("\n2. A new game, played from the real hand")
    brain("new-game", quiet=True)
    s = state()
    check(len(s["hand"]) == 7 and s["library"] == 92, f"7 in hand, 92 in library (got {len(s['hand'])}, {s['library']})")
    mark = call("GET", "/api/events?since=latest")[1]["last"]
    code, r = brain("begin")
    check(code == 200 and r["private"].get("drew") and r["private"]["drew"] not in json.dumps(r["public"]),
          "begin: the drawn card is returned privately and appears nowhere public")
    lands = [h["name"] for h in state()["hand"] if h["land"]]
    code, r = brain("land", name=lands[0])
    check(code == 200, f"land: {lands[0]} played")
    if len(lands) > 1:
        code, r = brain("land", name=lands[1])
        check(code == 400 and "already played a land" in r.get("error", ""), "a second land the same turn is refused")
    s = state()
    expensive = [h for h in s["hand"] if not h["land"] and not h["castable_now"]]
    if expensive:
        code, r = brain("cast", name=expensive[0]["name"])
        check(code == 400 and ("can't pay" in r.get("error", "") or "Aura" in r.get("error", "")),
              f"casting {expensive[0]['name']} ({expensive[0]['cost']}) with {s['untapped_mana']} mana is refused: {r.get('error', '')[:70]}")
    spell = next((h["name"] for h in s["hand"] if not h["land"]), None)
    if spell:
        code, r = brain("say", text=f"I'm holding {spell}.")
        check(code == 400 and "still in your hand" in r.get("error", ""), f"saying a hand card's name ({spell}) is blocked")
    code, r = brain("attack", assign={"Nothing Here": "Michael"})
    check(code == 400, "attacking with a creature you don't have is refused")

    print("\n3. Six more turns, casting whatever is legal")
    casts = 0
    problems = []
    for _ in range(6):
        brain("begin")
        s = state()
        land = next((h["name"] for h in s["hand"] if h["land"]), None)
        if land:
            brain("land", name=land)
        for _ in range(6):
            s = state()
            options = [h for h in s["hand"] if h["castable_now"] and not h["land"]]
            if s["commander_in_zone"] and s["commander_card"]["castable_now"]:
                code, r = brain("cast", name="commander")
                casts += code == 200
                continue
            if not options:
                break
            h = options[0]
            body = {"name": h["name"]}
            if h.get("aura_targets") is not None:
                if not h["aura_targets"] or not h["aura_targets"][0].startswith("#"):
                    options = [o for o in options if o is not h]
                    if not options:
                        break
                    h = options[0]
                    body = {"name": h["name"]}
                else:
                    body["on"] = h["aura_targets"][0].split(" ")[0]
            if "Choose" in (h["text"] or "") and "•" in (h["text"] or ""):
                body["modes"] = [1, 2] if "Choose two" in h["text"] else [1]
            code, r = brain("cast", **body)
            if code != 200:
                problems.append(f"{h['name']}: {r.get('error')}")
                break
            casts += 1
        brain("end")
        pub = call("GET", "/api/ai/state")[1]
        leaked = [h["name"] for h in state()["hand"] if h["name"] in json.dumps(pub)]
        if leaked:
            problems.append(f"hand card in public state: {leaked}")
    check(casts >= 3, f"cast {casts} spells over six turns")
    check(not problems, "every castable_now spell was accepted and nothing leaked" + (f": {problems}" if problems else ""))
    said = [e for e in events_since(mark) if e["type"] == "say"]
    check(len(said) >= 10 and all(e["speaker"] == "Claude" for e in said),
          f"the table heard {len(said)} announcements, all as Claude")
    hand_now = [h["name"] for h in state()["hand"]]
    check(not any(n in e["text"] for e in said for n in hand_now if not any(n in x["text"] for x in said if "I cast" in x["text"] or "I play" in x["text"])),
          "no announcement named a card that is still in the hand")

    print("\n4. Voice hands decisions to the brain instead of answering")
    turn_before = state()["turn"]
    mark = call("GET", "/api/events?since=latest")[1]["last"]
    code, r = call("POST", "/api/utterance", wav("b_your_turn"), "audio/wav")
    assert "heard" in r, f"utterance failed (HTTP {code}): {r}"
    ev = events_since(mark)
    check(r.get("awaiting") == "brain" and any(e["type"] == "attention" and e["kind"] == "turn" for e in ev)
          and state()["turn"] == turn_before,
          f"'{r['heard']}' → an attention event for the brain; the server does NOT play the turn itself")
    mark = ev[-1]["id"] if ev else mark
    r = call("POST", "/api/utterance", wav("b_hand_q"), "audio/wav")[1]
    ev = events_since(mark)
    check(r.get("reply") is None and any(e["type"] == "attention" and e["kind"] == "question" for e in ev),
          f"'{r['heard']}' → attention (question); no automatic reply")
    mark = ev[-1]["id"] if ev else mark
    r = call("POST", "/api/utterance", wav("b_michael_turn"), "audio/wav")[1]
    ev = events_since(mark)
    check(not any(e["type"] == "attention" for e in ev), f"'{r['heard']}' is for Michael → no attention event")
    mark = ev[-1]["id"] if ev else mark
    r = call("POST", "/api/utterance", wav("b_cast"), "audio/wav")[1]
    ev = events_since(mark)
    check(any(e["type"] == "heard" and "Llanowar Elves" in e.get("cards", []) for e in ev),
          f"'{r['heard']}' → a 'heard' event with Llanowar Elves on the board")

    print("\n5. Life")
    code, lt = call("POST", "/api/life", {"player": "Michael", "delta": -5, "by": "test"})
    check(code == 200 and lt.get("Michael") == 35, f"Michael -5 → {lt.get('Michael')}")
    code, r = brain("life", player="me", delta=3)
    check(code == 200 and call("GET", "/api/life")[1]["Claude"] == 43, "the brain gains 3 → 43")

    brain("new-game", quiet=True)
    watch.stop_flag = True
    watch.join()
    print("\n6. Offline")
    check(not watch.remote, "no non-loopback connection" + (f": {sorted(watch.remote)}" if watch.remote else ""))
    bad = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
