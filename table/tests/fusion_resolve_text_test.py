"""fusion_brain.resolve_simple_text: after a cast, the pilot applies the plain "draw N cards" / "you gain N life" its spell states (the
table does not resolve spell text; the oracle flagged 28 casts where nothing was drawn). Targeted, optional, conditional and counted
text is left alone, as is any permanent.   python3 table/tests/fusion_resolve_text_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fusion_brain as F  # noqa: E402


class T:
    def __init__(self):
        self.acts = []

    def act(self, action, **body):
        self.acts.append((action, body))
        return True, {}


fails = 0


def check(name, cond):
    global fails
    print(("✓ " if cond else "✗ ") + name)
    fails += 0 if cond else 1


def run(text, typ="Instant"):
    t = T()
    F.resolve_simple_text(t, {"name": "X", "type": typ, "text": text})
    return t.acts


check("draw two", run("Draw two cards.") == [("draw", {"n": 2})])
check("draw a card (reminder text ignored)", run("Draw a card. (Then do the thing.)") == [("draw", {"n": 1})])
check("gain life", run("You gain 3 life.") == [("life", {"player": "me", "delta": 3})])
check("draw and gain", run("You gain 2 life. Draw a card.") == [("draw", {"n": 1}), ("life", {"player": "me", "delta": 2})])
check("sorcery counts", run("Draw three cards.", "Sorcery") == [("draw", {"n": 3})])
check("a permanent is never resolved (its draw is a trigger)", run("When this enters, draw a card.", "Creature — Elf") == [])
check("target: left alone", run("Target player draws two cards.") == [])
check("optional: left alone", run("You may draw a card.") == [])
check("per-something: left alone", run("You draw a card for each player sacrificed this way.") == [])
check("X: left alone", run("Draw X cards.") == [])
check("conditional: left alone", run("If you control a Wizard, draw a card.") == [])
check("each player: left alone", run("Each player draws two cards.") == [])
check("no card: nothing", F.resolve_simple_text(T(), None) is None)
sys.exit(1 if fails else 0)
