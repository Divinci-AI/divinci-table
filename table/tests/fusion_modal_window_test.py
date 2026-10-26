"""fusion_brain.on_priority: a modal instant cast in a response window is refused until a mode is named; the pilot now names one and
casts again (it used to leave the refusal as a pilot-error).   python3 table/tests/fusion_modal_window_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fusion_brain as F  # noqa: E402

F.say = lambda t, line: None
F.local_choice = lambda *a, **k: None
MODAL = ("Drown in the Loch is modal — choose 1 with --mode (1-based): 1. Counter target spell with mana value 2 or less "
         "| 2. Destroy target creature with mana value 2 or less")


class T:
    def __init__(self):
        self.acts, self.casts = [], 0

    def state(self):
        return {"hand": [{"name": "Drown in the Loch", "castable_now": True, "type": "Instant", "cost": "{U}{B}", "text": "Choose one —"}]}

    def act(self, action, **body):
        self.acts.append((action, body))
        if action == "cast" and "modes" not in body:
            return False, {"error": MODAL}
        return True, {}


def choose(state, options, question, dry):
    if question.startswith("Which mode"):
        return options[1][1], ""                           # mode 2
    return next(b for _, b in options if b.get("action") == "cast"), ""


F.choose = choose
fails = 0


def check(name, cond):
    global fails
    print(("✓ " if cond else "✗ ") + name)
    fails += 0 if cond else 1


t = T()
F.on_priority(t, {"text": "Opponent casts a spell."}, False)
check("refused for a missing mode, then cast again with the chosen mode",
      t.acts == [("cast", {"name": "Drown in the Loch"}), ("cast", {"name": "Drown in the Loch", "modes": [2]})])

sys.exit(1 if fails else 0)
