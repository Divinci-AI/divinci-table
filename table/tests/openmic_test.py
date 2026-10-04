"""Open mic v2 policy (table/openmic.py) with a scripted judge: no model, no network.
    ~/.venvs/table/bin/python table/tests/openmic_test.py
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from openmic import OpenMic, Seat, wake_word, calibrate, rate, move_gate  # noqa: E402

fails = 0
def check(name, ok):
    global fails
    print(("  ✅ " if ok else "  ❌ ") + name); fails += 0 if ok else 1

def judge(scores):
    return lambda text, recent, speaker: dict(scores)

base = {"about_game": 0.9, "addressee": "nobody", "addressee_p": 0.9, "expects_answer": 0.1, "speak:Fusion": 0.5, "speak:Claude": 0.5}
mk = lambda sc, **kw: OpenMic([Seat("Fusion", chattiness=kw.get("f", "normal")), Seat("Claude", chattiness=kw.get("c", "quiet"))],
                             "Magic", log_path=kw.get("log"), judge=judge(sc))

check("wake word at the start", wake_word("Fusion, what's your plan?", ["Fusion", "Claude"]) == "Fusion")
check("wake word after 'hey'", wake_word("Hey Claude can you pass", ["Fusion", "Claude"]) == "Claude")
check("'Hey Divinci' wakes the first AI seat", wake_word("hey divinci, whose turn is it", ["Fusion", "Claude"]) == "Fusion")
check("a name mid-sentence is not a wake word", wake_word("I think Fusion is bluffing", ["Fusion", "Claude"]) is None)
check("wake word answers even with a judge that would stay silent", mk(base).heard("Claude, are you there?", "Ann", [], 100).seat == "Claude")

m = mk({**base, "addressee": "Fusion", "addressee_p": 0.95, "expects_answer": 0.9})
check("clearly addressed + expects an answer → that seat answers", m.heard("are you attacking me", "Ann", [], 100).reason == "addressed")
m = mk({**base, "addressee": "Fusion", "addressee_p": 0.95, "expects_answer": 0.2})
check("addressed but no answer expected → silent", m.heard("nice play", "Ann", [], 100).seat is None)

m = mk({**base, "speak:Fusion": 2.4})
check("normal seat speaks unprompted at 2.4 (min 2.2)", m.heard("that's a big board", "Ann", [], 100).seat == "Fusion")
check("…but not again within its cooldown", m.heard("still big", "Ann", [], 150).seat is None)
check("…and again after the cooldown", m.heard("still big", "Ann", [], 300).seat == "Fusion")
m = mk({**base, "speak:Claude": 2.4})
check("quiet seat stays silent at 2.4 (min 2.6)", m.heard("that's a big board", "Ann", [], 100).seat is None)
m = mk({**base, "speak:Fusion": 1.8}, f="chatty")
check("chatty seat speaks at 1.8 (min 1.6)", m.heard("ha", "Ann", [], 100).seat == "Fusion")

m = mk({**base, "speak:Fusion": 3.0}, f="chatty")
t, said = 1000, 0
for i in range(10):
    said += m.heard("x", "Ann", [], t + i * 50).seat == "Fusion"
check(f"budget: a chatty seat says at most 6 lines in 10 minutes (said {said})", said <= 6)

m = mk({**base, "speak:Fusion": 3.0, "speak:Claude": 3.0}, c="chatty", f="chatty")
d1 = m.heard("x", "Ann", [], 100)
m.ai_spoke(d1.seat)
m.last_human_at = 100                                   # nobody spoke since
check("never the same AI twice in a row without a person in between", m._decide(m.judge_fn("y", [], None), 120).seat != d1.seat)

bad = OpenMic([Seat("Fusion")], "Magic", judge=lambda *a: (_ for _ in ()).throw(TimeoutError("judge down")))
check("judge outage → silence, no crash", bad.heard("anything", "Ann", [], 1).seat is None)

with tempfile.TemporaryDirectory() as tmp:
    log = os.path.join(tmp, "decisions.jsonl")
    mk({**base, "about_game": 0.05}, log=log).heard("my sister's wedding is next week", "Ann", [], 1)
    mk({**base, "about_game": 0.95}, log=log).heard("I cast Sol Ring", "Ann", [], 2)
    recs = [json.loads(l) for l in open(log)]
    check("side conversation: scores logged, words NOT kept", "line" not in recs[0] and "scores" in recs[0])
    check("game talk: the line is kept for research", recs[1].get("line") == "I cast Sol Ring")
    m = mk({**base, "speak:Fusion": 2.4}, log=log)
    d = m.heard("big board", "Ann", [], 10)
    m.link(41, d)
    rate(tmp, 41, "up", "Ann")
    check("calibrate matches a 👍 to the score that produced the line", calibrate(log, os.path.join(tmp, "ratings.jsonl")) == {"2.0": {"up": 1, "roll": 0}})
    check("rating must be up or roll", "error" in rate(tmp, 1, "meh", "Ann"))

# ── Clef-compatible keys, and whether a line alters the game ──────────────────────────────────────────────
import re
m2 = OpenMic([Seat("Fusion 2"), Seat("Claude")], "Magic")
qs, back = m2.questions()
check("every question and choice key fits Clef's pattern (no spaces or colons)",
      all(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", k) for k in list(qs) + list(qs["addressee"]["criteria"])))
ans = {"about_game": {"noul": 0.95}, "changes_game": {"noul": 0.2}, "expects_answer": {"noul": 0.8},
       "addressee": {"choice": "seat_0", "probabilities": {"seat_0": 0.9}}, "speak_0": {"score": 2.1}, "speak_1": {"score": 0.4}}
sc = m2.answers_to_scores(ans, back)
check("answers map back to seat names (\"Fusion 2\" survives the key mapping)",
      sc["addressee"] == "Fusion 2" and sc["addressee_p"] == 0.9 and sc["speak:Fusion 2"] == 2.1 and sc["speak:Claude"] == 0.4)
check("'table' maps back to 'the whole table'", m2.answers_to_scores({**ans, "addressee": {"choice": "table", "probabilities": {}}}, back)["addressee"] == "the whole table")
check("move gate: a router play the judge is sure is NOT a move → chatter",
      move_gate("play", {"changes_game": 0.05, "about_game": 0.1}) == "chatter")
check("move gate: a real cast stays a play", move_gate("play", {"changes_game": 0.97, "about_game": 0.95}) == "keep")
check("move gate: a move the router missed → maybe_move", move_gate("chatter", {"changes_game": 0.9, "about_game": 0.9}) == "maybe_move")
check("move gate: no judge answer (outage, wake word) → the router's call stands",
      move_gate("play", {"error": "x"}) == "keep" and move_gate("play", {"wake": "Fusion"}) == "keep")
check("move gate: a game remark that changes nothing stays as it was", move_gate("chatter", {"changes_game": 0.3, "about_game": 0.9}) == "keep")

print(f"\n{'all passed' if not fails else str(fails) + ' failed'}")
sys.exit(1 if fails else 0)
