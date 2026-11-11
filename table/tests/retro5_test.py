"""Game 5 retro, table items (2026-11-10): T2 a stale seat tab says the game was restarted; T3 a game id on /api/phase and /api/events, and
/api/chat refuses a message for another game; T4 the host's house rule (local only, logged); T5 /lobby; T6 a person answers an attack before
its damage (no grace, no silent "no blocks"); T9 the choice banner; T12 an X picker; T13 Zefram's charge on a Spacecraft however it enters;
T14 the declare-attackers prompt; apply-removal by event id (a damage spell no longer blocks an older destroy); research folders keyed by
the game id. Tables of their own on free ports (never a live game's).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro5_test.py
"""
from __future__ import annotations

import os
import sys
import traceback
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import impulse_test as I  # noqa: E402

R, check, M, C = I.R, I.R.check, "Michael", "Claude"
R.POOL.update({"Zefram Cochrane, Warp Pioneer": 6, "Khaaaaaaaaaaaannn!": 6, "Enterprise NX-01": 6})
R.DECK_MICHAEL, R.DECK_CLAUDE = R.deck_file("Michael-r5"), R.deck_file("Claude-r5")


def static_pages():
    print("Pages: T2, T9, T12, T14 (the page code), T5 page")
    s = (HERE / "hand.html").read_text()
    seat = (HERE / "assets" / "seat.js").read_text()
    ca = (HERE / "assets" / "choicealert.js").read_text()
    check("staleNotice" in seat and "This game was restarted" in seat and "game_id" in seat, "T2: a tab with an old seat key says the game was restarted and to open the new link")
    check("choicealert.js" in s and "choicealert.js" in (HERE / "me.html").read_text() and "vibrate" in ca and "esc)" in ca, "T9: hand and me pages load the choice banner (sound, buzz, escaped text)")
    check("xPicker(c)" in s and "data-xfor" in s and "x: Math.max(0" in s, "T12: an {X} spell gets an X picker, sent with the cast")
    check("attackPromptDue" in s and "Not attacking? Tap Next step" in s, "T14: a pinned declare-attackers prompt; Next out of it without attacking takes a second tap")
    check("No block: take it" in s and "takeArm" in s, "T6: 'no block' on the page takes a second tap")
    check((HERE / "lobby.html").exists() and "esc(" in (HERE / "lobby.html").read_text(), "T5: lobby.html exists and escapes what it shows")


def table_main():
    print("Table: T3, T4, T5, T6, T12, T13, research folder")
    t = R.Table()
    t2 = R.Table()
    try:
        t.start()
        ph = t.call("GET", "/api/phase")[1]
        gid = ph.get("game_id")
        check(gid and f"-p{t.port}-" in gid and t.call("GET", "/api/events?since=0", host=False)[1].get("game_id") == gid, "T3: /api/phase and /api/events carry the game id (with the port in it)", gid)
        g2 = t2.call("GET", "/api/phase")[1].get("game_id")
        check(g2 and g2 != gid, "…two tables started together have different ids", (gid, g2))
        c, d = t.call("POST", "/api/chat", {"by": M, "text": "~hello", "game": "19990101-000000-p1-dead"}, key=t.keys[M])
        check(c == 409 and d.get("game_id") == gid, "T3: a chat line for another game is refused (and told the current id)", (c, d))
        c, d = t.call("POST", "/api/chat", {"by": M, "text": "~hello", "game": gid}, key=t.keys[M])
        check(c == 200, "…the right id goes through", (c, d))
        dirs = [p.name for p in (t.dir / "r").iterdir()] if (t.dir / "r").exists() else []
        check(gid in dirs, "research folder: named by the game id", dirs)
        # T5
        with urllib.request.urlopen(t.b + "/lobby", timeout=5) as r_:
            ok = r_.status == 200 and "lobby" in r_.read().decode().lower()
        check(ok, "T5: GET /lobby is a page")
        lb = t.call("GET", "/api/lobby", host=False)[1]
        check(lb.get("table", {}).get("game_id") == gid and {s_["name"] for s_ in lb["table"]["seats"]} >= {M, C}, "T5: /api/lobby says who sits here", lb)
        # T4
        x = t.onto(C, "Horta")
        c, d = t.call("POST", "/api/host/rule", {"seat": C, "ref": f"#{x}", "action": "damage", "n": 3, "why": "Saavik, by the rules we agreed"})
        said = " ".join(str(e.get("text")) for e in t.events()[-4:])
        check(c == 200 and t.perm(C, "Horta")["damage"] == 3 and "House rule (by host): 3 damage to Claude's Horta" in said, "T4: the host deals 3 to a bot seat's Horta; the log says it's a house rule", (c, d, said[-200:]))
        c, d = t.call("POST", "/api/host/rule", {"seat": C, "ref": f"#{x}", "action": "destroy"}, key=t.keys[M])
        check(c == 403 and t.perm(C, "Horta") is not None, "T4: not from another device (a seat key)", (c, d))
        c, d = t.call("POST", "/api/host/rule", {"seat": C, "ref": f"#{x}", "action": "exile"})
        check(c == 200 and t.perm(C, "Horta") is None, "T4: exile", (c, d))
        # T12: X
        foe = t.onto(C, "Mugato")
        t.lands(M, 4, "Mountain")
        t.hand(M, "Khaaaaaaaaaaaannn!")
        c, d = t.act(M, "cast", name="Khaaaaaaaaaaaannn!", x=2, targets=[{"at": C, "ref": f"#{foe}"}])
        mu = t.perm(C, "Mugato")
        tapped = sum(1 for p in t.state(M)["permanents"] if p["name"] == "Mountain" and p["tapped"])
        check(c == 200 and mu is not None and mu["damage"] == 4 and tapped == 3, "T12: X=2 taps three lands and deals twice X = 4", (c, d.get("error"), mu, tapped))
        # T13: Zefram's two extra charge counters, however the Spacecraft comes in
        t.onto(M, "Zefram Cochrane, Warp Pioneer")
        t.onto(M, "Enterprise NX-01")
        t.settle_setup()
        e = t.perm(M, "Enterprise NX-01")
        check(e and e["charge"] == 2, "T13: Enterprise NX-01 put onto the battlefield with Zefram there: 2 charge counters", e and e["charge"])
        # T6: an attack on a person waits for their answer
        t.turn(C)
        a = t.onto(C, "Horta")
        t.turn(M)
        t.turn(C)
        c, d = t.act(C, "attack", assign={f"#{a}": M})
        life0 = t.lifeof(M)
        c, d = t.act(C, "damage")
        check(c == 409 and "block or take it" in str(d.get("error")) and t.lifeof(M) == life0, "T6: the attacker can't deal damage before Michael answers", (c, d.get("error")))
        c, d = t.act(C, "end", text="over")
        check(c == 409 and t.lifeof(M) == life0, "T6: …nor end the turn past it", (c, d.get("error")))
        lg = [x_["action"] for x_ in t.legal(C)["actions"]]
        check("damage" not in lg and "end" not in lg, "T6: the attacker's legal list waits too", lg)
        c, d = t.act(M, "block", attacker=f"#{a}")
        c2, d2 = t.act(C, "damage")
        check(c == 200 and c2 == 200 and t.lifeof(M) == life0 - 7, "T6: Michael says 'no block, I take it', then the damage is dealt", (c, c2, t.lifeof(M)))
    finally:
        t.stop()
        t2.stop()


def table_removal():
    print("Table: apply-removal by event id")
    t = R.Table(human=True)
    try:
        t.keys = {n: t.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in (C, M, "Sam")}   # (no turns needed)
        t.onto(C, "Mugato")
        t.onto(C, "Horta")
        say = lambda text: t.call("POST", "/api/chat", {"by": "Sam", "text": text}, key=t.keys["Sam"])
        say("I cast He's Dead, Jim on Claude's Mugato")
        say("I cast Plasma Cascade on Claude's Horta")
        evs = [e for e in t.events() if e.get("type") == "attention" and e.get("kind") == "removal" and e.get("addressee") == C]
        check(len(evs) == 2 and [e.get("effect") for e in evs] == ["destroy", "damage"], "two removals aimed at Claude: a destroy, then a damage spell", [(e.get("effect"), e.get("spell")) for e in evs])
        if len(evs) == 2:
            c, d = t.act(C, "effect", kind="apply-removal", event=evs[1]["id"])
            check(c != 200 and "closed" in str(d.get("error")), "naming the damage one: refused (by hand), and closed", (c, d.get("error")))
            c, d = t.act(C, "effect", kind="apply-removal")
            check(c == 200 and t.perm(C, "Mugato") is None and t.perm(C, "Horta") is not None, "with no id: the older destroy applies (the damage spell no longer blocks it)", (c, d.get("error")))
            c, d = t.act(C, "effect", kind="apply-removal")
            check(c != 200, "…and then nothing is left to apply", (c, d.get("error")))
    finally:
        t.stop()


def main():
    for fn in (static_pages, table_main, table_removal):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {fn.__name__}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
