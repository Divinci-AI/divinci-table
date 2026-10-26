"""harness/engine_diff.py: the two rule sets agree on every plain combat, the known deathtouch gap is still there, and a planted
change in either side is caught.   python3 table/tests/engine_diff_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "harness"))
import engine_diff as D  # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"), flush=True)


r = D.run()
check("11,900 generated combats were compared", r["cases"] == 11900, r["cases"])
check("no divergence outside deathtouch: plain combat, trample and blocking agree", set(r["by_cause"]) <= D.KNOWN, r["by_cause"])
check("the known gap is still present (the table's block resolution cannot see deathtouch)", r["divergent"] > 0 and {"attacker-deathtouch", "blocker-deathtouch"} <= set(r["by_cause"]), r["by_cause"])
check("a plain case, spelled out: 5/3 trampler blocked by a 0/2 -> 3 tramples over, the blocker dies",
      D.engine_outcome({"p": 5, "t": 3, "trample": True}, {"p": 0, "t": 2}) == D.table_outcome({"p": 5, "t": 3, "trample": True}, {"p": 0, "t": 2})
      == {"def_loss": 3, "blocker_dead": True, "attacker_dead": False})

# planted: if the table's trample arithmetic were wrong, the diff reports it as 'other'
real = D.table_outcome
D.table_outcome = lambda a, b, life=40: {**real(a, b, life), "def_loss": real(a, b, life)["def_loss"] + (1 if a.get("trample") and b else 0)}
bad = D.run()
D.table_outcome = real
check("mutation: a planted trample error is reported as 'other' and fails the run", "other" in bad["by_cause"], bad["by_cause"])
real_e = D.engine_outcome
D.engine_outcome = lambda a, b, life=40: {**real_e(a, b, life), "blocker_dead": False}
bad = D.run()
D.engine_outcome = real_e
check("mutation: a planted engine error is caught too", "other" in bad["by_cause"], bad["by_cause"])
sys.exit(0 if ok_all else 1)
