"""Hologram Janeway (T8, 2026-11-09): "Whenever you attack while creatures you control have total power 8 or greater, put a +1/+1 counter on target
creature." Total power counts every creature its controller has, tapped ones too; the counter is aimed by the controller (asked, like any
aimed trigger). Below 8, nothing happens. The engine first, then a table of its own on a free port (never a live game's), the seat's own key.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/janeway_test.py
"""
from __future__ import annotations

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
R.POOL.update({"Hologram Janeway": 6})
R.DECK_MICHAEL, R.DECK_CLAUDE = R.deck_file("Michael-janeway"), R.deck_file("Claude-janeway")


def engine_janeway():
    print("Engine: the trigger, its threshold, tapped creatures counting")
    v = I.seat(lands=0)
    j = player.Perm(I.card("Hologram Janeway"), sick=False)
    h = player.Perm(I.card("Horta"), sick=False, tapped=True)
    v.battlefield += [j]
    check(v.total_power_triggers() == [] and not v.__dict__.get("pending_choices"), "total power 3: nothing")
    v.battlefield.append(h)
    v.__dict__["asks"] = True                       # a driven seat: asked to aim it
    said = v.total_power_triggers()
    pend = v.__dict__.get("pending_choices") or []
    check(pend and pend[0]["source"] == "Hologram Janeway" and pend[0]["slot"]["kinds"] == ["Creature"], "total power 10 (Horta tapped counts): Janeway asks for a target", (said, pend))
    if pend:
        said = v.resolve_choice(pend[0], [{"perm": h, "owner": v}])
        check(h.counters == 1, "the counter goes where its controller aimed it", said)
    v.__dict__["asks"] = False                      # an engine-run seat: its biggest creature, and it says so
    h.counters = 0
    said = v.total_power_triggers()
    check(h.counters == 1 and any("biggest" in x for x in said), "an engine-run seat puts it on its biggest creature", said)


def table_janeway():
    print("Table: Michael attacks; Janeway's counter is his to aim")
    t = R.Table()
    try:
        t.start()
        j = t.onto(M, "Hologram Janeway")
        h = t.onto(M, "Horta")
        foe = t.onto(C, "Mugato")
        t.turn(C)
        t.turn(M)
        c, d = t.act(M, "tap", ref=f"#{h}")
        check(c == 200 and t.perm(M, "Horta")["tapped"], "Horta is tapped (it still counts toward total power)", (c, d))
        c, d = t.act(M, "attack", assign={f"#{j}": C})
        pend = t.state(M).get("pending_choices") or []
        opts = [o.get("ref") for o in (pend[0]["slot"].get("options") if pend else [])]
        check(c == 200 and pend and pend[0]["source"] == "Hologram Janeway" and {f"#{j}", f"#{h}", f"#{foe}"} <= set(opts),
              "attacking with total power 10: Janeway asks Michael, any creature may be named", (c, d, opts))
        if pend:
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "ref": f"#{h}"})
            check(c == 200 and t.perm(M, "Horta")["counters"] == 1 and t.perm(M, "Horta")["pt"] == "8/7", "the counter is on Horta: 8/7", (c, d, t.perm(M, "Horta")))
    finally:
        t.stop()


def main():
    for fn in (engine_janeway, table_janeway):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {fn.__name__}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
