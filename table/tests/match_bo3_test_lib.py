"""The table the match tests share (match_bo3_test.py, match_bots_test.py): the retro harness's table with 40-card decks, its own
environment, and the few calls a match needs. Not a test itself (run_core.py runs only *_test.py)."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
import cards_path  # noqa: E402
import retro_game4_test as R  # noqa: E402

check, C, M = R.check, "Claude", "Michael"
RAW = {c["name"]: c for c in json.load(open(cards_path.cards_json())) if c.get("set") == "trk"}


def deck40(name: str, life: int = 20) -> str:
    f = R.TMP / f"{name}-40-{life}.json"
    f.write_text(json.dumps({"name": name, "commander": None, "startingLife": life,
                             "mainBoard": [R.shim.entry(dict(RAW["Mountain"]), 17), R.shim.entry(dict(RAW["Starfleet Security"]), 23)]}))
    return str(f)


class Table(R.Table):
    """The harness table with 40-card decks and its own environment. roll=True: the real opening (the table's roll, then the mulligan step), as the
    draft flow runs it; roll=False: the harness default (TABLE_HIGHROLL=first: the first seat starts, no mulligan step)."""
    def __init__(self, extra=(), roll=False, pilots=(C, M), life=20, env=None):
        orig, dc, dm = R.subprocess.Popen, R.DECK_CLAUDE, R.DECK_MICHAEL

        def popen(cmd, **kw):
            for n in (C, M):
                if n not in pilots:
                    i = next(i for i in range(len(cmd) - 1) if cmd[i] == "--pilot" and cmd[i + 1] == n)
                    cmd = cmd[:i] + cmd[i + 2:]
            e = dict(kw.get("env") or os.environ)
            if roll:
                e.pop("TABLE_HIGHROLL", None)
                e.update(TABLE_MULLIGAN="on", TABLE_MULLIGAN_SECS="0")
            e.update(env or {})
            return orig(cmd, **{**kw, "env": e})
        lives = life if isinstance(life, dict) else {C: life, M: life}          # (a seat can be given more life: a lopsided match)
        R.subprocess.Popen, R.DECK_CLAUDE, R.DECK_MICHAEL = popen, deck40(C, lives[C]), deck40(M, lives[M])
        try:
            super().__init__(extra=extra)
        finally:
            R.subprocess.Popen, R.DECK_CLAUDE, R.DECK_MICHAEL = orig, dc, dm
        self.pilots, self.roll = pilots, roll

    def claim(self):
        self.keys = {n: self.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in self.pilots}
        self.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)

    def phase(self):
        return self.call("GET", "/api/phase", host=False)[1]

    def match(self):
        return self.call("GET", "/api/match", host=False)[1]

    def until(self, pred, secs=30, what="the table"):
        end = time.time() + secs
        while time.time() < end:
            try:
                if pred():
                    return True
            except Exception:  # noqa: BLE001 — not there yet
                pass
            time.sleep(0.15)
        return False

    def keep_all(self):
        """Everyone keeps their opening seven (the mulligan step), then the first turn is under way and begun."""
        if self.roll:
            self.until(lambda: (self.phase().get("mulligan") or {}).get("on") or self.phase().get("player"), what="the opening hands")
            for n in self.pilots:
                if n in ((self.phase().get("mulligan") or {}).get("waiting") or []):
                    self.call("POST", "/api/brain/keep", {"seat": n}, key=self.keys[n])
        ok = self.until(lambda: self.phase().get("player") is not None, what="the first turn")
        ph = self.phase()
        if ok and ph["player"] in self.keys and not ph.get("begun"):
            self.call("POST", "/api/brain/begin", {"seat": ph["player"]}, key=self.keys[ph["player"]])
            self.until(lambda: self.phase().get("begun") and self.phase().get("step") == "main 1")
        return self.phase()

    def open_game(self, by=C):
        self.claim()
        self.call("POST", "/api/phase/next", {"by": by, "key": self.keys[by]}, key=self.keys[by])
        return self.keep_all()

    def end_game(self, loser, how="life"):
        """A seat's life goes to 0 or less (as the last point of damage would do it) and the table itself ends the game."""
        winner = M if loser == C else C
        c, d = self.call("POST", "/api/brain/life", {"seat": winner, "player": loser, "delta": -60, "text": "The last blow."})
        assert c == 200, d
        assert self.until(lambda: self.call("GET", "/api/game/summary", host=False)[1].get("over")), "the game never ended"

    def next_game(self, n, secs=30):
        """Until game `n` of the match has begun on this table (the first turn handed out, or its opening hands open)."""
        return self.until(lambda: ((self.match().get("current") or {}).get("n") == n) and not self.match().get("game_over"), secs)

    def events(self, since=0):
        """Every public event after `since` (the route hands out 300 at a time: a match's log is longer than that)."""
        out = []
        while True:
            page = self.call("GET", f"/api/events?since={since}", host=False)[1].get("events", [])
            out += page
            if len(page) < 300:
                return out
            since = page[-1]["id"]

    def says(self, since=0, speaker="Table"):
        return [e.get("text") or "" for e in self.events(since) if e.get("type") == "say" and e.get("speaker") == speaker]

    def last_id(self):
        return self.call("GET", "/api/events?since=latest", host=False)[1].get("last") or 0

    def zones(self, seat):
        """Everything the table holds for a seat, public and (with the host's eyes) private: used to prove a game starts clean."""
        st = self.call("GET", f"/api/brain/state?seat={seat}")[1]
        b3 = next(s for s in self.call("GET", "/api/board3d", host=False)[1]["seats"] if s["name"] == seat)
        return {"hand": len(st.get("hand") or []), "library": b3.get("library"), "life": b3.get("life"), "permanents": len(st.get("permanents") or []),
                "graveyard": b3.get("graveyard_count"), "exile": len(b3.get("exile") or []), "impulse": len(b3.get("playable_from_exile") or []),
                "pending": len(st.get("pending_choices") or []), "turn": st.get("turn"), "names": sorted(h["name"] for h in st.get("hand") or [])}
