"""Stage 1 of docs/hud-plan.md: the server refuses what the honor system used to allow, for a REMOTE player.

    ~/.venvs/table/bin/python table/tests/stage1_guard_test.py          # every item
    ~/.venvs/table/bin/python table/tests/stage1_guard_test.py 1 3      # only items 1 and 3

Every request carries X-Forwarded-For (what the cloud Worker adds), so the server treats the caller as another device:
the seat guard, the LAN block and the pilot key rules all apply. Only calls named `host_*` come from the laptop itself
(no forwarding header) and carry the host's brain token. Each item starts its own server on port 8831 with its own
research dir; nothing else is touched, no model is loaded, no network is used.

Items: 1 begin only on your own untap step · 2 fair-reveal · 3 pilot action allowlist · 4 untap only in your untap step
(both paths) · 5 a pilot's pass reaches the server · 7 BACK / HOLD / WINDOWS authority. (6 is the browser test.)
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

import keepalive  # noqa: E402  (one connection per server: polling must not use up local ports)
keepalive.install()

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent
PORT = 8831
BASE = f"http://127.0.0.1:{PORT}"
PASS = FAIL = 0
FAILED: list[str] = []


def check(name, ok, detail=""):
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {str(detail)[:300]}"), flush=True)
    if not ok:
        FAILED.append(name)


class Server:
    """A table with two pilots (Claude, Fusion) and two people (Michael, Sam), cloud-room style, on its own port."""

    def __init__(self, order="Claude,Fusion,Michael,Sam", extra=()):
        self.order, self.extra = order, list(extra)
        self.tmp = Path(tempfile.mkdtemp(prefix="stage1-"))
        self.token_file = self.tmp / "token"
        self.keys: dict[str, str] = {}

    def __enter__(self):
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        env.update(TABLE_CLOUD="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template",
                   TABLE_RESEARCH_DIR=str(self.tmp / "research"))
        cmd = [sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external",
               "--token-file", str(self.token_file),
               "--ai", "Claude|Kaust, Eyes of the Glade|", "--ai-deck", str(REPO / "decks/kaust.json"),
               "--ai", "Fusion|Tuvasa the Sunlit|", "--ai-deck", str(REPO / "decks/tuvasa.json"),
               "--pilot", "Claude", "--pilot", "Fusion", "--human", "Michael|", "--human", "Sam|",
               "--order", self.order, "--priority-window", "0.2", "--fair-seed", "off", *self.extra]
        self.proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=open(self.tmp / "server.log", "w"), stderr=subprocess.STDOUT)
        for _ in range(180):
            if self.proc.poll() is not None:
                sys.exit(f"server exited: {self.tmp / 'server.log'}")
            try:
                urllib.request.urlopen(BASE + "/api/phase", timeout=2).read()
                return self
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        sys.exit("server did not start")

    def __exit__(self, *a):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    # ── calls ──
    def call(self, method, path, body=None, key=None, host=False, ua="phone"):
        """A REMOTE call unless host=True: then it is the laptop itself, with the brain token."""
        h = {"Content-Type": "application/json", "User-Agent": ua}
        if host:
            h["X-Brain-Token"] = self.token_file.read_text().strip()
        else:
            h["X-Forwarded-For"] = "203.0.113.9"
        if key:
            h["X-Seat-Key"] = key
        r = urllib.request.Request(BASE + path, method=method, headers=h, data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(r, timeout=30) as x:
                raw = x.read()
                return x.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else {})

    def claim(self, name):
        if name not in self.keys:
            c, d = self.call("POST", "/api/seat/claim", {"name": name}, ua="phone-" + name)
            assert c == 200 and d.get("key"), (name, c, d)
            self.keys[name] = d["key"]
        return self.keys[name]

    def claim_all(self):
        for n in ("Claude", "Fusion", "Michael", "Sam"):
            self.claim(n)

    def brain(self, seat, action, **body):
        """A pilot driving its own seat with its own key (the /hand page)."""
        return self.call("POST", "/api/brain/" + action, {"seat": seat, **body}, key=self.claim(seat))

    def host_brain(self, seat, action, **body):
        return self.call("POST", "/api/brain/" + action, {"seat": seat, **body}, host=True)

    def state(self, seat):
        c, d = self.call("GET", "/api/brain/state?seat=" + seat, key=self.claim(seat))
        assert c == 200, (c, d)
        return d

    def phase(self):
        return self.call("GET", "/api/phase")[1]

    def next(self, name, **extra):
        return self.call("POST", "/api/phase/next", {"by": name, "key": self.claim(name), **extra})

    def start_game(self):
        c, d = self.next("Claude")
        assert c == 200 and d.get("player"), (c, d)
        return d

    def pass_for(self, name):
        """The pass a seat makes in someone else's step: a pilot with its brain pass, a person with NEXT."""
        if name in ("Claude", "Fusion"):
            return self.brain(name, "pass")
        return self.next(name)

    def act_walk(self, seat, action, **body):
        """What /hand does: retry while the table waits on passes, and let the others pass in turn order."""
        for _ in range(60):
            c, d = self.brain(seat, action, **body)
            if c == 409 and d.get("waiting"):
                nx = (d.get("passes") or {}).get("next")
                if nx and nx != seat:
                    self.pass_for(nx)
                else:
                    time.sleep(0.25)
                continue
            return c, d
        return 0, {"error": "never got through"}

    def finish_turn(self, who):
        """The active player's turn is over: every step passed in order until the turn moves on."""
        start = who
        for _ in range(200):
            ph = self.phase()
            if ph["player"] != start:
                return ph
            nx = (ph.get("passes") or {}).get("next")
            if nx is None:
                c, d = (self.brain(start, "end", text="done") if start in ("Claude", "Fusion") else self.next(start))
                if c == 409:
                    time.sleep(0.25)
                continue
            self.pass_for(nx) if nx != start or start not in ("Claude", "Fusion") else None
            time.sleep(0.05)
        return self.phase()


# ───────────────────────────────── item 1 ─────────────────────────────────
def item1():
    print("item 1: begin only for the active seat, at its untap step, once a turn")
    with Server() as S:                                        # a misclick by the wrong seat
        S.claim_all()
        c, d = S.brain("Fusion", "begin")
        check("before the game starts a pilot's begin is refused (it would make itself first and skip the roll)",
              c in (403, 409) and S.phase()["player"] is None, (c, S.phase()["player"], str(d)[:160]))
        S.start_game()                                         # Claude (first in order) is active, at untap
        f0 = S.state("Fusion")
        c, d = S.brain("Fusion", "begin")
        f1 = S.state("Fusion")
        ph = S.phase()
        check("Fusion's begin during Claude's turn is refused", c in (403, 409), (c, str(d)[:200]))
        check("…and says whose turn it is", "Claude" in str(d.get("error", "")), d)
        check("…Fusion did not untap, draw or take the turn",
              f1["turn"] == f0["turn"] and len(f1["hand"]) == len(f0["hand"]) and f1["library"] == f0["library"] and ph["player"] == "Claude",
              (f0["turn"], f1["turn"], len(f0["hand"]), len(f1["hand"]), ph["player"]))
    with Server() as S:                                        # the right seat, twice
        S.claim_all()
        S.start_game()
        c0 = S.state("Claude")
        retries = []
        for _ in range(60):                                    # /hand retries begin while the table waits on passes
            c, d = S.brain("Claude", "begin")
            retries.append((c, bool(d.get("waiting"))))
            if c == 409 and d.get("waiting"):
                nx = (d.get("passes") or {}).get("next")
                S.pass_for(nx) if nx and nx != "Claude" else time.sleep(0.25)
                continue
            break
        c1 = S.state("Claude")
        check("the active seat's begin goes through (after the others pass upkeep and draw)", c == 200 and c1["turn"] == c0["turn"] + 1, (c, str(d)[:200]))
        check("…a retry while the table waited was NOT refused as illegal (409 + waiting, not an error)",
              all(w for code, w in retries[:-1]) and len(retries) > 1, retries)
        check("…and drew one card", len(c1["hand"]) == len(c0["hand"]) + 1, (len(c0["hand"]), len(c1["hand"])))
        lands = [h["name"] for h in c1["hand"] if h.get("land")]
        if lands:
            S.brain("Claude", "land", name=lands[0])
        c2 = S.state("Claude")
        check("(the hand had a land to play, so the land drop can be checked)", bool(lands) and c2["land_played"], c2["land_played"])
        c, d = S.brain("Claude", "begin")
        c3 = S.state("Claude")
        check("a second begin in the same turn is refused", c in (403, 409), (c, str(d)[:200]))
        check("…it did not draw again or reset the land drop",
              len(c3["hand"]) == len(c2["hand"]) and c3["land_played"] == c2["land_played"] and c3["turn"] == c2["turn"],
              (len(c2["hand"]), len(c3["hand"]), c2["land_played"], c3["land_played"]))


# ───────────────────────────────── item 2 ─────────────────────────────────
def item2():
    print("item 2: fair-reveal is the host's")
    with Server() as S:
        S.claim_all()
        c, d = S.brain("Claude", "fair-reveal")
        pub = S.call("GET", "/api/fair")[1]
        check("a pilot's fair-reveal is refused, naming the fairness proof", c == 403 and "fair" in str(d.get("error", "")).lower(), (c, d))
        check("…and GET /api/fair still shows no seeds or orders", not pub.get("revealed") and "records" not in pub, str(pub)[:200])
        c, d = S.host_brain("Claude", "fair-reveal")
        pub = S.call("GET", "/api/fair")[1]
        check("the host's brain token may still publish the proof", c == 200 and pub.get("revealed") and "records" in pub, (c, str(pub)[:120]))


SECTIONS = {"1": item1, "2": item2}


def main():
    want = sys.argv[1:] or sorted(SECTIONS)
    for k in want:
        SECTIONS[k]()
    print(f"\n{PASS} passed, {FAIL} failed" + (": " + "; ".join(FAILED) if FAILED else ""))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
