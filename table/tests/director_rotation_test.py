"""The director's rotation (director/rotation.py) and how the Director feeds it: a schedule, cuts to the action, a recap, the operator.

Proves: each seat gets its dwell and the schedule wraps; a big moment at the other table cuts there and the schedule resumes afterwards;
cuts do not ping-pong inside min_hold unless something bigger happens; a game ending shows that table's recap once per game and then the
rotation moves on; a pinned seat ignores triggers; blank wins over everything; events that happened before the director started are not
treated as news; triggers at a person's table come only from events the stream may already see (the delayed log)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from director_lib import FakeTable, check, done, keyfile

from director import config as cfg
from director.rotation import Rotation
from director.server import Director

S = [(0, "A"), (0, "B"), (1, "C"), (1, "D")]
r = Rotation(S, dwell=45, min_hold=10, action_hold=18, recap_secs=20, now=0)
check(r.featured(0) == ((0, "A"), "schedule"), "starts on the first seat")
check(r.featured(44)[0] == (0, "A") and r.featured(45)[0] == (0, "B"), "a seat is on for its dwell, then the next")
check(r.featured(90)[0] == (1, "C") and r.featured(135)[0] == (1, "D") and r.featured(180)[0] == (0, "A"), "the schedule visits every seat at both tables and wraps")

r = Rotation(S, 45, 10, 18, 20, now=0)
check(r.on_trigger(5, 1, "D", "combat", 40) and r.featured(6) == ((1, "D"), "action:combat"), "combat at the other table cuts to the attacking seat")
check(not r.on_trigger(8, 0, "B", "big-spell", 30) and r.featured(9)[0] == (1, "D"), "a smaller moment inside min_hold does not cut away (no ping-pong)")
check(r.on_trigger(9, 0, "B", "life-swing", 60) and r.featured(9.5)[0] == (0, "B"), "a bigger moment does")
check(r.featured(9 + 18 + 0.1) == ((0, "A"), "schedule"), "after the action hold the schedule resumes where it was")
check(not r.on_trigger(40, 0, "A", "combat", 40), "a trigger for the seat already on screen changes nothing")
check(r.on_trigger(60, 1, "nobody-by-that-name", "combat", 40) and r.featured(61)[0][0] == 1, "a trigger naming no listed seat still cuts to that table")

r = Rotation(S, 45, 10, 18, 20, now=0)
check(r.on_trigger(10, 1, "C", "game-over", 100, game_id="g") and r.featured(11) == ((1, None), "recap"), "a game ending shows that table's recap")
check(not r.on_trigger(12, 0, "A", "life-swing", 60) and r.featured(13)[1] == "recap", "the recap is not interrupted")
check(not r.on_trigger(14, 1, "C", "game-over", 100, game_id="g"), "one recap per game")
slot, mode = r.featured(31)
check(mode == "schedule" and slot == (0, "B"), "after the recap the rotation moves on", f"{slot} {mode}")

r = Rotation(S, 45, 10, 18, 20, now=0)
check(r.pin((1, "C")) and r.featured(500) == ((1, "C"), "pinned"), "a pinned seat stays, however long")
check(not r.on_trigger(501, 0, "A", "life-swing", 60) and r.featured(502)[0] == (1, "C"), "…and ignores the action")
check(not r.pin((7, "Z")), "pinning a seat that is not on the rotation is refused")
r.set_blank(True)
check(r.featured(503) == (None, "blank"), "blank wins over a pin")
r.set_blank(False); r.unpin(504)
check(r.featured(505)[1] == "schedule", "un-pin returns to the schedule")
r.next(506)
check(r.featured(506)[0] == (0, "B"), "next: the following seat, now")

# through the Director, with fake tables
TMP = Path(tempfile.mkdtemp(prefix="dir-rot-"))
c = cfg.parse({"tables": [
    {"name": "T1", "url": "http://127.0.0.1:8921", "seats": [{"name": "Michael", "kind": "human", "key_file": keyfile(TMP, "Michael", "h" * 30)}, {"name": "Claude", "kind": "ai", "key_file": keyfile(TMP, "Claude", "k" * 30)}]},
    {"name": "T2", "url": "http://127.0.0.1:8922", "seats": [{"name": "Fusion", "kind": "ai", "key_file": keyfile(TMP, "Fusion", "k" * 30)},
                                                          {"name": "Botty", "kind": "ai", "key_file": keyfile(TMP, "Botty", "k" * 30)}]}]})


class Clock:
    t = 1000.0
    def __call__(self):
        return self.t


clock = Clock()
f1, f2 = FakeTable(["Michael", "Claude"]), FakeTable(["Fusion", "Botty"], game="g2")
f2.emit("life", player="Botty", delta=-9, life=11, attacker="Fusion")       # happened before the director started
d = Director(c, clients={"T1": f1, "T2": f2}, clock=clock)
d.poll()
p = d.program()
check(p["mode"] == "schedule" and p["stage"]["me"]["name"] == "Michael", "old events are not news: the director starts on its schedule", p["mode"])
check(p["stage"]["lower_third"]["name"] == "Michael" and "not switched it on" in p["stage"]["lower_third"]["hand"], "the lower third names the featured player and says the hand is hidden")
check(len(p["pip"]) == 1 and p["pip"][0]["name"] == "T2", "the other table is the picture-in-picture")
clock.t += 3
f2.emit("life", player="Botty", delta=-6, life=5, attacker="Fusion")
d.poll()
p = d.program()
check(p["mode"] == "action:life-swing" and p["stage"]["me"]["name"] == "Fusion" and p["pip"][0]["name"] == "T1", "a big life swing at the all-AI table cuts there, live", p["mode"])
clock.t += 30
f1.emit("say", speaker="Claude", text="I attack Michael.", action="attack")   # at the person's table: live, but not yet on the stream's log
d.poll()
check(d.program()["mode"] == "schedule", "a moment at a person's table does not cut until the stream may see it (the delayed log)", d.program()["mode"])
f1.turn_ends(); d.poll()
check(d.program()["mode"] == "action:combat" and d.program()["stage"]["me"]["name"] == "Claude", "…then it does", d.program()["mode"])
clock.t += 40
f2.over = True
f2.emit("game-over", winner="Fusion")
d.poll(); d.poll()
p = d.program()
check(p["mode"] == "recap" and p["stage"]["recap"]["winner"] == "Fusion" and p["stage"]["table"] == "T2", "game over: cut to that table and show the recap", p["mode"])
clock.t += 21
check(d.program()["mode"] == "schedule", "then the rotation resumes")
f2.match = {"on": True, "wins": {"Fusion": 1, "Botty": 0}, "score": "1-0", "current": {"n": 2}}
d.poll()
check(d.pov("Fusion")["stage"]["match"]["wins"] == {"Fusion": 1, "Botty": 0}, "a table with best-of-three shows the match score; one without shows none")
check(d.pov("Claude")["stage"]["match"] is None, "(no /api/match on that table: tolerated)")
code, _ = d.control({"cmd": "pin", "n": 3})
check(code == 200 and d.program()["mode"] == "pinned" and d.program()["stage"]["me"]["name"] == "Fusion", "the operator pins seat 3")
code, _ = d.control({"cmd": "blank", "on": True})
p = d.program()
check(p["mode"] == "blank" and p["stage"] is None and p["pip"] == [], "blank: nothing of any table is in the page data")
done()
