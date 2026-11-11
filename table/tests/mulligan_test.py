"""T15: the pre-game mulligan step. Every seat sees its opening seven and answers Keep or Mulligan (London) before the first turn.

    PYTHONDONTWRITEBYTECODE=1 ~/.venvs/table/bin/python table/tests/mulligan_test.py

The rule and the moves are checked on the engine alone; the step itself on real tables on free ports, through each seat's own key."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
os.environ["TABLE_MULLIGAN"] = "on"                    # (the harness starts tables with TABLE_HIGHROLL=first, which skips the step by default)
os.environ["TABLE_MULLIGAN_SECS"] = "0"                # a person is waited for; the timeout has its own table below
import retro_game4_test as R  # noqa: E402
import player  # noqa: E402

check = R.check
L = {"name": "Forest", "types": ["Land"], "manaValue": 0}
S2 = {"name": "Bear", "types": ["Creature"], "manaValue": 2}
S6 = {"name": "Whale", "types": ["Creature"], "manaValue": 6}


def hand(lands, n=7):
    return [dict(L) for _ in range(lands)] + [dict(S2) for _ in range(n - lands)]


def section(title, fn):
    print(f"\n{title}", flush=True)
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — one section that throws is one failure
        import traceback
        traceback.print_exc()
        check(False, f"{title}: raised {type(e).__name__}", e)


class Table(R.Table):
    """The harness table, but only the named seats are pilots (the other is an engine seat nobody drives)."""
    def __init__(self, pilots=("Claude", "Michael"), extra=(), env=None, dir_=None):
        self._pilots = pilots
        old = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try:
            super().__init__(extra=extra)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def claim(self):
        self.keys = {n: self.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in ("Claude", "Michael")}
        self.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)

    def press_start(self):
        return self.call("POST", "/api/phase/next", {"by": "Claude", "key": self.keys["Claude"]}, key=self.keys["Claude"])

    def phase(self):
        return self.call("GET", "/api/phase")[1]

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


def rule():
    A = player.mulligan_advice
    got = [A(hand(k), 17, 40)["verdict"] for k in range(8)]
    check(got == ["mulligan", "mulligan", "risky", "keep", "keep", "risky", "mulligan", "mulligan"], "40-card deck, seven cards: 0-1 and 6-7 lands mulligan, 2 and 5 risky, 3-4 keep", got)
    a = A(hand(1), 17, 40)
    check(a["text"] == "Mulligan suggested (1 land)" and a["lands"] == 1, "the words name the land count", a)
    check(A(hand(2), 17, 40)["text"] == "Risky keep (2 lands)" and A(hand(3), 17, 40)["text"] == "Keep suggested (3 lands)", "risky and keep say so", A(hand(2), 17, 40))
    check([A(hand(k), 24, 60)["verdict"] for k in (1, 2, 3, 4, 5, 6)] == ["mulligan", "risky", "keep", "keep", "risky", "mulligan"], "a 60-card deck with 24 lands: the same band", "")
    check([A(hand(k), 37, 99)["verdict"] for k in (1, 2, 3, 4, 5, 6)] == ["mulligan", "risky", "keep", "keep", "risky", "mulligan"], "a 99-card deck with 37 lands: the same band", "")
    low = [A(hand(k), 20, 60)["verdict"] for k in range(6)]
    check(low == ["mulligan", "risky", "keep", "keep", "risky", "mulligan"], "a deck built with fewer lands (20 of 60) wants two or three", low)
    check(A(hand(2), 17, 40, taken=1)["verdict"] == "keep" and A(hand(1), 17, 40, taken=1)["verdict"] == "mulligan", "after one mulligan a one-off hand is a keep, a one-lander still isn't", "")
    check(A(hand(1), 17, 40, taken=2)["verdict"] == "keep" and A(hand(0), 17, 40, taken=2)["verdict"] == "risky" and A(hand(7), 17, 40, taken=2)["verdict"] == "risky",
          "after two, nobody is told to go to four: only no lands or all lands is still called risky", "")
    check("already down to six" in A(hand(3), 17, 40, taken=1)["text"], "the words say what you are down to", A(hand(3), 17, 40, taken=1)["text"])
    b = player.mulligan_bottoms([dict(L), dict(L), dict(L), dict(S2), dict(S2), dict(S6), dict(S2)], 1, 17, 40)
    check(b == ["Whale"], "the rule bottoms the most expensive spell from a hand with the right lands", b)
    b = player.mulligan_bottoms(hand(6), 2, 17, 40)
    check(b == ["Forest", "Forest"], "…and lands from a hand with too many", b)


def engine():
    v = player.VirtualPlayer(R.DECK_MICHAEL, "Michael", seed=11)
    total = len(v.library) + len(v.hand)
    check(len(v.hand) == 7, "a seat starts with seven", len(v.hand))
    check(v.mull_again() == "six" and len(v.hand) == 7 and len(v.library) + len(v.hand) == total, "a mulligan: seven new cards, nothing lost (six will be kept)", (len(v.hand), len(v.library)))
    check(v.mull_again() == "five" and len(v.hand) == 7, "a second: seven again, five will be kept", len(v.hand))
    try:
        v.mull_keep([v.hand[0]["name"]])
        check(False, "keeping with one card named when two are owed is refused")
    except player.IllegalAction as e:
        check("exactly 2" in str(e) and len(v.hand) == 7, "keeping with one card named when two are owed is refused, nothing moved", e)
    try:
        v.mull_keep(["Not A Card", v.hand[0]["name"]])
        check(False, "a card that isn't in the hand is refused")
    except player.IllegalAction:
        check(len(v.hand) == 7 and not v.__dict__.get("mull_kept"), "a card that isn't in the hand is refused, nothing moved", "")
    a, b = v.hand[2], v.hand[5]
    size = v.mull_keep([a["name"], b["name"]])
    check(size == "five" and len(v.hand) == 5, "keep: five cards stay", (size, len(v.hand)))
    check(v.library[0] is a and v.library[1] is b, "the two named cards are the bottom two of the library (the first named lowest)", [c["name"] for c in v.library[:3]])
    check(len(v.library) + len(v.hand) == total and a not in v.hand and b not in v.hand, "…and are no longer in the hand", "")
    drawn = [v.library[-1], v.library[-2]]
    v.draw(2)
    check(v.hand[-2:] == drawn and a not in v.hand, "the next draws come off the top, not the bottom", "")
    try:
        v.mull_again()
        check(False, "no mulligan after keeping")
    except player.IllegalAction:
        check(True, "no mulligan after keeping")
    w = player.VirtualPlayer(R.DECK_MICHAEL, "Michael", seed=3)
    for _ in range(7):
        w.mull_again()
    try:
        w.mull_again()
        check(False, "an eighth mulligan is refused")
    except player.IllegalAction:
        check(True, "a hand can't go below zero: the eighth mulligan is refused")
    # a bot decides by the rule: build hands by hand
    x = player.VirtualPlayer(R.DECK_MICHAEL, "Michael", seed=5)
    lands = [c for c in x.library + x.hand if "Land" in c["types"]]
    spells = [c for c in x.library + x.hand if "Land" not in c["types"]]
    x.library, x.hand = [], []
    x.hand = spells[:7]                                # no lands: a mulligan by the rule
    x.library = spells[7:27] + lands[:20]
    x.rng.seed(1)
    steps = x.mull_by_rule()
    check(steps[0] == "six" and x.__dict__["mull_kept"] and len(x.hand) == 7 - x.__dict__["mull_n"] and x.__dict__["mull_n"] <= 2,
          "an engine seat with no lands mulligans, never below five, and keeps what the rule then says", (steps, len(x.hand)))
    y = player.VirtualPlayer(R.DECK_MICHAEL, "Michael", seed=5)
    y.library, y.hand = spells[10:30] + lands[3:20], lands[:3] + spells[:4]
    check(y.mull_by_rule() == ["seven"] and len(y.hand) == 7, "…and one with three lands keeps seven", "")


def both_keep():
    t = Table()
    try:
        t.claim()
        hands0 = {n: [c["name"] for c in t.state(n)["hand"]] for n in ("Claude", "Michael")}
        t.press_start()
        ph = t.phase()
        check(ph.get("player") is None and (ph.get("mulligan") or {}).get("on") and sorted(ph["mulligan"]["waiting"]) == ["Claude", "Michael"],
              "START opens the opening-hand step: no turn yet, both seats are waited on", ph.get("mulligan"))
        check(set(ph["mulligan"]) <= {"on", "first", "waiting", "taken", "seconds_left"}, "the public phase carries only who and how many", list(ph["mulligan"]))
        st = t.state("Michael")
        m = st.get("mulligan") or {}
        check(m.get("open") and not m.get("kept") and m.get("taken") == 0 and m.get("keeps") == 7 and len(st["hand"]) == 7, "a seat sees its seven and the question", m)
        lands = sum(1 for c in st["hand"] if c["land"])
        check(m["advice"]["lands"] == lands and m["advice"]["verdict"] in ("keep", "risky", "mulligan") and str(lands) in m["advice"]["text"], "…with a suggestion from its own land count", m.get("advice"))
        la = t.call("GET", "/api/seat/legal?seat=Michael", key=t.keys["Michael"])[1]
        check([a["action"] for a in la.get("actions", [])] == ["keep", "mulligan"], "the legal list offers exactly keep and mulligan", la)
        c, d = t.seat("Michael", "land", name="Forest")
        check(c != 200, "nothing else can be played before the hands are kept", (c, d))
        c, d = t.seat("Michael", "keep")
        check(c == 200 and d["said"] == ["Michael keeps seven."], "keep: the table says only 'Michael keeps seven.'", (c, d.get("said")))
        ph = t.phase()
        check(ph.get("player") is None and ph["mulligan"]["waiting"] == ["Claude"], "one keep doesn't start the game: the other seat is still asked", ph.get("mulligan"))
        check(t.state("Michael")["mulligan"]["kept"], "the seat that kept sees it has", "")
        c, d = t.seat("Michael", "mulligan")
        check(c == 400, "a seat that kept can't mulligan after all", (c, d))
        c, d = t.seat("Claude", "keep")
        ph = t.until(lambda p: p.get("player") is not None)
        check(c == 200 and ph.get("player") == "Claude" and "mulligan" not in ph, "the last keep starts the first turn, for the seat that was to go first", ph.get("player"))
        check({n: [c_["name"] for c_ in t.state(n)["hand"]][:7] for n in ("Claude", "Michael")}["Michael"] == hands0["Michael"], "a kept hand is the hand that was dealt", "")
        check("mulligan" not in t.state("Michael"), "the question is gone from the seat's state once the game is on", "")
        c, d = t.seat("Michael", "keep")
        check(c == 400 and "under way" in d.get("error", ""), "keep after the start is refused", (c, d))
        c, d = t.seat("Michael", "mulligan")
        check(c == 400, "…and so is a mulligan", (c, d))
    finally:
        t.stop()


def twice_and_leak():
    t = Table()
    try:
        t.claim()
        t.press_start()
        size0 = t.state("Michael")["library"]
        h0 = [c["name"] for c in t.state("Michael")["hand"]]
        c, d = t.seat("Michael", "mulligan")
        st = t.state("Michael")
        check(c == 200 and d["said"] == ["Michael mulligans to six."] and len(st["hand"]) == 7 and st["mulligan"]["taken"] == 1 and st["mulligan"]["bottom"] == 1,
              "first mulligan: 'Michael mulligans to six.', seven new cards, one owed to the bottom", (c, d.get("said"), st.get("mulligan")))
        c, d = t.seat("Michael", "keep")
        check(c == 400 and "exactly 1" in d.get("error", ""), "keeping six without naming the card for the bottom is refused", (c, d))
        c, d = t.seat("Michael", "mulligan")
        st = t.state("Michael")
        check(c == 200 and d["said"] == ["Michael mulligans to five."] and len(st["hand"]) == 7 and st["mulligan"]["bottom"] == 2 and st["mulligan"]["keeps"] == 5,
              "second mulligan: to five, seven cards in hand, two owed", (d.get("said"), st.get("mulligan")))
        names = [c_["name"] for c_ in st["hand"]]
        pick = [names[0], names[6]]
        c, d = t.seat("Michael", "keep", bottom=pick)
        st2 = t.state("Michael")
        left = list(names)
        for p in pick:
            left.remove(p)
        check(c == 200 and d["said"] == ["Michael keeps five."] and [c_["name"] for c_ in st2["hand"]] == left and st2["library"] == size0 + 2,
              "keep five: the two chosen cards left the hand for the library", (c, d.get("said"), st2["library"], size0))
        # nothing leaks: the other seat, the public log, the public phase and the board
        ev = t.call("GET", "/api/events?since=0", key=t.keys["Claude"])[1]
        evs = ev.get("events") if isinstance(ev, dict) else ev
        says = [e.get("text") for e in evs if e.get("type") == "say"]
        check(says == ["Michael mulligans to six.", "Michael mulligans to five.", "Michael keeps five."], "the public log has exactly the three size lines", says)
        blob = json.dumps([evs, t.phase(), t.call("GET", "/api/board3d")[1], t.state("Claude").get("opponents"), {k: x for k, x in t.state("Claude").items() if k not in ("mulligan", "hand")},
                           t.call("GET", "/api/seat/legal?seat=Claude", key=t.keys["Claude"])[1].get("note")])
        secret = {n for n in set(h0) | set(names) if n not in ("Plains", "Forest", "Mountain", "Swamp", "Island")} - {c_["name"] for c_ in t.state("Claude")["hand"]}
        seen = sorted(n for n in secret if n in blob)
        check(not seen, "no card of Michael's hands appears in the log, the phase, the board or Claude's state", seen)
        check(not re.search(r"suggested|Risky keep|keep_range|rule_bottom", blob), "…and neither does his suggestion or land count", re.findall(r".{30}suggested.{10}", blob)[:2])
        cm = t.state("Claude").get("mulligan") or {}
        check(cm.get("taken") == 0 and cm.get("waiting") == ["Claude"], "Claude's own view is about Claude", cm)
        c, d = t.call("GET", "/api/brain/state?seat=Michael", key=t.keys["Claude"])
        check(c == 403, "Claude's key doesn't open Michael's state", c)
        # a restart in the middle of the step: it is still open, for the seat that hasn't kept
        time.sleep(0.5)
        snap = next((t.dir / "r").glob("*/snapshot.pkl"))
        t.stop()
        t2 = Table(extra=["--restore", str(snap)])
        try:
            t2.claim()
            ph = t2.phase()
            check(ph.get("player") is None and (ph.get("mulligan") or {}).get("waiting") == ["Claude"], "restored mid-step: still open, still waiting on Claude only", ph.get("mulligan"))
            check(len(t2.state("Michael")["hand"]) == 5 and t2.state("Michael")["mulligan"]["kept"], "…and Michael's kept five are his hand", "")
            t2.seat("Claude", "keep")
            ph = t2.until(lambda p: p.get("player") is not None)
            check(ph.get("player") == "Claude", "the last keep after the restore starts the game", ph.get("player"))
        finally:
            t2.stop()
    finally:
        if t.proc.poll() is None:
            t.stop()


def bot_seat():
    t = Table(extra=["--no-such"]) if False else None
    # Claude is an engine seat here (not a pilot): it answers by the rule at once and never holds the start
    R_cmd = R.Table.__init__

    class OnePilot(Table):
        pass
    import retro_game4_test as RR
    orig = RR.subprocess.Popen

    def popen(cmd, **kw):                               # the harness names both seats as pilots: here only Michael is
        i = cmd.index("--pilot")
        assert cmd[i + 1] == "Claude"
        return orig(cmd[:i] + cmd[i + 2:], **kw)
    RR.subprocess.Popen = popen
    try:
        t = Table()
    finally:
        RR.subprocess.Popen = orig
    try:
        t.keys = {"Michael": t.call("POST", "/api/seat/claim", {"name": "Michael"}, key="dev-Michael")[1].get("key", "")}
        t.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)
        t.call("POST", "/api/phase/next", {"by": "Michael", "key": t.keys["Michael"]}, key=t.keys["Michael"])
        ph = t.phase()
        evs = t.call("GET", "/api/events?since=0")[1]
        evs = evs.get("events") if isinstance(evs, dict) else evs
        says = [e.get("text") for e in evs if e.get("type") == "say" and e.get("speaker") == "Claude"]
        check(ph.get("player") is None and ph["mulligan"]["waiting"] == ["Michael"], "an engine seat has answered by the time the step opens: only the person is waited on", ph.get("mulligan"))
        check(says and re.fullmatch(r"Claude keeps (seven|six|five)\.", says[-1]) and all(re.fullmatch(r"Claude (keeps|mulligans to) (seven|six|five)\.", s) for s in says),
              "…and the log says what size it kept, nothing else", says)
        n_m = sum(1 for s in says if "mulligans" in s)
        host = t.call("GET", "/api/brain/state?seat=Claude")[1]
        check(len(host["hand"]) == 7 - n_m, "its hand is seven less its mulligans", (len(host["hand"]), says))
        t.seat("Michael", "keep")
        ph = t.until(lambda p: p.get("player") is not None)
        check(ph.get("player") is not None, "the person's keep starts the game", ph.get("player"))
    finally:
        t.stop()


def timeout():
    t = Table(env={"TABLE_MULLIGAN_SECS": "2"})
    try:
        t.claim()
        t.press_start()
        t.seat("Michael", "mulligan")
        ph = t.until(lambda p: p.get("player") is not None, secs=12)
        evs = t.call("GET", "/api/events?since=0")[1]
        evs = evs.get("events") if isinstance(evs, dict) else evs
        says = [e.get("text") for e in evs if e.get("type") == "say"]
        check(ph.get("player") == "Claude", "seats that never answer don't hold the start: after the time limit the table keeps for them", ph.get("player"))
        check(any(s.startswith("Michael keeps six. (no answer") for s in says) and any(s.startswith("Claude keeps seven. (no answer") for s in says),
              "…keeping what they hold (no further mulligan), and the log says the table did it", says)
        check(len(t.state("Michael")["hand"]) == 6, "the seat that had mulliganed once keeps six: the rule chose its bottom card", len(t.state("Michael")["hand"]))
    finally:
        t.stop()


def off_by_default_in_tests():
    old = os.environ.pop("TABLE_MULLIGAN")
    try:
        t = R.Table()
        try:
            t.start()
            ph = t.call("GET", "/api/phase")[1]
            check(ph.get("player") == "Michael" and "mulligan" not in ph, "TABLE_HIGHROLL=first without TABLE_MULLIGAN (the other tests): no step, the game starts as before", ph.get("player"))
        finally:
            t.stop()
    finally:
        os.environ["TABLE_MULLIGAN"] = old


if __name__ == "__main__":
    section("the suggestion", rule)
    section("the moves, on the engine", engine)
    section("both keep", both_keep)
    section("one seat mulligans twice; nothing leaks; a restart mid-step", twice_and_leak)
    section("an engine seat decides by the rule", bot_seat)
    section("a seat that never answers", timeout)
    section("off where the tests skip the ceremony", off_by_default_in_tests)
    print(("\nFAILED: " + "; ".join(R.FAILS)) if R.FAILS else "\nall good", flush=True)
    sys.exit(1 if R.FAILS else 0)
