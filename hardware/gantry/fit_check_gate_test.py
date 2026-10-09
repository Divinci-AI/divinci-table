#!/usr/bin/env python3
"""The pre-print gate must hold in both directions and refuse a vague ledger (runs fit_check.py six times, one after another, ~13 s each):
  - a real failure that is NOT on the ledger breaks the run (exit 1, 'NEW');
  - a ledger entry that no longer fails breaks the run (exit 1, 'STALE');
  - the shipped ledger passes (exit 0) and says BLOCKED.
  python3 hardware/gantry/fit_check_gate_test.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
real = json.loads((HERE / "fit_check_ledger.json").read_text())["known_failures"]

def run(entries):
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "ledger.json"; f.write_text(json.dumps({"known_failures": entries}))
        r = subprocess.run(["nice", "-n", "10", sys.executable, str(HERE / "fit_check.py")], capture_output=True, text=True, env={**os.environ, "FIT_CHECK_LEDGER": str(f)})
    return r.returncode, r.stdout

ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ") + name)

code, out = run([e for e in real if e["id"] != "chute-step"])
check("a failure missing from the ledger breaks the run", code == 1 and "NEW    chute-step" in out and "BROKEN" in out)
code, out = run(real + [{"id": "not-a-real-check", "blocks": [], "reason": "x"}])
check("a ledger entry that no longer fails breaks the run", code == 1 and "STALE  not-a-real-check" in out and "BROKEN" in out)
code, out = run(real)
check("the shipped ledger passes and reports BLOCKED, not OPEN", code == 0 and "PRE-PRINT GATE: BLOCKED" in out)
def edited(i, **kw): return [dict(e, **kw) if e["id"] == i else e for e in real]
code, out = run(edited("chute-step", blocks=["discard_chut"]))
check("a ledger entry that names a part wrongly (so blocks nothing) breaks the run", code == 1 and "LEDGER chute-step: blocks 'discard_chut'" in out)
code, out = run(edited("chute-step", blocks=[]))
check("an entry that blocks nothing needs hardware_only: true", code == 1 and "blocks nothing" in out)
code, out = run(edited("chute-step", review_by="2020-01-01"))
check("an entry past its review_by date breaks the run", code == 1 and "review_by 2020-01-01 has passed" in out)
code, out = run(real)
check("a blocked part is not listed as cleared", "discard_chute" not in out.split("cleared to print:")[1].split("\n")[0])
print("ALL PASS" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
