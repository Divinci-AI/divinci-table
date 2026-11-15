"""The AI player's code rules (table/dnd_agent.py): reading the exact roll the DM asks for, and damage or healing the DM
reports for this character. Sentences are from the 2026-11-15 sessions. No network, no model.

    ~/.venvs/table/bin/python table/tests/dnd_agent_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.argv = ["dnd_agent.py", "--base", "http://127.0.0.1:1", "--name", "Gemma", "--model", "none", "--key-file", "/dev/null"]
import dnd_agent as A  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    PASS, FAIL = PASS + bool(ok), FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


FIGHTER = {"abilities": {"str": 16, "dex": 13, "con": 15, "int": 9, "wis": 12, "cha": 11}, "proficiency": 2,
           "skills": ["athletics", "intimidation", "perception"]}
cases = [
    ("GLM, that is a hit! Gemma, that is a solid hit! Please roll 1d8 plus 3 for your longsword slashing damage.", ("1d8+3", "longsword slashing damage")),
    ("Gemma, please roll 2d6 + 3 for your greatsword damage.", ("2d6+3", "greatsword damage")),
    ("Gemma, roll 1d6 plus 3 plus 2d6 for the strike.", ("1d6+2d6+3", "strike")),
    ("Gemma, roll a Wisdom (Perception) check using a d20 plus your Wisdom modifier to pinpoint them.", ("d20+3", "Perception check")),
    ("Gemma, please make a Dexterity saving throw with a d20 against DC 13.", ("d20+1", "Dexterity saving throw")),
    ("Gemma, give me a Stealth check as you creep down.", ("d20+1", "Stealth check")),
    ("Claude, please roll 1d8 plus 3 for your longsword damage.", None),               # someone else's roll
    ("Gemma, what do you do?", None),
    ("Gemma, please roll a melee weapon attack using a d20 plus your Strength modifier.", None),   # an attack: the model picks the weapon
]
for text, want in cases:
    got = A.asked_dice(text, FIGHTER)
    got = (got[0], got[1].lower()) if got else None
    want = (want[0], want[1].lower()) if want else None
    check(f"{text[:60]!r} → {want}", got == want, str(got))
for text, want in [("The goblin slashes at Gemma: 5 slashing damage to Gemma.", -5), ("Gemma takes 7 piercing damage as the arrow lands.", -7),
                   ("Gemma regains 6 hit points.", 6), ("The arrow skitters off Gemma's shield.", None), ("4 damage to Claude.", None)]:
    check(f"{text!r} → {want}", A.hp_said(text) == want, str(A.hp_said(text)))
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
