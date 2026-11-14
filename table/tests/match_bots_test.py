"""Two rule-based autopilots play a whole best-two-out-of-three match against each other on one table, with nobody touching it (2026-11-14).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/match_bots_test.py      (BOT_DIR: where claude_autopilot.py is)

The table is started the way the draft flow starts one (the real roll, the mulligan step, --match bo3), on a free port, with 40-card decks
whose starting life is 6 so that a game takes a minute, not ten. Each bot is its own process with only its own seat key, exactly as
play_round.sh runs them. Proved: the bots do NOT stop at "game over" while the match continues; the loser's bot chooses to play first; games
2 and 3 start by themselves with the same keys and no roll; the match ends at two wins; and only then do both bots stop, by themselves."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
import cards_path  # noqa: E402

BOT = Path(os.path.expanduser(os.environ.get("BOT_DIR", str(HERE.parent / "bots"))))
cards_path.need(BOT / "claude_autopilot.py", "the Claude bot", "BOT_DIR=<dir with claude_autopilot.py>")
import match_bo3_test_lib as L  # noqa: E402

R, check, C, M = L.R, L.check, L.C, L.M
def match(title, life):
    print(f"\n{title}", flush=True)
    t = L.Table(extra=["--match", "bo3", "--match-countdown", "8"], roll=True, life=life)   # (8 s: a bot looks at the table every 1-3 s)
    bots, logs = {}, {}
    try:
        t.claim()
        tmp = Path(tempfile.mkdtemp(prefix="bots-"))
        for n in (C, M):
            kf = tmp / f"{n}.key"
            kf.write_text(t.keys[n]); kf.chmod(0o600)
            logs[n] = tmp / f"{n}.log"
            env = {**os.environ, "TABLE_URL": t.b, "CLAUDE_SEAT": n, "CLAUDE_KEY_FILE": str(kf), "AUTOPILOT_GRACE": "0", "PYTHONDONTWRITEBYTECODE": "1"}
            bots[n] = subprocess.Popen([sys.executable, "-u", str(BOT / "claude_autopilot.py")], env=env, stdout=open(logs[n], "w"), stderr=subprocess.STDOUT)
        keys0 = dict(t.keys)
        t.call("POST", "/api/phase/next", {"by": C, "key": t.keys[C]}, key=t.keys[C])
        t0 = time.time()
        done = t.until(lambda: t.match().get("over"), secs=float(os.environ.get("MATCH_BOTS_SECS", "480")))
        m = t.match()
        print(f"  (the match took {time.time() - t0:.0f} s: {m.get('score')}; games: " + ", ".join(f"{g['n']}: {g['winner'] or 'draw'} in {g['turns']} turns" for g in m.get("games") or []) + ")", flush=True)
        check(done and m["over"] and m["winner"] in (C, M) and max(m["wins"].values()) == 2, "two autopilots play the whole match, by themselves, to two wins", {k: m.get(k) for k in ("over", "wins", "score", "next", "current")})
        check(len(m["games"]) in (2, 3) and sum(1 for g in m["games"] if g["winner"]) == len(m["games"]) and all(g["turns"] >= 2 for g in m["games"]),
              "it took two or three games, each played out over real turns", [(g["winner"], g["turns"]) for g in m["games"]])
        evs = t.events()
        check(len([e for e in evs if e["type"] == "highroll"]) == 1, "one roll in the whole match: before game 1", "")
        starts = [e for e in evs if e["type"] == "match" and e.get("kind") == "start"]
        check(len(starts) == len(m["games"]) - 1 and all(e["chooser"] != m["games"][e["game"] - 2]["winner"] for e in starts),
              "every later game was started by the match itself, the chooser being the loser of the game before", starts)
        check(all(e["how"] == "chose" and e["choice"] == "play" and e["first"] == e["chooser"] for e in starts),
              "the losing bot chose for itself (it was not the timeout's default): play first", [(e["chooser"], e["how"], e["choice"]) for e in starts])
        check(len([e for e in evs if e["type"] == "new-game"]) == len(m["games"]) - 1 and len([e for e in evs if e["type"] == "game-over"]) == len(m["games"]),
              "the log has one game-over per game and one new-game per later game", "")
        for g in m["games"][1:]:
            check(g["first"] == g["chooser"] and g["chooser"] == [x for x in (C, M) if x != m["games"][g["n"] - 2]["winner"]][0], f"game {g['n']}: the loser of game {g['n'] - 1} played first", g)
        check(all(t.call("GET", f"/api/brain/state?seat={n}", key=keys0[n])[0] == 200 for n in (C, M)), "the seat keys handed out before game 1 still work after the last game", "")
        end = time.time() + 25
        while time.time() < end and any(p.poll() is None for p in bots.values()):
            time.sleep(0.5)
        check(all(p.poll() == 0 for p in bots.values()), "both bots stop by themselves once the MATCH is over", {n: p.poll() for n, p in bots.items()})
        for n in (C, M):
            out = logs[n].read_text()
            stays = out.count("the match continues")
            check(stays == len(m["games"]) - 1 and "the match is over" in out and out.rstrip().splitlines()[-1].startswith("autopilot: game over") and "Traceback" not in out,
                  f"{n}'s bot said it stays after each earlier game and stops only at the match's end", out[-600:])
        losers = [x for g in m["games"][:-1] for x in (C, M) if x != g["winner"]]
        check(all("the choice is mine (I lost the last game): play first -> 200" in logs[n].read_text() for n in set(losers)), "the bot that lost a game says it chose to play first", losers)
        for n in (C, M):                                       # every game was really played by both: a land and a creature each game is the least
            told = logs[n].read_text()
            check(told.count("opening hand: keep") + told.count("opening hand: mulligan") >= len(m["games"]), f"{n}'s bot answered the opening-hand step in every game", told.count("opening hand"))
    finally:
        for p in bots.values():
            if p.poll() is None:
                p.terminate()
        t.stop()


match("An even match (both seats start at 6 life)", 6)
match("A lopsided match (Michael starts at 14 life, Claude at 4)", {C: 4, M: 14})
print("\nFAILED: " + "; ".join(R.FAILS) if R.FAILS else "\nall good")
sys.exit(1 if R.FAILS else 0)
