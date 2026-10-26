"""table/wipes.py: which permanents a board wipe hits, from its own text.   python3 table/tests/wipes_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import wipes  # noqa: E402


class P:
    def __init__(self, name, *types):
        self.name, self.types = name, types

    def is_(self, t):
        return t in self.types


BF = [P("Elf", "Creature"), P("Wall", "Artifact", "Creature"), P("Ring", "Artifact"), P("Aura", "Enchantment"), P("Forest", "Land"),
      P("Jace", "Planeswalker")]
fails = 0


def check(name, cond):
    global fails
    print(("✓ " if cond else "✗ ") + name)
    fails += 0 if cond else 1


def names(text):
    v = wipes.victims(BF, text)
    return None if v is None else sorted(p.name for p in v)


check("Fumigate: destroy all creatures", names("destroy all creatures. you gain 1 life for each creature destroyed this way.") == ["Elf", "Wall"])
check("Akroma's Vengeance: artifacts, creatures and enchantments",
      names("destroy all artifacts, creatures, and enchantments.") == ["Aura", "Elf", "Ring", "Wall"])
check("Devastation Tide: return all nonland permanents", names("return all nonland permanents to their owners' hands.") == ["Aura", "Elf", "Jace", "Ring", "Wall"])
check("all permanents", len(names("exile all permanents.")) == 6)
check("a land wipe", names("destroy all lands.") == ["Forest"])
check("text that names no type is refused (None), not guessed", names("destroy all the things you love.") is None)
check("noncreature permanents is not guessed", names("destroy all noncreature permanents.") is None)
check("no wipe text is refused", names("draw two cards.") is None)
sys.exit(1 if fails else 0)
