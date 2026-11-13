"""2026-11-13 retro, C4: the host corrects a mistaken "until end of turn" change.

Seen at the table: a player aimed "-2/-2 until end of turn" at the wrong creature; both players agreed to move it, and the host had no control
for it (the change lives in Perm.temp, which only effects could reach).

Now: POST /api/brain/manual {"seat": <controller>, "op": "temp", "ref": "#id", "power": ±n, "toughness": ±n} with the HOST's token adjusts it,
says so in public words, and runs the state-based check (0 toughness dies). A seat's own key is refused, for its own creature too.

A real table on a free port. Before the fix (git stash -- table/server.py table/player.py) the host calls answer 400 ("manual: move | counter |
token"), so every check but the refusals fails.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/retro_1113_host_temp_test.py
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import retro_game4_test as R  # noqa: E402

check = R.check
M, C = "Michael", "Claude"
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830, 8883))   # never a live game's port


def pt(t, seat, pid):
    return next((p.get("pt") for p in t.state(seat)["permanents"] if p["id"] == pid), None)


def host(t, seat, **body):
    return t.call("POST", "/api/brain/manual", {"seat": seat, "op": "temp", **body})          # (Table.call sends the host's token)


def main_():
    t = R.Table()
    try:
        t.start()                                                # Michael's main 1
        horta = t.onto(M, "Horta")                               # 7/6
        small = t.onto(C, "Moopsy")
        land = t.onto(M, "Plains")
        small_pt = pt(t, C, small)
        check(pt(t, M, horta) == "7/6", "set-up: Horta is 7/6", pt(t, M, horta))

        print("\n── the host moves a mistaken -2/-2")
        ev0 = max([e.get("id", 0) for e in t.events()] or [0])
        c, d = host(t, M, ref=f"#{horta}", power=-2, toughness=-2)          # (the mistake, as the spell made it)
        check(c == 200 and pt(t, M, horta) == "5/4", "-2/-2 on Horta: 5/4", (c, d, pt(t, M, horta)))
        c, d = host(t, M, ref=f"#{horta}", power=2, toughness=2)            # the correction: off again
        check(c == 200 and pt(t, M, horta) == "7/6", "+2/+2 takes it off again: 7/6", (c, d, pt(t, M, horta)))
        pub = " ".join(str(e.get("text") or "") for e in t.events() if e.get("id", 0) > ev0)
        check("Host: Horta gets -2/-2 until end of turn (a correction)" in pub and "Host: Horta gets +2/+2 until end of turn (a correction)" in pub,
              "both are in the public log, in plain words", pub[-400:])
        board = next((p for p in t.board(M).get("permanents", []) if p.get("id") == horta), {})
        check(str(board.get("pt") or "7/6") == "7/6", "the public board shows the corrected size", board)

        print("\n── only the host")
        c, d = t.act(M, "manual", op="temp", ref=f"#{horta}", power=3, toughness=3)
        check(c == 403 and "host" in str(d.get("error")), "Michael's own key, his own creature: refused (403, host only)", (c, d))
        c, d = t.act(C, "manual", op="temp", ref=f"#{small}", power=3, toughness=3)
        check(c == 403, "Claude's own key: refused", (c, d))
        c, d = t.call("POST", "/api/brain/manual", {"seat": M, "op": "temp", "ref": f"#{horta}", "power": 3, "toughness": 3, "_pilot": False}, key=t.keys[M])
        check(c == 403, "a seat key that sends _pilot: false itself is still a seat key", (c, d))
        c, d = t.call("POST", "/api/brain/manual", {"seat": M, "op": "temp", "ref": f"#{horta}", "power": 3, "toughness": 3}, host=False)
        check(c in (401, 403), "no token and no key: refused", (c, d))
        check(pt(t, M, horta) == "7/6" and pt(t, C, small) == small_pt, "…and none of those changed anything", (pt(t, M, horta), pt(t, C, small)))

        print("\n── refusals that change nothing")
        for body, what in (({"ref": f"#{land}", "power": 1, "toughness": 1}, "a land (not a creature)"), ({"ref": "#999999", "power": 1, "toughness": 1}, "a permanent that is not there"),
                           ({"ref": f"#{horta}"}, "no numbers"), ({"ref": f"#{horta}", "power": 99, "toughness": 0}, "more than 20"), ({"ref": f"#{horta}", "power": "big"}, "not a number")):
            c, d = host(t, M, **body)
            check(c in (400, 409) and "error" in d, f"{what}: refused", (c, str(d.get("error"))[:200]))    # (409 "not there any more" is the table's answer for a permanent that has left)
        check(pt(t, M, horta) == "7/6", "Horta is still 7/6")

        print("\n── state-based: 0 toughness dies")
        tough = int(str(small_pt).split("/")[1])
        c, d = host(t, C, ref=f"#{small}", power=0, toughness=-tough)
        check(c == 200 and t.perm(C, "Moopsy") is None, f"Moopsy at -0/-{tough}: it is gone from the battlefield", (c, d))
        check(any(g.get("name") == "Moopsy" if isinstance(g, dict) else g == "Moopsy" for g in t.state(C).get("graveyard", [])), "…in its owner's graveyard", t.state(C).get("graveyard"))
        check("dies" in " ".join(str(x) for x in d.get("said", [])) or "Moopsy dies" in " ".join(str(e.get("text") or "") for e in t.events()), "…and the log says it died")

        print("\n── it ends with the turn, like the effect it corrects")
        host(t, M, ref=f"#{horta}", power=-3, toughness=-1)
        check(pt(t, M, horta) == "4/5", "-3/-1 on Horta: 4/5", pt(t, M, horta))
        t.turn(C)
        check(pt(t, M, horta) == "7/6", "next turn: 7/6 again", pt(t, M, horta))
    finally:
        t.stop()


def main():
    try:
        main_()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        check(False, "the test ran", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
