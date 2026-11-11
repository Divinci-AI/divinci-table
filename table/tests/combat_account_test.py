"""T1 (Game 5 retro): the announced attack total vs the life changes after it. The declaration ("I attack X with A and B, N damage") is a
forecast: every attacker unblocked, at its power then (a double striker twice). Blocks, trample over a blocker, first strike and power
changing since make the real number, so at the damage step the table now says what the attack actually took off each player, and why
it differs when it does. Checked against the life events themselves. A table of its own on a free port (never a live game's).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 ~/.venvs/table/bin/python table/tests/combat_account_test.py
"""
from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import impulse_test as I  # noqa: E402
import player  # noqa: E402

R, check, M, C = I.R, I.R.check, "Michael", "Claude"


def engine_forecast():
    print("Engine: the declaration counts a double striker twice")
    v = I.seat("Claude")
    a = player.Perm(I.card("Horta"), sick=False)
    a.temp_kw = ("Double strike",)
    v.battlefield.append(a)
    said = v.manual_attack({f"#{a.id}": "Michael"})
    check(any(x.endswith(", 14 damage.") for x in said), "Horta with double strike: announced as 14", said)


def lost_since(t, ev0, who):
    return -sum(e.get("delta", 0) for e in t.events() if e["id"] > ev0 and e["type"] == "life" and e.get("player") == who and e.get("delta", 0) < 0)


def table_account():
    print("Table: blocked, trampled over, unblocked")
    t = R.Table()
    try:
        t.start()
        t.turn(C)
        ss = t.onto(C, "Starfleet Security")
        ho = t.onto(C, "Horta")
        mu = t.onto(M, "Mugato")
        t.turn(M)
        t.turn(C)
        ev0 = I.last_event(t)
        c, d = t.act(C, "attack", assign={f"#{ss}": M, f"#{ho}": M})
        check(c == 200 and any(x.endswith(", 8 damage.") for x in d.get("said") or []), "the declaration: 8 (1 + 7, unblocked)", d.get("said"))
        c, d = t.act(M, "block", blocker=f"#{mu}", attacker=f"#{ho}")
        check(c == 200, "Michael blocks Horta (7, trample) with Mugato (6/6)", (c, d))
        c, d = t.act(M, "block", attacker=f"#{ss}")   # and takes Starfleet Security (a person answers every attacker: T6)
        c, d = t.act(C, "damage")
        said = " ".join(d.get("said") or []) + " " + " ".join(str(e.get("text")) for e in t.events() if e["id"] > ev0 and e["type"] == "say")
        took = lost_since(t, ev0, M)
        m = re.search(r"Combat: Michael took (\d+)", said)
        check(c == 200 and m and int(m.group(1)) == took == 2 and "announced at 8" in said,
              "the damage step says what really reached Michael: 2 (1 unblocked + 1 trampled over), against the 8 announced", (took, said[-400:]))
        t.turn(M)
        t.turn(C)
        ev1 = I.last_event(t)
        c, d = t.act(C, "attack", assign={f"#{ss}": M})
        t.act(M, "block", attacker=f"#{ss}")
        c, d = t.act(C, "damage")
        said = " ".join(d.get("said") or []) + " " + " ".join(str(e.get("text")) for e in t.events() if e["id"] > ev1 and e["type"] == "say")
        check("Combat: Michael took 1." in said and lost_since(t, ev1, M) == 1, "unblocked: what was announced is what he took, and it says so plainly", said[-300:])
    finally:
        t.stop()


def main():
    for fn in (engine_forecast, table_account):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {fn.__name__}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
