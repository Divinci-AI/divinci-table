"""T10 (Game 5 retro, "think of Cockatrice"): a person moves their OWN cards by hand: any zone to any zone, counters, tokens; every move is
said in the public log, and a card from a hidden zone into a hidden zone is only "a card". Not for bot seats, not on another seat's cards.
Tables of their own on free ports (never a live game's), the seat's own key.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/manual_test.py
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import impulse_test as I  # noqa: E402

R, check, M, C = I.R, I.R.check, "Michael", "Claude"


def said_since(t, ev0):
    return [str(e.get("text")) for e in t.events() if e["id"] > ev0 and e.get("type") == "say"]


def table_manual():
    print("Table: Michael's own cards, by hand")
    t = R.Table()
    try:
        t.start()
        t.hand(M, "Horta")
        ev0 = I.last_event(t)
        c, d = t.act(M, "manual", op="move", ref="hand:Horta", to="battlefield")
        h = t.perm(M, "Horta")
        check(c == 200 and h is not None and any("Manual: I move Horta from my hand to my battlefield" in x for x in said_since(t, ev0)), "hand → battlefield, said by name (a public zone)", (c, d))
        c, d = t.act(M, "manual", op="counter", ref=f"#{h['id']}", kind="+1/+1", n=1)
        check(c == 200 and t.perm(M, "Horta")["counters"] == 1 and t.perm(M, "Horta")["pt"] == "8/7", "a +1/+1 counter: 8/7", (c, d))
        c, d = t.act(M, "manual", op="counter", ref=f"#{h['id']}", kind="+1/+1", n=-1)
        check(c == 200 and t.perm(M, "Horta")["counters"] == 0, "…and off again", (c, d))
        c, d = t.act(C, "manual", op="move", ref=f"#{h['id']}", to="graveyard")
        check(c != 200 and t.perm(M, "Horta") is not None, "Claude can't move Michael's Horta", (c, d))
        c, d = t.act(M, "manual", op="move", ref=f"#{h['id']}", to="graveyard")
        check(c == 200 and t.perm(M, "Horta") is None and "Horta" in t.state(M)["graveyard"], "battlefield → graveyard", (c, d))
        c, d = t.act(M, "manual", op="move", ref="graveyard:Horta", to="exile")
        check(c == 200 and "Horta" in I.seat_b3(t, M).get("exile", []) and "Horta" not in t.state(M)["graveyard"], "graveyard → exile: in exile, face up, for all to see", (c, d))
        top = t.call("POST", "/api/brain/peek", {"seat": M, "n": 2})[1].get("private", {}).get("top")
        public = json.dumps(t.call("GET", "/api/board3d", host=False)[1]) + json.dumps(t.events())
        ev1 = I.last_event(t)
        c, d = t.act(M, "manual", op="move", ref="library-top", to="hand")
        lines = said_since(t, ev1)
        hidden = top[0] not in public
        check(c == 200 and any("I move a card from my library" in x for x in lines) and (not hidden or not any(top[0] in x for x in lines)),
              "library → hand: only 'a card' in the log", lines)
        c, d = t.act(M, "manual", op="move", ref="library-top", to="graveyard")
        check(c == 200 and t.state(M)["graveyard"][-1] == top[1] and any(top[1] in x for x in said_since(t, ev1)), "library → graveyard: it is public now, so named", (c, said_since(t, ev1)))
        c, d = t.act(M, "manual", op="token", name="Tribble", power=1, toughness=1, n=2)
        check(c == 200 and len(t.perms(M, "Tribble token")) == 2, "two 1/1 Tribble tokens", (c, d))
        c, d = t.act(M, "manual", op="move", ref="nowhere:Horta", to="hand")
        check(c != 200, "a bad zone is refused, nothing moves", (c, d))
    finally:
        t.stop()


def table_bot_refused():
    print("Table: not for a bot seat")
    t = R.Table(extra=["--ai", "Robo|", "--ai-deck", R.DECK_CLAUDE])
    try:
        c, d = t.call("POST", "/api/brain/manual", {"seat": "Robo", "op": "token", "name": "Cheat", "power": 9, "toughness": 9})
        check(c != 200 and "person" in str(d.get("error")), "a bot seat's manual move is refused (its cards go through the engine)", (c, d))
    finally:
        t.stop()


def static_page():
    print("hand.html")
    s = (HERE / "hand.html").read_text()
    check("drawManual()" in s and 'act("manual"' in s and "data-mmove" in s, "the hand page has the By hand panel (move, counters, tokens)")


def main():
    for fn in (static_page, table_manual, table_bot_refused):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {fn.__name__}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
