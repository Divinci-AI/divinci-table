"""The match rules on their own (table/bo3.py): no table, no network. Best two out of three, drawn games, who chooses to play first,
the countdown with Start now / Wait, and the file kept beside the table's saves.

    PYTHONDONTWRITEBYTECODE=1 python3 table/tests/bo3_rules_test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import bo3  # noqa: E402

FAILS: list[str] = []
M, S = "Michael", "Sam"


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:300]}"), flush=True)
    if not ok:
        FAILS.append(what)


def play(m, winner, now, gid):
    """Start the waiting game (if one is waiting) and finish it."""
    if m.next:
        nx = m.take_next(now)
        m.game_started(gid, nx["first"], chooser=nx["chooser"], choice=nx["choice"], how=nx["how"])
    return m.record(winner, totals={M: 5, S: 0}, turns=9, lines=["a line"], now=now, game_id=gid)


print("Two games to none")
m = bo3.Match([M, S], countdown=25)
check(m.public()["score"] == "No games won yet" and m.public()["game"] == 1 and not m.over, "before game 1: nothing won, game 1", m.public())
m.game_started("g1", S, how="roll")
check(m.current == {"n": 1, "game_id": "g1", "first": S, "chooser": S, "choice": None, "how": "roll"}, "game 1: the roll's winner is first and counts as its chooser", m.current)
m.game_started("g1", M, how="roll")
check(m.current["first"] == S, "told twice about the same game (before and after the mulligans): nothing changes", m.current)
g = m.record(S, totals={M: 0, S: 12}, turns=11, now=1000.0)
check(g["winner"] == S and g["turns"] == 11 and g["totals"] == {M: 0, S: 12} and m.wins == {M: 0, S: 1}, "the game is recorded: winner, turns, final totals", g)
check(m.score() == "Sam leads 1–0" and not m.over, "the score reads 'Sam leads 1–0'", m.score())
check(m.next["chooser"] == M and m.next["why"] == "lost" and m.next["n"] == 2, "the LOSER of game 1 chooses for game 2", m.next)
check(not m.due(1000.0) and not m.due(1024.9) and m.due(1025.0), "the next game is due when the countdown has run, not before", m.next)
ok, why = m.choose(S, "play")
check(not ok and "Michael chooses" in why, "the winner cannot choose", why)
ok, why = m.choose(M, "second")
check(not ok, "only play or draw is a choice", why)
ok, _ = m.choose(M, "draw")
check(ok and m.public(1001)["next"]["plays_first"] == S, "the loser chooses to draw: the other seat will play first", m.public(1001)["next"])
ok, why = m.choose(M, "play")
check(not ok and "already chose" in why, "one choice: it cannot be changed after it is said", why)
nx = m.take_next(1025.0)
check(nx == {"n": 2, "first": S, "chooser": M, "choice": "draw", "how": "chose", "why": "lost"}, "game 2: who is first, who chose, what and how", nx)
m.game_started("g2", nx["first"], chooser=nx["chooser"], choice=nx["choice"], how=nx["how"])
m.record(S, now=2000.0)
check(m.over and m.winner == S and m.score() == "Sam wins the match 2–0" and m.next is None, "two wins end the match: no third game is set up", (m.score(), m.next))
check(not m.due(9e9) and m.take_next(9e9) is None, "nothing is ever due after the match is over", "")
g_ = m.record(M, now=3000.0)
check(m.wins == {M: 0, S: 2} and len(m.games) == 2, "a result reported after the match is over changes nothing", m.wins)

print("Two games to one; a chooser who says nothing plays first")
m = bo3.Match([M, S], countdown=10)
m.game_started("g1", M, how="roll")
play(m, M, 100.0, "g1")
nx = m.take_next(110.0)
check(nx["chooser"] == S and nx["choice"] == "play" and nx["how"] == "default" and nx["first"] == S, "no answer by the start: the loser plays first, recorded as the default", nx)
m.game_started("g2", nx["first"], chooser=nx["chooser"], choice=nx["choice"], how=nx["how"])
m.record(S, now=200.0)
check(m.score() == "Tied 1–1" and m.next["chooser"] == M and m.next["n"] == 3, "1–1: the loser of game 2 chooses for game 3", (m.score(), m.next))
play(m, M, 300.0, "g3")
check(m.over and m.winner == M and m.score() == "Michael wins the match 2–1" and [g["winner"] for g in m.games] == [M, S, M], "2–1 ends it, with every game's winner kept", m.score())

print("A drawn game counts for nobody")
m = bo3.Match([M, S], countdown=10)
m.game_started("g1", S, how="roll")
play(m, M, 100.0, "g1")                                   # Michael 1–0; Sam (the loser) chooses for game 2
m.choose(S, "draw")
nx = m.take_next(110.0)
m.game_started("g2", nx["first"], chooser=nx["chooser"], choice=nx["choice"], how=nx["how"])
g = m.record(None, now=200.0)
check(g["draw"] and g["winner"] is None and m.wins == {M: 1, S: 0} and not m.over, "a drawn game: no win for anyone, the match goes on", (g, m.wins))
check(m.next["chooser"] == S and m.next["why"] == "drawn" and m.next["n"] == 3, "after a drawn game the chooser is whoever chose for the drawn game", m.next)
m = bo3.Match([M, S], countdown=10)
m.game_started("g1", S, how="roll")
m.record(None, now=50.0)
check(m.next["chooser"] == S, "game 1 drawn: the chooser is whoever won the roll for it", m.next)
m = bo3.Match([M, S], countdown=1)
m.game_started("g1", M, how="roll")
for i in range(5):
    play(m, None, 100.0 * (i + 1), f"g{i + 1}")
check(m.over and m.winner is None and len(m.games) == 5 and m.score() == "The match is drawn 0–0", "five drawn games: the cap ends the match as a draw (it cannot go on for ever)", (m.over, len(m.games), m.score()))
m = bo3.Match([M, S], countdown=1)
m.game_started("g1", M, how="roll")
for i, w in enumerate([None, M, None, None, None]):
    play(m, w, 100.0 * (i + 1), f"g{i + 1}")
check(m.over and m.winner == M and m.score() == "Michael wins the match 1–0", "at the cap, whoever has more wins takes the match", m.score())

print("Start now and Wait")
m = bo3.Match([M, S], countdown=25, max_wait=120)
m.game_started("g1", M, how="roll")
m.record(M, now=1000.0)
m.wait(S, 1003.0)
check(not m.due(1025.0) and not m.due(1100.0), "Wait holds the next game past the countdown", m.next)
check(m.public(1010.0)["next"]["holds"] == [S] and m.public(1010.0)["next"]["seconds_left"] == 110.0, "…and everyone can see who is waited for, and for how long at most", m.public(1010.0)["next"])
ok, note = m.start_now(M, 1030.0)
check(ok and "waiting for Sam" in note and not m.due(1030.0), "the other player's Start now does not override a Wait", note)
check(m.due(1120.0), "a Wait cannot hold the game back for ever: max_wait starts it", "")
ok, note = m.start_now(S, 1040.0)
check(ok and note == "" and m.due(1040.0), "the waiting player's own Start now starts it at once", note)
m = bo3.Match([M, S], countdown=25)
m.game_started("g1", M, how="roll")
m.record(M, now=1000.0)
m.start_now(M, 1002.0)
check(m.due(1002.0), "Start now with nobody waiting: at once, before the countdown ends", "")
m = bo3.Match([M, S], countdown=25)
m.game_started("g1", M, how="roll")
m.record(M, now=1000.0)
m.wait(S, 1001.0)
m.start_now(None, 1002.0)
check(m.due(1002.0), "the host's Start now lifts every Wait", "")
check(bo3.Match([M, S]).wait(M, 1.0)[0] is False and bo3.Match([M, S]).start_now(M, 1.0)[0] is False and bo3.Match([M, S]).choose(M, "play")[0] is False,
      "with no game waiting, Wait, Start now and a choice are refused", "")

print("Kept beside the saves")
d = Path(tempfile.mkdtemp(prefix="bo3-"))
f = d / "saves" / "match.json"
m = bo3.Match([M, S], countdown=25, path=f)
m.game_started("g1", S, how="roll")
m.record(S, totals={M: -2, S: 7}, turns=8, lines=["The last blow."], now=1000.0)
m.choose(M, "play")
check(f.exists() and oct(f.stat().st_mode & 0o777) == "0o600" and not list(f.parent.glob("*.tmp")), "the match is written after every change, privately, with no half-written file left", list(f.parent.iterdir()))
r = bo3.load(f)
check(r is not None and r.to_json() == m.to_json() and r.score() == "Sam leads 1–0" and r.next["choice"] == "play", "read back, it is the same match: score, games, the choice already made", r and r.to_json())
r.restart_clock(5000.0)
check(not r.due(5024.0) and r.due(5025.0), "after a restart between two games the countdown starts again", r.next)
check(bo3.load(d / "nothing.json") is None, "no file: no match (never an exception)", "")
(d / "bad.json").write_text("{not json")
check(bo3.load(d / "bad.json") is None, "a damaged file: no match (never an exception)", "")
pub = json.dumps(m.public(1001.0))
check(set(m.public(1001.0)) >= {"on", "seats", "wins", "score", "games", "over", "winner", "next", "game", "games_played"} and "key" not in pub.lower(),
      "the public view has the score, each game and who plays first next, and nothing else", sorted(m.public(1001.0)))
for bad in ([M], [M, M], [M, S, "X"]):
    try:
        bo3.Match(bad)
        check(False, f"a match of {bad} is refused", "")
    except ValueError:
        check(True, f"a match of {len(bad)} seat(s) / the same seat twice is refused")

print("FAILED: " + "; ".join(FAILS) if FAILS else "all good")
sys.exit(1 if FAILS else 0)
