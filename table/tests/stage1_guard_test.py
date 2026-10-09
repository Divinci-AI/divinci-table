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
        check("a pilot's fair-reveal is refused, naming the fairness proof", c == 403 and "fairness proof" in str(d.get("error", "")), (c, d))
        check("…and GET /api/fair still shows no seeds or orders", not pub.get("revealed") and "records" not in pub, str(pub)[:200])
        c, d = S.host_brain("Claude", "fair-reveal")
        pub = S.call("GET", "/api/fair")[1]
        check("the host's brain token may still publish the proof", c == 200 and pub.get("revealed") and "records" in pub, (c, str(pub)[:120]))


# ───────────────────────────────── item 3 ─────────────────────────────────
# Everything /hand and the pilot mode of tablectl (pilot_ctl_test.py) use. The rest has no card behind it, or is the host's.
ALLOWED = ("say", "begin", "land", "cast", "turn-up", "tap", "untap", "attack", "damage", "block", "pass", "end", "life")
REFUSED = {"draw": {"n": 3}, "search": {"name": "Sol Ring"}, "peek": {"n": 2}, "topdeck": {"name": "Sol Ring"},
           "put": {"name": "Sol Ring"}, "token": {"name": "Treasure", "power": 9, "toughness": 9, "n": 3},
           "counter": {"ref": "#1", "n": 5}, "animate": {"ref": "#1", "power": 9, "toughness": 9},
           "bottom": {"name": "Sol Ring"}, "shuffle": {}, "mulligan": {}, "discard": {"name": "Sol Ring"}, "mill": {"n": 3},
           "role": {"ref": "#1", "kind": "Young Hero"}, "destroy": {"ref": "#1"}, "exile": {"ref": "#1"}, "bounce": {"ref": "#1"},
           "blink": {"ref": "#1"}, "graveyard-out": {"name": "x"}, "manifest": {"n": 2}, "no-such-action": {}}


def zones(st):
    return ([h["name"] for h in st["hand"]], st["library"], len(st["permanents"]), len(st["graveyard"]),
            sorted((p["id"], p.get("counters", 0), p.get("pt")) for p in st["permanents"]))


def item3():
    print("item 3: a pilot key may only do what /hand and tablectl's pilot mode use")
    with Server() as S:
        S.claim_all()
        S.start_game()
        lands = [h["name"] for h in S.state("Claude")["hand"] if h.get("land")]
        if lands:
            S.brain("Claude", "land", name=lands[0])           # so there is a permanent #id for the ref actions
        before = zones(S.state("Claude"))
        bad = {}
        for act, body in REFUSED.items():
            c, d = S.brain("Claude", act, **body)
            if not (c == 403 and "pilot" in str(d.get("error", "")).lower()):
                bad[act] = (c, str(d)[:90])
        check("every action outside the allowlist is refused with 403 and says it is not for a pilot", not bad, bad)
        check("…and none of them moved a card (hand, library, battlefield, graveyard, counters)", zones(S.state("Claude")) == before,
              (before, zones(S.state("Claude"))))
        c, d = S.call("POST", "/api/brain/anything/draw", {"seat": "Claude", "n": 2}, key=S.claim("Claude"))
        check("an alias path ending in draw is covered too", c == 403 and zones(S.state("Claude")) == before, (c, str(d)[:100]))
        c, d = S.brain("Claude", "say", text="Hello table.")
        check("allowed: say", c == 200, (c, d))
        c, d = S.brain("Claude", "life", player="me", delta=-1)
        check("allowed: my own life", c == 200, (c, d))
        st = S.state("Claude")
        land = next((p for p in st["permanents"] if "Land" in (p.get("type") or "")), None)
        if land:
            c, d = S.brain("Claude", "tap", ref="#%d" % land["id"], announce=True)
            check("allowed: tap a permanent", c == 200, (c, d))
        c, d = S.brain("Claude", "pass")
        check("allowed: pass is not blocked by the allowlist (its own rules are item 5)", c != 403 or "pilot" not in str(d.get("error", "")).lower(), (c, d))
        # a discount the client names is ignored for a pilot key
        S.host_brain("Claude", "search", name="Sol Ring", to="hand")
        c0, d0 = S.brain("Claude", "cast", name="Sol Ring")
        c1, d1 = S.brain("Claude", "cast", name="Sol Ring", discount=5)
        st = S.state("Claude")
        check("casting Sol Ring with no mana is refused (baseline)", c0 == 400 and "can't pay" in str(d0.get("error", "")), (c0, d0))
        check("…and a client-supplied discount does not make it castable", c1 == 400 and "can't pay" in str(d1.get("error", ""))
              and not any(p["name"] == "Sol Ring" for p in st["permanents"]), (c1, str(d1)[:120]))
        # the host brain token keeps everything
        n0 = len(S.state("Claude")["hand"])
        c, d = S.host_brain("Claude", "draw", n=2)
        c2, d2 = S.host_brain("Claude", "token", name="Treasure", power=0, toughness=0)
        check("the host's brain token still draws and makes tokens", c == 200 and len(S.state("Claude")["hand"]) == n0 + 2 and c2 == 200, (c, c2, str(d2)[:100]))
        c, d = S.host_brain("Claude", "cast", name="Sol Ring", discount=5)
        check("…and still honours a discount (Jukai Naturalist and friends)", c == 200, (c, str(d)[:120]))


# ───────────────────────────────── item 4 ─────────────────────────────────
def perm_of(S, seat, name):
    return next((p for p in S.state(seat)["permanents"] if p["name"] == name), None)


def item4():
    print("item 4: untap by hand only in your own untap step, on both paths")
    with Server() as S:                                        # Claude (a pilot) goes first
        S.claim_all()
        S.start_game()
        lands = [h["name"] for h in S.state("Claude")["hand"] if h.get("land")]
        flands = [h["name"] for h in S.state("Fusion")["hand"] if h.get("land")]
        check("(both pilots hold a land to work with)", bool(lands) and bool(flands), (lands, flands))
        S.brain("Claude", "land", name=lands[0])
        S.brain("Fusion", "land", name=flands[0])
        cl, fl = perm_of(S, "Claude", lands[0]), perm_of(S, "Fusion", flands[0])
        c, d = S.brain("Claude", "tap", ref="#%d" % cl["id"])
        check("tap is legal (Claude taps its land at its own untap step)", c == 200 and perm_of(S, "Claude", lands[0])["tapped"], (c, d))
        c, d = S.brain("Claude", "untap", ref="#%d" % cl["id"])
        check("the active pilot may untap in its own untap step", c == 200 and not perm_of(S, "Claude", lands[0])["tapped"], (c, str(d)[:140]))
        S.brain("Fusion", "tap", ref="#%d" % fl["id"])
        c, d = S.brain("Fusion", "untap", ref="#%d" % fl["id"])
        check("a pilot who is NOT the active player cannot untap", c in (403, 409) and perm_of(S, "Fusion", flands[0])["tapped"], (c, str(d)[:140]))
        check("…and the refusal says why", "untap" in str(d.get("error", "")) and "Claude" in str(d.get("error", "")), d)
        c, d = S.host_brain("Fusion", "untap", ref="#%d" % fl["id"])
        check("the host's brain token keeps untap (effects the engine does not model)", c == 200 and not perm_of(S, "Fusion", flands[0])["tapped"], (c, str(d)[:140]))
        S.brain("Claude", "tap", ref="#%d" % cl["id"])
        c, d = S.act_walk("Claude", "begin")
        check("begin still untaps everything at the start of the turn", c == 200 and not perm_of(S, "Claude", lands[0])["tapped"], (c, str(d)[:120]))
        S.brain("Claude", "tap", ref="#%d" % cl["id"])
        c, d = S.brain("Claude", "untap", ref="#%d" % cl["id"])
        check("after begin (main 1) a manual untap is refused, the land stays tapped", c in (403, 409) and perm_of(S, "Claude", lands[0])["tapped"], (c, str(d)[:140]))
        check("…naming the step", "untap step" in str(d.get("error", "")), d)
    with Server(order="Michael,Claude,Fusion,Sam") as S:       # a person with a real deck goes first
        S.claim_all()
        k = S.claim("Michael")

        def board(perms):
            return S.call("POST", "/api/my-board", {"by": "Michael", "key": k, "permanents": perms})

        def act(action, i=0, name="Forest"):
            return S.call("POST", "/api/card-action", {"seat": "Michael", "key": k, "index": i, "name": name, "action": action}, key=k)

        def tapped(i=0):
            b3 = S.call("GET", "/api/board3d")[1]
            m = next(x for x in b3["seats"] if x["name"] == "Michael")
            return m["permanents"][i].get("tapped")
        board([{"name": "Forest", "tapped": True}, {"name": "Island"}])
        c, d = act("untap")
        check("before the game starts a card-action untap is refused", c in (403, 409) and tapped(), (c, d))
        S.start_game()                                         # Michael is active, at his untap step
        c, d = act("untap")
        check("the active person may untap in their own untap step (card-action)", c == 200 and not tapped(), (c, d))
        c, d = act("tap")
        check("tap is legal at any time", c == 200 and tapped(), (c, d))
        c, d = S.next("Michael")                               # his untap step is passed: now upkeep
        c, d = act("untap")
        check("after the untap step a card-action untap is refused, the card stays tapped", c in (403, 409) and tapped(), (c, d))
        check("…and says why", "untap step" in str(d.get("error", "")), d)
        board([{"name": "Forest", "tapped": True}])
        k2 = S.claim("Sam")
        c, d = S.call("POST", "/api/card-action", {"seat": "Michael", "key": k2, "index": 0, "name": "Forest", "action": "untap"}, key=k2)
        check("(Sam's key cannot act on Michael's cards at all)", c == 403, (c, d))


# ───────────────────────────────── item 5 ─────────────────────────────────
def item5():
    print("item 5: a pilot's pass reaches the server, counts once, in order, for the right step")
    with Server() as S:                                        # order Claude, Fusion, Michael, Sam
        S.claim_all()
        c, d = S.brain("Fusion", "pass")
        check("before the game starts a pilot's pass is refused", c in (403, 409), (c, str(d)[:120]))
        S.start_game()
        c, d = S.brain("Claude", "begin")                      # Claude's turn walks to upkeep and waits for the others
        check("(Claude's begin waits at upkeep for the others to pass)", c == 409 and d.get("waiting") and d["step"] == "upkeep", (c, str(d)[:160]))
        c, d = S.brain("Claude", "pass")
        check("the active pilot cannot pass in its own turn", c in (403, 409) and "own turn" in str(d.get("error", "")), (c, str(d)[:140]))
        c, d = S.brain("Fusion", "pass", player="Claude", step="main 1")
        ps = S.phase()["passes"]
        check("a STALE pass (for a step the table is not at) is refused", c in (403, 409) and "stale" in str(d.get("error", "")), (c, str(d)[:140]))
        check("…and counted for nobody", "Fusion" not in ps["passed"] and ps["next"] == "Fusion", ps)
        c, d = S.brain("Fusion", "pass", player="Claude", step="upkeep")
        ps = S.phase()["passes"]
        ev = S.call("GET", "/api/events?since=0")[1]["events"]
        check("Fusion's pass for the current step reaches the server", c == 200, (c, str(d)[:140]))
        check("…the round moved on: Fusion passed, Michael is next", ps["passed"] == ["Fusion"] and ps["next"] == "Michael", ps)
        check("…and the table log shows the pass", any(e.get("type") == "pass" and e.get("by") == "Fusion" for e in ev), ev[-3:])
        c, d = S.brain("Fusion", "pass", player="Claude", step="upkeep")
        ps = S.phase()["passes"]
        check("a DUPLICATE pass is refused", c in (403, 409) and "already passed" in str(d.get("error", "")), (c, str(d)[:140]))
        check("…and does not count twice or skip Michael", ps["passed"] == ["Fusion"] and ps["next"] == "Michael", ps)
        S.next("Michael")
        S.next("Sam")
        time.sleep(0.4)                                        # the window
        c, d = S.brain("Claude", "begin")
        check("once everyone has passed, the step moves on (Claude's begin now waits at draw)", c == 409 and d.get("step") == "draw", (c, d.get("step"), str(d)[:120]))
    with Server(order="Claude,Michael,Fusion,Sam") as S:       # Michael must pass before Fusion
        S.claim_all()
        S.start_game()
        S.brain("Claude", "begin")
        c, d = S.brain("Fusion", "pass", player="Claude", step="upkeep")
        ps = S.phase()["passes"]
        check("a pilot cannot pass before the seat ahead of it (turn order)", c in (403, 409) and "Michael passes first" in str(d.get("error", "")), (c, str(d)[:140]))
        check("…and the round is unchanged", ps["passed"] == [] and ps["next"] == "Michael", ps)
        S.next("Michael")
        c, d = S.brain("Fusion", "pass", player="Claude", step="upkeep")
        check("after Michael it is Fusion's turn to pass, and the pass counts", c == 200 and S.phase()["passes"]["next"] == "Sam", (c, str(d)[:120], S.phase()["passes"]))
        c, d = S.host_brain("Fusion", "pass")
        check("(the host brain's pass is not held to the pilot rules)", c == 200, (c, str(d)[:100]))


SECTIONS = {"1": item1, "2": item2, "3": item3, "4": item4, "5": item5}


def main():
    want = sys.argv[1:] or sorted(SECTIONS)
    for k in want:
        SECTIONS[k]()
    print(f"\n{PASS} passed, {FAIL} failed" + (": " + "; ".join(FAILED) if FAILED else ""))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
