"""Game retro 2026-11-15/16, wave 2 (Star Trek set, 1v1, two pilot seats). Each section on a table of its own on a free port (never a live game's:
8800, 8801, 8820, 8830, 8883 are refused), through the route a player uses (the seat's own key); set-up and host corrections use the host token.

  1  who goes first, set by the host (game 2 of a best-of-three: the loser of game 1 chooses play or draw). TABLE_FIRST=<seat>:choose, or the
     host's POST /api/first-player, skips the high roll and says why in the log; the chooser answers play/draw with their own key (nobody else's);
     the mulligan step still runs before the first turn, and the seat that plays first skips its first draw
  2  BACK and host corrections: a BACK that would undo a host correction made since its saved point (a +1/+1 counter from `counter`) is refused
     and names it; the host can force it, and the log then lists the correction to make again
  3  counters by kind on the public board: /api/board3d carries quest, charge and promotion apart from +1/+1 (the pages name each kind)
  4  (review of the play/draw fix) a chooser the autopilot plays is played for at once; a person who doesn't answer in FIRSTPICK_SECS is played
     for by the table (and the log says so); a chooser who is out by the time they answer gets a 409, not a 500; the host can't set the first
     player during a real-dice roll, nor for game 2 of a match (game 1 of a match it can); the pick and its question survive a restore
     (TABLE_FIRST is not read again) and the next game clears it
  5  (review of the BACK guard) the refusal never names a hidden card (a quiet `take`); a seat key's force is not the host's; after a forced
     BACK the corrections it undid no longer block; a host /api/life change, `cant-block` and `draw` are corrections too

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 HF_HUB_OFFLINE=1 python3 table/tests/retro_1115b_test.py
    R1115B_ONLY="first|back"   only the sections whose title names one of these
"""
from __future__ import annotations

import os
import sys
import json
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
from v6_harness import R, Table as _Table, check  # noqa: E402
import match_bo3_test_lib as L  # noqa: E402

LIVE_PORTS = (8800, 8801, 8820, 8830, 8883)
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in LIVE_PORTS)   # never a live game's port
R.POOL.update({n: 6 for n in ("Support Mission", "Xenobotanist", "Tal Shiar Operative", "Mugato", "Horta")})
R.DECK_MICHAEL, R.DECK_CLAUDE = R.deck_file("Michael-1115b"), R.deck_file("Claude-1115b")
M, C = "Michael", "Claude"


class Table(_Table):
    """The harness table with extra environment for this one server (restored after it starts)."""
    def __init__(self, env=None, extra=(), human=False):
        old = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try:
            super().__init__(extra=extra, human=human)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        assert self.port not in LIVE_PORTS

    def claim(self):
        self.keys = {n: self.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in (C, M)}
        self.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)

    def phase(self):
        return self.call("GET", "/api/phase")[1]

    def press_start(self, seat=C):
        return self.call("POST", "/api/phase/next", {"by": seat, "key": self.keys[seat]}, key=self.keys[seat])

    def seat(self, seat, action, **body):
        return self.call("POST", f"/api/brain/{action}", {"seat": seat, **body}, key=self.keys[seat])

    def until(self, pred, secs=20):
        end = time.time() + secs
        while time.time() < end:
            ph = self.phase()
            if pred(ph):
                return ph
            time.sleep(0.15)
        return self.phase()


def says(t, ev0=0):
    return [e.get("text") or "" for e in t.events(ev0) if e.get("type") == "say"]


def first_player_chooses():
    # the launcher passes the loser of game 1 (Claude, first in the seating, who a plain start would have put first): Claude chooses to DRAW
    t = Table(env={"TABLE_FIRST": "Claude:choose", "TABLE_FIRST_REASON": "Claude lost game 1", "TABLE_MULLIGAN": "on", "TABLE_MULLIGAN_SECS": "0"})
    try:
        t.claim()
        ph = t.phase()
        check(ph.get("player") is None and (ph.get("first_player") or {}).get("seat") is None,
              "before START nothing is decided (the launcher's pick is read when the game starts)", ph.get("first_player"))
        c, d = t.press_start(M)
        ph = t.phase()
        fp = ph.get("first_player") or {}
        hr = t.call("GET", "/api/highroll")[1]
        check(c == 202 and fp.get("pending") and fp.get("seat") == C and ph.get("player") is None and not hr.get("rolls") and "mulligan" not in ph,
              "START: no high roll; Claude (the loser) is asked to play or draw; no turn and no opening hands yet", (c, d.get("error"), fp, hr.get("mode")))
        ev = says(t)
        check(any("No high roll" in x and "Claude lost game 1" in x and "chooses to play first or to draw" in x for x in ev),
              "…and the log says why there is no high roll", ev[-4:])
        att = [e for e in t.events() if e.get("type") == "attention" and e.get("kind") == "playdraw"]
        check(len(att) == 1 and att[0].get("addressee") == C, "…Claude's page is told it is theirs to choose", att)
        c, d = t.press_start(M)
        check(c == 202 and len([e for e in t.events() if e.get("type") == "attention" and e.get("kind") == "playdraw"]) == 1,
              "START again while Claude chooses: still waiting, asked once", (c, d.get("error")))
        c, d = t.call("POST", "/api/brain/playdraw", {"seat": C, "choice": "play"}, key=t.keys[M])
        check(c == 403, "Michael's key can't answer for Claude", (c, d.get("error")))
        c, d = t.seat(M, "playdraw", choice="play")
        check(c == 403 and t.phase().get("player") is None, "…nor answer it for himself: Claude is the one choosing", (c, d.get("error")))
        c, d = t.seat(C, "playdraw", choice="sideways")
        check(c == 400, "an answer that is neither play nor draw is refused", (c, d.get("error")))
        c, d = t.seat(C, "playdraw", choice="draw")
        ph = t.phase()
        hr = t.call("GET", "/api/highroll")[1]
        check(c == 200 and hr.get("winner") == M and hr.get("mode") == "host" and ph.get("order", [None])[0] == M,
              "Claude chooses to draw: Michael goes first (the order starts with him)", (c, d.get("error"), hr.get("winner"), ph.get("order")))
        check((ph.get("mulligan") or {}).get("on") and (ph.get("mulligan") or {}).get("first") == M and ph.get("player") is None,
              "…the mulligan step opens next (it is not skipped), first turn waiting on it", ph.get("mulligan"))
        ev = says(t)
        check(any("Claude chooses to draw" in x and "Michael goes first" in x for x in ev), "…and the log says who chose what", ev[-4:])
        c, d = t.seat(C, "playdraw", choice="play")
        check(c == 409, "the choice is made once", (c, d.get("error")))
        for n in (M, C):
            c, d = t.seat(n, "keep")
            check(c == 200, f"{n} keeps seven", (c, d.get("error")))
        ph = t.until(lambda p: p.get("player") == M)
        check(ph.get("player") == M, "every seat has kept: Michael's first turn", (ph.get("player"), ph.get("step")))
        if not ph.get("begun"):
            t.seat(M, "begin")
        ph = t.until(lambda p: p.get("player") == M and p.get("begun"))
        check(len(t.state(M)["hand"]) == 7, "Michael plays first: he skips his first draw (seven in hand)", len(t.state(M)["hand"]))
    finally:
        t.stop()


def first_player_host():
    t = Table()                                            # TABLE_HIGHROLL=first (the harness): a plain START would put Claude, first in the seating, first
    try:
        t.claim()
        c, d = t.call("POST", "/api/first-player", {"seat": M, "reason": "Michael lost game 1"}, key=t.keys[C])
        check(c == 403 and not t.phase().get("first_player"), "a seat key is not the host: refused", (c, d.get("error")))
        c, d = t.call("POST", "/api/first-player", {"seat": "Nobody"})
        check(c == 400, "a seat that isn't at the table is refused", (c, d.get("error")))
        c, d = t.call("POST", "/api/first-player", {"seat": M, "reason": "Michael lost game 1"})
        fp = (d.get("first_player") or {})
        check(c == 200 and fp.get("seat") == M and not fp.get("choose"), "the host sets Michael to go first", (c, d))
        ev0 = t.events(0)[-1]["id"]
        c, d = t.press_start(C)
        ph = t.until(lambda p: p.get("player") is not None)
        check(ph.get("player") == M, "START: Michael goes first, not the first seat in the order", (c, d.get("error"), ph.get("player")))
        ev = says(t, ev0)
        check(any("No high roll (Michael lost game 1)" in x and "Michael to go first" in x for x in ev), "…and the log says why no high roll was made", ev[-4:])
        c, d = t.call("POST", "/api/first-player", {"seat": C})
        check(c == 409 and t.phase().get("player") == M, "once the game has started, who went first can't be changed", (c, d.get("error")))
    finally:
        t.stop()


def first_player_host_answers():
    t = Table(env={"TABLE_FIRST": "Michael:choose"})
    try:
        t.claim()
        c, d = t.press_start(C)
        check(c == 202 and (t.phase().get("first_player") or {}).get("pending"), "START: Michael is asked to play or draw", (c, d.get("error")))
        c, d = t.call("POST", "/api/first-player", {"choice": "play"}, key=t.keys[C])
        check(c == 403 and t.phase().get("player") is None, "a seat key can't answer through the host's route", (c, d.get("error")))
        c, d = t.call("POST", "/api/first-player", {"choice": "play"})
        ph = t.until(lambda p: p.get("player") is not None)
        check(c == 200 and ph.get("player") == M, "the host answers for him (play): Michael goes first", (c, d.get("error"), ph.get("player")))
    finally:
        t.stop()


def back_keeps_host_corrections():
    t = Table()
    try:
        t.start()                                          # Michael's main 1
        op = t.onto(M, "Tal Shiar Operative")
        t.turn(C)
        t.turn(M)                                          # a fresh turn of Michael's: the previous turn's points are in the ring too
        c, d = t.act(M, "next")
        ph = t.phase()
        check(c == 200 and ph.get("player") == M and ph.get("step") != "main 1", "Michael moves on from main 1", (c, d.get("error"), ph.get("step")))
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{op}", "n": 1})
        check(c == 200 and t.perm(M, "Tal Shiar Operative")["counters"] == 1, "host correction: a +1/+1 counter on Tal Shiar Operative (the night's fix)", (c, d))
        c, d = t.call("POST", "/api/phase/back", {"by": M}, key=t.keys[M])
        check(c == 409 and "correction" in (d.get("error") or "") and "Tal Shiar Operative" in (d.get("error") or ""),
              "Michael's BACK would undo the host's correction: refused, and it names the correction", (c, d.get("error")))
        check(t.perm(M, "Tal Shiar Operative")["counters"] == 1 and not t.phase().get("back_request"),
              "…the counter is still there, and no BACK request is left waiting", t.perm(M, "Tal Shiar Operative"))
        c, d = t.call("POST", "/api/phase/back", {"by": "host"})
        check(c == 409 and "force" in (d.get("error") or ""), "the host's own BACK without force: refused the same way (it says force exists)", (c, d.get("error")))
        ev0 = t.events(0)[-1]["id"]
        c, d = t.call("POST", "/api/phase/back", {"by": "host", "force": True})
        p = t.perm(M, "Tal Shiar Operative")
        ev = says(t, ev0)
        check(c == 200 and p and p["counters"] == 0, "the host forces it: the table goes back, and the counter with it", (c, d.get("error"), p))
        check(any("to be made again" in x and "Tal Shiar Operative" in x for x in ev), "…and the log lists the correction to make again", ev[-3:])
        c, d = t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{op}", "n": 1})
        check(c == 200 and t.perm(M, "Tal Shiar Operative")["counters"] == 1, "the host makes it again", (c, d))
    finally:
        t.stop()


def board_counter_kinds():
    t = Table()
    try:
        t.start()
        sm = t.onto(M, "Support Mission")
        op = t.onto(M, "Tal Shiar Operative")
        t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{sm}", "n": 5})
        t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{op}", "n": 2})
        seat = next(s for s in t.call("GET", "/api/board3d", host=False)[1]["seats"] if s["name"] == M)
        smv = next(p for p in seat["permanents"] if p["id"] == sm)
        opv = next(p for p in seat["permanents"] if p["id"] == op)
        check(smv.get("quest") == 5 and not smv.get("counters") and "promo" in smv, "the public board: Support Mission has 5 quest counters, no +1/+1 (and carries promotion)", smv)
        check(opv.get("counters") == 2 and not opv.get("quest"), "…and the creature its two +1/+1 counters, apart", opv)
    finally:
        t.stop()


def playdraw_autopilot():
    t = Table(env={"TABLE_FIRST": "Michael:choose", "TABLE_FIRST_REASON": "Michael lost game 1"})
    try:
        t.claim()
        c, d = t.call("POST", "/api/seat/played-by", {"seat": M, "kind": "autopilot"})
        check(c == 200, "(set-up: the host says the rules autopilot plays Michael's seat)", (c, d))
        ev0 = t.events(0)[-1]["id"]
        c, d = t.press_start(C)
        ph = t.until(lambda p: p.get("player") is not None)
        ev = says(t, ev0)
        check(c == 200 and ph.get("player") == M and not (ph.get("first_player") or {}).get("pending"),
              "START with the autopilot as the chooser: nobody is left waiting on a page; it plays, so Michael goes first", (c, d.get("error"), ph.get("player"), ph.get("first_player")))
        check(any("Michael chooses to play" in x and "autopilot" in x for x in ev), "…and the log says the table played for the autopilot", ev[-3:])
    finally:
        t.stop()


def playdraw_timeout():
    t = Table(env={"TABLE_FIRST": "Michael:choose", "FIRSTPICK_SECS": "2"})
    try:
        t.claim()
        c, d = t.press_start(C)
        check(c == 202 and (t.phase().get("first_player") or {}).get("pending"), "a person chooses: asked on their page", (c, d.get("error")))
        ph = t.until(lambda p: p.get("player") is not None, secs=15)
        ev = says(t)
        check(ph.get("player") == M, "no answer in FIRSTPICK_SECS (2 s here): the table plays for Michael, who goes first", (ph.get("player"), ph.get("first_player")))
        check(any("no answer in 2 s" in x and "Michael chooses to play" in x for x in ev), "…and the log says the table did it", ev[-3:])
    finally:
        t.stop()


def playdraw_seat_out():
    t = Table(env={"TABLE_FIRST": "Michael:choose"}, human=True)               # three seats (Sam at the table too): one out is not the game over
    try:
        t.claim()
        c, d = t.press_start(C)
        check(c == 202, "Michael is asked to play or draw", (c, d.get("error")))
        t.call("POST", "/api/life", {"player": M, "delta": -20, "by": "host"})      # Michael is out (0 life) before the answer comes
        c, d = t.call("POST", "/api/first-player", {"choice": "play"})               # (the host answers for him: the same settle as his own answer)
        check(c == 409 and "no longer in the game" in str(d.get("error")) and not t.phase().get("first_player"),
              "…and the answer comes after he is out: a 409 that says so (not a 500), and the host's pick is cleared", (c, d.get("error"), t.phase().get("first_player")))
    finally:
        t.stop()


def first_player_refusals():
    t = Table()
    try:
        t.claim()
        c, d = t.call("POST", "/api/highroll/start", {"mode": "physical", "sides": 20, "by": "host"})
        check(c == 200, "(set-up: a real-dice high roll is under way)", (c, d.get("error")))
        c, d = t.call("POST", "/api/first-player", {"seat": M, "reason": "lost game 1"})
        check(c == 409 and not t.phase().get("first_player"), "the host can't set the first player while the dice are being rolled", (c, d.get("error")))
    finally:
        t.stop()
    t = L.Table(extra=["--match", "bo3", "--match-countdown", "40"], env={"TABLE_FIRST": "Claude"})
    try:
        t.claim()
        c, d = t.call("POST", "/api/first-player", {"seat": M, "reason": "lost the previous round"})
        check(c == 200, "game 1 of a match: the host may set its first player (it has no loser yet)", (c, d.get("error")))
        t.call("POST", "/api/phase/next", {"by": C, "key": t.keys[C]}, key=t.keys[C])
        ph = t.keep_all()
        check(ph.get("player") == M, "…and game 1 opens with Michael (the host's pick beats the launcher's TABLE_FIRST)", (ph.get("player"), ph.get("first_player")))
        t.end_game(M)
        c, d = t.call("POST", "/api/first-player", {"seat": C, "reason": "nope"})
        check(c == 409 and "match" in str(d.get("error")), "between games 1 and 2: refused, game 2 belongs to the match (the loser chooses)", (c, d.get("error")))
    finally:
        t.stop()


def firstpick_restore():
    t = Table(env={"TABLE_FIRST": "Michael:choose", "TABLE_FIRST_REASON": "Michael lost game 1"})
    try:
        t.claim()
        keys = dict(t.keys)
        c, d = t.press_start(C)
        check(c == 202, "Michael is asked to play or draw", (c, d.get("error")))
        t.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)   # (a change: the table saves)
        time.sleep(0.5)
        snap = next(iter(t.dir.rglob("snapshot.pkl")), None)
    finally:
        t.stop()
    check(snap is not None, "(the table saved)", snap)
    if snap is None:
        return
    t = Table(env={"TABLE_FIRST": "Claude"}, extra=["--restore", str(snap)])     # a launcher would pass TABLE_FIRST again: it is not re-read
    try:
        t.keys = keys
        fp = t.phase().get("first_player") or {}
        check(fp.get("seat") == M and fp.get("pending"), "restored: Michael is still the one asked (TABLE_FIRST=Claude is not applied again)", fp)
        c, d = t.press_start(C)
        c, d = t.seat(M, "playdraw", choice="draw")
        ph = t.until(lambda p: (p.get("mulligan") or {}).get("on") or p.get("player") is not None)
        hr = t.call("GET", "/api/highroll")[1]
        check(c == 200 and hr.get("winner") == C, "…Michael answers draw after the restore: Claude goes first", (c, d.get("error"), hr.get("winner")))
        t.until(lambda p: p.get("player") is not None)
        c, d = t.call("POST", "/api/brain/new-game", {"seat": C})
        check(c == 200 and not t.phase().get("first_player"), "the next game clears the pick (it is set again for each game)", (c, d.get("error"), t.phase().get("first_player")))
    finally:
        t.stop()


def to_a_fresh_step(t):
    """Michael's turn, moved on from main 1: BACK has a saved point before whatever comes next."""
    t.turn(C)
    t.turn(M)
    c, d = t.act(M, "next")
    assert c == 200, d


def back_guard_review():
    t = Table()
    try:
        t.start()
        op = t.onto(M, "Tal Shiar Operative")
        to_a_fresh_step(t)
        # a quiet take from the other hand: the refusal names the correction, never the card
        hand_c = [h["name"] for h in t.call("GET", f"/api/brain/state?seat={C}")[1].get("hand") or []]
        card = next(n for n in hand_c if n not in ("Plains", "Island", "Swamp", "Mountain", "Forest") and "Michael" not in n and "Claude" not in n)
        c, d = t.call("POST", "/api/brain/take", {"seat": M, "from": C, "name": card, "quiet": True})
        check(c == 200, f"(set-up: the host quietly takes a card from Claude's hand for Michael)", (c, d.get("error")))
        c, d = t.call("POST", "/api/phase/back", {"by": M}, key=t.keys[M])
        err = str(d.get("error") or "")
        check(c == 409 and "take from Claude's hand" in err and card not in json.dumps(d),
              "Michael's BACK across it: refused, naming 'take from Claude's hand', and the card's name is nowhere in the answer", (c, err[:200], card in json.dumps(d)))
        c, d = t.call("POST", "/api/phase/back", {"by": M, "force": True}, key=t.keys[M])
        check(c == 409 and "correction" in str(d.get("error")), "a seat key with force: true is still refused (force is the host's)", (c, d.get("error")))
        ev0 = t.events(0)[-1]["id"]
        c, d = t.call("POST", "/api/phase/back", {"by": "host", "force": True})
        logged = " ".join(says(t, ev0))
        check(c == 200 and "take from Claude's hand" in logged and card not in logged, "the host forces it: the log lists the correction by its label, not the card", (c, d.get("error"), logged[-200:]))
        # after the forced BACK, the correction it undid no longer blocks a later BACK
        c, d = t.act(M, "next")
        c, d = t.call("POST", "/api/phase/back", {"by": M}, key=t.keys[M])
        check(c in (200, 202) and "correction" not in str(d.get("error") or ""), "after the forced BACK, the old correction no longer blocks a BACK", (c, d.get("error")))
        if c == 202:
            t.call("POST", "/api/phase/back/confirm", {"by": C}, key=t.keys[C])
        # the corrections the first review found missing are guarded too
        for what, call in (("life change for Michael", ("/api/life", {"player": M, "delta": 3, "by": "host"})),
                           ("can't block", ("/api/brain/cant-block", {"seat": M, "ref": f"#{op}"})),
                           ("draw", ("/api/brain/draw", {"seat": M, "n": 1, "quiet": True}))):
            c0, d0 = t.act(M, "next") if t.phase().get("step") == "main 1" else (200, {})
            c, d = t.call("POST", call[0], call[1])
            c2, d2 = t.call("POST", "/api/phase/back", {"by": M}, key=t.keys[M])
            check(c == 200 and c2 == 409 and what in str(d2.get("error")), f"a host {call[0].rsplit('/', 1)[-1]} is a correction BACK won't silently undo", (c, d.get("error"), c2, d2.get("error")))
            t.call("POST", "/api/phase/back", {"by": "host", "force": True})
    finally:
        t.stop()


SECTIONS = [("1a who goes first: the loser chooses play or draw (first)", first_player_chooses),
            ("1b who goes first: the host sets it (first host)", first_player_host),
            ("1c who goes first: the host answers play/draw for the chooser (first host answers)", first_player_host_answers),
            ("2 BACK keeps host corrections (back)", back_keeps_host_corrections),
            ("3 counters by kind on the public board (board)", board_counter_kinds),
            ("4a play/draw: an autopilot chooser plays at once (playdraw auto)", playdraw_autopilot),
            ("4b play/draw: no answer in FIRSTPICK_SECS (playdraw timeout)", playdraw_timeout),
            ("4c play/draw: the chooser is out (playdraw out)", playdraw_seat_out),
            ("4d first player refused during a real-dice roll and in a match's game 2 (first refusals)", first_player_refusals),
            ("4e first player survives a restore, cleared by the next game (first restore)", firstpick_restore),
            ("5 BACK guard: hidden info, force, voided corrections, more kinds (back guard)", back_guard_review)]

if __name__ == "__main__":
    only = os.environ.get("R1115B_ONLY")
    for title, fn in SECTIONS:
        if only and not any(k.lower() in title.lower() for k in only.split("|")):
            continue
        print("\n" + title, flush=True)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — one section that throws is one failure
            traceback.print_exc()
            check(False, f"{title}: raised {type(e).__name__}", e)
    print(("\nFAILED: " + "; ".join(R.FAILS)) if R.FAILS else "\nall good", flush=True)
    sys.exit(1 if R.FAILS else 0)
