"""Game retro 2026-11-15/16, wave 2 (Star Trek set, 1v1, two pilot seats). Each section on a table of its own on a free port (never a live game's:
8800, 8801, 8820, 8830, 8883 are refused), through the route a player uses (the seat's own key); set-up and host corrections use the host token.

  1  who goes first, set by the host (game 2 of a best-of-three: the loser of game 1 chooses play or draw). TABLE_FIRST=<seat>:choose, or the
     host's POST /api/first-player, skips the high roll and says why in the log; the chooser answers play/draw with their own key (nobody else's);
     the mulligan step still runs before the first turn, and the seat that plays first skips its first draw
  2  BACK and host corrections: a BACK that would undo a host correction made since its saved point (a +1/+1 counter from `counter`) is refused
     and names it; the host can force it, and the log then lists the correction to make again
  3  counters by kind on the public board: /api/board3d carries quest, charge and promotion apart from +1/+1 (the pages name each kind)

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 HF_HUB_OFFLINE=1 python3 table/tests/retro_1115b_test.py
    R1115B_ONLY="first|back"   only the sections whose title names one of these
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
from v6_harness import R, Table as _Table, check  # noqa: E402

LIVE_PORTS = (8800, 8801, 8820, 8830, 8883)
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in LIVE_PORTS)   # never a live game's port
R.POOL.update({n: 6 for n in ("Support Mission", "Xenobotanist", "Tal Shiar Operative", "Mugato", "Horta")})
R.DECK_MICHAEL, R.DECK_CLAUDE = R.deck_file("Michael-1115b"), R.deck_file("Claude-1115b")
M, C = "Michael", "Claude"


class Table(_Table):
    """The harness table with extra environment for this one server (restored after it starts)."""
    def __init__(self, env=None):
        old = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try:
            super().__init__()
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


SECTIONS = [("1a who goes first: the loser chooses play or draw (first)", first_player_chooses),
            ("1b who goes first: the host sets it (first host)", first_player_host),
            ("1c who goes first: the host answers play/draw for the chooser (first host answers)", first_player_host_answers),
            ("2 BACK keeps host corrections (back)", back_keeps_host_corrections),
            ("3 counters by kind on the public board (board)", board_counter_kinds)]

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
