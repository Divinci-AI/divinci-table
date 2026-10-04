"""An AI seat's spend cap: after FUSION_MAX_CALLS release requests it stops asking, takes the most passive
option, says so once, and stays quiet. No network: the cap is checked before any request.

    FUSION_MAX_CALLS=2 python3 table/tests/fusion_cap_test.py"""
import os
import sys
from pathlib import Path

os.environ["FUSION_MAX_CALLS"] = "2"
sys.argv = ["x"]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fusion_brain as F  # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


check("cap read from the environment", F.MAX_CALLS == 2)
F.CALLS["n"] = 1
check("under the cap: not capped", not F.capped())
F.CALLS["n"] = 2
check("at the cap: capped", F.capped())
check("a capped ask makes no request", F.ask("x", False) == {"capped": True})
main = [('cast "Llanowar Elves"', {"action": "cast"}), ("stop casting this turn", {"action": "stop"})]
atk = [("don't attack", {"action": "none"}), ("attack Sam", {"action": "attack"})]
blk = [("take 5 (no block)", {"action": "block", "amount": 5}), ("block with #3", {"action": "block", "blocker": "#3"})]
pri = [("pass — keep my mana", {"action": "pass"}), ('cast "Counterspell"', {"action": "cast"})]
for name, opts, want in (("main phase → stop casting", main, "stop"), ("combat → don't attack", atk, "none"),
                         ("attacked → take it", blk, "block"), ("priority → pass", pri, "pass")):
    pick, line = F.choose({"commander": "Tuvasa the Sunlit"}, opts, "q", False)
    check(name, pick["action"] == want and line == "" and "blocker" not in pick, (pick, line))
said = []


class T:
    def act(self, a, **b):
        said.append(b.get("text"))


F.say(T(), "")
F.say(T(), "")
check("announces the cap once", said.count(said[0]) == 1 and "limit" in said[0], said)
F.MAX_CALLS = 0
check("no cap configured (the laptop table): never capped", not F.capped())
print("ok" if ok_all else "FAILED")
sys.exit(0 if ok_all else 1)
