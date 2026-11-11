"""Zefram Cochrane, Warp Pioneer: "You may look at the top card of your library any time." / "Once during each of your turns, you may cast an
artifact spell from the top of your library." On tables of their own on free ports (retro_game4_test.Table). Set-up uses the host token;
everything under test uses the seat's own key.

    PYTHONDONTWRITEBYTECODE=1 python3 table/tests/zefram_test.py
    OLD_TABLE=<the unmodified table code>   the code the old snapshot is written with (default: ../table-fix next to this copy)"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.dont_write_bytecode = True
import retro_game4_test as R  # noqa: E402

check = R.check
M, C = "Michael", "Claude"
ZEF = "Zefram Cochrane, Warp Pioneer"
TOP = "Warp Core"                      # an artifact, {2}
OLD_DIR = Path(os.environ.get("OLD_TABLE") or HERE.parent / "table-fix").expanduser()


def topdeck(t, seat, name):
    t.hand(seat, name)
    c, d = t.call("POST", "/api/brain/topdeck", {"seat": seat, "name": name, "quiet": True})
    assert c == 200, d


def public_text(t):
    out = []
    for r in ("/api/board3d", "/api/events?since=0", "/api/life", "/api/phase", "/api/spectate", "/api/state", "/api/history", "/api/todos"):
        out.append(json.dumps(t.call("GET", r, host=False)[1]))
    return "\n".join(out)


def cast_top(t, seat, name, **kw):
    return t.act(seat, "cast", name=name, **{"from": "library-top"}, **kw)


def visibility_and_cast(t):
    t.start()                                          # Michael's main 1
    st = t.state(M)
    check(st.get("library_top") is None, "no Zefram: no top card in the seat's own state", st.get("library_top"))
    t.lands(M, 4, "Island")
    zid = t.onto(M, ZEF)
    t.decline(M)
    topdeck(t, M, TOP)
    before = public_text(t)
    st = t.state(M)
    lt = st.get("library_top") or {}
    check(lt.get("name") == TOP and "Artifact" in (lt.get("type") or ""), "with Zefram: the seat's own state shows its top card", lt)
    check(lt.get("castable_now") is True, "…castable now (own main phase, mana open)", lt)
    leg = t.legal(M)
    check(any(a["action"] == "cast" and a["body"].get("from") == "library-top" and a["body"].get("name") == TOP for a in leg.get("actions", [])),
          "the seat's legal list offers the cast from the top", [a["body"] for a in leg.get("actions", []) if a["action"] == "cast"])
    cst = t.state(C)
    check(cst.get("library_top") is None, "the opponent's own state has no top card of anyone's", cst.get("library_top"))
    c, d = t.call("GET", f"/api/brain/state?seat={M}", key=t.keys[C])
    check(c == 403, "the opponent's key can't read Michael's state", (c, str(d)[:200]))
    check(TOP not in before, "the top card is in no public route (board3d, events, life, phase, spectate, state, history, todos)")

    # refused: a card that is not the top one, and a cast from the top of a card the table is not showing as top
    lib_before = t.state(M)["library"]
    c, d = cast_top(t, M, "Tricorder")
    check(c != 200 and "top card" in str(d.get("error")), "a card that isn't the top card is refused", (c, d))
    check(TOP not in str(d), "…and the refusal doesn't name the real top card", d)
    c, d = cast_top(t, M, TOP)
    check(c == 200 and TOP in json.dumps(d), "the top card, an artifact, is cast from the top on its own turn", (c, str(d)[:300]))
    st = t.state(M)
    check(any(p["name"] == TOP for p in st["permanents"]), "…and is on the battlefield", [p["name"] for p in st["permanents"]])
    check(st["library"] == lib_before - 1 and (st.get("library_top") or {}).get("name") != TOP, "…and left the library", (lib_before, st["library"]))
    check(not any(h["name"] == TOP for h in st.get("hand", [])), "…and is not left in the hand", [h["name"] for h in st.get("hand", [])])
    t.lands(M, 3, "Island")
    topdeck(t, M, "Tricorder")
    st = t.state(M)
    check((st.get("library_top") or {}).get("castable_now") is False, "after one use this turn the top card shows as not castable", st.get("library_top"))
    c, d = cast_top(t, M, "Tricorder")
    check(c != 200 and "already" in str(d.get("error")), "a second cast from the top in the same turn is refused", (c, d))

    # non-artifact on top
    t.turn(M)
    topdeck(t, M, "Horta")
    st = t.state(M)
    check((st.get("library_top") or {}).get("name") == "Horta" and not st["library_top"]["castable_now"], "a creature (non-artifact) on top is shown but not castable", st.get("library_top"))
    c, d = cast_top(t, M, "Horta")
    check(c != 200 and "artifact" in str(d.get("error")), "…and casting it from the top is refused", (c, d))
    check("Horta" not in str(d.get("error")), "…without naming it", d)

    # opponent's turn
    topdeck(t, M, "Tricorder")
    t.turn(C)
    st = t.state(M)
    check((st.get("library_top") or {}).get("name") == "Tricorder", "on the opponent's turn the seat still sees its top card", st.get("library_top"))
    check(st["library_top"]["castable_now"] is False, "…but it isn't castable there", st.get("library_top"))
    c, d = cast_top(t, M, "Tricorder")
    check(c != 200 and "your own turn" in str(d.get("error")), "a cast from the top on the opponent's turn is refused", (c, d))

    # a new turn of its own: the once-a-turn use is back
    t.turn(M)                                          # (its draw took the Tricorder: put another on top)
    topdeck(t, M, "Tricorder")
    c, d = cast_top(t, M, "Tricorder")
    check(c == 200, "a new turn of its own: the cast from the top is available again", (c, d))

    # Zefram leaves: the top card is gone from the seat's state
    topdeck(t, M, "Horta")
    t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{zid}", "quiet": True})
    t.settle_setup()
    st = t.state(M)
    check(st.get("library_top") is None, "Zefram gone: the top card is gone from the seat's state", st.get("library_top"))
    c, d = cast_top(t, M, "Horta")
    check(c != 200 and "nothing you control" in str(d.get("error")), "…and the cast from the top is refused", (c, d))
    check("Horta" not in public_text(t), "a card that sat on top and was never cast never reached a public route")


def old_snapshot():
    """A game saved by the unmodified code restores and plays under this code."""
    if not ((OLD_DIR / "server.py").exists() and OLD_DIR.resolve() != HERE.resolve()):
        print(f"  ⏭️  skipped: no older table at {OLD_DIR} to write the snapshot with (OLD_TABLE=<dir> compares an older table)")
        return
    o = R.Table(code=OLD_DIR / "server.py")
    try:
        o.start()
        o.lands(M, 4, "Island")
        zid = o.onto(M, ZEF)
        o.decline(M)
        topdeck(o, M, TOP)
        c, d = o.act(M, "say", text="saving", force=True)
        snap = None
        import time
        for _ in range(40):
            snap = next(iter(o.dir.rglob("snapshot.pkl")), None)
            if snap:
                break
            time.sleep(0.5)
        check(snap is not None, "the old code wrote a snapshot", snap)
        keys = dict(o.keys)
    finally:
        o.stop()
    t = R.Table(extra=["--restore", str(snap)])
    try:
        t.keys = keys
        ph = t.call("GET", "/api/phase")[1]
        check(ph.get("player") == M, "the new code restores the old game", ph)
        st = t.state(M)
        check(any(p["id"] == zid for p in st.get("permanents", [])), "…with Zefram on the battlefield", str(st)[:200])
        check((st.get("library_top") or {}).get("name") == TOP, "…and the new code shows the old game's top card to its seat", st.get("library_top"))
        c, d = cast_top(t, M, TOP)
        check(c == 200, "…and casts it from the top", (c, str(d)[:300]))
        t.lands(M, 2, "Island")
        topdeck(t, M, "Tricorder")
        c, d = cast_top(t, M, "Tricorder")
        check(c != 200 and "already" in str(d.get("error")), "…once that turn", (c, d))
    finally:
        t.stop()


def main():
    for name, fn in (("visibility and cast", lambda: visibility_and_cast(t_)), ("old snapshot", old_snapshot)):
        print(f"\n== {name}")
        global t_
        if name == "visibility and cast":
            t_ = R.Table()
        try:
            fn()
        except Exception as e:                       # noqa: BLE001 — one section that throws is one failure
            import traceback
            traceback.print_exc()
            check(False, f"{name} ran to the end", e)
        finally:
            if name == "visibility and cast":
                t_.stop()
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


t_ = None
if __name__ == "__main__":
    sys.exit(main())
