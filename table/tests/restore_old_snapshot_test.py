"""A game saved by the code before the Game 4 retro must load under the new code. Starts the OLD server on a free port with its own throwaway game,
plays a little (including the old page's chat-line "Station"), lets it save, stops it, and --restores that snapshot under this copy's server.

The OLD code is a FROZEN copy of the table as it was before the Game 4 retro patch: ~/.divinci/star-trek-mtg/backups/table-pre-retro-g4 (or OLD_TABLE).
It is never the live worktree: once the patch is applied there, "old" would be the new code and the test would compare new with new and pass
whatever it found. A missing or unfrozen copy is a FAILURE, not a skip.

    PYTHONDONTWRITEBYTECODE=1 python3 table/tests/restore_old_snapshot_test.py"""
from __future__ import annotations

import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.dont_write_bytecode = True
import retro_game4_test as R  # noqa: E402

FROZEN = Path(os.path.expanduser("~/.divinci/star-trek-mtg/backups/table-pre-retro-g4"))
OLD_DIR = Path(os.environ.get("OLD_TABLE") or FROZEN).expanduser()
OLD = OLD_DIR / "server.py"
cards_path.need(OLD, "a frozen pre-patch table", "OLD_TABLE=<dir>")
check = R.check


def old_is_old() -> str | None:
    """Why the OLD code can't stand for the pre-patch table, or None. Its server must exist, must not be this copy, and must not already carry
    the Game 4 retro (its own test file, or the patch's new modules, give it away)."""
    if not OLD.exists():
        return f"no frozen pre-patch table at {OLD_DIR} (make it: rsync -a --exclude art/ --exclude '*.pkl' --exclude '*.key' --exclude __pycache__ <pre-patch table>/ {FROZEN}/)"
    if OLD_DIR.resolve() == HERE.resolve():
        return f"OLD_TABLE is this copy ({HERE}): that compares the new code with itself"
    marks = [m for m in ("tests/retro_game4_test.py", "card_coverage.py") if (OLD_DIR / m).exists()]
    if marks or "def answer_choice(" in OLD.read_text():
        return f"{OLD_DIR} already has the Game 4 retro patch ({', '.join(marks) or 'answer_choice'}): it is not the old code"
    return None


class Old(R.Table):
    def __init__(self):
        super().__init__(code=OLD)


def old_cards(t, seat, name, tapped=False):
    c, d = t.call("POST", "/api/brain/search", {"seat": seat, "name": name})
    assert c == 200, d
    c, d = t.call("POST", "/api/brain/put", {"seat": seat, "name": name, "tapped": tapped, "quiet": True})
    assert c == 200, d


def main():
    why = old_is_old()
    check(why is None, "the OLD code is a frozen pre-patch copy, not the live worktree", why)
    if why is not None:
        print(f"\n❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS))
        return 1
    print(f"old code: {OLD_DIR}")
    o = Old()
    M, C = "Michael", "Claude"
    try:
        o.start()
        for n in ("Class-F Shuttlecraft", "Xindi Surveyors", "Tricorder", "Plains", "Plains", "Forest", "Sarcophagus, Ship of the Dead"):
            old_cards(o, M, n)
        st = o.state(M)
        ship = next(p for p in st["permanents"] if p["name"] == "Class-F Shuttlecraft")
        sar = next(p for p in st["permanents"] if p["name"].startswith("Sarcophagus"))
        c, d = o.call("POST", "/api/brain/say", {"seat": M, "text": f"Station: I tap Xindi Surveyors (#99, power 3) and put 3 charge counters on Class-F Shuttlecraft #{ship['id']} (now 3).", "force": True}, key=o.keys[M])
        assert c == 200, d
        c, d = o.call("POST", "/api/brain/say", {"seat": M, "text": f"Station: I tap Xindi Surveyors (#99, power 4) and put 4 charge counters on Class-F Shuttlecraft #{ship['id']} (now 7).", "force": True}, key=o.keys[M])
        assert c == 200, d
        c, d = o.call("POST", "/api/brain/station", {"seat": M, "ship": f"#{sar['id']}", "crew": "#" + str(next(p["id"] for p in st["permanents"] if p["name"] == "Xindi Surveyors"))}, key=o.keys[M])
        assert c == 200, d
        time.sleep(1)
        snap = None
        for _ in range(30):
            snap = next(iter(o.dir.rglob("snapshot.pkl")), None)
            if snap:
                break
            time.sleep(0.5)
        check(snap is not None and snap.exists(), "the old code wrote a snapshot of its own test game", snap)
        keys = dict(o.keys)
        sar_charge = next(p for p in o.state(M)["permanents"] if p["id"] == sar["id"])["charge"]
    finally:
        o.stop()

    t = R.Table(extra=["--restore", str(snap)])
    try:
        t.keys = keys
        ph = t.call("GET", "/api/phase")[1]
        check(ph.get("player") == M and ph.get("step") == "main 1", "the new code restores it, at the same step", ph.get("step"))
        st = t.state(M)
        check(not st.get("error") and len(st.get("permanents", [])) >= 7, "the seat's key still works and its board is there", str(st)[:200])
        s = next(p for p in st["permanents"] if p["id"] == ship["id"])
        check(s["charge"] == 7 and s["pt"] == "3/4" and "Flying" in s["keywords"], "the chat-line charge becomes the engine's: 7, a flier (3/4: Sarcophagus gives +1/+0)", s)
        check(next(p for p in st["permanents"] if p["id"] == sar["id"])["charge"] == sar_charge, "a charge the engine already held is untouched", sar_charge)
        tric = next(p for p in st["permanents"] if p["name"] == "Tricorder")
        xs = next(p for p in st["permanents"] if p["name"] == "Xindi Surveyors")
        c, d = t.act(M, "equip", equipment=f"#{tric['id']}", creature=f"#{xs['id']}")
        check(c == 200 and next(p for p in t.state(M)["permanents"] if p["id"] == xs["id"])["pt"] == "5/3", "new mechanics work on a permanent from the old save (a Perm pickled without the new fields)", (c, d))
        t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{xs['id']}", "quiet": True})
        check(next(p for p in t.state(M)["permanents"] if p["id"] == tric["id"])["attached_to"] is None, "…including falling off")
        check(t.board(M)["life"] == 20, "life survives", t.board(M)["life"])
    finally:
        t.stop()
    # and a second restore doesn't add it twice
    snap2 = next(iter(t.dir.rglob("snapshot.pkl")))
    t2 = R.Table(extra=["--restore", str(snap2)])
    try:
        t2.keys = keys
        s = next(p for p in t2.state(M)["permanents"] if p["id"] == ship["id"])
        check(s["charge"] == 7, "restoring twice doesn't count the log twice", s["charge"])
    finally:
        t2.stop()
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
