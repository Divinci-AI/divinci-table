"""The results ledger validates, and the board counts only what it says it counts.

    python3 table/tests/ledger_test.py"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ledger as L  # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


d = L.load()
check("the real ledger is valid", L.validate(d) == [], L.validate(d))
b = L.board(d)
rows = {r["system"]: r for r in b["systems"]}
check("forked and unfinished games never count toward placement",
      all(len(r["placements"]) == r["finished"] for r in b["systems"]))
check("assisted seats are excluded", rows["Claude"]["assisted"] == 2 and rows["Claude"]["finished"] == 2, rows["Claude"])
check("Claude: won g1, last in g2", sorted(rows["Claude"]["placements"]) == ["1/3", "4/4"], rows["Claude"]["placements"])
check("Fusion: 2nd of 4 in g2", rows["Fusion"]["placements"] == ["2/4"], rows["Fusion"]["placements"])
check("published board matches the ledger", json.loads(L.BOARD.read_text())["systems"] == b["systems"])

bad = copy.deepcopy(d)
bad["games"][0]["result"]["order"] = ["Claude", "Michael"]
check("a finished game must order every seat", any("every seat" in e for e in L.validate(bad)))
bad = copy.deepcopy(d)
bad["games"][1]["result"]["winner"] = "Claude"
check("the winner must be first", any("winner" in e for e in L.validate(bad)))
bad = copy.deepcopy(d)
bad["games"][1]["seats"][0]["controller"] = {"kind": "ai"}
check("an AI seat names its system", any("system" in e for e in L.validate(bad)))
bad = copy.deepcopy(d)
bad["games"].append(copy.deepcopy(d["games"][0]))
check("duplicate ids refused", any("duplicate" in e for e in L.validate(bad)))
print("ok" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
