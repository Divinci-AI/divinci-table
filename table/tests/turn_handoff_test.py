"""The turn hand-over after End happens once. `end` is listed (and accepted) again during the half-second wait; each press used to schedule its own
hand-over, and a late one restarted the NEXT player's turn after they had begun it (a listed Station refused "only in your main phase", 1 golden
path in ~6). Also: a listed move that went stale because the game ended answers 409 (read again), not 400.

    PYTHONDONTWRITEBYTECODE=1 ~/.venvs/table/bin/python table/tests/turn_handoff_test.py"""
import os, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
import retro_game4_test as R  # noqa: E402

check = R.check
M, C = "Michael", "Claude"


def main():
    t = R.Table()
    try:
        t.start()                                          # Michael's main 1
        c1, _ = t.call("POST", "/api/brain/end", {"seat": M, "text": "x"}, key=t.keys[M])
        time.sleep(0.35)                                   # inside the half-second wait, `end` is still accepted
        c2, _ = t.call("POST", "/api/brain/end", {"seat": M, "text": "x"}, key=t.keys[M])
        ph = t.wait(lambda p: p.get("player") == C, 10)
        c3, d3 = t.act(C, "begin")
        seen, regress = [], False
        for _ in range(60):                                # 3 s, every 50 ms: once Claude is in main 1 the step must never go back to untap
            ph = t.call("GET", "/api/phase")[1]
            seen.append(ph.get("step"))
            if "main 1" in seen and ph.get("step") in ("untap", "upkeep", "draw") and ph.get("player") == C:
                regress = True
            time.sleep(0.05)
        check(c1 == 200 and "main 1" in seen, "Claude's turn began and reached main 1", (c1, c2, c3, seen[:6]))
        check(not regress, "…and a second End did not restart it afterwards (the step never went back to untap)", [x for k, x in enumerate(seen) if k == 0 or x != seen[k - 1]])
        ph = t.call("GET", "/api/phase")[1]
        check(ph.get("player") == C and ph.get("begun"), "…and it is still Claude's begun turn", (ph.get("player"), ph.get("step"), ph.get("begun")))
        a = [x for x in t.legal(C)["actions"]]
        check(any(x["action"] in ("land", "cast", "attack", "end", "next") for x in a), "its listed moves are for the step it is really in", [x["action"] for x in a][:6])
    finally:
        t.stop()


def stale_after_loss():
    """A move listed while a player was still in, sent after they lost (another seat's action ended it in between): 409 and "stale", not a 400."""
    t = R.Table()
    try:
        t.start()
        h = t.onto(M, "Horta")
        t.turn(C); t.turn(M)
        la = t.legal(M)["actions"]
        listed = next((a_ for a_ in la if a_["action"] == "attack"), None)
        check(listed is not None, "attack is listed while Claude is at 20", [a_["action"] for a_ in la])
        c0, d0 = t.call("POST", "/api/brain/life", {"seat": M, "player": "Claude", "delta": -23, "text": "test"})
        c, d = t.call("POST", "/api/brain/attack", listed["body"], key=t.keys[M])
        check(c == 409 and d.get("stale") and "has lost" in d.get("error", ""), "…sent after Claude lost: 409 stale (read the list again), not a refusal", (c0, c, d.get("error")))
    finally:
        t.stop()


def stale_permanent():
    """A listed move that names a permanent which has since left play: 409 stale (read again)."""
    t = R.Table()
    try:
        t.start()
        h = t.onto(M, "Horta")
        t.turn(C); t.turn(M)
        t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{h}", "quiet": True})
        c, d = t.call("POST", "/api/brain/attack", {"seat": M, "assign": {f"#{h}": C}}, key=t.keys[M])
        check(c == 409 and d.get("stale"), "naming a permanent that has left play: 409 stale, not 400", (c, d.get("error")))
    finally:
        t.stop()


if __name__ == "__main__":
    main()
    stale_after_loss()
    stale_permanent()
    print(("\nFAILED: " + "; ".join(R.FAILS)) if R.FAILS else "\nall good", flush=True)
    sys.exit(1 if R.FAILS else 0)
