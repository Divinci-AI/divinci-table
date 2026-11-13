"""The table-talk card-name guard (draft_banter.NameGuard) and the typed-line refusal on the draft server. Offline: no model, no
key, a real server on an ephemeral loopback port with LIVE in a scratch folder. Run: python3 -m unittest test_banter_guard -v

Seen at the table (2026-11-13 retro): an AI seat's line was checked against every card name with r"\\b(name)\\b" over names of five
letters or more, so a name ending in punctuation ("Shields Up!", "He's Dead, Jim", "Set Phasers to . . .") or under five letters
("Risa") was never caught; and a line a person TYPED was not checked at all. Before the fix the punctuation, short-name and
typed-line checks below all fail (checked by stashing draft_banter.py + draft_server.py + table/voice.py and running this file).
The names are the real set's when the card data is there (DRAFT_CARDS_JSON); the awkward ones are also listed by hand so the
matcher is tested without it."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import draft_banter
from test_ready import Base
import draft_server as S

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "table"))
import voice  # noqa: E402  (table/voice.py: the hand-leak guard at the game table had the same \\b hole)

AWKWARD = ["Shields Up!", "He's Dead, Jim", "Set Phasers to . . .", "I'm a Doctor, Not a . . .", "Khaaaaaaaaaaaannn!", "Red Alert!",
           "Hit It!", "All Good Things...", "Risa", "Bat'leth", "Qo'noS", "Horta", "U.S.S. Voyager, Lone Wanderer",
           "Janice Rand, Essential Aide", "Energize", "Island", "Forest"]

# (line, the card it names or None, strict?)   strict = an AI seat's line
CASES = [
    ("Shields Up! That's my whole plan.", "Shields Up!", False),
    ("shields up", "Shields Up!", False),
    ("SHIELDS   UP?!", "Shields Up!", False),
    ("my shields are up", None, False),
    ("He's dead, Jim.", "He's Dead, Jim", False),
    ("hes dead jim", "He's Dead, Jim", False),
    ("He’s dead — Jim!", "He's Dead, Jim", False),
    ("he's not dead, jim", None, False),
    ("Set phasers to... stun", "Set Phasers to . . .", False),
    ("I set my phasers down", None, False),
    ("I'm a doctor, not a bricklayer", "I'm a Doctor, Not a . . .", False),
    ("im a doctor", None, False),
    ("Khaaaan!", "Khaaaaaaaaaaaannn!", False),
    ("KHAAAAAAAN", "Khaaaaaaaaaaaannn!", False),
    ("Khan would have taken that", None, False),
    ("red alert", "Red Alert!", False),
    ("a red, alert little pack", "Red Alert!", False),          # accepted: the words in order ARE the name, marks never matter
    ("Anyone been to Risa?", "Risa", False),
    ("risa", "Risa", False),
    ("an arisan custom", None, False),
    ("rise and shine", None, False),
    ("batleth time", "Bat'leth", False),
    ("a bat'leth, obviously", "Bat'leth", False),
    ("qonos", "Qo'noS", False),
    ("an exhortation to draft better", None, False),
    ("the horta!", "Horta", False),
    ("Energize.", "Energize", False),
    ("energized by this pack", None, False),
    ("I need one more island", None, False),                   # basics are free to every seat: not a pick
    ("into the forest we go", None, False),
    ("uss voyager lone wanderer", "U.S.S. Voyager, Lone Wanderer", False),
    ("U.S.S. Voyager, Lone Wanderer", "U.S.S. Voyager, Lone Wanderer", False),
    ("USS Voyager is a great show", None, False),               # a person may talk about the show
    ("USS Voyager is a great show", "U.S.S. Voyager, Lone Wanderer", True),   # an AI seat may not: it holds picks and costs nothing to re-roll
    ("Janice Rand says hi", None, False),
    ("Janice Rand says hi", "Janice Rand, Essential Aide", True),
    ("Voyager was my favourite", None, True),                   # one word of a title is ordinary Trek talk, even for an AI
    ("Fresh pack. Smells like possibility.", None, True),
    ("hit it", "Hit It!", False),
    ("all good things", "All Good Things...", False),
]


class Matcher(unittest.TestCase):
    def test_awkward_names_true_and_false(self):
        g = draft_banter.name_guard(AWKWARD)
        for line, want, strict in CASES:
            self.assertEqual(g.find(line, strict=strict), want, (line, strict))

    def test_common_short_word_needs_a_capital(self):
        g = draft_banter.name_guard(["Opt", "Fog", "Risa"])
        self.assertEqual(g.find("I'd Opt in"), "Opt")
        self.assertIsNone(g.find("I opt for the safe pick"))
        self.assertIsNone(g.find("option one, in a fog"))
        self.assertEqual(g.find("risa, obviously"), "Risa")       # not a common word: any case

    def test_canned_lines_never_trip_the_real_set(self):
        try:
            names = [c["name"] for c in json.loads(draft_banter.Path(S.D.CARDS_JSON).read_text()) if c.get("set") == "trk"]
        except OSError:
            self.skipTest("no card data")
        g = draft_banter.name_guard(names)
        for kind, by in draft_banter.CANNED.items():
            for who, lines in by.items():
                for line in lines:
                    self.assertIsNone(g.find(line, strict=True), line)
        hit = 0
        for n in sorted(set(names)):                              # every card in the set is caught by its own printed name
            if n.lower() in draft_banter.BASICS:
                continue
            self.assertEqual(g.find(f"well, {n} it is"), n, n)
            self.assertEqual(g.find(n.upper().replace("'", "’")), n, n)
            hit += 1
        self.assertGreater(hit, 250)

    def test_table_hand_guard_catches_names_that_end_in_punctuation(self):
        hand = ["Shields Up!", "He's Dead, Jim", "Set Phasers to . . .", "Cyclonic Rift"]
        self.assertEqual(voice.leaks_hand("Shields Up! is in my hand", hand), "Shields Up!")
        self.assertEqual(voice.leaks_hand("shields up", hand), "Shields Up!")
        self.assertEqual(voice.leaks_hand("He’s dead, Jim. Sorry.", hand), "He's Dead, Jim")
        self.assertEqual(voice.leaks_hand("set phasers to", hand), "Set Phasers to . . .")
        self.assertEqual(voice.leaks_hand("I'm holding Cyclonic Rift for you", hand), "Cyclonic Rift")
        self.assertIsNone(voice.leaks_hand("No deal. Shields are staying up.", hand))


class TypedLines(Base):
    def test_a_typed_line_naming_a_card_is_refused_with_a_reason(self):
        name = next(n for n in sorted({c["name"] for c in self.dr.cards.values() if c.get("set") == "trk"}) if n.endswith("!"))
        code, out, raw = self.req("POST", "/api/banter", {"text": f"I just took {name}"}, token="Sam")
        self.assertEqual(code, 400)
        self.assertIn("names a card", out["error"])
        self.assertIn("hidden", out["error"])
        self.assertNotIn(name, raw)                               # the refusal never says the name back
        self.assertFalse(self.dr.banter.lines)
        log = self.live / "draft-banter.jsonl"
        self.assertFalse(log.exists() and name in log.read_text())
        self.dr.chat_at.clear()
        code, out, _ = self.req("POST", "/api/banter", {"text": name.lower().rstrip("!") + "?? lol"}, token="Sam")
        self.assertEqual(code, 400)
        self.dr.chat_at.clear()
        code, out, _ = self.req("POST", "/api/banter", {"text": "nice pack, whoever opened it"}, token="Sam")
        self.assertEqual((code, out), (200, {"ok": True}))
        self.assertEqual([l["text"] for l in self.dr.banter.lines], ["nice pack, whoever opened it"])

    def test_add_itself_drops_a_named_card_whoever_calls_it(self):
        name = next(n for n in sorted({c["name"] for c in self.dr.cards.values() if c.get("set") == "trk"}) if "," in n and len(n.split(",")[0].split()) == 2 and n.replace(",", "").replace(" ", "").isalpha())
        self.assertIsNone(self.dr.banter.add("Sam", f"mine: {name}"))
        self.assertIsNone(self.dr.banter.add("Claude", f"{name.split(',')[0]} is mine"))    # an AI seat: the short title too
        self.assertIsNotNone(self.dr.banter.add("Sam", f"{name.split(',')[0]} would approve"))
        self.assertEqual(len(self.dr.banter.lines), 1)


if __name__ == "__main__":
    unittest.main()
