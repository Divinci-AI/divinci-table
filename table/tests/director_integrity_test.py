"""The director's integrity rules (director/integrity.py, watch.py, config.py, tableclient.py), against a fake table.

What each block proves:
  A. config: a table with a person cannot be made live from the config file; kind has no default; a key may not be written in the config.
  B. consent: a person's hand is never even FETCHED without their consent for the current game (the fake counts private reads);
     consent for one game is not consent for the next; withdrawing it throws the buffer away.
  C. delay: at a table with a person, a card drawn this turn is not shown until the table's spectator boundary has passed the moment
     the director saw it; thoughts and the "why" of a move obey the same boundary; an all-AI table shows at once.
  D. panic: hide() empties what is on screen and what is buffered; un-hiding does not bring the old buffer back.
  E. keys: read from a 0600 file only (a readable-by-others file is refused), never in any view, error or status; the table client
     cannot POST.
  F. only allow-listed fields of a hand card leave the server (no target slots, no ids, nothing else from the private state)."""
from __future__ import annotations

import inspect
import json
import os
import tempfile
from pathlib import Path

from director_lib import FakeTable, check, done, keyfile

from director import config as cfg, integrity as ig, tableclient
from director.server import Director

TMP = Path(tempfile.mkdtemp(prefix="dir-int-"))
KEY_H, KEY_A, KEY_B = "HUMANKEY-" + "h" * 30, "AIKEY-" + "a" * 30, "AIKEY-" + "b" * 30


def conf(extra=None, **kw):
    d = {"tables": [
        {"name": "T1", "url": "http://127.0.0.1:8921", "seats": [{"name": "Michael", "kind": "human", "key_file": keyfile(TMP, "Michael", KEY_H)},
                                                              {"name": "Claude", "kind": "ai", "key_file": keyfile(TMP, "Claude", KEY_A), "player": "rules autopilot"}]},
        {"name": "T2", "url": "http://127.0.0.1:8922", "seats": [{"name": "Fusion", "kind": "ai", "key_file": keyfile(TMP, "Fusion", KEY_B)},
                                                              {"name": "Botty", "kind": "ai", "key_file": keyfile(TMP, "Botty", KEY_B)}]}]}
    d.update(extra or {})
    return cfg.parse(d, **kw)


class Clock:
    t = 1000.0
    def __call__(self):
        return self.t


def setup(extra=None):
    clock = Clock()
    f1, f2 = FakeTable(["Michael", "Claude"]), FakeTable(["Fusion", "Botty"], game="g2")
    f1.keys, f2.keys = {"Michael": KEY_H, "Claude": KEY_A}, {"Fusion": KEY_B, "Botty": KEY_B}
    d = Director(conf(extra), clients={"T1": f1, "T2": f2}, clock=clock)
    return d, f1, f2, clock


print("A. config")
try:
    conf({"delay": {"turns": 0}})
    check(False, "a table with a person cannot be configured live from the file")
except cfg.ConfigError as e:
    check("--i-know-live-human-hands" in str(e), "a table with a person cannot be configured live from the file (and the error names the flag)", str(e))
try:
    c = conf({"delay": {"turns": 0}}, i_know_live=True)
    check(c.delay_for(c.tables[0]) == (0, 0.0), "with the explicit flag it can")
except cfg.ConfigError as e:
    check(False, "with the explicit flag it can", str(e))
try:
    conf({"delay": {"turns": 0, "secs": 5}})
    check(False, "0 turns and a few seconds is still 'live' for a person's table")
except cfg.ConfigError:
    check(True, "0 turns and a few seconds is still 'live' for a person's table")
c = conf()
check(c.delay_for(c.tables[0]) == (1, 0.0) and c.delay_for(c.tables[1]) == (0, 0.0), "defaults: one turn behind where a person plays; live at an all-AI table")
for bad, what in (({"tables": [{"name": "x", "url": "http://127.0.0.1:8921", "seats": [{"name": "A"}]}]}, "a seat with no kind is refused (no default)"),
                  ({"tables": [{"name": "x", "url": "http://127.0.0.1:8921", "seats": [{"name": "A", "kind": "ai", "key": "abc"}]}]}, "a key written into the config is refused"),
                  ({"tables": [{"name": "x", "url": "http://example.com:80", "seats": [{"name": "A", "kind": "ai"}]}]}, "a table that is not on this laptop is refused"),
                  ({"tables": [{"name": "x", "url": "http://127.0.0.1:8921/?key=abc", "seats": [{"name": "A", "kind": "ai"}]}]}, "a table URL with a query (a key in a URL) is refused")):
    try:
        cfg.parse(bad); check(False, what)
    except cfg.ConfigError:
        check(True, what)

print("B. consent")
d, f1, f2, clock = setup()
f1.hands["Michael"] = [f1.card("Michael Secret Card")]
f1.hands["Claude"] = [f1.card("Claude Secret Card")]
for _ in range(3):
    d.poll(); clock.t += 2
check(not any(s == "Michael" for _, s in f1.private_calls), "without consent a person's private state is never fetched", str(f1.private_calls[:3]))
check(any(s == "Claude" for _, s in f1.private_calls), "the AI seat at the same table is read (default on)")
v = d.watches[0].view()
me = next(s for s in v["seats"] if s["name"] == "Michael")
check(me["private"]["hand"] is None and "not switched it on" in me["private"]["reason"], "the view says why the hand is not shown", me["private"]["reason"])
f1.consent = {"Michael": True}
d.poll(); clock.t += 2
check(any(s == "Michael" for _, s in f1.private_calls), "after the person consents (the table says so) the seat is read")
check(d.watches[0].seat_private("Michael")["hand"] is None, "…but nothing is shown yet: the delay still applies")
f1.turn_ends(); d.poll(); clock.t += 2      # boundary = events so far; the observation was tagged at the same id
check(d.watches[0].seat_private("Michael")["hand"] is not None, "once the turn boundary passes the observation, the consented hand is shown")
f1.consent = {}
d.poll()
check(d.watches[0].seat_private("Michael")["hand"] is None and not d.watches[0].buf["Michael"].snaps, "withdrawing consent hides the hand and throws the buffer away")
f1.consent = {"Michael": True}; d.poll(); f1.turn_ends(); f1.emit("phase"); d.poll()
f1.game = "g1b"; f1.events.clear(); f1.spect = None      # a new game at the same table; the table reports no consent for it
f1.consent = {}
n0 = sum(1 for _, s in f1.private_calls if s == "Michael")
d.poll(); d.poll()
check(sum(1 for _, s in f1.private_calls if s == "Michael") == n0, "a new game: last game's consent does not carry over (no fetch)")
check(d.watches[0].seat_private("Claude")["hand"] is None and d.watches[0].shown_events() == [], "a new game: nothing from the last game's buffers is shown")

print("C. delay")
d, f1, f2, clock = setup()
w = d.watches[0]
f1.hands["Claude"] = [f1.card("Old Card")]
f1.emit("phase", player="Michael"); d.poll(); clock.t += 2
f1.turn_ends()                                   # boundary B1 = event 1
f1.emit("say", speaker="Claude", text="Claude, turn 2.", action="begin")
d.poll(); clock.t += 2
check([c_["name"] for c_ in w.seat_private("Claude")["hand"] or []] == ["Old Card"], "the hand as it stood at the last turn boundary is shown")
f1.hands["Claude"] = [f1.card("Old Card"), f1.card("Just Drawn Card")]
f1.legal["Claude"] = [{"action": "cast", "body": {"seat": "Claude", "name": "Old Card"}, "why": "your best play: a 2/2 for one"}]
f1.thoughts["Claude"] = [{"id": 1, "ev": 2, "kind": "thought", "text": "I am holding Just Drawn Card for later."}]
d.poll(); clock.t += 2
f1.emit("say", speaker="Claude", text="I cast Old Card.", action="cast")
f1.emit("cast", player="Claude", name="Old Card", mv=1.0)
d.poll(); clock.t += 2
blob = json.dumps(d.program()) + json.dumps(d.pov("Claude")) + json.dumps(w.view())
check("Just Drawn Card" not in blob, "a card drawn this turn is NOT on any page while the turn is being played")
check("I cast Old Card" not in blob and "your best play" not in blob, "this turn's move and its reason are not shown yet either")
check("holding Just Drawn" not in blob, "a thought written this turn is not shown yet")
f1.turn_ends(); d.poll(); clock.t += 2            # the turn ended: boundary moves past everything above
pr = w.seat_private("Claude")
check("Just Drawn Card" in json.dumps(pr["hand"]), "after the turn boundary the hand as last seen is shown")
mv = [i for i in pr["thinking"] if i["source"] == "move"]
check(mv and mv[-1]["why"] == "your best play: a 2/2 for one", "the autopilot's move carries the table's own reason from the legal list (never invented)", str(pr["thinking"]))
check(any(i["source"] == "thought" for i in pr["thinking"]), "the seat's own thought is shown after the boundary")
f1.emit("say", speaker="Claude", text="I attack with something unlisted.", action="attack")
d.poll(); f1.turn_ends(); d.poll()
mv = [i for i in w.seat_private("Claude")["thinking"] if i["source"] == "move"]
check(mv[-1]["why"] is None, "a move with no matching listed reason shows the public line only: no reason is made up")
# the all-AI table: live
f2.hands["Fusion"] = [f2.card("Fusion Live Card")]
d.poll()
check("Fusion Live Card" in json.dumps(d.pov("Fusion")), "an all-AI table shows hands at once (no person can benefit)")
check(d.watches[1].delay_label().startswith("live"), "and says so on screen", d.watches[1].delay_label())
# extra seconds
d3, g1, g2, clock3 = setup({"tables": None} if False else None)
d3.watches[1].secs = 30.0
g2.hands["Fusion"] = [g2.card("Timed Card")]
d3.poll()
check(d3.watches[1].seat_private("Fusion")["hand"] is None, "an extra delay in seconds holds a hand back")
clock3.t += 31; d3.poll()
check(d3.watches[1].seat_private("Fusion")["hand"] is not None, "…and releases it when the seconds have passed")
# pure rules
check(ig.boundary(1, [], False) == -1 and ig.boundary(1, [7, 9], False) == 9 and ig.boundary(2, [7, 9], False) == 7 and ig.boundary(2, [9], False) == -1,
      "boundary(): N turns behind is the Nth-newest spectator snapshot; not enough turns yet means nothing")
check(ig.boundary(1, [3], True) == ig.INF, "a finished game is released")
b = ig.SeatBuffer(); b.add_hand(10, 100.0, [{"name": "X"}])
check(b.released(9, 200.0)[0] is None and b.released(10, 200.0)[0] is not None, "an observation tagged at event 10 is not released at boundary 9")

print("D. panic")
d, f1, f2, clock = setup()
f2.hands["Fusion"] = [f2.card("Panic Card")]
d.poll()
check("Panic Card" in json.dumps(d.program()) or "Panic Card" in json.dumps(d.pov("Fusion")), "(before) the hand is on the stream")
code, st = d.control({"cmd": "hide"})
blob = json.dumps(d.program()) + json.dumps(d.pov("Fusion"))
check(code == 200 and "Panic Card" not in blob, "hide: the hand is gone from every page at once, before the next poll")
n0 = len(f2.private_calls); d.poll()
check(len(f2.private_calls) == n0, "while hidden the seat's private state is not even fetched")
# the race: the operator hides while a poll is already in flight (it has fetched the hand but not yet buffered it)
d.control({"cmd": "unhide"})
orig_public = f2.public
def hide_mid_poll(path, query=""):
    if path == "/api/events":
        d.watches[1].hide(None)
    return orig_public(path, query)
f2.public = hide_mid_poll
d.poll()
f2.public = orig_public
check(not d.watches[1].buf["Fusion"].snaps and "Panic Card" not in json.dumps(d.pov("Fusion")), "hidden while a poll was in flight: what that poll fetched is dropped, not shown")
check(d.pov("Fusion")["stage"]["me"]["private"]["reason"] == "hidden by the operator", "and the page is told why, at once")
d.control({"cmd": "unhide"})
check("Panic Card" not in json.dumps(d.pov("Fusion")), "un-hide does not bring the old buffer back")
d.poll()
check("Panic Card" in json.dumps(d.pov("Fusion")), "…it starts again from a fresh read")
d.control({"cmd": "hide", "table": "T2", "seat": "Fusion"})
check("Fusion" in d.watches[1].hidden and "Botty" not in d.watches[1].hidden, "one seat can be hidden on its own")

print("E. keys")
d, f1, f2, clock = setup()
f1.consent = {"Michael": True}
f1.hands["Michael"] = [f1.card("M")]
d.poll(); f1.turn_ends(); d.poll()
everything = json.dumps(d.program()) + json.dumps(d.pov("Michael")) + json.dumps(d.pov("Claude")) + json.dumps(d.control_state()) + \
    json.dumps([w_.view() for w_ in d.watches]) + repr(d.c)
for k, who in ((KEY_H, "the person's"), (KEY_A, "an AI's")):
    check(k not in everything, f"{who} seat key is in no page data, no control state and not in the parsed config")
check("key_file" not in json.dumps(d.program()) + json.dumps(d.control_state()), "not even the key file's PATH reaches a page")
f1.keys["Claude"] = "a-different-key"
d.poll()
check(not d.watches[0].vis["Claude"].read and "refused" in d.watches[0].vis["Claude"].reason and KEY_A not in d.watches[0].vis["Claude"].reason,
      "a refused key is reported as refused, without the key", d.watches[0].vis["Claude"].reason)
loose = TMP / "loose.key"; loose.write_text("k" * 20); os.chmod(loose, 0o644)
try:
    cfg.read_secret_file(str(loose), "seat key"); check(False, "a key file others can read is refused")
except cfg.ConfigError as e:
    check("chmod 600" in str(e) and "kkkk" not in str(e), "a key file others can read is refused (and the error does not quote it)", str(e))
src = inspect.getsource(tableclient)
check("POST" not in src.replace('"""', "").split("from __future__")[1] and "data=" not in src, "the table client has no way to POST: a director holding a person's key cannot act for them")
try:
    tableclient.TableClient("http://127.0.0.1:9").private("/api/brain/say", "Claude", keyfile(TMP, "Claude", KEY_A))
    check(False, "a path outside the read allow-list is refused")
except tableclient.TableError:
    check(True, "a path outside the read allow-list is refused")
try:
    tableclient.TableClient("http://127.0.0.1:9", timeout=1).private("/api/brain/state", "Claude", keyfile(TMP, "Claude", KEY_A))
    check(False, "an unreachable table raises")
except tableclient.TableError as e:
    check(KEY_A not in repr(e) and e.__cause__ is None and e.__suppress_context__, "a failed request's error does not carry the key or the request (the cause is cut off)")

print("F. allow-list")
d, f1, f2, clock = setup()
f2.hands["Fusion"] = [f2.card("Listed", secret_field="LEAK-ME")]
d.poll()
blob = json.dumps(d.pov("Fusion")["stage"])
check("Listed" in blob and "LEAK-ME" not in blob and '"slots"' not in blob and "TOP-SECRET" not in blob and "castable_now" not in blob
      and not any(x in blob for x in ("OPP-HAND-CARD", "OPP-HAND-TEXT", "LIB-BASIC-LAND", "pick_options", "by_hand_ok", "equip_targets", "reveal")),
      "only name, cost, type, text and P/T of a hand card leave the server (no slots, no other private state such as the library's top card)")
pv = d.pov("Fusion")
check(all(not o["private"]["shown"] for o in pv["stage"]["opponents"]), "a point-of-view page carries ONE seat's hand: the other seat's is not on it")
done()
