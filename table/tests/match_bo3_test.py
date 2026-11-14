"""A match on one table: best two out of three, the next game starting by itself with the same seat keys (2026-11-14).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/match_bo3_test.py
    MATCH_ONLY="clean|first" …      only the sections whose title names one of these

Throwaway tables on free ports bound to 127.0.0.1 (never a live game's), 40-card decks (17 Mountain, 23 small creatures), every player move
through the seat's own key. A game is ended the way a real one ends: a seat's life goes to 0 or less and the table notices.

What is proved here (each section says it again):
  single       a table started without --match is exactly as before: one game, no /api/match score, nothing restarts
  clean        game 2 starts by itself on the same process with the same keys, and inherits NOTHING from game 1 (the list is in the section)
  first        who plays first: one roll, before game 1 only; then the loser chooses (asked, with a default), an AI seat plays first, the log
               says who chose and what, and after a drawn game the chooser is whoever chose for the drawn game. (The defect seen live on
               2026-11-14: game 2 of Michael vs Sam rolled dice instead of asking the loser.)
  countdown    Start now and Wait
  over         2–0 and 2–1 end the match: no further game starts, and only then do agents read "the game is over"
  restore      the score survives a table restart (--restore), and is in a file beside the table's saves
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
import match_bo3_test_lib as L  # noqa: E402

R, check, C, M, Table = L.R, L.check, L.C, L.M, L.Table
ONLY = os.environ.get("MATCH_ONLY", "")


def section(title, fn):
    if ONLY and not re.search(ONLY, title, re.I):
        return
    print(f"\n{title}", flush=True)
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — one section that throws is one failure
        traceback.print_exc()
        check(False, f"{title}: raised {type(e).__name__}", e)


def single():
    t = Table()                                             # no --match: what the public lobby's agent tables run
    try:
        t.open_game()
        m = t.match()
        check(m == {"on": False}, "/api/match on a single-game table says only that it is one", m)
        check("match" not in t.phase() and "match" not in t.legal(C), "/api/phase and the legal list carry no match there", "")
        t.end_game(M)
        time.sleep(2.5)
        d = t.call("GET", "/api/game/summary", host=False)[1]
        check(d.get("over") and d.get("winner") == C and "match" not in d and "nothing restarts by itself" in d.get("rematch", ""),
              "the game ends as before: the banner's data, 'nothing restarts by itself', no match", {k: d.get(k) for k in ("over", "winner", "rematch")})
        check(t.legal(C).get("note") == "the game is over" and t.legal(M).get("note") == "the game is over", "agents read 'the game is over', as before",
              (t.legal(C).get("note"), t.legal(M).get("note")))
        check(t.phase().get("game_over") is True and not any(e["type"] in ("match", "new-game") for e in t.events()), "and no next game starts", "")
        c, d = t.call("POST", "/api/match/next", {"seat": C, "action": "now"}, key=t.keys[C])
        check(c == 409 and "single games" in d.get("error", ""), "Start now is refused: this table plays single games", (c, d))
    finally:
        t.stop()


def clean():
    t = Table(extra=["--match", "bo3", "--match-countdown", "2"], roll=True)
    try:
        ph = t.open_game()
        first1 = ph["player"]
        other1 = M if first1 == C else C
        m = t.match()
        check(m.get("on") and m["best_of"] == 3 and m["seats"] == [C, M] and m["wins"] == {C: 0, M: 0} and m["game"] == 1 and not m["over"],
              "the match is on: best of three, both seats, nothing won, game 1", m)
        check(m["current"]["first"] == first1 and m["current"]["how"] == "roll" and m["current"]["n"] == 1, "game 1: the roll's winner is first", m.get("current"))
        gid1 = t.phase()["game_id"]
        z0 = t.zones(first1)
        check(z0["library"] + z0["hand"] == 40 and z0["life"] == 20, "(a 40-card deck: 20 life, 7 in hand and 33 in the library before anything is played)", z0)
        # ── leave as much of game 1 lying around as a real game does ──
        k1, k2 = t.keys[first1], t.keys[other1]
        a = t.onto(first1, "Starfleet Security")
        b = t.onto(first1, "Starfleet Security")
        t.lands(first1, 2, "Mountain")
        for body in ({"op": "counter", "ref": f"#{a}", "kind": "+1/+1", "n": 2}, {"op": "token", "name": "Clue", "power": 0, "toughness": 1, "n": 1},
                     {"op": "move", "ref": f"#{b}", "to": "exile"}):
            c, d = t.call("POST", "/api/brain/manual", {"seat": first1, **body}, key=k1)
            assert c == 200, d
        nm = t.zones(first1)["names"][0]
        c, d = t.call("POST", "/api/brain/manual", {"seat": first1, "op": "move", "ref": f"hand:{nm}", "to": "graveyard"}, key=k1)
        assert c == 200, d
        t.call("POST", "/api/brain/mill", {"seat": other1, "n": 3})
        t.call("POST", "/api/brain/life", {"seat": first1, "player": first1, "delta": -3, "text": "a toll"})
        c, d = t.act(first1, "attack", assign={f"#{a}": other1})             # an attack left unanswered: a combat in progress
        check(c == 200, "(set-up: an attack is declared and not yet answered)", (c, d))
        c, d = t.call("POST", "/api/todo", {"text": "Michael: move a card to your graveyard", "for": M})
        check(c == 200 and len(t.call("GET", "/api/todos", host=False)[1]["open"]) == 1, "(set-up: the table has been asked to move something: an open to-do)", (c, d))
        dirty = t.zones(first1), t.zones(other1)
        check(dirty[0]["permanents"] >= 3 and dirty[0]["graveyard"] >= 1 and dirty[0]["exile"] >= 1 and dirty[1]["graveyard"] >= 3 and dirty[0]["life"] == 17
              and (t.phase().get("combat") or {}).get("attacks"),
              "(set-up: game 1 has permanents with counters, a token, cards in graveyards and exile, life changed, a combat open)", t.phase().get("combat"))
        ev0 = t.last_id()
        t.end_game(other1)                                                   # `first1` wins game 1
        m = t.match()
        check(m["wins"] == {first1: 1, other1: 0} and m["score"] == f"{first1} leads 1–0" and m["games"][0]["winner"] == first1 and m["games"][0]["turns"] >= 1
              and m["games"][0]["totals"][other1] <= 0 and m["games"][0]["totals"][first1] == 17 and not m["over"],
              "game 1 is in the match: winner, turns, final totals, the score 'X leads 1–0'", {k: m.get(k) for k in ("wins", "score", "games")})
        check(m["next"]["n"] == 2 and m["next"]["chooser"] == other1 and m["next"]["choice"] is None and 0 < m["next"]["seconds_left"] <= 2,
              "game 2 is counting down, and the loser is the one who chooses", m.get("next"))
        d = t.call("GET", "/api/game/summary", host=False)[1]
        check(d.get("over") and d.get("match", {}).get("continues") and "starts by itself" in d.get("rematch", ""),
              "the end-of-game data says the match continues (the pages show the recap card, not the old banner)", {k: d.get(k) for k in ("match", "rematch")})
        for n in (C, M):
            la = t.legal(n)
            check(la.get("note") == "match continues: the next game is starting" and la.get("actions") == [] and la["match"]["continues"] and "keep polling" in la["match"]["help"],
                  f"{n}'s legal list says 'match continues: the next game is starting' (never 'the game is over')", la)
        check(t.phase().get("match", {}).get("continues") is True and t.phase().get("game_over") is True, "/api/phase says the game is over AND that the match continues", t.phase().get("match"))
        check(t.next_game(2), "game 2 begins by itself, on the same table", t.match())
        # ── game 2: nothing of game 1 is left ──
        ph = t.phase()
        check(ph["game_id"] != gid1 and ph.get("player") is None and (ph.get("mulligan") or {}).get("on") and sorted(ph["mulligan"]["waiting"]) == [C, M]
              and not ph.get("game_over") and not (ph.get("combat") or {}).get("attacks") and not ph.get("hold"),
              "a new game id, the opening-hand step open for both, no turn yet, no combat, no game-over flag", ph)
        for n in (C, M):
            z = t.zones(n)
            check(z["hand"] == 7 and z["library"] == 33 and z["life"] == 20 and z["permanents"] == 0 and z["graveyard"] == 0 and z["exile"] == 0 and z["impulse"] == 0
                  and z["pending"] == 0 and not z["turn"],
                  f"{n}: seven in hand, 33 in the library (the full 40 again), 20 life, no permanents, empty graveyard and exile, nothing pending, turn 0", z)
            st = t.call("GET", f"/api/brain/state?seat={n}", key=t.keys[n])[1]
            check(st.get("seat") == n and len(st.get("hand") or []) == 7 and (st.get("mulligan") or {}).get("open") and (st["mulligan"]["taken"], st["mulligan"]["kept"]) == (0, False),
                  f"{n}'s OWN key from game 1 still opens its hand: no new link, and its mulligan count starts at 0", {k: st.get(k) for k in ("seat", "mulligan", "error")})
        todos = t.call("GET", "/api/todos", host=False)[1]
        check(todos == {"open": [], "done": []}, "no to-do from game 1 is left", todos)
        sp = t.call("GET", "/api/spectate", host=False)[1]
        seats = (sp.get("board") or {}).get("seats") or []
        check(sp.get("match", {}).get("score") == f"{first1} leads 1–0" and all(not s.get("permanents") and not s.get("graveyard") for s in seats) and not sp.get("game_over"),
              "/watch's one-turn-behind buffer was emptied: spectators do not see game 1's board as game 2's, and they see the score", sp.get("match"))
        fair = t.call("GET", "/api/fair", host=False)[1]
        check(fair.get("game") == ph["game_id"] and fair.get("revealed") is False, "the fairness record is the new game's, sealed again", {k: fair.get(k) for k in ("game", "revealed")})
        evs = t.events(ev0)
        kinds = [(e["type"], e.get("kind")) for e in evs]
        check(("new-game", None) in kinds and ("match", "start") in kinds and kinds.index(("match", "game")) < kinds.index(("new-game", None)),
              "the public log has, in order: the game's result, the new game, the match's start line", kinds[-12:])
        s2 = t.call("GET", "/api/game/summary", host=False)[1]
        check(s2 == {"over": False}, "the end-of-game summary is gone (it was game 1's)", s2)
        # ── and game 2 plays: both keep, the first player does not draw, a land can be played, the turn passes ──
        nxt = t.match()["current"]
        ph = t.keep_all()
        f2, o2 = nxt["first"], (M if nxt["first"] == C else C)
        check(ph.get("player") == f2 and ph.get("step") == "main 1" and ph.get("begun"), f"game 2's first turn is {f2}'s, as the match said", ph)
        z = t.zones(f2), t.zones(o2)
        check(z[0]["hand"] == 7 and z[0]["library"] == 33 and z[0]["turn"] == 1, "the player who is first does not draw on turn 1 (7 in hand, 33 in the library)", z[0])
        land = next((h for h in t.call("GET", f"/api/brain/state?seat={f2}", key=t.keys[f2])[1]["hand"] if h["name"] == "Mountain"), None)
        if land:
            c, d = t.call("POST", "/api/brain/land", {"seat": f2, "name": "Mountain"}, key=t.keys[f2])
            check(c == 200, "a land is played in game 2 (the land drop was reset)", (c, d))
        gid2 = t.phase()["game_id"]
        back = [t.call("POST", "/api/phase/back", {"by": f2}, host=False) for _ in range(3)]      # BACK, three times, on the laptop
        zb = t.zones(f2), t.zones(o2)
        check(t.phase()["game_id"] == gid2 and all(z_["life"] == 20 and z_["graveyard"] == 0 and z_["exile"] == 0 and z_["hand"] + z_["library"] + z_["permanents"] == 40 for z_ in zb)
              and t.match()["current"]["n"] == 2 and not t.phase().get("game_over"),
              "BACK, pressed again and again at the start of game 2, never reaches into game 1 (its saved points went with it)", ([(c_, d_.get("error")) for c_, d_ in back], zb))
        if t.phase().get("player") != f2 or not t.phase().get("begun"):
            t.turn(f2, fresh=False)
        t.turn(o2, fresh=False)
        z = t.zones(o2)
        check(z["hand"] == 8 and z["turn"] == 1, "the second player draws for its first turn (8 in hand)", z)
    finally:
        t.stop()


def first():
    # (a) one roll, before game 1 only; (b) the loser is asked; (c) the log says who chose and what
    t = Table(extra=["--match", "bo3", "--match-countdown", "4"], roll=True)
    try:
        ph = t.open_game()
        w1 = ph["player"]
        l1 = M if w1 == C else C
        rolls1 = [e for e in t.events() if e["type"] == "highroll" and e.get("winner")]
        check(len(rolls1) == 1 and rolls1[0]["winner"] == w1 and rolls1[0].get("rolls"), "game 1: ONE roll decides who is first", rolls1)
        ev0 = t.last_id()
        t.end_game(l1)
        m = t.match()
        check(m["next"]["chooser"] == l1 and m["next"]["why"] == "lost" and m["next"]["default"] == "play", "the loser of game 1 is the chooser; the default is to play first", m["next"])
        att = [e for e in t.events(ev0) if e["type"] == "attention" and e.get("kind") == "match-choice"]
        check(len(att) == 1 and att[0].get("addressee") == l1, "the loser's page is told it has a choice to make", att)
        la = t.legal(l1)["match"]["help"]
        check("POST /api/match/next" in la and '"play"' in la and "draw" in la, "…and an agent in that seat is told how to choose (and that silence means play first)", la)
        check("chooses who plays first; there is no roll" in t.legal(w1)["match"]["help"], "the winner's legal list says who chooses, and that there is no roll", t.legal(w1)["match"])
        c, d = t.call("POST", "/api/match/next", {"seat": w1, "action": "play"}, key=t.keys[w1])
        check(c == 409 and "chooses who plays first" in d.get("error", ""), "the winner's own choice is refused", (c, d))
        c, d = t.call("POST", "/api/match/next", {"seat": l1, "action": "draw"}, key=t.keys[w1])
        check(c == 403, "the winner cannot choose FOR the loser with its own key", (c, d))
        c, d = t.call("POST", "/api/match/next", {"seat": l1, "action": "draw"}, host=False)
        check(c == 403, "nobody chooses without a seat key", (c, d))
        c, d = t.call("POST", "/api/match/next", {"seat": l1, "action": "draw"})
        check(c == 403, "the host does not choose for a player either", (c, d))
        c, d = t.call("POST", "/api/highroll/start", {"mode": "table", "sides": 20, "by": w1}, key=t.keys[w1])
        check(c == 409 and "no roll after game 1" in d.get("error", ""), "asking for a roll between games is refused: no roll after game 1", (c, d))
        c, d = t.call("POST", "/api/match/next", {"seat": l1, "action": "draw"}, key=t.keys[l1])
        check(c == 200 and d["next"]["choice"] == "draw" and d["next"]["plays_first"] == w1 and d["next"]["how"] == "chose", "the loser chooses to DRAW first: the winner will play first", d.get("next"))
        c, d = t.call("POST", "/api/match/next", {"seat": l1, "action": "play"}, key=t.keys[l1])
        check(c == 409 and "already chose" in d.get("error", ""), "the choice cannot be changed once said", (c, d))
        check(any(s == f"{l1} chooses to draw first in game 2: {w1} will play first." for s in t.says(ev0)), "the public log says who chose and what", t.says(ev0))
        check(t.next_game(2), "game 2 begins", t.match())
        c, d = t.call("POST", "/api/phase/next", {"by": l1, "key": t.keys[l1]}, key=t.keys[l1])   # a Start pressed during the opening hands
        c2, d2 = t.call("POST", "/api/highroll/start", {"mode": "table", "sides": 20, "by": l1}, key=t.keys[l1])
        check(c2 == 409 and c != 200 or "error" in d, "a Start or a roll pressed during game 2's opening hands rolls nothing", ((c, d.get("error")), (c2, d2.get("error"))))
        ph = t.keep_all()
        check(ph["player"] == w1 and t.match()["current"] == {**t.match()["current"], "n": 2, "first": w1, "chooser": l1, "choice": "draw", "how": "chose"},
              "game 2: the winner of game 1 plays first, because the loser chose to draw", t.match()["current"])
        hr = t.call("GET", "/api/highroll", host=False)[1]
        check(hr.get("mode") == "match" and not hr.get("rolls") and hr.get("winner") == w1 and hr.get("chooser") == l1 and "no roll" in hr.get("note", ""),
              "/api/highroll for game 2 holds no dice: 'no roll after game 1', the chooser and the choice", hr)
        rolls = [e for e in t.events() if e["type"] == "highroll"]
        check(len(rolls) == 1, "the whole log still has exactly ONE roll (2026-11-14: game 2 used to roll)", len(rolls))
        check(any(re.fullmatch(rf"Game 2 of the match \({w1} leads 1–0\)\. {w1} plays first: {l1} chose to draw first\. No roll\.", s) for s in t.says(ev0)),
              "the log's first line of game 2 says who plays first, who chose, and 'No roll'", t.says(ev0)[-3:])
        # game 2 → the loser of game 2 says nothing: plays first by default, and the log says so
        ev1 = t.last_id()
        t.end_game(w1)                                                        # 1–1; w1 lost game 2, so w1 chooses for game 3
        m = t.match()
        check(m["score"] == "Tied 1–1" and m["next"]["chooser"] == w1 and m["next"]["n"] == 3, "1–1: the loser of game 2 chooses for game 3", (m["score"], m["next"]))
        check(t.next_game(3), "game 3 begins after the countdown with no answer", t.match())
        cur = t.match()["current"]
        check(cur["first"] == w1 and cur["chooser"] == w1 and cur["choice"] == "play" and cur["how"] == "default", "no answer in time: the chooser plays first, recorded as the default", cur)
        check(any(s == f"{w1} did not choose in time: {w1} plays first in game 3." for s in t.says(ev1)), "…and the log says that it was the default", t.says(ev1))
        check(len([e for e in t.events() if e["type"] == "highroll"]) == 1, "game 3 did not roll either", "")
    finally:
        t.stop()
    # an AI seat (nobody drives it) that loses plays first, at once; and (d) after a drawn game the chooser stays
    t = Table(extra=["--match", "bo3", "--match-countdown", "3"], roll=False, pilots=(M,))
    try:
        t.open_game(by=M)                                                    # TABLE_HIGHROLL=first: Claude, the first seat, starts game 1
        check(t.match()["current"]["first"] == C and t.match()["current"]["how"] == "first seat", "(no roll on this table: the first seat starts game 1)", t.match()["current"])
        ev0 = t.last_id()
        t.end_game(C)                                                        # the AI seat loses game 1
        m = t.match()
        check(m["next"]["chooser"] == C and m["next"]["choice"] == "play" and m["next"]["how"] == "ai" and m["next"]["plays_first"] == C,
              "an AI seat that lost chooses at once: play first", m["next"])
        check(any(s == f"{C} chooses to play first in game 2: {C} will play first." for s in t.says(ev0)), "the log says the AI seat chose to play first", t.says(ev0))
        check(t.next_game(2) and t.match()["current"]["first"] == C, "game 2: the AI seat plays first", t.match().get("current"))
        t.until(lambda: t.phase().get("player") == C)
        c, d = t.call("POST", "/api/game-over", {"draw": True})              # the host calls game 2 a draw
        check(c == 200 and d.get("draw") is True, "(the host calls game 2 a drawn game)", (c, d))
        m = t.match()
        check(m["wins"] == {C: 0, M: 1} and m["games"][1]["draw"] and m["games"][1]["winner"] is None and not m["over"] and m["score"] == f"{M} leads 1–0",
              "a drawn game counts for nobody: still 1–0, the match goes on", (m["wins"], m["score"]))
        check(m["next"]["n"] == 3 and m["next"]["chooser"] == C and m["next"]["why"] == "drawn",
              "after a drawn game the chooser is whoever chose for the drawn game (not a roll, not the other seat)", m["next"])
        check(any("Game 2 is a draw: it counts for nobody" in s and f"game 2 was drawn, and {C} chose (or won the roll) for it" in s for s in t.says(ev0)),
              "the log says the game was drawn, and why that seat chooses", t.says(ev0)[-4:])
        check(t.next_game(3) and not [e for e in t.events() if e["type"] == "highroll"], "game 3 begins, and nothing was ever rolled for games 2 and 3", "")
    finally:
        t.stop()


def countdown():
    t = Table(extra=["--match", "bo3", "--match-countdown", "3", "--match-max-wait", "8"])
    try:
        t.open_game()
        t.end_game(M)
        t0 = time.time()
        c, d = t.call("POST", "/api/match/next", {"seat": M, "action": "wait"}, key=t.keys[M])
        check(c == 200 and d["next"]["holds"] == [M] and d["next"]["seconds_left"] > 3, "Wait: the next game is held, and the page shows for whom and for how long at most", d.get("next"))
        c, d = t.call("POST", "/api/match/next", {"seat": C, "action": "now"}, key=t.keys[C])
        check(c == 200 and "waiting for Michael" in d.get("note", "") and d["next"]["holds"] == [M], "the other player's Start now does not override it", (c, d.get("note")))
        time.sleep(4.2)
        check(t.match()["next"] is not None and t.phase().get("game_over"), "past the countdown the game is still held", t.match()["next"])
        check(t.next_game(2, secs=8) and 7.0 <= time.time() - t0 <= 12.0, "…but not for ever: --match-max-wait starts it", round(time.time() - t0, 1))
        t.end_game(M)                                                        # (game 2 has no mulligan step on this table: the turn is on)
        check(t.match()["over"] is True, "(2–0)", t.match()["score"])
    finally:
        t.stop()
    t = Table(extra=["--match", "bo3", "--match-countdown", "60"])
    try:
        t.open_game()
        t.end_game(C)
        t0 = time.time()
        c, d = t.call("POST", "/api/match/next", {"seat": C, "action": "now"}, key=t.keys[C])
        check(c == 200 and t.next_game(2, secs=5) and time.time() - t0 < 5, "Start now: game 2 begins at once, 60 s before the countdown would have", (c, round(time.time() - t0, 1)))
        check(t.match()["current"]["how"] == "default" and t.match()["current"]["first"] == C, "(the loser had not chosen: it plays first)", t.match()["current"])
        t.end_game(M)                                                        # 1–1
        c, d = t.call("POST", "/api/match/next", {"seat": M, "action": "wait"}, key=t.keys[M])
        c, d = t.call("POST", "/api/match/next", {"action": "now"})          # the host, with the table's own token, on the laptop
        check(c == 200 and t.next_game(3, secs=5), "the host's Start now lifts a Wait", (c, d.get("error")))
        c, d = t.call("POST", "/api/match/next", {"seat": C, "action": "now"}, key=t.keys[C])
        check(c == 409, "Start now while a game is being played is refused", (c, d))
    finally:
        t.stop()


def over():
    t = Table(extra=["--match", "bo3", "--match-countdown", "1"])
    try:
        t.open_game()
        t.end_game(M)
        check(t.next_game(2), "(game 2 begins)", "")
        ev0 = t.last_id()
        t.end_game(M)                                                        # Claude 2–0
        m = t.match()
        check(m["over"] and m["winner"] == C and m["score"] == f"{C} wins the match 2–0" and m["next"] is None and m["games_played"] == 2,
              "2–0: the match is over, with its winner", {k: m.get(k) for k in ("over", "winner", "score", "next")})
        time.sleep(3.0)
        check(t.phase().get("game_over") is True and t.match()["games_played"] == 2 and not [e for e in t.events(ev0) if e["type"] == "new-game"],
              "no further game starts", "")
        for n in (C, M):
            la = t.legal(n)
            check(la.get("note") == "the game is over" and la["match"]["over"] and la["match"]["winner"] == C, f"now {n}'s legal list says 'the game is over', and that the match is", la)
        ph = t.phase()["match"]
        check(ph["over"] and not ph["continues"] and ph["score"] == f"{C} wins the match 2–0", "/api/phase: the match is over, nothing continues", ph)
        d = t.call("GET", "/api/game/summary", host=False)[1]
        check(d.get("rematch") == "the match is over" and d["match"]["over"], "the end-of-game data says the match is over", d.get("rematch"))
        check(any(s == f"{C} wins game 2. {C} wins the match 2–0." for s in t.says(ev0)), "the log says who won the match", t.says(ev0))
        for a in ("now", "wait", "play"):
            c, d = t.call("POST", "/api/match/next", {"seat": M, "action": a}, key=t.keys[M])
            check(c == 409, f"'{a}' after the match is over is refused", (c, d))
        c, d = t.call("POST", "/api/phase/next", {"by": M, "key": t.keys[M]}, key=t.keys[M])
        check(c == 409, "and nobody can start a game by pressing Start", (c, d.get("error")))
    finally:
        t.stop()
    t = Table(extra=["--match", "bo3", "--match-countdown", "1"])
    try:
        t.open_game()
        for n, loser in ((1, M), (2, C)):
            t.end_game(loser)
            check(t.next_game(n + 1), f"(game {n + 1} begins)", t.match())
        check(t.match()["score"] == "Tied 1–1", "(1–1)", t.match()["score"])
        t.end_game(C)
        m = t.match()
        check(m["over"] and m["winner"] == M and m["score"] == f"{M} wins the match 2–1" and [g["winner"] for g in m["games"]] == [C, M, M],
              "2–1: the match is over after the third game, every game's winner on record", m["score"])
        check(all(g["turns"] >= 1 and set(g["totals"]) == {C, M} and g["first"] for g in m["games"]), "each game has its turns, final totals and who played first", m["games"])
    finally:
        t.stop()


def restore():
    t = Table(extra=["--match", "bo3", "--match-countdown", "1"])
    try:
        t.open_game()
        t.end_game(M)
        check(t.next_game(2), "(game 2 begins)", "")
        t.until(lambda: t.phase().get("player") is not None)
        f = t.dir / "s" / "match.json"
        saved = json.loads(f.read_text())
        check(saved["wins"] == {C: 1, M: 0} and len(saved["games"]) == 1 and saved["current"]["n"] == 2 and oct(f.stat().st_mode & 0o777) == "0o600",
              "the match is in a private file beside the table's saves (TABLE_SAVES_DIR/match.json)", saved.get("wins"))
        gid = t.phase()["game_id"]
        t.call("POST", "/api/brain/life", {"seat": C, "player": M, "delta": -2, "text": "a scratch"})    # (any change: a snapshot is written)
        snap = t.dir / "r" / gid / "snapshot.pkl"
        check(t.until(lambda: snap.exists()), "(the game's snapshot exists)", snap)
        time.sleep(0.5)
    finally:
        t.stop()
    t2 = Table(extra=["--match", "bo3", "--match-countdown", "1", "--restore", str(snap)])
    try:
        t2.claim()
        m = t2.match()
        check(m.get("on") and m["wins"] == {C: 1, M: 0} and m["score"] == f"{C} leads 1–0" and m["games"][0]["winner"] == C and m["current"]["n"] == 2 and not m["over"],
              "a restarted table (--restore) carries on with the match: 1–0, game 2 being played", {k: m.get(k) for k in ("wins", "score", "current")})
        t2.end_game(M)
        check(t2.match()["over"] and t2.match()["score"] == f"{C} wins the match 2–0", "…and finishes it: the second win ends the match", t2.match()["score"])
    finally:
        t2.stop()
    t3 = Table(extra=["--restore", str(snap)])                              # the same save on a single-game table: no match appears
    try:
        check(t3.match() == {"on": False}, "the same save restored WITHOUT --match stays a single game", t3.match())
    finally:
        t3.stop()


for title, fn in (("single: a table without --match is unchanged", single), ("clean: game 2 starts by itself and inherits nothing", clean),
                  ("first: who plays first (one roll, then the loser chooses)", first), ("countdown: Start now and Wait", countdown),
                  ("over: 2–0 and 2–1 end the match", over), ("restore: the match survives a restart", restore)):
    section(title, fn)
print("\nFAILED: " + "; ".join(R.FAILS) if R.FAILS else "\nall good")
sys.exit(1 if R.FAILS else 0)
