"""`--bot Sam`: a seat whose person isn't playing is drafted by a bot; the draft advances without waiting on it and makes no
link for it.        python3 test_bots_for_absent.py"""
import os, sys, tempfile
os.environ["DRAFT_LIVE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draft_server as S

fails = []
def check(ok, what, d=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {d}")); fails.append(what) if not ok else None

check(S.make_bots(["Sam", "Nobody"]) == ["Sam"], "only a real seat can be handed to a bot")
check(1 not in S.REQUIRED and "Sam" not in S.NAMES.values() and 1 not in S.HUMANS, "Sam's seat is no longer waited on or listed")
dr = S.Draft(7)
check(not any(seat == 1 for seat in dr.tokens.values()), "no token (no link) is made for Sam's seat")
p0 = dr.pick_no
for seat in sorted(S.REQUIRED):
    err = dr.pick(seat, dr.current[seat][0]["id"]) if hasattr(dr, "pick") else "no pick()"
    if err:
        check(False, "picks go through", f"seat {seat}: {err}")
check(dr.pick_no == p0 + 1, "the step advances once Michael, Claude and Fusion have picked", f"{p0} -> {dr.pick_no}")
check(len(dr.picks[1]) == 1, "a bot picked for Sam's seat", str(len(dr.picks[1])))
print("failures: none" if not fails else f"failures: {fails}")
sys.exit(1 if fails else 0)
