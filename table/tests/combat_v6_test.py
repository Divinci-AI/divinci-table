"""Combat in the rules' order, and the game's end (game 5 retro: M1, M5, M6, M16, M17, M18, M19, M22, M23, M24 and the pass rule, M2).

Declare attackers once (locked) -> the attack's triggers are answered -> the defender declares every block -> a window in which both players
may act -> ONE combat damage step for all attackers -> only then is the game checked for its end. Every check goes through the route a
player uses (the seat's own key), on tables of their own on free ports (never a live game's), seats TestA and TestB. Set-up (a card into a
hand, a permanent onto the battlefield, a life total) uses the host token. Response windows are OFF on these tables, as they were on the night.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 HF_HUB_OFFLINE=1 SKIP_OLLAMA=1 python3 table/tests/combat_v6_test.py
    COMBAT_ONLY="simultaneous|restore"   only the sections whose name contains one of these
    COMBAT_BOT=<dir with claude_autopilot.py>   the bot for the bot-v-bot game (default: ../bot-v6d next to the table)
"""
from __future__ import annotations

import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import retro_game4_test as R  # noqa: E402

check = R.check
A, B = "TestA", "TestB"
LIVE_PORTS = (8800, 8801, 8820, 8830)
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in LIVE_PORTS)   # never a live game's port
R.POOL.update({n: 6 for n in ("Hologram Janeway", "La'An Noonien-Singh, Security", "Saavik, Stoic Student", "Wesley Crusher, Starfleet Cadet",
                              "Command Subadjunct", "Pet Sehlat", "Silicate Surveyor", "Consider the Prime Directive")})
DECK = R.deck_file("combat-v6")
WESLEY, SAAVIK, JIM = "Wesley Crusher, Starfleet Cadet", "Saavik, Stoic Student", "He's Dead, Jim"


class Table(R.Table):
    """A table of its own with seats TestA and TestB (R.Table's are Claude and Michael). TestA takes the first turn."""

    def __init__(self, extra=(), env_extra=None, deck_a=None, deck_b=None, code=None):
        self.port = R.free_port()
        self.b = f"http://127.0.0.1:{self.port}"
        self.dir = Path(tempfile.mkdtemp(prefix="t-", dir=R.TMP))
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        env.update(TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", NO_GEMMA="1",
                   TABLE_NO_VOICE="1", SKIP_OLLAMA="1", PYTHONDONTWRITEBYTECODE="1", TABLE_RESEARCH_DIR=str(self.dir / "r"), TABLE_SAVES_DIR=str(self.dir / "s"))
        env.update(env_extra or {})
        seed = [] if "--seed" in extra else ["--seed", os.environ.get("RETRO_SEED", "4")]
        cmd = [sys.executable, str(code or HERE / "server.py"), "--any-card", "--port", str(self.port), "--brain", "external", "--fair-seed", "off",
               "--token-file", str(self.dir / "token"), *seed, "--ai", f"{A}|", "--ai-deck", deck_a or DECK, "--ai", f"{B}|", "--ai-deck", deck_b or DECK,
               "--pilot", A, "--pilot", B, "--seat-rate", "0", "--order", f"{A},{B}", *extra]
        self.proc = subprocess.Popen(cmd, cwd=HERE.parent, env=env, stdout=open(self.dir / "log", "w"), stderr=subprocess.STDOUT)
        self.keys = {}
        for _ in range(120):
            try:
                self.token = (self.dir / "token").read_text().strip()
                urllib.request.urlopen(self.b + "/api/phase", timeout=2)
                break
            except Exception:  # noqa: BLE001 — not up yet
                time.sleep(0.5)

    def claim(self):
        self.keys = {n: self.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in (A, B)}

    def start(self, windows=False, empty_hands=True):
        self.claim()
        self.call("POST", "/api/phase/windows", {"on": windows, "by": "host"}, host=False)
        self.call("POST", "/api/phase/next", {"by": A, "key": self.keys[A]}, key=self.keys[A])
        self.wait(lambda p: p.get("player") == A and p.get("begun") and p.get("step") == "main 1")
        if empty_hands:
            self.empty_hands()

    def empty_hands(self):
        """Nothing in either hand that a test did not put there: whether a seat could respond is then the test's own doing."""
        for n in (A, B):
            for h in list(self.state(n).get("hand") or []):
                self.call("POST", "/api/brain/discard", {"seat": n, "name": h["name"], "quiet": True})

    def fresh(self, who):
        """A new turn of `who`, at main 1, with only what the test gave them in hand (the turn's draw goes)."""
        self.turn(who)
        self.empty_hands()

    def phase(self):
        return self.call("GET", "/api/phase")[1]

    def raw(self, seat, action, **body):
        """One request through the seat's own key, no retry and nobody passed for: exactly what the table answers."""
        return self.call("POST", f"/api/brain/{action}", {"seat": seat, **body}, key=self.keys[seat])

    def host(self, seat, action, **body):
        return self.call("POST", f"/api/brain/{action}", {"seat": seat, **body})

    def since(self, ev0, *types):
        return [e for e in self.events(ev0) if e["id"] > ev0 and (not types or e["type"] in types)]

    def last(self):
        ev = self.events()
        return ev[-1]["id"] if ev else 0

    def combat(self, seat=None):
        if seat:
            return self.state(seat).get("combat") or {}
        return self.phase().get("combat") or {}

    def stage(self, attacker=A):
        return next((a["stage"] for a in self.combat().get("attacks", []) if a["attacker"] == attacker), None)

    def gy(self, seat):
        return self.state(seat).get("graveyard") or []


def lost(t, ev0, who):
    return -sum(e.get("delta", 0) for e in t.since(ev0, "life") if e.get("player") == who and e.get("delta", 0) < 0)


# ── M1: one damage step, after all the blocks ─────────────────────────────────────────────────────────────────────────────────────────
def simultaneous():
    print("M1: two attackers, one blocked: nothing is dealt until every block is in, then all of it in the damage step")
    t = Table()
    try:
        t.start()
        xs = t.onto(A, "Xindi Surveyors")               # 3/2
        ss = t.onto(A, "Starfleet Security")            # 1/1 haste
        tso = t.onto(B, "Tal Shiar Operative")          # 2/2
        t.fresh(B)
        t.fresh(A)
        ev0 = t.last()
        c, d = t.act(A, "attack", assign={f"#{xs}": B, f"#{ss}": B})
        check(c == 200 and t.phase()["step"] == "declare attackers" and t.stage() == "blocks", "TestA attacks with Xindi Surveyors (3) and Starfleet Security (1)", (c, d, t.stage()))
        att = [e for e in t.since(ev0, "attention") if e.get("kind") == "attacked"]
        check(len(att) == 1 and att[0].get("amount") == 4 and len(att[0].get("attackers") or []) == 2,
              "M6: ONE prompt for the defender, listing both attackers (4 if nothing blocks)", att)
        inc = t.state(B).get("incoming") or []
        check(sorted(i["ref"] for i in inc) == sorted([f"#{xs}", f"#{ss}"]) and all(i["can_block"] == [tso] for i in inc),
              "…the seat's own view lists both, each with what may block it", inc)
        c, d = t.raw(B, "block", attacker=f"#{xs}", blocker=f"#{tso}")
        mid = t.since(ev0, "life")
        check(c == 200 and t.lifeof(B) == 20 and not mid and t.phase()["step"] == "declare attackers" and t.perm(A, "Xindi Surveyors") and t.perm(B, "Tal Shiar Operative")
              and t.perm(B, "Tal Shiar Operative")["damage"] == 0,
              "the first answer (a block on Xindi Surveyors) deals NOTHING: no life event, no creature hurt, the step still reads declare attackers", (c, d, mid, t.phase()["step"]))
        pub = t.combat()["attacks"][0]
        check(pub["stage"] == "blocks" and [r["answered"] for r in pub["attackers"]] == [True, False] and pub["attackers"][0]["blockers"][0]["id"] == tso,
              "…the public combat shows the block, and that Starfleet Security is still unanswered", pub)
        c, d = t.raw(A, "damage")
        check(c == 409 and "to block" in str(d.get("error")) and t.lifeof(B) == 20, "the attacker can't deal damage while a block is still owed", (c, str(d.get("error"))))
        c, d = t.raw(B, "block", attacker=f"#{ss}")
        evs = t.since(ev0)
        dmg_at = next((e["id"] for e in evs if e["type"] == "phase" and e.get("step") == "combat damage"), None)
        blk_at = next((e["id"] for e in evs if e["type"] == "phase" and e.get("step") == "declare blockers"), None)
        lifes = [e for e in evs if e["type"] == "life"]
        check(c == 200 and blk_at and dmg_at and blk_at < dmg_at and lifes and all(e["id"] > dmg_at for e in lifes),
              "the last answer: declare blockers, then combat damage, and every life event comes after the damage step began", (c, d, blk_at, dmg_at, lifes))
        check(t.lifeof(B) == 19 and t.lifeof(A) == 20, "TestB took exactly 1 (the unblocked Starfleet Security): 20 -> 19", (t.lifeof(A), t.lifeof(B)))
        check(t.perm(A, "Xindi Surveyors") is None and t.perm(B, "Tal Shiar Operative") is None and t.perm(A, "Starfleet Security") is not None
              and "Xindi Surveyors" in t.gy(A) and "Tal Shiar Operative" in t.gy(B),
              "the blocked pair traded in the same step (3 into a 2/2, 2 into a 3/2): both in their graveyards", (t.gy(A), t.gy(B)))
        check(any("Combat: TestB took 1" in str(e.get("text")) for e in evs if e["type"] == "say") and "announced at 4" in " ".join(str(e.get("text")) for e in evs),
              "the account line: took 1, against the 4 announced", [e.get("text") for e in evs if e["type"] == "say"][-4:])
        check(any(e["type"] == "pass" and e.get("by") == B and e.get("step") == "declare blockers" for e in evs),
              "the window after blocks was trivial for TestB (nothing to cast): passed for, as the rule allows", [e for e in evs if e["type"] == "pass"])
        c, d = t.raw(A, "damage")
        check(c == 200 and t.lifeof(B) == 19, "a later `damage` from the attacker changes nothing: the step was dealt once", (c, d, t.lifeof(B)))

        print("M1/M6: two unblocked attackers, one answer for both")
        t.fresh(B)
        a1, a2 = t.onto(A, "Xindi Surveyors"), t.onto(A, "Pet Sehlat")     # 3 and 4
        t.fresh(A)
        t.fresh(B)
        t.fresh(A)
        ev1 = t.last()
        c, d = t.act(A, "attack", assign={f"#{a1}": B, f"#{a2}": B})
        c2, d2 = t.raw(B, "block", none=True)
        evs = t.since(ev1)
        dmg_at = next((e["id"] for e in evs if e["type"] == "phase" and e.get("step") == "combat damage"), 0)
        lifes = [e for e in evs if e["type"] == "life" and e.get("player") == B]
        between = [e for e in evs if lifes and lifes[0]["id"] < e["id"] < lifes[-1]["id"] and e["type"] in ("phase", "pass", "attention", "game-over")]
        check(c == 200 and c2 == 200 and len(lifes) == 2 and all(e["id"] > dmg_at > 0 for e in lifes) and not between and lifes[-1]["ts"] - lifes[0]["ts"] < 0.2,
              "`block` with none answers both: two life events, both inside the damage step, nothing between them", (c, d, c2, d2, lifes, between))
        check(t.lifeof(B) == 12 and sorted(e["delta"] for e in lifes) == [-4, -3], "TestB: 19 -> 12 (3 + 4)", (t.lifeof(B), lifes))

        print("M6: every attacker in one `blocks` answer")
        t.fresh(B)
        b1, b2 = t.onto(B, "Mugato"), t.onto(B, "Tal Shiar Operative")     # 6/6 reach deathtouch; 2/2
        t.fresh(A)
        ev2 = t.last()
        c, d = t.act(A, "attack", assign={f"#{a1}": B, f"#{a2}": B, f"#{ss}": B})
        cx, dx = t.raw(B, "block", blocks=[f"#{b1}"])
        check(cx == 400 and "blocks" in str(dx.get("error")) and t.stage() == "blocks", "a `blocks` of the wrong shape is refused, never read as 'no block'", (cx, dx))
        cx, dx = t.raw(B, "block", blocks={f"#{a1}": [f"#{b1}"], f"#{a2}": [f"#{b1}"]})
        check(cx == 400 and "only one attacker" in str(dx.get("error")) and t.stage() == "blocks" and t.lifeof(B) == 12, "one creature named for two attackers is refused, and nothing is recorded", (cx, dx))
        c2, d2 = t.raw(B, "block", blocks={f"#{a1}": [f"#{b1}"], f"#{a2}": [f"#{b2}"]})
        check(c == 200 and c2 == 200 and t.lifeof(B) == 11 and lost(t, ev2, B) == 1, "Mugato on Xindi Surveyors, the Operative on Pet Sehlat, nothing on Starfleet Security: TestB takes 1 (12 -> 11)", (c, d, c2, d2, t.lifeof(B)))
        check(t.perm(A, "Xindi Surveyors") is None and t.perm(B, "Tal Shiar Operative") is None and t.perm(A, "Pet Sehlat") is not None and t.perm(B, "Mugato") is not None
              and t.perm(A, "Pet Sehlat")["damage"] == 2 and t.perm(B, "Mugato")["damage"] == 3,
              "each fight on its own, all in one step: Xindi dies to Mugato (which carries 3), the Operative dies to Pet Sehlat (which carries 2)", (t.perm(A, "Pet Sehlat"), t.perm(B, "Mugato")))
        c, d = t.raw(B, "block", none=True)
        check(c == 400 and "nothing is attacking" in str(d.get("error")), "a second answer to a combat already dealt is refused", (c, d))
    finally:
        t.stop()


# ── the window after blocks: a flash creature, then damage ────────────────────────────────────────────────────────────────────────────
def window_bounce():
    print("M1/M2/M16: after blocks, a flash creature (Wesley Crusher); the attacker it sends home deals nothing")
    t = Table(env_extra={"TABLE_AI_PASS_SECS": "1.5"})
    try:
        t.start()
        xs = t.onto(A, "Xindi Surveyors")               # 3/2
        cs = t.onto(A, "Command Subadjunct")            # 2/1, mana value 1
        t.fresh(B)
        t.lands(B, 4, "Island")
        t.fresh(A)
        t.hand(B, WESLEY)
        ev0 = t.last()
        c, d = t.act(A, "attack", assign={f"#{xs}": B, f"#{cs}": B})
        c2, d2 = t.raw(B, "block", none=True)
        ph = t.phase()
        check(c == 200 and c2 == 200 and ph["step"] == "declare blockers" and t.lifeof(B) == 20 and t.stage() == "window" and (ph.get("passes") or {}).get("next") == B,
              "blocks are in (none): the step is declare blockers, nothing is dealt, and the table waits for TestB, who holds a flash card it can pay for",
              (c, d, c2, d2, ph["step"], t.stage(), ph.get("passes")))
        you = t.combat(B).get("you") or {}
        check(you.get("window") is True and not you.get("blocks_due"), "TestB's page is told: the window after blocks is yours", you)
        time.sleep(3.2)                                  # twice the pass timer (1.5 s), windows OFF
        ph = t.phase()
        passed = [e for e in t.since(ev0, "pass") if e.get("by") == B and e.get("step") == "declare blockers"]
        check(ph["step"] == "declare blockers" and t.lifeof(B) == 20 and not passed,
              "M2: windows off and the pass timer run out twice: TestB is NOT passed for (a real decision), nothing dealt", (ph["step"], passed))
        c, d = t.raw(A, "damage")
        check(c == 409 and "waiting on passes: TestB" in str(d.get("error")) and t.lifeof(B) == 20, "the attacker's Damage is its pass: it waits for TestB", (c, d))
        lg = [a["action"] for a in t.legal(B)["actions"]]
        check("cast" in lg and "pass" in lg, "TestB's legal list: cast, or pass", lg)
        c, d = t.raw(B, "cast", name=WESLEY)
        check(c == 200 and t.perm(B, WESLEY) is not None and t.phase()["step"] == "declare blockers" and t.lifeof(B) == 20,
              "TestB casts Wesley Crusher (flash) after blocks, before damage", (c, d))
        # Wesley's "choose two": where the table carries the modes out itself it asks TestB (answered here with the first option each time,
        # which sends the only creature of mana value 2 or less home); where it is still by hand, the host applies that mode.
        for _ in range(8):
            if not any(t.state(n).get("pending_choices") for n in (A, B)):
                break
            t.settle_setup()
        c, d = (200, {}) if t.perm(A, "Command Subadjunct") is None else t.host(A, "bounce", ref=f"#{cs}", quiet=True)
        check(c == 200 and t.perm(A, "Command Subadjunct") is None and t.lifeof(B) == 20, "…and Command Subadjunct goes back to its owner's hand (Wesley's mode: return a creature of mana value 2 or less)", (c, d))
        time.sleep(0.8)
        check(t.phase()["step"] == "declare blockers" and t.lifeof(B) == 20, "still no damage: TestB responded, so the round starts again and TestB has not passed", t.phase()["step"])
        c, d = t.raw(B, "pass", player=A, step="declare blockers")
        evs = t.since(ev0)
        check(c == 200 and t.lifeof(B) == 17 and lost(t, ev0, B) == 3, "TestB passes: the damage step. Only Xindi Surveyors hits: 20 -> 17 (the bounced attacker deals nothing)", (c, d, t.lifeof(B)))
        said = " ".join(str(e.get("text")) for e in evs if e["type"] == "say")
        check("Command Subadjunct has left the battlefield" in said and "Combat: TestB took 3." in said, "the table says the attacker left, and the account is 3 of the 3 still announced", said[-400:])
        check(t.phase()["step"] == "combat damage" and t.stage() == "done", "the turn is at combat damage, the combat done", (t.phase()["step"], t.stage()))
    finally:
        t.stop()


def window_saavik():
    print("M1: the attacker flashes Saavik in after blocks: she kills the blocker, which then deals and takes nothing")
    t = Table()
    try:
        t.start()
        xs = t.onto(A, "Xindi Surveyors")               # 3/2, Xindi Scientist
        tso = t.onto(B, "Tal Shiar Operative")          # 2/2
        t.lands(A, 5, "Plains")
        t.fresh(B)
        t.fresh(A)
        t.hand(A, SAAVIK)
        ev0 = t.last()
        c, d = t.act(A, "attack", assign={f"#{xs}": B})
        c2, d2 = t.raw(B, "block", blocks={f"#{xs}": [f"#{tso}"]})
        check(c == 200 and c2 == 200 and t.phase()["step"] == "declare blockers" and t.stage() == "window" and t.perm(A, "Xindi Surveyors")["damage"] == 0
              and (t.combat(A).get("you") or {}).get("deal") is True,
              "TestB blocks with the Operative; nothing is dealt: TestA (who holds Saavik and five Plains) has the window", (c, d, c2, d2, t.stage()))
        c, d = t.act(A, "cast", name=SAAVIK)
        pend = t.state(A).get("pending_choices") or []
        if pend:
            t.raw(A, "choose", id=pend[0]["id"], target={"at": B, "ref": f"#{tso}"})
        said = " ".join(str(e.get("text")) for e in t.since(ev0, "say"))
        check(c == 200 and t.perm(B, "Tal Shiar Operative") is None and "X is 4" in said and t.phase()["step"] == "declare blockers",
              "Saavik (flash) enters after blocks: 4 damage (Xindi, Scientist, Vulcan, Advisor) to the BLOCKING Operative, which dies", (c, d, said[-300:]))
        c, d = t.act(A, "damage")
        xi = t.perm(A, "Xindi Surveyors")
        check(c == 200 and t.lifeof(B) == 20 and xi is not None and xi["damage"] == 0 and t.stage() == "done",
              "damage: Xindi Surveyors stays blocked (no trample), deals nothing to TestB (20) and takes nothing from a blocker that is gone", (c, d, t.lifeof(B), xi))
        said = " ".join(str(e.get("text")) for e in t.since(ev0, "say"))
        check("was blocked, and what blocked it has left the battlefield: no damage" in said, "…and the table says why", said[-300:])
    finally:
        t.stop()


# ── M5: a targeted attack trigger is answered before blocks ───────────────────────────────────────────────────────────────────────────
def janeway_first():
    print("M5: Hologram Janeway's target is chosen BEFORE blocks are asked")
    t = Table()
    try:
        t.start()
        j = t.onto(A, "Hologram Janeway")               # 3/3
        h = t.onto(A, "Horta")                          # 7/6 trample
        guard = t.onto(B, "Tal Shiar Operative")
        t.fresh(B)
        t.fresh(A)
        ev0 = t.last()
        c, d = t.act(A, "attack", assign={f"#{j}": B, f"#{h}": B})
        pend = t.state(A).get("pending_choices") or []
        check(c == 200 and pend and pend[0]["source"] == "Hologram Janeway" and t.stage() == "triggers", "TestA attacks with Janeway and Horta (total power 10): her trigger asks for a target", (c, d, pend, t.stage()))
        check(not [e for e in t.since(ev0, "attention") if e.get("kind") == "attacked"] and not t.state(B).get("incoming"),
              "TestB has NOT been asked to block yet", (t.state(B).get("incoming"),))
        c, d = t.raw(B, "block", none=True)
        check(c == 400 and "attack triggers come first" in str(d.get("error")) and t.lifeof(B) == 20, "a block sent now is refused: the trigger comes first", (c, d))
        c, d = t.raw(B, "block", attacker=f"#{h}", blocker=f"#{guard}")
        check(c == 400 and t.lifeof(B) == 20 and t.perm(B, "Tal Shiar Operative") is not None, "…in the older one-attacker form too", (c, d))
        check((t.combat(B).get("you") or {}).get("triggers_first") == [A], "TestB's page is told who it is waiting for", t.combat(B).get("you"))
        c, d = t.raw(A, "choose", id=pend[0]["id"], target={"at": A, "ref": f"#{h}"})
        att = [e for e in t.since(ev0, "attention") if e.get("kind") == "attacked"]
        inc = {i["ref"]: i for i in t.state(B).get("incoming") or []}
        check(c == 200 and t.perm(A, "Horta")["pt"] == "8/7" and len(att) == 1 and att[0]["amount"] == 11 and inc.get(f"#{h}", {}).get("power") == 8,
              "the counter goes on Horta (8/7); only now is TestB asked, with Horta at 8: 11 if nothing blocks", (c, d, att, inc))
        c, d = t.raw(B, "block", none=True)
        check(c == 200 and t.lifeof(B) == 9 and lost(t, ev0, B) == 11, "no blocks: TestB takes 3 + 8 = 11 (20 -> 9), the counter counted", (c, d, t.lifeof(B)))
    finally:
        t.stop()


# ── M16: an attacker destroyed before damage ──────────────────────────────────────────────────────────────────────────────────────────
def destroyed_attacker():
    print("M16: an attacker destroyed before blocks (He's Dead, Jim) is out of the combat")
    t = Table()
    try:
        t.start()
        xs = t.onto(A, "Xindi Surveyors")
        ss = t.onto(A, "Starfleet Security")
        t.fresh(B)
        t.lands(B, 4, "Swamp")
        t.fresh(A)
        t.hand(B, JIM)
        ev0 = t.last()
        c, d = t.act(A, "attack", assign={f"#{xs}": B, f"#{ss}": B})
        c2, d2 = t.raw(B, "cast", name=JIM, targets=[{"at": A, "ref": f"#{xs}"}])
        st = t.state(B)
        if st.get("pending_surveil"):
            t.raw(B, "surveil", graveyard=[])
        inc = t.state(B).get("incoming") or []
        said = " ".join(str(e.get("text")) for e in t.since(ev0, "say"))
        check(c == 200 and c2 == 200 and t.perm(A, "Xindi Surveyors") is None and [i["ref"] for i in inc] == [f"#{ss}"] and "no longer attacking" in said,
              "TestB destroys Xindi Surveyors in the declare-attackers step: only Starfleet Security is left to answer, and the table says so", (c, d, c2, d2, inc, said[-200:]))
        c, d = t.raw(B, "block", none=True)
        check(c == 200 and t.lifeof(B) == 19 and lost(t, ev0, B) == 1, "no blocks: TestB takes 1, not 4 (20 -> 19)", (c, d, t.lifeof(B)))
        said = " ".join(str(e.get("text")) for e in t.since(ev0, "say"))
        check("Combat: TestB took 1." in said, "the account: 1 of the 1 still attacking", said[-200:])
    finally:
        t.stop()


# ── M22: "can't block this turn" ──────────────────────────────────────────────────────────────────────────────────────────────────────
def cant_block():
    print("M22: a creature that can't block this turn is not offered, and a block with it is refused")
    t = Table()
    try:
        t.start()
        xs = t.onto(A, "Xindi Surveyors")
        op = t.onto(B, "Tal Shiar Operative")
        mu = t.onto(B, "Mugato")
        t.fresh(B)
        t.fresh(A)
        c, d = t.host(B, "cant-block", ref=f"#{op}")     # La'An's "target creature can't block this turn", as the effect marks it
        check(c == 200 and "can't block this turn" in " ".join(d.get("said") or []), "the Operative is marked: can't block this turn", (c, d))
        ev0 = t.last()
        c, d = t.act(A, "attack", assign={f"#{xs}": B})
        inc = t.state(B).get("incoming") or []
        check(c == 200 and inc and inc[0]["can_block"] == [mu] and t.state(B).get("block_options", {}).get(f"#{xs}") == [mu],
              "TestB's block prompt offers Mugato only", inc)
        blocks = [a for a in t.legal(B)["actions"] if a["action"] == "block"]
        refs = [a["body"].get("blocker") for a in blocks]
        check(f"#{op}" not in refs and f"#{mu}" in refs and any(a["body"].get("none") for a in blocks), "the legal list (what a bot follows) has no block with the Operative", refs)
        c, d = t.raw(B, "block", blocks={f"#{xs}": [f"#{op}"]})
        check(c == 400 and "can't block this turn" in str(d.get("error")) and t.stage() == "blocks" and t.lifeof(B) == 20, "a block with it is refused, and the combat stays as it was", (c, d))
        c, d = t.raw(B, "block", attacker=f"#{xs}", blocker=f"#{op}")
        check(c == 400 and "can't block this turn" in str(d.get("error")), "…in the one-attacker form too", (c, d))
        c, d = t.raw(B, "block", none=True)
        check(c == 200 and t.lifeof(B) == 17, "no block: 3 comes through (20 -> 17)", (c, d, t.lifeof(B)))
        t.fresh(B)
        t.fresh(A)
        c, d = t.act(A, "attack", assign={f"#{xs}": B})
        inc = t.state(B).get("incoming") or []
        check(inc and sorted(inc[0]["can_block"]) == sorted([op, mu]), "next turn the mark is gone: both may block", inc)
        c, d = t.raw(B, "block", blocks={f"#{xs}": [f"#{op}"]})
        check(c == 200 and t.lifeof(B) == 17 and t.perm(A, "Xindi Surveyors") is None, "…and the Operative blocks (17 stays 17, Xindi dies)", (c, d, t.lifeof(B)))
    finally:
        t.stop()


# ── M17: the combat survives a restart; one attack a turn; M19: the declaration is locked ─────────────────────────────────────────────
def restore_mid_combat():
    print("M17: a restart in the middle of combat keeps the attackers, the blocks and who has attacked")
    t = Table()
    snap = None
    try:
        t.start()
        mu = t.onto(A, "Mugato")                         # 6/6 vigilance: stays untapped after attacking
        xs = t.onto(A, "Xindi Surveyors")
        spare = t.onto(A, "Starfleet Security")          # haste: could attack, is kept home
        op = t.onto(B, "Tal Shiar Operative")
        t.fresh(B)
        t.fresh(A)
        c, d = t.act(A, "attack", assign={f"#{mu}": B, f"#{xs}": B})
        check(c == 200 and not t.perm(A, "Mugato")["tapped"], "TestA attacks with Mugato (vigilance, untapped) and Xindi Surveyors", (c, d))
        c, d = t.raw(A, "attack", assign={f"#{spare}": B})
        check(c == 409 and "locked" in str(d.get("error")) and not t.perm(A, "Starfleet Security")["tapped"],
              "M19: a second batch of attackers is refused: the declaration is locked once made", (c, d))
        c, d = t.raw(B, "block", attacker=f"#{xs}", blocker=f"#{op}")
        check(c == 200 and t.lifeof(B) == 20 and t.stage() == "blocks", "TestB blocks Xindi Surveyors; Mugato is still unanswered; nothing dealt", (c, d))
        time.sleep(0.5)
        snap = next(iter(t.dir.rglob("snapshot.pkl")), None)
        keys = dict(t.keys)
    finally:
        t.stop()
    check(snap is not None, "the table saved the game", snap)
    if snap is None:
        return
    t = Table(extra=["--restore", str(snap)])
    try:
        t.keys = keys
        ph = t.phase()
        pub = (ph.get("combat") or {}).get("attacks") or [{}]
        rows = {r["ref"]: r for r in pub[0].get("attackers") or []}
        check(ph.get("player") == A and ph.get("step") == "declare attackers" and pub[0].get("stage") == "blocks" and set(rows) == {f"#{mu}", f"#{xs}"}
              and rows[f"#{xs}"]["answered"] and rows[f"#{xs}"]["blockers"][0]["id"] == op and not rows[f"#{mu}"]["answered"],
              "restored: the same step, both attackers, the Operative's block on Xindi Surveyors, Mugato unanswered", (ph.get("step"), pub))
        inc = t.state(B).get("incoming") or []
        check([i["ref"] for i in inc] == [f"#{mu}"] and inc[0]["can_block"] == [] and sorted(t.state(A).get("attacked_ids") or []) == sorted([mu, xs]),
              "TestB still owes an answer for Mugato only (its Operative is already blocking); TestA's attackers are remembered", (inc, t.state(A).get("attacked_ids")))
        c, d = t.raw(A, "attack", assign={f"#{mu}": B})
        check(c == 409 and t.lifeof(B) == 20, "Mugato (untapped: vigilance) can NOT attack a second time after the restart", (c, d))
        c, d = t.raw(B, "block", attacker=f"#{mu}")
        check(c == 200 and t.lifeof(B) == 14 and t.perm(A, "Xindi Surveyors") is None and t.perm(B, "Tal Shiar Operative") is None,
              "TestB takes Mugato: one damage step: 6 to TestB (20 -> 14), and the blocked pair trades", (c, d, t.lifeof(B)))
        ph = t.phase()
        check(ph["step"] == "combat damage", "the turn is at combat damage", ph["step"])
        c, d = t.raw(A, "attack", assign={f"#{mu}": B})
        c2, d2 = t.raw(A, "attack", assign={f"#{spare}": B})
        check(c == 409 and c2 == 409 and t.lifeof(B) == 14, "an attack in the combat damage step is refused (the creature that attacked, and one that did not)", (c, d, c2, d2))
        time.sleep(0.5)
        snap2 = next(iter(t.dir.rglob("snapshot.pkl")), None)
    finally:
        t.stop()
    t = Table(extra=["--restore", str(snap2)])
    try:
        t.keys = keys
        c, d = t.raw(A, "attack", assign={f"#{mu}": B})
        check(c == 409 and t.lifeof(B) == 14 and t.phase()["step"] == "combat damage",
              "restored again AFTER damage (game 1, turn 15): the creature that attacked still can't attack again this turn", (c, d, t.phase()["step"]))
        c, d = t.act(A, "next")
        check(c == 200 and t.phase()["step"] == "main 2" and t.lifeof(B) == 14, "Next: main 2, and no second damage", (c, d, t.phase()["step"], t.lifeof(B)))
        t.fresh(B)
        t.fresh(A)
        c, d = t.act(A, "attack", assign={f"#{mu}": B})
        c2, d2 = t.raw(B, "block", none=True)
        check(c == 200 and c2 == 200 and t.lifeof(B) == 8, "on TestA's NEXT turn Mugato attacks again (14 -> 8)", (c, d, c2, d2, t.lifeof(B)))
    finally:
        t.stop()


# ── M18: sorcery speed is the cast action's own rule ──────────────────────────────────────────────────────────────────────────────────
def sorcery_speed():
    print("M18: a creature without flash can't be cast on the opponent's turn, whatever the page shows")
    t = Table()
    try:
        t.start()
        t.fresh(B)
        t.lands(B, 4, "Forest")
        t.lands(B, 4, "Island")
        t.fresh(A)
        t.hand(B, "Xindi Surveyors")
        t.hand(B, WESLEY)
        src0 = len(t.state(B)["mana_sources"])
        c, d = t.raw(B, "cast", name="Xindi Surveyors")
        st = t.state(B)
        check(c == 400 and "sorcery speed" in str(d.get("error")) and len(st["mana_sources"]) == src0 and any(h["name"] == "Xindi Surveyors" for h in st["hand"])
              and t.perm(B, "Xindi Surveyors") is None,
              "TestB casts Xindi Surveyors in TestA's main 1: refused (400, sorcery speed), nothing tapped, the card still in hand", (c, d, src0, len(st["mana_sources"])))
        c, d = t.raw(B, "cast", name="Xindi Surveyors", face_down=True)
        check(c == 400 and "sorcery speed" in str(d.get("error")), "…face down too", (c, d))
        c, d = t.raw(B, "cast", name=WESLEY)
        check(c == 200 and t.perm(B, WESLEY) is not None, "a flash creature (Wesley Crusher) is accepted", (c, d))
        t.decline(B)
        t.fresh(B)
        t.hand(B, "Xindi Surveyors")
        c, d = t.act(B, "cast", name="Xindi Surveyors")
        check(c == 200 and t.perm(B, "Xindi Surveyors") is not None, "in TestB's own main phase the same card is cast", (c, d))
        t.hand(B, "Xindi Surveyors")
        t.next_to(B, "end step")
        c, d = t.raw(B, "cast", name="Xindi Surveyors")
        check(c == 400 and "sorcery speed" in str(d.get("error")), "…and not in its own end step", (t.phase()["step"], c, d))
    finally:
        t.stop()


# ── M23/M24: the game's end ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def overkill():
    print("M23/M24: lethal with overkill: one game-over, after the whole damage step, at the true totals; nothing after it")
    t = Table()
    try:
        t.start()
        a5 = t.onto(A, "Xindi Surveyors")
        a4 = t.onto(A, "Pet Sehlat")
        a2 = t.onto(A, "Command Subadjunct")
        t.fresh(B)
        t.lands(B, 4, "Swamp")
        t.fresh(A)
        for _ in range(2):                               # Xindi Surveyors to 5/4
            t.host(A, "counter", ref=f"#{a5}", n=1, quiet=True)
        t.host(B, "life", player=B, delta=-15, quiet=True)
        t.hand(B, JIM)
        check(t.lifeof(B) == 5 and t.perm(A, "Xindi Surveyors")["pt"] == "5/4", "TestB is at 5; TestA has a 5/4, a 4/3 and a 2/1", (t.lifeof(B), t.perm(A, "Xindi Surveyors")))
        ev0 = t.last()
        c, d = t.act(A, "attack", assign={f"#{a5}": B, f"#{a4}": B, f"#{a2}": B})
        c1, d1 = t.raw(B, "cast", name=JIM, targets=[{"at": A, "ref": f"#{a2}"}])      # kills the 2/1 and leaves TestB owing a surveil
        st = t.state(B)
        c2, d2 = t.raw(B, "block", none=True)
        time.sleep(0.6)
        evs = t.since(ev0)
        over = [e for e in evs if e["type"] == "game-over"]
        lifes = [e for e in evs if e["type"] == "life" and e.get("player") == B]
        check(c == 200 and c1 == 200 and st.get("pending_surveil") == 1 and c2 == 200 and len(lifes) == 2 and [e["life"] for e in lifes] == [0, -4],
              "5 then 4 unblocked into 5 life (the 2/1 was destroyed): both hits land, 5 -> 0 -> -4", (c, d, c1, d1, c2, d2, lifes))
        check(len(over) == 1 and over[0]["id"] > lifes[-1]["id"] and over[0].get("totals") == {A: 20, B: -4} and over[0].get("winner") == A,
              "ONE game-over event, after the last life event, with the true totals (TestB -4, not 0)", over)
        s = (over[0].get("summary") or {}) if over else {}
        fb = s.get("finishing_blow") or {}
        check(fb.get("to") == B and fb.get("player") == B and fb.get("dealt_by") == A and fb.get("amount") == 9 and fb.get("final") == -4
              and sorted((w["name"], w["amount"]) for w in fb.get("with") or []) == [("Pet Sehlat", 4), ("Xindi Surveyors", 5)] and str(fb.get("line", "")).startswith("I attack"),
              "the finishing blow: 9 to TestB, dealt by TestA, with Xindi Surveyors (5) and Pet Sehlat (4), after 'I attack …'", fb)
        check(s.get("life_lost", {}).get(B) == 24 and s.get("life", {}).get(B, [{}])[-1].get("life") == -4 and s.get("damage_dealt_on_their_turn", {}).get(A) == 24,
              "the summary's own numbers agree (life lost 15 + 9, the life line ends at -4)", (s.get("life_lost"), s.get("life", {}).get(B, [])[-2:], s.get("damage_dealt_on_their_turn")))
        g = t.call("GET", "/api/game/summary", host=False)[1]
        check(g.get("over") and g.get("totals") == {A: 20, B: -4}, "every page reads the same totals", g.get("totals"))
        st = t.state(B)
        said = " ".join(str(e.get("text")) for e in evs if e["type"] == "say")
        check(not st.get("pending_surveil") and not st.get("pending_choices") and "The game is over: 1 thing" in said, "M24: TestB's open surveil is dropped, with one public line", (st.get("pending_surveil"), said[-200:]))
        ev1 = t.last()
        got = {}
        got["end"] = t.raw(A, "end", text="over")
        got["next"] = t.raw(A, "next")
        got["damage"] = t.raw(A, "damage")
        got["attack"] = t.raw(A, "attack", assign={f"#{a4}": B})
        got["surveil"] = t.raw(B, "surveil", graveyard=[])
        got["block"] = t.raw(B, "block", none=True)
        got["pass"] = t.raw(B, "pass")
        got["cast"] = t.raw(B, "cast", name="Swamp")
        check(all(c_ == 409 and "game is over" in str(d_.get("error")) for c_, d_ in got.values()), "after the end every play action is refused (end, next, damage, attack, surveil, block, pass, cast)",
              {k: v for k, v in got.items() if v[0] != 409})
        c, d = t.raw(A, "say", text="Good game.")
        check(c == 200, "…talking still works", (c, d))
        c, d = t.call("POST", "/api/phase/next", {"by": A, "key": t.keys[A]}, key=t.keys[A])
        time.sleep(2.5)                                  # (longer than the auto-begin loop's second and the end-of-turn timer)
        after = [e for e in t.since(ev1) if e["type"] in ("phase", "life", "pass", "attention", "game-over")]
        ph = t.phase()
        check(not after and ph.get("game_over") and ph.get("step") == "combat damage" and t.lifeof(B) == -4,
              "no step, life, pass or prompt event after the end; the table stays where the game ended", (after, ph.get("step")))
        check(t.legal(A).get("note") == "the game is over" and not t.legal(A).get("actions"), "the legal list is empty: the game is over", t.legal(A))
    finally:
        t.stop()


# ── M2: the pass rule at blocks ───────────────────────────────────────────────────────────────────────────────────────────────────────
def not_passed_at_blocks():
    print("M2/T6: with response windows off, a person is never passed for (or blocked for) while they owe a block declaration")
    t = Table(env_extra={"TABLE_AI_PASS_SECS": "1"})
    try:
        t.start(windows=False)
        xs = t.onto(A, "Xindi Surveyors")
        op = t.onto(B, "Tal Shiar Operative")
        t.fresh(B)
        t.fresh(A)
        t.hand(A, "Xindi Surveyors")                     # (for the main-phase cast below)
        t.lands(A, 3, "Forest")
        ev0 = t.last()
        c, d = t.act(A, "attack", assign={f"#{xs}": B})
        time.sleep(3.0)                                  # three times the pass timer
        evs = t.since(ev0)
        auto = [e for e in evs if (e["type"] == "pass" and e.get("by") == B and e.get("step") in ("declare attackers", "declare blockers")) or
                (e["type"] == "say" and e.get("speaker") == B)]
        check(c == 200 and t.phase()["step"] == "declare attackers" and t.stage() == "blocks" and t.lifeof(B) == 20 and not auto and t.phase()["windows"] is False,
              "three pass timers later: still declare attackers, TestB neither passed for nor answered for, nothing dealt", (t.phase()["step"], auto))
        got = {k: t.raw(A, k) for k in ("next", "damage", "end")}
        check(all(c_ == 409 and ("declare blocks" in str(d_.get("error")) or "to block" in str(d_.get("error"))) for c_, d_ in got.values()) and t.lifeof(B) == 20,
              "the attacker's Next, Damage and End all wait for TestB's answer", got)
        c, d = t.raw(A, "cast", name="Xindi Surveyors")
        check(c == 409 and "declare blocks" in str(d.get("error")) and t.lifeof(B) == 20 and t.phase()["step"] == "declare attackers",
              "…and so does a main-phase cast that would walk the turn out of combat (it used to deal the attack unblocked)", (c, d))
        c, d = t.raw(B, "block", blocks={f"#{xs}": [f"#{op}"]})
        check(c == 200 and t.lifeof(B) == 20 and t.perm(B, "Tal Shiar Operative") is None, "TestB blocks when ready: the Operative dies, no damage to TestB", (c, d))

        print("M6: nothing can block (no untapped creature): still ONE explicit answer, never an automatic one")
        t.fresh(B)
        a1, a2 = t.onto(A, "Starfleet Security"), t.onto(A, "Starfleet Security")
        t.fresh(A)
        ev1 = t.last()
        c, d = t.act(A, "attack", assign={f"#{a1}": B, f"#{a2}": B})
        time.sleep(2.2)
        att = [e for e in t.since(ev1, "attention") if e.get("kind") == "attacked"]
        check(len(att) == 1 and att[0].get("unblockable") is True and "Nothing of yours can block" in att[0].get("text", "") and t.lifeof(B) == 20 and t.stage() == "blocks",
              "one prompt for both attackers, saying nothing can block; the table waits", (att, t.stage()))
        c, d = t.raw(B, "block", none=True)
        check(c == 200 and t.lifeof(B) == 18, "one answer: take 2 (20 -> 18)", (c, d, t.lifeof(B)))
    finally:
        t.stop()


# ── v6d: the combat meets the v6a pieces (the one priority prompt, the held spell) ───────────────────────────────────────────────────────
def pass_and_chip_at_blocks():
    print("v6d: while blocks are owed the seat is not offered a Pass (no duplicate prompt), and a Pass sent anyway is refused")
    t = Table()
    try:
        t.start()
        xs = t.onto(A, "Xindi Surveyors")
        t.fresh(B)
        t.fresh(A)
        c, d = t.act(A, "attack", assign={f"#{xs}": B})
        ph = t.phase()
        check((ph.get("passes") or {}).get("decision") == "blocks" and (ph.get("passes") or {}).get("next") == B,
              "/api/phase says TestB's decision is `blocks` (the pages show the block panel, not the pass chip)", ph.get("passes"))
        acts = [a["action"] for a in t.legal(B)["actions"]]
        check("pass" not in acts and "block" in acts, "TestB's legal list has the block entries and no `pass`", acts)
        c, d = t.raw(B, "pass", player=A, step="declare attackers")
        check(c == 409 and "declare your blocks" in str(d.get("error")) and t.stage() == "blocks", "a pass is refused: blocks are declared, not passed", (c, d.get("error")))
        c, d = t.raw(B, "block", none=True)
        check(c == 200 and t.lifeof(B) == 17, "the declaration goes through (20 -> 17)", (c, d))
    finally:
        t.stop()


def window_held_spell():
    print("v6d: the window after blocks and a held spell: TestA casts a creature after blocks, TestB holds a counterspell; combat waits for that window first")
    t = Table()
    try:
        t.start()
        xs = t.onto(A, "Xindi Surveyors")
        t.lands(A, 5, "Plains")
        t.fresh(B)
        t.lands(B, 3, "Island")
        t.fresh(A)
        t.hand(A, SAAVIK)
        t.hand(B, "Consider the Prime Directive")
        ev0 = t.last()
        c, d = t.act(A, "attack", assign={f"#{xs}": B})
        c2, d2 = t.raw(B, "block", none=True)
        check(c == 200 and c2 == 200 and t.stage() == "window" and t.lifeof(B) == 20, "blocks in; the window is open; TestB holds a counterspell", (c, d, c2, d2, t.stage()))
        c, d = t.raw(A, "cast", name=SAAVIK)
        ph = t.phase()
        check(c == 200 and (ph.get("held") or {}).get("spell", "").startswith("Saavik") and (ph["held"].get("waiting") == [B]) and t.perm(A, SAAVIK) is None,
              "Saavik is cast after blocks and HELD for TestB's answer", (c, d, ph.get("held")))
        time.sleep(1.0)
        check(t.lifeof(B) == 20 and t.phase()["step"] == "declare blockers" and t.stage() == "window", "while the spell is held the combat does not move on", (t.lifeof(B), t.phase()["step"], t.stage()))
        c, d = t.raw(A, "damage")
        check(c in (409, 400) and t.lifeof(B) == 20, "the attacker's Damage waits for the held spell first", (c, str(d.get("error"))[:120]))
        c, d = t.raw(B, "pass", player=A, step=t.phase()["step"])
        check(c == 200 and t.perm(A, SAAVIK) is not None and t.lifeof(B) == 20, "TestB passes: Saavik resolves and enters; still no damage", (c, d, t.lifeof(B)))
        t.decline(A)
        c, d = t.raw(A, "damage")
        for _ in range(12):
            if t.lifeof(B) < 20:
                break
            ph = t.phase()
            nxt = (ph.get("passes") or {}).get("next")
            if nxt in t.keys:
                t.raw(nxt, "pass", player=ph.get("player"), step=ph.get("step"))
            else:
                t.raw(A, "damage")
            time.sleep(0.4)
        check(t.lifeof(B) == 17 and t.phase()["step"] == "combat damage" and t.stage() == "done", "everyone has passed: the damage step, 20 -> 17", (t.lifeof(B), t.phase()["step"], t.stage()))
        check(any(e["type"] == "life" and e.get("by") == "combat" for e in t.since(ev0)) and not any(e["type"] == "life" and e.get("by") == "combat" and e["id"] < next((x["id"] for x in t.since(ev0, "phase") if x.get("step") == "combat damage"), 0) for e in t.since(ev0)),
              "every combat life event is after the damage step began")
    finally:
        t.stop()


def mulligan_then_combat():
    print("v6d: the opening-hand step (keep / mulligan), then a combat through the new flow")
    t = Table(env_extra={"TABLE_MULLIGAN": "on", "TABLE_MULLIGAN_SECS": "0"})
    try:
        t.claim()
        t.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)
        t.call("POST", "/api/phase/next", {"by": A, "key": t.keys[A]}, key=t.keys[A])
        ph = t.phase()
        check((ph.get("mulligan") or {}).get("on") and sorted(ph["mulligan"].get("waiting") or []) == sorted([A, B]) and ph.get("player") in (None, ""),
              "the table asks both seats keep or mulligan; no turn has started", ph.get("mulligan"))
        for n in (A, B):
            acts = {a["action"]: a for a in t.legal(n)["actions"]}
            c, d = t.raw(n, "keep", **{k: v for k, v in acts["keep"]["body"].items() if k != "seat"}) if "keep" in acts else (0, {})
            check(c == 200, f"{n} keeps", (c, d))
        t.wait(lambda p: p.get("player") == A and p.get("begun") and p.get("step") == "main 1")
        t.empty_hands()
        xs = t.onto(A, "Xindi Surveyors")
        t.fresh(B)
        t.fresh(A)
        c, d = t.act(A, "attack", assign={f"#{xs}": B})
        c2, d2 = t.raw(B, "block", none=True)
        check(c == 200 and c2 == 200 and t.lifeof(B) == 17, "after the mulligan step the attack and the one-answer block work: 20 -> 17", (c, d, c2, d2, t.lifeof(B)))
    finally:
        t.stop()


def restore_prepatch_snapshot():
    print("v6d: a game saved by the PRE-patch table (table-v6b: no combat state in its snapshot) restores here, mid-turn, and plays on")
    old = HERE.parent / "table-v6b" / "server.py"
    if not old.exists():
        print(f"  ⏭️  skipped: no pre-patch table at {old}")
        return
    t = Table(code=old)
    snap, keys = None, {}
    try:
        t.start()
        mu = t.onto(A, "Mugato")                          # vigilance: stays untapped after attacking
        xs = t.onto(A, "Xindi Surveyors")
        op = t.onto(B, "Tal Shiar Operative")
        t.fresh(B)
        t.fresh(A)
        c, d = t.act(A, "attack", assign={f"#{mu}": B})
        check(c == 200 and not t.perm(A, "Mugato")["tapped"], "(pre-patch table) TestA attacks with Mugato (vigilance)", (c, d))
        time.sleep(0.6)
        snap = next(iter(t.dir.rglob("snapshot.pkl")), None)
        keys = dict(t.keys)
    finally:
        t.stop()
    check(snap is not None, "(pre-patch table) it saved the game", snap)
    if snap is None:
        return
    t = Table(extra=["--restore", str(snap)])
    try:
        t.keys = keys
        ph = t.phase()
        check(ph.get("player") == A and ph.get("step") in ("declare attackers", "main 1", "beginning of combat") and t.lifeof(B) == 20,
              "restored under table-v6d at the same turn (no combat state in the file: plain defaults)", (ph.get("player"), ph.get("step"), (ph.get("combat") or {})))
        c, d = t.raw(A, "attack", assign={f"#{mu}": B})
        check(c in (400, 409) and "already attacked" in str(d.get("error")) and t.lifeof(B) == 20, "Mugato (untapped) is still known to have attacked this turn: a second attack is refused", (c, str(d.get("error"))[:140]))
        c, d = t.raw(B, "block", none=True)
        check(c == 400 and t.lifeof(B) == 20, "no declaration was saved, so there is nothing to block (the old attack's record is gone, as it always was)", (c, d.get("error")))
        t.fresh(B)
        t.fresh(A)
        c, d = t.act(A, "attack", assign={f"#{mu}": B, f"#{xs}": B})
        c2, d2 = t.raw(B, "block", blocks={f"#{xs}": [f"#{op}"]})
        check(c == 200 and c2 == 200 and t.lifeof(B) == 14 and t.perm(B, "Tal Shiar Operative") is None, "the next turn runs the new combat: Mugato 6 through, Xindi Surveyors and the Operative trade (20 -> 14)", (c, c2, t.lifeof(B)))
    finally:
        t.stop()


# ── a person's real-deck seat attacks a driven seat ──────────────────────────────────────────────────────────────────────────────────
def real_deck_attacker():
    print("A real-deck seat (no engine deck) declares two attackers at a driven seat: the same rule, nothing dealt until both are answered")
    t = Table(extra=["--human", "Sam|", "--order", f"{A},{B},Sam", "--human-pass-secs", "0.2"])
    try:
        t.start()
        op = t.onto(B, "Tal Shiar Operative")
        ev0 = t.last()
        c, d = t.call("POST", "/api/declare/attack", {"by": "Sam", "attacks": [{"attacker": "Grizzly Bears", "target": B, "power": 2}, {"attacker": "Hill Giant", "target": B, "power": 3}]}, host=False)
        inc = t.state(B).get("incoming") or []
        check(c == 200 and sorted(i["creature"] for i in inc) == ["Grizzly Bears", "Hill Giant"], "Sam declares Grizzly Bears (2) and Hill Giant (3) at TestB from the board", (c, d, inc))
        c, d = t.raw(B, "block", attacker="Hill Giant", blocker=f"#{op}")
        check(c == 200 and t.lifeof(B) == 20 and not t.since(ev0, "life") and t.perm(B, "Tal Shiar Operative") is not None, "TestB blocks Hill Giant: nothing dealt yet", (c, d, t.lifeof(B)))
        c, d = t.raw(B, "block", attacker="Grizzly Bears")
        check(c == 200 and t.lifeof(B) == 18 and t.perm(B, "Tal Shiar Operative") is None, "TestB takes Grizzly Bears: both resolve together (18; the Operative dies to the Giant's 3)", (c, d, t.lifeof(B)))
    finally:
        t.stop()


# ── turn structure: End once; timed windows ───────────────────────────────────────────────────────────────────────────────────────────
def double_end():
    print("Turn structure: two Ends in the same moment are one end of turn (the next turn used to start twice)")
    t = Table()
    try:
        t.start()
        t.fresh(B)
        t.fresh(A)
        ev0 = t.last()
        h0 = len(t.state(B).get("hand") or [])
        c1, d1 = t.raw(A, "end", text="over")
        c2, d2 = t.raw(A, "end", text="over again")
        time.sleep(3.5)
        evs = t.since(ev0)
        starts = [e for e in evs if e["type"] == "phase" and e.get("index") == 0]
        check(c1 == 200 and c2 == 409 and "already ending" in str(d2.get("error")), "the second End is refused: the turn is already ending", (c1, c2, d2.get("error")))
        check(len(starts) == 1 and starts[0].get("player") == B and len(t.state(B).get("hand") or []) == h0 + 1 and sum(1 for e in evs if e["type"] == "say" and "turn " in str(e.get("text")) and e.get("speaker") == B) == 1,
              "TestB's turn starts ONCE: one untap step, one draw", ([e.get("player") for e in starts], len(t.state(B).get("hand") or []) - h0))
    finally:
        t.stop()


def timed_windows():
    print("With response windows ON: the same order; the damage step follows the window's beat")
    t = Table()
    try:
        t.start()
        xs = t.onto(A, "Xindi Surveyors")
        t.fresh(B)
        t.fresh(A)
        t.call("POST", "/api/phase/windows", {"on": True, "by": "host"}, host=False)     # (set-up ran with them off: each step's beat is seconds)
        ev0 = t.last()
        c, d = t.act(A, "attack", tries=120, assign={f"#{xs}": B})
        check(t.phase().get("windows") is True, "response windows are on", t.phase().get("windows"))
        c2, d2 = t.raw(B, "block", none=True)
        end = time.time() + 12
        while time.time() < end and t.lifeof(B) == 20:
            time.sleep(0.3)
        evs = t.since(ev0)
        order = [e.get("step") for e in evs if e["type"] == "phase" and e.get("step") in ("declare attackers", "declare blockers", "combat damage")]
        dmg_at = next((e["id"] for e in evs if e["type"] == "phase" and e.get("step") == "combat damage"), 0)
        lifes = [e for e in evs if e["type"] == "life"]
        check(c == 200 and c2 == 200 and t.lifeof(B) == 17 and order == ["declare attackers", "declare blockers", "combat damage"] and lifes and all(e["id"] > dmg_at for e in lifes),
              "attack, no blocks, the timed window, then the damage step: 20 -> 17, in that order", (c, d, c2, d2, order, t.lifeof(B)))
    finally:
        t.stop()


# ── bots ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
BOT_POOL = {"Mountain": 17, "Starfleet Security": 6, "Xindi Surveyors": 0, "Horta": 3}


def bot_deck(name, instants=False):
    import make_deck_json_shim as shim
    cards = json.load(open(cards_path.cards_json()))
    by = {c["name"]: c for c in cards if c.get("set") == "trk"}
    reds = [c["name"] for c in cards if c.get("set") == "trk" and "Creature" in (c.get("type_line") or "") and c.get("colors") == ["R"]
            and (c.get("cmc") or 9) <= 4 and "{X}" not in (c.get("mana_cost") or "")]
    pool = {"Mountain": 17}
    if instants:                                      # a deck whose bot HOLDS an instant it could pay for (the bot never casts them): the table must never wait on it
        pool = {"Mountain": 10, "Island": 8, "Consider the Prime Directive": 6}
        by["Consider the Prime Directive"] = next(c for c in cards if c["name"] == "Consider the Prime Directive" and c.get("set") == "trk") if any(c["name"] == "Consider the Prime Directive" and c.get("set") == "trk" for c in cards) else next(c for c in cards if c["name"] == "Consider the Prime Directive")
        by["Island"] = next(c for c in cards if c["name"] == "Island" and c.get("set") == "trk") if any(c["name"] == "Island" and c.get("set") == "trk" for c in cards) else next(c for c in cards if c["name"] == "Island")
    for n in sorted(set(reds))[: 6 if instants else 8]:
        pool[n] = 3
    f = R.TMP / f"{name}.json"
    f.write_text(json.dumps({"name": name, "commander": None, "startingLife": 20, "mainBoard": [shim.entry({**by[n]}, k) for n, k in pool.items()]}))
    return str(f), sorted(pool)


def bot_game_instants():
    bot_game(instants=True)


def bot_game(instants=False):
    print("Bots: two autopilots play a whole game through the new combat (declare, triggers, one block answer, pass, damage, the end)"
          + (" — each holding counterspells it never casts: the table must not wait on a bot that holds an instant" if instants else ""))
    botdir = Path(os.environ.get("COMBAT_BOT") or HERE.parent / "bot-v6d")
    if not (botdir / "claude_autopilot.py").exists():
        print(f"  ⏭️  skipped: no bot in {botdir} (COMBAT_BOT=<dir>)")
        return
    deck, names = bot_deck("bot-red-i" if instants else "bot-red", instants=instants)
    t = Table(deck_a=deck, deck_b=deck, extra=["--seed", "11"])
    procs = []
    try:
        t.claim()
        t.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)
        t.call("POST", "/api/phase/next", {"by": A, "key": t.keys[A]}, key=t.keys[A])
        for n in (A, B):
            kf = t.dir / f"{n}.key"
            kf.write_text(t.keys[n])
            kf.chmod(0o600)
            env = {**os.environ, "TABLE_URL": t.b, "CLAUDE_SEAT": n, "CLAUDE_KEY_FILE": str(kf), "AUTOPILOT_GRACE": "0", "PYTHONDONTWRITEBYTECODE": "1"}
            procs.append(subprocess.Popen([sys.executable, str(botdir / "claude_autopilot.py")], env=env, stdout=open(t.dir / f"bot-{n}.log", "w"), stderr=subprocess.STDOUT))
        end, ph, stuck_at, last_ev = time.time() + float(os.environ.get("COMBAT_BOT_SECS", "900")), {}, time.time(), 0
        while time.time() < end:
            ph = t.phase()
            if ph.get("game_over"):
                break
            cur = t.last()
            if cur != last_ev:
                last_ev, stuck_at = cur, time.time()
            if time.time() - stuck_at > 120:
                break
            time.sleep(2)
        evs = t.events()
        allev = []
        since = 0
        while True:                                      # every page of the log
            page = t.call("GET", f"/api/events?since={since}", host=False)[1].get("events", [])
            if not page:
                break
            allev += page
            since = page[-1]["id"]
        turns = sum(1 for e in allev if e["type"] == "phase" and e.get("index") == 0)
        over = [e for e in allev if e["type"] == "game-over"]
        check(bool(ph.get("game_over")) and len(over) == 1, f"the game ended by itself ({turns} turns, {len(allev)} events), with one game-over", (ph.get("player"), ph.get("step"), ph.get("combat"), [e for e in allev[-6:]]))
        atk = [e for e in allev if e["type"] == "attention" and e.get("kind") == "attacked"]
        dmg_steps = [e for e in allev if e["type"] == "phase" and e.get("step") == "combat damage"]
        blocks = [e for e in allev if e["type"] == "say" and e.get("action") == "block" and not e.get("auto")]
        check(len(atk) >= 3 and len(dmg_steps) >= 3 and len(blocks) >= len(atk) - 1, f"combat happened: {len(atk)} attacks asked, {len(blocks)} block declarations, {len(dmg_steps)} damage steps", (len(atk), len(blocks), len(dmg_steps)))
        bad = []
        step_now = None
        for e in allev:                                  # every combat life event lies inside a combat damage step
            if e["type"] == "phase" and not e.get("kind"):
                step_now = e.get("step")
            if e["type"] == "life" and e.get("by") == "combat" and step_now != "combat damage":
                bad.append((e["id"], step_now))
        check(not bad, "every combat life event was dealt in a combat damage step", bad[:5])
        if over:
            tot = over[0].get("totals") or {}
            lt = t.call("GET", "/api/life", host=False)[1]
            tail = [e for e in allev if e["id"] > over[0]["id"] and e["type"] in ("phase", "life", "pass")]
            check(tot == {k: lt.get(k) for k in tot} and min(tot.values()) <= 0 and not tail, "the recorded totals are the table's life totals, and nothing was played after the end", (tot, lt, tail[:3]))
        logs = "".join((t.dir / f"bot-{n}.log").read_text() for n in (A, B))
        check("Traceback" not in logs, "neither bot crashed", logs[-600:])
        if not ph.get("game_over"):                       # (what each bot was doing, for whoever reads this failure)
            for n in (A, B):
                print(f"  --- bot {n} log tail:\n" + "\n".join((t.dir / f"bot-{n}.log").read_text().splitlines()[-14:]), flush=True)
            print("  --- phase:", json.dumps({k: ph.get(k) for k in ("player", "step", "passes", "combat", "held", "mulligan")})[:1500], flush=True)
            for n in (A, B):
                stt = t.state(n)
                print(f"  --- {n} pending:", json.dumps({"choices": [{"source": c.get("source"), "text": str(c.get("text"))[:140], "options": len((c.get("slot") or {}).get("options") or []), "optional": (c.get("slot") or {}).get("optional"), "kind": c.get("kind")} for c in stt.get("pending_choices") or []],
                                                       "surveil": stt.get("pending_surveil"), "scry": stt.get("pending_scry")})[:1200], flush=True)
                print(f"  --- {n} legal:", json.dumps([a["action"] for a in t.legal(n).get("actions", [])]), t.legal(n).get("note"), flush=True)
        _ = evs
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        t.stop()


def static_pages():
    print("Pages: the one-prompt block declaration is what the hand page sends")
    s = (HERE / "hand.html").read_text()
    check('act("block", { blocks })' in s and 'act("block", { none: true })' in s and "data-blkgo" in s and "takeArm.all" in s,
          "hand.html sends ONE declaration (`blocks`, or `none` on the second tap), not one request per attacker")
    check('act("block", { attacker' not in s and 'act("block", { blocker' not in s, "…and no longer sends a per-attacker block")
    check("Confirm attack with" in s and "data-dmg" in s and "you.window" in s and "triggers_first" in s,
          "…the attack button says Confirm, and the window after blocks has its prompts (Pass for the defender, Deal combat damage for the attacker)")
    c = (HERE / "assets" / "choicealert.js").read_text()
    check("declare your blocks (one answer for all)" in c and "you.window" in c, "choicealert.js: the pinned banner asks for the one declaration, and says when the window is yours")


def main():
    only = [x for x in os.environ.get("COMBAT_ONLY", "").split("|") if x]
    for fn in (simultaneous, window_bounce, window_saavik, janeway_first, destroyed_attacker, cant_block, restore_mid_combat, sorcery_speed, overkill,
               not_passed_at_blocks, pass_and_chip_at_blocks, window_held_spell, mulligan_then_combat, restore_prepatch_snapshot, real_deck_attacker, double_end, timed_windows, static_pages, bot_game, bot_game_instants):
        if only and not any(o in fn.__name__ for o in only):
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {fn.__name__}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
