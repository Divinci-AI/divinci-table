"""BACK is a real rewind on a person's turn (Michael's call, 2026-11-09; tonight Michael pressed Finish combat damage before Sam could block).
The table keeps the game as it stood at the start of each recent step; BACK restores the start of the previous step (or of this one, if
something happened in it): life, damage, the board, hands, libraries in the same order (no redraw), pending questions. The laptop's host does
it alone; a seated person asks and another person confirms. Never into or across a bot's moves. Tables of their own on free ports (never a
live game's).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/rewind_test.py
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import impulse_test as I  # noqa: E402  (free ports never a live game's; decks from the set)

R, check, M, C = I.R, I.R.check, "Michael", "Claude"


class T(R.Table):
    """A table whose waits also pass for a bot seat (Robo), through the host token, as its brain would."""
    def wait(self, pred, secs=45):
        end = time.time() + secs
        ph = {}
        while time.time() < end:
            ph = self.call("GET", "/api/phase")[1]
            if pred(ph):
                return ph
            nxt = (ph.get("passes") or {}).get("next")
            if nxt in self.keys:
                self.call("POST", "/api/brain/pass", {"seat": nxt, "player": ph.get("player"), "step": ph.get("step")}, key=self.keys[nxt])
            elif nxt == "Robo":
                self.call("POST", "/api/brain/pass", {"seat": "Robo", "player": ph.get("player"), "step": ph.get("step")})
            time.sleep(0.15)
        raise AssertionError(f"the table never got there: {ph.get('player')} {ph.get('step')}")


def pump_act(t, seat, action, **body):
    """An action that may wait on a bot's (or anyone's) pass: pass for them and try again."""
    c, d = 0, {}
    for _ in range(30):
        c, d = t.call("POST", f"/api/brain/{action}", {"seat": seat, **body}, key=t.keys[seat])
        if c != 409:
            return c, d
        ph = t.call("GET", "/api/phase")[1]
        nxt = (ph.get("passes") or {}).get("next")
        if nxt in t.keys and nxt != seat:
            t.call("POST", "/api/brain/pass", {"seat": nxt, "player": ph.get("player"), "step": ph.get("step")}, key=t.keys[nxt])
        elif nxt == "Robo":
            t.call("POST", "/api/brain/pass", {"seat": "Robo", "player": ph.get("player"), "step": ph.get("step")})
        time.sleep(0.3)
    return c, d


def back(t, seat=None):
    return t.call("POST", "/api/phase/back", {"by": seat or "host"}, key=t.keys[seat] if seat else None)


def table_combat_and_draw():
    print("Table: two people (pilot seats): rewind a damage step, then a draw")
    t = T()
    try:
        t.start()                                        # Michael, main 1
        mu = t.onto(M, "Mugato")
        t.turn(C)
        ho = t.onto(C, "Horta")
        t.turn(M)
        t.turn(C)                                        # Horta can attack now
        c, d = t.act(C, "attack", assign={f"#{ho}": M})
        c, d = t.act(M, "block", attacker=f"#{ho}")      # Michael takes it (a misclick: he meant to block; T6 means he did have to answer)
        c2, d2 = t.act(C, "damage")
        check(c == 200 and t.lifeof(M) == 13, "Michael takes Horta by mistake: 13", (c, c2, t.lifeof(M)))
        c, d = back(t, C)
        check(c == 202 and (d.get("back_request") or {}).get("confirm_by") == [M], "Claude asks to go back: Michael has to confirm", (c, d.get("error"), d.get("back_request")))
        check(t.call("GET", "/api/phase")[1].get("back_request", {}).get("by") == C, "every page sees the request (to show a Confirm button)")
        c, d = t.call("POST", "/api/phase/back/confirm", {"by": C}, key=t.keys[C])
        check(c == 403, "the asker can't confirm their own request", (c, d.get("error")))
        c, d = t.call("POST", "/api/phase/back/confirm", {"by": M}, key=t.keys[M])
        check(c == 200, "Michael confirms: the table goes back a step", (c, d.get("error")))
        for _ in range(3):                               # (the block step resolved the damage at once: it can take a step or two to get before it)
            if t.lifeof(M) == 20:
                break
            back(t)
        ph = t.call("GET", "/api/phase")[1]
        check(t.lifeof(M) == 20, "…back to before the damage: 20", (t.lifeof(M), ph.get("step")))
        said = " ".join(str(e.get("text")) for e in t.events()[-6:])
        check("Back to Claude" in said and "undoing" in said, "the log says it went back, and what it undid", said[-200:])
        ph = t.call("GET", "/api/phase")[1]
        check(ph.get("player") == C and ph.get("step") in ("beginning of combat", "declare attackers"), "…to the start of Claude's attack step", ph.get("step"))
        c, d = t.act(C, "attack", assign={f"#{ho}": M})
        inc = t.state(M).get("incoming") or []
        check(c == 200 and any(i.get("ref") == f"#{ho}" for i in inc), "Claude attacks again with Horta: Michael gets to answer", (c, d.get("error"), inc))
        c, d = t.act(M, "block", blocker=f"#{mu}", attacker=f"#{ho}")
        check(c == 200 and t.lifeof(M) == 19, "now Michael blocks with Mugato: only Horta's trample gets through (19, not 13)", (c, d.get("error"), t.lifeof(M)))
        # a draw, rewound: the same card comes back on top
        t.act(C, "end", text="over")
        t.wait(lambda p: p.get("player") == M and p.get("step") == "untap")
        top = t.call("POST", "/api/brain/peek", {"seat": M, "n": 2})[1].get("private", {}).get("top")
        n0 = len(t.state(M)["hand"])
        c, d = t.act(M, "begin")
        drew = (d.get("private") or {}).get("drew")
        check(c == 200 and drew == top[0] and len(t.state(M)["hand"]) == n0 + 1, "Michael begins his turn and draws the top card", (c, drew, top))
        for _ in range(4):                               # back before his turn began: the card is on top again
            if len(t.state(M)["hand"]) == n0:
                break
            c, d = back(t)
        ph = t.call("GET", "/api/phase")[1]
        top2 = t.call("POST", "/api/brain/peek", {"seat": M, "n": 2})[1].get("private", {}).get("top")
        check(len(t.state(M)["hand"]) == n0 and top2 == top, "BACK to before his turn began: the card is back on top, the library in the same order", (top2, top, ph.get("player"), ph.get("step")))
        check(not any(str(top[0]) in str(e.get("text")) for e in t.events()[-8:] if e.get("type") == "say"), "the rewind names no card that was drawn", "")
        if ph.get("player") != M:                        # it went back into Claude's last step: Claude ends again
            pump_act(t, C, "end", text="over")
        t.wait(lambda p: p.get("player") == M and p.get("step") in ("untap", "main 1"))
        if t.call("GET", "/api/phase")[1].get("step") == "untap":
            pump_act(t, M, "begin")
        hand = [h["name"] for h in t.state(M)["hand"]]
        top3 = t.call("POST", "/api/brain/peek", {"seat": M, "n": 1})[1].get("private", {}).get("top")
        check(len(hand) == n0 + 1 and top3 == top[1:2], "his turn begins again and draws the same card: no redraw", (len(hand), n0, top3, top))
    finally:
        t.stop()


def table_bot():
    print("Table: never into or across a bot's turn")
    t = T(extra=["--ai", "Robo|", "--ai-deck", R.DECK_CLAUDE, "--order", "Michael,Robo,Claude"])
    try:
        t.keys = {n: t.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in (C, M)}
        t.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)
        t.call("POST", "/api/phase/next", {"by": M, "key": t.keys[M]}, key=t.keys[M])
        t.wait(lambda p: p.get("player") == M and p.get("begun") and p.get("step") == "main 1")
        c, d = pump_act(t, M, "end", text="over")
        t.wait(lambda p: p.get("player") == "Robo")
        t.call("POST", "/api/brain/begin", {"seat": "Robo"})
        t.call("POST", "/api/brain/say", {"seat": "Robo", "text": "Nothing this turn."})
        t.call("POST", "/api/brain/end", {"seat": "Robo", "text": "Done."})
        t.wait(lambda p: p.get("player") == C and p.get("begun") and p.get("step") == "main 1")
        refused = None
        for _ in range(8):
            c, d = back(t)
            if c != 200:
                refused = (c, d.get("error"))
                break
        check(refused and refused[0] == 409 and "bot" in str(refused[1]), "BACK stops at the bot's turn, and says why", refused)
        ph = t.call("GET", "/api/phase")[1]
        check(ph.get("player") == C, "…and the game is still on Claude's turn", ph.get("player"))
    finally:
        t.stop()


def main():
    for fn in (table_combat_and_draw, table_bot):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {fn.__name__}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
