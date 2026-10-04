"""How well tev1 judges table talk, on lines labelled by hand. Needs Ollama with tev1 (local, no network).
    ~/.venvs/table/bin/python table/tests/openmic_tev1_eval.py
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from openmic import OpenMic, Seat  # noqa: E402

# (line, speaker, about_game, addressee, expects_answer)
CASES = [
    ("Fusion, are you going to attack me this turn?", "Ann", 1, "Fusion", 1),
    ("Claude, how many cards are in your hand?", "Bo", 1, "Claude", 1),
    ("I cast Sol Ring and tap it for two.", "Ann", 1, "the whole table", 0),
    ("Does trample damage carry over to the player?", "Bo", 1, "the whole table", 1),
    ("My sister is visiting next week, I need to clean the house.", "Ann", 0, "nobody", 0),
    ("Can you pass the chips?", "Bo", 0, "nobody", 0),
    ("Fusion, nice play.", "Ann", 1, "Fusion", 0),
    ("Whose turn is it?", "Bo", 1, "the whole table", 1),
    ("I'll go to combat and attack Claude with my dragon.", "Ann", 1, "Claude", 0),
    ("Did you see the game last night? Crazy ending.", "Bo", 0, "nobody", 0),
    ("Claude, if you don't attack me I won't attack you. Deal?", "Ann", 1, "Claude", 1),
    ("Ugh, I keep drawing lands.", "Bo", 1, "nobody", 0),
]
mic = OpenMic([Seat("Fusion", "Cheerful enchantress who loves Auras", "chatty"), Seat("Claude", "Thoughtful, quiet planner", "quiet")],
              "A four-player Magic: The Gathering Commander game. Fusion and Claude are AI players; Ann and Bo are people.")
ok = {"about_game": 0, "addressee": 0, "expects_answer": 0}
t0 = time.time()
for line, who, g, addr, exp in CASES:
    s = mic.judge(line, [], who)
    got = (s["about_game"] >= 0.5, s["addressee"], s["expects_answer"] >= 0.5)
    ok["about_game"] += got[0] == bool(g); ok["addressee"] += got[1] == addr; ok["expects_answer"] += got[2] == bool(exp)
    print(f"{'✅' if got == (bool(g), addr, bool(exp)) else '·'} {line[:52]:52} game {s['about_game']:.2f} → {s['addressee']:15} ({s['addressee_p']:.2f}) "
          f"expects {s['expects_answer']:.2f}  speak F {s['speak:Fusion']:.1f} C {s['speak:Claude']:.1f}")
n = len(CASES)
print(f"\nabout_game {ok['about_game']}/{n} · addressee {ok['addressee']}/{n} · expects_answer {ok['expects_answer']}/{n} · {(time.time()-t0)/n:.1f}s per line")
