"""fusion_brain.play_turn plays a plain land before a bounce land, and names the land to return only when it must.
A stub table, no server.   python3 table/tests/fusion_land_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fusion_brain as F  # noqa: E402

BOUNCE = {"name": "Bounce Vale", "land": True, "castable_now": False, "type": "Land", "text": "Bounce Vale enters tapped. When it enters, return a land you control to its owner's hand."}
PLAIN = {"name": "Plain Meadow", "land": True, "castable_now": False, "type": "Basic Land", "text": ""}


class T:
    def __init__(self, hand, perms):
        self.hand, self.perms, self.acts = hand, perms, []

    def state(self):
        return {"hand": self.hand, "permanents": self.perms, "land_played": False, "commander_in_zone": False, "commander_card": {}}

    def act(self, action, **body):
        self.acts.append((action, body)); return True, {}


def lands(hand, perms):
    t = T(hand, perms)
    F.play_turn(t, True)
    return [(a, b) for a, b in t.acts if a == "land"]


assert lands([BOUNCE, PLAIN], []) == [("land", {"name": "Plain Meadow"})], "plain land first"
assert lands([BOUNCE], []) == [("land", {"name": "Bounce Vale"})], "no other land: the table returns it itself, say nothing"
assert lands([BOUNCE], [{"type": "Basic Land", "name": "X"}]) == [("land", {"name": "Bounce Vale", "bounce": "self"})], "another land out: name the choice"
assert lands([], []) == [], "no land in hand: no land action"

# a modal spell is refused until a mode is named: the pilot asks which, then casts again with it
class M(T):
    def act(self, action, **body):
        self.acts.append((action, body))
        if action == "cast" and "modes" not in body:
            return False, {"error": "Bant Charm is modal \u2014 choose 1 with --mode (1-based): 1. Destroy target artifact. | 2. Put target creature on the bottom. | 3. Counter target instant."}
        return True, {}


SPELL = {"name": "Bant Charm", "land": False, "castable_now": True, "type": "Instant", "cost": "{G}{W}{U}", "text": "Choose one \u2014"}
m = M([SPELL], [])
F.play_turn(m, True)
casts = [b for a, b in m.acts if a == "cast"]
assert casts[0] == {"name": "Bant Charm"} and casts[1] == {"name": "Bant Charm", "modes": [1]}, casts

# a face-down creature is offered "turn face up" only when its cost can be afforded (the state gives no castable_now for it)
assert F.mana_value("{4}{G}") == 5 and F.mana_value("{X}{G}{G}") == 2 and F.mana_value("{W/U}{3}") == 4 and F.mana_value("") == 0
class TU(T):
    def __init__(self, mana):
        super().__init__([], [{"id": 7, "name": "Hidden", "face_down": True, "turn_up_cost": "{4}{G}", "type": "Creature"}])
        self.mana = mana

    def state(self):
        return {**super().state(), "untapped_mana": self.mana}


for mana, expect in ((4, False), (5, True)):
    t = TU(mana); F.play_turn(t, True)
    assert any(a == "turn-up" for a, _ in t.acts) is expect, (mana, t.acts)
print("fusion_land_test: ok")
