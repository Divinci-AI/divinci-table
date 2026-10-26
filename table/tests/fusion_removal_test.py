"""fusion_brain.on_removal: a wipe aimed at this seat is applied through the table's apply-removal effect (it used to say "Noted." and
leave the board untouched); a single target still goes through its own verb.   python3 table/tests/fusion_removal_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fusion_brain as F  # noqa: E402

F.say = lambda t, line: t.said.append(line)


class T:
    def __init__(self, ok=True, err=""):
        self.acts, self.said, self.ok, self.err = [], [], ok, err

    def act(self, action, **body):
        self.acts.append((action, body))
        return self.ok, ({} if self.ok else {"error": self.err})


fails = 0


def check(name, cond):
    global fails
    print(("✓ " if cond else "✗ ") + name)
    fails += 0 if cond else 1


for eff in ("wipe", "exile-all", "bounce-all", "damage-all"):
    t = T()
    F.on_removal(t, {"effect": eff, "target": "all", "spell": "Akroma's Vengeance"})
    check(f"{eff} aimed at me: apply-removal is called", t.acts == [("effect", {"kind": "apply-removal"})] and not t.said)

t = T(ok=False, err="the table can't apply a 'exile-all' effect yet: do it by hand")
F.on_removal(t, {"effect": "exile-all", "target": "all", "spell": "Farewell"})
check("a refusal is said at the table, not swallowed", len(t.said) == 1 and "Farewell" in t.said[0] and "by hand" in t.said[0])

t = T()
F.on_removal(t, {"effect": "destroy", "target": "#7", "spell": "Murder"})
check("single target: its own verb, unchanged", t.acts == [("destroy", {"ref": "#7"})])

t = T()
F.on_removal(t, {"effect": "destroy", "target": "#7", "illegal": "Hexproof."})
check("an illegal target is said, nothing acted", t.acts == [] and t.said == ["Hexproof."])

t = T()
F.on_removal(t, {"effect": None, "target": "all"})
check("an unknown effect aimed at all is still just noted", t.acts == [] and t.said == ["Noted."])

sys.exit(1 if fails else 0)
