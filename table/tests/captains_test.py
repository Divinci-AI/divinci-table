#!/usr/bin/env python3
"""Captain voices, offline: which events wake which captain, how a reply is cleaned up, and the
speaking gate. No network, no server, no keys.

    /usr/bin/python3 table/tests/captains_test.py      (the interpreter captains.py runs under)
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import captains  # noqa: E402

fails = []


def check(ok, what):
    print(("  ✅ " if ok else "  ❌ ") + what, flush=True)
    if not ok:
        fails.append(what)


SEATS = {"Michael": "Kilo, Apogee Mind", "Sam": "Captain N'ghathrod", "Claude": "Aminatou, the Fateshifter",
         "Fusion": "Kaust, Eyes of the Glade"}
T = lambda e: captains.triggers(e, SEATS)

print("what wakes a captain")
check([s for s, _, _ in T({"type": "phase", "step": "untap", "player": "Sam"})] == ["Sam"],
      "a human's turn starting wakes that seat's captain")
check([s for s, _, _ in T({"type": "attention", "kind": "turn", "addressee": "Fusion"})] == ["Fusion"],
      "an AI's turn starting wakes its captain")
check(T({"type": "phase", "step": "main 1", "player": "Sam"}) == [], "other steps don't")
check([s for s, _, _ in T({"type": "say", "speaker": "Claude", "action": "attack", "text": "I attack Sam."})] == ["Claude"],
      "an attack wakes the attacker's captain")
check(T({"type": "say", "speaker": "Claude", "action": "say", "text": "hello"}) == [], "plain talk doesn't")
check(T({"type": "say", "speaker": "Claude", "action": "filler", "text": "One moment."}) == [], "fillers don't")
lost = T({"type": "life", "player": "Michael", "delta": -4, "by": "Sam", "life": 36})
check([s for s, _, _ in lost] == ["Michael", "Sam"], "a life loss wakes the loser's captain, then the dealer's")
check(lost[0][1] > T({"type": "life", "player": "Michael", "delta": -1, "by": "Michael", "life": 39})[0][1],
      "a big hit is likelier to get a line than a scratch")
check([s for s, _, _ in T({"type": "life", "player": "Michael", "delta": -2, "by": "combat", "life": 34})] == ["Michael"],
      "a 'by' that isn't a seat (combat, a phone) wakes nobody else")
check(T({"type": "life", "player": "Nobody", "delta": -2, "by": "Sam", "life": 1})[:1] == [("Sam", 0.3, "Your seat, Sam, just took 2 life off Nobody (now 1).")],
      "only seats with a captain are ever returned")
check(T({"type": "captain", "seat": "Sam", "text": "Arr."}) == [], "a captain's own line never triggers another")
check(T({"type": "attention", "kind": "priority", "addressee": "Claude"}) == [], "priority windows never trigger a line")
everything = [{"type": t} for t in ("hand", "board", "heard", "fair", "shown", "hush", "new-game")]
check(all(T(e) == [] for e in everything), "nothing else does either")

print("what a captain hears (public only)")
words = " ".join(w for e in ({"type": "say", "speaker": "Fusion", "action": "cast", "text": "I cast a creature face down."},
                             {"type": "life", "player": "Sam", "delta": 3, "by": "Sam", "life": 41})
                 for _, _, w in T(e))
check("face down" in words and "41" in words, "it hears what was said aloud and the new life total")
check(all(isinstance(c, float) and 0 < c <= 1 for e in ({"type": "life", "player": "Sam", "delta": -9, "by": "Sam", "life": 1},)
          for _, c, _ in T(e)), "chances are probabilities")

print("cleaning a reply")
c = captains.clean_line
check(c('"The depths are patient."') == "The depths are patient.", "quotes go")
check(c("*adjusts monocle* A clue! (smiles)") == "A clue!", "stage directions go")
check(c("[laughs] One more counter.") == "One more counter.", "bracketed directions go")
check(c("First line.\nSecond line.") == "First line.", "only the first line")
check(c("\n\n  Late start. \n") == "Late start.", "leading blank lines are skipped")
check(c("") == "" and c(None) == "" and c("   \n  ") == "", "an empty reply is empty, not an error")
long = " ".join(f"w{i}" for i in range(40))
check(len(c(long).split()) == captains.MAX_WORDS, "long replies are cut to MAX_WORDS")

print("the speaking gate")
g = captains.Gate(gap=25, cooldown=75)
check(g.ready("Kilo", 1000), "a fresh captain may speak")
g.spoke("Kilo", 1000)
check(not g.ready("Kaust", 1010), "no captain speaks within the gap after any line")
check(g.ready("Kaust", 1026), "another captain may speak after the gap")
check(not g.ready("Kilo", 1050), "the same captain waits out its cooldown")
check(g.ready("Kilo", 1076), "…and then may speak again")

print("personas")
P = json.loads((HERE / "captains" / "personas.json").read_text())
check(set(P["captains"]) == set(SEATS.values()), "one persona per commander at the table")
check(all({"voice", "card", "character"} <= set(v) for v in P["captains"].values()), "each has a voice, card and character")
check(len({v["voice"] for v in P["captains"].values()}) == len(P["captains"]), "no two captains share a voice")
check("hidden cards" in P["rules"] and "15 words" in P["rules"], "the rules forbid hidden cards and keep lines short")
check(not any(k in json.dumps(P).lower() for k in ("release_id", "api_key", "bearer")), "no IDs or secrets in the public file")

print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
