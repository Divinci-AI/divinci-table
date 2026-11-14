"""Who plays each seat is public, and a person always wins over the autopilot (2026-11-14, retro M5).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/played_by_test.py

On 2026-11-13 a player went to bed; the host started the rules autopilot on his seat by hand with the seat's key file, and every page
went on showing a person. Proved here, on a scratch table on a free port:

  1. the record: a pilot seat nobody has played is "unknown", never "person" on a guess; the host hands a seat to the autopilot and the
     public log says so; GET /api/played-by, /api/phase, /api/match, /api/spectate and /api/lobby all carry it, with no key;
  2. who may set it: the host any seat; a seat's own key only "a person plays it"; the autopilot only about itself, and never over a person;
     nobody without a key; a remote caller with another seat's key is refused;
  3. THE RULE, at a seat the autopilot is STANDING IN at for a person: a move from the seat's own page (no X-Seat-Driver header) gives the
     seat back to the person, the log says "is back", and the autopilot's next move is refused with 409 "yield"; table talk is not a
     move; a helper's pass is neutral. At the autopilot's OWN seat a move by hand with the seat key changes nothing (it was built to
     share its seat with its operator: retro_round4_test drives a seat by hand beside the bot);
  4. the real bot (bots/claude_autopilot.py): declares itself at start, plays, stands by the moment the person moves (it plays NOTHING
     while standing by), and plays on when the host hands the seat back; the record survives a save and restore.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
import cards_path  # noqa: E402

BOT = Path(os.path.expanduser(os.environ.get("BOT_DIR", str(HERE.parent / "bots"))))
cards_path.need(BOT / "claude_autopilot.py", "the Claude bot", "BOT_DIR=<dir with claude_autopilot.py>")
import match_bo3_test_lib as L  # noqa: E402

R, check, C, M = L.R, L.check, L.C, L.M


def call(t, method, path, body=None, key=None, driver=None, host=False, remote=True):
    """One request. key: a seat key (sent as a remote device would, unless remote=False). driver: the X-Seat-Driver header. host: the brain token."""
    h = {"Content-Type": "application/json"}
    if key:
        h["X-Seat-Key"] = key
        if remote:
            h.update({"X-Forwarded-For": "203.0.113.9", "User-Agent": "played-by-test"})
    if driver:
        h["X-Seat-Driver"] = driver
    if host:
        h["X-Brain-Token"] = t.token
    r = urllib.request.Request(t.b + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def who(t, seat):
    return next(s for s in call(t, "GET", "/api/played-by")[1]["seats"] if s["name"] == seat)


def notes(t, since=0):
    return [e for e in t.events(since) if e.get("type") == "table-note" and e.get("kind") == "played-by"]


print("1. The record, and where everyone can read it", flush=True)
t = L.Table(extra=["--match", "bo3", "--match-countdown", "5"], life=20)
bot = None
try:
    t.claim()
    c, d = call(t, "GET", "/api/played-by")
    check(c == 200 and {s["name"]: s["kind"] for s in d["seats"]} == {C: "unknown", M: "unknown"},
          "a pilot seat nobody has played or declared is 'unknown' (never 'person' on a guess)", d)
    check(call(t, "GET", "/api/played-by", key=t.keys[M])[0] == 200, "…and a remote device may read it")
    c, d = call(t, "POST", "/api/seat/played-by", {"seat": M, "kind": "autopilot"}, host=True)
    check(c == 200 and d["played_by"]["kind"] == "autopilot" and d["played_by"]["label"] == "the rules autopilot", "the host hands Michael's seat to the autopilot", d)
    n = notes(t)
    check(len(n) == 1 and n[0]["text"] == "Michael's seat is now played by the rules autopilot. A move from Michael's own page takes it back."
          and n[0]["seat"] == M and n[0]["played_by"] == "autopilot" and n[0]["stand_in"] is True and d["played_by"].get("stand_in") is True,
          "the public log says: Michael's seat is now played by the rules autopilot (and that Michael takes it back by playing)", n)
    call(t, "POST", "/api/seat/played-by", {"seat": M, "kind": "autopilot"}, host=True)
    check(len(notes(t)) == 1, "saying the same thing twice is not written twice")
    ph = call(t, "GET", "/api/phase")[1]
    check(ph["played_by"][M]["kind"] == "autopilot" and ph["played_by"][C]["kind"] == "unknown", "/api/phase carries it", ph.get("played_by"))
    check(call(t, "GET", "/api/match")[1]["played_by"][M]["kind"] == "autopilot", "/api/match carries it (a stream overlay reads that)")
    check(call(t, "GET", "/api/spectate")[1]["played_by"][M]["kind"] == "autopilot", "/api/spectate carries it (the /watch page)")
    lob = call(t, "GET", "/api/lobby?self=1")[1]["table"]["seats"]
    check(next(s for s in lob if s["name"] == M)["played_by"]["kind"] == "autopilot", "/api/lobby carries it", lob)
    page = urllib.request.urlopen(t.b + "/watch").read().decode()
    check('src="/assets/playedby.js"' in page and urllib.request.urlopen(t.b + "/assets/playedby.js").status == 200,
          "/watch loads the line that shows it (assets/playedby.js)")

    print("2. Who may set it", flush=True)
    check(call(t, "POST", "/api/seat/played-by", {"seat": M, "kind": "person"})[0] == 403, "nobody without a key or the host's token")
    check(call(t, "POST", "/api/seat/played-by", {"seat": M, "kind": "person"}, key=t.keys[C])[0] == 403, "not another seat's key")
    check(call(t, "POST", "/api/seat/played-by", {"seat": C, "kind": "autopilot"}, key=t.keys[C])[0] == 403,
          "a seat's own key cannot hand the seat to the autopilot (the host does that: the bot runs on the host's laptop)")
    check(call(t, "POST", "/api/seat/played-by", {"seat": C, "kind": "wizard"}, host=True)[0] == 400, "an unknown kind is refused")
    check(call(t, "POST", "/api/seat/played-by", {"seat": "Nobody", "kind": "person"}, host=True)[0] == 404, "an unknown seat is refused")
    c, d = call(t, "POST", "/api/seat/played-by", {"seat": C, "kind": "autopilot"}, key=t.keys[C], driver="autopilot")
    check(c == 200 and who(t, C)["kind"] == "autopilot", "the autopilot may say what it is at a seat nobody has claimed for a person", d)
    c, d = call(t, "POST", "/api/seat/played-by", {"seat": C, "kind": "person"}, key=t.keys[C], driver="autopilot")
    check(c == 403, "…and can say nothing else", d)
    c, d = call(t, "POST", "/api/seat/played-by", {"seat": C, "kind": "person"}, key=t.keys[C])
    check(c == 200 and who(t, C)["kind"] == "person" and who(t, C)["label"] == C, "the seat's own key says: a person plays it", d)
    c, d = call(t, "POST", "/api/seat/played-by", {"seat": C, "kind": "autopilot"}, key=t.keys[C], driver="autopilot")
    check(c == 409 and d.get("yield") is True and who(t, C)["kind"] == "person", "the autopilot cannot take a seat a person holds (409, yield)", d)
    c, d = call(t, "POST", "/api/seat/played-by", {"seat": C, "kind": "model", "label": "ollama:gemma4:e2b"}, host=True)
    check(c == 200 and who(t, C) == {**who(t, C), "kind": "model", "label": "ollama:gemma4:e2b"}, "the host names a model", d)
    check(notes(t)[-1]["text"] == "Claude's seat is now played by ollama:gemma4:e2b.", "…and the log names it", notes(t)[-1]["text"])
    call(t, "POST", "/api/seat/played-by", {"seat": C, "kind": "person", "label": "Ann <b>"}, host=True)
    check(who(t, C)["label"] == "Ann <b>" and notes(t)[-1]["text"] == "Claude's seat is now played by Ann <b> (a person).", "a person standing in is named", notes(t)[-1])
    call(t, "POST", "/api/seat/played-by", {"seat": C, "kind": "person"}, host=True)

    print("3. The rule: the person wins", flush=True)
    t.call("POST", "/api/phase/next", {"by": C, "key": t.keys[C]}, key=t.keys[C])
    ph = t.keep_all()
    first = ph["player"]
    check(first in (C, M), "the game is under way", ph.get("player"))
    since = t.last_id()
    # Michael's seat is the autopilot's (set in part 1). A move with the autopilot's header is taken…
    c, d = call(t, "POST", "/api/brain/say", {"seat": M, "text": "Good luck."}, key=t.keys[M])
    check(c == 200 and who(t, M)["kind"] == "autopilot", "table talk from the seat's key is not a move: the seat stays the autopilot's", (c, d))
    c, d = call(t, "POST", "/api/brain/pass", {"seat": M, "player": first, "step": ph["step"]}, key=t.keys[M], driver="helper")
    check(who(t, M)["kind"] == "autopilot", "a helper's pass (claude_autopass.py) says nothing about who plays the seat", (c, d))
    c, d = call(t, "POST", "/api/brain/pass", {"seat": M, "player": first, "step": ph["step"]}, key=t.keys[M], driver="autopilot")
    check(not d.get("yield") and who(t, M)["kind"] == "autopilot", "the autopilot's own move is not refused for 'yield' while the seat is its own", (c, d))
    # …then the person makes a move from their page (no driver header):
    c, d = call(t, "POST", "/api/brain/pass", {"seat": M, "player": first, "step": ph["step"]}, key=t.keys[M])
    check(not d.get("yield") and who(t, M)["kind"] == "person", "a move from the seat's own page takes the seat back", (c, d, who(t, M)))
    n = notes(t, since)
    check(len(n) == 1 and n[0]["text"] == "Michael is back: Michael's seat is played by a person again." and n[0]["by"] == "return" and n[0]["was"] == "autopilot",
          "the log says: Michael is back", n)
    c, d = call(t, "POST", "/api/brain/pass", {"seat": M, "player": first, "step": ph["step"]}, key=t.keys[M], driver="autopilot")
    check(c == 409 and d.get("yield") is True and d["played_by"]["kind"] == "person", "from then on the autopilot's move is refused: 409, yield", (c, d))
    c, d = call(t, "POST", "/api/brain/end", {"seat": M}, key=t.keys[M], driver="autopilot")
    check(c == 409 and d.get("yield") is True, "…every move, not only a pass", (c, d))
    check(len(notes(t, since)) == 1, "a refused autopilot move writes nothing into the log")
    c, d = call(t, "POST", "/api/seat/played-by", {"seat": M, "kind": "autopilot"}, host=True)
    c, d = call(t, "POST", "/api/brain/pass", {"seat": M, "player": first, "step": ph["step"]}, key=t.keys[M], driver="autopilot")
    check(not d.get("yield"), "the host hands the seat back to the autopilot and its moves are taken again", (c, d))
    call(t, "POST", "/api/seat/played-by", {"seat": M, "kind": "person"}, host=True)
    # the autopilot's OWN seat (not standing in for anybody): its operator may move by hand, and nothing changes hands
    t2 = L.Table(life=20)
    try:
        t2.open_game(by=C)
        ph2 = t2.phase()
        act = ph2["player"]
        other = M if act == C else C

        def move(seat, driver=None):
            p_ = t2.phase()
            return call(t2, "POST", "/api/brain/pass", {"seat": seat, "player": p_["player"], "step": p_["step"]}, key=t2.keys[seat], driver=driver)
        move(act)
        check(who(t2, act)["kind"] == "person", "a seat nobody declared becomes a person's at its first move from its page", who(t2, act))
        check(not [e for e in notes(t2) if e["seat"] == act], "…quietly: a person playing their own seat from the start is not announced in the log")
        c, d = call(t2, "POST", "/api/identity", {"seat": act, "model": "ollama:gemma-test"}, key=t2.keys[act])
        check(who(t2, act)["kind"] == "model" and who(t2, act)["label"] == "ollama:gemma-test",
              "…and a seat that then declares the model that PLAYS it (/api/identity) is shown as that model", who(t2, act))
        move(other)
        c, d = call(t2, "POST", "/api/seat/played-by", {"seat": other, "kind": "autopilot"}, key=t2.keys[other], driver="autopilot")
        check(c == 200 and who(t2, other)["kind"] == "autopilot" and not who(t2, other).get("stand_in"),
              "the autopilot, started at a seat the table had only GUESSED a person at, says what it is (its own seat: not a stand-in)", (c, d))
        c, d = move(other)
        check(not d.get("yield") and who(t2, other)["kind"] == "autopilot", "a move by hand at the autopilot's own seat changes nothing", (c, d, who(t2, other)))
        c, d = move(other, driver="autopilot")
        check(not d.get("yield"), "…and the autopilot is not refused after it", (c, d))
        check(not [e for e in notes(t2) if e["seat"] == other and e["by"] == "return"], "…and nobody 'is back'")
        c, d = call(t2, "POST", "/api/identity", {"seat": other, "model": "anthropic:claude-test", "role": "talk"}, key=t2.keys[other])
        check(c == 200 and who(t2, other)["kind"] == "autopilot" and who(t2, other).get("talk") == "anthropic:claude-test",
              "a model that only TALKS for a seat (role: talk) is shown as its voice, not as its player", who(t2, other))
        c, d = call(t2, "POST", "/api/seat/played-by", {"seat": other, "kind": "autopilot", "stand_in": False}, host=True)
        check(c == 200 and not d["played_by"].get("stand_in"), "the host can set a seat as the autopilot's own (stand_in: false)", d)
        # the record is saved with the game
        snap = next(Path(t2.dir / "r").rglob("snapshot.pkl"))
        t2.stop()
        t3 = L.Table(extra=["--restore", str(snap)], life=20)
        try:
            check(who(t3, other)["kind"] == "autopilot" and who(t3, act)["kind"] == "model", "the record survives a save and restore",
                  call(t3, "GET", "/api/played-by")[1])
        finally:
            t3.stop()
    finally:
        t2.stop()

    print("4. The real bot stands by for a person, and plays on when the seat is handed back", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="pb-"))
    kf = tmp / "m.key"
    kf.write_text(t.keys[M]); kf.chmod(0o600)
    log = tmp / "bot.log"
    env = {**os.environ, "TABLE_URL": t.b, "CLAUDE_SEAT": M, "CLAUDE_KEY_FILE": str(kf), "AUTOPILOT_GRACE": "0", "PYTHONDONTWRITEBYTECODE": "1"}
    bot = subprocess.Popen([sys.executable, "-u", str(BOT / "claude_autopilot.py")], env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT)
    check(t.until(lambda: "standing by" in log.read_text(), 20), "started at a seat a person holds, the bot stands by at once", log.read_text()[-400:])
    check(who(t, M)["kind"] == "person", "…and the seat is still the person's")
    call(t, "POST", "/api/seat/played-by", {"seat": M, "kind": "autopilot"}, host=True)
    check(t.until(lambda: "the autopilot's again: playing on" in log.read_text(), 20), "the host hands the seat over: the bot plays on", log.read_text()[-400:])

    def bot_moves():
        return [e for e in t.events() if e.get("by") == M or e.get("seat") == M or e.get("speaker") == M or e.get("player") == M]
    # play until the bot has clearly acted for Michael's seat (the other seat, the test, passes and ends its turns)
    def drive_other():
        p = t.phase()
        if p.get("game_over"):
            return True
        if p.get("player") == C and p.get("begun"):
            call(t, "POST", "/api/brain/end", {"seat": C}, key=t.keys[C])
        elif p.get("player") == C:
            call(t, "POST", "/api/brain/begin", {"seat": C}, key=t.keys[C])
        elif (p.get("passes") or {}).get("next") == C:
            call(t, "POST", "/api/brain/pass", {"seat": C, "player": p.get("player"), "step": p.get("step")}, key=t.keys[C])
        return "  land:" in log.read_text() or "  end" in log.read_text()
    check(t.until(drive_other, 60), "the bot plays Michael's turn (a land, or End)", log.read_text()[-600:])
    # the person comes back and makes a move: the bot must yield, and play nothing afterwards
    p = t.phase()
    call(t, "POST", "/api/brain/pass", {"seat": M, "player": p.get("player"), "step": p.get("step")}, key=t.keys[M])
    check(who(t, M)["kind"] == "person", "the person moves: the seat is theirs")
    check(t.until(lambda: log.read_text().count("standing by") >= 2, 20), "the bot notices at its next move and stands by", log.read_text()[-400:])
    mark, size = t.last_id(), len(log.read_text())
    for _ in range(40):                                # ~8 s of the game going on around it: the other seat passes and ends turns
        p = t.phase()
        if p.get("player") == C and p.get("begun"):
            call(t, "POST", "/api/brain/end", {"seat": C}, key=t.keys[C])
        elif (p.get("passes") or {}).get("next") == C:
            call(t, "POST", "/api/brain/pass", {"seat": C, "player": p.get("player"), "step": p.get("step")}, key=t.keys[C])
        time.sleep(0.2)
    mine = [e for e in t.events(mark) if e.get("type") in ("play", "cast", "attack", "pass", "phase") and (e.get("by") == M) and not e.get("auto")]
    check(not mine and "Traceback" not in log.read_text() and bot.poll() is None,
          "while it stands by the bot makes NO move for the seat, and it is still running", (mine[:3], log.read_text()[size:][-300:]))
finally:
    if bot is not None and bot.poll() is None:
        bot.terminate()
    t.stop()

print("\nFAILED: " + "; ".join(R.FAILS) if R.FAILS else "\nall good")
sys.exit(1 if R.FAILS else 0)
