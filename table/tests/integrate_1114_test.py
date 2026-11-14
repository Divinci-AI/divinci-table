"""Where the three 2026-11-13/14 branches meet (retro-20261113, flow-20261114 with bo3-20261114, pov-20261114).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 TABLE_OFFLINE=1 python3 table/tests/integrate_1114_test.py

Each branch has its own tests. These are the interactions none of them could test alone, on scratch tables on free ports:

  1. best of three: the next game inherits none of the card-fix branch's per-game state: an "until end of turn" change (Perm.temp),
     a land token made by hand (New Planet), a by-hand tap that could be taken back, a creature token;
  2. played-by: an autopilot STANDING IN for a person is not refused for the new land moves (tap, take back a tap); the person's first
     such move from their own page takes the seat back, and the autopilot's next one is refused with "yield".
  3. /watch: with the new scripts on the pages it still loads only static or public helpers and reads only /api/spectate.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
import match_bo3_test_lib as L  # noqa: E402

R, check, C, M = L.R, L.check, L.C, L.M


def call(t, method, path, body=None, key=None, driver=None):
    """A seat's request as a remote device sends it, with an optional X-Seat-Driver."""
    h = {"Content-Type": "application/json", "X-Seat-Key": key, "X-Forwarded-For": "203.0.113.9", "User-Agent": "integrate-test"}
    if driver:
        h["X-Seat-Driver"] = driver
    r = urllib.request.Request(t.b + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def who(t, seat):
    return next(s for s in t.call("GET", "/api/played-by", host=False)[1]["seats"] if s["name"] == seat)


def main():
    t = L.Table(extra=["--match", "bo3", "--match-countdown", "2"], roll=True)
    try:
        ph = t.open_game()
        first = ph["player"]
        other = M if first == C else C
        k = t.keys[first]
        print("\n── 1. game 1 leaves the card-fix branch's per-game state lying around")
        cre = t.onto(first, "Starfleet Security")
        t.lands(first, 2, "Mountain")
        land = next(p["id"] for p in t.state(first)["permanents"] if p["name"] == "Mountain")
        c, d = t.call("POST", "/api/brain/manual", {"seat": first, "op": "temp", "ref": f"#{cre}", "power": 3, "toughness": 3})     # host token
        check(c == 200 and "until end of turn" in " ".join(d.get("said") or []), "(set-up: the host gives the creature +3/+3 until end of turn)", (c, d))
        c, d = t.call("POST", "/api/brain/manual", {"seat": first, "op": "land-token", "name": "New Planet"}, key=k)
        check(c == 200, "(set-up: a New Planet land token made by hand)", (c, d))
        c, d = t.call("POST", "/api/brain/manual", {"seat": first, "op": "token", "name": "Clue", "power": 0, "toughness": 1}, key=k)
        check(c == 200, "(set-up: a token)", (c, d))
        c, d = t.call("POST", "/api/brain/tap", {"seat": first, "ref": f"#{land}"}, key=k)
        check(c == 200, "(set-up: a Mountain tapped by hand: a tap its player could take back)", (c, d))
        st = t.state(first)
        check(any(p["name"].startswith("New Planet") for p in st["permanents"]) and any(p["id"] == cre and p.get("pt") != "1/1" and p.get("pt") for p in st["permanents"]),
              "(set-up: game 1's board has the land token and the changed creature)", [(p["name"], p.get("pt")) for p in st["permanents"]])
        t.end_game(other)
        check(t.next_game(2), "game 2 of the match begins on the same table", t.match())
        ph2 = t.keep_all()
        for n in (C, M):
            st = t.state(n)
            names = [p["name"] for p in st.get("permanents") or []]
            check(not names, f"game 2: {n} starts with no permanents at all (no New Planet, no token, no creature with an until-end-of-turn change)", names)
        print("\n── 2. played-by and the land moves")
        p2 = ph2["player"]
        t.lands(p2, 2, "Mountain")
        ids = [p["id"] for p in t.state(p2)["permanents"] if p["name"] == "Mountain"]
        c, d = t.call("POST", "/api/seat/played-by", {"seat": p2, "kind": "autopilot"})
        check(c == 200 and d["played_by"].get("stand_in") is True, "the host hands the seat on turn to the autopilot (standing in)", (c, d))
        c, d = call(t, "POST", "/api/brain/tap", {"seat": p2, "ref": f"#{ids[0]}"}, key=t.keys[p2], driver="autopilot")
        check(c == 200 and not d.get("yield"), "the stand-in autopilot taps a land by hand: taken", (c, d))
        c, d = call(t, "POST", "/api/brain/untap", {"seat": p2, "ref": f"#{ids[0]}"}, key=t.keys[p2], driver="autopilot")
        check(c == 200 and not d.get("yield"), "…and takes that tap back in the same step: taken (it is the seat's own tap)", (c, d))
        check(who(t, p2)["kind"] == "autopilot", "the autopilot still plays the seat", who(t, p2))
        c, d = call(t, "POST", "/api/brain/tap", {"seat": p2, "ref": f"#{ids[1]}"}, key=t.keys[p2])
        check(c == 200 and who(t, p2)["kind"] == "person", "the person taps a land from their own page: that is a move, and the seat is theirs again", (c, d, who(t, p2)))
        c, d = call(t, "POST", "/api/brain/untap", {"seat": p2, "ref": f"#{ids[1]}"}, key=t.keys[p2], driver="autopilot")
        check(c == 409 and d.get("yield") is True, "the autopilot's next land move (taking back the person's tap) is refused: it yields", (c, d))
        st = t.state(p2)
        check(next(p for p in st["permanents"] if p["id"] == ids[1])["tapped"], "…and the person's tap stands", [(p["id"], p["tapped"]) for p in st["permanents"]])
        print("\n── 3. /watch keeps its delay with the new scripts")
        import re
        with urllib.request.urlopen(urllib.request.Request(t.b + "/watch", headers={"X-Forwarded-For": "203.0.113.9", "User-Agent": "integrate-test"}), timeout=30) as x:
            page = x.read().decode()
        srcs = sorted(set(re.findall(r'<script src="/assets/([^"]+)"', page)))
        check(srcs == ["art.js", "mana.js", "matchrecap.js", "playedby.js"],
              "/watch loads only the static/public helpers (no battleline.js, seat.js or any script that reads the live game)", srcs)
        check("/api/spectate" in page and "/api/phase" not in page and "/api/brain" not in page, "…and its own board reads only /api/spectate", "")
    finally:
        t.stop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        check(False, "the test ran to its end", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    sys.exit(1 if R.FAILS else 0)
